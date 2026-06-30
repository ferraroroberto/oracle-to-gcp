"""File-backed SQL execution helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import ROOT_DIR
from src.pipeline_config import PipelineConfig
from src.pipelines.oracle_to_bigquery import run
from src.sql_models import RunReport


@dataclass(slots=True)
class ScriptExecution:
    """Summary of one file-backed pipeline execution."""

    script_path: Path
    result_dir: Path
    status: str
    artifacts: dict[str, str]
    error: str = ""


def resolve_user_path(path: str | Path) -> Path:
    """Resolve a user/config path relative to the repo root."""
    resolved = Path(path)
    return resolved if resolved.is_absolute() else ROOT_DIR / resolved


def ensure_execution_input_dir(config: PipelineConfig) -> Path:
    """Create and return the configured execution input directory."""
    directory = resolve_user_path(config.execution.default_input_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def result_dir_for_script(script_path: Path, suffix: str) -> Path:
    """Return the sibling result directory for a SQL script."""
    return script_path.parent / f"{script_path.stem}{suffix}"


def list_sql_scripts(directory: str | Path) -> list[Path]:
    """Return non-recursive `.sql` scripts in a directory."""
    root = resolve_user_path(directory)
    if not root.exists():
        return []
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    return sorted(path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".sql")


def run_sql_file(
    script_path: str | Path,
    *,
    pipeline_config: PipelineConfig,
    result_suffix: str | None = None,
) -> ScriptExecution:
    """Run the pipeline for one SQL file and write sibling result artifacts."""
    resolved_script = resolve_user_path(script_path)
    if resolved_script.suffix.lower() != ".sql":
        raise ValueError(f"SQL script must end with .sql: {resolved_script}")
    if not resolved_script.is_file():
        raise FileNotFoundError(str(resolved_script))

    suffix = result_suffix if result_suffix is not None else pipeline_config.execution.result_suffix
    result_dir = result_dir_for_script(resolved_script, suffix)
    result_dir.mkdir(parents=True, exist_ok=True)
    source_sql = resolved_script.read_text(encoding="utf-8")
    report = run(script=source_sql, pipeline_config=pipeline_config, run_dir=result_dir)
    return _finalize_execution_report(report, resolved_script, result_dir)


def run_sql_batch(
    directory: str | Path,
    *,
    pipeline_config: PipelineConfig,
    result_suffix: str | None = None,
) -> list[ScriptExecution]:
    """Run every `.sql` script in a directory, non-recursively."""
    results: list[ScriptExecution] = []
    suffix = result_suffix if result_suffix is not None else pipeline_config.execution.result_suffix
    for script in list_sql_scripts(directory):
        try:
            results.append(run_sql_file(script, pipeline_config=pipeline_config, result_suffix=suffix))
        except Exception as exc:  # noqa: BLE001 - batch mode reports per-script failures and continues.
            result_dir = result_dir_for_script(script, suffix)
            artifacts: dict[str, str] = {}
            latest_trace = _latest_file(result_dir, "run_trace_*.json")
            if latest_trace is not None:
                artifacts["run_trace_json"] = str(latest_trace)
            results.append(
                ScriptExecution(
                    script_path=script,
                    result_dir=result_dir,
                    status="failed",
                    artifacts=artifacts,
                    error=str(exc),
                )
            )
    return results


def find_previous_results(root: str | Path, *, result_suffix: str = "_bq") -> list[Path]:
    """Find previous report JSON files under sibling result directories."""
    directory = resolve_user_path(root)
    if not directory.exists() or not directory.is_dir():
        return []
    reports: list[Path] = []
    for result_dir in directory.glob(f"*{result_suffix}"):
        if result_dir.is_dir():
            reports.extend(result_dir.glob("run_report_*.json"))
    return sorted(reports, key=lambda path: (path.stat().st_mtime, path.name), reverse=True)


def load_report_json(report_path: str | Path) -> dict[str, Any]:
    """Load a previous pipeline report JSON."""
    resolved = resolve_user_path(report_path)
    return json.loads(resolved.read_text(encoding="utf-8"))


def _finalize_execution_report(report: RunReport, script_path: Path, result_dir: Path) -> ScriptExecution:
    source_copy = result_dir / "source_oracle.sql"
    source_copy.write_text(script_path.read_text(encoding="utf-8"), encoding="utf-8")

    log_path = result_dir / f"{report.script_id}_log.txt"
    log_path.write_text("\n".join(report.log) + "\n", encoding="utf-8")

    report.artifacts["source_sql"] = str(source_copy)
    report.artifacts["run_log_txt"] = str(log_path)
    report.artifacts["input_sql"] = str(script_path)
    report_path = Path(report.artifacts["run_report_json"])
    report_path.write_text(json.dumps(_report_to_jsonable(report), indent=2), encoding="utf-8")
    return ScriptExecution(
        script_path=script_path,
        result_dir=result_dir,
        status=report.status,
        artifacts=report.artifacts,
    )


def _latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _report_to_jsonable(report: RunReport) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(report)
