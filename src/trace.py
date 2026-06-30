"""Structured trace capture for pipeline runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.pipeline_config import PipelineConfig, TraceConfig, config_to_dict


@dataclass(slots=True)
class TraceEvent:
    """One timestamped trace event."""

    timestamp: str
    stage: str
    event: str
    details: dict[str, Any] = field(default_factory=dict)


class TraceRecorder:
    """Collect and persist structured trace events."""

    def __init__(self, config: TraceConfig) -> None:
        self.config = config
        self.events: list[TraceEvent] = []

    def add(self, stage: str, event: str, details: dict[str, Any] | None = None) -> None:
        """Append a trace event when tracing is enabled."""
        if not self.config.enabled:
            return
        self.events.append(
            TraceEvent(
                timestamp=datetime.now(UTC).isoformat(),
                stage=stage,
                event=event,
                details=details or {},
            )
        )

    def rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Return row-count and optional row samples for query trace events."""
        details: dict[str, Any] = {"row_count": len(rows)}
        if self.config.capture_query_results:
            max_rows = max(0, self.config.max_query_rows)
            details["rows"] = rows[:max_rows]
            details["truncated"] = len(rows) > max_rows
        return details

    def to_list(self) -> list[dict[str, Any]]:
        """Return JSON-serializable trace events."""
        return [asdict(event) for event in self.events]

    def write(
        self,
        path: Path,
        *,
        status: str,
        pipeline_config: PipelineConfig,
        artifacts: dict[str, str] | None = None,
    ) -> None:
        """Write a browsable JSON trace artifact to disk."""
        if not self.config.enabled:
            return
        payload = {
            "status": status,
            "generated_at": datetime.now(UTC).isoformat(),
            "config": config_to_dict(pipeline_config),
            "artifacts": artifacts or {},
            "events": self.to_list(),
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
