# Team Semantic Layer + Data Agent — Design and Phased Plan

_Durable design record for the `semantic_layer/` reference implementation in this repository. Section 14 maps every part of this plan to the code; `semantic_layer/README.md` is the operating manual and `semantic_layer/PORTING.md` the bootstrap guide for a real repository._

> **For the agent reading this:** this is a self-contained design and implementation plan, written outside your environment. Merge it with what already exists in this repository (for example an existing repo of table and column metadata). Where the plan assumes something about the existing setup, **check first and adapt**. Every product capability marked *(verify)* is unconfirmed until tested here. Components are tagged **[P1]** (build now) or **[P2]** (designed now, built later). Phase 1 must still capture every field Phase 2 will read (section 3, principle 6).

## 1. What we are building

A **git-hosted, team-owned semantic layer**: business definitions stored as small YAML files with clear ownership, covering what a "customer" is, which table, which filter, who owns it, when it was last checked and which quality checks guard it. Around it sits a **set of agent skills** (GitHub Copilot or any agent that reads `SKILL.md` skills):

- **ask** answers business questions. It resolves each term, asks when a term is ambiguous, prefers certified query templates, builds the SQL and a validation plan, and cites sources and owners.
- **find** answers "where is X, who owns X, what changed".
- **harvest** works backwards from SQL that already works. It parses the query and interviews its author to turn the knowledge hidden in it into definitions.
- **contribute** guides a contributor through adding a definition by hand.
- **steward** runs and triages the recurring maintenance checks.

A generated **navigator** gives humans and agents the same view of who owns what.

The definitions are the durable asset. The agent is their first consumer and can be replaced later.

The data platform assumed here is generic: **a legacy warehouse and a cloud warehouse, with tables being copied from one to the other during a migration**. The examples use Oracle and BigQuery; substitute your own.

## 2. Why this shape: what the market has learned

- **The model is rarely the bottleneck; missing business context is.** On Spider 2.0 (real enterprise schemas, several SQL dialects) the best reasoning models fell from ~91% on the academic Spider 1.0 to ~21%. Most failures are at the schema level (wrong table, column or join), not SQL syntax. Vendors report large accuracy gains once a semantic layer grounds the agent. The consensus: **semantic layer first, agent second**.
- **Uber QueryGPT** (≈1.2M queries/month; authoring time ~10 → ~3 min) replaced one big prompt with small steps: an *intent agent* routes to a business-domain "workspace", a *table agent* proposes tables and **lets the user confirm**, and a *column-prune agent* trims context. It is evaluated on intent accuracy, table overlap, execution success and similarity to golden SQL. Hallucinated tables never fully disappeared, so validation stays mandatory.
- **LinkedIn SQL Bot** (arXiv 2507.14372) built a knowledge graph from metadata, **historical query logs**, wikis and code, and clustered tables per team. Its agent retrieves, ranks, writes, then **self-corrects**. Query *debugging* became one of its most valued features.
- **Verified queries.** Snowflake Cortex Analyst lets a semantic model carry vetted question → SQL pairs that the agent prefers over writing new SQL. Our parameterised templates (section 5.5) apply the same idea.
- **Standards are converging but young:**
  - **Open Semantic Interchange (OSI)** is now **Apache Ossie** (Apache-2.0). It is a YAML spec with `datasets`, `fields` (per-dialect `expression`s), `relationships`, `metrics`, an `ai_context` block (`instructions`, `synonyms`, `examples`) and `custom_extensions`. Version **0.2.0.dev0**, still mutable.
  - **Open Data Contract Standard (ODCS v3.1, Linux Foundation / Bitol)** defines per-dataset contracts with predefined quality rules (`nullValues`, `missingValues`, `invalidValues`, `duplicateValues`, `rowCount`).
  - **dbt MetricFlow** (Apache-2.0 since Oct 2025) compiles metric requests to SQL deterministically. It is only relevant if the warehouse is dbt-modelled.
- **Cloud vendor tooling.** BigQuery Conversational Analytics accepts business glossary terms (importable from Dataplex Universal Catalog). The open-source **MCP Toolbox for Databases** gives agents metadata and SQL tools. If adopted later, our repo should feed these tools rather than compete with them.
- **GitHub Copilot** reads **agent skills** (`SKILL.md` folders) from `.github/skills/` (also `.claude/skills/`, `.agents/skills/`) in VS Code, Visual Studio, the CLI and the cloud agent. It also reads **custom agents** (`.github/agents/<name>.agent.md`) that pin tools and MCP servers. The `SKILL.md` format is shared with other agents, so the skills stay portable.

## 3. Design principles

1. **Definitions are the product.** Invest there first; the agent is a consumer.
2. **One meaning, one id.** A business word with several meanings becomes several definitions sharing a synonym, never one definition with variants. Ambiguity detection follows directly: a term matching more than one certified id means the agent must ask.
3. **Parse deterministically, interpret with the LLM.** Table and join structure comes from a SQL parser and the catalog. The model handles meaning, questions and wording.
4. **Certified first, generated second.** A matching certified template beats newly written SQL. New SQL is always labelled as uncertified.
5. **Answers must earn trust.** Every answer carries provenance (definition, owner, version, health). Headline numbers reconcile against figures the organisation already trusts. "I don't know" beats a confident wrong number.
6. **Phase 1 writes the full data, Phase 2 adds readers.** The schema is complete from day one: bindings for both platforms, quality checks, review dates, harvest provenance, official anchors, template parameters. Phase 2 features only read data that already exists, so pausing after Phase 1 costs nothing and resuming needs no migration.
7. **Generated views, one source.** YAML is the only thing anyone edits. Index, docs, navigator and agent views are all build outputs.
8. **Align with standards without adopting a 0.x spec.** Use OSI field names where an equivalent exists and borrow ODCS quality-rule names; a converter can export later.

## 4. Architecture

```
            ┌──────────────────────── semantic-layer repo (git) ─────────────────────────┐
            │                                                                             │
 existing   │  catalog/  (GENERATED, never hand-edited)    domains/<team>/  (CURATED)      │
 metadata ──┼─▶ cloud/*.yaml  legacy/*.yaml              entities/ metrics/ dimensions/  │
 repo       │   migration_map.yaml (legacy ⇄ cloud)        filters/ relationships/         │
            │                                              templates/ anchors/             │
            │  build/ (GENERATED: index, model.json,      evals/golden_questions.yaml      │
            │          docs/*.md, navigator.html)                                          │
            │  schema/*.json  sl/ (toolkit)  CODEOWNERS  .github/{skills,agents,workflows}   │
            └──────────────┬──────────────────────────────────────┬───────────────────────┘
                           │ read via the sl CLI                  │ PRs (owners approve)
                ┌──────────▼─────────────────┐          ┌─────────▼──────────┐
                │ Skills: ask · find ·        │          │ CI on every PR      │
                │ harvest · contribute ·      │          │ schema · refs ·     │
                │ steward                     │          │ bindings · evals    │
                └──────────┬─────────────────┘          └────────────────────┘
                           │ dry-run / metadata / (if permitted) execute
                    ┌──────▼────────┐       ┌────────────────────────────────┐
                    │ cloud DW       │       │ Scheduled checks               │
                    │ legacy DW      │◀──────│ P1: drift + review expiry      │
                    └───────────────┘       │ P2: freshness, quality, recon, │
                                            │     template re-runs, evals    │
                                            └────────────────────────────────┘
```

| Layer | Content | Written by | Changes |
|---|---|---|---|
| **Physical catalog** | Tables, columns, types, partitions on both platforms; legacy → cloud copy map | Generator from the existing metadata repo | Automatically |
| **Semantic layer** | Entities, metrics, dimensions, filters, relationships, templates, anchors | Domain owners, via PR | Slowly, reviewed |
| **Agent layer** | Skills, shared prompt fragments, eval set | Core team | Guarded by the eval set |

## 5. The definition schema [P1, complete from day one]

One file per definition with a stable `id`. Kinds: `entity`, `metric`, `dimension`, `filter`, `relationship`, `template`, `anchor`. Field names follow OSI where an equivalent exists; governance fields are ours. Everything below is illustrative; table and column names are invented.

### 5.1 Entity

```yaml
id: customer.active_buyer
kind: entity
name: Active customer (bought in the last 12 months)
synonyms: [customer, client, active customer, buyer]
description: >
  Person or company with at least one completed purchase in the 12 months
  before the snapshot date.
not_to_be_confused_with:
  - id: customer.any_account
    difference: every registered account, including ones that never bought
  - id: customer.loyalty_member
    difference: enrolled in the loyalty programme, regardless of purchases
grain: one row per customer per month-end snapshot
owner:
  team: core-analytics          # matches the CODEOWNERS entry for this folder
  steward: jane.doe             # the person to ask when it breaks
status: certified               # draft | reviewed | certified | deprecated
version: 3
last_reviewed: 2026-09-15
review_by: 2027-03-15           # scheduled check opens an issue when this passes
bindings:
  - platform: cloud
    preferred: true
    source: analytics.customer_monthly
    key: customer_id
    time_column: snapshot_date
    filter: "last_purchase_date >= DATE_SUB(snapshot_date, INTERVAL 12 MONTH)"
  - platform: legacy
    status: legacy              # valid until migration sign-off
    source: DWH.CUSTOMER_MONTHLY
    key: CUSTOMER_ID
    time_column: SNAPSHOT_DATE
    filter: "LAST_PURCHASE_DATE >= ADD_MONTHS(SNAPSHOT_DATE, -12)"
checks:                         # names borrowed from ODCS; read by P2 checks
  - {type: nullValues, column: customer_id, must_be: 0}
  - {type: duplicateValues, columns: [customer_id, snapshot_date], must_be: 0}
  - {type: rowCount, per: snapshot_date, between: [MIN, MAX]}   # owner fills the band
  - {type: freshness, column: snapshot_date, max_lag_days: 35}
  - {type: reconcile, platforms: [cloud, legacy], measure: "COUNT(DISTINCT key)", tolerance_pct: 0.1}
anchors: [anchor.monthly_report.active_customers]
provenance:                     # how it was learnt: harvest | capture | contribute
  method: harvest
  from: queries/pilot_customer_report.sql
  lines: [12, 31]
  confirmed_by: jane.doe
  confirmed_on: 2026-10-03
  open_questions: []
ai_context:
  instructions: >
    If the user says "customer" with no qualifier, ask which of the three
    customer definitions they mean, showing each one's difference in one line.
  examples: ["how many customers do we have", "active customers by region"]
```

### 5.2 Metric

```yaml
id: metric.customer_count
kind: metric
name: Number of customers
expression: "COUNT(DISTINCT {entity.key})"
applies_to: [customer.active_buyer, customer.any_account, customer.loyalty_member]
additivity: non_additive_over_time   # snapshot count: never SUM across months
owner: {team: core-analytics, steward: jane.doe}
status: certified
version: 1
last_reviewed: 2026-09-15
review_by: 2027-03-15
```

### 5.3 Dimension and relationship (where join knowledge lives)

```yaml
id: dimension.home_store
kind: dimension
name: Home store
synonyms: [store, shop, location, home store]
not_to_be_confused_with:
  - id: dimension.first_purchase_store
    difference: store where the customer made their first purchase
bindings:
  - {platform: cloud, source: analytics.store, key: store_id, label_column: store_name}
hierarchy: [store, district, region]   # enables "by region" roll-ups
```

```yaml
id: rel.customer_monthly__home_store
kind: relationship
from: customer.active_buyer
to: dimension.home_store
join: "{from}.home_store_id = {to}.store_id"
cardinality: many_to_one              # confirmed by harvest probe where available
safe_for_metrics: [metric.customer_count]
caveats: "Snapshot table: join on the same snapshot_date or store moves are double counted."
```

Generated SQL may only use joins declared as relationships.

### 5.4 Anchor: a figure the organisation already trusts

```yaml
id: anchor.monthly_report.active_customers
kind: anchor
name: Active customers, monthly management report
published_in: "Monthly management report, page 'Customer base'"
owner: {team: reporting, steward: john.roe}
values:                               # appended monthly, by hand or by a loader
  - {period: 2026-08, value: PUBLISHED_VALUE}   # the real published figure
measures: {metric: metric.customer_count, entity: customer.active_buyer}
tolerance_pct: 0.5
```

The ask skill reconciles matching answers against anchors ("matches the monthly report for 2026-08 within 0.2%"). In Phase 2 the scheduled checks do the same, so a moving official figure raises an alert.

### 5.5 Template: a parameterised verified query

```yaml
id: tpl.customers_by_dimension_and_join_month
kind: template
question_pattern: "{entity} customers in {region}, by {dimension} and month they joined"
parameters:
  period_end: {type: period}        # the reference implementation supports period parameters
  # value / one_of parameters (entity, region, dimension choice) are a natural extension
uses: [customer.active_buyer@3, metric.customer_count@1, dimension.home_store@2, dimension.join_month@1]
sql: {cloud: customers_by_dimension_and_join_month.cloud.sql}   # {{period_end}} placeholders
expected:
  executes: true
  reconciles_with: anchor.monthly_report.active_customers
verified_by: jane.doe
verified_on: 2026-10-03
expires: 2027-04-03
```

The `@version` pins make staleness mechanical. When a used definition changes version, CI marks the template **needs re-verification** and the agent stops treating it as certified until someone re-signs it. Templates are demoted when they go stale, never deleted.

### 5.6 Status lifecycle

`draft` → `reviewed` (a second person checked it) → `certified` (owner signed, checks pass) → `deprecated` (kept, with `replaced_by`). The agent may use `draft`/`reviewed` items only with an explicit **uncertified** warning.

## 6. Ownership and contribution [P1]

- **One folder per owning team** under `domains/`, with a `CODEOWNERS` line per folder, so GitHub requires that team's approval for changes. Shared dimensions (time, geography) live in `shared/` with a named owner.
- **Inner source:** anyone may open a PR anywhere; only owners approve.
- **PR template checklist:** unique id; owner and steward; a binding that resolves in the catalog; at least one check; `review_by`; `not_to_be_confused_with` where synonyms overlap; a golden question for a new concept.
- **History is git.** Who added what and why lives in commit and PR history; the find skill reads `git log` to answer "what changed?".

## 7. The skills

Skills live in `.github/skills/`, each a `SKILL.md` with a sharp `description` for routing. Shared steps (loading the index, citation format, uncertified warning, capture offer) live in one `_shared/` file referenced by every skill. An optional `data-analyst.agent.md` custom agent pins the tools. All skills read the layer through the `sl` CLI (section 9).

### 7.1 `semantic-harvest` — work backwards from working SQL [P1 single query · P2 bulk]

This skill is the main way content enters the layer. Start from SQL people already trust, not from a blank "what is a customer?" interview.

1. **Static parse** [P1] with [`sqlglot`](https://github.com/tobymao/sqlglot) (open-source Python parser; supports the `oracle` and `bigquery` dialects among many; plus column lineage). `sl harvest analyse <file> --platform <p> --session <id>` emits JSON with:
   - tables, aliases and CTEs, with each CTE's grain;
   - joins: both sides, keys, join type (`LEFT` vs `INNER` says whether the right side is optional);
   - filters, with **literal values** separated out (`STATUS = 'A'`, `TYPE IN (3, 7)`, date windows);
   - aggregations and `GROUP BY` columns (candidate metrics and dimensions);
   - output columns with lineage back to source columns.
2. **Cross-reference** [P1] against `catalog/` (does the table exist, on which platform, is there a cloud copy of this legacy table) and against existing definitions:
   - an expression matching a certified definition → reuse it, no question needed;
   - a filter on the same table that **differs** from a certified definition → ask *"Your query counts customers with `STATUS IN ('A','P')`; the certified `customer.active_buyer` uses `STATUS = 'A'`. A different concept, or should it use the certified one?"* The answer is either a new definition or a bug in the query.
3. **Probes** [P1 if execution is permitted, else P2] — aggregate-only, never rows:
   - distinct values with counts for each filtered code column, so the interview shows `'A' 81%, 'P' 6%, 'B' 13%` instead of asking blind;
   - join cardinality: key uniqueness per side and row counts before/after each join;
   - null rates on join keys.
4. **Interview** [P1] in batches of 3–5 questions, highest value first. Where possible **propose a guess and ask for confirmation**. Question bank:
   - What business question does this answer, and who uses the result? (→ template `question_pattern`, golden question)
   - For each filter literal: what does the value mean, and why is it included or excluded? *(Undocumented knowledge sits in these values.)*
   - For each CTE or subquery: is this a named business concept? (→ entity or filter)
   - For each join: why `LEFT` and not `INNER`? What does a missing right side mean?
   - What is one row of the output? (→ grain)
   - Which published figure should this match? (→ anchor)
   - What breaks this query, or what have people got wrong before? (→ `caveats`)
   - Who owns each source table? (→ owner, steward)
   - Which parts would people want to change? (→ template parameters)
5. **Output as a PR** [P1], everything `status: draft`, each item carrying its `provenance:` block (`method: harvest`). Contents: entities, dimensions, filters, metrics; one relationship per confirmed join; the original query as a **template** with its known-good result in `expected`; one or more anchors; golden questions; and the open questions for the reviewer.
6. **Bulk mode** [P2]: run step 1 over folders of scripts, or over query history if accessible (BigQuery: `INFORMATION_SCHEMA.JOBS_BY_PROJECT` holds executed query text, *verify* permission and retention). Aggregate:
   - most-used tables, and catalog tables nobody queries;
   - most frequent join pairs and keys → **candidate relationships ranked by usage**;
   - recurring filter literals per table → candidate filters and entity variants;
   - **disagreement report**: queries computing the same concept with different filters, and how far their results differ.

### 7.2 `semantic-ask` — business question to answer [P1 core · P2 execution and checks]

1. **Parse into an analysis spec**: measures, population, dimensions, filters, time grain and range. Echo it back briefly.
2. **Resolve terms** with `sl search` over names, synonyms and descriptions. One certified match → use it. Several → **ask**, listing each candidate's one-line difference and owner, all ambiguities in one multiple-choice message. None → say so, offer an uncertified fallback from the raw catalog, and offer to start a harvest or contribute draft.
3. **Template first.** If a certified template matches the resolved spec, fill its parameters and use it. This gives the fastest and most reliable path.
4. **Otherwise plan**: platform (`preferred` binding unless only legacy has it); join path **only from declared relationships**; grain and fan-out check; additivity check (no summing snapshot counts).
5. **Generate SQL from certified fragments** (binding `source`/`filter`/`key`, metric `expression`), with comments citing each definition id and version. Label the result **new SQL, uncertified**.
6. **Validate:**
   - [P1] syntax and cost: BigQuery dry run (`bq query --dry_run --use_legacy_sql=false`) validates and estimates bytes; Oracle `EXPLAIN PLAN FOR …`;
   - [P1] lint: every certified filter of the cited definitions appears; no undeclared joins; partition filter present; no `SELECT *`;
   - [P1 if execution is permitted, else P2] run aggregate results, reconcile with anchors, check for null keys in breakdowns and plausible row counts;
   - self-correct once or twice, then report the failure instead of looping.
7. **Answer** with: the SQL (or the result table, if executed); a **provenance card** (each definition → owner, steward, status, version, last reviewed, health, link to its docs page); the anchor reconciliation line where one applies; a short **manual validation plan** (2–4 concrete spot checks); caveats (uncertified items, legacy bindings, freshness).
8. **Capture in the session** [P1]. When the user resolves an ambiguity the layer did not cover, corrects the SQL, or says a result is wrong, offer: *"Save this as a draft definition for your team?"* / *"Open an issue for the owner of X?"* Accepted offers become a branch and PR (draft status) or an issue assigned to the steward. This is how the layer grows from normal use.

**Worked example.** *"Total customers in the region, by store and month they joined"* holds three ambiguities: **customer** (active buyer / any account / loyalty member), **store** (home store / first-purchase store), **month they joined** (account created / first purchase). A good first reply asks all three at once, as multiple choice.

### 7.3 `semantic-find` — where is X, who owns X, what changed [P1]

Lookups without SQL: the definition, its bindings on each platform, its legacy → cloud copy status, owner and steward, health, templates and anchors that use it, and `git log` for "what changed and when". It always names the steward.

### 7.4 `semantic-contribute` — add a definition by hand [P1, light]

An interview over the schema fields for concepts no query shows. It proposes bindings from `catalog/`, suggests checks, drafts `not_to_be_confused_with` notes for overlapping synonyms, adds a golden question and opens a PR. It never sets `certified`.

### 7.5 `semantic-steward` — maintenance [P1 minimal · P2 full]

A scheduled deterministic job (section 8) does the checking; the skill reads its report, groups failures by owner, explains likely causes (renamed column, late load, migration cut-over) and drafts the fix PR or issue text.

### 7.6 Retrieval: start simple [P1]

Up to roughly a thousand definitions, a compact generated index (id, kind, name, synonyms, one-line description, owner, status per line) can be loaded whole, followed by reading only the selected files. Partition by domain when it grows. Add embedding search [P2] only when the index stops fitting or the eval set shows synonym misses. The threshold is a rule of thumb; check it against the evals.

## 8. Validation and scheduled checks

### 8.1 CI on every PR [P1] — deterministic, no LLM

`sl validate` fails the PR when:
1. a file does not match its JSON Schema (`schema/<kind>.json`);
2. an `id` is duplicated, or a reference (`applies_to`, `from`, `to`, `uses`, `anchors`, `replaced_by`) points nowhere;
3. a binding's table or column is missing from the catalog snapshot;
4. a certified item lacks owner, steward, checks or `review_by`;
5. two certified definitions share a synonym without a `not_to_be_confused_with` link;
6. a template pins an older version than current (warn, mark for re-verification).

Then `sl build` regenerates `build/` and the eval set runs (section 10).

### 8.2 Scheduled checks

Run as a scheduled job, on a self-hosted runner or a cloud scheduler, wherever there is read access to the databases.

| Check | How | Phase |
|---|---|---|
| Schema drift | Compare bindings to live metadata (BigQuery `INFORMATION_SCHEMA.COLUMNS`, Oracle `ALL_TAB_COLUMNS`) | **P1** |
| Review expiry | `review_by` / `expires` in the past → issue to steward | **P1** |
| Catalog refresh | Regenerate `catalog/`, diff, PR | **P1** |
| Freshness | `max(time_column)` vs `max_lag_days` | P2 |
| Quality checks | Each definition's `checks`, aggregate-only | P2 |
| Legacy ⇄ cloud reconciliation | Same aggregate on both bindings | P2 |
| Anchor reconciliation | Template results vs published figures | P2 |
| Template re-runs | Execute, test `expected`, demote on failure | P2 |
| Agent evals | Golden question set | P2 (P1 runs in CI) |

Outputs: one grouped issue per owner per run and `build/health.json` for the navigator and the agent. Checks return **aggregates only**, so no row-level data reaches reports, issues or prompts.

## 9. Navigator and agent access: one build, two audiences

```
domains/**/*.yaml + catalog/ + health.json
              │
         sl build         (CI on every merge, and after each scheduled run)
              │
   ┌──────────┼──────────────────┬───────────────────────┐
   ▼          ▼                  ▼                       ▼
model.json   index              docs/*.md               navigator.html
(agents)     (agent context)    (GitHub browsing) [P1]  (static, searchable) [P2]
```

- **`build/model.json`** [P1]: the whole layer with references resolved both ways (uses / used by), owner, status, health, catalog facts.
- **the `sl` CLI** (`python -m semantic_layer`) [P1]: one CLI over the layer for people and agents alike (`sl search`, `sl show <id>`, `sl owners --team <t>`, `sl table <name>`, `sl lineage <id>`, `sl health --overdue`). Skills call it instead of walking files.
- **Generated markdown docs** [P1]: one page per team, per definition and per table, plus a Mermaid relationship diagram per domain, which GitHub renders natively. This needs no infrastructure and is enough for Phase 1.
- **`navigator.html`** [P2]: one self-contained static file with `model.json` embedded and client-side search, with views for teams, concepts, tables, relationships, and health and coverage. It needs no server or database. Host it as a CI artifact, on private GitHub Pages *(verify)*, or behind the organisation's login.
- **MCP server** [P2, only if needed]: wraps the same `sl` functions for clients that cannot run a CLI.
- **Do not build a web app with its own database.** If the organisation already runs a data catalog (Dataplex, DataHub, OpenMetadata, Collibra), export into it instead; DataHub, for example, already ingests ODCS contracts.

A few thousand definitions make a `model.json` of a few megabytes, which a static page searches comfortably.

## 10. Evaluation and success metrics

`evals/golden_questions.yaml` holds real questions collected from users before and during the pilot:

```yaml
- question: "active customers by home store at the end of August"
  expect_definitions: [customer.active_buyer, metric.customer_count, dimension.home_store]
  expect_template: tpl.customers_by_dimension_and_join_month
  expect_clarification: false
  expect_result: {reconciles_with: anchor.monthly_report.active_customers}
- question: "how many customers per store"
  expect_clarification: true          # must ask: which customer, which store
```

**Agent metrics** (CI, on every PR touching definitions or skills; a drop blocks the merge): resolution accuracy, clarification precision and recall, template hit rate, execution success, result match.

**Adoption metrics** (the pilot's scorecard):
- **Baseline before launch**: ad-hoc data requests per week and average time to answer, for each pilot team.
- Weekly active users of the skills.
- Time from question to validated answer.
- **Definitions and templates contributed by people outside the core team without being asked.** This is the real stickiness signal: usage can be curiosity, unprompted contribution is adoption.
- Complaints fixed, and days from complaint to fix.

## 11. Phase 1 — the quick win that sticks

**Goal:** two teams answering real questions from a small certified layer that grew out of their own working queries, with numbers that reconcile to published figures and a feedback loop that keeps growing the layer. **Scope:** the core team first, then one more team. Each team brings **one working query** it built and validated recently. That query is harvested in an interview session, and everything else grows from there.

### Step 1 — Discovery (days, not weeks)
- Inspect the existing metadata repo: format, generation, frequency, coverage of both platforms, any legacy ⇄ cloud mapping.
- Establish what the agent can reach in this environment: dry run, query execution, metadata reads, permitted MCP servers *(verify each)*. This decides which [P1-if-permitted] items are in.
- Settle **who the users are**. If they are analysts who write SQL in an IDE, Copilot skills are the right front end. If they are business users, the pilot users are the analysts who serve them, and a business-facing front end is a Phase 2 question.
- Record the adoption baseline for both teams.
- Start the request for read access from a scheduled job now; approvals are usually the slowest step.

### Step 2 — Skeleton
- Repo layout; JSON Schemas for **all** kinds (complete, per principle 6); `sl validate`; `sl build` (index, `model.json`, markdown docs); the `sl` lookups; catalog generator; CODEOWNERS; PR template; golden question file.

### Step 3 — Core team: harvest the pilot query
- `harvest_parse.py` and the `semantic-harvest` skill (steps 1–5).
- One interview session (60–90 min) with the query's authors. Expected yield: 5–15 definitions, the relationships behind every join, the query as a certified template, at least one anchor, 5–10 golden questions.
- Owners review and certify via PR.

### Step 4 — Core team: use it daily
- `semantic-find` and `semantic-ask` with: template-first matching, the provenance card, anchor reconciliation, the uncertified warning, and **in-session capture**.
- The core team routes its own ad-hoc questions through the skill for 2–3 weeks. Every gap becomes a captured draft or a new harvest. Iterate on the golden set.
- Minimal scheduled checks: schema drift, review expiry, catalog refresh.

### Step 5 — Second team
- The second team harvests its own working query **using only the docs and the harvest skill**, with the core team watching but not driving. Whatever confuses them gets fixed.
- **Two-team disagreement check**: wherever the two teams' queries compute the same concept, compare filters and results. Any difference found becomes either a new distinct definition or a correction, and is the pilot's strongest result to show stakeholders.

### Exit criteria (agree targets up front)
- Golden-set resolution accuracy and clarification behaviour at the agreed bar.
- Both teams using it weekly; measured time-to-answer gain against the baseline.
- At least one definition or template contributed by the second team without being asked.
- Every headline number in the pilot reconciles with an anchor.

### Deliberately not in Phase 1
Navigator HTML, MCP server, embedding search, bulk harvest over query history, the full check suite, standards export, more teams.

## 12. Phase 2 — build later, from data that already exists

Each module below reads data that Phase 1 already captures, so any of them can start, pause or be skipped independently with no schema migration. Each lists its trigger.

| Module | Reads (already captured in P1) | Start when |
|---|---|---|
| **Bulk harvest + usage ranking** | Query folders / query history; catalog | A third team joins, or you need to prioritise what to define next |
| **Full disagreement report** | Bulk harvest output; definitions | You need the case for org-wide ownership |
| **Full scheduled checks** (freshness, quality, legacy ⇄ cloud reconciliation, template re-runs, anchor checks) | `checks`, bindings on both platforms, templates' `expected`, anchors | The layer holds enough certified items that silent breakage becomes likely |
| **Agent executes queries** | Bindings, anchors | Execution permission is granted |
| **Navigator HTML + health dashboard** | `model.json`, `health.json` | More than one or two teams browse the layer |
| **Embedding search** | Index | Index no longer fits, or evals show synonym misses |
| **MCP server** | the `sl` functions | A client cannot run the CLI |
| **Business-user front end** | The same skills and `model.json` | Phase 1 shows analysts trust it and business users ask for direct access |
| **Standards export / catalog sync** (OSI/Ossie, ODCS, Dataplex, DataHub) | Definitions (OSI-aligned names) | Another tool needs to consume the definitions |
| **Deterministic metric compiler** | Metrics, relationships | Evals show recurring join or grain errors that templates do not cover |
| **Org-wide rollout** | Everything above | Phase 1 exit criteria met |

## 13. Risks and trade-offs

- **People, not code, decide whether this lasts.** Definitions rot without owners who have a reason to keep them current. Countermeasures: review dates, issues assigned by name, visible health, a sponsor who makes ownership part of the job (for example 30 minutes a week on the weekly issue), and quick, visible fixes for every complaint.
- **One wrong number can end the pilot.** Hence template-first answers, anchor reconciliation, provenance on every answer, and uncertified warnings.
- **LLM-written SQL vs a deterministic compiler.** Templates plus certified fragments plus lint give most of the reliability with no new infrastructure. A compiler is a Phase 2 option.
- **Standards churn.** OSI/Ossie is pre-1.0; align names, do not adopt it as storage.
- **Access and governance.** Scheduled read access and agent execution are separate approvals; use the sanctioned route (service account, scheduled job inside the cloud project), never a personal machine.
- **Data protection.** The agent sees metadata, SQL and aggregates. Rows and personal data never enter prompts, issues or reports.
- **Migration.** Two bindings per definition while tables move; reconciliation catches divergence; mark the legacy binding `deprecated` after sign-off rather than deleting it.

## 14. Reference implementation in this repository

`semantic_layer/` implements Phase 1 fully and the Phase 2 modules marked below, on mock data (a fictitious retail domain in two DuckDB files standing in for the legacy and cloud warehouses). Command names are `python -m semantic_layer <command>`, shortened to `sl` here.

| Plan section | Implementation | Phase |
|---|---|---|
| 4 Architecture: physical catalog | `sl catalog generate` → `catalog/` (`sl/catalog.py`; `collect()` is the porting seam) | P1 |
| 4 Platforms (legacy / cloud) | `sl/platforms.py`: `MockDuckDBPlatform` (sqlglot transpile), `BigQueryPlatform`, `OraclePlatform` behind one protocol; `config.json` selects | P1 |
| 5 Definition schema | `schema/*.schema.json` (all seven kinds, complete from day one), loaded by `sl/model.py` | P1 |
| 6 Ownership | `domains/<team>/` + `CODEOWNERS.example`; validate warns when `owner.team` ≠ folder | P1 |
| 7.1 Harvest (single query) | `sl harvest analyse` / `sl harvest draft` (`sl/harvest.py`); two recorded mock sessions under `harvest/sessions/` | P1 |
| 7.1.6 Bulk harvest | `sl harvest bulk --history \| --folder` (`sl/phase2/bulk.py`); mock `ops.query_history` | P2 |
| 7.2 Ask | `sl ask resolve` / `sl ask run` (`sl/ask.py`): template-first, compose, lint, dry run, execute, anchor reconcile, provenance card, validation plan | P1 |
| 7.2.8 Capture | `sl capture definition` / `sl capture issue` (`sl/capture.py`) | P1 |
| 7.3 Find | `sl search · show · owners · table · lineage · health` (`sl/find.py`) | P1 |
| 7.4 Contribute | `.github/skills/semantic-contribute/` over the schemas + `sl validate` | P1 |
| 7.5 Steward | `.github/skills/semantic-steward/` over `sl checks run` | P1 |
| 8.1 CI | `sl validate` (`sl/validate.py`) + `sl build --check` + `sl eval`; `.github/workflows/semantic-layer.yml`; `scripts/verify-before-ship.ps1` | P1 |
| 8.2 Scheduled checks (drift, expiry, catalog refresh) | `sl checks run` (`sl/checks.py`) → `build/health.json`, `build/issues/<team>.md`; exit 3 = unknown | P1 |
| 8.2 Full checks | `sl checks run --full` (`sl/phase2/checks_full.py`) | P2 |
| 9 Model + docs | `sl build` (`sl/build.py`) → `build/index.jsonl`, `build/model.json`, `build/docs/` | P1 |
| 9 Navigator | `sl build --navigator` (`sl/phase2/navigator.py`) | P2 |
| 10 Evaluation | `evals/golden_questions.yaml` (15 questions) + `sl eval` (`sl/evals.py`) | P1 |
| 11 Step 5 Two-team disagreement | `sl disagree` (`sl/disagree.py`): mock result 446 vs 398 | P1 |
| 12 Full disagreement | `sl disagree --all` | P2 |
| 12 Standards export | `sl export osi` (`sl/phase2/export_osi.py`, experimental) | P2 |
| 12 MCP server, embeddings, business front end, deterministic compiler | not built; each documented with its trigger | — |

Decisions taken while building it, and why:

- **Provenance is one block with a `method`** (`harvest | capture | contribute`) rather than a `harvest:` block. All three ways a definition is learnt need the same fields.
- **Cross-platform bindings from a harvest are *proposals***: transpiled with sqlglot, mapped through the migration map, dry-run, and marked `status: proposed` with a review note. In the mock, sqlglot kept `ADD_MONTHS` (not a BigQuery function), compared a BOOLEAN to `0`, and produced invalid Oracle from `DATE_SUB`. DuckDB accepted all three. The committed reviews fixed them by hand, which is why a person reviews every cross-platform binding.
- **Health is a separate state, never a YAML rewrite.** Checks write `build/health.json` (`ok · warning · failing · unknown`). A stale or failing template is *demoted* there, and the agent stops treating it as certified, but no check edits curated definitions.
- **`unknown` has its own exit code (3)** so a scheduler cannot mistake "could not check" for "healthy".
- **Template parameters:** only `period` parameters are implemented. Value and choice parameters are a straightforward extension of `match_template` in `sl/ask.py`.
- **Disagreement counts are taken at one period per platform**, and variants that differ only in literal values (`region = 'North'` vs `'South'`) are treated as parameters, not disagreements.

## 15. Sources

- Uber QueryGPT: https://www.uber.com/blog/query-gpt
- LinkedIn, *Text-to-SQL for Enterprise Data Analytics* (arXiv 2507.14372): https://arxiv.org/abs/2507.14372
- Open Semantic Interchange / Apache Ossie spec: https://github.com/open-semantic-interchange/OSI
- OSI announcement: https://www.snowflake.com/en/blog/open-semantic-interchanges-specs-finalized/
- ODCS v3.1.0: https://bitol.io/bitol-announces-odcs-v3-1-0-stronger-smarter-and-stricter/
- sqlglot (parser, dialects, lineage): https://github.com/tobymao/sqlglot
- DataHub ODCS ingestion: https://docs.datahub.com/docs/generated/ingestion/sources/odcs
- MetricFlow open-sourced (Apache 2.0): https://www.getdbt.com/blog/open-source-metricflow-governed-metrics
- BigQuery conversational analytics: https://docs.cloud.google.com/bigquery/docs/conversational-analytics
- BigQuery tools for ADK and MCP: https://cloud.google.com/blog/products/ai-machine-learning/bigquery-meets-google-adk-and-mcp
- Copilot agent skills: https://learn.microsoft.com/visualstudio/ide/copilot-agent-skills
- Copilot custom agents: https://docs.github.com/copilot/concepts/agents/coding-agent/about-custom-agents
- Enterprise text-to-SQL and semantic layers: https://atlan.com/know/ai-agent/data-for-ai/text-to-sql-for-enterprise/
