"""Pure elevator condition, transition, and timing tests."""

from copy import deepcopy

import pytest

from app.floors import floor_name, is_floor
from app.items.types.elevator.car import (
    DEFAULT_DOOR_OPEN_SECONDS,
    DEFAULT_TRAVEL_SECONDS,
    DOOR_CLOSE_CLIP_SECONDS,
    DOOR_OPEN_CLIP_SECONDS,
    ElevatorCar,
    Phase,
)
from app.items.types.elevator.definition import DEFAULT_PARAMS
from app.models import WorldItem


@pytest.fixture
def item() -> WorldItem:
    """Build an elevator without a server or event loop."""

    return WorldItem(
        id="elevator-1",
        type="elevator",
        title="Elevator",
        x=10,
        y=10,
        z=0,
        createdBy="u1",
        createdByName="Tester",
        updatedBy="u1",
        updatedByName="Tester",
        createdAt=1,
        updatedAt=1,
        version=1,
        capabilities=[],
        params=deepcopy(DEFAULT_PARAMS),
    )


def test_call_completes_full_cycle_without_touching_metadata(item: WorldItem) -> None:
    """A called car arrives, opens, closes, and settles on the destination."""

    before = item.model_dump(exclude={"params"})
    car = ElevatorCar(item)
    car.call_to(40)
    assert (car.phase, car.landing, car.target, car.door_open) == (
        "moving",
        0,
        40,
        False,
    )
    assert car.advance() == "arrived"
    assert (car.phase, car.landing, car.target, car.door_open) == (
        "arriving",
        40,
        None,
        False,
    )
    assert car.advance() == "door_opened"
    assert car.phase == "door_open"
    assert car.door_open
    assert car.advance() == "door_closed"
    assert car.phase == "closing"
    assert not car.door_open
    assert car.advance() == "settled"
    assert (car.phase, car.landing, car.target) == ("idle", 40, None)
    assert car.advance() is None
    assert item.model_dump(exclude={"params"}) == before
    assert item.params["currentZ"] == 40


def test_boarding_destination_takes_priority_and_clears_calls(item: WorldItem) -> None:
    """Closing consumes both pending calls, preferring the rider's destination."""

    car = ElevatorCar(item)
    car.begin_opening()
    assert car.phase == "opening"
    assert not car.door_open
    assert car.advance() == "door_opened"
    car.queue_call(80)
    car.board(40)
    assert car.next_destination() == 40
    assert car.advance() == "door_closed"
    assert car.phase_seconds() == DOOR_CLOSE_CLIP_SECONDS
    assert car.target is None
    assert car.advance() == "departed"
    assert (car.phase, car.target, car.door_open) == ("moving", 40, False)
    assert item.params["departOnCloseZ"] is None
    assert item.params["queuedZ"] is None


@pytest.mark.parametrize(
    ("depart", "queued", "expected"),
    [(0, 40, 40), (None, 40, 40), ("40", 40, 40), (None, "40", None)],
)
def test_next_destination_ignores_current_floor_and_nonintegers(
    item: WorldItem, depart: object, queued: object, expected: int | None
) -> None:
    """Only integer destinations away from the landing can cause departure."""

    item.params.update(departOnCloseZ=depart, queuedZ=queued)
    assert ElevatorCar(item).next_destination() == expected


def test_queued_call_at_current_floor_settles(item: WorldItem) -> None:
    """A queued call at the landing must not start another trip."""

    car = ElevatorCar(item)
    car.begin_opening()
    car.advance()
    car.queue_call(0)
    car.advance()
    assert car.advance() == "settled"
    assert car.phase == "idle"
    assert car.target is None
    assert item.params["queuedZ"] is None
    assert item.params["departOnCloseZ"] is None


@pytest.mark.parametrize(
    ("phase", "seconds", "reason"),
    [
        ("idle", None, None),
        ("moving", DEFAULT_TRAVEL_SECONDS, "The elevator is moving."),
        ("arriving", DOOR_OPEN_CLIP_SECONDS, "The elevator is moving."),
        ("opening", DOOR_OPEN_CLIP_SECONDS, "The elevator door is opening."),
        ("door_open", DEFAULT_DOOR_OPEN_SECONDS, None),
        ("closing", DOOR_CLOSE_CLIP_SECONDS, "The elevator door is closing."),
    ],
)
def test_phase_timing_and_door_block_reason(
    item: WorldItem, phase: Phase, seconds: float | None, reason: str | None
) -> None:
    """Each phase defines its wait and whether door passage is blocked."""

    item.params["state"] = phase
    car = ElevatorCar(item)
    assert car.phase_seconds() == seconds
    assert car.door_block_reason() == reason


@pytest.mark.parametrize(
    ("key", "phase"), [("doorOpenSeconds", "door_open"), ("travelSeconds", "moving")]
)
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7.5, 7.5),
        (1.25, 1.25),
        ("8.2", 8.2),
        (0, 0),
        (-1, 0),
        (301, 300),
        (None, 5),
        ("invalid", 5),
        (float("nan"), 5),
        (float("inf"), 5),
        (float("-inf"), 5),
    ],
)
def test_editable_durations_are_finite_and_clamped(
    item: WorldItem, key: str, phase: Phase, value: object, expected: float
) -> None:
    """Phase timing uses editable durations with safe defaults and bounds."""

    item.params.update({key: value, "state": phase})
    assert ElevatorCar(item).phase_seconds() == expected


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        (
            {"currentZ": 0, "state": "door_open", "doorOpen": True},
            "Elevator is on Ground floor, door open.",
        ),
        (
            {"currentZ": 40, "state": "idle", "doorOpen": False},
            "Elevator is on Second floor, door closed.",
        ),
        (
            {"currentZ": 0, "targetZ": 40, "state": "moving"},
            "Elevator is headed to Second floor, traveling up.",
        ),
        (
            {"currentZ": 40, "targetZ": 0, "state": "moving"},
            "Elevator is headed to Ground floor, traveling down.",
        ),
        (
            {"currentZ": 40, "state": "arriving"},
            "Elevator is on Second floor, door opening.",
        ),
        (
            {"currentZ": 0, "state": "opening"},
            "Elevator is on Ground floor, door opening.",
        ),
        (
            {"currentZ": 0, "state": "closing"},
            "Elevator is on Ground floor, door closing.",
        ),
        (
            {"currentZ": 80},
            "Elevator is on z 80, door closed.",
        ),
    ],
)
def test_describe(item: WorldItem, params: dict[str, object], expected: str) -> None:
    """Describe travel direction or the car's landing and door condition."""

    item.params.update(params)
    assert ElevatorCar(item).describe() == expected


@pytest.mark.parametrize(
    ("floors", "landing", "expected"),
    [([40, 0], 20, 0), ([80, 40], 20, 40), ([40, 0], 40, 40), ([], 20, 0)],
)
def test_reset_to_landing_repairs_unfinished_trip(
    item: WorldItem, floors: list[int], landing: int, expected: int
) -> None:
    """Startup snaps off-floor cars to a landing and clears pending transitions."""

    item.z = 20
    item.params.update(
        floorZs=floors,
        currentZ=landing,
        targetZ=40,
        queuedZ=0,
        departOnCloseZ=40,
        state="moving",
        doorOpen=True,
        travelSeconds=7.5,
    )
    del item.params["doorOpenSeconds"]
    car = ElevatorCar(item)
    car.reset_to_landing(DEFAULT_PARAMS)
    assert (car.landing, car.phase, car.target, car.door_open) == (
        expected,
        "idle",
        None,
        False,
    )
    assert item.params["queuedZ"] is None
    assert item.params["departOnCloseZ"] is None
    assert car.door_open_seconds == DEFAULT_DOOR_OPEN_SECONDS
    assert car.travel_seconds == 7.5
    assert item.z == 0
    restored = item.model_dump()
    car.reset_to_landing(DEFAULT_PARAMS)
    assert item.model_dump() == restored


def test_defaults_and_live_item_view(item: WorldItem) -> None:
    """Missing parameters use defaults and existing views follow live changes."""

    item.params.clear()
    car = ElevatorCar(item)
    assert (car.phase, car.landing, car.target, car.door_open) == (
        "idle",
        0,
        None,
        False,
    )
    assert car.floors == [0, 40]
    assert car.door_open_seconds == DEFAULT_DOOR_OPEN_SECONDS
    assert car.travel_seconds == DEFAULT_TRAVEL_SECONDS
    car.place_at(40)
    assert car.landing == 40
    assert ElevatorCar(item).landing == 40
    item.params["floorZs"] = [80, "not a floor", 40, 0]
    assert car.floors == [0, 40, 80]
    assert car.other_floor(0) == 40
    assert car.other_floor(40) == 0
    item.params["state"] = "moving"
    assert car.advance() == "arrived"
    assert car.landing == 40


@pytest.mark.parametrize(
    ("z", "supported", "name"),
    [(0, True, "Ground floor"), (40, True, "Second floor"), (20, False, "z 20")],
)
def test_world_floor_names(z: int, supported: bool, name: str) -> None:
    """Floor recognition and descriptions share the world definitions."""

    assert is_floor(z) is supported
    assert floor_name(z) == name
