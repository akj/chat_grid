"""Deliver packet objects to individual clients or a shared client roster."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol, TypeVar

from websockets.asyncio.server import ServerConnection

from .client import ClientConnection

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


class Transport(Protocol):
    """Adapter that delivers one packet object to one client."""

    async def deliver(self, client: ClientConnection, packet: object) -> None:
        """Deliver a packet to the given client."""
        ...


class WebsocketTransport:
    """Serialize packets to websockets while isolating per-client failures."""

    def __init__(self, send_timeout_seconds: float = 5.0) -> None:
        """Set how long one send may wait for a client to accept data."""

        self.send_timeout_seconds = send_timeout_seconds

    async def deliver(self, client: ClientConnection, packet: object) -> None:
        """Send JSON, logging and ignoring missing sockets or send failures.

        A send that cannot finish within the timeout means the client has
        stopped reading. Its connection is aborted so that broadcasts, which
        wait for every recipient, keep flowing to everyone else.
        """

        if client.websocket is None:
            LOGGER.debug("send skipped: client %s has no websocket", client.id)
            return
        try:
            if hasattr(packet, "model_dump"):
                data = packet.model_dump(exclude_none=True)
            else:
                data = packet
            async with asyncio.timeout(self.send_timeout_seconds):
                await client.websocket.send(json.dumps(data))
        except TimeoutError:
            LOGGER.warning("send timed out; dropping stalled client %s", client.id)
            client.websocket.transport.abort()
        except Exception as exc:
            LOGGER.debug("send failure: %s", exc)


class RecordingTransport:
    """Record typed packet objects by recipient id and delivery order."""

    def __init__(self) -> None:
        """Start with no recorded deliveries."""

        self._packets: dict[str, list[object]] = {}
        self._deliveries: list[tuple[ClientConnection, object]] = []

    async def deliver(self, client: ClientConnection, packet: object) -> None:
        """Append the original packet to the recipient and global records."""

        self._packets.setdefault(client.id, []).append(packet)
        self._deliveries.append((client, packet))

    def packets_to(self, client: ClientConnection) -> list[object]:
        """Return the client's packets in delivery order."""

        return list(self._packets.get(client.id, []))

    def packets_of_type(
        self, client: ClientConnection, packet_type: type[T]
    ) -> list[T]:
        """Return the client's packets matching the requested type in order."""

        return [
            packet
            for packet in self.packets_to(client)
            if isinstance(packet, packet_type)
        ]

    def last_packet_of_type(self, client: ClientConnection, packet_type: type[T]) -> T:
        """Return the last matching packet, asserting that one was delivered."""

        packets = self.packets_of_type(client, packet_type)
        if not packets:
            raise AssertionError("No packet of the requested type was delivered.")
        return packets[-1]

    def all_deliveries(self) -> list[tuple[ClientConnection, object]]:
        """Return recipient and packet pairs in delivery order."""

        return list(self._deliveries)

    def clear(self) -> None:
        """Discard all recipient and delivery records."""

        self._packets.clear()
        self._deliveries.clear()


class Delivery:
    """Send and broadcast through a transport using a shared roster reference."""

    def __init__(
        self,
        transport: Transport,
        roster: dict[ServerConnection, ClientConnection],
    ) -> None:
        """Bind a transport and the server-owned mutable roster."""

        self.transport = transport
        self.roster = roster

    async def send(self, client: ClientConnection, packet: object) -> None:
        """Deliver one packet, including to clients outside the roster."""

        await self.transport.deliver(client, packet)

    async def broadcast(
        self, packet: object, exclude: ClientConnection | None = None
    ) -> None:
        """Deliver concurrently to roster clients except the excluded object."""

        recipients = [
            client for client in self.roster.values() if client is not exclude
        ]
        if not recipients:
            return
        await asyncio.gather(
            *(self.transport.deliver(client, packet) for client in recipients)
        )
