"""The write helpers refuse a target inside a code folder and write anywhere else."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.fs_write import (
    CODE_FOLDERS,
    ROOT_DIR,
    ensure_dir,
    require_runtime_path,
    write_text_file,
)
from src.sqlite_db import connect_sqlite


def test_helpers_write_outside_the_code_folders(tmp_path: Path) -> None:
    folder = ensure_dir(tmp_path / "run" / "nested")
    target = write_text_file(folder / "report.json", "{}")
    assert target.read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize("folder", CODE_FOLDERS)
def test_a_code_folder_target_is_refused_and_nothing_is_created(folder: str) -> None:
    inside = ROOT_DIR / folder / "_never_created" / "out.txt"
    with pytest.raises(ValueError, match=folder):
        write_text_file(inside, "x")
    with pytest.raises(ValueError, match=folder):
        ensure_dir(inside.parent)
    with pytest.raises(ValueError, match=folder):
        connect_sqlite(inside.with_suffix(".db"))
    assert not inside.parent.exists()


def test_a_dotdot_path_that_lands_in_a_code_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        require_runtime_path(ROOT_DIR / "data" / ".." / "src" / "x.txt")
