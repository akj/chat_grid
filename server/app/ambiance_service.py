"""Server-authoritative ambiance catalog, geometry, and persistence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import logging
from pathlib import Path
import re
import uuid
from urllib.parse import quote

from pydantic import ValidationError

from .client import ClientConnection
from .models import AmbianceRegion, AmbianceType
from .state_files import read_state_text, set_aside_unreadable, write_json_atomic

LOGGER = logging.getLogger("chgrid.server.ambiances")

# These are the formats browsers commonly expose through HTMLAudioElement. The
# catalog is deliberately limited to ordinary files in the dedicated ambiance
# directory; clients never send or receive a server filesystem path.
SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav", ".webm"}
)
DEFAULT_FLOOR_ELEVATIONS = (0, 40)


class AmbianceError(ValueError):
    """Describe a rejected ambiance mutation or persisted region."""


def default_ambiance_sounds_dir() -> Path:
    """Return the repository's client ambiance directory when available."""

    module_root = Path(__file__).resolve().parents[2]
    candidate = module_root / "client" / "public" / "sounds" / "ambiances"
    if candidate.is_dir():
        return candidate
    # Keep a stable path even when the asset directory is not installed yet;
    # discovery then returns an empty catalog and the server remains usable.
    return candidate


def friendly_ambiance_title(stem: str) -> str:
    """Turn a filename stem into a compact editor-facing title."""

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(stem))
    spaced = re.sub(r"[_-]+", " ", spaced).strip()
    if not spaced:
        return str(stem)
    return " ".join(word[:1].upper() + word[1:] for word in spaced.split())


def discover_ambiance_types(sounds_dir: Path) -> list[AmbianceType]:
    """Discover supported ambiance files in deterministic filename order."""

    try:
        paths = sorted(
            (
                path
                for path in sounds_dir.iterdir()
                if path.is_file()
                and path.suffix.casefold() in SUPPORTED_AUDIO_EXTENSIONS
            ),
            key=lambda path: (path.name.casefold(), path.name),
        )
    except OSError as exc:
        LOGGER.warning(
            "failed to discover ambiance assets from %s: %s", sounds_dir, exc
        )
        return []

    discovered: list[AmbianceType] = []
    seen_ids: set[str] = set()
    for path in paths:
        sound_id = path.stem
        # A filename cannot contain a slash, but reject the special path names
        # explicitly so this boundary remains safe if discovery is refactored.
        if (
            not sound_id
            or sound_id in {".", ".."}
            or "/" in sound_id
            or "\\" in sound_id
        ):
            LOGGER.warning("skipping unsafe ambiance filename %s", path.name)
            continue
        if sound_id in seen_ids:
            LOGGER.warning("skipping duplicate ambiance id from %s", path.name)
            continue
        seen_ids.add(sound_id)
        try:
            discovered.append(
                AmbianceType(
                    id=sound_id,
                    title=friendly_ambiance_title(sound_id),
                    url=f"/sounds/ambiances/{quote(path.name, safe='')}",
                )
            )
        except ValidationError:
            LOGGER.warning(
                "skipping ambiance filename with invalid metadata %s", path.name
            )
    return discovered


class AmbianceService:
    """Own inclusive ambiance rectangles and their discovered sound catalog."""

    def __init__(
        self,
        *,
        state_file: Path | None,
        grid_size: int,
        sounds_dir: Path | None = None,
        floor_elevations: Iterable[int] = DEFAULT_FLOOR_ELEVATIONS,
    ) -> None:
        """Load persisted regions and discover the startup ambiance catalog."""

        self.state_file = state_file
        self.grid_size = max(1, int(grid_size))
        self.floor_elevations = frozenset(int(value) for value in floor_elevations)
        self.sounds_dir = (
            Path(sounds_dir)
            if sounds_dir is not None
            else default_ambiance_sounds_dir()
        )
        self.ambiance_types = discover_ambiance_types(self.sounds_dir)
        self._types_by_id = {
            sound_type.id: sound_type for sound_type in self.ambiance_types
        }
        self.ambiances: dict[str, AmbianceRegion] = {}
        self.load_state()

    def ambiance_type_snapshot(self) -> list[dict[str, str]]:
        """Return a client-safe deterministic catalog snapshot."""

        return [sound_type.model_dump() for sound_type in self.ambiance_types]

    def add_ambiance(self, client: ClientConnection) -> AmbianceRegion:
        """Create a one-cell region at the caller's position and floor."""

        if client.z not in self.floor_elevations:
            raise AmbianceError("Ambiances can only be added on a valid floor.")
        first_type = self.ambiance_types[0] if self.ambiance_types else None
        if first_type is None:
            raise AmbianceError("No ambiance sounds are available.")
        region = AmbianceRegion(
            id=str(uuid.uuid4()),
            name="Ambiance",
            soundId=first_type.id,
            floorZ=client.z,
            startX=client.x,
            startY=client.y,
            endX=client.x,
            endY=client.y,
        )
        self._validate_region(region, require_known_sound=True, require_floor=True)
        self.ambiances[region.id] = region
        return region

    def update_ambiance(
        self,
        ambiance_id: str,
        *,
        name: str | None = None,
        sound_id: str | None = None,
        volume: int | None = None,
        fade_distance: float | None = None,
    ) -> AmbianceRegion:
        """Replace supplied editable properties while preserving all geometry."""

        current = self._get(ambiance_id)
        if all(value is None for value in (name, sound_id, volume, fade_distance)):
            raise AmbianceError("No ambiance property was supplied.")
        values = current.model_dump()
        if name is not None:
            values["name"] = name.strip()
        if sound_id is not None:
            values["soundId"] = sound_id
        if volume is not None:
            values["volume"] = volume
        if fade_distance is not None:
            values["fadeDistance"] = fade_distance
        updated = self._validated_replacement(
            values,
            require_known_sound=sound_id is not None,
        )
        self.ambiances[updated.id] = updated
        return updated

    def resize_ambiance(
        self,
        ambiance_id: str,
        *,
        edge: str,
        delta: int,
    ) -> AmbianceRegion:
        """Move one rectangle edge by one cell and validate the result."""

        current = self._get(ambiance_id)
        if edge not in {"west", "east", "south", "north"}:
            raise AmbianceError("Unknown ambiance edge.")
        if delta not in {-1, 1}:
            raise AmbianceError("Ambiance resize delta must be -1 or 1.")
        values = current.model_dump()
        coordinate = {
            "west": "startX",
            "east": "endX",
            "south": "startY",
            "north": "endY",
        }[edge]
        values[coordinate] += delta
        resized = self._validated_replacement(values)
        self.ambiances[resized.id] = resized
        return resized

    def slide_ambiance(
        self,
        ambiance_id: str,
        *,
        axis: str,
        delta: int,
    ) -> AmbianceRegion:
        """Translate a complete rectangle by one cell along one axis."""

        current = self._get(ambiance_id)
        if axis not in {"x", "y"}:
            raise AmbianceError("Unknown ambiance slide axis.")
        if delta not in {-1, 1}:
            raise AmbianceError("Ambiance slide delta must be -1 or 1.")
        values = current.model_dump()
        start_coordinate = "startX" if axis == "x" else "startY"
        end_coordinate = "endX" if axis == "x" else "endY"
        values[start_coordinate] += delta
        values[end_coordinate] += delta
        moved = self._validated_replacement(values)
        self.ambiances[moved.id] = moved
        return moved

    def remove(self, ambiance_id: str) -> AmbianceRegion:
        """Remove and return one complete ambiance region."""

        region = self._get(ambiance_id)
        del self.ambiances[ambiance_id]
        return region

    def save_state(self) -> None:
        """Atomically persist all regions to the configured state file."""

        if self.state_file is None:
            return
        try:
            payload = [
                region.model_dump(mode="json") for region in self.ambiances.values()
            ]
            write_json_atomic(self.state_file, payload)
        except Exception as exc:
            LOGGER.warning(
                "failed to persist ambiances to %s: %s", self.state_file, exc
            )

    def load_state(self) -> None:
        """Load a valid region list transactionally, retaining missing assets."""

        if self.state_file is None:
            return
        try:
            text = read_state_text(self.state_file)
            if text is None:
                return
            raw = json.loads(text)
            if not isinstance(raw, list):
                raise AmbianceError("Ambiance state must be a list.")
            loaded: dict[str, AmbianceRegion] = {}
            for entry in raw:
                region = AmbianceRegion.model_validate(entry)
                self._validate_region(region)
                if region.id in loaded:
                    raise AmbianceError(f"Duplicate ambiance id: {region.id}")
                loaded[region.id] = region
            self.ambiances = loaded
            LOGGER.info("loaded %d ambiances from %s", len(loaded), self.state_file)
        except Exception as exc:
            if not set_aside_unreadable(self.state_file, exc):
                self.state_file = None

    def _get(self, ambiance_id: str) -> AmbianceRegion:
        """Return one region or raise a mutation-friendly error."""

        region = self.ambiances.get(ambiance_id)
        if region is None:
            raise AmbianceError("Ambiance not found.")
        return region

    def _validated_replacement(
        self,
        values: Mapping[str, object],
        *,
        require_known_sound: bool = False,
    ) -> AmbianceRegion:
        """Validate a candidate before replacing the current map entry."""

        try:
            candidate = AmbianceRegion.model_validate(values)
        except ValidationError as exc:
            raise AmbianceError(self._validation_message(exc)) from exc
        self._validate_region(candidate, require_known_sound=require_known_sound)
        return candidate

    def _validate_region(
        self,
        region: AmbianceRegion,
        *,
        require_known_sound: bool = False,
        require_floor: bool = False,
    ) -> None:
        """Validate bounds, positive area, floor selection, and sound selection."""

        if region.startX > region.endX or region.startY > region.endY:
            raise AmbianceError("Ambiance must have a positive area.")
        if not (
            0 <= region.startX < self.grid_size
            and 0 <= region.endX < self.grid_size
            and 0 <= region.startY < self.grid_size
            and 0 <= region.endY < self.grid_size
        ):
            raise AmbianceError("Ambiance would extend outside the world.")
        if require_floor and region.floorZ not in self.floor_elevations:
            raise AmbianceError("Ambiances can only be added on a valid floor.")
        if require_known_sound and region.soundId not in self._types_by_id:
            raise AmbianceError("Unknown ambiance sound.")

    @staticmethod
    def _validation_message(exc: ValidationError) -> str:
        """Extract a compact user-facing message from model validation errors."""

        errors = exc.errors()
        if not errors:
            return "Invalid ambiance."
        message = errors[0].get("msg")
        return str(message) if message else "Invalid ambiance."
