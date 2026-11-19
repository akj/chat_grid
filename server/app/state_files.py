"""Crash-safe JSON world-state files.

World state is saved by rewriting a whole JSON file. These helpers make that
rewrite all-or-nothing, and keep a file that fails to load from being replaced
by the empty state the server falls back to.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import time

LOGGER = logging.getLogger("chgrid.server.state_files")


def write_json_atomic(path: Path, payload: object) -> None:
    """Replace `path` with `payload` as compact JSON, or leave it untouched.

    The JSON is written and synced to a temporary file in the same directory,
    then renamed over `path`, so a crash at any point leaves either the old
    file or the complete new one. Errors propagate after the temporary file is
    removed.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def set_aside_unreadable(path: Path, error: Exception) -> None:
    """Rename a state file that failed to load so later saves cannot replace it.

    The file keeps its name with an `.unreadable-<UTC time>` suffix next to the
    original, where an admin can inspect or restore it. The server then starts
    without that state.
    """

    target = path.with_name(
        f"{path.name}.unreadable-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    )
    try:
        os.replace(path, target)
    except OSError as rename_error:
        LOGGER.error(
            "could not load %s (%s) and could not set it aside (%s); "
            "the next save will replace it",
            path,
            error,
            rename_error,
        )
        return
    LOGGER.error(
        "could not load %s (%s); kept it as %s and started without it",
        path,
        error,
        target,
    )
