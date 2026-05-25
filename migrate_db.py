"""Backward-compatible wrapper for `scripts.migrations.migrate_db`."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _maybe_reexec_into_venv() -> None:
    repo_root = Path(__file__).resolve().parent
    venv_python = repo_root / ".venv" / "bin" / "python3"
    current_executable = Path(sys.executable)
    if (
        current_executable == venv_python
        or current_executable.parent == venv_python.parent
        or os.environ.get("VIRTUAL_ENV") == str(repo_root / ".venv")
    ):
        return
    if venv_python.exists():
        os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


def main() -> None:
    _maybe_reexec_into_venv()
    from scripts.migrations.migrate_db import main as migrate_main

    migrate_main()


if __name__ == "__main__":
    main()
