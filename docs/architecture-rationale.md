# Architecture Rationale

## The Product Is the Orchestrator

The core choice is to make Oracle to GCP a deterministic converter pipeline, not a runtime agent. The LLM is useful for one bounded function: translating a single Oracle SQL statement into BigQuery Standard SQL. It is not allowed to decide the pipeline order, invent mappings, skip validation, or retry indefinitely.

That shape matters because the intended user is not one developer hand-holding one migration. The intended user is someone who may run many scripts and needs reproducible, inspectable results. Two runs over the same inputs should follow the same stages, produce the same logs, and stop at the same unresolved decisions.

## Why a Local Mock First

The brief calls for live Oracle and BigQuery execution, but that is not available in a portable demo. SQLite gives us a cheap stand-in that still exercises the important mechanics:

- two separate database files, one representing Oracle and one representing BigQuery;
- a seeded source-data parity check;
- execution of ordered SQL units;
- intermediate scratch table creation;
- fingerprint comparison over real query results;
- generated artifacts a reviewer can inspect.

SQLite is not pretending to be fully compatible with either Oracle or BigQuery. It is a demonstration harness for the orchestration contract. The code isolates dialect adaptation in `src/sql_processing.py` and translation in `src/translator.py` so real adapters can replace the mock pieces later.

## Why the LLM Is Stateless

The local LLM hub is called as a stateless request: given one Oracle statement and the mapping registry, return one BigQuery statement. The orchestrator owns everything around it:

- the mapping registry;
- variable materialization;
- source and target extraction;
- row-count pre-flight;
- execution order;
- validation;
- bounded repair attempts;
- reporting.

This avoids the failure mode where an agent “does its thing” differently each run. The hub can improve translation quality, but it cannot change the process.

## Why There Is a Deterministic Fallback

The mock must run on a fresh machine even when the hub is down or a model responds with non-executable SQL. The fallback translator implements the small dialect subset used by the demo: table remapping, `NVL` to `COALESCE`, `TRUNC(date)` to `DATE(...)`, and `CREATE TABLE AS` to `CREATE OR REPLACE TABLE AS`.

This is not meant to replace a model for real migrations. It keeps the showcase honest: the UI and tests prove the pipeline mechanics without requiring cloud credentials or a live model.

## Why Repair Is Bounded

Unbounded repair loops hide uncertainty and burn time. This prototype retries a failing unit up to a fixed limit, then flags it for human review with full context.

The demo can intentionally emit a bad first translation for the `CREATE TABLE AS` unit. Validation catches the mismatch, one repair attempt reruns translation, and the corrected SQL passes. That shows the repair path without relying on a real model failure.

## Validation Tradeoffs

The mock uses cheap statistical fingerprints:

- row count;
- numeric sums;
- grouped count and sums.

These checks are fast and useful, but they are not perfect. Two result sets can share counts and sums while row-level values differ. A production version should add optional deep checks, such as sorted row hashes, for high-stakes statements or mismatched fingerprints.

The important design choice is that translation is not trusted until execution validates it.

## Why Mapping Is Explicit

The source-to-target registry is a first-class input. The converter halts on unmapped source tables instead of guessing dataset names.

That is deliberate. Table mapping is domain knowledge, not a formatting problem. Guessing a wrong target table is more dangerous than asking the user to add a mapping.

## What Changes for Production

A production version keeps the same orchestration stages but swaps the adapters:

- Oracle adapter: `python-oracledb`, live execution, Oracle metadata checks.
- BigQuery adapter: `google-cloud-bigquery`, scratch dataset management, Standard SQL jobs.
- LLM adapter: local hub, Vertex/Gemini, or another provider behind the same `translate(statement, mapping)` contract.
- Splitter: a stronger PL/SQL-aware tokenizer or grammar for complex scripts.
- Persistence: SQLite run history and mapping registry, with UI editing and audit logs.

The philosophy should not change: deterministic stages, explicit mappings, bounded repair, and validation before trust.
