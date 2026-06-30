#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate for Oracle to GCP. One pass/fail pipeline.

.DESCRIPTION
    Runs, fail-fast, the checks for this Streamlit + Python pipeline prototype.

    Stages:
      1. byte-compile  — every .py under app/ src/ tests/ parses
      2. ruff          — lint the whole repo (ruff defaults + pyupgrade)
      3. pytest        — unit + headless e2e (auto-boots Streamlit per its fixture)

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

Invoke-Stage "byte-compile"                       { & $py -m compileall -q app src tests }
Invoke-Stage "ruff"                               { & $py -m ruff check . }
Invoke-Stage "pytest (unit + e2e)"                { & $py -m pytest }

Write-Host ""
Write-Host "[PASS] all checks green - safe to ship." -ForegroundColor Green
