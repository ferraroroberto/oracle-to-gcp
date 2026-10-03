---
name: semantic-contribute
description: Add or change a semantic-layer definition by hand when no working query shows it — interviews the contributor over the schema fields, proposes bindings from the catalog, drafts the YAML with confusion notes and checks, validates it and opens a draft PR. Use for "add a definition for X", "we need a new metric", "change the definition of Y".
---

# semantic-contribute

First read `.github/skills/_shared/semantic-common.md`. If the user has a working query, use `semantic-harvest` instead: it is faster and grounded in code.

## Add a definition

1. **Check it isn't there already:** `python -m semantic_layer search "<words>"`. If something close exists, decide with the user: same meaning → change that definition; different meaning → a new id plus `not_to_be_confused_with` links in **both** directions.
2. **Interview** over the fields for the kind (`semantic_layer/schema/<kind>.schema.json` is authoritative): name, one-paragraph description, synonyms, grain, owner team and steward. Then bindings: find the table with `python -m semantic_layer table <name>` and propose source, key, time column and filter **in each platform's dialect**, using `{t}` as the table alias. Also cover the known confusions, caveats, and at least one quality check (`nullValues` on the key, `duplicateValues` on the grain).
3. **Write** `semantic_layer/domains/<team>/<kind-folder>/<id>.yaml` with `status: draft`, `version: 1`, `provenance: {method: contribute, from: conversation, confirmed_by: <person>, confirmed_on: <date>}`.
4. `python -m semantic_layer validate`, then `python -m semantic_layer build`. Fix everything validate reports.
5. Add a golden question to `semantic_layer/evals/golden_questions.yaml` that the new concept should answer. Run `python -m semantic_layer eval`.
6. Open a branch and a **draft PR**. The owning team (CODEOWNERS) reviews and certifies.

## Change a definition

- A change of meaning **bumps `version`**. Every template pinned to the old version then shows as *needs re-verification* (`sl validate` warns), and that's intended.
- Never delete a definition. Set `status: deprecated` and `replaced_by: <new id>`.
- Never edit a definition you don't own without its owners on the PR.
