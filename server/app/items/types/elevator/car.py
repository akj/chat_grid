"""Elevator condition and transitions over an item's persisted parameters."""

from __future__ import annotations

from math import isfinite
from typing import Literal, cast

from ....floors import floor_name
from ....models import WorldItem

Phase = Literal["idle", "opening", "arriving", "door_open", "closing", "moving"]
Step = Literal["arrived", "door_opened", "door_closed", "departed", "settled"]

DOOR_OPEN_CLIP_SECONDS = 2.563107
DOOR_CLOSE_CLIP_SECONDS = 3.765601
DEFAULT_DOOR_OPEN_SECONDS = 5.0
DEFAULT_TRAVEL_SECONDS = 5.0


class ElevatorCar:
    """One elevator item's condition: where it is, its door, and what comes next."""

    def __init__(self, item: WorldItem) -> None:
        """View the item's live parameters without copying them."""

        self.item = item

    @property
    def phase(self) -> Phase:
        """Return the current lifecycle phase."""

        return cast(Phase, self.item.params.get("state", "idle"))

    @property
    def landing(self) -> int:
        """Return the last completed landing elevation."""

        return int(self.item.params.get("currentZ", 0))

    @property
    def target(self) -> int | None:
        """Return the active travel destination, if any."""

        target = self.item.params.get("targetZ")
        return int(target) if target is not None else None

    @property
    def door_open(self) -> bool:
        """Return whether the door permits boarding and exiting."""

        return bool(self.item.params.get("doorOpen", False))

    @property
    def floors(self) -> list[int]:
        """Return configured integer floor elevations in ascending order."""

        floors = self.item.params.get("floorZs", [0, 40])
        return sorted(int(z) for z in floors if isinstance(z, int))

    def other_floor(self, z: int) -> int:
        """Return the first configured floor different from the given landing."""

        return next(floor for floor in self.floors if floor != z)

    def next_destination(self) -> int | None:
        """Prefer a rider's destination over a queued call to another floor."""

        for key in ("departOnCloseZ", "queuedZ"):
            destination = self.item.params.get(key)
            if isinstance(destination, int) and destination != self.landing:
                return destination
        return None

    @property
    def door_open_seconds(self) -> float:
        """Return the validated door dwell duration in seconds."""

        return self._duration_seconds("doorOpenSeconds", DEFAULT_DOOR_OPEN_SECONDS)

    @property
    def travel_seconds(self) -> float:
        """Return the validated travel duration in seconds."""

        return self._duration_seconds("travelSeconds", DEFAULT_TRAVEL_SECONDS)

    def phase_seconds(self) -> float | None:
        """Return the current phase's duration, or None when idle."""

        if self.phase in {"opening", "arriving"}:
            return DOOR_OPEN_CLIP_SECONDS
        if self.phase == "door_open":
            return self.door_open_seconds
        if self.phase == "closing":
            return DOOR_CLOSE_CLIP_SECONDS
        if self.phase == "moving":
            return self.travel_seconds
        return None

    def door_block_reason(self) -> str | None:
        """Explain why the current phase prevents passage through the door."""

        if self.phase in {"moving", "arriving"}:
            return "The elevator is moving."
        if self.phase in {"opening", "closing"}:
            return f"The elevator door is {self.phase}."
        return None

    def describe(self) -> str:
        """Describe the car's destination or its landing and door condition."""

        if self.phase == "moving":
            target = self.target if self.target is not None else self.landing
            direction = "up" if target > self.landing else "down"
            return (
                f"{self.item.title} is headed to {floor_name(target)}, "
                f"traveling {direction}."
            )
        if self.phase in {"opening", "arriving", "closing"}:
            door: str = "opening" if self.phase == "arriving" else self.phase
        else:
            door = "open" if self.door_open else "closed"
        return f"{self.item.title} is on {floor_name(self.landing)}, door {door}."

    def place_at(self, z: int) -> None:
        """Set the car's landing when placing an elevator item."""

        self.item.params["currentZ"] = z

    def call_to(self, z: int) -> None:
        """Start a trip to the requested landing with the door closed."""

        self.item.params.update(targetZ=z, state="moving", doorOpen=False)

    def queue_call(self, z: int) -> None:
        """Remember a landing call until the door finishes closing."""

        self.item.params["queuedZ"] = z

    def begin_opening(self) -> None:
        """Start opening the door while keeping passage blocked."""

        self.item.params.update(state="opening", doorOpen=False)

    def board(self, destination_z: int) -> None:
        """Schedule the rider's destination for departure after closing."""

        self.item.params["departOnCloseZ"] = destination_z

    def advance(self) -> Step | None:
        """Complete the current phase and report the resulting transition."""

        params = self.item.params
        if self.phase == "moving":
            params.update(
                currentZ=self.target if self.target is not None else self.landing,
                targetZ=None,
                state="arriving",
                doorOpen=False,
            )
            return "arrived"
        if self.phase in {"opening", "arriving"}:
            params.update(state="door_open", doorOpen=True)
            return "door_opened"
        if self.phase == "door_open":
            params.update(state="closing", doorOpen=False)
            return "door_closed"
        if self.phase == "closing":
            destination = self.next_destination()
            params.update(departOnCloseZ=None, queuedZ=None)
            if destination is None:
                params.update(state="idle", targetZ=None)
                return "settled"
            params.update(state="moving", targetZ=destination)
            return "departed"
        return None

    def reset_to_landing(self, default_params: dict) -> None:
        """Repair persisted trips by resting at a configured landing on startup."""

        for key in ("doorOpenSeconds", "travelSeconds"):
            self.item.params.setdefault(key, default_params[key])
        floors = self.floors
        landing = self.landing
        if landing not in floors:
            landing = min(floors, default=0)
        self.item.params.update(
            currentZ=landing,
            targetZ=None,
            queuedZ=None,
            departOnCloseZ=None,
            state="idle",
            doorOpen=False,
        )
        self.item.z = 0

    def _duration_seconds(self, key: str, default: float) -> float:
        """Read a finite duration clamped to the editable range."""

        try:
            value = float(self.item.params.get(key, default))
        except (TypeError, ValueError):
            return default
        if not isfinite(value):
            return default
        return max(0, min(300, value))
