"""Server-authoritative wall geometry, validation, and persistence."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from pathlib import Path
from typing import Literal, cast
import uuid

from .client import ClientConnection
from .models import WallStructure
from .state_files import read_state_text, set_aside_unreadable, write_json_atomic

LOGGER = logging.getLogger("chgrid.server.structures")


class StructureError(ValueError):
    """Describe a rejected World Builder structure mutation."""


class StructureService:
    """Own wall runs and their unit-edge collision index."""

    def __init__(
        self,
        *,
        state_file: Path | None,
        grid_size: int,
        presets: Mapping[str, Mapping[str, object]],
    ) -> None:
        """Load structures and normalize editable server preset definitions."""

        self.state_file = state_file
        self.grid_size = max(1, int(grid_size))
        self.presets = {str(key): dict(value) for key, value in presets.items()}
        self.structures: dict[str, WallStructure] = {}
        self._edges: dict[tuple[int, str, int, int], str] = {}
        self.load_state()

    def preset_snapshot(self) -> list[dict[str, object]]:
        """Return deterministic client-safe World Builder preset definitions."""

        return [
            {"id": preset_id, **values}
            for preset_id, values in sorted(self.presets.items())
        ]

    @staticmethod
    def wall_edges(wall: WallStructure) -> list[tuple[int, str, int, int]]:
        """Expand one editable run into canonical unit-edge keys."""

        return [
            (
                wall.floorZ,
                wall.orientation,
                wall.startX + (offset if wall.orientation == "horizontal" else 0),
                wall.startY + (offset if wall.orientation == "vertical" else 0),
            )
            for offset in range(wall.length)
        ]

    def add_wall(
        self, client: ClientConnection, *, preset_id: str, direction: str
    ) -> WallStructure:
        """Create a one-edge wall beside the requesting builder."""

        preset = self.presets.get(preset_id)
        if preset is None:
            raise StructureError("Unknown wall preset.")
        placement = {
            "north": (client.x, client.y + 1, "horizontal"),
            "south": (client.x, client.y, "horizontal"),
            "east": (client.x + 1, client.y, "vertical"),
            "west": (client.x, client.y, "vertical"),
        }.get(direction)
        if placement is None:
            raise StructureError("Unknown wall direction.")
        start_x, start_y, orientation = placement
        wall = WallStructure(
            id=str(uuid.uuid4()),
            floorZ=client.z,
            startX=start_x,
            startY=start_y,
            orientation=cast(Literal["horizontal", "vertical"], orientation),
            length=1,
            title=str(preset.get("title", "Wall")),
            movementBlocked=bool(preset.get("movementBlocked", True)),
            soundTransmission=self._preset_number(preset, "soundTransmission", 0.0),
            occlusionLowpassHz=int(
                self._preset_number(preset, "occlusionLowpassHz", 800)
            ),
            height=int(self._preset_number(preset, "height", 40)),
            preset=preset_id,
            contactSound=str(preset.get("contactSound", "/sounds/wall.ogg")),
        )
        self._validate_wall(wall)
        self._insert(wall)
        return wall

    def resize_wall(
        self, structure_id: str, *, endpoint: str, delta: int
    ) -> WallStructure:
        """Move one wall endpoint by one edge and return the updated wall."""

        wall = self.structures.get(structure_id)
        if wall is None:
            raise StructureError("Wall not found.")
        values = wall.model_dump()
        if endpoint == "start":
            if delta < 0:
                values["startX" if wall.orientation == "horizontal" else "startY"] -= 1
                values["length"] += 1
            else:
                if wall.length == 1:
                    raise StructureError("A wall must remain at least one square long.")
                values["startX" if wall.orientation == "horizontal" else "startY"] += 1
                values["length"] -= 1
        elif endpoint == "end":
            values["length"] += delta
            if values["length"] < 1:
                raise StructureError("A wall must remain at least one square long.")
        else:
            raise StructureError("Unknown wall endpoint.")
        resized = WallStructure.model_validate(values)
        self._validate_wall(resized, exclude_id=wall.id)
        self._remove_from_index(wall)
        self._insert(resized)
        return resized

    @staticmethod
    def wall_edge_anchor(
        wall: WallStructure, endpoint: Literal["start", "end"]
    ) -> tuple[int, int, int]:
        """Return the first or last occupied unit-edge anchor coordinate."""

        if endpoint == "start":
            return wall.startX, wall.startY, wall.floorZ
        end_offset = wall.length - 1
        return (
            wall.startX + (end_offset if wall.orientation == "horizontal" else 0),
            wall.startY + (end_offset if wall.orientation == "vertical" else 0),
            wall.floorZ,
        )

    def slide_wall(self, structure_id: str, *, delta: int) -> WallStructure:
        """Translate a complete wall run by one perpendicular grid edge."""

        wall = self.structures.get(structure_id)
        if wall is None:
            raise StructureError("Wall not found.")
        values = wall.model_dump()
        values["startY" if wall.orientation == "horizontal" else "startX"] += delta
        moved = WallStructure.model_validate(values)
        self._validate_wall(moved, exclude_id=wall.id)
        self._remove_from_index(wall)
        self._insert(moved)
        return moved

    def rotate_wall(
        self, structure_id: str, *, orientation: Literal["horizontal", "vertical"]
    ) -> WallStructure:
        """Rotate a wall around its start coordinate when the new run fits."""

        wall = self.structures.get(structure_id)
        if wall is None:
            raise StructureError("Wall not found.")
        if wall.orientation == orientation:
            return wall
        values = wall.model_dump()
        values["orientation"] = orientation
        rotated = WallStructure.model_validate(values)
        self._validate_wall(rotated, exclude_id=wall.id)
        self._remove_from_index(wall)
        self._insert(rotated)
        return rotated

    def update_wall(
        self,
        structure_id: str,
        *,
        preset_id: str | None,
        sound_transmission: float | None,
        occlusion_lowpass_hz: int | None,
        contact_sound: str | None,
    ) -> WallStructure:
        """Update explicit properties or reapply a preset to one wall run."""

        wall = self.structures.get(structure_id)
        if wall is None:
            raise StructureError("Wall not found.")
        if all(
            value is None
            for value in (
                preset_id,
                sound_transmission,
                occlusion_lowpass_hz,
                contact_sound,
            )
        ):
            raise StructureError("No wall property was supplied.")
        values = wall.model_dump()
        if preset_id is not None:
            preset = self.presets.get(preset_id)
            if preset is None:
                raise StructureError("Unknown wall preset.")
            values.update(
                {
                    "title": str(preset.get("title", "Wall")),
                    "movementBlocked": bool(preset.get("movementBlocked", True)),
                    "soundTransmission": self._preset_number(
                        preset, "soundTransmission", 0.0
                    ),
                    "occlusionLowpassHz": int(
                        self._preset_number(preset, "occlusionLowpassHz", 800)
                    ),
                    "height": int(self._preset_number(preset, "height", 40)),
                    "preset": preset_id,
                    "contactSound": str(preset.get("contactSound", "/sounds/wall.ogg")),
                }
            )
        if sound_transmission is not None:
            values["soundTransmission"] = sound_transmission
        if occlusion_lowpass_hz is not None:
            values["occlusionLowpassHz"] = occlusion_lowpass_hz
        if contact_sound is not None:
            values["contactSound"] = contact_sound.strip()
        updated = WallStructure.model_validate(values)
        self.structures[wall.id] = updated
        return updated

    def remove(self, structure_id: str) -> WallStructure:
        """Remove and return one complete wall structure."""

        wall = self.structures.get(structure_id)
        if wall is None:
            raise StructureError("Wall not found.")
        self._remove_from_index(wall)
        del self.structures[structure_id]
        return wall

    def blocking_wall_for_move(
        self, *, x: int, y: int, z: int, next_x: int, next_y: int
    ) -> WallStructure | None:
        """Return a blocking wall under the agreed cardinal/diagonal corner rule."""

        dx = next_x - x
        dy = next_y - y
        if abs(dx) > 1 or abs(dy) > 1 or (dx == 0 and dy == 0):
            return None
        if dx and dy:
            line_x = x + (1 if dx > 0 else 0)
            line_y = y + (1 if dy > 0 else 0)

            def blocking_at(edge: tuple[int, str, int, int]) -> WallStructure | None:
                wall = self._wall_at(edge)
                return wall if wall is not None and wall.movementBlocked else None

            x_first_route_wall = blocking_at((z, "vertical", line_x, y)) or blocking_at(
                (z, "horizontal", next_x, line_y)
            )
            y_first_route_wall = blocking_at(
                (z, "horizontal", x, line_y)
            ) or blocking_at((z, "vertical", line_x, next_y))
            return (
                x_first_route_wall
                if x_first_route_wall and y_first_route_wall
                else None
            )

        crossed = [
            wall
            for wall in self.walls_crossed_for_move(
                x=x, y=y, z=z, next_x=next_x, next_y=next_y
            )
            if wall.movementBlocked
        ]
        return crossed[0] if crossed else None

    def walls_crossed_for_move(
        self, *, x: int, y: int, z: int, next_x: int, next_y: int
    ) -> list[WallStructure]:
        """Return all wall edges crossed by one valid adjacent-cell move."""

        dx = next_x - x
        dy = next_y - y
        if abs(dx) > 1 or abs(dy) > 1 or (dx == 0 and dy == 0):
            return []
        crossed: list[WallStructure] = []
        if dx:
            key = (z, "vertical", x + (1 if dx > 0 else 0), y)
            wall = self._wall_at(key)
            if wall:
                crossed.append(wall)
        if dy:
            key = (z, "horizontal", x, y + (1 if dy > 0 else 0))
            wall = self._wall_at(key)
            if wall:
                crossed.append(wall)
        return crossed

    def save_state(self) -> None:
        """Persist structures independently from world item state."""

        if self.state_file is None:
            return
        try:
            payload = [wall.model_dump() for wall in self.structures.values()]
            write_json_atomic(self.state_file, payload)
        except Exception as exc:
            LOGGER.warning(
                "failed to persist structures to %s: %s", self.state_file, exc
            )

    def load_state(self) -> None:
        """Load valid, non-overlapping wall runs from persistent storage."""

        if self.state_file is None:
            return
        try:
            text = read_state_text(self.state_file)
            if text is None:
                return
            raw = json.loads(text)
            if not isinstance(raw, list):
                raise StructureError("Structure state must be a list.")
            for entry in raw:
                wall = WallStructure.model_validate(entry)
                self._validate_wall(wall)
                self._insert(wall)
            LOGGER.info(
                "loaded %d structures from %s", len(self.structures), self.state_file
            )
        except Exception as exc:
            self.structures.clear()
            self._edges.clear()
            if not set_aside_unreadable(self.state_file, exc):
                self.state_file = None

    @staticmethod
    def _preset_number(
        preset: Mapping[str, object], key: str, default: int | float
    ) -> int | float:
        """Return one numeric preset value or reject malformed service input."""

        value = preset.get(key, default)
        if not isinstance(value, (int, float)):
            raise StructureError(f"Wall preset {key} must be numeric.")
        return value

    def _validate_wall(
        self, wall: WallStructure, exclude_id: str | None = None
    ) -> None:
        """Validate bounds and canonical edge uniqueness for one run."""

        if wall.orientation == "horizontal":
            in_bounds = (
                0 <= wall.startX < self.grid_size
                and 0 <= wall.startY <= self.grid_size
                and wall.startX + wall.length <= self.grid_size
            )
        else:
            in_bounds = (
                0 <= wall.startX <= self.grid_size
                and 0 <= wall.startY < self.grid_size
                and wall.startY + wall.length <= self.grid_size
            )
        if not in_bounds:
            raise StructureError("Wall would extend outside the world.")
        for edge in self.wall_edges(wall):
            existing = self._edges.get(edge)
            if existing is not None and existing != exclude_id:
                raise StructureError("A wall already occupies part of that edge.")

    def _insert(self, wall: WallStructure) -> None:
        """Insert a validated wall and all of its unit edges."""

        self.structures[wall.id] = wall
        for edge in self.wall_edges(wall):
            self._edges[edge] = wall.id

    def _remove_from_index(self, wall: WallStructure) -> None:
        """Remove the unit edges belonging to one wall from the collision index."""

        for edge in self.wall_edges(wall):
            if self._edges.get(edge) == wall.id:
                del self._edges[edge]

    def _wall_at(self, edge: tuple[int, str, int, int]) -> WallStructure | None:
        """Resolve a canonical edge key to its wall, if present."""

        structure_id = self._edges.get(edge)
        return self.structures.get(structure_id) if structure_id else None
