"""Crash-safe JSON world-state files.

World state is saved by rewriting a whole JSON file. These helpers make that
rewrite all-or-nothing, and keep a file that fails to load from being replaced
by the empty state the server falls back to.
"""

from __future__ import annotations

from contextlib import suppress
import json
import logging
from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile

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
        with suppress(OSError):
            Path(temp_name).unlink(missing_ok=True)
        raise


def set_aside_unreadable(path: Path, error: Exception) -> bool:
    """Rename a state file that failed to load so later saves cannot replace it.

    The file keeps its name with an `.unreadable-<UTC time>` suffix next to the
    original, where an admin can inspect or restore it. Returns False when
    `path` is not a regular file or cannot be renamed; the caller must then
    stop saving to `path` for this run, or the next save would replace it.
    """

    try:
        regular_file = path.is_file()
    except OSError:
        regular_file = False
    if not regular_file:
        LOGGER.error(
            "could not load %s (%s); it is not a regular file, so saving it is "
            "disabled until restart",
            path,
            error,
        )
        return False
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.unreadable-{stamp}")
    try:
        os.replace(path, target)
    except OSError as rename_error:
        LOGGER.error(
            "could not load %s (%s) or set it aside (%s); saving it is disabled "
            "until restart",
            path,
            error,
            rename_error,
        )
        return False
    LOGGER.error(
        "could not load %s (%s); kept it as %s and started without it",
        path,
        error,
        target,
    )
    return True
