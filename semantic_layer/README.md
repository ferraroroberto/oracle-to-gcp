# Team semantic layer — mock reference implementation

A git-hosted, team-owned **semantic layer** plus the deterministic toolkit (`sl`) and agent skills that use it. Business definitions (what a "customer" is, which table, which filter, who owns it, when it was last reviewed, which quality checks guard it) live as small YAML files. The skills answer business questions from them, learn new ones from working SQL, and keep them healthy.

Everything here runs on **mock data**: a fictitious retail domain in two DuckDB files standing in for a *legacy* warehouse (Oracle dialect) and a *cloud* warehouse (BigQuery dialect) mid-migration. The folder is self-contained and imports nothing from the host repo, so you can lift it out and port it (see [`PORTING.md`](PORTING.md)). The design and the phased plan behind it are in [`docs/semantic-layer-design.md`](../docs/semantic-layer-design.md).

- **Phase 1 (the pilot, the default path):** schema, validation, build/docs, harvest, ask, capture, minimal checks, two-team disagreement, evals, five skills.
- **Phase 2 (opt-in, off by default):** bulk harvest + usage ranking, full disagreement report, full checks, navigator page, OSI export. It reads the data Phase 1 already captures. See [Phase 2](#phase-2--turn-on-when).

## Quick start

From the repository root (Windows shown; on macOS/Linux use `.venv/bin/python`):

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt   # duckdb, sqlglot, PyYAML, jsonschema
& .\.venv\Scripts\python.exe -m semantic_layer mock seed          # build the two mock warehouses (~7 s)
& .\.venv\Scripts\python.exe -m semantic_layer validate
& .\.venv\Scripts\python.exe -m pytest semantic_layer/tests -q
```

Below, `sl` stands for `& .\.venv\Scripts\python.exe -m semantic_layer`. Every command takes `--json` (machine-readable, which is what the skills read) and `--root <dir>` (another layer tree), before or after the subcommand.

## The pilot walkthrough

This is the story the mock tells. It is also `tests/test_sl_walkthrough.py`, which runs it end to end with every Phase 2 flag off.

| # | Step | Command | What you see |
|---|---|---|---|
| 1 | Mock warehouses | `sl mock seed` | `legacy.duckdb` (`DWH.*`, uppercase, one table not yet migrated) and `cloud.duckdb` (`analytics.*`) |
| 2 | Physical catalog | `sl catalog generate` | `catalog/{legacy,cloud}/*.yaml` + `migration_map.yaml`: 6 legacy + 5 cloud tables, 1 pending |
| 3 | Harvest team A's working query | `sl harvest analyse queries/core_analytics_pilot.oracle.sql --platform legacy --session <id>` | parse facts, aggregate probes (`ACCOUNT_STATUS`: A 88% · C 8% · P 3%), both joins confirmed many-to-one, 12 interview questions |
| 4 | Interview → drafts | the `semantic-harvest` skill records [`interview.md`](harvest/sessions/2026-10-03-core-analytics/interview.md) + `answers.yaml`; `sl harvest draft <id>` | draft entity, dimensions, metric, relationships, template, anchor, golden questions. Owners review and certify (already done in the committed files) |
| 5 | Gate | `sl validate` · `sl build` | no errors; `build/` docs regenerated |
| 6 | Ask | `sl ask resolve "how many customers per store"` | *ambiguous*: which customer (4 options), which store (home vs first purchase), each with its difference and steward |
| | | `sl ask run spec.json` | the certified template runs, total **446**, ✅ reconciles with the management-pack headline (446), provenance card, validation plan |
| 7 | Capture | `sl capture definition …` | the user's clarification saved as a valid **draft**, plus the branch/PR commands |
| 8 | Second team | team B's [`marketing_pilot.bigquery.sql`](queries/marketing_pilot.bigquery.sql) harvested the same way; `sl disagree` | `analytics.customer_monthly`: core_analytics **446** vs marketing **398** (spread 10.8%). Marketing's is a distinct concept, kept *reviewed* with two open questions |
| 9 | Drift | rename a column in the mock, `sl checks run` | ❌ `schema_drift` on the affected definitions, one issue body per owning team |
| 10 | Evals | `sl eval` | 15 golden questions: clarification, resolution, template choice, execution and reconciliation all at 1.0 |

The committed state *is* the result of steps 1–8: the definitions under `domains/` were drafted by `sl harvest draft` from the two recorded sessions, then "reviewed" (certified, with the cloud bindings' transpile mistakes fixed by hand, and the review notes say what was fixed). `tests/test_sl_harvest.py::test_draft_reproduces_the_committed_harvest` proves the drafts reproduce them.

## Concepts

**Kinds.** `entity` (a population, one meaning per id), `metric`, `dimension` (optional hierarchy `levels`), `filter`, `relationship` (the only joins generated SQL may use), `template` (a parameterised verified query), and `anchor` (a published figure the organisation already trusts). JSON Schemas in [`schema/`](schema/).

**One meaning, one id.** "Customer" is four definitions here (`customer.active_buyer`, `customer.any_account`, `customer.campaign_reachable`, `customer.loyalty_member`) that share the synonym *customer*. `sl validate` refuses two certified definitions sharing a synonym unless one lists the other in `not_to_be_confused_with`. The agent asks the user to choose, using those notes.

**Status.** `draft` → `reviewed` → `certified` → `deprecated` (with `replaced_by`). Only certified items count as unambiguous; anything else is answered with a visible warning. Skills never certify. Owners do, in PR review.

**Bindings and placeholders.** Each definition binds to one table per platform, in that platform's dialect. Fragments use `{t}` for the table alias, `{from}`/`{to}` for the two sides of a relationship, and `{entity.key}` in metric expressions.

**Versions and templates.** A change of meaning bumps `version`. Templates pin what they use (`customer.active_buyer@1`), so a bump marks them *needs re-verification* automatically (validate warns; the checks and the ask pipeline stop treating them as certified).

**Provenance.** Every definition records how it was learnt: `method: harvest | capture | contribute`, the source query and lines, who confirmed it and when, and open questions. History of changes is git; `sl show <id>` prints it.

## Commands

| Command | Phase | What it does |
|---|---|---|
| `mock seed` | 1 | (Re)build the two mock warehouses |
| `catalog generate` | 1 | Physical catalog + migration map from platform metadata |
| `validate` | 1 | The CI gate: schema, references, bindings vs catalog, certified completeness, synonym collisions, stale pins |
| `build [--check]` | 1 | `build/index.jsonl`, `build/model.json`, `build/docs/` (fails with `--check` if stale) |
| `search` · `show` · `owners` · `table` · `lineage` · `health` | 1 | Lookups (the `semantic-find` skill) |
| `harvest analyse <query> --platform … --session …` | 1 | Parse → cross-reference → probe → interview questions |
| `harvest draft <session> [--overwrite]` | 1 | `answers.yaml` + parse → draft definitions (never overwrites reviewed work by default) |
| `ask resolve "<text>"` | 1 | Terms → definitions; every ambiguity with options |
| `ask run <spec.json \| -> [--platform] [--no-execute]` | 1 | Template-first → compose → lint → dry run → execute → reconcile → provenance |
| `capture definition …` · `capture issue …` | 1 | Save a clarification as a draft, or draft an issue for the steward |
| `checks run [--today]` | 1 | Drift, review/expiry dates, stale pins, catalog refresh → `build/health.json`, `build/issues/`. Exit 0 healthy · 1 failing · 3 unknown |
| `disagree` | 1 | Same table, different filters across harvested queries, with population counts |
| `eval` | 1 | Score `evals/golden_questions.yaml` against the thresholds in `config.json` |
| `init --clean --yes` | 1 | Empty every content folder: the "start from scratch" button |
| `harvest bulk --folder … \| --history` | 2 | Usage ranking over many queries / the query history |
| `disagree --all` | 2 | Disagreements across the whole bulk harvest |
| `checks run --full` | 2 | + freshness, quality rules, legacy ⇄ cloud and anchor reconciliation, template re-runs |
| `build --navigator` | 2 | `build/navigator.html`, one self-contained page |
| `export osi` | 2 | Experimental export to the OSI / Apache Ossie 0.2 shape |

### Harvest answers file

`harvest/sessions/<id>/answers.yaml` is what the `semantic-harvest` skill writes from the interview, and what `sl harvest draft` reads. Keys:

- `session`: `id`, `team`, `steward`, `interviewee`, `query`, `platform`, `date`
- `entities[]`: `id`, `from_alias` (the CTE or table alias in the query), `key`, `time_column`, `name`, `synonyms`, `description`, `grain`, `not_to_be_confused_with`, `caveats`, `ai_context`, `anchors`
- `dimensions[]`: `id`, `from_join` (the joined alias), and either `levels: {level: COLUMN}` or `expression_from_output: <output column>`; `reuse: true` for an existing dimension, which then only gets a new relationship
- `metrics[]`: `id`, `expression` (with `{entity.key}`), `applies_to`, `additivity`
- `anchors[]`: `id`, `published_in`, `measures`, `values[{period, value}]`, `tolerance_pct`
- `template`: `id`, `question_pattern`, `entity`, `metric`, `dimensions`, `uses`, `reconciles_with`, `parameters.period.replaces` (the literal date to parameterise)
- `golden_questions[]` (merged into `evals/`), `open_questions[]`

The two committed sessions are complete worked examples.

## Phase 2 — turn on when…

All Phase 2 modules are off in `config.json` (`"phase2": {...: false}`). A disabled command refuses with a pointer here; `--force` runs one once. Each reads data the pilot already captures, so turning one on needs no migration.

| Module | Flag | Turn on when |
|---|---|---|
| Bulk harvest + usage ranking, `disagree --all` | `bulk_harvest` | a third team joins, or you need to decide what to define next |
| Full checks | `full_checks` | the layer holds enough certified items that silent breakage becomes likely |
| Navigator page | `navigator` | more than one or two teams browse the layer (until then `build/docs/` on GitHub is enough) |
| OSI export | `osi_export` | another tool needs to consume the definitions |

Deliberately **not built**, each waiting on its trigger: an MCP server (if a client cannot run the CLI), embedding search (if the index stops fitting in context or evals show synonym misses), a business-user front end (once analysts trust the layer and business users ask for direct access), and a deterministic metric compiler (if evals show recurring join or grain errors that templates don't cover).

## Layout

```
semantic_layer/
  README.md  PORTING.md  CODEOWNERS.example  config.json
  sl/                 the toolkit (one module per command group; phase2/ for the opt-in modules)
  schema/             JSON Schema per kind
  domains/<team>/     curated definitions — one YAML per definition (+ template .sql files)
  queries/            the working queries harvested so far
  harvest/sessions/   per session: parse/xref/probes/questions JSON, interview.md, answers.yaml
  catalog/            GENERATED physical catalog (committed so validation runs offline)
  evals/              golden questions
  build/              GENERATED: index.jsonl, model.json, docs/ (committed, checked); health/issues/navigator (runtime, ignored)
  tests/              pytest suite (each test runs on a throw-away copy)
  .mock/              runtime DuckDB files (ignored)
.github/skills/semantic-{ask,find,harvest,contribute,steward}/   the agent skills (+ _shared/semantic-common.md)
.github/agents/data-analyst.agent.md                             optional custom agent
.github/workflows/semantic-layer.yml                             CI gate
```

## Honest limits of the mock

- **The language steps are not CI-tested.** Turning a question into terms and running the interview are skill prompts. The evals score everything from the extracted terms onward.
- **DuckDB is permissive.** A dry run on the mock accepts functions and types the real platform would reject (`ADD_MONTHS` in BigQuery, comparing a BOOLEAN to `0`). That's why transpiled bindings are *proposals* with a review note, and why the committed reviews fixed two of them by hand.
- **Lint matches joins by their column pairs**, not by table. That is enough to catch an invented join, but not a declared join used between the wrong pair of tables.
- **Transpilation is a suggestion.** sqlglot maps most of Oracle ⇄ BigQuery but not everything (it turned `DATE_SUB(…, INTERVAL 6 MONTH)` into invalid Oracle). Every cross-platform binding is reviewed by a person.
