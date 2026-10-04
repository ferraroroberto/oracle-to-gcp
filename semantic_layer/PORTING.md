# Porting the semantic layer to a real repository

This folder is a working mock. In a real repository you keep the **tool** (`sl/`, `schema/`, the skills, CI) and replace the **content** (mock definitions, queries, sessions, catalog) with your own, bootstrapped from your own working SQL. Follow the steps in order. Each ends in something you can run.

## 0. What to copy

| Copy as-is | Then |
|---|---|
| `semantic_layer/` (whole folder) | wipe its content in step 2 |
| `.github/skills/semantic-*/` and `.github/skills/_shared/semantic-common.md` | Copilot, Claude Code and other `SKILL.md` agents pick them up |
| `.github/agents/data-analyst.agent.md` | optional; adjust `tools` to what your environment allows |
| `.github/workflows/semantic-layer.yml` | adjust the Python version and the install step to your CI |
| the four lines added to `requirements.txt` (`duckdb`, `sqlglot`, `PyYAML`, `jsonschema`) | `duckdb` is only needed for the mock and the tests |
| `docs/semantic-layer-design.md` | the design and phased plan: the "why" for reviewers |

## 1. Discovery (before you write anything)

Answer these and write the answers in the porting issue:

1. **Where does table/column metadata come from today?** An existing metadata repository, a table registry, or the warehouses' own `INFORMATION_SCHEMA` / `ALL_TAB_COLUMNS`? Which tables are on the legacy and the cloud platform, and is there already a legacy → cloud mapping?
2. **What can the agent reach?** BigQuery dry run, query execution, Oracle access, permitted MCP servers. Test each. The answer decides `execution.allow_execute` and which checks can run.
3. **Who are the users?** Analysts who write SQL in an IDE → the skills fit as they are. Business users → the pilot users are the analysts who serve them.
4. **Baseline.** Ad-hoc data requests per week and time to answer, for each pilot team. You'll need it to show the gain.
5. **Approvals.** Who approves a scheduled job with metadata read access? Start that request now.

## 2. Start from scratch

```
python -m semantic_layer init --clean --yes
```

This removes the mock definitions, queries, sessions, catalog and generated files, keeps the tool, schemas and `config.json`, and resets `evals/golden_questions.yaml`. `python -m semantic_layer validate` passes on the empty layer.

## 3. Point the platforms at reality

Edit `semantic_layer/config.json`:

```json
"platforms": {
  "legacy": {"adapter": "oracle",   "dialect": "oracle",
             "oracle": {"username_env": "ORACLE_USERNAME", "password_env": "ORACLE_PASSWORD",
                        "dsn_env": "ORACLE_DSN", "schemas": ["<SCHEMA>"]}},
  "cloud":  {"adapter": "bigquery", "dialect": "bigquery",
             "bigquery": {"project_env": "GOOGLE_CLOUD_PROJECT", "datasets": ["<dataset>"]}}
}
```

Credentials come from environment variables (never from the file). The real adapters lazy-import `python-oracledb` and `google-cloud-bigquery`; install whichever you use. Set `execution.allow_execute` to match what step 1.2 allows. With it set to `false`, `ask run` returns SQL plus a dry run and says that the answer was not executed. Set `catalog.exclude_schemas` and `harvest.history_table` for your platforms.

## 4. The one seam to write: the catalog source

`sl catalog generate` calls `sl/catalog.py::collect()`, which reads metadata through the adapters, and `derive_migration_map()`, which pairs tables by name. If your metadata already lives in a repository or registry, replace those two functions with a reader of that source. They only need to return a `Catalog`: tables per platform with columns, and migration rows `{legacy, cloud, status, columns}`. Nothing else in the toolkit changes.

```
python -m semantic_layer catalog generate
python -m semantic_layer validate
```

## 5. Phase 1 — the pilot

1. **Your team's query first.** Save the working query your team trusts most as `semantic_layer/queries/<team>_<topic>.<dialect>.sql` and run the `semantic-harvest` skill on it (60–90 min with its author). Expect 5–15 definitions, the relationships behind every join, the query as a template, at least one anchor and 5–10 golden questions. Owners review, fix the proposed cross-platform bindings, and certify in the PR.
2. **Use it daily for 2–3 weeks.** Route your own ad-hoc questions through `semantic-ask`. Every gap becomes a capture or a new harvest. Run `sl eval` as the golden set grows.
3. **Scheduled checks.** Run `python -m semantic_layer checks run` on a schedule from wherever has metadata access (a self-hosted runner or a cloud scheduler). Post `build/issues/<team>.md` to the owners.
4. **Second team.** They harvest their own query using only the README and the skill, with your team watching but not driving. Then run `python -m semantic_layer disagree`: every difference becomes a distinct definition or a correction.
5. **Ownership.** Copy `CODEOWNERS.example` into `.github/CODEOWNERS` with your real teams.

Exit criteria to agree up front: golden-set accuracy at the bar, both teams using it weekly, a measured time-to-answer gain against the baseline, and at least one definition contributed by the second team without being asked.

## 6. Phase 2 — when its trigger is hit

Flip the matching flag in `config.json` → `phase2` (see the README's Phase 2 table). No data migration is needed: the modules read what Phase 1 already stores.

## What the mock taught that still applies

- Transpiled bindings are proposals. Review every cross-platform filter by hand, and dry-run it on the **real** platform, which is stricter than DuckDB.
- Keep distinct meanings as distinct ids from day one. Merging later is much harder than splitting now.
- Anchors are what make people trust the numbers. Add the published figure for every headline metric you harvest.
