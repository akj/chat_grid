from __future__ import annotations

import json

import pytest

from app.models import (
    BroadcastChatMessagePacket,
    BroadcastNicknamePacket,
    NicknameResultPacket,
)

from .conftest import World


@pytest.mark.asyncio
async def test_taken_nickname_is_rejected(world: World) -> None:
    server, transport = world.server, world.transport
    observer = world.join("Jage")
    client = world.join("Alice")

    await server._handle_message(
        client, json.dumps({"type": "update_nickname", "nickname": " JAGE "})
    )

    result = transport.last_packet_of_type(client, NicknameResultPacket)
    assert not result.accepted
    assert result.reason == "Nickname already in use."
    assert result.requestedNickname == "JAGE"
    assert result.effectiveNickname == client.nickname == "Alice"
    assert transport.packets_to(client) == [result]
    assert transport.packets_to(observer) == []


@pytest.mark.asyncio
async def test_same_nickname_same_case_is_noop(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("Jage", client_id="1")

    await server._handle_message(
        client, json.dumps({"type": "update_nickname", "nickname": "Jage"})
    )

    assert client.nickname == "Jage"
    assert transport.packets_to(observer) == []
    assert any(
        packet.accepted and packet.effectiveNickname == "Jage"
        for packet in transport.packets_of_type(client, NicknameResultPacket)
    )


@pytest.mark.asyncio
async def test_case_only_change_is_allowed_and_broadcast(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("jage", client_id="1")

    await server._handle_message(
        client, json.dumps({"type": "update_nickname", "nickname": "Jage"})
    )

    assert client.nickname == "Jage"
    assert any(
        packet.accepted and packet.effectiveNickname == "Jage"
        for packet in transport.packets_of_type(client, NicknameResultPacket)
    )
    nickname = transport.last_packet_of_type(observer, BroadcastNicknamePacket)
    assert (nickname.id, nickname.nickname) == (client.id, "Jage")
    assert [
        packet.message
        for packet in transport.packets_of_type(observer, BroadcastChatMessagePacket)
    ] == ["jage is now known as Jage."]
    assert transport.packets_of_type(client, BroadcastNicknamePacket) == []
    assert [
        packet.message
        for packet in transport.packets_of_type(client, BroadcastChatMessagePacket)
    ] == ["You are now known as Jage."]
