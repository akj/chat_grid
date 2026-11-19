from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.config import RateLimitsConfigSection
from app.models import (
    BroadcastChatMessagePacket,
    ItemActionResultPacket,
    ItemPianoNoteBroadcastPacket,
)

from .conftest import World


class Clock:
    """Mutable stand-in for `item_service.now_ms`."""

    def __init__(self, now_ms: int) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


@pytest.fixture
def clock(make_world, monkeypatch: pytest.MonkeyPatch):
    def install(world: World, now_ms: int = 100_000) -> Clock:
        fake = Clock(now_ms)
        monkeypatch.setattr(world.server.item_service, "now_ms", fake)
        return fake

    return install


async def send_chat(world: World, client, message: str) -> None:
    await world.server._handle_message(
        client, json.dumps({"type": "chat_message", "message": message})
    )


def user_chat(world: World, client) -> list[str]:
    return [
        packet.message
        for packet in world.transport.packets_of_type(
            client, BroadcastChatMessagePacket
        )
        if not packet.system
    ]


def system_chat(world: World, client) -> list[str]:
    return [
        packet.message
        for packet in world.transport.packets_of_type(
            client, BroadcastChatMessagePacket
        )
        if packet.system
    ]


@pytest.mark.asyncio
async def test_chat_default_limit_is_five_per_five_seconds(make_world, clock) -> None:
    world = make_world()
    now = clock(world)
    sender = world.join("sender")
    observer = world.join("observer")

    for index in range(5):
        await send_chat(world, sender, f"hello {index}")
    await send_chat(world, sender, "too many")

    assert user_chat(world, observer) == [f"hello {index}" for index in range(5)]
    assert system_chat(world, sender) == [
        "You are sending messages too quickly. Please wait a moment."
    ]
    assert system_chat(world, observer) == []

    now.now_ms += 5_000
    await send_chat(world, sender, "next window")

    assert user_chat(world, observer)[-1] == "next window"


@pytest.mark.asyncio
async def test_piano_note_ons_over_limit_are_dropped_but_releases_pass(
    make_world, clock
) -> None:
    world = make_world(
        rate_limits=RateLimitsConfigSection(piano_notes=4, piano_window_ms=1_000)
    )
    now = clock(world)
    sender = world.join("sender", x=5, y=6)
    listener = world.join("listener", x=6, y=6)
    piano = world.server.item_service.default_item(sender, "piano")
    world.server.item_service.add_item(piano)

    async def note(key_id: str, on: bool) -> None:
        await world.server._handle_message(
            sender,
            json.dumps(
                {
                    "type": "item_piano_note",
                    "itemId": piano.id,
                    "keyId": key_id,
                    "midi": 60,
                    "on": on,
                }
            ),
        )

    for key_id in ("KeyA", "KeyS", "KeyD"):
        await note(key_id, True)
        await note(key_id, False)
    await note("KeyF", True)
    await note("KeyG", True)
    await note("KeyQ", False)
    await note("KeyF", False)

    heard = world.transport.packets_of_type(listener, ItemPianoNoteBroadcastPacket)
    assert [(packet.keyId, packet.on) for packet in heard] == [
        ("KeyA", True),
        ("KeyA", False),
        ("KeyS", True),
        ("KeyS", False),
        ("KeyD", True),
        ("KeyD", False),
        ("KeyF", True),
        ("KeyF", False),
    ]
    assert world.transport.packets_to(sender) == []

    now.now_ms += 1_000
    await note("KeyG", False)
    for key_id in ("KeyH", "KeyJ", "KeyK", "KeyL"):
        await note(key_id, True)

    heard = world.transport.packets_of_type(listener, ItemPianoNoteBroadcastPacket)
    assert [(packet.keyId, packet.on) for packet in heard[8:]] == [
        ("KeyH", True),
        ("KeyJ", True),
        ("KeyK", True),
        ("KeyL", True),
    ]


@pytest.mark.asyncio
async def test_item_add_and_delete_share_one_limit(make_world, clock) -> None:
    world = make_world(
        rate_limits=RateLimitsConfigSection(item_changes=2, item_window_ms=10_000)
    )
    now = clock(world)
    client = world.join("builder", x=5, y=6)
    server = world.server

    await server._handle_message(
        client, json.dumps({"type": "item_add", "itemType": "dice"})
    )
    item_id = world.transport.last_packet_of_type(client, ItemActionResultPacket).itemId
    assert item_id is not None
    await server._handle_message(
        client, json.dumps({"type": "item_add", "itemType": "dice"})
    )
    await server._handle_message(
        client, json.dumps({"type": "item_add", "itemType": "dice"})
    )
    await server._handle_message(
        client, json.dumps({"type": "item_delete", "itemId": item_id})
    )

    results = world.transport.packets_of_type(client, ItemActionResultPacket)
    assert [
        (result.action, result.ok, result.message, result.itemId)
        for result in results[2:]
    ] == [
        (
            "add",
            False,
            "You are changing items too quickly. Please wait a moment.",
            None,
        ),
        (
            "delete",
            False,
            "You are changing items too quickly. Please wait a moment.",
            item_id,
        ),
    ]
    assert len(server.item_service.items) == 2

    now.now_ms += 10_000
    await server._handle_message(
        client, json.dumps({"type": "item_delete", "itemId": item_id})
    )

    result = world.transport.last_packet_of_type(client, ItemActionResultPacket)
    assert (result.action, result.ok) == ("delete", True)
    assert item_id not in server.item_service.items


@pytest.mark.asyncio
async def test_item_updates_have_their_own_limit(make_world, clock) -> None:
    world = make_world(
        rate_limits=RateLimitsConfigSection(
            item_changes=1, item_updates=2, item_update_window_ms=1_000
        )
    )
    now = clock(world)
    client = world.join("builder", x=5, y=6)
    server = world.server
    await server._handle_message(
        client, json.dumps({"type": "item_add", "itemType": "dice"})
    )
    item_id = world.transport.last_packet_of_type(client, ItemActionResultPacket).itemId

    for title in ("One", "Two", "Three"):
        await server._handle_message(
            client,
            json.dumps({"type": "item_update", "itemId": item_id, "title": title}),
        )

    results = world.transport.packets_of_type(client, ItemActionResultPacket)
    assert [(result.action, result.ok) for result in results] == [
        ("add", True),
        ("update", True),
        ("update", True),
        ("update", False),
    ]
    assert results[-1].message == (
        "You are changing items too quickly. Please wait a moment."
    )
    assert server.item_service.items[item_id].title == "Two"

    now.now_ms += 1_000
    await server._handle_message(
        client,
        json.dumps({"type": "item_update", "itemId": item_id, "title": "Four"}),
    )
    assert server.item_service.items[item_id].title == "Four"


@pytest.mark.asyncio
async def test_item_add_rejected_at_per_user_item_cap(make_world) -> None:
    world = make_world(max_items_per_user=2)
    server = world.server
    owner = world.join("owner", x=5, y=6, user_id="owner-user")
    other = world.join("other", x=5, y=6, user_id="other-user")
    for creator in (owner, owner, other):
        server.item_service.add_item(server.item_service.default_item(creator, "dice"))

    await server._handle_message(
        owner, json.dumps({"type": "item_add", "itemType": "dice"})
    )

    result = world.transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert (result.action, result.ok, result.message) == (
        "add",
        False,
        "You already own the maximum of 2 items. Delete one before placing another.",
    )
    assert len(server.item_service.items) == 3

    await server._handle_message(
        other, json.dumps({"type": "item_add", "itemType": "dice"})
    )

    result = world.transport.last_packet_of_type(other, ItemActionResultPacket)
    assert (result.action, result.ok) == ("add", True)
    assert len(server.item_service.items) == 4


@pytest.mark.asyncio
async def test_item_transfer_cannot_bypass_the_item_cap(make_world) -> None:
    world = make_world(max_items_per_user=1)
    server = world.server
    owner = world.join("owner", x=5, y=6, user_id="owner-user")
    full = world.join("full", x=9, y=9, user_id="full-user")
    server.item_service.add_item(server.item_service.default_item(full, "dice"))
    item = server.item_service.default_item(owner, "dice")
    item.x, item.y = owner.x, owner.y
    server.item_service.add_item(item)

    for target_user_id in ("no-such-user", "full-user"):
        await server._handle_message(
            owner,
            json.dumps(
                {
                    "type": "item_transfer",
                    "itemId": item.id,
                    "targetUserId": target_user_id,
                }
            ),
        )

    results = world.transport.packets_of_type(owner, ItemActionResultPacket)
    assert [(result.action, result.ok, result.message) for result in results] == [
        ("transfer", False, "Target user is not available."),
        ("transfer", False, "That user already owns the maximum of 1 items."),
    ]
    assert item.createdBy == "owner-user"


@pytest.mark.asyncio
async def test_item_transfer_counts_an_account_once_however_its_id_is_spelled(
    make_world,
) -> None:
    world = make_world(max_items_per_user=1)
    server = world.server
    owner = world.join("owner", x=5, y=6, user_id="owner-user")
    account_id = server.auth_service.register(
        f"full_{uuid4().hex[:8]}", "password123"
    ).user.id
    held = server.item_service.default_item(owner, "dice")
    held.createdBy = account_id
    server.item_service.add_item(held)
    item = server.item_service.default_item(owner, "dice")
    item.x, item.y = owner.x, owner.y
    server.item_service.add_item(item)

    await server._handle_message(
        owner,
        json.dumps(
            {
                "type": "item_transfer",
                "itemId": item.id,
                "targetUserId": f"0{account_id}",
            }
        ),
    )

    result = world.transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert (result.ok, result.message) == (
        False,
        "That user already owns the maximum of 1 items.",
    )
    assert item.createdBy == "owner-user"
