"""Authoritative elevator lifecycle, timing, movement, and sound runtime."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from ....acoustic_zones import (
    client_position_packet,
    floor_acoustic_zone_id,
)
from ....client import ClientConnection
from ....delivery import Delivery
from ....models import (
    ItemElevatorStatusPacket,
    ItemUseSoundPacket,
    WorldItem,
)

ELEVATOR_DOOR_OPEN_SECONDS = 5.0
ELEVATOR_DOOR_OPEN_SOUND_SECONDS = 2.563107
ELEVATOR_DOOR_CLOSE_SOUND_SECONDS = 3.765601
ELEVATOR_TRAVEL_SECONDS = 5.0
ELEVATOR_TRAVEL_UPDATE_SECONDS = 0.25

LOGGER = logging.getLogger("chgrid.server.elevator")

ItemResultCallback = Callable[
    [ClientConnection, bool, Literal["use"], str, str | None], Awaitable[None]
]


@dataclass(frozen=True)
class ElevatorRuntimeCallbacks:
    """Server orchestration hooks required by the elevator runtime."""

    get_item: Callable[[str], WorldItem | None]
    iter_clients: Callable[[], Iterable[ClientConnection]]
    delivery: Delivery
    broadcast_item: Callable[[WorldItem], Awaitable[None]]
    send_item_result: ItemResultCallback
    request_state_save: Callable[[], None]
    persist_client_position: Callable[[ClientConnection], None]
    find_carried_items: Callable[[str], list[WorldItem]]
    now_ms: Callable[[], int]
    floor_name: Callable[[int], str]
    get_emit_range: Callable[[WorldItem], int]


class ElevatorRuntime:
    """Own independent elevator tasks and all server-authoritative car behavior."""

    def __init__(self, callbacks: ElevatorRuntimeCallbacks) -> None:
        """Create an elevator runtime using generic server delivery callbacks."""

        self.callbacks = callbacks
        self.delivery = callbacks.delivery
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
        item = self.callbacks.get_item(client.elevator_id)
        if item is not None and item.type == "elevator":
            client.x = item.x
            client.y = item.y
            client.z = int(item.params.get("currentZ", 0))
        client.elevator_id = None

    async def use(self, client: ClientConnection, item: WorldItem) -> None:
        """Apply one context-sensitive elevator call, enter, open, or exit action."""

        state = str(item.params.get("state", "idle"))
        current_z = int(item.params.get("currentZ", 0))
        if client.elevator_id == item.id:
            if state in {"moving", "arriving"}:
                await self._send_result(client, "The elevator is moving.", item.id)
                return
            if state in {"opening", "closing"}:
                await self._send_result(
                    client, f"The elevator door is {state}.", item.id
                )
                return
            if not bool(item.params.get("doorOpen", False)):
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
                f"You exit {item.title} on {self.callbacks.floor_name(current_z)}.",
                item.id,
            )
            return

        if state in {"moving", "arriving"}:
            item.params["queuedZ"] = client.z
            self._touch(item)
            await self.callbacks.broadcast_item(item)
            await self._send_result(client, f"You call {item.title}.", item.id)
            return

        if state in {"opening", "closing"}:
            if client.z != current_z:
                item.params["queuedZ"] = client.z
                self._touch(item)
                await self.callbacks.broadcast_item(item)
                await self._send_result(client, f"You call {item.title}.", item.id)
                return
            await self._send_result(client, f"The elevator door is {state}.", item.id)
            return

        if current_z != client.z:
            item.params["targetZ"] = client.z
            item.params["state"] = "moving"
            item.params["doorOpen"] = False
            self._touch(item)
            await self.callbacks.broadcast_item(item)
            self._restart_task(item.id)
            await self._send_result(client, f"You call {item.title}.", item.id)
            return

        if not bool(item.params.get("doorOpen", False)):
            await self._begin_opening(item, current_z)
            await self._send_result(client, "The elevator door is opening.", item.id)
            return

        client.elevator_id = item.id
        await self.delivery.broadcast(client_position_packet(client))
        destination_z = next(z for z in self._floor_elevations(item) if z != current_z)
        item.params["departOnCloseZ"] = destination_z
        self._touch(item)
        await self.callbacks.broadcast_item(item)
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
        door_open_seconds = self._duration_seconds(
            item, "doorOpenSeconds", ELEVATOR_DOOR_OPEN_SECONDS
        )
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
                item = self.callbacks.get_item(item_id)
                if item is None or item.type != "elevator":
                    return
                state = str(item.params.get("state", "idle"))
                if state == "moving":
                    origin_z = int(item.params.get("currentZ", 0))
                    target_z = int(item.params.get("targetZ", origin_z))
                    await self.advance_travel(item, origin_z, target_z)
                    item.params["currentZ"] = target_z
                    item.params["targetZ"] = None
                    item.params["state"] = "arriving"
                    item.params["doorOpen"] = False
                    self._touch(item)
                    await self.callbacks.broadcast_item(item)
                    await self.broadcast_direction_sound(item, target_z)
                    await self._broadcast_sound(
                        item, target_z, "/sounds/elevator_open.ogg"
                    )
                    continue
                if state in {"opening", "arriving"}:
                    await asyncio.sleep(ELEVATOR_DOOR_OPEN_SOUND_SECONDS)
                    current_z = int(item.params.get("currentZ", 0))
                    item.params["state"] = "door_open"
                    item.params["doorOpen"] = True
                    self._touch(item)
                    if state == "arriving":
                        await self._move_occupants(item, current_z)
                    await self.callbacks.broadcast_item(item)
                    continue
                if state == "door_open":
                    await asyncio.sleep(
                        self._duration_seconds(
                            item, "doorOpenSeconds", ELEVATOR_DOOR_OPEN_SECONDS
                        )
                    )
                    current_z = int(item.params.get("currentZ", 0))
                    item.params["state"] = "closing"
                    item.params["doorOpen"] = False
                    self._touch(item)
                    await self.callbacks.broadcast_item(item)
                    await self._broadcast_sound(
                        item, current_z, "/sounds/elevator_close.ogg"
                    )
                    continue
                if state == "closing":
                    await asyncio.sleep(ELEVATOR_DOOR_CLOSE_SOUND_SECONDS)
                    current_z = int(item.params.get("currentZ", 0))
                    next_z = self._next_destination(item, current_z)
                    item.params["departOnCloseZ"] = None
                    item.params["queuedZ"] = None
                    if next_z is None:
                        item.params["state"] = "idle"
                        item.params["targetZ"] = None
                    else:
                        item.params["state"] = "moving"
                        item.params["targetZ"] = next_z
                    self._touch(item)
                    await self.callbacks.broadcast_item(item)
                    if next_z is None:
                        return
                    continue
                return
        except asyncio.CancelledError:
            return
        except Exception:
            LOGGER.exception("elevator cycle failed item_id=%s", item_id)
            await self._return_to_idle(item_id)
        finally:
            current = self._tasks.get(item_id)
            if current is asyncio.current_task():
                self._tasks.pop(item_id, None)

    async def advance_travel(
        self, item: WorldItem, origin_z: int, destination_z: int
    ) -> None:
        """Publish progressive rider heights over the elevator travel interval."""

        travel_seconds = self._duration_seconds(
            item, "travelSeconds", ELEVATOR_TRAVEL_SECONDS
        )
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

        next_z = next(z for z in self._floor_elevations(item) if z != current_z)
        sound = (
            "/sounds/elevator_up.ogg"
            if next_z > current_z
            else "/sounds/elevator_down.ogg"
        )
        await self._broadcast_sound(item, current_z, sound)

    async def _begin_opening(self, item: WorldItem, current_z: int) -> None:
        """Enter the non-traversable opening phase and start its sound."""

        item.params["state"] = "opening"
        item.params["doorOpen"] = False
        self._touch(item)
        await self.callbacks.broadcast_item(item)
        await self.broadcast_direction_sound(item, current_z)
        await self._broadcast_sound(item, current_z, "/sounds/elevator_open.ogg")
        self._restart_task(item.id)

    async def _move_occupants(self, item: WorldItem, destination_z: int) -> None:
        """Move elevator riders and carried items once the arrival door is open."""

        for rider in tuple(self.callbacks.iter_clients()):
            if rider.elevator_id != item.id:
                continue
            rider.x = item.x
            rider.y = item.y
            rider.z = destination_z
            rider.last_position_update_ms = self.callbacks.now_ms()
            self.callbacks.persist_client_position(rider)
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
                        f"{self.callbacks.floor_name(destination_z)}. The door opens."
                    ),
                ),
            )
            carried_items = self.callbacks.find_carried_items(rider.id)
            for carried in carried_items:
                carried.x = rider.x
                carried.y = rider.y
                carried.z = rider.z
                carried.updatedAt = self.callbacks.now_ms()
                carried.updatedBy = rider.user_id or rider.id
                carried.updatedByName = rider.username or rider.nickname
                await self.callbacks.broadcast_item(carried)

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
            range=self.callbacks.get_emit_range(item),
        )
        await self.delivery.broadcast(packet)

    async def _broadcast_travel_position(self, item: WorldItem, travel_z: int) -> None:
        """Move riders to one intermediate elevator height."""

        for rider in tuple(self.callbacks.iter_clients()):
            if rider.elevator_id != item.id:
                continue
            rider.x = item.x
            rider.y = item.y
            rider.z = travel_z
            carried_items = self.callbacks.find_carried_items(rider.id)
            for carried in carried_items:
                carried.x = rider.x
                carried.y = rider.y
                carried.z = rider.z
                await self.callbacks.broadcast_item(carried)
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

    async def _return_to_idle(self, item_id: str) -> None:
        """Park a car whose cycle failed at its last landing with the door closed.

        Riders and their carried items are placed at that landing too, since the
        failure may have left them at a travel height. Positions are broadcast
        but not saved, because saving may be what failed.
        """

        # Leave the task table so a use() during the broadcasts below starts a
        # new cycle without cancelling this recovery.
        if self._tasks.get(item_id) is asyncio.current_task():
            self._tasks.pop(item_id, None)
        item = self.callbacks.get_item(item_id)
        if item is None or item.type != "elevator":
            return
        # Settle all state before the first await, so a cycle started during
        # the broadcasts sees a parked car with riders at the landing.
        item.params.update(
            {
                "targetZ": None,
                "queuedZ": None,
                "departOnCloseZ": None,
                "state": "idle",
                "doorOpen": False,
            }
        )
        self._touch(item)
        landing_z = int(item.params.get("currentZ", 0))
        riders = [
            rider
            for rider in self.callbacks.iter_clients()
            if rider.elevator_id == item.id
        ]
        carried_items: list[WorldItem] = []
        for rider in riders:
            rider.x, rider.y, rider.z = item.x, item.y, landing_z
            for carried in self.callbacks.find_carried_items(rider.id):
                carried.x, carried.y, carried.z = rider.x, rider.y, rider.z
                carried_items.append(carried)
        await self.callbacks.broadcast_item(item)
        # Items and riders can leave during these awaits; don't resurrect them.
        for carried in carried_items:
            if self.callbacks.get_item(carried.id) is carried:
                await self.callbacks.broadcast_item(carried)
        for rider in riders:
            if any(client is rider for client in self.callbacks.iter_clients()):
                await self.delivery.broadcast(client_position_packet(rider))

    def _touch(self, item: WorldItem) -> None:
        """Mark elevator state changed and schedule persistence."""

        item.updatedAt = self.callbacks.now_ms()
        item.updatedBy = "system"
        item.updatedByName = "system"
        item.version += 1
        self.callbacks.request_state_save()

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

        await self.callbacks.send_item_result(client, True, "use", message, item_id)

    @staticmethod
    def _floor_elevations(item: WorldItem) -> list[int]:
        """Return this elevator's configured floors in ascending order."""

        floors = item.params.get("floorZs", [0, 40])
        return sorted(int(z) for z in floors if isinstance(z, int))

    @staticmethod
    def _next_destination(item: WorldItem, current_z: int) -> int | None:
        """Resolve the pending departure or queued landing after closing."""

        depart_z = item.params.get("departOnCloseZ")
        queued_z = item.params.get("queuedZ")
        if isinstance(depart_z, int) and depart_z != current_z:
            return depart_z
        if isinstance(queued_z, int) and queued_z != current_z:
            return queued_z
        return None

    @staticmethod
    def _duration_seconds(item: WorldItem, key: str, default: float) -> float:
        """Read one validated duration with a safe default for older items."""

        try:
            value = float(item.params.get(key, default))
        except (TypeError, ValueError):
            return default
        if not isfinite(value):
            return default
        return max(0, min(300, value))
