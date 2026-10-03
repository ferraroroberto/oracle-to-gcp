"""[Phase 2] ``sl checks run --full`` — the rest of the sweep.

Turn this on when the layer holds enough certified items that silent breakage
becomes likely. Everything reads data Phase 1 already captures:

- ``freshness``, ``nullValues``, ``duplicateValues``, ``rowCount``, ``invalidValues``
  (each definition's ``checks``) on its preferred platform
- ``reconcile`` across legacy ⇄ cloud bindings (migration divergence)
- anchor reconciliation: every certified template that reconciles with an anchor
  is re-run for each published period
- template re-runs: any template that no longer executes or lints is demoted to
  ``needs_reverification`` in ``health.json`` (the YAML is never rewritten by a check)

All probes are aggregate-only. An unreachable platform makes a check ``unknown``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from semantic_layer.sl import ask
from semantic_layer.sl.common import Layer
from semantic_layer.sl.model import Definition, Model
from semantic_layer.sl.platforms import PlatformUnavailable, get_platform
from semantic_layer.sl.sqlutil import substitute


def run(layer: Layer, model: Model, findings: Any, today: dt.date, unreachable: set[str]) -> None:
    for definition in model.all():
        for check in definition.data.get("checks", []) or []:
            try:
                _quality(layer, definition, check, findings, today, unreachable)
            except PlatformUnavailable as exc:
                findings.add(definition.id, check["type"], "unknown", f"platform unreachable: {exc}")
    _anchors_and_templates(layer, model, findings, unreachable)


def _scalar(layer: Layer, platform: str, sql: str) -> Any:
    rows = get_platform(layer, platform).query(sql, max_rows=1)
    return next(iter(rows[0].values())) if rows else None


def _quality(layer: Layer, definition: Definition, check: dict[str, Any], findings: Any, today: dt.date,
             unreachable: set[str]) -> None:
    kind = check["type"]
    platform = definition.preferred_platform()
    if platform is None:
        return
    if platform in unreachable and kind != "reconcile":
        findings.add(definition.id, kind, "unknown", f"{platform} unreachable")
        return
    binding = definition.binding(platform)
    source = binding["source"]
    if kind == "freshness":
        latest = _scalar(layer, platform, f"SELECT MAX({check['column']}) AS v FROM {source}")
        if latest is None:
            findings.add(definition.id, kind, "failing", f"{source} has no {check['column']} values")
            return
        lag = (today - dt.date.fromisoformat(str(latest)[:10])).days
        if lag > int(check["max_lag_days"]):
            findings.add(definition.id, kind, "failing",
                         f"latest {check['column']} is {latest} ({lag} days old, limit {check['max_lag_days']})")
    elif kind == "nullValues":
        nulls = _scalar(layer, platform, f"SELECT COUNT(*) - COUNT({check['column']}) AS v FROM {source}")
        if nulls > check.get("must_be", 0):
            findings.add(definition.id, kind, "failing", f"{nulls} null {check['column']} values in {source}")
    elif kind == "duplicateValues":
        cols = ", ".join(check["columns"])
        dups = _scalar(layer, platform, f"SELECT COUNT(*) AS v FROM (SELECT {cols} FROM {source} "
                                        f"GROUP BY {cols} HAVING COUNT(*) > 1) dup")
        if dups > check.get("must_be", 0):
            findings.add(definition.id, kind, "failing", f"{dups} duplicated ({cols}) keys in {source}")
    elif kind == "rowCount":
        per = check.get("per")
        where = f" WHERE {per} = (SELECT MAX({per}) FROM {source})" if per else ""
        rows = _scalar(layer, platform, f"SELECT COUNT(*) AS v FROM {source}{where}")
        low, high = check["between"]
        if not low <= rows <= high:
            findings.add(definition.id, kind, "failing", f"{rows} rows{' in the latest ' + per if per else ''}, "
                                                         f"expected {low}–{high}")
    elif kind == "invalidValues":
        values = ", ".join("'" + str(v).replace("'", "''") + "'" for v in check["valid_values"])
        bad = _scalar(layer, platform, f"SELECT COUNT(*) AS v FROM {source} WHERE {check['column']} NOT IN ({values})")
        if bad:
            findings.add(definition.id, kind, "failing", f"{bad} rows with an unexpected {check['column']}")
    elif kind == "reconcile":
        _reconcile_platforms(layer, definition, check, findings, unreachable)


def _reconcile_platforms(layer: Layer, definition: Definition, check: dict[str, Any], findings: Any,
                         unreachable: set[str]) -> None:
    platforms = check.get("platforms", ["legacy", "cloud"])
    if any(p in unreachable for p in platforms):
        findings.add(definition.id, "reconcile", "unknown", "a platform is unreachable — legacy/cloud not compared")
        return
    values: dict[str, float] = {}
    latest = None
    for platform in platforms:
        binding = definition.binding(platform)
        if binding and binding.get("time_column") and latest is None:
            latest = _scalar(layer, platform, f"SELECT MAX({binding['time_column']}) AS v FROM {binding['source']}")
    for platform in platforms:
        binding = definition.binding(platform)
        if binding is None:
            return
        measure = substitute(check["measure"], t="t", entity_key=f"t.{binding['key']}")
        conditions = [substitute(binding["filter"], t="t")] if binding.get("filter") else []
        if binding.get("time_column") and latest is not None:
            conditions.append(f"t.{binding['time_column']} = DATE '{str(latest)[:10]}'")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        values[platform] = float(_scalar(layer, platform, f"SELECT {measure} AS v FROM {binding['source']} t{where}"))
    low, high = min(values.values()), max(values.values())
    diff_pct = (high - low) / high * 100 if high else 0.0
    if diff_pct > float(check.get("tolerance_pct", 0.1)):
        findings.add(definition.id, "reconcile", "failing",
                     f"legacy vs cloud differ by {diff_pct:.2f}% ({values}) — migration divergence")


def _anchors_and_templates(layer: Layer, model: Model, findings: Any, unreachable: set[str]) -> None:
    for template in model.of_kind("template"):
        data = template.data
        anchor_id = (data.get("expected") or {}).get("reconciles_with")
        anchor = model.find(anchor_id) if anchor_id else None
        periods = [str(v["period"]) for v in (anchor.data.get("values", []) if anchor else [])] or [None]
        for period in periods:
            spec = {"entity": data["entity"], "metric": data["metric"], "dimensions": data["dimensions"]}
            if period:
                spec["period"] = period
            elif model.get(data["entity"]).binding("cloud") and model.get(data["entity"]).binding("cloud").get("time_column"):
                continue  # a snapshot template with no anchor period to replay
            if template.certified:
                answer = ask.run(layer, spec, model=model)
                if answer.get("platform") in unreachable or any("unreachable" in e for e in answer["errors"]):
                    findings.add(template.id, "template_rerun", "unknown", "platform unreachable")
                    continue
                if not answer["ok"] or answer.get("source", "").startswith("composed"):
                    findings.add(template.id, "template_rerun", "failing",
                                 "needs_reverification — the template no longer runs cleanly: "
                                 + "; ".join(answer["errors"] or ["not selected as a certified template"]))
                    continue
                recon = answer.get("reconciliation", {})
                if anchor and recon.get("status") == "differs":
                    message = (f"{period}: template total {recon['actual']} vs published {recon['expected']} "
                               f"({recon['diff_pct']}%)")
                    findings.add(template.id, "anchor_reconcile", "failing", "needs_reverification — " + message)
                    findings.add(anchor.id, "anchor_reconcile", "failing", message)
