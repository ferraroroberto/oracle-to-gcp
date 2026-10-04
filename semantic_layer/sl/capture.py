"""``sl capture`` — the layer grows from normal use.

When a user, mid-answer, settles an ambiguity the layer did not cover, corrects
the SQL, or says a number is wrong, the ``semantic-ask`` skill offers to save it:

- ``capture definition`` writes a *draft* definition with capture provenance
  (the user's words), validates it, and prints the branch/PR commands the skill
  runs. Owners certify it in review — capture never certifies.
- ``capture issue`` drafts an issue body for the steward of the definition concerned.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from semantic_layer.sl.common import KIND_DIRS, Layer, LayerError, write_yaml
from semantic_layer.sl.model import load_model
from semantic_layer.sl.validate import validate

ALLOWED_FIELDS = {"name", "description", "synonyms", "grain", "bindings", "not_to_be_confused_with", "caveats",
                  "expression", "applies_to", "additivity", "levels", "ai_context"}
REQUIRED = {"entity": ("bindings", "grain"), "dimension": ("bindings",), "filter": ("bindings",),
            "metric": ("expression", "applies_to", "additivity")}


def definition(layer: Layer, kind: str, def_id: str, team: str, steward: str, note: str,
               fields: dict[str, Any]) -> Path:
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise LayerError(f"Unsupported fields for capture: {', '.join(sorted(unknown))}")
    missing = [f for f in REQUIRED[kind] if not fields.get(f)]
    if missing:
        raise LayerError(f"A captured {kind} needs {', '.join(missing)} — ask the user, or look the table up with "
                         "`sl table <name>`, before saving")
    if load_model(layer).find(def_id):
        raise LayerError(f"{def_id} already exists — propose a change to it instead (or pick a distinct id)")
    record: dict[str, Any] = {"id": def_id, "kind": kind, "name": fields.get("name", def_id)}
    for key in ("description", "synonyms", "grain", "not_to_be_confused_with", "caveats", "ai_context"):
        if fields.get(key):
            record[key] = fields[key]
    record["owner"] = {"team": team, "steward": steward}
    record["status"] = "draft"
    record["version"] = 1
    for key in ("bindings", "levels", "expression", "applies_to", "additivity"):
        if fields.get(key):
            record[key] = fields[key]
    record["provenance"] = {
        "method": "capture", "from": "conversation", "session": note,
        "confirmed_on": dt.date.today().isoformat(),
        "open_questions": ["Captured in an ask session — owner to confirm the definition and add quality checks"],
    }
    path = layer.domains / team / KIND_DIRS[kind] / f"{def_id}.yaml"
    write_yaml(path, record)
    return path


def definition_markdown(layer: Layer, path: Path) -> str:
    rel = path.relative_to(layer.root).as_posix()
    report = validate(layer)
    own = [e for e in report.errors if rel in e]
    def_id = path.stem
    lines = [f"✅ Draft saved: `{rel}` (status: draft)"]
    lines += [f"❌ {e}" for e in own] or ["✅ It validates."]
    lines += ["", "Next (the skill runs these; the owner certifies in review):", "```",
              f"git checkout -b capture/{def_id.replace('.', '-')}",
              f"git add {path.as_posix()}",
              f'git commit -m "feat: capture draft {def_id} from an ask session"',
              "gh pr create --draft --fill", "```"]
    return "\n".join(lines)


def issue(layer: Layer, about: str, note: str, reported_by: str) -> dict[str, Any]:
    model = load_model(layer)
    target = model.get(about)
    steward = target.owner.get("steward", "?")
    title = f"semantic layer: {about} — reported problem"
    body = "\n".join([
        f"**Definition:** `{about}` ({target.kind}, {target.status}, v{target.version})",
        f"**Owner:** {target.owner.get('team')} · **Steward:** @{steward}",
        f"**Reported by:** {reported_by}", "",
        "**What the user said:**", "", f"> {note}", "",
        "Please confirm whether the definition, its binding, or the user's expectation is wrong, and update the "
        "YAML (bump `version`) if the definition changes.",
    ])
    command = f'gh issue create --title "{title}" --body-file <file> --assignee {steward} --label bug'
    return {"title": title, "body": body, "steward": steward, "command": command,
            "markdown": f"## Issue draft for @{steward}\n\n**{title}**\n\n{body}\n\n`{command}`"}
