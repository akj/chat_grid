"""Server behavior when a persistence step fails mid-flow."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from app.models import (
    BroadcastChatMessagePacket,
    BroadcastPositionPacket,
    ItemUpsertPacket,
    UserLeftPacket,
)


class _ClosedWebsocket:
    """Websocket whose peer closes before sending any message."""

    def __init__(self, path: str) -> None:
        self.request = SimpleNamespace(path=path, headers={})
        self.remote_address = ("203.0.113.5", 5000)

    def __aiter__(self) -> _ClosedWebsocket:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


def _fail_position_write(*_args: object, **_kwargs: object) -> None:
    raise sqlite3.OperationalError("disk I/O error")


async def test_elevator_failure_returns_car_to_idle(
    make_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed step must not leave the car stuck in a moving or open state."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    rider = world.join("rider", x=10, y=10, z=0, elevator_id="elevator-1")
    second = world.join("second", x=10, y=10, z=0, elevator_id="elevator-1")
    elevator = server.item_service.default_item(rider, "elevator")
    elevator.id = "elevator-1"
    elevator.params.update({"currentZ": 0, "targetZ": 40, "state": "moving"})
    server.item_service.add_item(elevator)
    carried = server.item_service.default_item(second, "dice")
    carried.carrierId = second.id
    server.item_service.add_item(carried)

    async def immediate_sleep(_seconds: float) -> None:
        return None

    failed = False
    first_after_failure: list[tuple[str, int]] = []

    def fail_position_write(*_args: object, **_kwargs: object) -> None:
        nonlocal failed
        failed = True
        _fail_position_write()

    record = transport.deliver

    async def deliver(client, packet) -> None:
        # Recovery yields at its first send; a use() there restarts the cycle,
        # so the car and riders must already be settled.
        if failed and not first_after_failure:
            first_after_failure.append((elevator.params["state"], second.z))
        await record(client, packet)

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(server.auth_service, "set_last_position", fail_position_write)
    monkeypatch.setattr(transport, "deliver", deliver)

    await server.item_runtime.elevator.run_cycle(elevator.id)

    assert first_after_failure == [("idle", 40)]

    assert elevator.params["state"] == "idle"
    assert elevator.params["doorOpen"] is False
    assert elevator.params["currentZ"] == 40
    last = [
        packet
        for packet in transport.packets_of_type(observer, ItemUpsertPacket)
        if packet.item.id == elevator.id
    ][-1]
    assert last.item.params["state"] == "idle"
    assert (rider.z, second.z, carried.z) == (40, 40, 40)
    second_positions = [
        packet
        for packet in transport.packets_of_type(observer, BroadcastPositionPacket)
        if packet.id == second.id
    ]
    assert second_positions[-1].z == 40


async def test_disconnect_announces_departure_when_cleanup_fails(
    make_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed position save must not skip later cleanup or the departure notice."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    websocket = _ClosedWebsocket(server.websocket_path)
    leaving = world.connect("leaving")
    leaving.websocket = websocket  # type: ignore[assignment]
    leaving.authenticated = True
    leaving.user_id = "42"
    server.clients[websocket] = leaving  # type: ignore[index]
    carried = server.item_service.default_item(leaving, "dice")
    carried.carrierId = leaving.id
    server.item_service.add_item(carried)
    monkeypatch.setattr(server.auth_service, "set_last_position", _fail_position_write)
    last_seen_users: list[str] = []
    monkeypatch.setattr(
        server.auth_service,
        "touch_last_seen",
        lambda user_id, *_args, **_kwargs: last_seen_users.append(user_id),
    )

    await server._handle_client(websocket)  # type: ignore[arg-type]

    left = transport.packets_of_type(observer, UserLeftPacket)
    assert [packet.id for packet in left] == [leaving.id]
    chat = transport.packets_of_type(observer, BroadcastChatMessagePacket)
    assert chat[-1].message == "leaving has logged out."
    assert carried.carrierId is None
    assert last_seen_users == ["42"]


async def test_reopening_during_recovery_keeps_rider_broadcasts(
    make_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rider opening the door mid-recovery must not cancel position updates."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    rider = world.join("rider", x=10, y=10, z=0, elevator_id="elevator-1")
    second = world.join("second", x=10, y=10, z=0, elevator_id="elevator-1")
    elevator = server.item_service.default_item(rider, "elevator")
    elevator.id = "elevator-1"
    elevator.params.update({"currentZ": 0, "targetZ": 40, "state": "moving"})
    server.item_service.add_item(elevator)
    runtime = server.item_runtime.elevator

    async def immediate_sleep(_seconds: float) -> None:
        return None

    failed = False

    def fail_once(*_args: object, **_kwargs: object) -> None:
        nonlocal failed
        if not failed:
            failed = True
            _fail_position_write()

    record = transport.deliver
    reopened = False

    async def deliver(client, packet) -> None:
        nonlocal reopened
        if failed and not reopened:
            reopened = True
            await asyncio.create_task(runtime.use(rider, elevator))
        await record(client, packet)

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(server.auth_service, "set_last_position", fail_once)
    monkeypatch.setattr(transport, "deliver", deliver)

    runtime._restart_task(elevator.id)
    await asyncio.gather(*list(runtime._tasks.values()), return_exceptions=True)
    while runtime._tasks:
        await asyncio.gather(*list(runtime._tasks.values()), return_exceptions=True)

    assert reopened
    second_positions = [
        packet.z
        for packet in transport.packets_of_type(observer, BroadcastPositionPacket)
        if packet.id == second.id
    ]
    assert second_positions[-1] == 40


async def test_recovery_skips_items_and_riders_gone_mid_broadcast(
    make_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not resurrect an item or rider removed while it awaits."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    rider = world.join("rider", x=10, y=10, z=0, elevator_id="elevator-1")
    second = world.join("second", x=10, y=10, z=0, elevator_id="elevator-1")
    elevator = server.item_service.default_item(rider, "elevator")
    elevator.id = "elevator-1"
    elevator.params.update({"currentZ": 0, "targetZ": 40, "state": "moving"})
    server.item_service.add_item(elevator)
    carried = server.item_service.default_item(second, "dice")
    carried.carrierId = second.id
    server.item_service.add_item(carried)

    async def immediate_sleep(_seconds: float) -> None:
        return None

    failed = False

    def fail_position_write(*_args: object, **_kwargs: object) -> None:
        nonlocal failed
        failed = True
        _fail_position_write()

    record = transport.deliver
    removed_at: int | None = None

    async def deliver(client, packet) -> None:
        nonlocal removed_at
        if failed and removed_at is None:
            server.item_service.items.pop(carried.id)
            server.clients.pop(second.websocket)
            removed_at = len(transport.packets_to(observer))
        await record(client, packet)

    monkeypatch.setattr(asyncio, "sleep", immediate_sleep)
    monkeypatch.setattr(server.auth_service, "set_last_position", fail_position_write)
    monkeypatch.setattr(transport, "deliver", deliver)

    await server.item_runtime.elevator.run_cycle(elevator.id)

    assert removed_at is not None
    after = transport.packets_to(observer)[removed_at:]
    assert not [
        packet
        for packet in after
        if isinstance(packet, ItemUpsertPacket) and packet.item.id == carried.id
    ]
    assert not [
        packet
        for packet in after
        if isinstance(packet, BroadcastPositionPacket) and packet.id == second.id
    ]
