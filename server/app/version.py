"""Server version metadata helpers.

This module owns the server-only revision identifier used for diagnostics and
`/version` output. The shared public release version continues to come from the
client's `public/version.js` metadata so one release number can be used across
the whole app.
"""

from __future__ import annotations


SERVER_REVISION = "S415"


def format_server_version(release_version: str) -> str:
    """Return display text for the current server build."""

    release = str(release_version).strip()
    revision = str(SERVER_REVISION).strip()
    if release and revision:
        return f"{release} {revision}"
    return release or revision or "unknown"
