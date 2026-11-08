"""The one place this repo writes caller-chosen files and folders.

Pipeline artifacts, configs and mock databases land wherever the caller points
the run (a run directory, a result folder beside a script), so the target is a
parameter at every call site. Every helper checks at runtime that the target is
not inside a code folder, so a caller cannot write runtime output into the source
tree: the guarantee is enforced here instead of being asserted at each caller.
"""

from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
# Mirrors `code_folders` in .fleet.toml [runtime_write_ratchet]; the ratchet cannot
# resolve a caller-supplied path, so this check enforces the same boundary at runtime.
CODE_FOLDERS = ("src", "app", "scripts", "semantic_layer")


def require_runtime_path(path: str | Path) -> Path:
    """Return ``path`` unchanged; raise ``ValueError`` if it resolves inside a code folder."""
    resolved = Path(path).resolve()
    for folder in CODE_FOLDERS:
        if resolved.is_relative_to(ROOT_DIR / folder):
            raise ValueError(f"{path} is inside the {folder}/ code folder; write runtime output elsewhere")
    return Path(path)


def ensure_dir(path: str | Path) -> Path:
    """Create ``path`` and its parents if missing; return it."""
    directory = require_runtime_path(path)
    # runtime-write-exempt: the folder is caller-supplied; require_runtime_path above refuses a code folder
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_text_file(path: str | Path, text: str) -> Path:
    """Write UTF-8 ``text`` to ``path`` (the parent must exist); return the path."""
    target = require_runtime_path(path)
    # runtime-write-exempt: the file is caller-supplied; require_runtime_path above refuses a code folder
    target.write_text(text, encoding="utf-8")
    return target
