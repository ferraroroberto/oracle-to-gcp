---
name: semantic-harvest
description: Turn a working SQL query into semantic-layer definitions by working backwards from the code — parses it deterministically, compares it with the catalog and the certified layer, probes the data (aggregates only), interviews the author about what the code cannot say, then drafts entities, dimensions, relationships, a template, anchors and golden questions as a PR. Use for "here is our query, document it", "learn from this script", "harvest this SQL".
---

# semantic-harvest

First read `.github/skills/_shared/semantic-common.md`.

The fastest way to fill the layer is to start from SQL people already trust. The code proves the structure; the interview adds the meaning.

## 1. Analyse (deterministic)

Save the query under `semantic_layer/queries/<team>_<topic>.<dialect>.sql`, then:

```
python -m semantic_layer harvest analyse semantic_layer/queries/<file> --platform <legacy|cloud> --session <YYYY-MM-DD-team> --json
```

This writes `harvest/sessions/<session>/` (`parse.json`, `xref.json`, `probes.json`, `questions.json`). Read `xref.json` first:
- `matches` → the query already uses a certified definition, so reuse it and don't ask about it
- `differs` → **the most valuable question in the interview**: a different concept, or should it use the certified one? The answer is either a new definition or a bug in someone's query.
- `new` → nothing defined on that table yet

## 2. Interview (you)

Ask the questions from `questions.json`, **3–5 at a time, highest value first** (conflicts, then filter literals, then joins). Wherever `guess` is set, or a probe shows value shares, **propose your guess and ask for confirmation**: *"I see A 88%, C 8%, P 3% — A is open, C closed, P pending?"* Confirming is much faster than explaining from scratch.

Cover: the business question and who uses it, the meaning of every literal, named concepts behind CTEs, why LEFT vs INNER, the output grain, the published figure it should match (anchor), what breaks it, owners and stewards, which parts people change (parameters), and any joined-but-unused tables.

Keep a readable transcript as `interview.md` in the session folder.

## 3. Record the answers

Write `harvest/sessions/<session>/answers.yaml`. The shape is documented in `semantic_layer/README.md` ("Harvest answers file"), and the two committed sessions are worked examples. Use distinct ids for distinct meanings, and fill `not_to_be_confused_with` wherever a word is shared. Put anything unresolved in `open_questions`.

## 4. Draft and open a PR

```
python -m semantic_layer harvest draft <session>
python -m semantic_layer validate
python -m semantic_layer build
```

Drafts are `status: draft` with `provenance.method: harvest`. Bindings for the other platform are **proposed**: transpiled and dry-run, but marked for review, because transpilation is a suggestion and not a truth. Fix validation errors (often a missing confusion target: draft it or remove the link), then open a branch and a **draft PR** listing the open questions. Owners certify in review. Never set `certified` yourself.

Finally, run `python -m semantic_layer disagree`. If this query computes something another team computes differently, put the numbers in the PR description.
