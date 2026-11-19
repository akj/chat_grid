"""Item persistence, hydration, and local mutation helpers."""

from __future__ import annotations

import json
import logging
import time
import uuid
from copy import deepcopy
from pathlib import Path
from .client import ClientConnection
from .item_catalog import get_item_definition
from .models import PersistedWorldItem, WorldItem
from .state_files import read_state_text, set_aside_unreadable, write_json_atomic

LOGGER = logging.getLogger("chgrid.server")


class ItemService:
    """Owns world-item storage, lifecycle, and persistence to disk."""

    def __init__(self, state_file: Path | None = None):
        """Create service and eagerly load persisted state when configured."""

        self.state_file = state_file
        self.piano_songs_file = (
            state_file.with_name("piano_songs.json") if state_file else None
        )
        self.items: dict[str, WorldItem] = {}
        self.piano_songs: dict[str, dict] = {}
        self.load_state()
        self.load_piano_songs()

    @staticmethod
    def now_ms() -> int:
        """Return current Unix time in milliseconds."""

        return int(time.time() * 1000)

    def default_item(self, client: ClientConnection, item_type: str) -> WorldItem:
        """Create a new server-authoritative item at the caller's position."""

        item_def = get_item_definition(item_type)
        now = self.now_ms()
        actor_id = client.user_id or client.id
        actor_name = client.username or client.nickname or actor_id
        return WorldItem(
            id=str(uuid.uuid4()),
            type=item_type,
            title=item_def.default_title,
            x=client.x,
            y=client.y,
            z=client.z,
            createdBy=actor_id,
            createdByName=actor_name,
            updatedBy=actor_id,
            updatedByName=actor_name,
            createdAt=now,
            updatedAt=now,
            version=1,
            capabilities=list(item_def.capabilities),
            useSound=item_def.use_sound,
            emitSound=item_def.emit_sound,
            params=deepcopy(item_def.default_params),
            carrierId=None,
            occupiedOffsets=[
                {"x": offset_x, "y": offset_y}
                for offset_x, offset_y in item_def.occupied_offsets
            ],
        )

    def add_item(self, item: WorldItem) -> None:
        """Insert or replace an item in in-memory state."""

        self.items[item.id] = item

    def remove_item(self, item_id: str) -> None:
        """Remove an item by id when present."""

        if item_id in self.items:
            del self.items[item_id]

    def find_carried_items(self, client_id: str) -> list[WorldItem]:
        """Return all items currently carried by one client."""

        return [item for item in self.items.values() if item.carrierId == client_id]

    def items_on_square(self, x: int, y: int, z: int) -> list[WorldItem]:
        """Return non-carried items occupying a specific world coordinate."""

        return [
            item
            for item in self.items.values()
            if item.carrierId is None
            and self.item_occupies_position(item, x=x, y=y, z=z)
        ]

    @staticmethod
    def item_occupies_position(item: WorldItem, *, x: int, y: int, z: int) -> bool:
        """Return whether an item's floor-aware footprint occupies one cell."""

        floor_zs = item.params.get("floorZs")
        occupies_floor = z in floor_zs if isinstance(floor_zs, list) else item.z == z
        if not occupies_floor:
            return False
        return any(
            item.x + int(offset.get("x", 0)) == x
            and item.y + int(offset.get("y", 0)) == y
            for offset in item.occupiedOffsets
        )

    def drop_carried_items_for_disconnect(
        self, client: ClientConnection
    ) -> list[WorldItem]:
        """Drop all items carried by a disconnected client onto their last tile."""

        changed: list[WorldItem] = []
        for item in self.items.values():
            if item.carrierId == client.id:
                item.carrierId = None
                item.x = client.x
                item.y = client.y
                item.z = client.z
                item.updatedAt = self.now_ms()
                item.updatedBy = "system"
                item.updatedByName = "system"
                changed.append(item)
        return changed

    def load_state(self) -> None:
        """Restore items with catalog defaults for missing params and global fields."""

        if not self.state_file:
            return
        try:
            text = read_state_text(self.state_file)
            if text is None:
                return
            raw = json.loads(text)
            if not isinstance(raw, list):
                raise ValueError("Item state must be a list.")
            loaded: dict[str, WorldItem] = {}
            for entry in raw:
                persisted = PersistedWorldItem.model_validate(entry)
                item_def = get_item_definition(persisted.type)
                item = WorldItem(
                    id=persisted.id,
                    type=persisted.type,
                    title=persisted.title,
                    x=persisted.x,
                    y=persisted.y,
                    z=persisted.z,
                    createdBy=persisted.createdBy,
                    createdByName=persisted.createdByName or persisted.createdBy,
                    updatedBy=persisted.updatedBy or persisted.createdBy,
                    updatedByName=persisted.updatedByName
                    or persisted.updatedBy
                    or persisted.createdBy,
                    createdAt=persisted.createdAt,
                    updatedAt=persisted.updatedAt,
                    version=persisted.version,
                    capabilities=list(item_def.capabilities),
                    useSound=item_def.use_sound,
                    emitSound=item_def.emit_sound,
                    params={**deepcopy(item_def.default_params), **persisted.params},
                    carrierId=persisted.carrierId,
                    occupiedOffsets=[
                        {"x": offset_x, "y": offset_y}
                        for offset_x, offset_y in item_def.occupied_offsets
                    ],
                )
                if item.type == "elevator":
                    for timing_key in ("doorOpenSeconds", "travelSeconds"):
                        item.params.setdefault(
                            timing_key, item_def.default_params[timing_key]
                        )
                    configured_floor_zs = item.params.get("floorZs", [0, 40])
                    floor_zs = {
                        int(floor_z)
                        for floor_z in configured_floor_zs
                        if isinstance(floor_z, int)
                    }
                    current_z = int(item.params.get("currentZ", 0))
                    if current_z not in floor_zs:
                        current_z = min(floor_zs, default=0)
                    item.z = 0
                    item.params.update(
                        {
                            "currentZ": current_z,
                            "targetZ": None,
                            "queuedZ": None,
                            "departOnCloseZ": None,
                            "state": "idle",
                            "doorOpen": False,
                        }
                    )
                elif item.carrierId is not None:
                    item.carrierId = None
                    if item.z not in (0, 40):
                        item.z = 0
                loaded[item.id] = item
            self.items = loaded
            LOGGER.info(
                "loaded %d persisted items from %s", len(self.items), self.state_file
            )
        except Exception as exc:
            if not set_aside_unreadable(self.state_file, exc):
                self.state_file = None

    def load_piano_songs(self) -> None:
        """Load persisted piano song registry used by piano items."""

        if not self.piano_songs_file:
            return
        try:
            text = read_state_text(self.piano_songs_file)
            if text is None:
                return
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("Piano song state must be an object.")
            loaded: dict[str, dict] = {}
            for song_id, payload in raw.items():
                if not isinstance(song_id, str) or not song_id.strip():
                    raise ValueError("Piano song ids must be non-empty text.")
                if not isinstance(payload, dict):
                    raise ValueError(f"Piano song {song_id} must be an object.")
                loaded[song_id] = payload
            self.piano_songs = loaded
            LOGGER.info(
                "loaded %d persisted piano songs from %s",
                len(self.piano_songs),
                self.piano_songs_file,
            )
        except Exception as exc:
            if not set_aside_unreadable(self.piano_songs_file, exc):
                self.piano_songs_file = None

    def save_state(self) -> None:
        """Persist instance-only item data to configured state file."""

        if not self.state_file:
            return
        try:
            payload = [
                PersistedWorldItem(
                    id=item.id,
                    type=item.type,
                    title=item.title,
                    x=item.x,
                    y=item.y,
                    z=item.z,
                    createdBy=item.createdBy,
                    createdByName=item.createdByName,
                    updatedBy=item.updatedBy,
                    updatedByName=item.updatedByName,
                    createdAt=item.createdAt,
                    updatedAt=item.updatedAt,
                    version=item.version,
                    params=item.params,
                    carrierId=item.carrierId,
                ).model_dump(exclude_none=True)
                for item in self.items.values()
            ]
            write_json_atomic(self.state_file, payload)
        except Exception as exc:
            LOGGER.warning(
                "failed to persist item state to %s: %s", self.state_file, exc
            )

    def save_piano_songs(self) -> None:
        """Persist compact piano song registry payload to configured storage file."""

        if not self.piano_songs_file:
            return
        try:
            write_json_atomic(self.piano_songs_file, self.piano_songs)
        except Exception as exc:
            LOGGER.warning(
                "failed to persist piano songs to %s: %s", self.piano_songs_file, exc
            )
