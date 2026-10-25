"""The pre-ship gate runs each vendored ratchet as a stage that any non-zero exit fails.

The ratchet scripts exit 3 when a detector could not run (nothing was checked).
`Invoke-Stage` fails on every non-zero exit, so 3 is NOT CONFIRMED and never a
pass. These tests pin that the stages exist and that nothing in the gate script
can swallow an exit code. (The docs ratchet has its own wrapper test in
`test_docs_ratchet_gate.py`.)
"""
import re
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "verify-before-ship.ps1"

DIRECT_STAGES = ["static_ratchet", "comment_ratchet"]


@pytest.fixture(scope="module")
def gate_text() -> str:
    return GATE.read_text(encoding="utf-8")


def test_invoke_stage_fails_on_every_non_zero_exit(gate_text):
    body = gate_text.split("function Invoke-Stage", 1)[1].split("\nInvoke-Stage ", 1)[0]
    assert "$LASTEXITCODE -ne 0" in body
    assert "exit 1" in body


@pytest.mark.parametrize("script", DIRECT_STAGES)
def test_each_ratchet_runs_as_a_gate_stage(gate_text, script):
    assert re.search(
        rf'^Invoke-Stage "[^"]+"\s+\{{ & \$py scripts/{script}\.py \}}\s*$',
        gate_text,
        re.MULTILINE,
    )


def test_no_gate_line_swallows_an_exit_code(gate_text):
    code = [ln for ln in gate_text.splitlines() if not ln.lstrip().startswith("#")]
    swallowed = [ln for ln in code if "SilentlyContinue" in ln or "|| true" in ln or "-ErrorAction Ignore" in ln]
    assert not swallowed
