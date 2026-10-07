"""``sl validate`` — the deterministic CI gate on every change to definitions.

Errors fail the PR; warnings are reported but pass. No LLM, no warehouse: it
reads only the YAML and the committed catalog snapshot.

Rules
  1. every file matches its kind's JSON Schema
  2. ids are unique and every reference points at an existing definition
  3. every binding's table and referenced columns exist in the catalog
  4. certified items are complete (owner + steward, review_by; entities need checks)
  5. certified definitions sharing a synonym must link each other in not_to_be_confused_with
  6. (warning) templates pinned to an older version of something they use → needs re-verification
  7. (warning) owner.team should match the domains/<team>/ folder
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import sqlglot
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from sqlglot import exp

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import KINDS, PLATFORMS, Layer
from semantic_layer.sl.model import Definition, Model, jsonable, load_model, ref_version, references, strip_ref
from semantic_layer.sl.sqlutil import as_join, fragment_columns


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stale_templates: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings,
                "stale_templates": self.stale_templates}


def _validators(layer: Layer) -> dict[str, Draft202012Validator]:
    resources = []
    for path in layer.schema.glob("*.schema.json"):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    registry = Registry().with_resources(resources)
    validators = {}
    for kind in KINDS:
        schema = json.loads((layer.schema / f"{kind}.schema.json").read_text(encoding="utf-8"))
        validators[kind] = Draft202012Validator(schema, registry=registry)
    return validators


def normalise_term(term: str) -> str:
    """Lower-case, collapse spaces and strip a plural 's' so 'Customers' == 'customer'."""
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in term.lower().replace("-", " ").split()]
    return " ".join(words)


def _rel(layer: Layer, definition: Definition) -> str:
    return definition.path.relative_to(layer.root).as_posix()


def validate(layer: Layer, model: Model | None = None) -> Report:
    model = model or load_model(layer)
    catalog = catalog_mod.load(layer)
    report = Report(errors=list(model.load_errors))
    validators = _validators(layer)
    dialects = {platform: layer.dialect(platform) for platform in PLATFORMS}

    for definition in model.all():
        where = _rel(layer, definition)
        # 1. schema
        for error in sorted(validators[definition.kind].iter_errors(jsonable(definition.data)), key=str):
            location = "/".join(str(p) for p in error.absolute_path) or "(root)"
            report.errors.append(f"{where}: schema: {error.message} at {location}")
        # 2. references
        for ref in references(definition):
            target = model.find(ref)
            if target is None:
                report.errors.append(f"{where}: dangling reference '{ref}'")
                continue
            pinned = ref_version(ref)
            if pinned is not None and pinned > target.version:
                report.errors.append(f"{where}: pins {ref} but {target.id} is only at version {target.version}")
        # 3. bindings against the catalog
        _check_bindings(definition, catalog, dialects, report, where)
        # 4. certified completeness
        if definition.certified:
            if not definition.data.get("review_by"):
                report.errors.append(f"{where}: certified but has no review_by date")
            if definition.kind == "entity" and not definition.data.get("checks"):
                report.errors.append(f"{where}: certified entity has no quality checks")
            if definition.kind == "template" and not definition.data.get("expires"):
                report.errors.append(f"{where}: certified template has no expires date")
        if definition.kind == "template":
            for platform, rel_path in (definition.data.get("sql") or {}).items():
                if not (definition.path.parent / rel_path).exists():
                    report.errors.append(f"{where}: sql file for {platform} not found: {rel_path}")
        # 7. ownership folder
        team = definition.owner.get("team")
        if team and definition.domain not in ("shared", team):
            report.warnings.append(f"{where}: owner.team '{team}' does not match folder domains/{definition.domain}/")
        if definition.kind == "relationship":
            _check_relationship_kinds(definition, model, dialects, report, where)

    # 6. stale template pins
    for stale in stale_pins(model):
        template = model.get(stale["template"])
        report.warnings.append(f"{_rel(layer, template)}: pins {stale['pinned']} but it is now "
                               f"{stale['current']} — needs re-verification")
        report.stale_templates.append(stale)

    _check_synonyms(model, report)
    return report


def _check_bindings(definition: Definition, catalog: catalog_mod.Catalog, dialects: dict[str, str],
                    report: Report, where: str) -> None:
    for binding in definition.data.get("bindings", []) or []:
        platform, source = binding.get("platform"), binding.get("source")
        if platform not in PLATFORMS or not source:
            continue  # schema already reports it
        if not catalog.has_table(platform, source):
            report.errors.append(f"{where}: binding table {platform}:{source} is not in the catalog")
            continue
        known = catalog.column_names(platform, source)
        try:
            wanted = binding_columns(definition, binding, dialects[platform])
        except sqlglot.errors.ParseError as exc:
            report.errors.append(f"{where}: {platform} fragment does not parse: {exc}")
            continue
        for column in sorted(wanted - known):
            report.errors.append(f"{where}: column '{column}' not found in {platform}:{source}")


def binding_columns(definition: Definition, binding: dict, dialect: str) -> set[str]:
    """Lower-cased columns a binding reads (key, time, label, filter, expression, levels).

    Shared by validation (against the catalog snapshot) and the drift check (against live metadata).
    """
    platform = binding["platform"]
    wanted: set[str] = set()
    for field_name in ("key", "time_column", "label_column"):
        if binding.get(field_name):
            wanted.add(str(binding[field_name]).lower())
    if binding.get("filter"):
        wanted |= fragment_columns(binding["filter"], dialect, predicate=True)
    if binding.get("expression"):
        wanted |= fragment_columns(binding["expression"], dialect, predicate=False)
    for level in definition.data.get("levels", []) or []:
        expression = (level.get("expressions") or {}).get(platform)
        if expression:
            wanted |= fragment_columns(expression, dialect, predicate=False)
    return wanted


def _check_relationship_kinds(definition: Definition, model: Model, dialects: dict[str, str], report: Report,
                              where: str) -> None:
    source, target = model.find(definition.data.get("from", "")), model.find(definition.data.get("to", ""))
    if source and source.kind not in ("entity", "dimension"):
        report.errors.append(f"{where}: 'from' must be an entity or dimension, got {source.kind}")
    if target and target.kind != "dimension":
        report.errors.append(f"{where}: 'to' must be a dimension, got {target.kind}")
    if not (source and target):
        return
    for platform, join in (definition.data.get("join") or {}).items():
        if not (source.binding(platform) and target.binding(platform)):
            report.errors.append(f"{where}: join given for {platform} but both sides are not bound there")
            continue
        try:
            on = as_join(join, dialects[platform])
        except sqlglot.errors.ParseError as exc:
            report.errors.append(f"{where}: {platform} join does not parse: {exc}")
            continue
        aliases = {column.table for column in on.find_all(exp.Column)}
        if not aliases <= {"f", "o"}:
            report.errors.append(f"{where}: {platform} join must reference only {{from}} and {{to}}")


def _check_synonyms(model: Model, report: Report) -> None:
    certified = [d for d in model.all() if d.certified and d.kind in ("entity", "dimension", "metric", "filter")]
    seen: dict[str, list[Definition]] = {}
    for definition in certified:
        terms = {normalise_term(t) for t in definition.synonyms + [definition.data.get("name", "")] if t}
        for term in terms:
            seen.setdefault(term, []).append(definition)
    reported: set[tuple[str, str]] = set()
    for term, holders in sorted(seen.items()):
        for i, first in enumerate(holders):
            for second in holders[i + 1:]:
                pair = tuple(sorted((first.id, second.id)))
                if pair in reported:
                    continue
                links_first = {c["id"] for c in first.data.get("not_to_be_confused_with", []) or []}
                links_second = {c["id"] for c in second.data.get("not_to_be_confused_with", []) or []}
                if second.id not in links_first and first.id not in links_second:
                    reported.add(pair)
                    report.errors.append(
                        f"synonym '{term}' is shared by certified {pair[0]} and {pair[1]} but neither lists "
                        f"the other in not_to_be_confused_with"
                    )


def stale_pins(model: Model) -> list[dict[str, str]]:
    """Templates whose pinned versions are behind — reused by checks and the ask pipeline."""
    stale = []
    for template in model.of_kind("template"):
        for ref in template.data.get("uses", []) or []:
            target = model.find(ref)
            pinned = ref_version(ref)
            if target and pinned is not None and pinned < target.version:
                stale.append({"template": template.id, "pinned": ref, "current": f"{strip_ref(ref)}@{target.version}"})
    return stale
