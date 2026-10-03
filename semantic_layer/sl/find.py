"""``sl search | show | owners | table | lineage | health`` — lookups without SQL.

Each handler returns ``(data, markdown)``; the CLI prints one or the other.
The ``semantic-find`` skill is a thin layer over these.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from typing import Any

from semantic_layer.sl import catalog as catalog_mod
from semantic_layer.sl.common import Layer, LayerError, read_json, resolve_today
from semantic_layer.sl.model import jsonable, load_model, references, strip_ref
from semantic_layer.sl.validate import normalise_term

Result = tuple[Any, str]


def search(layer: Layer, args: argparse.Namespace) -> Result:
    model = load_model(layer)
    query = normalise_term(args.text)
    words = set(query.split())
    hits = []
    for definition in model.all():
        names = [normalise_term(s) for s in definition.synonyms + [str(definition.data.get("name", ""))]]
        text = normalise_term(" ".join([definition.id.replace(".", " ").replace("_", " "),
                                        str(definition.data.get("description", ""))] + names))
        if query in names:
            score = 3
        elif query in text:
            score = 2
        else:
            score = len(words & set(text.split())) / max(len(words), 1)
        if score >= 0.5:
            hits.append({"id": definition.id, "kind": definition.kind, "name": definition.data.get("name"),
                         "status": definition.status, "team": definition.owner.get("team"), "score": round(score, 2)})
    hits.sort(key=lambda h: (-h["score"], h["id"]))
    markdown = "\n".join([f"## Search: {args.text}"] + [
        f"- `{h['id']}` ({h['kind']}, {h['status']}, {h['team']}) — {h['name']}" for h in hits
    ] or ["_No match. Not in the layer yet — consider harvesting a query that uses it._"])
    return hits, markdown


def show(layer: Layer, args: argparse.Namespace) -> Result:
    model = load_model(layer)
    definition = model.get(args.id)
    data = jsonable(definition.data)
    payload = {"id": definition.id, "path": definition.path.relative_to(layer.root).as_posix(), "data": data,
               "uses": sorted({strip_ref(r) for r in references(definition, include_links=False)}),
               "used_by": sorted(model.used_by.get(definition.id, set())),
               "history": _git_history(layer, definition.path)}
    owner = definition.owner
    lines = [f"## {data.get('name')} (`{definition.id}`)",
             f"{definition.kind} · **{definition.status}** · v{definition.version} · owner {owner.get('team')} · "
             f"steward **{owner.get('steward')}** · review by {data.get('review_by', '—')}", "",
             " ".join(str(data.get("description", "")).split())]
    for binding in data.get("bindings", []) or []:
        lines.append(f"- {binding['platform']}: `{binding['source']}`"
                     + (f" where `{binding['filter']}`" if binding.get("filter") else "")
                     + (f" ({binding.get('status')})" if binding.get("status") else ""))
    for note in data.get("not_to_be_confused_with", []) or []:
        lines.append(f"- ≠ `{note['id']}`: {note['difference']}")
    if payload["used_by"]:
        lines.append(f"\nUsed by: {', '.join(payload['used_by'])}")
    if payload["history"]:
        lines.append("\n**Recent changes**")
        lines += [f"- {h}" for h in payload["history"]]
    return payload, "\n".join(lines)


def _git_history(layer: Layer, path: Any, limit: int = 5) -> list[str]:
    """Who changed this definition and why — straight from git (no hand-kept changelog)."""
    kwargs: dict[str, Any] = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(["git", "log", f"-{limit}", "--format=%ad %an: %s", "--date=short", "--", str(path)],
                                cwd=layer.root, check=False, **kwargs)
    except OSError:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def owners(layer: Layer, args: argparse.Namespace) -> Result:
    model = load_model(layer)
    teams: dict[str, list[dict[str, Any]]] = {}
    for definition in model.all():
        team = definition.owner.get("team", "unowned")
        if args.team and team != args.team:
            continue
        teams.setdefault(team, []).append({"id": definition.id, "kind": definition.kind,
                                           "status": definition.status,
                                           "steward": definition.owner.get("steward")})
    if args.team and not teams:
        raise LayerError(f"No definitions owned by team '{args.team}'")
    lines = []
    for team, items in sorted(teams.items()):
        stewards = sorted({i["steward"] for i in items})
        lines.append(f"## {team} — {len(items)} definitions (stewards: {', '.join(stewards)})")
        lines += [f"- `{i['id']}` ({i['kind']}, {i['status']})" for i in items]
    return teams, "\n".join(lines)


def table(layer: Layer, args: argparse.Namespace) -> Result:
    model = load_model(layer)
    catalog = catalog_mod.load(layer)
    found = [(p, t) for p, t in catalog.all_tables() if catalog_mod.norm(t) == catalog_mod.norm(args.name)
             or t.split(".")[-1].lower() == args.name.lower()]
    if not found:
        raise LayerError(f"Table '{args.name}' is not in the catalog")
    results = []
    lines = []
    for platform, name in found:
        users = [d.id for d in model.all() if any(b["platform"] == platform and catalog_mod.norm(b["source"])
                                                  == catalog_mod.norm(name) for b in d.data.get("bindings", []) or [])]
        row = catalog.counterpart(platform, name) or {}
        results.append({"platform": platform, "table": name, "migration_status": row.get("status", "unknown"),
                        "counterpart": catalog.other_side(platform, name), "used_by": users})
        lines.append(f"## {name} ({platform}) — migration: {row.get('status', 'unknown')}"
                     + (f", counterpart `{catalog.other_side(platform, name)}`" if catalog.other_side(platform, name)
                        else ""))
        lines += [f"- used by `{u}`" for u in users] or ["- _not used by any definition_"]
    return results, "\n".join(lines)


def lineage(layer: Layer, args: argparse.Namespace) -> Result:
    model = load_model(layer)
    root = model.get(args.id)

    def walk(def_id: str, direction: str, seen: set[str]) -> list[dict[str, Any]]:
        definition = model.find(def_id)
        if definition is None:
            return []
        nxt = sorted({strip_ref(r) for r in references(definition, include_links=False)}) if direction == "uses" \
            else sorted(model.used_by.get(def_id, set()))
        nodes = []
        for child in nxt:
            if child in seen:
                continue  # each definition once — the graph has shared dependencies
            seen.add(child)
            nodes.append({"id": child, "children": walk(child, direction, seen)})
        return nodes

    tree = {"id": root.id, "uses": walk(root.id, "uses", {root.id}), "used_by": walk(root.id, "used_by", {root.id})}

    def render(nodes: list[dict[str, Any]], indent: int) -> list[str]:
        out = []
        for node in nodes:
            out.append("  " * indent + f"- `{node['id']}`")
            out += render(node["children"], indent + 1)
        return out

    lines = [f"## Lineage of `{root.id}`", "**Uses**"] + (render(tree["uses"], 0) or ["- (nothing)"])
    lines += ["**Used by**"] + (render(tree["used_by"], 0) or ["- (nothing)"])
    return tree, "\n".join(lines)


def health(layer: Layer, args: argparse.Namespace) -> Result:
    model = load_model(layer)
    today = resolve_today(getattr(args, "today", None))
    path = layer.build / "health.json"
    report = read_json(path) if path.exists() else {"definitions": {}, "generated_on": None}
    rows = []
    for definition in model.all():
        state = report["definitions"].get(definition.id, {})
        review_by = definition.data.get("review_by")
        overdue = bool(review_by and dt.date.fromisoformat(str(review_by)) < today)
        if args.overdue and not overdue:
            continue
        rows.append({"id": definition.id, "status": definition.status, "health": state.get("status", "unknown"),
                     "findings": state.get("findings", []), "review_by": str(review_by or ""), "overdue": overdue,
                     "steward": definition.owner.get("steward")})
    header = f"## Health (checks run: {report.get('generated_on') or 'never — run `sl checks run`'})"
    lines = [header, "| definition | status | health | review by | steward |", "|---|---|---|---|---|"]
    lines += [f"| `{r['id']}` | {r['status']} | {r['health']} | {r['review_by']}{' ⚠️ overdue' if r['overdue'] else ''}"
              f" | {r['steward']} |" for r in rows]
    return rows, "\n".join(lines)
