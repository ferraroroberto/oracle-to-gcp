"""SQL lineage staging and markdown rendering for a translation run."""

from __future__ import annotations

from src.sql_models import SqlUnit


def compute_stages(units: list[SqlUnit]) -> dict[str, int]:
    """Return each table's dependency-depth stage.

    Stage 0 tables never appear as a target in this run (true roots). A table
    that is a target gets stage = 1 + max(stage of the sources feeding the
    unit(s) that produce it), so independent chains land at the depth that
    matches their own position instead of all sharing one row.
    """
    producer_sources: dict[str, list[str]] = {}
    for unit in units:
        for target in unit.targets:
            producer_sources.setdefault(target, []).extend(unit.sources)

    all_tables: set[str] = set()
    for unit in units:
        all_tables.update(unit.sources)
        all_tables.update(unit.targets)

    stages: dict[str, int] = {}

    def stage_of(table: str, visiting: frozenset[str]) -> int:
        if table in stages:
            return stages[table]
        if table not in producer_sources or table in visiting:
            stages[table] = 0
            return 0
        nested = visiting | {table}
        sources = producer_sources[table]
        stages[table] = max((1 + stage_of(source, nested) for source in sources), default=1)
        return stages[table]

    for table in sorted(all_tables):
        stage_of(table, frozenset())
    return stages


def build_edges(units: list[SqlUnit]) -> list[tuple[str, str, int]]:
    """Return (source, target, unit_id) edges in pipeline order, deduplicated."""
    seen: set[tuple[str, str, int]] = set()
    edges: list[tuple[str, str, int]] = []
    for unit in units:
        for target in unit.targets:
            for source in unit.sources:
                edge = (source, target, unit.id)
                if edge not in seen:
                    seen.add(edge)
                    edges.append(edge)
    return edges


def render_lineage_markdown(units: list[SqlUnit], *, script_id: str = "") -> str:
    """Render a dependency-staged markdown lineage map for a run.

    Each downstream table is listed once with its own explicit incoming
    sources, so independent single-source chains stay on separate lines
    instead of collapsing into one shared block.
    """
    stages = compute_stages(units)
    edges = build_edges(units)

    incoming: dict[str, list[tuple[str, int]]] = {}
    for source, target, unit_id in edges:
        incoming.setdefault(target, []).append((source, unit_id))

    tables_by_stage: dict[int, list[str]] = {}
    for table, stage in stages.items():
        tables_by_stage.setdefault(stage, []).append(table)

    lines: list[str] = []
    title = f"SQL Lineage Map — {script_id}" if script_id else "SQL Lineage Map"
    lines.append(f"# {title}")
    lines.append("")

    if not stages:
        lines.append("_No tables found in this run._")
        return "\n".join(lines) + "\n"

    for stage in sorted(tables_by_stage):
        heading = "Stage 0 — root sources" if stage == 0 else f"Stage {stage}"
        lines.append(f"## {heading}")
        for table in sorted(tables_by_stage[stage]):
            sources = sorted(set(incoming.get(table, [])))
            if not sources:
                lines.append(f"- `{table}`")
            else:
                for source, unit_id in sources:
                    lines.append(f"- `{table}` ← `{source}` (unit {unit_id})")
        lines.append("")

    lines.append("## Edges")
    lines.append("")
    if edges:
        lines.append("| source | target | unit |")
        lines.append("|---|---|---|")
        for source, target, unit_id in edges:
            lines.append(f"| `{source}` | `{target}` | {unit_id} |")
    else:
        lines.append("_No source→target edges recorded._")

    return "\n".join(lines) + "\n"
