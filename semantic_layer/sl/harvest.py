"""``sl harvest`` — work backwards from SQL people already trust.

Rule: **parse deterministically, interpret with the LLM.** This module extracts
structure (tables, CTEs, joins, filters with their literals, aggregates,
outputs and where each output comes from), cross-references it against the
catalog and the certified layer, runs aggregate-only probes, and generates the
interview questions. The ``semantic-harvest`` skill conducts the interview and
records the answers as ``answers.yaml``; :func:`draft` then merges parse facts
and answers into draft definitions with provenance.

Session layout: ``harvest/sessions/<session-id>/{parse.json, xref.json,
probes.json, questions.json, interview.md, answers.yaml}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import KIND_DIRS, Layer, LayerError, read_json, read_yaml, write_json, write_yaml
from semantic_layer.sl.model import Model, load_model
from semantic_layer.sl.platforms import MockDuckDBPlatform, get_platform
from semantic_layer.sl.sqlutil import canonical, is_time_equality, literals, predicate_set, split_conjuncts

MAX_DISTRIBUTION_VALUES = 50  # wider than this is not a code column — never probe its values


# --------------------------------------------------------------------------- parse


def _join_type(join: exp.Join) -> str:
    side = (join.side or "").lower()
    kind = (join.kind or "").lower()
    return side or kind or "inner"


@dataclass
class _Scope:
    name: str  # "main" or "cte:<name>"
    select: exp.Select
    aliases: dict[str, str]  # alias → table name or "cte:<name>"


def _scopes(tree: exp.Expression) -> tuple[list[_Scope], dict[str, exp.Select]]:
    ctes: dict[str, exp.Select] = {}
    for cte in tree.find_all(exp.CTE):
        if isinstance(cte.this, exp.Select):
            ctes[cte.alias_or_name.lower()] = cte.this
    scopes: list[_Scope] = []

    def scope_for(name: str, select: exp.Select) -> _Scope:
        aliases: dict[str, str] = {}
        tables = []
        from_clause = select.args.get("from") or select.args.get("from_")
        if from_clause is not None:
            tables.append(from_clause.this)
        tables += [j.this for j in select.args.get("joins") or []]
        for table in tables:
            if not isinstance(table, exp.Table):
                continue
            physical = table.name if not table.db else f"{table.db}.{table.name}"
            target = f"cte:{table.name.lower()}" if not table.db and table.name.lower() in ctes else physical
            aliases[table.alias_or_name] = target
        return _Scope(name=name, select=select, aliases=aliases)

    for cte_name, select in ctes.items():
        scopes.append(scope_for(f"cte:{cte_name}", select))
    main = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if main is not None:
        scopes.append(scope_for("main", main))
    return scopes, ctes


def _resolve_column(column: exp.Column, scope: _Scope, scopes: dict[str, _Scope]) -> list[str]:
    """Follow aliases (and CTE projections) back to ``schema.table.COLUMN``."""
    target = scope.aliases.get(column.table) if column.table else None
    if target is None and not column.table and len(scope.aliases) == 1:
        target = next(iter(scope.aliases.values()))
    if target is None:
        return []
    if not target.startswith("cte:"):
        return [f"{target}.{column.name}"]
    cte_scope = scopes.get(target)
    if cte_scope is None:
        return []
    for projection in cte_scope.select.expressions:
        if projection.alias_or_name.lower() == column.name.lower():
            found: list[str] = []
            for inner in projection.find_all(exp.Column):
                found += _resolve_column(inner, cte_scope, scopes)
            return found
    return []


def _physical_table(alias: str, scope: _Scope, scopes: dict[str, _Scope]) -> str | None:
    target = scope.aliases.get(alias)
    while target and target.startswith("cte:"):
        inner = scopes.get(target)
        if inner is None or len(inner.aliases) != 1:
            return None
        target = next(iter(inner.aliases.values()))
    return target


def _line_of(sql: str, pattern: str) -> int:
    for number, line in enumerate(sql.splitlines(), start=1):
        if re.search(pattern, line, flags=re.IGNORECASE):
            return number
    return 1


def parse_sql(sql: str, dialect: str) -> dict[str, Any]:
    """Structural facts about one query. Pure function: no catalog, no database."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise LayerError(f"Could not parse the {dialect} query: {exc}") from exc
    if not statements:
        raise LayerError("No SQL statement found")
    tree = statements[-1]
    scope_list, _ = _scopes(tree)
    scopes = {s.name: s for s in scope_list}
    lines = sql.splitlines()
    first = next((i for i, line in enumerate(lines, 1) if line.strip() and not line.strip().startswith("--")), 1)
    facts: dict[str, Any] = {
        "dialect": dialect,
        "statements": len(statements),
        "lines": [first, len(lines)],
        "tables": [], "ctes": [], "joins": [], "filters": [], "aggregates": [], "group_by": [], "outputs": [],
        "unused_joins": [],
    }
    for scope in scope_list:
        for alias, target in scope.aliases.items():
            if not target.startswith("cte:"):
                facts["tables"].append({"table": target, "alias": alias, "scope": scope.name})
        if scope.name.startswith("cte:"):
            name = scope.name[4:]
            facts["ctes"].append({
                "name": name,
                "tables": sorted(t for t in scope.aliases.values() if not t.startswith("cte:")),
                "outputs": [p.alias_or_name for p in scope.select.expressions],
                "line": _line_of(sql, rf"\b{re.escape(name)}\s+AS\s*\("),
            })
        where = scope.select.args.get("where")
        for term in split_conjuncts(where.this if where is not None else None):
            term_aliases = sorted({c.table for c in term.find_all(exp.Column) if c.table})
            if not term_aliases and len(scope.aliases) == 1:
                term_aliases = [next(iter(scope.aliases))]
            alias = term_aliases[0] if len(term_aliases) == 1 else None
            facts["filters"].append({
                "scope": scope.name,
                "alias": alias,
                "table": _physical_table(alias, scope, scopes) if alias else None,
                "predicate": term.sql(dialect=dialect),
                "canonical": canonical(term),
                "columns": sorted({c.name for c in term.find_all(exp.Column)}),
                "literals": literals(term),
            })
        for join in scope.select.args.get("joins") or []:
            right = join.this
            if not isinstance(right, exp.Table):
                continue
            on = join.args.get("on")
            keys = []
            for term in split_conjuncts(on):
                if isinstance(term, exp.EQ) and isinstance(term.left, exp.Column) and isinstance(term.right, exp.Column):
                    a, b = term.left, term.right
                    if a.table == right.alias_or_name:
                        a, b = b, a
                    keys.append({"left_alias": a.table, "left": a.name, "right_alias": b.table, "right": b.name,
                                 "left_source": _resolve_column(a, scope, scopes),
                                 "right_source": _resolve_column(b, scope, scopes)})
            left_alias = keys[0]["left_alias"] if keys else None
            facts["joins"].append({
                "scope": scope.name,
                "type": _join_type(join),
                "right_alias": right.alias_or_name,
                "right_table": scope.aliases.get(right.alias_or_name),
                "left_alias": left_alias,
                "left_table": scope.aliases.get(left_alias) if left_alias else None,
                "condition": on.sql(dialect=dialect) if on is not None else None,
                "keys": keys,
            })
            # A joined table whose alias is used nowhere but its own ON clause.
            used_elsewhere = any(
                c.table == right.alias_or_name
                for c in scope.select.find_all(exp.Column)
                if not (on is not None and c.find_ancestor(exp.Join) is join)
            )
            if not used_elsewhere:
                facts["unused_joins"].append({"alias": right.alias_or_name,
                                              "table": scope.aliases.get(right.alias_or_name),
                                              "type": _join_type(join)})
        if scope.name == "main":
            for projection in scope.select.expressions:
                sources = sorted({src for c in projection.find_all(exp.Column)
                                  for src in _resolve_column(c, scope, scopes)})
                agg = projection.find(exp.AggFunc)
                entry = {"name": projection.alias_or_name, "expression": projection.unalias().sql(dialect=dialect),
                         "sources": sources}
                facts["outputs"].append(entry)
                if agg is not None:
                    facts["aggregates"].append({"name": projection.alias_or_name,
                                                "function": agg.key.upper(),
                                                "distinct": agg.find(exp.Distinct) is not None,
                                                "expression": projection.unalias().sql(dialect=dialect),
                                                "sources": sources})
            group = scope.select.args.get("group")
            facts["group_by"] = [g.sql(dialect=dialect) for g in (group.expressions if group else [])]
    return facts


# --------------------------------------------------------------------------- cross-reference


def cross_reference(facts: dict[str, Any], layer: Layer, platform: str, model: Model | None = None,
                    catalog: catalog_mod.Catalog | None = None) -> dict[str, Any]:
    """Compare parse facts with the catalog and the existing definitions."""
    model = model or load_model(layer)
    catalog = catalog or catalog_mod.load(layer)
    dialect = layer.dialect(platform)
    tables = []
    for table in sorted({t["table"] for t in facts["tables"]}):
        row = catalog.counterpart(platform, table)
        tables.append({"table": table, "in_catalog": catalog.has_table(platform, table),
                       "migration_status": row.get("status") if row else "unknown",
                       "counterpart": catalog.other_side(platform, table)})

    filters_by_table: dict[str, list[dict[str, Any]]] = {}
    for item in facts["filters"]:
        if item.get("table"):
            filters_by_table.setdefault(catalog_mod.norm(item["table"]), []).append(item)

    comparisons = []
    for table_key, items in sorted(filters_by_table.items()):
        candidates = [d for d in model.all() if d.kind in ("entity", "filter") and d.binding(platform)
                      and catalog_mod.norm(d.binding(platform)["source"]) == table_key]
        query_terms = {i["canonical"] for i in items}
        if not candidates:
            comparisons.append({"table": items[0]["table"], "outcome": "new", "query_terms": sorted(query_terms)})
            continue
        table_comparisons = []
        for definition in candidates:
            binding = definition.binding(platform)
            def_terms = set(predicate_set(binding["filter"], dialect)) if binding.get("filter") else set()
            time_col = (binding.get("time_column") or "").lower()
            compared = {t for t in query_terms
                        if not (time_col and is_time_equality(t, time_col))}
            if compared == def_terms:
                outcome = "matches"
            else:
                outcome = "differs"
            table_comparisons.append({
                "table": items[0]["table"], "definition": definition.id, "status": definition.status,
                "outcome": outcome,
                "only_in_query": sorted(compared - def_terms),
                "only_in_definition": sorted(def_terms - compared),
            })
        # A query that exactly matches one definition is using it; differences from the
        # table's other definitions are expected, not conflicts worth an interview question.
        exact = [c for c in table_comparisons if c["outcome"] == "matches"]
        comparisons += exact or table_comparisons
    return {"platform": platform, "tables": tables, "filter_comparisons": comparisons}


# --------------------------------------------------------------------------- probes


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_FUNCTIONS = re.compile(r"\b(DATE|TO_DATE|INTERVAL|ADD_MONTHS|DATE_SUB|DATE_ADD|CAST)\b", flags=re.IGNORECASE)


def is_period_filter(item: dict[str, Any]) -> bool:
    """A filter pinning a date literal is the period parameter, not business logic."""
    return any(_ISO_DATE.match(str(lit)) for lit in item["literals"])


def _distribution_targets(facts: dict[str, Any]) -> list[tuple[str, str]]:
    targets: set[tuple[str, str]] = set()
    for item in facts["filters"]:
        if not item.get("table") or not item["literals"] or len(item["columns"]) != 1:
            continue
        if is_period_filter(item) or _DATE_FUNCTIONS.search(item["predicate"]):
            continue  # dates and date windows are not code columns
        targets.add((item["table"], item["columns"][0]))
    return sorted(targets)


def _q(dialect: str, table: str, *select: str, where: str | None = None, group: str | None = None,
       order: str | None = None, limit: int | None = None) -> str:
    """Build a small aggregate query and render it for the platform (LIMIT vs FETCH FIRST)."""
    query = exp.select(*select).from_(table)
    if where:
        query = query.where(where)
    if group:
        query = query.group_by(group)
    if order:
        query = query.order_by(order)
    if limit:
        query = query.limit(limit)
    return query.sql(dialect=dialect)


def probe(facts: dict[str, Any], layer: Layer, platform: str) -> dict[str, Any]:
    """Aggregate-only evidence for the interview. Never returns row-level data."""
    adapter = get_platform(layer, platform)
    dialect = layer.dialect(platform)
    result: dict[str, Any] = {"platform": platform, "distributions": [], "keys": [], "null_rates": []}

    for table, column in _distribution_targets(facts):
        distinct = adapter.query(_q(dialect, table, f"COUNT(DISTINCT {column}) AS n"))[0]["n"]
        if distinct > MAX_DISTRIBUTION_VALUES:
            result["distributions"].append({"table": table, "column": column, "skipped": f"{distinct} distinct values"})
            continue
        rows = adapter.query(_q(dialect, table, f"{column} AS code_value", "COUNT(*) AS n", group=column,
                                order="n DESC", limit=MAX_DISTRIBUTION_VALUES))
        total = sum(r["n"] for r in rows) or 1
        result["distributions"].append({
            "table": table, "column": column,
            "values": [{"value": r["code_value"], "share": round(r["n"] / total, 3)} for r in rows],
        })

    seen: set[tuple[str, str]] = set()
    for join in facts["joins"]:
        for key in join["keys"]:
            for side in ("left_source", "right_source"):
                for source in key[side]:
                    table, column = source.rsplit(".", 1)
                    if (table, column) in seen:
                        continue
                    seen.add((table, column))
                    stats = adapter.query(_q(dialect, table, "COUNT(*) AS row_count",
                                             f"COUNT(DISTINCT {column}) AS distinct_keys",
                                             f"COUNT(*) - COUNT({column}) AS null_keys"))[0]
                    result["keys"].append({"table": table, "column": column, "rows": stats["row_count"],
                                           "distinct": stats["distinct_keys"],
                                           "unique": stats["row_count"] == stats["distinct_keys"]})
                    result["null_rates"].append({"table": table, "column": column,
                                                 "null_share": round(stats["null_keys"] / max(stats["row_count"], 1), 4)})
    for join in facts["joins"]:
        join["inferred_cardinality"] = _cardinality(join, result["keys"])
    result["join_cardinality"] = [{"right_alias": j["right_alias"], "cardinality": j["inferred_cardinality"]}
                                  for j in facts["joins"]]
    return result


def _cardinality(join: dict[str, Any], keys: list[dict[str, Any]]) -> str | None:
    if not join["keys"]:
        return None
    key = join["keys"][0]
    if not (key["left_source"] and key["right_source"]):
        return None
    lookup = {(k["table"], k["column"]): k["unique"] for k in keys}
    left_unique = lookup.get(tuple(key["left_source"][0].rsplit(".", 1)))
    right_unique = lookup.get(tuple(key["right_source"][0].rsplit(".", 1)))
    if left_unique is None or right_unique is None:
        return None
    if right_unique:
        return "one_to_one" if left_unique else "many_to_one"
    return "one_to_many" if left_unique else "many_to_many"


# --------------------------------------------------------------------------- questions


def questions(facts: dict[str, Any], xref: dict[str, Any], probes: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The interview script: only what the code cannot say, highest value first."""
    out: list[dict[str, Any]] = []

    def add(topic: str, text: str, guess: str | None = None, **refs: Any) -> None:
        out.append({"id": f"q{len(out) + 1:02d}", "topic": topic, "question": text, "guess": guess, **refs})

    add("purpose", "What business question does this query answer, and who uses the result?")
    for comparison in xref.get("filter_comparisons", []):
        if comparison["outcome"] == "differs":
            add("conflict",
                f"On {comparison['table']} your filters differ from certified {comparison['definition']}: "
                f"only in your query {comparison['only_in_query']}, only in the definition "
                f"{comparison['only_in_definition']}. A different concept, or should it use the certified one?",
                definition=comparison["definition"])
    distributions = {(d["table"], d["column"]): d for d in (probes or {}).get("distributions", [])}
    for item in facts["filters"]:
        if not item["literals"] or is_period_filter(item):
            continue  # period filters are asked about once, under 'parameters'
        dist = distributions.get((item.get("table"), item["columns"][0] if item["columns"] else None))
        evidence = ""
        if dist and "values" in dist:
            evidence = " Observed: " + ", ".join(f"{v['value']} {v['share']:.0%}" for v in dist["values"][:6]) + "."
        add("literal", f"`{item['predicate']}` — what does this value mean, and why is it included or excluded?"
                       f"{evidence}", predicate=item["predicate"])
    for cte in facts["ctes"]:
        add("concept", f"CTE `{cte['name']}` (from {', '.join(cte['tables'])}): is this a named business concept? "
                       "What would you call it, and what is one row?", cte=cte["name"])
    for join in facts["joins"]:
        cardinality = join.get("inferred_cardinality")
        guess = f"probes say {cardinality}" if cardinality else None
        if join["type"] == "left":
            add("join", f"Why LEFT JOIN {join['right_table']} ({join['right_alias']})? What does a missing match mean?",
                guess, alias=join["right_alias"])
        else:
            add("join", f"Join to {join['right_table']} ({join['right_alias']}) on {join['condition']}: "
                        "is every row expected to match exactly one?", guess, alias=join["right_alias"])
    for unused in facts["unused_joins"]:
        add("unused", f"{unused['table']} ({unused['alias']}) is joined but never used. Leftover, or a hidden filter?",
            alias=unused["alias"])
    add("grain", "What is one row of the output?",
        guess=", ".join(facts["group_by"]) if facts["group_by"] else None)
    add("anchor", "Which published figure should this match (report, page, period)?")
    add("caveats", "What breaks this query, or what have people got wrong with it before?")
    add("owners", "Who owns each source table, and who is the steward to ask when this breaks?",
        tables=sorted({t["table"] for t in facts["tables"]}))
    period_literals = sorted({str(lit) for f in facts["filters"] if is_period_filter(f) for lit in f["literals"]})
    add("parameters", "Which parts would people want to change (period, region, …)?",
        guess=f"the period ({', '.join(period_literals)})" if period_literals else None)
    return out


# --------------------------------------------------------------------------- session runner


def session_dir(layer: Layer, session_id: str) -> Path:
    return layer.harvest / "sessions" / session_id


def analyse(layer: Layer, query_path: Path, platform: str, session_id: str, run_probes: bool = True) -> Path:
    """parse → cross-reference → probe → questions, written into the session folder."""
    sql = query_path.read_text(encoding="utf-8")
    facts = parse_sql(sql, layer.dialect(platform))
    facts["query"] = _relative(layer, query_path)
    folder = session_dir(layer, session_id)
    xref = cross_reference(facts, layer, platform)
    probes = probe(facts, layer, platform) if run_probes else None
    write_json(folder / "parse.json", facts)
    write_json(folder / "xref.json", xref)
    if probes is not None:
        write_json(folder / "probes.json", probes)
    write_json(folder / "questions.json", questions(facts, xref, probes))
    return folder


def _relative(layer: Layer, path: Path) -> str:
    try:
        return path.resolve().relative_to(layer.root).as_posix()
    except ValueError:
        return path.as_posix()


# --------------------------------------------------------------------------- draft


class _Placeholders:
    """Render fragments with {t}/{from}/{to} placeholders via sentinel identifiers."""

    SENTINELS = {"XQFROM": "{from}", "XQTO": "{to}", "XQT": "{t}"}

    @classmethod
    def render(cls, node: exp.Expression, alias_to_placeholder: dict[str, str], dialect: str,
               column_map: dict[str, dict[str, str]] | None = None) -> str:
        reverse = {v: k for k, v in cls.SENTINELS.items()}
        copy = node.copy()
        for column in copy.find_all(exp.Column):
            alias = column.table
            if alias in alias_to_placeholder:
                if column_map and alias in column_map:
                    mapped = column_map[alias].get(column.name.upper()) or column_map[alias].get(column.name)
                    if mapped:
                        column.set("this", exp.to_identifier(mapped))
                column.set("table", exp.to_identifier(reverse[alias_to_placeholder[alias]]))
        text = copy.sql(dialect=dialect)
        for sentinel in sorted(cls.SENTINELS, key=len, reverse=True):
            text = re.sub(rf'"?\b{sentinel}\b"?\.', cls.SENTINELS[sentinel] + ".", text)
        return text


def _entity_alias_scope(facts: dict[str, Any], alias: str) -> tuple[str | None, str | None]:
    """For an entity alias (table alias or CTE), return (physical table, alias inside its scope)."""
    for table in facts["tables"]:
        if table["alias"] == alias and table["scope"] == "main":
            return table["table"], alias
    for cte in facts["ctes"]:
        if cte["name"].lower() == alias.lower():
            inner = [t for t in facts["tables"] if t["scope"] == f"cte:{cte['name']}"]
            if len(inner) == 1:
                return inner[0]["table"], inner[0]["alias"]
    return None, None


def _main_alias_for_cte(facts: dict[str, Any], sql: str, cte_name: str, dialect: str) -> str | None:
    tree = sqlglot.parse(sql, read=dialect)[-1]
    main = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    for table in main.find_all(exp.Table):
        if table.name.lower() == cte_name.lower() and not table.db:
            return table.alias_or_name
    return None


def draft(layer: Layer, session_id: str, overwrite: bool = False) -> list[Path]:
    """Merge parse facts + interview answers into draft definitions (status: draft)."""
    folder = session_dir(layer, session_id)
    answers_path = folder / "answers.yaml"
    if not answers_path.exists():
        raise LayerError(f"No answers.yaml in {folder} — run the interview first (semantic-harvest skill)")
    answers = read_yaml(answers_path)
    facts = read_json(folder / "parse.json")
    probes = read_json(folder / "probes.json") if (folder / "probes.json").exists() else {}
    session = answers["session"]
    platform = session["platform"]
    other = "cloud" if platform == "legacy" else "legacy"
    dialect, other_dialect = layer.dialect(platform), layer.dialect(other)
    catalog = catalog_mod.load(layer)
    model = load_model(layer)
    sql = (layer.root / facts["query"]).read_text(encoding="utf-8")
    tree = sqlglot.parse(sql, read=dialect)[-1]
    team, steward = session["team"], session["steward"]
    provenance = {
        "method": "harvest", "from": facts["query"], "lines": facts["lines"], "session": session_id,
        "confirmed_by": session.get("interviewee", steward), "confirmed_on": str(session["date"]),
    }
    open_questions = list(answers.get("open_questions", []) or [])
    if open_questions:
        provenance["open_questions"] = open_questions

    def base(item: dict[str, Any], kind: str) -> dict[str, Any]:
        record: dict[str, Any] = {"id": item["id"], "kind": kind, "name": item["name"]}
        for key in ("description", "synonyms", "grain", "not_to_be_confused_with", "caveats", "ai_context"):
            if item.get(key):
                record[key] = item[key]
        record["owner"] = {"team": item.get("team", team), "steward": item.get("steward", steward)}
        record["status"] = "draft"
        record["version"] = 1
        return record

    definitions: list[dict[str, Any]] = []
    entity_alias_main: dict[str, str] = {}  # entity id → its alias in the main query
    entity_tables: dict[str, tuple[str, str]] = {}  # entity id → (table, alias in defining scope)

    for item in answers.get("entities", []) or []:
        table, scope_alias = _entity_alias_scope(facts, item["from_alias"])
        if table is None:
            raise LayerError(f"Entity {item['id']}: alias '{item['from_alias']}' not found in the parse")
        main_alias = _main_alias_for_cte(facts, sql, item["from_alias"], dialect) or item["from_alias"]
        entity_alias_main[item["id"]] = main_alias
        entity_tables[item["id"]] = (table, scope_alias)
        time_col = item.get("time_column")
        conditions = [f for f in facts["filters"] if f.get("table") and catalog_mod.norm(f["table"]) ==
                      catalog_mod.norm(table) and not (time_col and is_time_equality(f["canonical"], time_col.lower()))]
        nodes = [sqlglot.parse_one(f"SELECT 1 WHERE {c['predicate']}", read=dialect).args["where"].this
                 for c in conditions]
        record = base(item, "entity")
        binding: dict[str, Any] = {"platform": platform, "source": table, "key": item["key"]}
        if time_col:
            binding["time_column"] = time_col
        if nodes:
            binding["filter"] = " AND ".join(
                _Placeholders.render(n, {c["alias"]: "{t}" for c in conditions if c["alias"]}, dialect) for n in nodes
            )
        binding["preferred"] = platform == "cloud"
        binding["status"] = "active" if platform == "cloud" else "legacy"
        record["bindings"] = [binding]
        counterpart = _counterpart_binding(layer, catalog, platform, other, other_dialect, table, binding,
                                           nodes, conditions, item)
        if counterpart:
            record["bindings"].append(counterpart)
        record["checks"] = _suggest_checks(item, probes, table, counterpart is not None)
        if item.get("anchors"):
            record["anchors"] = item["anchors"]
        record["provenance"] = provenance
        definitions.append(record)

    for item in answers.get("dimensions", []) or []:
        join = next((j for j in facts["joins"] if j["right_alias"] == item["from_join"] and j["scope"] == "main"), None)
        if join is None:
            raise LayerError(f"Dimension {item['id']}: join alias '{item['from_join']}' not found in the parse")
        table = join["right_table"]
        right_key = join["keys"][0]["right"] if join["keys"] else item.get("key")
        column_map = catalog.column_map(platform, table)
        other_table = catalog.other_side(platform, table)
        if item.get("reuse"):
            # An existing certified dimension: only the relationship is new.
            existing = model.find(item["id"])
            if existing is None:
                raise LayerError(f"Dimension {item['id']} is marked reuse but does not exist yet")
            item = {**item, "name": existing.data.get("name", item["id"])}
        else:
            definitions.append(_dimension_record(item, base(item, "dimension"), facts, join, platform, other, dialect,
                                                 other_dialect, table, other_table, right_key, column_map,
                                                 provenance))

        # The relationship comes straight from the join the query already uses.
        entity_id = item.get("relates_to") or next(iter(entity_alias_main), None)
        if entity_id is None:
            continue
        entity_alias = entity_alias_main[entity_id]
        on_node = sqlglot.parse_one(f"SELECT 1 WHERE {join['condition']}", read=dialect).args["where"].this
        entity_table, _ = entity_tables[entity_id]
        joins = {platform: _Placeholders.render(on_node, {entity_alias: "{from}", item["from_join"]: "{to}"}, dialect)}
        if other_table and catalog.other_side(platform, entity_table):
            joins[other] = _Placeholders.render(
                on_node, {entity_alias: "{from}", item["from_join"]: "{to}"}, other_dialect,
                {entity_alias: catalog.column_map(platform, entity_table), item["from_join"]: column_map},
            )
        relationship = {
            "id": f"rel.{entity_id.split('.', 1)[1]}__{item['id'].split('.', 1)[1]}",
            "kind": "relationship",
            "name": f"{item['name']} for {entity_id}",
            "owner": {"team": team, "steward": steward},
            "status": "draft",
            "version": 1,
            "from": entity_id,
            "to": item["id"],
            "join": joins,
            "join_type": join["type"] if join["type"] in ("inner", "left") else "inner",
            "cardinality": join.get("inferred_cardinality") or item.get("cardinality", "many_to_one"),
            "provenance": provenance,
        }
        if join.get("inferred_cardinality"):
            relationship["cardinality_evidence"] = "harvest probe: key uniqueness on both sides"
        definitions.append(relationship)

    for item in answers.get("metrics", []) or []:
        record = base(item, "metric")
        record["expression"] = item["expression"]
        record["applies_to"] = item["applies_to"]
        record["additivity"] = item.get("additivity", "additive")
        record["provenance"] = provenance
        definitions.append(record)

    for item in answers.get("anchors", []) or []:
        record = base(item, "anchor")
        record["published_in"] = item["published_in"]
        record["measures"] = item["measures"]
        record["values"] = item["values"]
        record["tolerance_pct"] = item.get("tolerance_pct", 0.5)
        record["provenance"] = provenance
        definitions.append(record)

    return _write_drafts(layer, model, catalog, answers, tree, facts, platform, other, dialect, other_dialect,
                         definitions, provenance, team, steward, overwrite)


def _dimension_record(item: dict[str, Any], record: dict[str, Any], facts: dict[str, Any], join: dict[str, Any],
                      platform: str, other: str, dialect: str, other_dialect: str, table: str,
                      other_table: str | None, right_key: str, column_map: dict[str, str],
                      provenance: dict[str, Any]) -> dict[str, Any]:
    binding: dict[str, Any] = {"platform": platform, "source": table, "key": right_key}
    other_binding: dict[str, Any] | None = None
    if other_table:
        other_binding = {"platform": other, "source": other_table,
                         "key": column_map.get(right_key.upper(), right_key.lower()), "status": "proposed",
                         "review_note": "auto-proposed from the migration map; confirm in review"}
    if item.get("expression_from_output"):
        output = next(o for o in facts["outputs"] if o["name"].upper() == item["expression_from_output"].upper())
        node = sqlglot.parse_one(f"SELECT {output['expression']}", read=dialect).expressions[0]
        binding["expression"] = _Placeholders.render(node, {item["from_join"]: "{t}"}, dialect)
        if other_binding:
            other_binding["expression"] = _Placeholders.render(node, {item["from_join"]: "{t}"}, other_dialect,
                                                               {item["from_join"]: column_map})
    if item.get("levels"):
        levels = []
        for level_name, column in item["levels"].items():
            expressions = {platform: f"{{t}}.{column}"}
            if other_binding:
                expressions[other] = f"{{t}}.{column_map.get(column.upper(), column.lower())}"
            levels.append({"name": level_name, "expressions": expressions})
        record["levels"] = levels
    binding["preferred"] = platform == "cloud"
    record["bindings"] = [binding] + ([other_binding] if other_binding else [])
    record["provenance"] = provenance
    return record


def _write_drafts(layer: Layer, model: Model, catalog: catalog_mod.Catalog, answers: dict[str, Any],
                  tree: exp.Expression, facts: dict[str, Any], platform: str, other: str, dialect: str,
                  other_dialect: str, definitions: list[dict[str, Any]], provenance: dict[str, Any], team: str,
                  steward: str, overwrite: bool) -> list[Path]:
    written: list[Path] = []
    template = answers.get("template")
    if template:
        versions = {d.id: d.version for d in model.all()} | {d["id"]: d["version"] for d in definitions}
        record, sql_files = _draft_template(catalog, template, tree, platform, other, dialect, other_dialect,
                                            versions, provenance, team, steward)
        definitions.append(record)
        for rel_name, text in sql_files.items():
            path = layer.domains / team / KIND_DIRS["template"] / rel_name
            if path.exists() and not overwrite:
                raise LayerError(f"{path} exists — pass --overwrite to replace it")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(path)

    for record in definitions:
        domain = record["owner"]["team"]
        path = layer.domains / domain / KIND_DIRS[record["kind"]] / f"{record['id']}.yaml"
        if path.exists() and not overwrite:
            raise LayerError(f"{path} exists — pass --overwrite to replace it (drafts never clobber reviewed work)")
        write_yaml(path, record)
        written.append(path)

    golden = answers.get("golden_questions", []) or []
    if golden:
        written.append(_merge_golden(layer, golden))
    return written


def _counterpart_binding(layer: Layer, catalog: catalog_mod.Catalog, platform: str, other: str, other_dialect: str,
                         table: str, binding: dict[str, Any], nodes: list[exp.Expression],
                         conditions: list[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any] | None:
    """Propose the other platform's binding by migration map + transpile, then dry-run it."""
    other_table = catalog.other_side(platform, table)
    if not other_table:
        return None
    column_map = catalog.column_map(platform, table)
    proposal: dict[str, Any] = {"platform": other, "source": other_table,
                                "key": column_map.get(binding["key"].upper(), binding["key"].lower())}
    if binding.get("time_column"):
        proposal["time_column"] = column_map.get(binding["time_column"].upper(), binding["time_column"].lower())
    if nodes:
        alias_map = {c["alias"]: "{t}" for c in conditions if c["alias"]}
        proposal["filter"] = " AND ".join(
            _Placeholders.render(n, alias_map, other_dialect, {a: column_map for a in alias_map}) for n in nodes
        )
    proposal["preferred"] = other == "cloud"
    proposal["status"] = "proposed"
    note = "auto-transpiled from the harvested query and mapped through the migration map"
    try:
        adapter = get_platform(layer, other)
        check_sql = f"SELECT COUNT(*) AS n FROM {other_table} t"
        if proposal.get("filter"):
            check_sql += " WHERE " + proposal["filter"].replace("{t}", "t")
        result = adapter.dry_run(check_sql)
        if not result.ok:
            note += f"; DRY RUN FAILED: {result.detail}"
        elif isinstance(adapter, MockDuckDBPlatform):
            note += ("; dry run OK on the mock, but DuckDB accepts functions and types the real platform may "
                     "reject — confirm on the real platform and check the semantics")
        else:
            note += "; dry run OK — confirm the semantics"
    except Exception as exc:  # noqa: BLE001 — an unverifiable proposal is still useful, flagged as such
        note += f"; dry run not possible: {exc}"
    proposal["review_note"] = note
    return proposal


def _suggest_checks(item: dict[str, Any], probes: dict[str, Any], table: str, has_counterpart: bool) -> list[dict]:
    key = item["key"]
    checks: list[dict[str, Any]] = [{"type": "nullValues", "column": key, "must_be": 0}]
    if item.get("time_column"):
        checks.append({"type": "duplicateValues", "columns": [key, item["time_column"]], "must_be": 0})
        checks.append({"type": "freshness", "column": item["time_column"], "max_lag_days": 35})
    else:
        checks.append({"type": "duplicateValues", "columns": [key], "must_be": 0})
    if has_counterpart:
        checks.append({"type": "reconcile", "platforms": ["legacy", "cloud"], "measure": "COUNT(DISTINCT {entity.key})",
                       "tolerance_pct": 0.1})
    return checks


def _draft_template(catalog: catalog_mod.Catalog, template: dict[str, Any], tree: exp.Expression, platform: str,
                    other: str, dialect: str, other_dialect: str, versions: dict[str, int],
                    provenance: dict[str, Any], team: str, steward: str) -> tuple[dict[str, Any], dict[str, str]]:
    """The harvested query itself becomes a parameterised template (both dialects)."""
    period_literal = template["parameters"]["period"]["replaces"]
    original = tree.sql(dialect=dialect, pretty=True)
    files = {f"{template['id']}.{platform}.sql": original.replace(period_literal, "{{period_end}}") + "\n"}
    translated = _translate_query(tree, catalog, platform, other_dialect)
    files[f"{template['id']}.{other}.sql"] = translated.replace(period_literal, "{{period_end}}") + "\n"
    record = {
        "id": template["id"], "kind": "template", "name": template["name"],
        "description": template.get("description", ""),
        "owner": {"team": team, "steward": steward}, "status": "draft", "version": 1,
        "question_pattern": template["question_pattern"],
        "entity": template["entity"], "metric": template["metric"], "dimensions": template["dimensions"],
        "parameters": {"period_end": {"type": "period", "description": "Snapshot month (YYYY-MM)"}},
        "uses": [f"{ref}@{versions.get(ref, 1)}" for ref in template["uses"]],
        "sql": {platform: f"{template['id']}.{platform}.sql", other: f"{template['id']}.{other}.sql"},
        "expected": {"executes": True, **({"reconciles_with": template["reconciles_with"]}
                                          if template.get("reconciles_with") else {})},
        "verified_by": provenance["confirmed_by"], "verified_on": provenance["confirmed_on"],
        "provenance": provenance,
    }
    return record, files


def _translate_query(tree: exp.Expression, catalog: catalog_mod.Catalog, platform: str, other_dialect: str) -> str:
    """Rewrite table and column names through the migration map, then render in the other dialect."""
    copy = tree.copy()
    alias_tables: dict[str, str] = {}
    for table in copy.find_all(exp.Table):
        if not table.db:
            continue
        source = f"{table.db}.{table.name}"
        alias_tables[table.alias_or_name] = source
        target = catalog.other_side(platform, source)
        if target:
            schema, name = target.split(".")
            table.set("db", exp.to_identifier(schema))
            table.set("this", exp.to_identifier(name))
    for column in copy.find_all(exp.Column):
        source = alias_tables.get(column.table)
        mapped = catalog.map_column(platform, source, column.name) if source else None
        if mapped:
            column.set("this", exp.to_identifier(mapped))
        elif platform == "legacy":
            column.set("this", exp.to_identifier(column.name.lower()))
    for alias in copy.find_all(exp.TableAlias):
        alias.set("this", exp.to_identifier(alias.name.lower()) if platform == "legacy" else alias.this)
    return copy.sql(dialect=other_dialect, pretty=True)


def _merge_golden(layer: Layer, golden: list[dict[str, Any]]) -> Path:
    path = layer.evals / "golden_questions.yaml"
    existing = (read_yaml(path) or {}).get("questions", []) if path.exists() else []
    known = {q["question"] for q in existing}
    merged = existing + [q for q in golden if q["question"] not in known]
    write_yaml(path, {"questions": merged})
    return path
