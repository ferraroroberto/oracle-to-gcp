"""[Phase 2, experimental] ``sl export osi`` — one-way export to Open Semantic Interchange.

Turn this on when another tool needs to consume the definitions. OSI (now
Apache Ossie) is at spec 0.2.0.dev0 and still mutable: this converter tracks its
documented shapes (``datasets`` / ``fields`` with per-dialect ``expression``,
``relationships`` with ``from_columns``/``to_columns``, ``metrics``,
``ai_context``, ``custom_extensions``) and is labelled experimental. The YAML
under ``domains/`` stays the source of truth; this is a generated view.

Mapping decisions:
- each bound table → an OSI dataset; dimensions and keys → its fields
- OSI has no "filtered population", so every (metric × entity) pair becomes a
  metric whose expression carries the entity's filter
  (``COUNT(DISTINCT CASE WHEN <filter> THEN key END)``)
- governance (owner, steward, status, version) → ``custom_extensions``
- the legacy platform exports as ``ANSI_SQL``: the spec's dialect list has no Oracle entry
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semantic_layer.sl.common import Layer, write_yaml
from semantic_layer.sl.model import Definition, load_model
from semantic_layer.sl.sqlutil import substitute

SPEC_VERSION = "0.2.0.dev0"
VENDOR = "TEAM_SEMANTIC_LAYER"
DIALECT = {"cloud": "BIGQUERY", "legacy": "ANSI_SQL"}


def _dataset_name(table: str) -> str:
    return table.split(".")[-1].lower()


def _governance(definition: Definition) -> list[dict[str, str]]:
    owner = definition.owner
    return [{"vendor_name": VENDOR, "data": json.dumps({
        "id": definition.id, "status": definition.status, "version": definition.version,
        "team": owner.get("team"), "steward": owner.get("steward"),
        "review_by": str(definition.data.get("review_by") or ""),
    }, sort_keys=True)}]


def _ai_context(definition: Definition) -> dict[str, Any] | None:
    context: dict[str, Any] = {}
    if definition.synonyms:
        context["synonyms"] = definition.synonyms
    hints = definition.data.get("ai_context") or {}
    if hints.get("instructions"):
        context["instructions"] = " ".join(str(hints["instructions"]).split())
    if hints.get("examples"):
        context["examples"] = hints["examples"]
    return context or None


def _expr(platform: str, text: str) -> dict[str, Any]:
    return {"dialects": [{"dialect": DIALECT[platform], "expression": text}]}


def export(layer: Layer, platform: str = "cloud") -> dict[str, Any]:
    model = load_model(layer)
    usable = [d for d in model.all() if d.status != "deprecated"]
    datasets: dict[str, dict[str, Any]] = {}

    def dataset(table: str) -> dict[str, Any]:
        name = _dataset_name(table)
        return datasets.setdefault(name, {"name": name, "source": table, "fields": []})

    for definition in usable:
        binding = definition.binding(platform)
        if not binding or definition.kind not in ("entity", "dimension"):
            continue
        target = dataset(binding["source"])
        alias = _dataset_name(binding["source"])
        if definition.kind == "entity":
            if not any(f["name"] == binding["key"] for f in target["fields"]):
                target["fields"].append({"name": binding["key"], "expression": _expr(platform, binding["key"]),
                                         "description": f"Key of {definition.id}"})
            continue
        levels = definition.data.get("levels") or []
        expressions = [(lvl["name"], lvl["expressions"].get(platform)) for lvl in levels] or \
            [(definition.id.split(".", 1)[1], binding.get("expression") or binding.get("label_column") or binding["key"])]
        for suffix, expression in expressions:
            if not expression:
                continue
            name = suffix if levels else definition.id.split(".", 1)[1]
            field = {"name": f"{definition.id.split('.', 1)[1]}__{name}" if levels else name,
                     "expression": _expr(platform, substitute(expression, t=alias)),
                     "dimension": {"is_time": "month" in definition.id},
                     "label": definition.data.get("name"),
                     "description": " ".join(str(definition.data.get("description", "")).split()),
                     "custom_extensions": _governance(definition)}
            if _ai_context(definition):
                field["ai_context"] = _ai_context(definition)
            target["fields"].append(field)

    relationships = []
    for rel in (d for d in usable if d.kind == "relationship"):
        source, target = model.find(rel.data["from"]), model.find(rel.data["to"])
        condition = (rel.data.get("join") or {}).get(platform)
        if not (source and target and condition and source.binding(platform) and target.binding(platform)):
            continue
        from_cols, to_cols = [], []
        for term in condition.split(" AND "):
            left, _, right = term.partition("=")
            sides = {s.strip().split(".")[0]: s.strip().split(".")[1] for s in (left, right) if "." in s}
            from_cols.append(sides.get("{from}"))
            to_cols.append(sides.get("{to}"))
        relationships.append({"name": rel.id.split(".", 1)[1],
                              "from": _dataset_name(source.binding(platform)["source"]),
                              "to": _dataset_name(target.binding(platform)["source"]),
                              "from_columns": from_cols, "to_columns": to_cols,
                              "custom_extensions": _governance(rel)})

    metrics = []
    for metric in (d for d in usable if d.kind == "metric"):
        for entity_id in metric.data.get("applies_to", []):
            entity = model.find(entity_id)
            binding = entity.binding(platform) if entity else None
            if not binding:
                continue
            alias = _dataset_name(binding["source"])
            key = f"{alias}.{binding['key']}"
            if binding.get("filter"):
                inner = f"CASE WHEN {substitute(binding['filter'], t=alias)} THEN {key} END"
            else:
                inner = key
            expression = substitute(metric.data["expression"], t=alias, entity_key="__KEY__").replace("__KEY__", inner)
            entry = {"name": f"{metric.id.split('.', 1)[1]}__{entity.id.split('.', 1)[1]}",
                     "expression": _expr(platform, expression),
                     "description": f"{metric.data.get('name')} — {entity.data.get('name')}",
                     "custom_extensions": _governance(metric) + _governance(entity)}
            if binding.get("time_column"):
                entry["ai_context"] = {"instructions": f"Snapshot population: filter {alias}.{binding['time_column']} "
                                                       "to one period; never sum across periods."}
            metrics.append(entry)

    return {"version": SPEC_VERSION, "name": "team_semantic_layer",
            "description": f"Exported from the team semantic layer ({platform} platform) by `sl export osi` — "
                           "experimental; the YAML definitions remain the source of truth.",
            "datasets": sorted(datasets.values(), key=lambda d: d["name"]),
            "relationships": relationships, "metrics": metrics}


def write(layer: Layer, platform: str = "cloud") -> Path:
    path = layer.build / "export" / f"osi.{platform}.yaml"
    write_yaml(path, export(layer, platform))
    return path
