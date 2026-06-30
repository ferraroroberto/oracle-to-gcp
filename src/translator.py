"""Bounded Oracle-to-BigQuery translation functions."""

from __future__ import annotations

import re

from src.llm_client import LocalHubClient
from src.pipeline_config import LLMConfig
from src.sql_processing import extract_targets


class TranslationEngine:
    """Translate one SQL unit with local-hub consultation and guardrails."""

    def __init__(
        self,
        *,
        use_local_hub: bool = True,
        simulate_first_attempt_mismatch: bool = False,
        client: LocalHubClient | None = None,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.use_local_hub = use_local_hub
        self.simulate_first_attempt_mismatch = simulate_first_attempt_mismatch
        self.client = client or (
            LocalHubClient.from_config(llm_config) if llm_config is not None else LocalHubClient()
        )
        self.last_note = ""
        self.last_llm_response = None

    def translate(
        self,
        oracle_sql: str,
        mapping: dict[str, str],
        *,
        attempt: int = 0,
    ) -> tuple[str, str]:
        """Return BigQuery SQL and the provider label used for the unit."""
        self.last_llm_response = None
        if self.use_local_hub:
            response = self.client.translate(oracle_sql, mapping)
            self.last_llm_response = response
            if response.text and _candidate_uses_known_tables(response.text, mapping):
                self.last_note = "local hub candidate accepted"
                return response.text, response.provider
            if response.error:
                self.last_note = f"local hub unavailable: {response.error}"
            else:
                self.last_note = "local hub response failed deterministic guardrails"

        sql = deterministic_translate(oracle_sql, mapping)
        if self.simulate_first_attempt_mismatch and attempt == 0 and extract_targets(oracle_sql):
            sql = re.sub(r"SUM\s*\(\s*COALESCE\(o\.amount,\s*0\)\s*\)", "SUM(0)", sql, flags=re.IGNORECASE)
            self.last_note = "deterministic demo intentionally emitted a bad first attempt"
        return sql, "deterministic-fallback"


def deterministic_translate(oracle_sql: str, mapping: dict[str, str]) -> str:
    """Translate the supported mock Oracle subset to BigQuery Standard SQL."""
    sql = oracle_sql
    for oracle_name, bq_name in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
        sql = re.sub(rf"\b{re.escape(oracle_name)}\b", bq_name, sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bNVL\s*\(", "COALESCE(", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bTRUNC\s*\(", "DATE(", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"\bCREATE\s+TABLE\s+([a-zA-Z_][\w$]*)\s+AS",
        lambda match: f"CREATE OR REPLACE TABLE {mapping.get(match.group(1).lower(), match.group(1).lower())} AS",
        sql,
        flags=re.IGNORECASE,
    )
    return sql.strip()


def _candidate_uses_known_tables(candidate: str, mapping: dict[str, str]) -> bool:
    lowered = candidate.lower()
    return any(target in lowered for target in mapping.values())
