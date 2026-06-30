"""Unit tests for src/config.py — the derived-path contract."""

from __future__ import annotations

from src import config


def test_data_dirs_are_anchored_under_root() -> None:
    assert config.DATA_DIR == config.ROOT_DIR / "data"
    assert config.INPUT_DIR == config.DATA_DIR / "input"
    assert config.OUTPUT_DIR == config.DATA_DIR / "output"
    assert config.LOG_DIR == config.DATA_DIR / "logs"
    assert config.EXAMPLES_DIR == config.ROOT_DIR / "examples"


def test_root_dir_is_the_repo_root() -> None:
    # config.py lives in src/; ROOT_DIR is its parent's parent.
    assert (config.ROOT_DIR / "src" / "config.py").is_file()


def test_app_name_and_debug_have_sane_defaults() -> None:
    assert config.APP_NAME == "Oracle to GCP"
    assert isinstance(config.DEBUG, bool)
