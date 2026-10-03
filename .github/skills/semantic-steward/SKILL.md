---
name: semantic-steward
description: Run and triage the semantic layer's health checks — schema drift against live metadata, overdue reviews, stale or expired templates, catalog changes, and (Phase 2) freshness, quality rules, legacy/cloud and anchor reconciliation — grouping findings per owning team, explaining likely causes and drafting the fix PR or issue. Use for "run the weekly checks", "what's broken in the layer", "triage the health report".
---

# semantic-steward

First read `.github/skills/_shared/semantic-common.md`.

## 1. Run

```
python -m semantic_layer checks run --json            # Phase 1: drift, dates, stale pins, catalog refresh
python -m semantic_layer checks run --full --json     # only when phase2.full_checks is enabled in config.json
```

Exit code 0 = healthy, 1 = something failing, 3 = some checks could not run (**unknown**, not healthy). Results go to `build/health.json` and to one issue body per team under `build/issues/`.

## 2. Triage, per team

For each failing or unknown definition, explain the likely cause in one line:
- **schema_drift**, column gone → renamed or dropped upstream. Check `catalog.changes` for an added column with a similar name, and propose the binding fix.
- **review_expiry** → the steward re-confirms the definition, then `last_reviewed` / `review_by` move forward in a PR.
- **stale_pin / template_rerun / template_expiry** → the template is *needs re-verification*. Re-run it, compare with the anchor, and re-sign it (update the `uses` pins, `verified_by`, `verified_on`, `expires`).
- **reconcile** (legacy vs cloud) → migration divergence: a late load, a partial copy or a filter translated differently.
- **anchor_reconcile** → the published figure moved, or the data did. Ask the anchor's steward.
- **unknown** → name the unreachable platform and stop. Never report it as passing.

## 3. Act

Draft the fix PR (binding change, re-signed template) or post the team's issue body from `build/issues/<team>.md`, assigned to the stewards it names. Never certify on an owner's behalf. Never silence a check by loosening its threshold without the steward agreeing in the PR.
