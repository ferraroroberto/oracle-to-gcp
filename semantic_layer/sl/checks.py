"""``sl checks run`` — the scheduled health sweep.

Phase 1 (always): schema drift (bindings vs live metadata), review / expiry
dates, stale template pins, catalog refresh diff.
Phase 2 (``--full``, opt-in): freshness, quality rules, legacy ⇄ cloud and
anchor reconciliation, template re-runs (see ``phase2/checks_full.py``).

Every check that cannot establish a fact reports ``unknown`` — never folded
into ``ok``. Output: ``build/health.json`` (read by the agent's provenance card
and the navigator) and one grouped issue body per owning team under
``build/issues/`` (written, never posted — the steward skill or CI posts them).
Probes are aggregate-only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlglot

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import PLATFORMS, Layer, resolve_today, write_json
from semantic_layer.sl.model import Model, load_model
from semantic_layer.sl.platforms import PlatformUnavailable, get_platform
from semantic_layer.sl.validate import binding_columns, stale_pins

SEVERITY = {"ok": 0, "unknown": 1, "warning": 2, "failing": 3}


class Findings:
    """Per-definition findings, folded into one status by worst severity."""

    def __init__(self, model: Model) -> None:
        self.by_id: dict[str, list[dict[str, str]]] = {d.id: [] for d in model.all()}

    def add(self, def_id: str, check: str, severity: str, message: str) -> None:
        self.by_id.setdefault(def_id, []).append({"check": check, "severity": severity, "message": message})

    def status(self, def_id: str) -> str:
        findings = self.by_id.get(def_id, [])
        return max((f["severity"] for f in findings), key=lambda s: SEVERITY[s], default="ok")


def run(layer: Layer, today: str | None = None, full: bool = False) -> dict[str, Any]:
    model = load_model(layer)
    day = resolve_today(today)
    findings = Findings(model)
    unreachable = _schema_drift(layer, model, findings)
    _dates(model, findings, day)
    for stale in stale_pins(model):
        findings.add(stale["template"], "stale_pin", "failing",
                     f"pins {stale['pinned']} but it is now {stale['current']} — needs re-verification")
    catalog_changes, catalog_status = _catalog_refresh(layer, unreachable)
    if full:
        from semantic_layer.sl.phase2 import checks_full

        checks_full.run(layer, model, findings, day, unreachable)

    definitions = {def_id: {"status": findings.status(def_id), "findings": items}
                   for def_id, items in sorted(findings.by_id.items())}
    summary = {state: sum(1 for d in definitions.values() if d["status"] == state) for state in SEVERITY}
    report = {
        "generated_on": day.isoformat(), "mode": "full" if full else "phase1",
        "summary": summary, "definitions": definitions,
        "catalog": {"status": catalog_status, "changes": catalog_changes},
        "unreachable_platforms": sorted(unreachable),
    }
    write_json(layer.build / "health.json", report)
    report["issues"] = _write_issues(layer, model, definitions, catalog_changes)
    return report


def _schema_drift(layer: Layer, model: Model, findings: Findings) -> set[str]:
    """Bindings vs live metadata. Returns the platforms that could not be reached."""
    unreachable: set[str] = set()
    live: dict[tuple[str, str], set[str] | None] = {}
    adapters = {}
    for platform in PLATFORMS:
        try:
            adapters[platform] = get_platform(layer, platform)
        except PlatformUnavailable:
            unreachable.add(platform)
    for definition in model.all():
        for binding in definition.data.get("bindings", []) or []:
            platform, source = binding["platform"], binding["source"]
            if platform in unreachable:
                findings.add(definition.id, "schema_drift", "unknown", f"{platform} unreachable — drift not checked")
                continue
            key = (platform, catalog_mod.norm(source))
            if key not in live:
                try:
                    live[key] = {c.name.lower() for c in adapters[platform].columns(source)}
                except PlatformUnavailable as exc:
                    unreachable.add(platform)
                    findings.add(definition.id, "schema_drift", "unknown", f"{platform} unreachable: {exc}")
                    continue
            columns = live[key]
            if not columns:
                findings.add(definition.id, "schema_drift", "failing", f"table {platform}:{source} no longer exists")
                continue
            try:
                missing = binding_columns(definition, binding, layer.dialect(platform)) - columns
            except sqlglot.errors.ParseError as exc:
                findings.add(definition.id, "schema_drift", "failing", f"{platform} fragment does not parse: {exc}")
                continue
            for column in sorted(missing):
                findings.add(definition.id, "schema_drift", "failing",
                             f"column '{column}' is gone from {platform}:{source}")
    return unreachable


def _dates(model: Model, findings: Findings, day: dt.date) -> None:
    for definition in model.all():
        review_by = definition.data.get("review_by")
        if review_by and dt.date.fromisoformat(str(review_by)) < day:
            findings.add(definition.id, "review_expiry", "warning",
                         f"review overdue since {review_by} — steward {definition.owner.get('steward')} to re-confirm")
        expires = definition.data.get("expires")
        if expires and dt.date.fromisoformat(str(expires)) < day:
            findings.add(definition.id, "template_expiry", "failing",
                         f"template expired on {expires} — re-verify before the agent uses it again")


def _catalog_refresh(layer: Layer, unreachable: set[str]) -> tuple[list[dict[str, Any]], str]:
    if unreachable:
        return [], "unknown"
    try:
        fresh = catalog_mod.collect(layer)
    except PlatformUnavailable:
        return [], "unknown"
    changes = catalog_mod.diff(catalog_mod.load(layer), fresh)
    return changes, "changed" if changes else "unchanged"


def _write_issues(layer: Layer, model: Model, definitions: dict[str, Any],
                  catalog_changes: list[dict[str, Any]]) -> list[str]:
    folder = layer.build / "issues"
    if folder.exists():
        for old in folder.glob("*.md"):
            old.unlink()
    by_team: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for def_id, state in definitions.items():
        definition = model.find(def_id)
        if definition is None:
            continue
        for finding in state["findings"]:
            by_team.setdefault(definition.owner.get("team", "unowned"), []).append((def_id, finding))
    written = []
    for team, items in sorted(by_team.items()):
        stewards = sorted({model.get(i).owner.get("steward", "?") for i, _ in items})
        lines = [f"# Semantic layer health — {team}", "",
                 f"Stewards: {', '.join('@' + s for s in stewards)}", "",
                 "| definition | check | severity | finding |", "|---|---|---|---|"]
        lines += [f"| `{i}` | {f['check']} | {f['severity']} | {f['message']} |" for i, f in items]
        if catalog_changes:
            lines += ["", "Catalog changes this run:"] + [f"- {c}" for c in catalog_changes]
        path = folder / f"{team}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path.relative_to(layer.root).as_posix())
    return written


def markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [f"## Health checks ({report['mode']}, {report['generated_on']})",
             f"✅ ok {s['ok']} · ⚠️ warning {s['warning']} · ❌ failing {s['failing']} · ❔ unknown {s['unknown']}"]
    if report["unreachable_platforms"]:
        lines.append(f"❔ Unreachable: {', '.join(report['unreachable_platforms'])} — affected checks are UNKNOWN, "
                     "not passed")
    lines.append(f"Catalog: {report['catalog']['status']}"
                 + (f" ({len(report['catalog']['changes'])} changes)" if report["catalog"]["changes"] else ""))
    for def_id, state in report["definitions"].items():
        for finding in state["findings"]:
            icon = {"failing": "❌", "warning": "⚠️", "unknown": "❔"}.get(finding["severity"], "•")
            lines.append(f"- {icon} `{def_id}` [{finding['check']}] {finding['message']}")
    if report.get("issues"):
        lines.append("\nIssue bodies (one per team, not posted): " + ", ".join(report["issues"]))
    return "\n".join(lines)
