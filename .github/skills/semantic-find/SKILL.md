---
name: semantic-find
description: Look things up in the team semantic layer without writing SQL — where a definition lives on each platform, who owns it and who the steward is, which definitions use a table, whether a table is migrated, what changed and when, and current health. Use for "where is the customer definition", "who owns X", "what uses this table", "what changed in Y", "is Z still valid".
---

# semantic-find

First read `.github/skills/_shared/semantic-common.md`.

Pick the command that answers the question and show its markdown output. Add `--json` only if you need to read it yourself first.

| Question | Command |
|---|---|
| "Is there a definition for X?" / "what do we call Y?" | `python -m semantic_layer search "<words>"` |
| "What exactly is X, and who do I ask?" | `python -m semantic_layer show <id>` (definition, bindings per platform, confusions, steward, recent git history) |
| "What does team T own?" | `python -m semantic_layer owners --team <team>` |
| "What uses table T? Is it migrated?" | `python -m semantic_layer table <name>` |
| "What depends on X / what does X depend on?" | `python -m semantic_layer lineage <id>` |
| "Is X healthy? What's overdue?" | `python -m semantic_layer health [--overdue]` |

Rules:
- Always name the **steward**: they are the answer to "who do I ask?".
- When the user asks *what changed*, use the history in `sl show` (git log). Don't guess.
- If `health` says checks never ran, or a status is `unknown`, say exactly that. Never present unknown as fine.
- If the thing isn't in the layer, say so and offer `semantic-harvest` (the user has a working query) or `semantic-contribute` (they can describe it).
