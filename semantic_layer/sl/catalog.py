"""The physical catalog: generated table/column metadata for both platforms
plus the legacy → cloud migration map. Never hand-edited.

``sl catalog generate`` writes one YAML per table under ``catalog/<platform>/``
and ``catalog/migration_map.yaml``. Validation reads the committed files, so it
works offline; the P1 checks regenerate in memory and diff (catalog refresh).

**Porting seam:** in a real deployment the catalog usually comes from an
existing metadata repository or table registry rather than from querying the
warehouses directly. Replace :func:`collect` (or feed its output) — every other
module only reads :class:`Catalog`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from semantic_layer.sl.common import PLATFORMS, Layer, read_yaml, write_yaml
from semantic_layer.sl.platforms import get_platform, split_table


def norm(table: str) -> str:
    return table.replace("`", "").lower()


@dataclass
class Catalog:
    tables: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # platform → norm name → entry
    migration: list[dict[str, Any]] = field(default_factory=list)

    def has_table(self, platform: str, table: str) -> bool:
        return norm(table) in self.tables.get(platform, {})

    def entry(self, platform: str, table: str) -> dict[str, Any] | None:
        return self.tables.get(platform, {}).get(norm(table))

    def column_names(self, platform: str, table: str) -> set[str]:
        entry = self.entry(platform, table)
        return {c["name"].lower() for c in entry["columns"]} if entry else set()

    def counterpart(self, platform: str, table: str) -> dict[str, Any] | None:
        """Migration-map row for ``table`` seen from ``platform``."""
        for row in self.migration:
            if norm(row.get(platform) or "") == norm(table):
                return row
        return None

    def other_side(self, platform: str, table: str) -> str | None:
        row = self.counterpart(platform, table)
        if not row:
            return None
        other = "cloud" if platform == "legacy" else "legacy"
        return row.get(other)

    def map_column(self, platform: str, table: str, column: str) -> str | None:
        """Translate a column name to the other platform's spelling."""
        row = self.counterpart(platform, table)
        if not row:
            return None
        mapping = row.get("columns", {})
        if platform == "legacy":
            return mapping.get(column.upper()) or mapping.get(column)
        reverse = {v.lower(): k for k, v in mapping.items()}
        return reverse.get(column.lower())

    def all_tables(self) -> list[tuple[str, str]]:
        return [(p, e["table"]) for p in PLATFORMS for e in self.tables.get(p, {}).values()]


def collect(layer: Layer) -> Catalog:
    """Read live metadata from both platforms through their adapters."""
    excluded = {s.lower() for s in layer.config.get("catalog", {}).get("exclude_schemas", [])}
    catalog = Catalog()
    for platform in PLATFORMS:
        adapter = get_platform(layer, platform)
        catalog.tables[platform] = {}
        for table in adapter.tables():
            schema, _ = split_table(table)
            if schema.lower() in excluded:
                continue
            columns = [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in adapter.columns(table)]
            catalog.tables[platform][norm(table)] = {"table": table, "platform": platform, "columns": columns}
    catalog.migration = derive_migration_map(catalog)
    return catalog


def derive_migration_map(catalog: Catalog) -> list[dict[str, Any]]:
    """Pair tables by name (schema ignored) and columns by case-insensitive name.

    The mock follows a 'same name, lowercase, new dataset' convention. A real
    migration usually has a registry instead — swap this rule for a lookup.
    """
    cloud_by_name = {e["table"].split(".")[-1].lower(): e for e in catalog.tables.get("cloud", {}).values()}
    rows: list[dict[str, Any]] = []
    for entry in sorted(catalog.tables.get("legacy", {}).values(), key=lambda e: e["table"]):
        target = cloud_by_name.get(entry["table"].split(".")[-1].lower())
        row: dict[str, Any] = {"legacy": entry["table"], "cloud": target["table"] if target else None,
                               "status": "migrated" if target else "pending"}
        if target:
            cloud_cols = {c["name"].lower(): c["name"] for c in target["columns"]}
            row["columns"] = {c["name"]: cloud_cols[c["name"].lower()]
                              for c in entry["columns"] if c["name"].lower() in cloud_cols}
            missing = [c["name"] for c in entry["columns"] if c["name"].lower() not in cloud_cols]
            if missing:
                row["unmapped_columns"] = missing
        rows.append(row)
    return rows


def write(layer: Layer, catalog: Catalog) -> list[Path]:
    """Replace ``catalog/`` with the given snapshot (deterministic output)."""
    written: list[Path] = []
    for platform in PLATFORMS:
        folder = layer.catalog / platform
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob("*.yaml"):
            stale.unlink()
        for entry in sorted(catalog.tables.get(platform, {}).values(), key=lambda e: e["table"]):
            path = folder / f"{entry['table']}.yaml"
            write_yaml(path, entry)
            written.append(path)
    path = layer.catalog / "migration_map.yaml"
    write_yaml(path, {"generated_by": "sl catalog generate", "tables": catalog.migration})
    written.append(path)
    return written


def load(layer: Layer) -> Catalog:
    """Load the committed catalog snapshot (empty catalog if none yet)."""
    catalog = Catalog()
    for platform in PLATFORMS:
        catalog.tables[platform] = {}
        folder = layer.catalog / platform
        if folder.is_dir():
            for path in sorted(folder.glob("*.yaml")):
                entry = read_yaml(path)
                catalog.tables[platform][norm(entry["table"])] = entry
    map_path = layer.catalog / "migration_map.yaml"
    if map_path.exists():
        catalog.migration = (read_yaml(map_path) or {}).get("tables", []) or []
    return catalog


def diff(old: Catalog, new: Catalog) -> list[dict[str, Any]]:
    """Table- and column-level differences between two snapshots (catalog refresh)."""
    changes: list[dict[str, Any]] = []
    for platform in PLATFORMS:
        before, after = old.tables.get(platform, {}), new.tables.get(platform, {})
        for name in sorted(set(before) | set(after)):
            if name not in after:
                changes.append({"platform": platform, "table": before[name]["table"], "change": "table_removed"})
                continue
            if name not in before:
                changes.append({"platform": platform, "table": after[name]["table"], "change": "table_added"})
                continue
            old_cols = {c["name"].lower(): c for c in before[name]["columns"]}
            new_cols = {c["name"].lower(): c for c in after[name]["columns"]}
            for col in sorted(set(old_cols) - set(new_cols)):
                changes.append({"platform": platform, "table": after[name]["table"], "change": "column_removed",
                                "column": old_cols[col]["name"]})
            for col in sorted(set(new_cols) - set(old_cols)):
                changes.append({"platform": platform, "table": after[name]["table"], "change": "column_added",
                                "column": new_cols[col]["name"]})
            for col in sorted(set(old_cols) & set(new_cols)):
                if str(old_cols[col]["type"]).upper() != str(new_cols[col]["type"]).upper():
                    changes.append({"platform": platform, "table": after[name]["table"], "change": "type_changed",
                                    "column": new_cols[col]["name"], "from": old_cols[col]["type"],
                                    "to": new_cols[col]["type"]})
    return changes
