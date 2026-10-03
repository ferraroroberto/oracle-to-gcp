"""``sl ask`` — from business terms to a validated, cited answer.

The ``semantic-ask`` skill does the language work (question → terms → spec,
asking the user when :func:`resolve_text` reports ambiguity). Everything after
that is deterministic and lives here:

  resolve → template-first → compose from certified fragments → lint →
  dry run → execute (aggregates) → reconcile with anchors → provenance card

A spec looks like::

    {"entity": "customer.active_buyer", "metric": "metric.customer_count",
     "dimensions": ["dimension.home_store:region"],
     "filters": [{"dimension": "dimension.home_store:region", "op": "=", "value": "North"}],
     "period": "2026-08"}
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import sqlglot
from sqlglot import exp

from semantic_layer.sl.common import Layer, LayerError, month_end, read_json
from semantic_layer.sl.model import Definition, Model, load_model, strip_ref, template_sql
from semantic_layer.sl.platforms import get_platform
from semantic_layer.sl.sqlutil import canonical, split_conjuncts, substitute
from semantic_layer.sl.validate import normalise_term, stale_pins

RESOLVABLE = ("entity", "dimension", "metric", "filter")


# --------------------------------------------------------------------------- resolve


def _phrase_index(model: Model) -> dict[str, list[tuple[Definition, str | None]]]:
    """Normalised phrase → (definition, level) for every name, synonym and level name."""
    index: dict[str, list[tuple[Definition, str | None]]] = {}
    for definition in model.all():
        if definition.kind not in RESOLVABLE or definition.status == "deprecated":
            continue
        levels = {lvl["name"] for lvl in definition.data.get("levels", []) or []}
        for phrase in definition.synonyms + [definition.data.get("name", "")]:
            if not phrase:
                continue
            key = normalise_term(re.sub(r"\(.*?\)", "", phrase))
            level = key if key in levels else None
            entries = index.setdefault(key, [])
            if all(d.id != definition.id for d, _ in entries):
                entries.append((definition, level))
    return index


def _difference(candidate: Definition, others: list[Definition]) -> str:
    """One line that tells this candidate apart — from the others' confusion notes, else its description."""
    for other in others:
        for note in other.data.get("not_to_be_confused_with", []) or []:
            if note.get("id") == candidate.id:
                return note["difference"]
    description = str(candidate.data.get("description", candidate.data.get("name", ""))).strip()
    return description.split(". ")[0].rstrip(".")


def _match_phrases(text: str, index: dict[str, list[tuple[Definition, str | None]]]) -> list[dict[str, Any]]:
    words = normalise_term(text.replace(",", " ")).split()
    longest = max((len(k.split()) for k in index), default=1)
    found: list[dict[str, Any]] = []
    i = 0
    while i < len(words):
        for size in range(min(longest, len(words) - i), 0, -1):
            phrase = " ".join(words[i:i + size])
            if phrase in index:
                found.append({"term": phrase, "entries": index[phrase]})
                i += size
                break
        else:
            i += 1
    return found


def resolve_text(layer: Layer, text: str | list[str], model: Model | None = None) -> dict[str, Any]:
    """Match business terms to definitions; flag every ambiguity the user must settle."""
    model = model or load_model(layer)
    index = _phrase_index(model)
    texts = [text] if isinstance(text, str) else list(text)
    matches = [m for t in texts for m in _match_phrases(t, index)]
    result: dict[str, Any] = {"terms": [], "entity": None, "metric": None, "dimensions": [],
                              "clarifications": [], "uncertified": []}
    for match in matches:
        definitions = [d for d, _ in match["entries"]]
        certified = [d for d in definitions if d.certified]
        pool = certified or definitions
        options = [{"id": d.id, "kind": d.kind, "name": d.data.get("name"), "status": d.status,
                    "owner": d.owner.get("team"), "steward": d.owner.get("steward"),
                    "difference": _difference(d, [o for o in definitions if o is not d])} for d in definitions]
        entry: dict[str, Any] = {"term": match["term"], "candidates": options}
        if len(pool) == 1:
            chosen = pool[0]
            level = next(lvl for d, lvl in match["entries"] if d is chosen)
            entry.update(status="resolved" if chosen.certified else "resolved_uncertified", resolved=chosen.id,
                         level=level)
            if not chosen.certified:
                result["uncertified"].append(chosen.id)
            _assign(result, chosen, level)
        else:
            entry["status"] = "ambiguous"
            result["clarifications"].append({
                "term": match["term"],
                "question": f"Which '{match['term']}' do you mean?",
                "options": [o for o in options if o["status"] == "certified"] or options,
            })
        result["terms"].append(entry)

    if result["entity"] is None and not any(c for c in result["clarifications"]
                                            if any(o["kind"] == "entity" for o in c["options"])):
        metric = model.find(result["metric"]) if result["metric"] else None
        candidates = [model.find(e) for e in (metric.data.get("applies_to", []) if metric else [])] or \
            [d for d in model.of_kind("entity") if d.certified]
        candidates = [c for c in candidates if c is not None]
        result["clarifications"].append({
            "term": "(population)",
            "question": "Which population should be counted?",
            "options": [{"id": c.id, "kind": "entity", "name": c.data.get("name"), "status": c.status,
                         "owner": c.owner.get("team"), "steward": c.owner.get("steward"),
                         "difference": _difference(c, [o for o in candidates if o is not c])} for c in candidates],
        })
    if result["entity"] and result["metric"] is None:
        metrics = [m for m in model.of_kind("metric") if m.certified and result["entity"] in m.data.get("applies_to", [])]
        if len(metrics) == 1:
            result["metric"] = metrics[0].id
            result["metric_defaulted"] = True
    result["needs_clarification"] = bool(result["clarifications"])
    return result


def _assign(result: dict[str, Any], definition: Definition, level: str | None) -> None:
    if definition.kind == "entity" and result["entity"] is None:
        result["entity"] = definition.id
    elif definition.kind == "metric" and result["metric"] is None:
        result["metric"] = definition.id
    elif definition.kind == "dimension":
        ref = f"{definition.id}:{level}" if level else definition.id
        if ref not in result["dimensions"]:
            result["dimensions"].append(ref)


def resolve_markdown(result: dict[str, Any]) -> str:
    lines = ["## Term resolution"]
    for term in result["terms"]:
        if term["status"] == "ambiguous":
            lines.append(f"- **{term['term']}** — ambiguous ({len(term['candidates'])} definitions)")
        else:
            flag = " ⚠️ uncertified" if term["status"] == "resolved_uncertified" else ""
            level = f" (level: {term['level']})" if term.get("level") else ""
            lines.append(f"- **{term['term']}** → `{term['resolved']}`{level}{flag}")
    if result["clarifications"]:
        lines.append("\n## Ask the user before building anything")
        for c in result["clarifications"]:
            lines.append(f"\n**{c['question']}**")
            for o in c["options"]:
                lines.append(f"- `{o['id']}` — {o['difference']} (owner: {o['owner']}, steward: {o['steward']}, "
                             f"{o['status']})")
    else:
        lines.append(f"\n✅ No ambiguity. Entity `{result['entity']}`, metric `{result['metric']}`, "
                     f"dimensions {result['dimensions']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- spec helpers


def _period_literal(period: str) -> str:
    return month_end(period).isoformat()


def _sql_literal(value: Any) -> str:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _dim_expression(dimension: Definition, level: str | None, platform: str, alias: str) -> str:
    binding = dimension.binding(platform) or {}
    if level:
        for lvl in dimension.data.get("levels", []) or []:
            if lvl["name"] == level:
                return substitute(lvl["expressions"][platform], t=alias)
        raise LayerError(f"{dimension.id} has no level '{level}'")
    levels = dimension.data.get("levels", []) or []
    if binding.get("expression"):
        return substitute(binding["expression"], t=alias)
    if levels:
        return substitute(levels[0]["expressions"][platform], t=alias)
    column = binding.get("label_column") or binding["key"]
    return f"{alias}.{column}"


def _output_name(ref: str) -> str:
    bare, _, level = ref.partition(":")
    return level or bare.split(".", 1)[1]


def choose_platform(model: Model, spec: dict[str, Any], requested: str | None) -> str:
    entity = model.get(spec["entity"])
    needed = [entity] + [model.get(strip_ref(d)) for d in spec.get("dimensions", [])] + \
        [model.get(strip_ref(f["dimension"])) for f in spec.get("filters", []) or []]
    order = [requested] if requested else [entity.preferred_platform() or "cloud", "cloud", "legacy"]
    for platform in order:
        if platform and all(d.binding(platform) for d in needed):
            return platform
    raise LayerError("No platform binds every definition in this spec")


# --------------------------------------------------------------------------- template


def match_template(model: Model, spec: dict[str, Any], today: dt.date | None = None) -> tuple[Definition | None, list[str]]:
    """A certified, current template answering exactly this spec — or why none qualifies."""
    today = today or dt.date.today()
    stale = {s["template"] for s in stale_pins(model)}
    notes: list[str] = []
    for template in model.of_kind("template"):
        data = template.data
        if data.get("entity") != spec.get("entity") or data.get("metric") != spec.get("metric"):
            continue
        if sorted(data.get("dimensions", [])) != sorted(spec.get("dimensions", [])) or spec.get("filters"):
            continue
        if not template.certified:
            notes.append(f"{template.id} matches but is {template.status}, not certified — composing new SQL instead")
            continue
        if template.id in stale:
            notes.append(f"{template.id} matches but needs re-verification (stale version pins)")
            continue
        expires = data.get("expires")
        if expires and dt.date.fromisoformat(str(expires)) < today:
            notes.append(f"{template.id} matches but expired on {expires}")
            continue
        return template, notes
    return None, notes


# --------------------------------------------------------------------------- compose


def compose(model: Model, spec: dict[str, Any], platform: str) -> tuple[str, list[str], list[Definition]]:
    """Build SQL only from certified fragments and declared relationships."""
    entity = model.get(spec["entity"])
    metric = model.get(spec["metric"])
    if entity.kind != "entity" or metric.kind != "metric":
        raise LayerError("spec.entity must be an entity and spec.metric a metric")
    if entity.id not in metric.data.get("applies_to", []):
        raise LayerError(f"{metric.id} does not apply to {entity.id}")
    binding = entity.binding(platform)
    used: list[Definition] = [entity, metric]
    notes: list[str] = []
    joins: list[str] = []
    join_aliases: dict[tuple[str, str], str] = {}

    def alias_for(dim_ref: str) -> tuple[Definition, str]:
        dimension = model.get(strip_ref(dim_ref))
        relationship = next((r for r in model.relationships_from(entity.id) if r.data.get("to") == dimension.id), None)
        if relationship is None:
            raise LayerError(f"no declared relationship from {entity.id} to {dimension.id} — "
                             "generated SQL may only use declared joins")
        dim_binding = dimension.binding(platform)
        condition_template = (relationship.data.get("join") or {}).get(platform)
        if not condition_template:
            raise LayerError(f"{relationship.id} has no {platform} join")
        key = (dim_binding["source"], condition_template)
        if key not in join_aliases:
            alias = f"d{len(join_aliases) + 1}"
            join_aliases[key] = alias
            join_kind = "LEFT JOIN" if relationship.data.get("join_type") == "left" else "JOIN"
            condition = substitute(condition_template, **{"from": "e", "to": alias})
            joins.append(f"{join_kind} {dim_binding['source']} AS {alias} ON {condition}"
                         f"  -- {relationship.id}@{relationship.version}")
            if relationship not in used:
                used.append(relationship)
        if dimension not in used:
            used.append(dimension)
        return dimension, join_aliases[key]

    selects: list[tuple[str, str]] = []  # (expression AS name, provenance comment)
    group_by: list[str] = []
    for ref in spec.get("dimensions", []) or []:
        dimension, alias = alias_for(ref)
        level = ref.partition(":")[2] or None
        expression = _dim_expression(dimension, level, platform, alias)
        selects.append((f"{expression} AS {_output_name(ref)}", f"{dimension.id}@{dimension.version}"))
        group_by.append(expression)
    metric_sql = substitute(metric.data["expression"], t="e", entity_key=f"e.{binding['key']}")
    metric_name = metric.id.split(".", 1)[1]
    selects.append((f"{metric_sql} AS {metric_name}",
                    f"{metric.id}@{metric.version} on {entity.id}@{entity.version}"))

    where: list[str] = []
    if binding.get("filter"):
        where.append(f"({substitute(binding['filter'], t='e')})  -- {entity.id}@{entity.version}")
    period = spec.get("period")
    if period and binding.get("time_column"):
        where.append(f"e.{binding['time_column']} = DATE '{_period_literal(period)}'  -- period {period}")
    elif period:
        notes.append(f"{entity.id} has no time column (current state): period {period} ignored")
    elif binding.get("time_column"):
        raise LayerError(f"{entity.id} is a snapshot (time column {binding['time_column']}): give a period "
                         "(YYYY-MM), otherwise every snapshot would be counted")
    for flt in spec.get("filters", []) or []:
        dimension, alias = alias_for(flt["dimension"])
        expression = _dim_expression(dimension, flt["dimension"].partition(":")[2] or None, platform, alias)
        op = flt.get("op", "=").upper()
        if op == "IN":
            values = ", ".join(_sql_literal(v) for v in flt["value"])
            where.append(f"{expression} IN ({values})  -- filter on {dimension.id}")
        elif op in ("=", "!=", "<>"):
            where.append(f"{expression} {op} {_sql_literal(flt['value'])}  -- filter on {dimension.id}")
        else:
            raise LayerError(f"Unsupported filter operator '{op}' (use =, != or IN)")

    # The comma must precede the comment, or the comment swallows it.
    select_lines = [f"  {sql}{',' if i < len(selects) - 1 else ''}  -- {comment}"
                    for i, (sql, comment) in enumerate(selects)]
    lines = ["-- Generated by sl ask from certified definitions — NEW SQL, not a certified template",
             "SELECT", *select_lines,
             f"FROM {binding['source']} AS e  -- {entity.id}@{entity.version}"]
    lines += joins
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
        lines.append("ORDER BY " + ", ".join(str(i) for i in range(1, len(group_by) + 1)))
    return "\n".join(lines), notes, used


# --------------------------------------------------------------------------- lint


def lint(sql: str, model: Model, spec: dict[str, Any], platform: str, dialect: str) -> dict[str, Any]:
    """Deterministic checks that the SQL really uses the certified fragments."""
    findings: list[dict[str, str]] = []
    try:
        tree = sqlglot.parse(sql, read=dialect)[-1]
    except sqlglot.errors.ParseError as exc:
        return {"ok": False, "findings": [{"rule": "parse", "message": str(exc)}]}
    if any(isinstance(p, exp.Star) for s in tree.find_all(exp.Select) for p in s.expressions):
        findings.append({"rule": "no_select_star", "message": "SELECT * is not allowed"})
    where_terms = {canonical(t) for w in tree.find_all(exp.Where) for t in split_conjuncts(w.this)}
    entity = model.get(spec["entity"])
    binding = entity.binding(platform) or {}
    if binding.get("filter"):
        expected = [canonical(t) for t in split_conjuncts(
            sqlglot.parse_one(f"SELECT 1 FROM x AS e WHERE {substitute(binding['filter'], t='e')}", read=dialect)
            .args["where"].this)]
        for term in expected:
            if term not in where_terms:
                findings.append({"rule": "certified_filter",
                                 "message": f"certified filter of {entity.id} missing: {term}"})
    time_column = (binding.get("time_column") or "").lower()
    if time_column and not any(re.match(rf"^{re.escape(time_column)}\s*=", t) for t in where_terms):
        findings.append({"rule": "snapshot_filter",
                         "message": f"no {time_column} = <period> filter — would count every snapshot"})
    declared = []
    for relationship in model.of_kind("relationship"):
        condition = (relationship.data.get("join") or {}).get(platform)
        if condition:
            node = sqlglot.parse_one(f"SELECT 1 FROM a AS f JOIN b AS o ON {substitute(condition, **{'from': 'f', 'to': 'o'})}",
                                     read=dialect)
            declared.append(_column_pairs(node.find(exp.Join).args["on"]))
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None and _column_pairs(on) not in declared:
            findings.append({"rule": "declared_joins", "message": f"undeclared join: {on.sql(dialect=dialect)}"})
    return {"ok": not findings, "findings": findings}


def _column_pairs(on: exp.Expression) -> frozenset[frozenset[str]]:
    pairs = set()
    for term in split_conjuncts(on):
        if isinstance(term, exp.EQ):
            pairs.add(frozenset({c.name.lower() for c in term.find_all(exp.Column)}))
    return frozenset(pairs)


# --------------------------------------------------------------------------- run


def _metric_column(rows: list[dict[str, Any]], sql: str, dialect: str, preferred: str) -> str | None:
    if not rows:
        return None
    if preferred in rows[0]:
        return preferred
    try:
        tree = sqlglot.parse(sql, read=dialect)[-1]
        main = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
        for projection in main.expressions:
            if projection.find(exp.AggFunc) is not None:
                name = projection.alias_or_name
                for key in rows[0]:
                    if key.lower() == name.lower():
                        return key
    except sqlglot.errors.ParseError:
        pass
    return None


def reconcile(model: Model, spec: dict[str, Any], total: float | None, truncated: bool) -> dict[str, Any]:
    """Compare the answer's total with a published anchor, when that comparison is meaningful."""
    anchors = [a for a in model.of_kind("anchor")
               if (a.data.get("measures") or {}).get("entity") == spec["entity"]
               and (a.data.get("measures") or {}).get("metric") == spec["metric"]]
    if not anchors:
        return {"status": "no_anchor", "detail": "no published figure is anchored to this entity and metric"}
    anchor = anchors[0]
    if spec.get("filters"):
        return {"status": "not_comparable", "anchor": anchor.id, "detail": "the answer is a filtered subset"}
    period = spec.get("period")
    value = next((v["value"] for v in anchor.data.get("values", []) if str(v["period"]) == str(period)), None)
    if value is None:
        return {"status": "no_value", "anchor": anchor.id, "detail": f"anchor has no published value for {period}"}
    if total is None or truncated:
        return {"status": "unknown", "anchor": anchor.id,
                "detail": "total not established (not executed or result truncated)"}
    diff_pct = abs(total - value) / value * 100 if value else 0.0
    tolerance = float(anchor.data.get("tolerance_pct", 0.5))
    return {"status": "reconciles" if diff_pct <= tolerance else "differs", "anchor": anchor.id,
            "published_in": anchor.data.get("published_in"), "expected": value, "actual": total,
            "diff_pct": round(diff_pct, 3), "tolerance_pct": tolerance}


def run(layer: Layer, spec: dict[str, Any], platform: str | None = None, execute: bool = True,
        model: Model | None = None) -> dict[str, Any]:
    model = model or load_model(layer)
    answer: dict[str, Any] = {"ok": False, "spec": spec, "errors": [], "warnings": []}
    try:
        for key in ("entity", "metric"):
            if not spec.get(key):
                raise LayerError(f"spec.{key} is required (resolve the terms first)")
        chosen = choose_platform(model, spec, platform)
        dialect = layer.dialect(chosen)
        answer["platform"] = chosen
        template, notes = match_template(model, spec)
        answer["warnings"] += notes
        if template is not None:
            sql = template_sql(template, chosen)
            if sql is None:
                raise LayerError(f"{template.id} has no SQL for {chosen}")
            if spec.get("period"):
                sql = sql.replace("{{period_end}}", _period_literal(spec["period"]))
            answer["source"] = f"certified template {template.id}@{template.version}"
            used = [model.get(r) for r in template.data.get("uses", [])] + [template]
            used += [r for r in model.of_kind("relationship") if r.data.get("from") == spec["entity"]
                     and any(strip_ref(d) == r.data.get("to") for d in spec.get("dimensions", []))]
        else:
            sql, compose_notes, used = compose(model, spec, chosen)
            answer["warnings"] += compose_notes
            answer["source"] = "composed from certified definitions (new SQL — uncertified)"
        answer["sql"] = sql.strip()
        answer["lint"] = lint(sql, model, spec, chosen, dialect)
        adapter = get_platform(layer, chosen)
        dry = adapter.dry_run(sql)
        answer["dry_run"] = {"ok": dry.ok, "detail": dry.detail, "bytes_estimate": dry.bytes_estimate}
        allowed = bool(layer.config.get("execution", {}).get("allow_execute", False))
        total = None
        truncated = False
        if execute and allowed and dry.ok:
            cap = int(layer.config.get("execution", {}).get("max_result_rows", 500))
            rows = adapter.query(sql, max_rows=cap + 1)
            truncated = len(rows) > cap
            rows = rows[:cap]
            answer["rows"] = rows
            answer["truncated"] = truncated
            column = _metric_column(rows, sql, dialect, spec["metric"].split(".", 1)[1])
            if column:
                total = sum(float(r[column] or 0) for r in rows)
                answer["total"] = int(total) if float(total).is_integer() else total
        elif execute and not allowed:
            answer["warnings"].append("execution disabled in config.json — returning SQL only")
        anchors_used = []
        answer["reconciliation"] = reconcile(model, spec, total, truncated)
        if answer["reconciliation"].get("anchor"):
            anchors_used.append(model.get(answer["reconciliation"]["anchor"]))
        answer["provenance"] = provenance_card(layer, model, used + anchors_used)
        answer["warnings"] += _warnings(model, used, chosen)
        answer["validation_plan"] = validation_plan(model, spec, answer)
        answer["ok"] = answer["lint"]["ok"] and dry.ok
        if not answer["lint"]["ok"]:
            answer["errors"] += [f["message"] for f in answer["lint"]["findings"]]
        if not dry.ok:
            answer["errors"].append(f"dry run failed: {dry.detail}")
    except LayerError as exc:
        answer["errors"].append(str(exc))
    return answer


def _health(layer: Layer) -> dict[str, Any]:
    path = layer.build / "health.json"
    return read_json(path).get("definitions", {}) if path.exists() else {}


def provenance_card(layer: Layer, model: Model, used: list[Definition]) -> list[dict[str, Any]]:
    health = _health(layer)
    seen: set[str] = set()
    card = []
    for definition in used:
        if definition.id in seen:
            continue
        seen.add(definition.id)
        provenance = definition.data.get("provenance") or {}
        card.append({
            "id": definition.id, "kind": definition.kind, "version": definition.version,
            "status": definition.status, "owner": definition.owner.get("team"),
            "steward": definition.owner.get("steward"),
            "last_reviewed": str(definition.data.get("last_reviewed") or ""),
            "review_by": str(definition.data.get("review_by") or ""),
            "health": (health.get(definition.id) or {}).get("status", "unknown"),
            "source": f"{provenance.get('method', '?')}: {provenance.get('from', '')}".strip(": "),
            "docs": f"build/docs/definitions/{definition.id}.md",
        })
    return card


def _warnings(model: Model, used: list[Definition], platform: str) -> list[str]:
    warnings: list[str] = []
    asked: set[str] = set()
    today = dt.date.today()
    for definition in {d.id: d for d in used}.values():
        if not definition.certified:
            warnings.append(f"⚠️ {definition.id} is {definition.status}, not certified — treat the answer as provisional")
        review_by = definition.data.get("review_by")
        if review_by and dt.date.fromisoformat(str(review_by)) < today:
            warnings.append(f"⚠️ {definition.id} review is overdue (review_by {review_by})")
        binding = definition.binding(platform)
        if binding and binding.get("status") == "legacy":
            warnings.append(f"ℹ️ {definition.id} answered from the legacy platform (not yet on cloud)")
        for question in (definition.data.get("provenance") or {}).get("open_questions", []) or []:
            if question not in asked:  # one harvest session's questions sit on every draft it produced
                asked.add(question)
                warnings.append(f"❓ open question ({definition.owner.get('steward')}): {question}")
    return warnings


def validation_plan(model: Model, spec: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    plan = []
    entity = model.get(spec["entity"])
    recon = answer.get("reconciliation", {})
    if recon.get("status") == "reconciles":
        plan.append(f"Total matches {recon['published_in']} ({recon['expected']}) — glance at the trend vs the "
                    "previous period to catch a partial load.")
    elif recon.get("anchor"):
        plan.append(f"Compare against the published figure in {model.get(recon['anchor']).data.get('published_in')} "
                    f"({recon.get('detail', recon.get('status'))}).")
    dims = spec.get("dimensions", []) or []
    if dims:
        plan.append(f"Pick one value of {_output_name(dims[0])} and check it against the source report or a "
                    "colleague's number for the same period.")
    binding = entity.binding(answer.get("platform", "cloud")) or {}
    if binding.get("filter"):
        plan.append(f"Confirm the population is what you meant — {entity.data.get('name')}: "
                    f"{binding['filter'].replace('{t}.', '')}")
    if spec.get("period"):
        plan.append(f"Re-run for the previous month and check the change is plausible (period {spec['period']}).")
    return plan


def answer_markdown(answer: dict[str, Any]) -> str:
    lines = []
    if answer["errors"]:
        lines.append("## ❌ Not answered")
        lines += [f"- {e}" for e in answer["errors"]]
    if answer.get("sql"):
        lines.append(f"## Answer — {answer.get('source')} ({answer.get('platform')})")
        if answer.get("rows") is not None:
            rows = answer["rows"]
            if rows:
                headers = list(rows[0])
                lines.append("| " + " | ".join(headers) + " |")
                lines.append("|" + "---|" * len(headers))
                for row in rows[:20]:
                    lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")
                if len(rows) > 20:
                    lines.append(f"_…{len(rows) - 20} more rows_")
            if answer.get("total") is not None:
                lines.append(f"\n**Total:** {answer['total']}")
        recon = answer.get("reconciliation", {})
        if recon.get("status") == "reconciles":
            lines.append(f"✅ Reconciles with {recon['published_in']}: {recon['expected']} "
                         f"(diff {recon['diff_pct']}%, tolerance {recon['tolerance_pct']}%)")
        elif recon.get("status") == "differs":
            lines.append(f"❌ Differs from {recon['published_in']}: expected {recon['expected']}, got {recon['actual']} "
                         f"({recon['diff_pct']}%)")
        else:
            lines.append(f"ℹ️ Reconciliation: {recon.get('status')} — {recon.get('detail', '')}")
        lines.append("\n```sql\n" + answer["sql"] + "\n```")
        lint_result = answer.get("lint", {})
        lines.append("Lint: ✅ uses the certified fragments" if lint_result.get("ok") else
                     "Lint: ❌ " + "; ".join(f["message"] for f in lint_result.get("findings", [])))
        lines.append(f"Dry run: {'✅' if answer['dry_run']['ok'] else '❌'} {answer['dry_run']['detail']}")
        lines.append("\n## Provenance")
        lines.append("| definition | v | status | owner | steward | reviewed | health |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in answer.get("provenance", []):
            lines.append(f"| `{p['id']}` | {p['version']} | {p['status']} | {p['owner']} | {p['steward']} | "
                         f"{p['last_reviewed']} | {p['health']} |")
        lines.append("\n## How to validate")
        lines += [f"{i}. {step}" for i, step in enumerate(answer.get("validation_plan", []), 1)]
    if answer.get("warnings"):
        lines.append("\n## Caveats")
        lines += [f"- {w}" for w in answer["warnings"]]
    return "\n".join(lines)
