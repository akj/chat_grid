"""Floor, footprint, and elevator state-machine tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.item_service import ItemService
from app.items.types.elevator.validator import validate_update

from app.models import (
    BroadcastPositionPacket,
    ItemActionResultPacket,
    ItemElevatorStatusPacket,
    ItemUpsertPacket,
    ItemUseSoundPacket,
)
from app.items.types.elevator.car import (
    DOOR_CLOSE_CLIP_SECONDS,
    DEFAULT_DOOR_OPEN_SECONDS,
    DOOR_OPEN_CLIP_SECONDS,
)


def test_elevator_single_square_occupies_both_floors(make_world) -> None:
    """One elevator entity should expose its anchor square on both landings."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, z=0, client_id="u1")
    elevator = server.item_service.default_item(client, "elevator")
    elevator.z = 0

    assert server.item_runtime.item_is_on_client_square(elevator, client)
    client.z = 40
    assert server.item_runtime.item_is_on_client_square(elevator, client)
    client.x = 11
    assert not server.item_runtime.item_is_on_client_square(elevator, client)


def test_same_xy_on_another_floor_is_not_the_same_item_square(make_world) -> None:
    """Ordinary item occupancy should compare height as well as x and y."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, z=40, client_id="u1")
    item = server.item_service.default_item(client, "dice")
    item.z = 0

    assert not server.item_runtime.item_is_on_client_square(item, client)


def test_elevator_timing_and_emitter_are_editable_but_runtime_state_is_not(
    make_world,
) -> None:
    """Elevator edits should accept timing/audio without exposing runtime state."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, z=0, client_id="u1")
    elevator = server.item_service.default_item(client, "elevator")

    params = validate_update(
        elevator,
        {
            **elevator.params,
            "directional": True,
            "facing": 123.4,
            "doorOpenSeconds": 8.25,
            "travelSeconds": 12.34,
            "emitRange": 12,
            "emitVolume": 65,
            "emitSoundSpeed": 40.5,
            "emitSoundTempo": 62.5,
            "emitInitialDelay": 1.2,
            "emitLoopDelay": 3.4,
            "emitEffect": "echo",
            "emitEffectValue": 44.4,
            "emitSound": "elevator_motor.ogg",
            "state": "moving",
        },
    )

    assert {
        key: params[key]
        for key in (
            "directional",
            "facing",
            "doorOpenSeconds",
            "travelSeconds",
            "emitRange",
            "emitVolume",
            "emitSoundSpeed",
            "emitSoundTempo",
            "emitInitialDelay",
            "emitLoopDelay",
            "emitEffect",
            "emitEffectValue",
            "emitSound",
        )
    } == {
        "directional": True,
        "facing": 123,
        "doorOpenSeconds": 8.2,
        "travelSeconds": 12.3,
        "emitRange": 12,
        "emitVolume": 65,
        "emitSoundSpeed": 40.5,
        "emitSoundTempo": 62.5,
        "emitInitialDelay": 1.2,
        "emitLoopDelay": 3.4,
        "emitEffect": "echo",
        "emitEffectValue": 44.4,
        "emitSound": "sounds/elevator_motor.ogg",
    }
    assert params["state"] == "idle"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("doorOpenSeconds", -0.1),
        ("doorOpenSeconds", 300.1),
        ("travelSeconds", "not a number"),
    ],
)
def test_elevator_rejects_invalid_editable_durations(
    make_world, key: str, value: object
) -> None:
    """Editable elevator timing must remain finite and within its UI range."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, z=0, client_id="u1")
    elevator = server.item_service.default_item(client, "elevator")

    with pytest.raises(ValueError):
        validate_update(elevator, {**elevator.params, key: value})


@pytest.mark.asyncio
async def test_elevator_opens_then_second_use_enters(
    make_world,
) -> None:
    """A present elevator should reject boarding until its opening sound ends."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=10, y=10, z=0, client_id="u1")
    elevator = server.item_service.default_item(client, "elevator")
    elevator.z = 0
    server.item_service.add_item(elevator)

    await server.item_runtime.elevator.use(client, elevator)
    assert elevator.params["state"] == "opening"
    assert elevator.params["doorOpen"] is False
    assert client.elevator_id is None
    door_packets = transport.packets_of_type(observer, ItemUseSoundPacket)
    door_sounds = [packet.sound for packet in door_packets]
    assert door_sounds == [
        "/sounds/elevator_up.ogg",
        "/sounds/elevator_open.ogg",
    ]
    assert all(packet.acousticZoneId == "floor:0" for packet in door_packets)
    opening_index = next(
        index
        for index, packet in enumerate(transport.packets_to(observer))
        if isinstance(packet, ItemUpsertPacket)
        and packet.item.params["state"] == "opening"
    )
    first_sound_index = next(
        index
        for index, packet in enumerate(transport.packets_to(observer))
        if isinstance(packet, ItemUseSoundPacket)
    )
    assert opening_index < first_sound_index

    await server.item_runtime.elevator.use(client, elevator)
    assert client.elevator_id is None
    result = transport.last_packet_of_type(client, ItemActionResultPacket)
    assert result.message == "The elevator door is opening."

    elevator.params["state"] = "closing"
    await server.item_runtime.elevator.use(client, elevator)
    assert client.elevator_id is None
    result = transport.last_packet_of_type(client, ItemActionResultPacket)
    assert result.message == "The elevator door is closing."

    elevator.params["state"] = "door_open"
    elevator.params["doorOpen"] = True
    elevator.params["doorOpenSeconds"] = 8.2
    await server.item_runtime.elevator.use(client, elevator)
    assert client.elevator_id == elevator.id
    assert elevator.params["departOnCloseZ"] == 40
    assert any(
        packet.event == "entered"
        for packet in transport.packets_of_type(client, ItemElevatorStatusPacket)
    )
    presence = transport.last_packet_of_type(observer, BroadcastPositionPacket)
    assert presence.acousticZoneId == f"elevator:{elevator.id}"
    result = transport.last_packet_of_type(client, ItemActionResultPacket)
    assert result.message == "You enter Elevator. The door will close in 8.2 seconds."

    await server.item_runtime.elevator.use(client, elevator)
    assert client.elevator_id is None
    exit_presence = transport.last_packet_of_type(observer, BroadcastPositionPacket)
    assert exit_presence.acousticZoneId == "floor:0"

    await server.item_runtime.elevator.cancel(elevator.id)


@pytest.mark.asyncio
async def test_absent_elevator_is_called_and_moving_calls_are_queued(
    make_world,
) -> None:
    """A landing use should call an absent car and queue a later opposite call."""

    world = make_world(grid_size=41)
    server = world.server
    ground_client = world.join("ground", x=10, y=10, z=0, client_id="ground")
    second_client = world.join("second", x=10, y=10, z=40, client_id="second")
    elevator = server.item_service.default_item(ground_client, "elevator")
    elevator.params["currentZ"] = 40
    server.item_service.add_item(elevator)

    await server.item_runtime.elevator.use(ground_client, elevator)
    assert elevator.params["state"] == "moving"
    assert elevator.params["targetZ"] == 0
    assert ground_client.elevator_id is None

    await server.item_runtime.elevator.use(second_client, elevator)
    assert elevator.params["queuedZ"] == 40

    await server.item_runtime.elevator.cancel(elevator.id)


@pytest.mark.asyncio
async def test_opposite_landing_call_is_queued_while_door_is_closing(
    make_world,
) -> None:
    """A closing door should block traversal without dropping an away-floor call."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join("tester", x=10, y=10, z=40, client_id="u1")
    elevator = server.item_service.default_item(client, "elevator")
    elevator.params.update({"currentZ": 0, "state": "closing", "doorOpen": False})
    server.item_service.add_item(elevator)

    await server.item_runtime.elevator.use(client, elevator)

    assert client.elevator_id is None
    assert elevator.params["state"] == "closing"
    assert elevator.params["queuedZ"] == 40
    result = transport.packets_of_type(client, ItemActionResultPacket)[0]
    assert result.message == "You call Elevator."


@pytest.mark.asyncio
async def test_multiple_elevators_keep_independent_state(
    make_world,
) -> None:
    """Using one elevator must not mutate another elevator object."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, z=0, client_id="u1")
    first = server.item_service.default_item(client, "elevator")
    second = server.item_service.default_item(client, "elevator")
    second.x = 20
    second.y = 20
    server.item_service.add_item(first)
    server.item_service.add_item(second)

    await server.item_runtime.elevator.use(client, first)

    assert first.params["state"] == "opening"
    assert first.params["doorOpen"] is False
    assert second.params["doorOpen"] is False
    assert second.params["state"] == "idle"

    await server.item_runtime.elevator.cancel(first.id)


@pytest.mark.asyncio
async def test_elevator_arrival_moves_rider_and_carried_item(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arrival should move the rider and held item to the destination floor."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join(
        "tester", x=10, y=10, z=0, elevator_id="elevator-1", client_id="u1"
    )
    elevator = server.item_service.default_item(client, "elevator")
    elevator.id = "elevator-1"
    elevator.z = 0
    elevator.params.update(
        {
            "currentZ": 0,
            "targetZ": 40,
            "state": "moving",
            "doorOpen": False,
            "departOnCloseZ": None,
            "queuedZ": None,
        }
    )
    server.item_service.add_item(elevator)
    carried = server.item_service.default_item(client, "dice")
    carried.carrierId = client.id
    server.item_service.add_item(carried)

    sleeps: list[tuple[float, str]] = []

    async def immediate_sleep(seconds: float) -> None:
        sleeps.append((seconds, str(elevator.params["state"])))
        return None

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)

    await server.item_runtime.elevator.run_cycle(elevator.id)

    assert elevator.params["currentZ"] == 40
    assert elevator.params["state"] == "idle"
    assert client.z == 40
    assert client.elevator_id == elevator.id
    assert carried.z == 40
    arrival = next(
        packet
        for packet in transport.packets_of_type(client, ItemElevatorStatusPacket)
        if packet.event == "arrived"
    )
    assert arrival.message == "Elevator arrives on Second floor. The door opens."
    elevator_sounds = transport.packets_of_type(observer, ItemUseSoundPacket)
    assert [packet.sound for packet in elevator_sounds] == [
        "/sounds/elevator_down.ogg",
        "/sounds/elevator_open.ogg",
        "/sounds/elevator_close.ogg",
    ]
    assert (DOOR_OPEN_CLIP_SECONDS, "arriving") in sleeps
    assert (DEFAULT_DOOR_OPEN_SECONDS, "door_open") in sleeps
    assert (DOOR_CLOSE_CLIP_SECONDS, "closing") in sleeps
    close_index = sleeps.index((DOOR_CLOSE_CLIP_SECONDS, "closing"))
    assert all(state == "moving" for _, state in sleeps[close_index + 1 :])
    opening_sound = elevator_sounds[1]
    assert (opening_sound.x, opening_sound.y, opening_sound.z) == (10, 10, 40)
    assert opening_sound.acousticZoneId == "floor:40"


@pytest.mark.asyncio
async def test_elevator_door_sound_announces_next_upward_trip(
    make_world,
) -> None:
    """A ground-floor door opening should announce the next upward trip."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=10, y=10, z=40, client_id="u1")
    elevator = server.item_service.default_item(client, "elevator")

    await server.item_runtime.elevator.broadcast_direction_sound(elevator, 0)

    arrival_sound = transport.packets_of_type(observer, ItemUseSoundPacket)[0]
    assert arrival_sound.sound == "/sounds/elevator_up.ogg"
    assert arrival_sound.z == 0
    assert arrival_sound.acousticZoneId == "floor:0"


@pytest.mark.asyncio
async def test_stopped_rider_waits_for_reopened_door_before_exiting(
    make_world,
) -> None:
    """A rider should remain inside until a stopped car finishes reopening."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join(
        "tester", x=10, y=10, z=40, elevator_id="elevator-1", client_id="u1"
    )
    elevator = server.item_service.default_item(client, "elevator")
    elevator.id = "elevator-1"
    elevator.params["currentZ"] = 40
    server.item_service.add_item(elevator)

    await server.item_runtime.elevator.use(client, elevator)

    assert client.elevator_id == elevator.id
    assert elevator.params["state"] == "opening"
    assert elevator.params["doorOpen"] is False
    assert not any(
        packet.event == "exited"
        for packet in transport.packets_of_type(client, ItemElevatorStatusPacket)
    )
    door_sounds = [
        packet.sound
        for packet in transport.packets_of_type(observer, ItemUseSoundPacket)
    ]
    assert door_sounds == [
        "/sounds/elevator_down.ogg",
        "/sounds/elevator_open.ogg",
    ]

    await server.item_runtime.elevator.use(client, elevator)
    assert client.elevator_id == elevator.id

    elevator.params["state"] = "door_open"
    elevator.params["doorOpen"] = True
    await server.item_runtime.elevator.use(client, elevator)
    assert client.elevator_id is None
    assert any(
        packet.event == "exited"
        for packet in transport.packets_of_type(client, ItemElevatorStatusPacket)
    )

    await server.item_runtime.elevator.cancel(elevator.id)


@pytest.mark.asyncio
async def test_rider_cannot_exit_while_elevator_is_moving(
    make_world,
) -> None:
    """A rider must remain in the car until it reaches a landing."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join(
        "tester", x=10, y=10, z=20, elevator_id="elevator-1", client_id="u1"
    )
    elevator = server.item_service.default_item(client, "elevator")
    elevator.id = "elevator-1"
    elevator.params.update({"state": "moving", "targetZ": 40})
    server.item_service.add_item(elevator)

    await server.item_runtime.elevator.use(client, elevator)

    assert client.elevator_id == elevator.id
    assert not any(
        packet.event == "exited"
        for packet in transport.packets_of_type(client, ItemElevatorStatusPacket)
    )
    result = transport.packets_of_type(client, ItemActionResultPacket)[0]
    assert result.message == "The elevator is moving."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin_z", "destination_z", "first_height", "last_height"),
    [(0, 40, 1, 38), (40, 0, 39, 2)],
)
async def test_elevator_travel_height_progresses_between_acoustic_floors(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
    origin_z: int,
    destination_z: int,
    first_height: int,
    last_height: int,
) -> None:
    """Travel should progressively move riders without publishing either landing."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join(
        "tester", x=10, y=10, z=origin_z, elevator_id="elevator-1", client_id="u1"
    )
    elevator = server.item_service.default_item(client, "elevator")
    elevator.id = "elevator-1"
    elevator.params["currentZ"] = origin_z
    server.item_service.add_item(elevator)

    async def immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)

    await server.item_runtime.elevator.advance_travel(elevator, origin_z, destination_z)

    heights = [
        packet.z
        for packet in transport.packets_of_type(observer, BroadcastPositionPacket)
    ]
    assert heights[0] == first_height
    assert heights[-1] == last_height
    assert len(set(heights)) == len(heights)
    assert all(
        next_height > height if destination_z > origin_z else next_height < height
        for height, next_height in zip(heights, heights[1:])
    )
    assert len(heights) > 2
    assert client.z == heights[-1]
    status_heights = [
        packet.z
        for packet in transport.packets_of_type(client, ItemElevatorStatusPacket)
        if packet.event == "moving"
    ]
    assert status_heights == heights
    assert [
        packet.z
        for packet in transport.packets_of_type(client, BroadcastPositionPacket)
    ] == heights
    assert not transport.packets_of_type(observer, ItemUpsertPacket)
    assert elevator.z == origin_z


def test_disconnecting_rider_returns_to_last_landing(make_world) -> None:
    """An unfinished trip must never persist an intermediate elevator height."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join(
        "tester", x=10, y=10, z=20, elevator_id="elevator-1", client_id="u1"
    )
    elevator = server.item_service.default_item(client, "elevator")
    elevator.id = "elevator-1"
    elevator.params["currentZ"] = 0
    elevator.params["state"] = "moving"
    server.item_service.add_item(elevator)

    server.item_runtime.elevator.restore_rider_to_landing(client)

    assert (client.x, client.y, client.z) == (10, 10, 0)
    assert client.elevator_id is None


def test_persisted_elevator_resets_to_resting_state(tmp_path: Path) -> None:
    """A restart should not leave an elevator stuck in an unfinished timer state."""

    state_file = tmp_path / "items.json"
    state_file.write_text(
        json.dumps(
            [
                {
                    "id": "elevator-1",
                    "type": "elevator",
                    "title": "Elevator",
                    "x": 10,
                    "y": 10,
                    "z": 20,
                    "createdBy": "u1",
                    "createdAt": 1,
                    "updatedAt": 2,
                    "version": 3,
                    "params": {
                        "floorZs": [0, 40],
                        "currentZ": 0,
                        "targetZ": 40,
                        "queuedZ": 0,
                        "departOnCloseZ": 40,
                        "state": "moving",
                        "doorOpen": False,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    elevator = ItemService(state_file=state_file).items["elevator-1"]

    assert elevator.params["currentZ"] == 0
    assert elevator.params["targetZ"] is None
    assert elevator.params["queuedZ"] is None
    assert elevator.params["departOnCloseZ"] is None
    assert elevator.params["state"] == "idle"
    assert elevator.params["doorOpen"] is False
    assert elevator.params["doorOpenSeconds"] == 5
    assert elevator.params["travelSeconds"] == 5
    assert elevator.z == 0


def test_persisted_transient_carried_item_is_dropped_safely(tmp_path: Path) -> None:
    """Connection-scoped carrying state must not survive a process restart."""

    state_file = tmp_path / "items.json"
    state_file.write_text(
        json.dumps(
            [
                {
                    "id": "dice-1",
                    "type": "dice",
                    "title": "Dice",
                    "x": 10,
                    "y": 10,
                    "z": 20,
                    "createdBy": "u1",
                    "createdAt": 1,
                    "updatedAt": 2,
                    "version": 3,
                    "params": {"sides": 6, "number": 2},
                    "carrierId": "old-connection-id",
                }
            ]
        ),
        encoding="utf-8",
    )

    item = ItemService(state_file=state_file).items["dice-1"]

    assert item.carrierId is None
    assert item.z == 0
