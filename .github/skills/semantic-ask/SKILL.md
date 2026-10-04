---
name: semantic-ask
description: Answer a business data question from the team semantic layer — resolves each term to a certified definition (asking when a term is ambiguous), prefers certified query templates, builds SQL only from certified fragments and declared joins, validates it (lint, dry run, anchor reconciliation) and answers with cited owners and a validation plan. Use for "how many customers by store last month", "build me a query for X", "give me the SQL for Y".
---

# semantic-ask

First read `.github/skills/_shared/semantic-common.md`.

## 1. Turn the question into terms

Pull out the business terms the user used: the population ("active customers"), measures ("how many"), breakdowns ("by store", "by month they joined"), filters ("in the North region") and the period ("August 2026"). Use their words, not your guesses.

## 2. Resolve, and ask before building anything

```
python -m semantic_layer ask resolve "<the terms or the whole question>" --json
```

- `needs_clarification: true` → ask **all** open points in **one** message, as multiple choice, using each option's `difference`, `owner` and `steward`. Example: *"By 'customer' do you mean (a) active customers — bought in the last 12 months, management pack; (b) any registered account; (c) marketing's campaign-reachable customers?"*
- A term with no match → say it isn't in the layer. Offer either an uncertified answer built from the raw catalog (clearly flagged) or a `semantic-harvest` / `semantic-contribute` draft.
- `resolved_uncertified` → you may continue, but carry the warning into the answer.

## 3. Confirm the interpretation

Show the resolved spec in one short block: the entity, metric, breakdowns with their level (e.g. `dimension.home_store:region`), filters and period. Skip this only when every term resolved to a single certified definition and the user's wording was already precise.

## 4. Run the pipeline

Write the spec as JSON and run it:

```json
{"entity": "customer.active_buyer", "metric": "metric.customer_count",
 "dimensions": ["dimension.home_store:region"],
 "filters": [{"dimension": "dimension.home_store:region", "op": "=", "value": "North"}],
 "period": "2026-08"}
```

```
python -m semantic_layer ask run spec.json --json        # add --no-execute for "just give me the SQL"
```

The command chooses a certified template when one matches. Otherwise it composes SQL from certified fragments, then lints it, dry-runs it, executes aggregates (if allowed), reconciles against published anchors and builds the provenance card. Don't edit the SQL it returns. If something is wrong, fix the spec or the definitions.

On failure (`ok: false`), show the error plainly. "No declared relationship" means the join is not allowed yet, so offer to capture it. Never work around it with a hand-written join.

## 5. Answer

Show the markdown form (`ask run` without `--json`). It contains:
1. the result table (or the SQL, for a "build me a query" request), and what it was built from: a certified template, or new composed SQL marked uncertified
2. the reconciliation line (matches the published figure / differs / not comparable, and why)
3. the provenance card: every definition with its owner, steward, status, version and health
4. the validation plan: 2–4 concrete spot checks
5. caveats: uncertified items, legacy-platform answers, open questions, overdue reviews

## 6. Capture

If the user clarified something the layer did not know, or corrected the result, make the capture offer from the shared rules.
