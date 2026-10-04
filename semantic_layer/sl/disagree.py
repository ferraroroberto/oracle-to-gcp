"""``sl disagree`` — where teams compute the same thing differently, and by how much.

Phase 1 compares the harvested queries (one per pilot team): on every table two
or more sessions filter, it lists the filter sets that differ and runs each
variant's population count, so the report says *"446 vs 398"* rather than
*"the filters differ"*. Each finding becomes either a new distinct definition
or a correction — it is the pilot's strongest result to show stakeholders.

Phase 2 (``--all``) does the same across every query in the bulk harvest.
"""

from __future__ import annotations

import re
from typing import Any

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import Layer, read_json, read_yaml
from semantic_layer.sl.harvest import is_period_filter
from semantic_layer.sl.platforms import PlatformUnavailable, get_platform


def _sessions(layer: Layer) -> list[dict[str, Any]]:
    sessions = []
    root = layer.harvest / "sessions"
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if not (folder / "parse.json").exists():
            continue
        facts = read_json(folder / "parse.json")
        xref = read_json(folder / "xref.json") if (folder / "xref.json").exists() else {}
        answers = read_yaml(folder / "answers.yaml") if (folder / "answers.yaml").exists() else {}
        session = answers.get("session", {}) if answers else {}
        keys = {catalog_mod.norm(e.get("from_alias", "")): e.get("key") for e in answers.get("entities", []) or []}
        sessions.append({"id": folder.name, "team": session.get("team", "?"),
                         "platform": session.get("platform") or xref.get("platform"),
                         "query": facts.get("query"), "facts": facts, "keys": keys,
                         "entity_ids": [e["id"] for e in answers.get("entities", []) or []]})
    return sessions


def variants_from_sessions(layer: Layer) -> list[dict[str, Any]]:
    """One variant per (session, table): the non-period filters it applies."""
    catalog = catalog_mod.load(layer)
    variants = []
    for session in _sessions(layer):
        by_table: dict[str, list[dict[str, Any]]] = {}
        for item in session["facts"]["filters"]:
            if item.get("table"):
                by_table.setdefault(item["table"], []).append(item)
        for table, items in by_table.items():
            platform = session["platform"]
            cloud_name = table if platform == "cloud" else (catalog.other_side("legacy", table) or table)
            variants.append({
                "source": session["id"], "team": session["team"], "platform": platform, "table": table,
                "table_key": catalog_mod.norm(cloud_name), "query": session["query"],
                "entities": session["entity_ids"],
                "terms": sorted(i["canonical"] for i in items if not is_period_filter(i)),
                "period_predicates": [i["predicate"] for i in items if is_period_filter(i)],
                "predicates": [i["predicate"] for i in items],
                "alias": items[0]["alias"],
                "key": _key_for(session, items[0]["alias"], table),
            })
    return variants


def _key_for(session: dict[str, Any], alias: str | None, table: str) -> str | None:
    for join in session["facts"]["joins"]:
        for key in join["keys"]:
            for source in key["left_source"] + key["right_source"]:
                src_table, column = source.rsplit(".", 1)
                if catalog_mod.norm(src_table) == catalog_mod.norm(table) and column.lower().endswith("customer_id"):
                    return column
    return None


_LITERAL = re.compile(r"'[^']*'|\b\d+(\.\d+)?\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _shape(terms: list[str]) -> tuple[str, ...]:
    """Filter set with literal values blanked: equal shapes = same logic, different parameter values."""
    return tuple(sorted(_LITERAL.sub("?", t) for t in terms))


def _align_periods(group: list[dict[str, Any]]) -> None:
    """Evaluate variants at the same period: the latest one used on their platform.

    Predicates are only swapped within one platform — they are text in that platform's dialect.
    """
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for variant in group:
        if variant["period_predicates"]:
            by_platform.setdefault(variant["platform"], []).append(variant)
    for dated in by_platform.values():
        _align_within_platform(dated)


def _align_within_platform(dated: list[dict[str, Any]]) -> None:
    if len(dated) < 2:
        return
    reference = max(dated, key=lambda v: max(_ISO.findall(" ".join(v["period_predicates"])) or [""]))
    for variant in dated:
        if variant is reference:
            continue
        swapped = [re.sub(rf"\b{re.escape(reference['alias'])}\.", f"{variant['alias']}.", p)
                   for p in reference["period_predicates"]] if reference["alias"] and variant["alias"] \
            else list(reference["period_predicates"])
        variant["predicates"] = [p for p in variant["predicates"] if p not in variant["period_predicates"]] + swapped
        variant["period_predicates"] = swapped


def compare(layer: Layer, variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group variants by table; every table with ≥2 distinct filter *logics* is a disagreement.

    Variants that differ only in literal values (region = 'North' vs 'South') are parameters, not
    disagreements. Population counts are taken at one common period so the numbers compare.
    """
    by_table: dict[str, list[dict[str, Any]]] = {}
    for variant in variants:
        by_table.setdefault(variant["table_key"], []).append(variant)
    disagreements = []
    for table_key, group in sorted(by_table.items()):
        if len({tuple(v["terms"]) for v in group}) < 2 or len({_shape(v["terms"]) for v in group}) < 2:
            continue
        _align_periods(group)
        current_state = not any(v["period_predicates"] for v in group)
        common = set(group[0]["terms"]).intersection(*(set(v["terms"]) for v in group[1:]))
        rows = []
        for variant in group:
            count, detail = _population(layer, variant, current_state)
            rows.append({"source": variant["source"], "team": variant["team"], "platform": variant["platform"],
                         "query": variant["query"], "entities": variant["entities"],
                         "only_here": sorted(set(variant["terms"]) - common), "count": count, "detail": detail})
        counts = [r["count"] for r in rows if isinstance(r["count"], int | float)]
        spread = (max(counts) - min(counts)) if len(counts) >= 2 else None
        disagreements.append({
            "table": table_key, "common": sorted(common), "variants": rows,
            "spread": spread, "spread_pct": round(spread / max(counts) * 100, 1) if spread is not None and max(counts)
            else None,
        })
    return disagreements


def _population(layer: Layer, variant: dict[str, Any], current_state: bool) -> tuple[int | None, str]:
    """Count the population each variant selects (aggregate only), on its own platform and dialect."""
    if not variant["period_predicates"] and not current_state:
        return None, "no period filter while the others pin one — not comparable"
    alias = variant["alias"] or ""
    measure = f"COUNT(DISTINCT {alias + '.' if alias else ''}{variant['key']})" if variant["key"] else "COUNT(*)"
    where = f" WHERE {' AND '.join(variant['predicates'])}" if variant["predicates"] else ""
    sql = f"SELECT {measure} AS n FROM {variant['table']} {alias}{where}"
    try:
        rows = get_platform(layer, variant["platform"]).query(sql)
    except PlatformUnavailable as exc:
        return None, f"unknown — {exc}"
    when = ", ".join(variant["period_predicates"]) or "current state"
    return int(rows[0]["n"]), f"{measure} at {when}"


def run(layer: Layer, all_queries: bool = False) -> dict[str, Any]:
    if all_queries:
        from semantic_layer.sl.phase2 import bulk

        variants = bulk.variants(layer)
        scope = "bulk harvest (all queries)"
    else:
        variants = variants_from_sessions(layer)
        scope = "harvest sessions"
    return {"scope": scope, "variants": len(variants), "disagreements": compare(layer, variants)}


def markdown(report: dict[str, Any]) -> str:
    lines = [f"## Disagreement report — {report['scope']}"]
    if not report["disagreements"]:
        lines.append("✅ No table is filtered differently by different queries.")
        return "\n".join(lines)
    for item in report["disagreements"]:
        counts = [f"{v['team']} **{v['count']}**" for v in item["variants"] if v["count"] is not None]
        spread = f" — spread {item['spread']} ({item['spread_pct']}%)" if item["spread"] is not None else ""
        lines.append(f"\n### `{item['table']}`: {len(item['variants'])} variants — {' vs '.join(counts)}{spread}")
        if item["common"]:
            lines.append(f"Shared filters: {', '.join(f'`{t}`' for t in item['common'])}")
        for variant in item["variants"]:
            only = ", ".join(f"`{t}`" for t in variant["only_here"]) or "(nothing beyond the shared filters)"
            entities = f" → {', '.join(variant['entities'])}" if variant.get("entities") else ""
            lines.append(f"- **{variant['team']}** ({variant['source']}{entities}): only here {only} — "
                         f"{variant['count'] if variant['count'] is not None else variant['detail']}")
    lines.append("\nEach difference should end as a distinct, named definition or as a correction — never as two "
                 "numbers both called the same thing.")
    return "\n".join(lines)
