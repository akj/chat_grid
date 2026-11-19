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


def _sync_directory(directory: Path) -> None:
    """Persist a rename on systems supporting directory file descriptors."""

    if os.name == "posix":
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write_json_atomic(path: Path, payload: object) -> None:
    """Replace `path` with `payload` as compact JSON, and sync the replacement.

    The JSON is written and synced to a temporary file in the same directory,
    then renamed over `path`, so a crash at any point leaves either the old
    file or the complete new one. A symlinked `path` keeps its link: the file
    it points to is the one replaced. Errors propagate after the temporary file
    is removed.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    path = Path(os.path.realpath(path))
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        _sync_directory(path.parent)
    except BaseException:
        with suppress(OSError):
            Path(temp_name).unlink(missing_ok=True)
        raise


def read_state_text(path: Path) -> str | None:
    """Return a state file's text, or None when the file does not exist yet.

    Any other error, including denied access, propagates so the caller treats
    the file as unreadable rather than as a first run.
    """

    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def set_aside_unreadable(path: Path, error: Exception) -> bool:
    """Rename a state file that failed to load so later saves cannot replace it.

    The file keeps its name with an `.unreadable-<UTC time>` suffix next to the
    original, where an admin can inspect or restore it. A symlinked `path`
    keeps its link and its target is set aside. Returns False when `path` is
    not a regular file or cannot be renamed; the caller must then stop saving
    to `path` for this run, or the next save would replace it.
    """

    path = Path(os.path.realpath(path))
    try:
        regular_file = path.is_file()
    except OSError:
        regular_file = False
    if not regular_file:
        LOGGER.error(
            "could not load %s (%s) or confirm it is a regular file to set "
            "aside; saving it is disabled until restart",
            path,
            error,
        )
        return False
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.name}.unreadable-{stamp}")
    try:
        os.replace(path, target)
        _sync_directory(target.parent)
    except OSError as rename_error:
        LOGGER.error(
            "could not load %s (%s) or durably set it aside (%s); saving it is disabled "
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
