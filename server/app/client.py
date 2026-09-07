"""Client connection model used by the signaling server."""

from __future__ import annotations

from dataclasses import dataclass

from websockets.asyncio.server import ServerConnection

from .models import FacingDeg


@dataclass
class ClientConnection:
    """Represents one connected websocket client and its world state."""

    websocket: ServerConnection
    id: str
    authenticated: bool = False
    user_id: str | None = None
    username: str | None = None
    role: str = "user"
    permissions: set[str] | None = None
    session_token: str | None = None
    nickname: str = "user..."
    saved_x: int | None = None
    saved_y: int | None = None
    saved_z: int | None = None
    saved_facing_deg: int | None = None
    x: int = 20
    y: int = 20
    z: int = 0
    facing_deg: FacingDeg = 0
    last_position_update_ms: int = 0
    last_seen_at_ms: int = 0
    movement_window_index: int = -1
    movement_window_steps_used: int = 0
    world_ready: bool = False
    elevator_id: str | None = None

    def summary(self) -> dict[str, str | int]:
        """Return a compact serializable snapshot for logs/diagnostics."""

        return {
            "id": self.id,
            "nickname": self.nickname,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "facingDeg": self.facing_deg,
        }
