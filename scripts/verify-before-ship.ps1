#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate for Oracle to GCP. One pass/fail pipeline.

.DESCRIPTION
    Runs, fail-fast, the checks for this Streamlit + Python pipeline prototype.

    Stages:
      1. byte-compile    — every .py under app/ src/ tests/ semantic_layer/ parses
      2. ruff            — lint the whole repo (ruff defaults + pyupgrade)
      3. docs ratchet    — README.md within 40 KiB and no new history marker (issue
                           reference, dated or changelog heading) in README.md or
                           docs/**/*.md beyond .docs-ratchet-baseline.json. Whole
                           tree, never routed; exit 3 (count not established) is
                           NOT CONFIRMED and fails, never a pass or a skip.
      4. structure ratchet — no file over 1,000 lines, function over 100 lines,
                           directory past 40 modules, src -> app import or root
                           folder outside .fleet.toml shape + extra_roots,
                           beyond .structure-ratchet-baseline.json. Exit 3 (not
                           confirmed) fails like any non-zero exit.
      5. instruction ratchet — no rise in a prompt-audit rule hit count in CLAUDE.md /
                           AGENTS.md beyond .instruction-ratchet-baseline.json, and
                           no instruction file past its size cap. Whole tree, never
                           routed; exit 3 (not confirmed) fails like any non-zero exit.
      6. doc-refs ratchet — no README.md / CLAUDE.md / docs/**/*.md reference to a repo
                           path, python -m module or relative link that does not exist,
                           beyond .doc-refs-ratchet-baseline.json. Whole tree, never
                           routed; exit 3 (not confirmed) fails like any non-zero exit.
      7. dead-code ratchet — no new Python function, class or method that nothing in the
                           tracked tree refers to, beyond .dead-code-ratchet-baseline.json.
                           Whole tree, never routed; exit 3 (not confirmed) fails like any
                           non-zero exit.
      8. semantic layer  — definitions validate, generated build/ is current,
                           golden-question evals pass
      9. pytest          — unit + headless e2e (auto-boots Streamlit per its fixture),
                           including semantic_layer/tests

    Ruff config lives in pyproject.toml. This script only sequences the tools.
    It anchors to the repo root, so run it from
    anywhere:  & .\scripts\verify-before-ship.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] .venv not found at $py" -ForegroundColor Red
    Write-Host "       Create it and run: $py -m pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host ">> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
    Write-Host "[PASS] $Name" -ForegroundColor Green
}

Invoke-Stage "byte-compile"                       { & $py -m compileall -q app src tests semantic_layer }
Invoke-Stage "ruff"                               { & $py -m ruff check . }
# The wrapper maps the script's exit 3 to NOT CONFIRMED (tests/test_docs_ratchet_gate.py
# pins it); call it, never scripts/docs_ratchet.py directly.
Invoke-Stage "docs ratchet"                       { & $py -m scripts.docs_ratchet_gate }
# Invoke-Stage fails on any non-zero exit, so the script's exit 3 (NOT CONFIRMED)
# fails the gate instead of passing.
Invoke-Stage "structure ratchet"                  { & $py scripts/structure_ratchet.py }
Invoke-Stage "instruction ratchet"                { & $py scripts/instruction_ratchet.py }
Invoke-Stage "doc-refs ratchet"                   { & $py scripts/doc_refs_ratchet.py }
Invoke-Stage "dead-code ratchet"                  { & $py scripts/dead_code_ratchet.py }
Invoke-Stage "semantic layer: validate"           { & $py -m semantic_layer validate }
Invoke-Stage "semantic layer: build --check"      { & $py -m semantic_layer build --check }
Invoke-Stage "semantic layer: eval"               { & $py -m semantic_layer eval }
Invoke-Stage "pytest (unit + e2e)"                { & $py -m pytest }

Write-Host ""
Write-Host "[PASS] all checks green - safe to ship." -ForegroundColor Green
