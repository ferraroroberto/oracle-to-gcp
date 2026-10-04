# Semantic layer — shared rules for every `semantic-*` skill

Read this file once at the start of any `semantic-*` skill. It owns the rules all five skills share, so they never drift apart.

## The tool

Every structural step is a deterministic command. Never re-implement one in the conversation.

```
python -m semantic_layer <command> [--json] [--root <layer-root>]
```

- Run from the repository root; use the repo's virtualenv interpreter if it has one (e.g. `.venv\Scripts\python.exe` on Windows, `.venv/bin/python` elsewhere).
- Add `--json` whenever *you* will read the output; use the markdown form when showing the user.
- `--root` defaults to the `semantic_layer/` folder.
- Command reference: `semantic_layer/README.md` → "Commands".

## Ground rules

1. **Certified first.** Prefer certified definitions and certified templates. Anything `draft` or `reviewed` may be used only with a visible **uncertified** warning in your answer.
2. **One meaning, one id.** Never merge two definitions that share a word. When a term matches several definitions, ask the user, using the one-line differences that `sl ask resolve` returns.
3. **Parse deterministically, interpret yourself.** Tables, joins, filters and columns come from `sl` output, never from you reading SQL by eye. Your job is meaning, questions and wording.
4. **Only declared joins.** Never write a join that isn't a declared relationship. If one is missing, say so and offer to capture it.
5. **Cite every definition** you used, with its owner and steward (the provenance card from `sl ask run`). The steward is who the user asks when a number looks wrong.
6. **Skills never certify.** Drafts go through a PR, and owners certify in review. Never set `status: certified` yourself.
7. **No row-level data in the conversation.** Probes, checks and answers are aggregates. Never paste sample rows or personal data into prompts, issues or PRs.
8. **Unknown is not a pass.** If a check, dry run or platform could not run, report it as unknown, with the reason.
9. **History is git.** Who changed what and why lives in commits and PRs, read with `sl show <id>`. Don't keep a hand-written changelog.

## Offer to capture (the layer grows from use)

When, during any skill, the user settles an ambiguity the layer did not cover, corrects SQL, or says a number is wrong, offer once and in one line:

- *"Save this as a draft definition for your team?"* → `sl capture definition …`, then open a branch + draft PR with the printed commands.
- *"Open an issue for the steward of X?"* → `sl capture issue --about <id> --note "<their words>"`.

Use the user's own words in `--note`. Don't nag. If they decline, move on.
