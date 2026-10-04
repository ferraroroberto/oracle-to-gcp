"""[Phase 2] ``sl build --navigator`` — one self-contained HTML file to browse the layer.

Turn this on when more than one or two teams browse the layer (before that, the
generated markdown under ``build/docs/`` is enough). The page embeds
``model.json`` plus ``health.json`` and the bulk-harvest usage when present:
no server, no database, no external requests. Host it as a CI artifact, on
private Pages, or behind the organisation's login.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from semantic_layer.sl.build import model_document
from semantic_layer.sl.common import Layer, read_json


def _payload(layer: Layer) -> dict[str, Any]:
    health = layer.build / "health.json"
    usage = layer.harvest / "bulk" / "usage.json"
    data: dict[str, Any] = {"model": model_document(layer),
                            "health": read_json(health) if health.exists() else None,
                            "usage": None}
    if usage.exists():
        report = read_json(usage)
        data["usage"] = {k: report[k] for k in ("tables", "join_candidates", "unused_catalog_tables", "coverage")}
    return data


def render(layer: Layer) -> str:
    blob = json.dumps(_payload(layer), sort_keys=True, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", blob)


def write(layer: Layer) -> Path:
    path = layer.build / "navigator.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(layer), encoding="utf-8")
    return path


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Semantic Layer Navigator</title>
<style>
:root { --bg:#f7f8fa; --panel:#fff; --fg:#1d2433; --muted:#5d6779; --rule:#dfe3ea; --accent:#2457a6;
  --ok:#1f7a4a; --warn:#a86500; --bad:#b3261e; --unk:#6b6f7a; color-scheme: light; }
@media (prefers-color-scheme: dark) { :root { --bg:#12151b; --panel:#1a1f27; --fg:#e4e8ef; --muted:#9aa4b5;
  --rule:#2c3340; --accent:#86b1f2; --ok:#5cc28d; --warn:#e2a54b; --bad:#ff8a80; --unk:#9aa0ab; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
header { padding:16px; border-bottom:1px solid var(--rule); display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
h1 { font-size:18px; margin:0 12px 0 0; }
nav { display:flex; flex-wrap:wrap; gap:6px; }
nav button { font:inherit; border:1px solid var(--rule); background:var(--panel); color:var(--fg); border-radius:6px;
  padding:6px 12px; cursor:pointer; }
nav button[aria-selected="true"] { border-color:var(--accent); color:var(--accent); font-weight:600; }
input[type=search] { font:inherit; padding:6px 10px; border:1px solid var(--rule); border-radius:6px; background:var(--panel);
  color:var(--fg); min-width:min(320px, 100%); }
main { padding:16px; max-width:1200px; margin:0 auto; }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:12px; }
.card { background:var(--panel); border:1px solid var(--rule); border-radius:8px; padding:12px; min-width:0; }
.card h3 { margin:0 0 4px; font-size:15px; overflow-wrap:anywhere; }
.muted { color:var(--muted); font-size:13px; }
code { font-family: ui-monospace, Consolas, monospace; font-size:12.5px; overflow-wrap:anywhere; }
.pill { display:inline-block; font-size:12px; padding:1px 8px; border-radius:99px; border:1px solid currentColor; margin-right:4px; }
.certified, .ok { color:var(--ok); } .reviewed, .warning { color:var(--warn); } .draft, .unknown { color:var(--unk); }
.deprecated, .failing { color:var(--bad); }
table { border-collapse:collapse; width:100%; background:var(--panel); }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--rule); vertical-align:top; }
th { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
.scroll { overflow-x:auto; }
a { color:var(--accent); cursor:pointer; }
#detail { margin-top:16px; }
</style>
</head>
<body>
<header>
  <h1>Semantic Layer Navigator</h1>
  <nav id="tabs"></nav>
  <input type="search" id="q" placeholder="Search names, synonyms, descriptions" aria-label="Search">
</header>
<main><div id="view"></div><div id="detail"></div></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const DEFS = DATA.model.definitions;
const HEALTH = (DATA.health && DATA.health.definitions) || {};
const el = (tag, attrs = {}, ...kids) => { const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === 'onclick') n.onclick = v; else n.setAttribute(k, v); }
  for (const k of kids.flat()) n.append(k instanceof Node ? k : document.createTextNode(String(k ?? ''))); return n; };
const pill = (cls, text) => el('span', {class: 'pill ' + cls}, text || cls);
const health = id => (HEALTH[id] && HEALTH[id].status) || 'unknown';
const link = id => el('a', {onclick: () => showDetail(id)}, id);
const VIEWS = { Teams: teams, Concepts: concepts, Tables: tables, Relationships: relationships, 'Health & coverage': healthView };
let current = 'Concepts';

function tabs() { const nav = document.getElementById('tabs'); nav.replaceChildren(...Object.keys(VIEWS).map(name =>
  el('button', {'aria-selected': String(name === current), onclick: () => { current = name; render(); }}, name))); }
function render() { tabs(); document.getElementById('detail').replaceChildren(); document.getElementById('view').replaceChildren(VIEWS[current]()); }

function teams() { return el('div', {class: 'grid'}, Object.entries(DATA.model.teams).map(([team, ids]) => {
  const certified = ids.filter(i => DEFS[i].data.status === 'certified').length;
  const failing = ids.filter(i => health(i) === 'failing').length;
  const stewards = [...new Set(ids.map(i => (DEFS[i].data.owner || {}).steward))].join(', ');
  return el('div', {class: 'card'}, el('h3', {}, team), el('div', {class: 'muted'}, `${ids.length} definitions · ${certified} certified · stewards: ${stewards}`),
    failing ? pill('failing', `${failing} failing`) : '', el('ul', {}, ids.map(i => el('li', {}, link(i)))));
})); }

function matches(id, q) { if (!q) return true; const d = DEFS[id].data;
  return [id, d.name, d.description, ...(d.synonyms || [])].join(' ').toLowerCase().includes(q); }
function concepts() { const q = document.getElementById('q').value.trim().toLowerCase();
  const ids = Object.keys(DEFS).filter(i => ['entity', 'metric', 'dimension', 'filter', 'anchor', 'template'].includes(DEFS[i].kind) && matches(i, q));
  return el('div', {class: 'grid'}, ids.map(i => { const d = DEFS[i].data;
    return el('div', {class: 'card'}, el('h3', {}, link(i)), el('div', {}, pill(d.status), pill(health(i)), el('span', {class: 'muted'}, DEFS[i].kind)),
      el('div', {}, d.name), el('div', {class: 'muted'}, DEFS[i].summary), el('div', {class: 'muted'}, `owner ${(d.owner || {}).team} · steward ${(d.owner || {}).steward}`)); })); }

function tables() { return el('div', {class: 'scroll'}, el('table', {}, el('tr', {}, ['platform', 'table', 'migration', 'counterpart', 'used by'].map(h => el('th', {}, h))),
  DATA.model.tables.map(t => el('tr', {}, el('td', {}, t.platform), el('td', {}, el('code', {}, t.table)), el('td', {}, t.migration_status),
    el('td', {}, t.counterpart || '—'), el('td', {}, t.used_by.length ? t.used_by.map(u => el('div', {}, link(u))) : 'not covered')))))); }

function relationships() { const rels = Object.entries(DEFS).filter(([, v]) => v.kind === 'relationship');
  return el('div', {class: 'scroll'}, el('table', {}, el('tr', {}, ['from', 'to', 'cardinality', 'join (cloud)', 'status'].map(h => el('th', {}, h))),
    rels.map(([id, v]) => el('tr', {}, el('td', {}, link(v.data.from)), el('td', {}, link(v.data.to)), el('td', {}, v.data.cardinality),
      el('td', {}, el('code', {}, (v.data.join || {}).cloud || (v.data.join || {}).legacy || '')), el('td', {}, pill(v.data.status))))))); }

function healthView() { const box = el('div');
  const s = DATA.health ? DATA.health.summary : null;
  box.append(el('p', {}, s ? `Checks run ${DATA.health.generated_on} (${DATA.health.mode}): ${s.ok} ok · ${s.warning} warning · ${s.failing} failing · ${s.unknown} unknown`
    : 'No checks have run yet (sl checks run). Health shows as unknown — not as passing.'));
  const rows = Object.keys(DEFS).map(id => [id, health(id), ((HEALTH[id] || {}).findings || []).map(f => f.message).join('; ')]);
  box.append(el('div', {class: 'scroll'}, el('table', {}, el('tr', {}, ['definition', 'health', 'findings'].map(h => el('th', {}, h))),
    rows.map(([id, h, f]) => el('tr', {}, el('td', {}, link(id)), el('td', {}, pill(h)), el('td', {class: 'muted'}, f || '—'))))));
  if (DATA.usage) { box.append(el('h3', {}, 'Usage (bulk harvest)'), el('p', {class: 'muted'}, `${DATA.usage.coverage.defined} of ${DATA.usage.coverage.top_tables} tables in use have a definition.`),
    el('ul', {}, DATA.usage.tables.map(t => el('li', {}, `${t.table} — ${t.queries} queries ${t.defined ? '' : '(no definition yet)'}`))),
    el('h3', {}, 'Undeclared joins in use'), el('ul', {}, DATA.usage.join_candidates.filter(c => !c.declared).map(c => el('li', {}, el('code', {}, c.join), ` — ${c.queries} queries`)))); }
  return box; }

function showDetail(id) { const v = DEFS[id]; if (!v) return; const d = v.data;
  const card = el('div', {class: 'card'}, el('h3', {}, `${d.name || id}`), el('div', {}, el('code', {}, id), ' ', pill(d.status), pill(health(id)), ` v${d.version}`),
    el('p', {}, d.description || ''), el('div', {class: 'muted'}, `owner ${(d.owner || {}).team} · steward ${(d.owner || {}).steward} · review by ${d.review_by || '—'} · source ${v.path}`));
  if (d.synonyms) card.append(el('p', {}, 'Synonyms: ', d.synonyms.join(', ')));
  if (d.not_to_be_confused_with) card.append(el('ul', {}, d.not_to_be_confused_with.map(c => el('li', {}, 'not ', link(c.id), ': ', c.difference))));
  if (d.bindings) card.append(el('div', {class: 'scroll'}, el('table', {}, d.bindings.map(b => el('tr', {}, el('td', {}, b.platform), el('td', {}, el('code', {}, b.source)), el('td', {}, el('code', {}, b.filter || b.expression || '')), el('td', {}, b.status || ''))))));
  if (v.uses.length) card.append(el('p', {}, 'Uses: ', ...v.uses.map(u => [link(u), ' '])));
  if (v.used_by.length) card.append(el('p', {}, 'Used by: ', ...v.used_by.map(u => [link(u), ' '])));
  const f = (HEALTH[id] || {}).findings || []; if (f.length) card.append(el('ul', {}, f.map(x => el('li', {class: x.severity}, `${x.check}: ${x.message}`))));
  document.getElementById('detail').replaceChildren(card); card.scrollIntoView({behavior: 'smooth', block: 'nearest'}); }

document.getElementById('q').addEventListener('input', () => { current = 'Concepts'; render(); });
render();
</script>
</body>
</html>
"""
