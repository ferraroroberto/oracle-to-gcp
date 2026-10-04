"""[Phase 2] ``sl harvest bulk`` — usage ranking over many queries.

Turn this on when a third team joins, or when you need to decide what to define
next. It parses every query in a folder and/or the warehouse's query history
(the mock's ``ops.query_history`` stands in for BigQuery's
``INFORMATION_SCHEMA.JOBS_BY_PROJECT``) with the same parser the single-query
harvest uses, then ranks:

- tables by how many queries read them, and catalog tables nobody reads
- join pairs by frequency → candidate relationships not yet declared
- filter literals per table → candidate named filters / entity variants

Output: ``harvest/bulk/usage.json`` + ``usage.md``. ``sl disagree --all`` reads it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import PLATFORMS, Layer, LayerError, read_json, write_json
from semantic_layer.sl.harvest import is_period_filter, parse_sql
from semantic_layer.sl.model import load_model
from semantic_layer.sl.platforms import get_platform


def _collect(layer: Layer, folder: Path | None, history: bool, dialect: str | None) -> list[dict[str, Any]]:
    dialect_to_platform = {layer.dialect(p): p for p in PLATFORMS}
    queries: list[dict[str, Any]] = []
    if folder:
        for path in sorted(folder.glob("*.sql")):
            suffix = path.stem.rsplit(".", 1)[-1] if "." in path.stem else None
            file_dialect = suffix if suffix in dialect_to_platform else dialect
            if file_dialect not in dialect_to_platform:
                raise LayerError(f"{path.name}: no dialect — name it <name>.<dialect>.sql or pass --dialect")
            queries.append({"id": path.name, "team": None, "user": None, "platform": dialect_to_platform[file_dialect],
                            "sql": path.read_text(encoding="utf-8")})
    if history:
        cfg = layer.config.get("harvest", {})
        platform = cfg.get("history_platform", "cloud")
        table = cfg.get("history_table", "ops.query_history")
        rows = get_platform(layer, platform).query(
            f"SELECT query_id, user_name, team, query_text FROM {table} ORDER BY query_id", max_rows=100000)
        queries += [{"id": f"history:{r['query_id']}", "team": r["team"], "user": r["user_name"],
                     "platform": platform, "sql": r["query_text"]} for r in rows]
    if not queries:
        raise LayerError("Nothing to harvest — pass --folder and/or --history")
    return queries


def run(layer: Layer, folder: Path | None = None, history: bool = False, dialect: str | None = None) -> dict[str, Any]:
    catalog = catalog_mod.load(layer)
    model = load_model(layer)
    queries = _collect(layer, folder, history, dialect)

    def cloud_name(platform: str, table: str) -> str:
        return catalog_mod.norm(table if platform == "cloud" else (catalog.other_side("legacy", table) or table))

    table_use: Counter[str] = Counter()
    table_teams: dict[str, set[str]] = defaultdict(set)
    join_use: Counter[tuple[str, str]] = Counter()
    filter_use: dict[str, Counter[str]] = defaultdict(Counter)
    parsed, failures = [], []
    for query in queries:
        try:
            facts = parse_sql(query["sql"], layer.dialect(query["platform"]))
        except LayerError as exc:
            failures.append({"id": query["id"], "error": str(exc)})
            continue
        tables = {cloud_name(query["platform"], t["table"]) for t in facts["tables"]}
        for table in tables:
            table_use[table] += 1
            if query["team"]:
                table_teams[table].add(query["team"])
        for join in facts["joins"]:
            for key in join["keys"]:
                for left in key["left_source"]:
                    for right in key["right_source"]:
                        lt, lc = left.rsplit(".", 1)
                        rt, rc = right.rsplit(".", 1)
                        pair = sorted([f"{cloud_name(query['platform'], lt)}.{lc.lower()}",
                                       f"{cloud_name(query['platform'], rt)}.{rc.lower()}"])
                        join_use[(pair[0], pair[1])] += 1
        per_table: dict[str, dict[str, Any]] = {}
        for item in facts["filters"]:
            if not item.get("table"):
                continue
            key = cloud_name(query["platform"], item["table"])
            slot = per_table.setdefault(key, {"table": item["table"], "alias": item["alias"], "predicates": [],
                                              "terms": [], "period_predicates": []})
            slot["predicates"].append(item["predicate"])
            if is_period_filter(item):
                slot["period_predicates"].append(item["predicate"])
            else:
                slot["terms"].append(item["canonical"])
                filter_use[key][item["canonical"]] += 1
        measure_key = next((src.rsplit(".", 1) for agg in facts["aggregates"] if agg["distinct"]
                            for src in agg["sources"]), None)
        parsed.append({"id": query["id"], "team": query["team"], "user": query["user"],
                       "platform": query["platform"], "filters": per_table,
                       "distinct_key": {"table": cloud_name(query["platform"], measure_key[0]),
                                        "column": measure_key[1]} if measure_key else None})

    declared = set()
    for rel in model.of_kind("relationship"):
        for platform, condition in (rel.data.get("join") or {}).items():
            source = model.find(rel.data["from"])
            target = model.find(rel.data["to"])
            if not (source and target and source.binding(platform) and target.binding(platform)):
                continue
            declared.add((cloud_name(platform, source.binding(platform)["source"]),
                          cloud_name(platform, target.binding(platform)["source"])))
    candidates = []
    for (left, right), count in join_use.most_common():
        tables = (left.rsplit(".", 1)[0], right.rsplit(".", 1)[0])
        covered = tables in declared or tables[::-1] in declared
        candidates.append({"join": f"{left} = {right}", "queries": count, "declared": covered})

    used_tables = set(table_use)
    bound_tables = {cloud_name(b["platform"], b["source"]) for d in model.all()
                    for b in d.data.get("bindings", []) or []}
    catalog_tables = sorted({cloud_name(p, t) for p, t in catalog.all_tables()})
    report = {
        "queries": len(queries), "parsed": len(parsed), "failures": failures,
        "tables": [{"table": t, "queries": n, "teams": sorted(table_teams[t]), "defined": t in bound_tables}
                   for t, n in table_use.most_common()],
        "unused_catalog_tables": [t for t in catalog_tables if t not in used_tables],
        "join_candidates": candidates,
        "filters": {t: [{"term": term, "queries": n} for term, n in c.most_common()] for t, c in sorted(filter_use.items())},
        "coverage": {"top_tables": len(table_use), "defined": sum(1 for t in table_use if t in bound_tables)},
        "query_details": parsed,
    }
    write_json(layer.harvest / "bulk" / "usage.json", report)
    (layer.harvest / "bulk" / "usage.md").write_text(markdown(report) + "\n", encoding="utf-8")
    return report


def variants(layer: Layer) -> list[dict[str, Any]]:
    """Collapse the bulk harvest into one variant per distinct (table, filter set) for ``sl disagree --all``."""
    path = layer.harvest / "bulk" / "usage.json"
    if not path.exists():
        raise LayerError("No bulk harvest yet — run `sl harvest bulk --history` (Phase 2) first")
    report = read_json(path)
    grouped: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for query in report["query_details"]:
        for table_key, slot in query["filters"].items():
            group_key = (table_key, tuple(sorted(slot["terms"])))
            entry = grouped.get(group_key)
            if entry is None:
                key = query["distinct_key"]
                entry = grouped[group_key] = {
                    "source": "", "team": "", "platform": query["platform"], "table": slot["table"],
                    "table_key": table_key, "query": query["id"], "entities": [], "terms": list(group_key[1]),
                    "period_predicates": slot["period_predicates"], "predicates": slot["predicates"],
                    "alias": slot["alias"],
                    "key": key["column"] if key and key["table"] == table_key else None,
                    "_queries": [], "_teams": set(),
                }
            entry["_queries"].append(query["id"])
            if query["team"]:
                entry["_teams"].add(query["team"])
    out = []
    for entry in grouped.values():
        entry["source"] = f"{len(entry.pop('_queries'))} queries"
        entry["team"] = ", ".join(sorted(entry.pop("_teams"))) or "unknown team"
        out.append(entry)
    return out


def markdown(report: dict[str, Any]) -> str:
    lines = [f"## Bulk harvest — {report['parsed']}/{report['queries']} queries parsed",
             f"Coverage: {report['coverage']['defined']} of the {report['coverage']['top_tables']} tables in use "
             "have at least one definition.", "", "| table | queries | teams | defined |", "|---|---|---|---|"]
    lines += [f"| {t['table']} | {t['queries']} | {', '.join(t['teams'])} | {'✅' if t['defined'] else '—'} |"
              for t in report["tables"]]
    lines += ["", "**Join candidates** (most used first)"]
    lines += [f"- {c['join']} — {c['queries']} queries {'(declared)' if c['declared'] else '⬅ not declared yet'}"
              for c in report["join_candidates"]]
    if report["unused_catalog_tables"]:
        lines += ["", "**Catalog tables nobody queries:** " + ", ".join(report["unused_catalog_tables"])]
    for table, terms in report["filters"].items():
        lines.append(f"\n**Filters on {table}:** " + "; ".join(f"`{t['term']}` ×{t['queries']}" for t in terms[:8]))
    if report["failures"]:
        lines += ["", f"⚠️ {len(report['failures'])} queries did not parse"]
    return "\n".join(lines)
