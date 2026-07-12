"""Tests for dependency-staged SQL lineage computation and rendering."""

from __future__ import annotations

from src.lineage import build_edges, compute_stages, render_lineage_markdown
from src.sql_models import SqlUnit


def _unit(id_: int, sources: list[str], targets: list[str]) -> SqlUnit:
    return SqlUnit(
        id=id_,
        order=id_,
        raw_oracle="",
        pure_oracle="",
        statement_type="DDL",
        sources=sources,
        targets=targets,
    )


def test_independent_single_use_sources_stay_at_matching_stages() -> None:
    units = [
        _unit(1, ["raw_a"], ["stg_a"]),
        _unit(2, ["raw_b"], ["stg_b"]),
        _unit(3, ["raw_c"], ["stg_c"]),
    ]

    stages = compute_stages(units)

    assert stages == {
        "raw_a": 0,
        "raw_b": 0,
        "raw_c": 0,
        "stg_a": 1,
        "stg_b": 1,
        "stg_c": 1,
    }


def test_independent_chains_render_as_three_distinct_links_not_one_block() -> None:
    units = [
        _unit(1, ["raw_a"], ["stg_a"]),
        _unit(2, ["raw_b"], ["stg_b"]),
        _unit(3, ["raw_c"], ["stg_c"]),
    ]

    markdown = render_lineage_markdown(units, script_id="demo-1")
    stage1_section = markdown.split("## Stage 1")[1].split("## Edges")[0]
    edge_lines = [line for line in stage1_section.splitlines() if line.startswith("- `")]

    assert len(edge_lines) == 3
    assert "- `stg_a` ← `raw_a` (unit 1)" in edge_lines
    assert "- `stg_b` ← `raw_b` (unit 2)" in edge_lines
    assert "- `stg_c` ← `raw_c` (unit 3)" in edge_lines


def test_multi_hop_chain_advances_stage_per_hop() -> None:
    units = [
        _unit(1, ["raw"], ["stg"]),
        _unit(2, ["stg"], ["fact"]),
    ]

    stages = compute_stages(units)

    assert stages == {"raw": 0, "stg": 1, "fact": 2}


def test_multiple_sources_feed_one_target_at_the_deepest_source_stage() -> None:
    units = [
        _unit(1, ["raw_orders"], ["stg_orders"]),
        _unit(2, ["raw_customers"], ["stg_customers"]),
        _unit(3, ["stg_orders", "stg_customers"], ["fact_orders"]),
    ]

    stages = compute_stages(units)

    assert stages["fact_orders"] == 2
    edges = build_edges(units)
    assert ("stg_orders", "fact_orders", 3) in edges
    assert ("stg_customers", "fact_orders", 3) in edges


def test_target_with_no_recorded_sources_is_not_treated_as_root() -> None:
    units = [_unit(1, [], ["standalone"])]

    stages = compute_stages(units)

    assert stages["standalone"] == 1


def test_self_referencing_target_does_not_recurse_infinitely() -> None:
    units = [_unit(1, ["t"], ["t"])]

    stages = compute_stages(units)

    assert stages["t"] == 1


def test_render_lineage_markdown_includes_title_and_edges_table() -> None:
    units = [_unit(1, ["raw"], ["stg"])]

    markdown = render_lineage_markdown(units, script_id="demo-42")

    assert markdown.startswith("# SQL Lineage Map — demo-42")
    assert "## Edges" in markdown
    assert "| `raw` | `stg` | 1 |" in markdown


def test_render_lineage_markdown_handles_no_units() -> None:
    markdown = render_lineage_markdown([])

    assert "No tables found" in markdown
