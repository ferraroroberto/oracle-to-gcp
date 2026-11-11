# Translation-run state

The status of a translation run is a declared state machine. `src/sql_models.py` owns it: the `UnitStatus` and `RunStatus` types, `SqlUnit.transition` for a unit's one legal move, and the pure function `derive_run_status(units)` that maps unit statuses to the run's status. No other code spells `"validated"`, `"flagged"` or `"failed"` to compare or set a status; the pipeline, the file executor, the trace and the Streamlit views use the members.

## Why it is declared

A run's outcome used to be three free strings set in three modules: the pipeline set a unit's `status`, a second expression in the pipeline combined them into the report's `status`, and the batch executor wrote `"failed"` for a script that raised. Nothing said which moves were legal, so a unit could be re-resolved after the report was derived from it.

## States

| Type | State | Meaning |
|---|---|---|
| `UnitStatus` | `pending` | Not yet validated against the mock warehouses. |
| `UnitStatus` | `validated` | The Oracle and BigQuery fingerprints matched. Final. |
| `UnitStatus` | `flagged` | The repair limit was reached without a match; a human reviews it. Final. |
| `RunStatus` | `validated` | Every unit is `validated` (also an empty run). |
| `RunStatus` | `flagged` | At least one unit is not `validated`, including one still `pending`. |
| `RunStatus` | `failed` | The run raised before a report existed; written by the pipeline's error path and by the batch executor. |

`pending` moves to `validated` or `flagged` exactly once. `SqlUnit.transition` raises `IllegalStatusTransition` for any other move, so a resolved unit cannot change behind a report derived from it.

## Stored form

The members are `StrEnum` values, so run reports, traces and CLI output keep the plain strings (`validated`, `flagged`, `failed`) they always held, and a report loaded back from JSON compares equal to the member.

## Readers

- `src/pipelines/oracle_to_bigquery.py` resolves each unit with `transition`, derives the run status with `derive_run_status`, and exits non-zero unless every script's status is `RunStatus.VALIDATED`.
- `src/execution.py` carries the run status on `ScriptExecution` and records `RunStatus.FAILED` for a script that raised.
- `src/trace.py` writes the status it is given into the trace artifact.

A new reader calls `derive_run_status` or compares to a member; it does not re-derive the outcome from unit strings. The machine is declared in `.fleet.toml` as `translation-run`, and `tests/test_run_status.py` holds the table test (one row per observation) and the test that proves a resolved unit refuses every further move.
