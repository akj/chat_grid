"""Server-authoritative player movement, rate limits, and wall sounds."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from .acoustic_zones import (
    client_acoustic_zone_id,
    client_position_packet,
    floor_acoustic_zone_id,
)
from .client import ClientConnection
from .delivery import Delivery
from .models import (
    BroadcastTeleportCompletePacket,
    ClientPacket,
    TeleportCompletePacket,
    UpdatePositionPacket,
    WallStructure,
    WorldSoundPacket,
)
from .structure_service import StructureService

PACKET_LOGGER = logging.getLogger("chgrid.server.packet")
MOVEMENT_TICK_MS = 200
MOVEMENT_MAX_STEPS_PER_TICK = 1


class Movement:
    """Server-authoritative player stepping and teleporting."""

    def __init__(
        self,
        *,
        delivery: Delivery,
        structures: StructureService,
        in_bounds: Callable[[int, int], bool],
        now_ms: Callable[[], int],
        persist_position: Callable[..., None],
        sync_carried_item: Callable[[ClientConnection], Awaitable[None]],
    ) -> None:
        """Bind world services and callbacks used by movement actions."""

        self.tick_ms = MOVEMENT_TICK_MS
        self.max_steps_per_tick = MOVEMENT_MAX_STEPS_PER_TICK
        self.delivery = delivery
        self.structures = structures
        self.in_bounds = in_bounds
        self.now_ms = now_ms
        self.persist_position = persist_position
        self.sync_carried_item = sync_carried_item

    def window_index(self, now_ms: int) -> int:
        """Return current movement rate-limit window index for a server timestamp."""

        return max(0, now_ms // self.tick_ms)

    def consume_budget(
        self, client: ClientConnection, now_ms: int, requested_delta: int
    ) -> bool:
        """Consume per-window movement budget; return whether the move is allowed."""

        window_index = self.window_index(now_ms)
        if client.movement_window_index != window_index:
            client.movement_window_index = window_index
            client.movement_window_steps_used = 0
        remaining = max(0, self.max_steps_per_tick - client.movement_window_steps_used)
        if requested_delta > remaining:
            return False
        client.movement_window_steps_used += requested_delta
        return True

    async def handle_packet(
        self, client: ClientConnection, packet: ClientPacket
    ) -> bool:
        """Handle a step or teleport packet and report whether it was consumed."""

        if isinstance(packet, UpdatePositionPacket):
            await self.step(client, packet.x, packet.y, packet.z)
            return True
        if isinstance(packet, TeleportCompletePacket):
            await self.teleport(client, packet.x, packet.y, packet.z)
            return True
        return False

    async def step(self, client: ClientConnection, x: int, y: int, z: int) -> None:
        """Apply a rate-limited step, correcting rejected moves to the sender."""

        if client.elevator_id is not None:
            await self.delivery.send(
                client,
                client_position_packet(client),
            )
            return
        if not self.in_bounds(x, y) or z != client.z:
            PACKET_LOGGER.warning(
                "out-of-bounds position ignored id=%s x=%d y=%d grid_size=%d",
                client.id,
                x,
                y,
                self.structures.grid_size,
            )
            await self.delivery.send(
                client,
                client_position_packet(client),
            )
            return
        now_ms = self.now_ms()
        requested_delta = max(abs(x - client.x), abs(y - client.y))
        if not self.consume_budget(client, now_ms, requested_delta):
            remaining = max(
                0,
                self.max_steps_per_tick - client.movement_window_steps_used,
            )
            PACKET_LOGGER.warning(
                "position rate limit ignored id=%s from=%d,%d to=%d,%d requested_delta=%d remaining_budget=%d window=%d",
                client.id,
                client.x,
                client.y,
                x,
                y,
                requested_delta,
                remaining,
                client.movement_window_index,
            )
            await self.delivery.send(
                client,
                client_position_packet(client),
            )
            return
        crossed_walls = self.structures.walls_crossed_for_move(
            x=client.x,
            y=client.y,
            z=client.z,
            next_x=x,
            next_y=y,
        )
        blocking_wall = self.structures.blocking_wall_for_move(
            x=client.x,
            y=client.y,
            z=client.z,
            next_x=x,
            next_y=y,
        )
        if blocking_wall is not None:
            await self._broadcast_wall_sound(
                blocking_wall,
                x=client.x,
                y=client.y,
                z=client.z,
                exclude=client,
            )
            await self.delivery.send(client, client_position_packet(client))
            return
        client.x = x
        client.y = y
        client.last_position_update_ms = now_ms
        self.persist_position(client)
        await self.delivery.send(
            client,
            client_position_packet(client),
        )
        await self.delivery.broadcast(
            client_position_packet(client),
            exclude=client,
        )
        for crossed_wall in crossed_walls:
            await self._broadcast_wall_sound(
                crossed_wall,
                x=client.x,
                y=client.y,
                z=client.z,
                exclude=client,
            )
        await self.sync_carried_item(client)
        return

    async def teleport(self, client: ClientConnection, x: int, y: int, z: int) -> None:
        """Apply a same-floor teleport and publish position and spatial events."""

        if client.elevator_id is not None:
            await self.delivery.send(
                client,
                client_position_packet(client),
            )
            return
        if not self.in_bounds(x, y) or z != client.z:
            PACKET_LOGGER.warning(
                "out-of-bounds teleport ignored id=%s x=%d y=%d grid_size=%d",
                client.id,
                x,
                y,
                self.structures.grid_size,
            )
            await self.delivery.send(
                client,
                client_position_packet(client),
            )
            return

        client.x = x
        client.y = y
        client.last_position_update_ms = self.now_ms()
        self.persist_position(client, force=True)
        await self.delivery.send(
            client,
            client_position_packet(client),
        )
        await self.delivery.broadcast(
            client_position_packet(client),
            exclude=client,
        )
        await self.sync_carried_item(client)
        await self.delivery.broadcast(
            BroadcastTeleportCompletePacket(
                type="teleport_complete",
                id=client.id,
                x=client.x,
                y=client.y,
                z=client.z,
                acousticZoneId=client_acoustic_zone_id(client),
            ),
            exclude=client,
        )
        return

    async def _broadcast_wall_sound(
        self,
        wall: WallStructure,
        *,
        x: int,
        y: int,
        z: int,
        exclude: ClientConnection,
    ) -> None:
        """Broadcast one validated wall impact/crossing sound to other users."""

        sound = str(wall.contactSound).strip()
        if not sound:
            return
        await self.delivery.broadcast(
            WorldSoundPacket(
                type="world_sound",
                sound=sound,
                x=x,
                y=y,
                z=z,
                acousticZoneId=floor_acoustic_zone_id(z),
            ),
            exclude=exclude,
        )
