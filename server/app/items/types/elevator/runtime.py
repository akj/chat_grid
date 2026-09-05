"""Authoritative elevator lifecycle, timing, movement, and sound runtime."""

from __future__ import annotations

import asyncio
from typing import Literal, Protocol

from websockets.asyncio.server import ServerConnection

from ....acoustic_zones import (
    client_position_packet,
    floor_acoustic_zone_id,
)
from ....client import ClientConnection
from ....delivery import Delivery
from ....floors import floor_name
from ....item_service import ItemService
from ....models import (
    ItemElevatorStatusPacket,
    ItemUseSoundPacket,
    WorldItem,
)

from .car import ElevatorCar

ELEVATOR_TRAVEL_UPDATE_SECONDS = 0.25


class ElevatorRuntimeHost(Protocol):
    """Server operations required by the elevator runtime."""

    delivery: Delivery

    @property
    def items(self) -> dict[str, WorldItem]: ...

    @property
    def clients(self) -> dict[ServerConnection, ClientConnection]: ...

    @property
    def item_service(self) -> ItemService: ...

    async def broadcast_item(self, item: WorldItem) -> None: ...

    async def send_result(
        self,
        client: ClientConnection,
        ok: bool,
        action: Literal["use"],
        message: str,
        item_id: str | None = None,
    ) -> None: ...

    def request_state_save(self) -> None: ...

    def get_emit_range(self, item: WorldItem) -> int: ...

    def persist_client_position(self, client: ClientConnection) -> None: ...


class ElevatorRuntime:
    """Own independent elevator tasks and all server-authoritative car behavior."""

    def __init__(self, host: ElevatorRuntimeHost) -> None:
        """Create an elevator runtime using authoritative item state."""

        self.host = host
        self.delivery = host.delivery
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def shutdown(self) -> None:
        """Cancel and await every active elevator transition task."""

        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def cancel(self, item_id: str) -> None:
        """Cancel one elevator's active transition task, if present."""

        task = self._tasks.pop(item_id, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def restore_rider_to_landing(self, client: ClientConnection) -> None:
        """Return a disconnecting rider to the car's last completed landing."""

        if not client.elevator_id:
            return
        item = self.host.items.get(client.elevator_id)
        if item is not None and item.type == "elevator":
            client.x = item.x
            client.y = item.y
            client.z = ElevatorCar(item).landing
        client.elevator_id = None

    async def use(self, client: ClientConnection, item: WorldItem) -> None:
        """Apply one context-sensitive elevator call, enter, open, or exit action."""

        car = ElevatorCar(item)
        phase = car.phase
        current_z = car.landing
        if client.elevator_id == item.id:
            block_reason = car.door_block_reason()
            if block_reason is not None:
                await self._send_result(client, block_reason, item.id)
                return
            if not car.door_open:
                await self._begin_opening(item, current_z)
                await self._send_result(
                    client, "The elevator door is opening.", item.id
                )
                return
            client.elevator_id = None
            await self.delivery.broadcast(client_position_packet(client))
            await self.delivery.send(
                client,
                ItemElevatorStatusPacket(
                    type="item_elevator_status",
                    itemId=item.id,
                    event="exited",
                    z=current_z,
                ),
            )
            await self._send_result(
                client,
                f"You exit {item.title} on {floor_name(current_z)}.",
                item.id,
            )
            return

        if phase in {"moving", "arriving"}:
            car.queue_call(client.z)
            self._touch(item)
            await self.host.broadcast_item(item)
            await self._send_result(client, f"You call {item.title}.", item.id)
            return

        if phase in {"opening", "closing"}:
            if client.z != current_z:
                car.queue_call(client.z)
                self._touch(item)
                await self.host.broadcast_item(item)
                await self._send_result(client, f"You call {item.title}.", item.id)
                return
            await self._send_result(client, f"The elevator door is {phase}.", item.id)
            return

        if current_z != client.z:
            car.call_to(client.z)
            self._touch(item)
            await self.host.broadcast_item(item)
            self._restart_task(item.id)
            await self._send_result(client, f"You call {item.title}.", item.id)
            return

        if not car.door_open:
            await self._begin_opening(item, current_z)
            await self._send_result(client, "The elevator door is opening.", item.id)
            return

        client.elevator_id = item.id
        await self.delivery.broadcast(client_position_packet(client))
        destination_z = car.other_floor(current_z)
        car.board(destination_z)
        self._touch(item)
        await self.host.broadcast_item(item)
        self._restart_task(item.id)
        await self.delivery.send(
            client,
            ItemElevatorStatusPacket(
                type="item_elevator_status",
                itemId=item.id,
                event="entered",
                z=current_z,
            ),
        )
        door_open_seconds = car.door_open_seconds
        seconds_label = f"{door_open_seconds:g} second"
        if door_open_seconds != 1:
            seconds_label += "s"
        await self._send_result(
            client,
            f"You enter {item.title}. The door will close in {seconds_label}.",
            item.id,
        )

    async def run_cycle(self, item_id: str) -> None:
        """Advance one elevator through travel, arrival, and door timing."""

        try:
            while True:
                item = self.host.items.get(item_id)
                if item is None or item.type != "elevator":
                    return
                car = ElevatorCar(item)
                phase = car.phase
                if phase == "idle":
                    return
                if phase == "moving":
                    await self.advance_travel(
                        item,
                        car.landing,
                        car.target if car.target is not None else car.landing,
                    )
                else:
                    seconds = car.phase_seconds()
                    if seconds is None:
                        return
                    await asyncio.sleep(seconds)
                step = car.advance()
                self._touch(item)
                if step == "arrived":
                    await self.host.broadcast_item(item)
                    await self.broadcast_direction_sound(item, car.landing)
                    await self._broadcast_sound(
                        item, car.landing, "/sounds/elevator_open.ogg"
                    )
                elif step == "door_opened":
                    if phase == "arriving":
                        await self._move_occupants(item, car.landing)
                    await self.host.broadcast_item(item)
                elif step == "door_closed":
                    await self.host.broadcast_item(item)
                    await self._broadcast_sound(
                        item, car.landing, "/sounds/elevator_close.ogg"
                    )
                elif step == "departed":
                    await self.host.broadcast_item(item)
                elif step == "settled":
                    await self.host.broadcast_item(item)
                    return
        except asyncio.CancelledError:
            return
        finally:
            current = self._tasks.get(item_id)
            if current is asyncio.current_task():
                self._tasks.pop(item_id, None)

    async def advance_travel(
        self, item: WorldItem, origin_z: int, destination_z: int
    ) -> None:
        """Publish progressive rider heights over the elevator travel interval."""

        travel_seconds = ElevatorCar(item).travel_seconds
        distance = destination_z - origin_z
        if distance == 0:
            await asyncio.sleep(travel_seconds)
            return
        direction = 1 if distance > 0 else -1
        update_count = max(1, round(travel_seconds / ELEVATOR_TRAVEL_UPDATE_SECONDS))
        last_z = origin_z
        if abs(distance) > 1:
            last_z = origin_z + direction
            await self._broadcast_travel_position(item, last_z)

        for update_index in range(1, update_count + 1):
            await asyncio.sleep(travel_seconds / update_count)
            if update_index == update_count:
                continue
            travel_z = round(origin_z + (distance * update_index / update_count))
            if direction > 0:
                travel_z = max(origin_z + 1, min(destination_z - 1, travel_z))
            else:
                travel_z = max(destination_z + 1, min(origin_z - 1, travel_z))
            if travel_z == last_z:
                continue
            last_z = travel_z
            await self._broadcast_travel_position(item, travel_z)

    async def broadcast_direction_sound(self, item: WorldItem, current_z: int) -> None:
        """Announce the elevator's next travel direction after its door opens."""

        next_z = ElevatorCar(item).other_floor(current_z)
        sound = (
            "/sounds/elevator_up.ogg"
            if next_z > current_z
            else "/sounds/elevator_down.ogg"
        )
        await self._broadcast_sound(item, current_z, sound)

    async def _begin_opening(self, item: WorldItem, current_z: int) -> None:
        """Enter the non-traversable opening phase and start its sound."""

        ElevatorCar(item).begin_opening()
        self._touch(item)
        await self.host.broadcast_item(item)
        await self.broadcast_direction_sound(item, current_z)
        await self._broadcast_sound(item, current_z, "/sounds/elevator_open.ogg")
        self._restart_task(item.id)

    async def _move_occupants(self, item: WorldItem, destination_z: int) -> None:
        """Move elevator riders and carried items once the arrival door is open."""

        for rider in tuple(self.host.clients.values()):
            if rider.elevator_id != item.id:
                continue
            rider.x = item.x
            rider.y = item.y
            rider.z = destination_z
            rider.last_position_update_ms = self.host.item_service.now_ms()
            self.host.persist_client_position(rider)
            await self.delivery.broadcast(client_position_packet(rider))
            await self.delivery.send(
                rider,
                ItemElevatorStatusPacket(
                    type="item_elevator_status",
                    itemId=item.id,
                    event="arrived",
                    z=destination_z,
                    message=(
                        f"{item.title} arrives on "
                        f"{floor_name(destination_z)}. The door opens."
                    ),
                ),
            )
            carried = self.host.item_service.find_carried_item(rider.id)
            if carried is not None:
                carried.x = rider.x
                carried.y = rider.y
                carried.z = rider.z
                carried.updatedAt = self.host.item_service.now_ms()
                carried.updatedBy = rider.user_id or rider.id
                carried.updatedByName = rider.username or rider.nickname
                await self.host.broadcast_item(carried)

    async def _broadcast_sound(
        self, item: WorldItem, current_z: int, sound: str
    ) -> None:
        """Emit one landing-zone sound transmitted through the door to riders."""

        packet = ItemUseSoundPacket(
            type="item_use_sound",
            itemId=item.id,
            sound=sound,
            x=item.x,
            y=item.y,
            z=current_z,
            acousticZoneId=floor_acoustic_zone_id(current_z),
            range=self.host.get_emit_range(item),
        )
        await self.delivery.broadcast(packet)

    async def _broadcast_travel_position(self, item: WorldItem, travel_z: int) -> None:
        """Move riders to one intermediate elevator height."""

        for rider in tuple(self.host.clients.values()):
            if rider.elevator_id != item.id:
                continue
            rider.x = item.x
            rider.y = item.y
            rider.z = travel_z
            carried = self.host.item_service.find_carried_item(rider.id)
            if carried is not None:
                carried.x = rider.x
                carried.y = rider.y
                carried.z = rider.z
                await self.host.broadcast_item(carried)
            await self.delivery.broadcast(client_position_packet(rider))
            await self.delivery.send(
                rider,
                ItemElevatorStatusPacket(
                    type="item_elevator_status",
                    itemId=item.id,
                    event="moving",
                    z=travel_z,
                ),
            )

    def _touch(self, item: WorldItem) -> None:
        """Mark elevator state changed and schedule persistence."""

        item.updatedAt = self.host.item_service.now_ms()
        item.updatedBy = "system"
        item.updatedByName = "system"
        item.version += 1
        self.host.request_state_save()

    def _restart_task(self, item_id: str) -> None:
        """Restart the timer/state-machine task for one elevator."""

        existing = self._tasks.get(item_id)
        if existing is not None and existing is not asyncio.current_task():
            existing.cancel()
        self._tasks[item_id] = asyncio.create_task(self.run_cycle(item_id))

    async def _send_result(
        self, client: ClientConnection, message: str, item_id: str
    ) -> None:
        """Send a successful elevator-use result through the server callback."""

        await self.host.send_result(client, True, "use", message, item_id)
