"""The one place this repo writes caller-chosen files and folders.

Pipeline artifacts, configs and mock databases land wherever the caller points
the run (a run directory, a result folder beside a script), so the target is a
parameter at every call site. Routing the writes through these helpers keeps
the reasoning for that in one spot instead of repeating it at each caller.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    """Create ``path`` and its parents if missing; return it."""
    directory = Path(path)
    # runtime-write-exempt: the helper creates whatever folder the caller names; the callers
    # pass run, result, input or log directories derived from config.
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_text_file(path: str | Path, text: str) -> Path:
    """Write UTF-8 ``text`` to ``path`` (the parent must exist); return the path."""
    target = Path(path)
    # runtime-write-exempt: the helper writes whatever file the caller names; the callers
    # pass artifact, config or input-sample paths derived from config.
    target.write_text(text, encoding="utf-8")
    return target


def copy_file(source: str | Path, destination: str | Path) -> Path:
    """Copy the bytes of ``source`` to ``destination``; return the destination."""
    # runtime-write-exempt: the helper copies to whatever file the caller names; the one
    # caller passes the latest-report path inside its run directory.
    shutil.copyfile(source, destination)
    return Path(destination)
