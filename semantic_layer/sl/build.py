"""``sl build`` — one source (the YAML), every view generated.

Writes, deterministically (no timestamps, sorted keys) so ``--check`` can diff:

- ``build/index.jsonl`` — one line per definition: the compact search index an agent loads whole
- ``build/model.json`` — the resolved layer: references both ways, catalog facts, teams
- ``build/docs/`` — markdown GitHub renders: overview, one page per team, definition,
  table and domain (with a Mermaid relationship diagram)

``build/health.json`` (from ``sl checks``) and the Phase 2 navigator are runtime
outputs and are not part of ``--check``.
"""

from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import PLATFORMS, Layer
from semantic_layer.sl.model import Definition, Model, jsonable, load_model, references, strip_ref

CHECKED = ("index.jsonl", "model.json", "docs")


def _summary(definition: Definition) -> str:
    text = " ".join(str(definition.data.get("description", "")).split())
    return text.split(". ")[0].rstrip(".") if text else str(definition.data.get("name", ""))


def model_document(layer: Layer, model: Model | None = None) -> dict[str, Any]:
    """The whole layer as one JSON document (agents, the navigator and the docs all read this)."""
    model = model or load_model(layer)
    catalog = catalog_mod.load(layer)
    table_users: dict[tuple[str, str], list[str]] = defaultdict(list)
    for definition in model.all():
        for binding in definition.data.get("bindings", []) or []:
            table_users[(binding["platform"], catalog_mod.norm(binding["source"]))].append(definition.id)
    definitions = {}
    teams: dict[str, list[str]] = defaultdict(list)
    for definition in model.all():
        definitions[definition.id] = {
            "kind": definition.kind,
            "domain": definition.domain,
            "path": definition.path.relative_to(layer.root).as_posix(),
            "summary": _summary(definition),
            "uses": sorted({strip_ref(r) for r in references(definition, include_links=False)}),
            "used_by": sorted(model.used_by.get(definition.id, set())),
            "data": jsonable(definition.data),
        }
        teams[definition.owner.get("team", "unowned")].append(definition.id)
    tables = []
    for platform in PLATFORMS:
        for entry in sorted(catalog.tables.get(platform, {}).values(), key=lambda e: e["table"]):
            row = catalog.counterpart(platform, entry["table"])
            tables.append({
                "platform": platform, "table": entry["table"], "columns": len(entry["columns"]),
                "migration_status": row.get("status") if row else "unknown",
                "counterpart": catalog.other_side(platform, entry["table"]),
                "used_by": sorted(table_users.get((platform, catalog_mod.norm(entry["table"])), [])),
            })
    return {"generated_by": "sl build", "definitions": definitions, "tables": tables,
            "teams": {team: sorted(ids) for team, ids in sorted(teams.items())}}


def _index_lines(doc: dict[str, Any]) -> list[str]:
    lines = []
    for def_id, item in sorted(doc["definitions"].items()):
        data = item["data"]
        lines.append(json.dumps({
            "id": def_id, "kind": item["kind"], "name": data.get("name"), "synonyms": data.get("synonyms", []),
            "summary": item["summary"], "team": (data.get("owner") or {}).get("team"),
            "steward": (data.get("owner") or {}).get("steward"), "status": data.get("status"),
            "version": data.get("version"),
        }, sort_keys=True, ensure_ascii=False))
    return lines


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def _render(doc: dict[str, Any], out: Path) -> list[Path]:
    written = [
        _write(out / "index.jsonl", "\n".join(_index_lines(doc))),
        _write(out / "model.json", json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)),
    ]
    docs = out / "docs"
    defs = doc["definitions"]
    written.append(_write(docs / "README.md", _overview(doc)))
    for team, ids in doc["teams"].items():
        written.append(_write(docs / "teams" / f"{team}.md", _team_page(team, ids, defs)))
    for def_id, item in defs.items():
        written.append(_write(docs / "definitions" / f"{def_id}.md", _definition_page(def_id, item, defs)))
    for table in doc["tables"]:
        written.append(_write(docs / "tables" / table["platform"] / f"{table['table']}.md", _table_page(table, defs)))
    for domain in sorted({item["domain"] for item in defs.values()}):
        written.append(_write(docs / "domains" / f"{domain}.md", _domain_page(domain, defs)))
    return written


def build(layer: Layer) -> list[Path]:
    """Regenerate the checked outputs under ``build/`` (removing stale docs pages)."""
    doc = model_document(layer)
    docs = layer.build / "docs"
    if docs.exists():
        for stale in sorted(docs.rglob("*.md")):
            stale.unlink()
    return _render(doc, layer.build)


def check(layer: Layer) -> list[str]:
    """Paths under build/ that differ from a fresh build (empty list = up to date)."""
    with tempfile.TemporaryDirectory() as scratch:
        fresh_root = Path(scratch)
        _render(model_document(layer), fresh_root)
        fresh = {p.relative_to(fresh_root).as_posix(): p for p in fresh_root.rglob("*") if p.is_file()}
        committed = {}
        for name in CHECKED:
            target = layer.build / name
            if target.is_file():
                committed[name] = target
            elif target.is_dir():
                committed.update({p.relative_to(layer.build).as_posix(): p for p in target.rglob("*") if p.is_file()})
        stale = sorted(set(fresh) ^ set(committed))
        for rel in sorted(set(fresh) & set(committed)):
            if fresh[rel].read_text(encoding="utf-8") != committed[rel].read_text(encoding="utf-8"):
                stale.append(rel)
        return sorted(set(stale))


# --------------------------------------------------------------------------- markdown pages


def _link(def_id: str, from_depth: int = 1) -> str:
    return f"[`{def_id}`]({'../' * from_depth}definitions/{def_id}.md)"


def _overview(doc: dict[str, Any]) -> str:
    defs = doc["definitions"]
    by_status: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    for item in defs.values():
        by_status[item["data"].get("status", "draft")] += 1
        by_kind[item["kind"]] += 1
    covered = sum(1 for t in doc["tables"] if t["used_by"])
    lines = ["# Semantic layer — overview", "",
             "_Generated by `sl build` from the YAML under `domains/`. Do not edit; change the YAML._", "",
             f"**{len(defs)} definitions** · " + " · ".join(f"{n} {s}" for s, n in sorted(by_status.items())), "",
             "| kind | count |", "|---|---|"]
    lines += [f"| {kind} | {n} |" for kind, n in sorted(by_kind.items())]
    lines += ["", "## Teams", "", "| team | definitions | certified |", "|---|---|---|"]
    for team, ids in doc["teams"].items():
        certified = sum(1 for i in ids if defs[i]["data"].get("status") == "certified")
        lines.append(f"| [{team}](teams/{team}.md) | {len(ids)} | {certified} |")
    lines += ["", "## Catalog coverage", "",
              f"{covered} of {len(doc['tables'])} catalog tables are used by at least one definition.", "",
              "| platform | table | migration | used by |", "|---|---|---|---|"]
    for table in doc["tables"]:
        lines.append(f"| {table['platform']} | [{table['table']}](tables/{table['platform']}/{table['table']}.md) | "
                     f"{table['migration_status']} | {len(table['used_by'])} |")
    lines += ["", "## Domains", ""] + [f"- [{d}](domains/{d}.md)" for d in sorted({i['domain'] for i in defs.values()})]
    return "\n".join(lines)


def _team_page(team: str, ids: list[str], defs: dict[str, Any]) -> str:
    lines = [f"# Team: {team}", "", "| definition | kind | status | steward | review by |", "|---|---|---|---|---|"]
    for def_id in ids:
        data = defs[def_id]["data"]
        lines.append(f"| {_link(def_id)} | {defs[def_id]['kind']} | {data.get('status')} | "
                     f"{(data.get('owner') or {}).get('steward', '')} | {data.get('review_by', '')} |")
    return "\n".join(lines)


def _definition_page(def_id: str, item: dict[str, Any], defs: dict[str, Any]) -> str:
    data = item["data"]
    owner = data.get("owner") or {}
    lines = [f"# {data.get('name', def_id)}", "", f"`{def_id}` · {item['kind']} · **{data.get('status')}** · "
             f"v{data.get('version')} · owner **{owner.get('team')}** · steward **{owner.get('steward')}**", ""]
    if data.get("description"):
        lines += [" ".join(str(data["description"]).split()), ""]
    if data.get("synonyms"):
        lines += ["**Synonyms:** " + ", ".join(data["synonyms"]), ""]
    if data.get("grain"):
        lines += [f"**Grain:** {data['grain']}", ""]
    confusions = data.get("not_to_be_confused_with") or []
    if confusions:
        lines += ["## Not to be confused with", ""]
        lines += [f"- {_link(c['id'])} — {c['difference']}" for c in confusions]
        lines.append("")
    bindings = data.get("bindings") or []
    if bindings:
        lines += ["## Bindings", "", "| platform | table | key | filter / expression | status |", "|---|---|---|---|---|"]
        for b in bindings:
            fragment = b.get("filter") or b.get("expression") or ""
            lines.append(f"| {b['platform']}{' (preferred)' if b.get('preferred') else ''} | "
                         f"[{b['source']}](../tables/{b['platform']}/{b['source']}.md) | {b.get('key', '')} | "
                         f"`{fragment}` | {b.get('status', '')} |")
        lines.append("")
    if item["kind"] == "relationship":
        lines += ["## Join", "", f"{_link(data['from'])} → {_link(data['to'])} · {data.get('cardinality')}", ""]
        lines += [f"- {p}: `{j}`" for p, j in (data.get("join") or {}).items()]
        lines.append("")
    if data.get("caveats"):
        lines += ["## Caveats", ""] + [f"- {c}" for c in data["caveats"]] + [""]
    if item["uses"] or item["used_by"]:
        lines += ["## Lineage", ""]
        if item["uses"]:
            lines.append("Uses: " + ", ".join(_link(u) for u in item["uses"] if u in defs))
        if item["used_by"]:
            lines.append("Used by: " + ", ".join(_link(u) for u in item["used_by"]))
        lines.append("")
    provenance = data.get("provenance") or {}
    if provenance:
        lines += ["## Provenance", "", f"- method: {provenance.get('method')}",
                  f"- source: {provenance.get('from', '')}",
                  f"- confirmed by {provenance.get('confirmed_by', '?')} on {provenance.get('confirmed_on', '?')}"]
        lines += [f"- ❓ open question: {q}" for q in provenance.get("open_questions", []) or []]
        lines.append("")
    lines += [f"Source YAML: [`{item['path']}`](../../../{item['path']})",
              f"· review by {data.get('review_by', '—')}"]
    return "\n".join(lines)


def _table_page(table: dict[str, Any], defs: dict[str, Any]) -> str:
    lines = [f"# {table['table']} ({table['platform']})", "",
             f"Migration: **{table['migration_status']}**" +
             (f" · counterpart `{table['counterpart']}`" if table["counterpart"] else ""), "",
             f"{table['columns']} columns.", ""]
    if table["used_by"]:
        lines += ["## Used by", ""] + [f"- {_link(d, 2)} — {defs[d]['summary']}" for d in table["used_by"]]
    else:
        lines.append("_No definition uses this table yet._")
    return "\n".join(lines)


def _domain_page(domain: str, defs: dict[str, Any]) -> str:
    members = sorted(i for i, item in defs.items() if item["domain"] == domain)
    lines = [f"# Domain: {domain}", "", "```mermaid", "flowchart LR"]
    node = {def_id: f"n{i}" for i, def_id in enumerate(sorted(defs))}
    shown: set[str] = set()
    for def_id in members:
        item = defs[def_id]
        if item["kind"] != "relationship":
            continue
        data = item["data"]
        for end in (data["from"], data["to"]):
            if end in defs and end not in shown:
                lines.append(f'  {node[end]}["{end}"]')
                shown.add(end)
        if data["from"] in defs and data["to"] in defs:
            lines.append(f"  {node[data['from']]} -->|{data.get('cardinality', '')}| {node[data['to']]}")
    lines += ["```", "", "| definition | kind | status |", "|---|---|---|"]
    lines += [f"| {_link(d)} | {defs[d]['kind']} | {defs[d]['data'].get('status')} |" for d in members]
    return "\n".join(lines)
