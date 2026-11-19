from __future__ import annotations

import asyncio
import json
from typing import cast

from websockets.asyncio.server import ServerConnection

from app.client import ClientConnection
from app.delivery import Delivery, RecordingTransport, WebsocketTransport
from app.models import BroadcastChatMessagePacket, PongPacket


def _client(client_id: str) -> ClientConnection:
    return ClientConnection(
        websocket=cast(ServerConnection, object()),
        id=client_id,
        nickname=client_id,
        x=20,
        y=20,
        z=0,
    )


def test_broadcast_excludes_one_client() -> None:
    first, excluded, last = (_client(name) for name in ("first", "excluded", "last"))
    transport = RecordingTransport()
    roster: dict[ServerConnection, ClientConnection] = {}
    delivery = Delivery(transport, roster)
    roster.update({client.websocket: client for client in (first, excluded, last)})
    packet = PongPacket(type="pong", clientSentAt=123)

    asyncio.run(delivery.broadcast(packet, exclude=excluded))

    assert transport.packets_to(first) == [packet]
    assert transport.packets_to(excluded) == []
    assert transport.packets_to(last) == [packet]
    assert transport.all_deliveries() == [(first, packet), (last, packet)]
    assert transport.last_packet_of_type(first, PongPacket) is packet


def test_broadcast_with_empty_roster() -> None:
    transport = RecordingTransport()
    delivery = Delivery(transport, {})

    asyncio.run(delivery.broadcast(PongPacket(type="pong", clientSentAt=123)))

    assert transport.all_deliveries() == []


def test_send_to_client_outside_roster() -> None:
    client = _client("outside")
    transport = RecordingTransport()
    delivery = Delivery(transport, {})
    first = PongPacket(type="pong", clientSentAt=123)
    message = BroadcastChatMessagePacket(type="chat_message", message="hello")
    last = PongPacket(type="pong", clientSentAt=456)

    async def scenario() -> None:
        await delivery.send(client, first)
        await delivery.send(client, message)
        await delivery.send(client, last)

    asyncio.run(scenario())

    assert transport.packets_to(client) == [first, message, last]
    assert transport.packets_of_type(client, PongPacket) == [first, last]
    assert transport.last_packet_of_type(client, PongPacket) is last
    assert transport.all_deliveries() == [
        (client, first),
        (client, message),
        (client, last),
    ]
    transport.clear()
    assert transport.packets_to(client) == []
    assert transport.all_deliveries() == []


def test_websocket_transport_swallows_failures_and_serializes_packets() -> None:
    class FailingSocket:
        async def send(self, data: str) -> None:
            raise RuntimeError("socket closed")

    class RecordingSocket:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, data: str) -> None:
            self.messages.append(data)

    failing = _client("failing")
    failing.websocket = cast(ServerConnection, FailingSocket())
    missing = _client("missing")
    missing.websocket = cast(ServerConnection, None)
    successful = _client("successful")
    socket = RecordingSocket()
    successful.websocket = cast(ServerConnection, socket)
    delivery = Delivery(WebsocketTransport(), {})
    packet = BroadcastChatMessagePacket(type="chat_message", message="hello")
    raw_packet = {"type": "custom", "value": 42}

    async def scenario() -> None:
        await delivery.send(failing, packet)
        await delivery.send(missing, packet)
        await delivery.send(successful, packet)
        await delivery.send(successful, raw_packet)

    asyncio.run(scenario())

    assert socket.messages == [
        json.dumps(packet.model_dump(exclude_none=True)),
        json.dumps(raw_packet),
    ]


def test_stalled_client_is_dropped_so_broadcasts_keep_flowing() -> None:
    class StalledSocket:
        def __init__(self) -> None:
            self.transport = self
            self.aborted = False

        async def send(self, data: str) -> None:
            await asyncio.Event().wait()

        def abort(self) -> None:
            self.aborted = True

    class RecordingSocket:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, data: str) -> None:
            self.messages.append(data)

    stalled = _client("stalled")
    stalled_socket = StalledSocket()
    stalled.websocket = cast(ServerConnection, stalled_socket)
    healthy = _client("healthy")
    healthy_socket = RecordingSocket()
    healthy.websocket = cast(ServerConnection, healthy_socket)
    roster = {
        cast(ServerConnection, object()): stalled,
        cast(ServerConnection, object()): healthy,
    }
    delivery = Delivery(WebsocketTransport(send_timeout_seconds=0.05), roster)

    async def scenario() -> None:
        for message in ("one", "two"):
            await asyncio.wait_for(
                delivery.broadcast({"type": "custom", "message": message}),
                timeout=1,
            )

    asyncio.run(scenario())

    assert stalled_socket.aborted is True
    assert [json.loads(raw)["message"] for raw in healthy_socket.messages] == [
        "one",
        "two",
    ]
