# Table-mapping state

A table mapping's readiness is a declared state machine. `src/table_registry.py` owns it: the `MappingState` type, the `MappingReason` enum, and the one pure function `derive_mapping_state(mapping)` that maps a stored row to a state. No other code compares a mapping's `status` string to `"ready"` or `"blocked"`; preflight, the pipeline and the registry view read the derived state. `TableMapping.ready` is `derive_mapping_state(self).is_ready`.

## Why it is declared

A mapping's mode lives in several stored facts: a free-text `status` column, two reachability booleans and whether `bigquery_table` is empty. Read separately they disagree (a seeded row that says `ready` before any probe, a `ready` row with no BigQuery table, a deliberate `blocked` lost to the next probe, a reachable flag that is never re-probed). One derive function turns each of those into a named state.

## States

| State | Meaning |
|---|---|
| `unmapped` | No BigQuery table is named. |
| `unchecked` | Mapped, but at least one side has never been probed. |
| `ready` | Mapped, not held, and both sides last probed reachable. The only state that may run. |
| `blocked` | Cannot run. Carries one or more reasons: `held`, `oracle_unreachable`, `bigquery_unreachable`. |
| `unknown` | A probe could not be run, so reachability is not established. Carries `oracle_probe_failed` and/or `bigquery_probe_failed`. Never folded into `blocked`. |

Precedence: `unmapped` first (a hold on a row with no BigQuery table applies once it is mapped), then any `blocked` reason, then `unknown`, then `unchecked`, then `ready`. A known block outranks an unknown probe on the other side.

## Stored facts

The SQLite schema and CSV columns are unchanged. `status` is the stored operator intent: `blocked` is a hold that only the operator clears, while `pending` and `ready` both mean "no hold". A side counts as probed when its `*_reachable` flag is true or its `*_checked_at` is set; a side that answered no has the stamp and a false flag.

Two behaviours follow from that:

- A manual `blocked` survives a successful probe. `update_reachability` records probe facts and never rewrites `status`.
- Every preflight re-probes both sides. A stored reachable flag is history; a probe that now fails makes the mapping `blocked` (the table is absent) or `unknown` (the probe could not run, for example a database error other than "no such table").

An unknown probe has no column to live in, so preflight passes it to the derive function as `oracle_probe_failed` / `bigquery_probe_failed` for that run only; it is not stored.

## Readers

- `src/preflight.py` derives the state after each probe; a table that is not `ready` is blocked and its message names the state and reasons, for example `sales_orders: blocked (held)`.
- `src/pipelines/oracle_to_bigquery.py` builds the registry mapping from rows whose derived state is ready.
- The **Table Correspondence** tab in `app/views/translator_demo.py` shows the derived `state` beside the stored columns, and offers `STORED_STATUSES` for the manual edit form.

A new reader calls `derive_mapping_state`; it does not re-derive a mode from the flags. The machine is declared in `.fleet.toml` as `table-mapping`, and `tests/test_mapping_state.py` holds the table test (one row per observation) and the test that proves a hold is not lifted by a probe.
