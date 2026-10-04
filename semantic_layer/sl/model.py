"""Load the curated definitions into one resolved, queryable model.

One YAML file = one definition, under ``domains/<team>/<kind-folder>/``.
References are resolved both ways — what a definition *uses* and what *uses
it* — so ``sl lineage``, staleness checks and the docs all read the same graph.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from semantic_layer.sl.common import KINDS, Layer, LayerError, read_yaml


@dataclass
class Definition:
    id: str
    kind: str
    data: dict[str, Any]
    path: Path
    domain: str

    @property
    def status(self) -> str:
        return str(self.data.get("status", "draft"))

    @property
    def version(self) -> int:
        return int(self.data.get("version", 1))

    @property
    def certified(self) -> bool:
        return self.status == "certified"

    @property
    def owner(self) -> dict[str, str]:
        return self.data.get("owner") or {}

    @property
    def synonyms(self) -> list[str]:
        return [str(s) for s in self.data.get("synonyms", []) or []]

    def binding(self, platform: str) -> dict[str, Any] | None:
        for binding in self.data.get("bindings", []) or []:
            if binding.get("platform") == platform:
                return binding
        return None

    def platforms(self) -> list[str]:
        return [b["platform"] for b in self.data.get("bindings", []) or []]

    def preferred_platform(self) -> str | None:
        bindings = self.data.get("bindings", []) or []
        for binding in bindings:
            if binding.get("preferred"):
                return str(binding["platform"])
        for platform in ("cloud", "legacy"):
            if any(b.get("platform") == platform for b in bindings):
                return platform
        return None


@dataclass
class Model:
    definitions: dict[str, Definition] = field(default_factory=dict)
    load_errors: list[str] = field(default_factory=list)
    used_by: dict[str, set[str]] = field(default_factory=dict)  # structural dependents only

    def get(self, def_id: str) -> Definition:
        bare = strip_ref(def_id)
        if bare not in self.definitions:
            raise LayerError(f"Unknown definition '{def_id}'")
        return self.definitions[bare]

    def find(self, def_id: str) -> Definition | None:
        return self.definitions.get(strip_ref(def_id))

    def of_kind(self, kind: str) -> list[Definition]:
        return sorted((d for d in self.definitions.values() if d.kind == kind), key=lambda d: d.id)

    def all(self) -> list[Definition]:
        return sorted(self.definitions.values(), key=lambda d: d.id)

    def relationships_from(self, def_id: str) -> list[Definition]:
        return [r for r in self.of_kind("relationship") if r.data.get("from") == def_id]


def strip_ref(ref: str) -> str:
    """``'dimension.home_store:region'`` / ``'customer.active_buyer@3'`` → bare id."""
    return ref.split("@")[0].split(":")[0]


def ref_version(ref: str) -> int | None:
    return int(ref.split("@")[1]) if "@" in ref else None


def jsonable(value: Any) -> Any:
    """YAML dates → ISO strings so JSON Schema and JSON output see plain types."""
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def references(definition: Definition, include_links: bool = True) -> list[str]:
    """Every id this definition points at (versions/levels kept for callers that need them).

    ``include_links=False`` keeps only structural dependencies — the cross-references in
    ``not_to_be_confused_with`` / ``replaced_by`` are informational, not lineage.
    """
    data = definition.data
    refs: list[str] = []
    if include_links:
        refs += [c["id"] for c in data.get("not_to_be_confused_with", []) or [] if isinstance(c, dict) and "id" in c]
        if data.get("replaced_by"):
            refs.append(data["replaced_by"])
        refs += data.get("anchors", []) or []  # an entity lists its anchors; the anchor depends on it
    kind = definition.kind
    if kind == "metric":
        refs += data.get("applies_to", []) or []
    elif kind == "relationship":
        refs += [data.get("from", ""), data.get("to", "")]
        refs += data.get("safe_for_metrics", []) or []
    elif kind == "template":
        refs += [data.get("entity", ""), data.get("metric", "")]
        refs += data.get("dimensions", []) or []
        refs += data.get("uses", []) or []
        expected = data.get("expected") or {}
        if expected.get("reconciles_with"):
            refs.append(expected["reconciles_with"])
    elif kind == "anchor":
        measures = data.get("measures") or {}
        refs += [measures.get("metric", ""), measures.get("entity", "")]
    return [r for r in refs if r]


def load_model(layer: Layer) -> Model:
    """Read every definition file; structural problems become ``load_errors``."""
    model = Model()
    if not layer.domains.is_dir():
        return model
    for path in sorted(layer.domains.rglob("*.yaml")):
        relative = path.relative_to(layer.domains)
        domain = relative.parts[0]
        try:
            data = read_yaml(path)
        except Exception as exc:  # noqa: BLE001 — any parse failure is a validation error, not a crash
            model.load_errors.append(f"{relative}: YAML parse error: {exc}")
            continue
        if not isinstance(data, dict) or "id" not in data or "kind" not in data:
            model.load_errors.append(f"{relative}: not a definition (needs at least 'id' and 'kind')")
            continue
        if data["kind"] not in KINDS:
            model.load_errors.append(f"{relative}: unknown kind '{data['kind']}'")
            continue
        def_id = str(data["id"])
        if def_id in model.definitions:
            model.load_errors.append(f"{relative}: duplicate id '{def_id}' (also in {model.definitions[def_id].path.name})")
            continue
        model.definitions[def_id] = Definition(id=def_id, kind=str(data["kind"]), data=data, path=path, domain=domain)

    for definition in model.definitions.values():
        for ref in references(definition, include_links=False):
            model.used_by.setdefault(strip_ref(ref), set()).add(definition.id)
    return model


def template_sql(definition: Definition, platform: str) -> str | None:
    """The template's SQL file for ``platform``, if it has one."""
    rel = (definition.data.get("sql") or {}).get(platform)
    if not rel:
        return None
    path = definition.path.parent / rel
    return path.read_text(encoding="utf-8") if path.exists() else None
