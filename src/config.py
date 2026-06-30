"""
Project configuration
=====================
Single place to read environment-driven settings.  Pipelines and pages
should import from here rather than reading ``os.environ`` directly so
defaults stay consistent.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output"
LOG_DIR = DATA_DIR / "logs"
EXAMPLES_DIR = ROOT_DIR / "examples"

APP_NAME = os.getenv("APP_NAME", "Oracle to GCP")
DEBUG = os.getenv("DEBUG", "0") == "1"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8000")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "8"))
