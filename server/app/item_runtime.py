"""Authoritative item lifecycle orchestration and packet routing."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Literal, Protocol

from websockets.asyncio.server import ServerConnection

from .auth_service import AuthService
from .acoustic_zones import client_acoustic_zone_id, floor_acoustic_zone_id
from .client import ClientConnection
from .delivery import Delivery
from .floors import floor_name
from .item_catalog import (
    get_item_definition,
    get_item_use_cooldown_ms,
    is_known_item_type,
)
from .item_service import ItemService
from .item_type_handlers import get_item_type_handler
from .items.types.clock.runtime import ClockRuntime
from .items.types.elevator.car import ElevatorCar
from .items.types.elevator.runtime import ElevatorRuntime
from .items.types.piano.runtime import PianoRuntime
from .items.types.radio_station.runtime import RadioRuntime
from .models import (
    BroadcastChatMessagePacket,
    ClientPacket,
    ItemActionResultPacket,
    ItemAddPacket,
    ItemDeletePacket,
    ItemDropPacket,
    ItemPianoNotePacket,
    ItemPianoRecordingPacket,
    ItemPickupPacket,
    ItemRemovePacket,
    ItemSecondaryUsePacket,
    ItemTransferPacket,
    ItemTransferTargetSummary,
    ItemTransferTargetsPacket,
    ItemTransferTargetsResultPacket,
    ItemUpdatePacket,
    ItemUpsertPacket,
    ItemUsePacket,
    ItemUseSoundPacket,
    WorldItem,
)

LOGGER = logging.getLogger("chgrid.server")


class ItemRuntimeHost(Protocol):
    """Server operations required by authoritative item orchestration."""

    auth_service: AuthService
    clients: dict[ServerConnection, ClientConnection]
    delivery: Delivery
    item_service: ItemService

    def _client_has_permission(self, client: ClientConnection, key: str) -> bool: ...

    def _is_in_bounds(self, x: int, y: int) -> bool: ...

    def _persist_client_position(
        self, client: ClientConnection, *, force: bool = False
    ) -> None: ...

    def _request_state_save(self) -> None: ...


class ItemRuntime:
    """Own item-domain routing, shared rules, and per-type runtimes."""

    def __init__(self, host: ItemRuntimeHost) -> None:
        """Compose generic and type-specific item runtimes."""

        self.host = host
        self.delivery = host.delivery
        self.item_last_use_ms: dict[str, int] = {}
        self.piano = PianoRuntime(self)
        self.radio = RadioRuntime(self)
        self.clock = ClockRuntime(self)
        self.elevator = ElevatorRuntime(self)

    @property
    def auth_service(self) -> AuthService:
        """Return account data used for ownership transfer targets."""

        return self.host.auth_service

    @property
    def clients(self) -> dict[ServerConnection, ClientConnection]:
        """Return currently connected clients."""

        return self.host.clients

    @property
    def item_service(self) -> ItemService:
        """Return authoritative item persistence and storage."""

        return self.host.item_service

    @property
    def items(self) -> dict[str, WorldItem]:
        """Return the current authoritative item mapping."""

        return self.item_service.items

    def start(self) -> None:
        """Start all item background runtimes."""

        self.radio.start()
        self.clock.start()

    async def shutdown(self) -> None:
        """Stop all item tasks owned by generic and type-specific runtimes."""

        await self.piano.shutdown()
        await self.elevator.shutdown()
        await self.clock.shutdown()
        await self.radio.shutdown()

    async def prepare_client_disconnect(self, client: ClientConnection) -> None:
        """Release per-client item runtime state before position persistence."""

        await self.piano.client_disconnected(client)
        self.elevator.restore_rider_to_landing(client)

    async def finish_client_disconnect(self, client: ClientConnection) -> None:
        """Drop carried items after final client position persistence."""

        for item in self.item_service.drop_carried_items_for_disconnect(client):
            await self.broadcast_item(item)
        self.request_state_save()

    async def sync_carried_item(self, client: ClientConnection) -> None:
        """Move and broadcast the item carried by a moving client."""

        carried = self.item_service.find_carried_item(client.id)
        if carried is None:
            return
        actor_id, actor_name = self._item_updated_actor(client)
        carried.x = client.x
        carried.y = client.y
        carried.z = client.z
        carried.updatedAt = self.item_service.now_ms()
        carried.updatedBy = actor_id
        carried.updatedByName = actor_name
        await self.broadcast_item(carried)

    def client_has_permission(self, client: ClientConnection, key: str) -> bool:
        """Return whether a client has one item-related permission."""

        return self.host._client_has_permission(client, key)

    def request_state_save(self) -> None:
        """Request coalesced persistence after an item mutation."""

        self.host._request_state_save()

    def persist_client_position(self, client: ClientConnection) -> None:
        """Persist a rider's completed landing without movement debouncing."""

        self.host._persist_client_position(client, force=True)

    def get_client_by_id(self, client_id: str) -> ClientConnection | None:
        """Resolve one connected client by runtime id."""

        for client in self.clients.values():
            if client.id == client_id:
                return client
        return None

    async def handle_packet(
        self, client: ClientConnection, packet: ClientPacket
    ) -> bool:
        """Route an item-domain packet and report whether it was consumed."""

        if isinstance(packet, ItemAddPacket):
            await self._handle_add(client, packet)
            return True
        if isinstance(packet, ItemPickupPacket):
            await self._handle_pickup(client, packet)
            return True
        if isinstance(packet, ItemDropPacket):
            await self._handle_drop(client, packet)
            return True
        if isinstance(packet, ItemDeletePacket):
            await self._handle_delete(client, packet)
            return True
        if isinstance(packet, ItemTransferTargetsPacket):
            await self._handle_transfer_targets(client, packet)
            return True
        if isinstance(packet, ItemTransferPacket):
            await self._handle_transfer(client, packet)
            return True
        if isinstance(packet, ItemUsePacket):
            await self._handle_use(client, packet)
            return True
        if isinstance(packet, ItemSecondaryUsePacket):
            await self._handle_secondary_use(client, packet)
            return True
        if isinstance(packet, ItemPianoNotePacket):
            await self._handle_piano_note(client, packet)
            return True
        if isinstance(packet, ItemPianoRecordingPacket):
            await self._handle_piano_recording(client, packet)
            return True
        if isinstance(packet, ItemUpdatePacket):
            await self._handle_update(client, packet)
            return True
        return False

    async def _handle_add(
        self, client: ClientConnection, packet: ItemAddPacket
    ) -> None:
        """Handle one add item packet."""

        if not self.client_has_permission(client, "item.create"):
            await self.send_result(
                client, False, "add", "Not authorized to create items."
            )
            return
        if not is_known_item_type(packet.itemType):
            await self.send_result(client, False, "add", "Unknown item type.")
            return
        item = self.item_service.default_item(client, packet.itemType)
        if item.type == "elevator":
            item.z = 0
            ElevatorCar(item).place_at(client.z)
        if not self._item_footprint_in_bounds(item):
            await self.send_result(
                client,
                False,
                "add",
                "The item footprint does not fit at this position.",
            )
            return
        self.item_service.add_item(item)
        await self.broadcast_item(item)
        self.request_state_save()
        LOGGER.info(
            "item created by=%s item_id=%s type=%s title=%s x=%d y=%d z=%d",
            client.nickname,
            item.id,
            item.type,
            item.title,
            item.x,
            item.y,
            item.z,
        )
        item_text = f"{item.title} ({self._item_type_label(item)})"
        await self.delivery.broadcast(
            BroadcastChatMessagePacket(
                type="chat_message",
                message=f"{client.nickname} placed {item_text} at {item.x}, {item.y}, {item.z}, {floor_name(item.z)}.",
                system=True,
            ),
            exclude=client,
        )
        await self.send_result(
            client,
            True,
            "add",
            f"You placed {item_text} at {item.x}, {item.y}, {item.z}, {floor_name(item.z)}.",
            item.id,
        )
        return

    async def _handle_pickup(
        self, client: ClientConnection, packet: ItemPickupPacket
    ) -> None:
        """Handle one pickup item packet."""

        pickup_item = self.items.get(packet.itemId)
        if not pickup_item:
            await self.send_result(client, False, "pickup", "Item not found.")
            return
        if "carryable" not in pickup_item.capabilities:
            await self.send_result(
                client,
                False,
                "pickup",
                "That item cannot be carried.",
                pickup_item.id,
            )
            return
        if pickup_item.carrierId and pickup_item.carrierId != client.id:
            await self.send_result(
                client,
                False,
                "pickup",
                "Item is already being carried.",
                pickup_item.id,
            )
            return
        carried = self.item_service.find_carried_item(client.id)
        if carried and carried.id != pickup_item.id:
            await self.send_result(
                client,
                False,
                "pickup",
                "You are already carrying an item.",
                pickup_item.id,
            )
            return
        if pickup_item.carrierId is None and (
            not self.item_is_on_client_square(pickup_item, client)
        ):
            await self.send_result(
                client,
                False,
                "pickup",
                "Item is not on your square.",
                pickup_item.id,
            )
            return
        can_pickup_any = self.client_has_permission(client, "item.pickup_drop.any")
        can_pickup_own = self.client_has_permission(
            client, "item.pickup_drop.own"
        ) and self._owns_item(client, pickup_item)
        if not can_pickup_any and not can_pickup_own:
            await self.send_result(
                client,
                False,
                "pickup",
                "Not authorized to pick up this item.",
                pickup_item.id,
            )
            return
        pickup_item.carrierId = client.id
        pickup_item.x = client.x
        pickup_item.y = client.y
        pickup_item.z = client.z
        pickup_item.updatedAt = self.item_service.now_ms()
        actor_id, actor_name = self._item_updated_actor(client)
        pickup_item.updatedBy = actor_id
        pickup_item.updatedByName = actor_name
        await self.broadcast_item(pickup_item)
        self.request_state_save()
        item_text = f"{pickup_item.title} ({self._item_type_label(pickup_item)})"
        await self.delivery.broadcast(
            BroadcastChatMessagePacket(
                type="chat_message",
                message=f"{client.nickname} picked up {item_text}.",
                system=True,
            ),
            exclude=client,
        )
        await self.send_result(
            client,
            True,
            "pickup",
            f"Picked up {pickup_item.title}.",
            pickup_item.id,
        )
        return

    async def _handle_drop(
        self, client: ClientConnection, packet: ItemDropPacket
    ) -> None:
        """Handle one drop item packet."""

        drop_item = self.items.get(packet.itemId)
        if not drop_item:
            await self.send_result(client, False, "drop", "Item not found.")
            return
        if drop_item.carrierId != client.id:
            await self.send_result(
                client,
                False,
                "drop",
                "You are not carrying that item.",
                drop_item.id,
            )
            return
        if not self.host._is_in_bounds(packet.x, packet.y) or packet.z != client.z:
            await self.send_result(
                client,
                False,
                "drop",
                "Drop position is out of bounds.",
                drop_item.id,
            )
            return
        can_drop_any = self.client_has_permission(client, "item.pickup_drop.any")
        can_drop_own = self.client_has_permission(
            client, "item.pickup_drop.own"
        ) and self._owns_item(client, drop_item)
        if not can_drop_any and not can_drop_own:
            await self.send_result(
                client,
                False,
                "drop",
                "Not authorized to drop this item.",
                drop_item.id,
            )
            return
        drop_item.carrierId = None
        drop_item.x = packet.x
        drop_item.y = packet.y
        drop_item.z = packet.z
        drop_item.updatedAt = self.item_service.now_ms()
        actor_id, actor_name = self._item_updated_actor(client)
        drop_item.updatedBy = actor_id
        drop_item.updatedByName = actor_name
        await self.broadcast_item(drop_item)
        self.request_state_save()
        item_text = f"{drop_item.title} ({self._item_type_label(drop_item)})"
        await self.delivery.broadcast(
            BroadcastChatMessagePacket(
                type="chat_message",
                message=f"{client.nickname} dropped {item_text} at {drop_item.x}, {drop_item.y}, {drop_item.z}, {floor_name(drop_item.z)}.",
                system=True,
            ),
            exclude=client,
        )
        await self.send_result(
            client,
            True,
            "drop",
            f"Dropped {drop_item.title} at {drop_item.x}, {drop_item.y}, {drop_item.z}, {floor_name(drop_item.z)}.",
            drop_item.id,
        )
        return

    async def _handle_delete(
        self, client: ClientConnection, packet: ItemDeletePacket
    ) -> None:
        """Handle one delete item packet."""

        delete_item = self.items.get(packet.itemId)
        if not delete_item:
            await self.send_result(client, False, "delete", "Item not found.")
            return
        if delete_item.carrierId and delete_item.carrierId != client.id:
            await self.send_result(
                client,
                False,
                "delete",
                "Item is being carried by another user.",
                delete_item.id,
            )
            return
        if delete_item.type == "elevator" and (
            ElevatorCar(delete_item).phase == "moving"
            or any(
                other.elevator_id == delete_item.id for other in self.clients.values()
            )
        ):
            await self.send_result(
                client,
                False,
                "delete",
                "The elevator cannot be deleted while moving or occupied.",
                delete_item.id,
            )
            return
        if delete_item.carrierId is None and (
            not self.item_is_on_client_square(delete_item, client)
        ):
            await self.send_result(
                client,
                False,
                "delete",
                "Item is not on your square.",
                delete_item.id,
            )
            return
        can_delete_any = self.client_has_permission(client, "item.delete.any")
        can_delete_own = self.client_has_permission(
            client, "item.delete.own"
        ) and self._owns_item(client, delete_item)
        if not can_delete_any and not can_delete_own:
            await self.send_result(
                client,
                False,
                "delete",
                "Not authorized to delete this item.",
                delete_item.id,
            )
            return
        LOGGER.info(
            "item deleted by=%s item_id=%s type=%s title=%s",
            client.nickname,
            delete_item.id,
            delete_item.type,
            delete_item.title,
        )
        self.piano.remove_item(delete_item)
        self.item_service.remove_item(delete_item.id)
        await self.elevator.cancel(delete_item.id)
        self.item_last_use_ms.pop(delete_item.id, None)
        await self.delivery.broadcast(
            ItemRemovePacket(type="item_remove", itemId=delete_item.id)
        )
        self.request_state_save()
        item_text = f"{delete_item.title} ({self._item_type_label(delete_item)})"
        await self.delivery.broadcast(
            BroadcastChatMessagePacket(
                type="chat_message",
                message=f"{client.nickname} deleted {item_text}.",
                system=True,
            ),
            exclude=client,
        )
        await self.send_result(
            client, True, "delete", f"You deleted {item_text}.", delete_item.id
        )
        return

    async def _handle_transfer_targets(
        self, client: ClientConnection, packet: ItemTransferTargetsPacket
    ) -> None:
        """Handle one transfer targets item packet."""

        transfer_targets_item = self.items.get(packet.itemId)
        if not transfer_targets_item:
            await self.send_result(client, False, "transfer", "Item not found.")
            return
        if transfer_targets_item.carrierId:
            await self.send_result(
                client,
                False,
                "transfer",
                "Item cannot be transferred while carried.",
                transfer_targets_item.id,
            )
            return
        if not self.item_is_on_client_square(transfer_targets_item, client):
            await self.send_result(
                client,
                False,
                "transfer",
                "Item is not on your square.",
                transfer_targets_item.id,
            )
            return
        can_transfer_any = self.client_has_permission(client, "item.transfer.any")
        can_transfer_own = self.client_has_permission(
            client, "item.transfer.own"
        ) and self._owns_item(client, transfer_targets_item)
        if not can_transfer_any and not can_transfer_own:
            await self.send_result(
                client,
                False,
                "transfer",
                "Not authorized to transfer this item.",
                transfer_targets_item.id,
            )
            return
        users = self.auth_service.list_users_for_admin()
        connected_user_ids = {
            other.user_id
            for other in self.clients.values()
            if other.authenticated and other.user_id
        }
        targets = [
            ItemTransferTargetSummary(
                userId=str(entry["id"]),
                username=str(entry["username"]),
                online=str(entry.get("id")) in connected_user_ids,
            )
            for entry in users
            if str(entry.get("status")) == "active"
            and str(entry["id"]) != transfer_targets_item.createdBy
        ]
        await self.delivery.send(
            client,
            ItemTransferTargetsResultPacket(
                type="item_transfer_targets",
                itemId=transfer_targets_item.id,
                targets=targets,
            ),
        )
        return

    async def _handle_transfer(
        self, client: ClientConnection, packet: ItemTransferPacket
    ) -> None:
        """Handle one transfer item packet."""

        transfer_item = self.items.get(packet.itemId)
        if not transfer_item:
            await self.send_result(client, False, "transfer", "Item not found.")
            return
        if transfer_item.carrierId:
            await self.send_result(
                client,
                False,
                "transfer",
                "Item cannot be transferred while carried.",
                transfer_item.id,
            )
            return
        if not self.item_is_on_client_square(transfer_item, client):
            await self.send_result(
                client,
                False,
                "transfer",
                "Item is not on your square.",
                transfer_item.id,
            )
            return
        can_transfer_any = self.client_has_permission(client, "item.transfer.any")
        can_transfer_own = self.client_has_permission(
            client, "item.transfer.own"
        ) and self._owns_item(client, transfer_item)
        if not can_transfer_any and not can_transfer_own:
            await self.send_result(
                client,
                False,
                "transfer",
                "Not authorized to transfer this item.",
                transfer_item.id,
            )
            return
        target_user_id = str(packet.targetUserId).strip()
        if not target_user_id:
            await self.send_result(
                client,
                False,
                "transfer",
                "Target user is not available.",
                transfer_item.id,
            )
            return
        if transfer_item.createdBy == target_user_id:
            await self.send_result(
                client,
                False,
                "transfer",
                "Item already belongs to that user.",
                transfer_item.id,
            )
            return
        target = next(
            (
                other
                for other in self.clients.values()
                if other.authenticated and other.user_id == target_user_id
            ),
            None,
        )
        target_username = (
            target.username
            if target and target.username
            else target.nickname
            if target
            else self.auth_service.get_username_by_id(target_user_id) or target_user_id
        )
        transfer_item.createdBy = target_user_id
        transfer_item.createdByName = target_username
        transfer_item.updatedAt = self.item_service.now_ms()
        actor_id, actor_name = self._item_updated_actor(client)
        transfer_item.updatedBy = actor_id
        transfer_item.updatedByName = actor_name
        transfer_item.version += 1
        await self.broadcast_item(transfer_item)
        self.request_state_save()
        item_text = f"{transfer_item.title} ({self._item_type_label(transfer_item)})"
        await self.delivery.broadcast(
            BroadcastChatMessagePacket(
                type="chat_message",
                message=f"{client.nickname} transferred {item_text} to {target_username}.",
                system=True,
            ),
            exclude=client,
        )
        await self.send_result(
            client,
            True,
            "transfer",
            f"You transferred {item_text} to {target_username}.",
            transfer_item.id,
        )
        return

    async def _handle_use(
        self, client: ClientConnection, packet: ItemUsePacket
    ) -> None:
        """Handle one use item packet."""

        if not self.client_has_permission(client, "item.use"):
            await self.send_result(client, False, "use", "Not authorized to use items.")
            return
        use_item = self.items.get(packet.itemId)
        if not use_item:
            await self.send_result(client, False, "use", "Item not found.")
            return
        if use_item.carrierId not in (None, client.id):
            await self.send_result(
                client, False, "use", "Item is not available.", use_item.id
            )
            return
        if use_item.carrierId is None and (
            not self.item_is_on_client_square(use_item, client)
        ):
            await self.send_result(
                client, False, "use", "Item is not on your square.", use_item.id
            )
            return
        if use_item.type == "elevator":
            await self.elevator.use(client, use_item)
            return
        handler = get_item_type_handler(use_item.type)
        now_ms = self.item_service.now_ms()
        cooldown_ms = get_item_use_cooldown_ms(use_item.type)
        last_use_ms = self.item_last_use_ms.get(use_item.id)
        if last_use_ms is not None and now_ms - last_use_ms < cooldown_ms:
            remaining_ms = cooldown_ms - (now_ms - last_use_ms)
            remaining_seconds = max(0.1, round(remaining_ms / 1000, 1))
            await self.send_result(
                client,
                False,
                "use",
                f"{use_item.title} is on cooldown for {remaining_seconds:.1f} s.",
                use_item.id,
            )
            return
        try:
            use_result = handler.use(
                use_item, client.nickname, self.clock.format_display_time
            )
        except ValueError as exc:
            await self.send_result(client, False, "use", str(exc), use_item.id)
            return

        if use_result.updated_params is not None:
            try:
                use_item.params = handler.validate_update(
                    use_item, {**use_item.params, **use_result.updated_params}
                )
            except ValueError as exc:
                await self.send_result(client, False, "use", str(exc), use_item.id)
                return
            use_item.updatedAt = now_ms
            actor_id, actor_name = self._item_updated_actor(client)
            use_item.updatedBy = actor_id
            use_item.updatedByName = actor_name
            self.request_state_save()
            await self.broadcast_item(use_item)

        self.item_last_use_ms[use_item.id] = now_ms
        if use_result.others_message:
            await self.delivery.broadcast(
                BroadcastChatMessagePacket(
                    type="chat_message",
                    message=use_result.others_message,
                    system=True,
                ),
                exclude=client,
            )
        use_sound = self._resolve_item_use_sound(use_item)
        if use_sound:
            sound_x, sound_y, sound_z, sound_zone_id = self.get_sound_source(use_item)
            sound_range = self.get_emit_range(use_item)
            await self.delivery.broadcast(
                ItemUseSoundPacket(
                    type="item_use_sound",
                    itemId=use_item.id,
                    sound=use_sound,
                    x=sound_x,
                    y=sound_y,
                    z=sound_z,
                    acousticZoneId=sound_zone_id,
                    range=sound_range,
                )
            )
        if use_item.type == "clock":
            await self.clock.broadcast_announcement(
                use_item, top_of_hour=False, alarm=False
            )
        if use_item.type == "piano":
            await self.piano.send_status(
                client,
                item_id=use_item.id,
                event="use_mode_entered",
                recording_state="idle",
            )
        await self.send_result(
            client, True, "use", use_result.self_message, use_item.id
        )
        if (
            use_result.delayed_self_message is not None
            and use_result.delayed_others_message is not None
        ):
            asyncio.create_task(
                self._broadcast_wheel_result_after_delay(
                    client=client,
                    self_message=use_result.delayed_self_message,
                    others_message=use_result.delayed_others_message,
                )
            )
        return

    async def _handle_secondary_use(
        self, client: ClientConnection, packet: ItemSecondaryUsePacket
    ) -> None:
        """Handle one secondary use item packet."""

        if not self.client_has_permission(client, "item.use"):
            await self.send_result(
                client, False, "secondary_use", "Not authorized to use items."
            )
            return
        secondary_item = self.items.get(packet.itemId)
        if not secondary_item:
            await self.send_result(client, False, "secondary_use", "Item not found.")
            return
        if secondary_item.carrierId not in (None, client.id):
            await self.send_result(
                client,
                False,
                "secondary_use",
                "Item is not available.",
                secondary_item.id,
            )
            return
        if secondary_item.carrierId is None and (
            not self.item_is_on_client_square(secondary_item, client)
        ):
            await self.send_result(
                client,
                False,
                "secondary_use",
                "Item is not on your square.",
                secondary_item.id,
            )
            return
        handler = get_item_type_handler(secondary_item.type)
        if handler.secondary_use is None:
            await self.send_result(
                client,
                False,
                "secondary_use",
                f"No secondary action for {secondary_item.title}.",
                secondary_item.id,
            )
            return
        if secondary_item.type == "radio_station" and not (
            str(secondary_item.params.get("stationName", "")).strip()
            or str(secondary_item.params.get("nowPlaying", "")).strip()
        ):
            stream_url = str(secondary_item.params.get("streamUrl", "")).strip()
            if stream_url:
                metadata = await self.radio.fetch_metadata_safely(stream_url)
                await self.radio.apply_metadata([secondary_item], metadata)
        try:
            secondary_result = handler.secondary_use(
                secondary_item,
                client.nickname,
                self.clock.format_display_time,
            )
        except ValueError as exc:
            await self.send_result(
                client, False, "secondary_use", str(exc), secondary_item.id
            )
            return
        if secondary_result.updated_params is not None:
            try:
                secondary_item.params = handler.validate_update(
                    secondary_item,
                    {**secondary_item.params, **secondary_result.updated_params},
                )
            except ValueError as exc:
                await self.send_result(
                    client, False, "secondary_use", str(exc), secondary_item.id
                )
                return
            secondary_item.updatedAt = self.item_service.now_ms()
            actor_id, actor_name = self._item_updated_actor(client)
            secondary_item.updatedBy = actor_id
            secondary_item.updatedByName = actor_name
            secondary_item.version += 1
            self.request_state_save()
            await self.broadcast_item(secondary_item)
        if secondary_result.others_message.strip():
            await self.delivery.broadcast(
                BroadcastChatMessagePacket(
                    type="chat_message",
                    message=secondary_result.others_message,
                    system=True,
                ),
                exclude=client,
            )
        await self.send_result(
            client,
            True,
            "secondary_use",
            secondary_result.self_message,
            secondary_item.id,
        )
        return

    async def _handle_piano_note(
        self, client: ClientConnection, packet: ItemPianoNotePacket
    ) -> None:
        """Handle one piano note item packet."""

        await self.piano.handle_note(client, packet)
        return

    async def _handle_piano_recording(
        self, client: ClientConnection, packet: ItemPianoRecordingPacket
    ) -> None:
        """Handle one piano recording item packet."""

        await self.piano.handle_recording_action(client, packet)
        return

    async def _handle_update(
        self, client: ClientConnection, packet: ItemUpdatePacket
    ) -> None:
        """Handle one update item packet."""

        update_item = self.items.get(packet.itemId)
        if not update_item:
            await self.send_result(client, False, "update", "Item not found.")
            return
        if update_item.carrierId not in (None, client.id):
            await self.send_result(
                client,
                False,
                "update",
                "Item is not available for editing.",
                update_item.id,
            )
            return
        if update_item.carrierId is None and (
            not self.item_is_on_client_square(update_item, client)
        ):
            await self.send_result(
                client,
                False,
                "update",
                "Item is not on your square.",
                update_item.id,
            )
            return
        can_edit_any = self.client_has_permission(client, "item.edit.any")
        can_edit_own = self.client_has_permission(
            client, "item.edit.own"
        ) and self._owns_item(client, update_item)
        if not can_edit_any and not can_edit_own:
            await self.send_result(
                client,
                False,
                "update",
                "Not authorized to edit this item.",
                update_item.id,
            )
            return
        if packet.title is not None:
            title = packet.title.strip()
            if not title:
                await self.send_result(
                    client,
                    False,
                    "update",
                    "Title cannot be empty.",
                    update_item.id,
                )
                return
            update_item.title = title[:80]
        if packet.params:
            next_params = {**update_item.params, **packet.params}
            handler = get_item_type_handler(update_item.type)
            try:
                next_params = handler.validate_update(update_item, next_params)
            except ValueError as exc:
                await self.send_result(
                    client, False, "update", str(exc), update_item.id
                )
                return
            update_item.params = next_params
        update_item.updatedAt = self.item_service.now_ms()
        actor_id, actor_name = self._item_updated_actor(client)
        update_item.updatedBy = actor_id
        update_item.updatedByName = actor_name
        update_item.version += 1
        await self.broadcast_item(update_item)
        self.request_state_save()
        await self.send_result(
            client, True, "update", f"Updated {update_item.title}.", update_item.id
        )
        return

    @staticmethod
    def _item_type_label(item: WorldItem) -> str:
        """Return user-facing item type wording for chat/status strings."""

        return "radio" if item.type == "radio_station" else item.type

    @staticmethod
    def _resolve_item_use_sound(item: WorldItem) -> str | None:
        """Resolve one-shot use sound, preferring per-item param override."""

        param_sound = item.params.get("useSound")
        if isinstance(param_sound, str):
            token = param_sound.strip()
            if token:
                return token
            return None
        if isinstance(item.useSound, str) and item.useSound.strip():
            return item.useSound.strip()
        return None

    @staticmethod
    def _format_display_sound_name(value: object) -> str:
        """Return display-friendly sound token (file name only) for item property menus."""

        raw = str(value or "").strip()
        if not raw:
            return "none"
        if raw.lower() == "none":
            return "none"
        without_query = raw.split("?", 1)[0].split("#", 1)[0]
        segments = [segment for segment in without_query.split("/") if segment]
        return segments[-1] if segments else raw

    @staticmethod
    def _format_display_timestamp_ms(value: int) -> str:
        """Format epoch milliseconds to compact UTC text used in item property menus."""

        dt = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")

    def _build_item_display_values(self, item: WorldItem) -> dict[str, str]:
        """Build server-authoritative item property display values for readonly/system fields."""

        carrier_label = "none"
        if item.carrierId:
            carrier = self.get_client_by_id(item.carrierId)
            carrier_label = carrier.nickname if carrier is not None else item.carrierId
        return {
            "type": item.type,
            "x": str(item.x),
            "y": str(item.y),
            "z": str(item.z),
            "carrierId": carrier_label,
            "version": str(item.version),
            "createdBy": item.createdByName or item.createdBy,
            "updatedBy": item.updatedByName or item.updatedBy,
            "createdAt": self._format_display_timestamp_ms(item.createdAt),
            "updatedAt": self._format_display_timestamp_ms(item.updatedAt),
            "capabilities": ", ".join(item.capabilities)
            if item.capabilities
            else "none",
            "useSound": self._format_display_sound_name(
                item.params.get("useSound", item.useSound)
            ),
            "emitSound": self._format_display_sound_name(
                item.params.get("emitSound", item.emitSound)
            ),
        }

    def outbound_item(self, item: WorldItem) -> WorldItem:
        """Return one outbound item snapshot enriched with server-owned display values."""

        return item.model_copy(
            update={"display": self._build_item_display_values(item)}
        )

    @staticmethod
    def _item_updated_actor(client: ClientConnection) -> tuple[str, str]:
        """Resolve `(actor_id, actor_name)` used in item update tracking fields."""

        actor_id = client.user_id or client.id
        actor_name = client.username or client.nickname or actor_id
        return actor_id, actor_name

    @staticmethod
    def _owns_item(client: ClientConnection, item: WorldItem) -> bool:
        """Return whether the authenticated client is the creator/owner of an item."""

        if not client.user_id:
            return False
        return item.createdBy == client.user_id

    def get_emit_range(self, item: WorldItem) -> int:
        """Return effective emit range for one item with sane bounds."""

        value = item.params.get("emitRange")
        if isinstance(value, (int, float)):
            emit_range = int(value)
            if emit_range > 0:
                return emit_range
        definition = get_item_definition(item.type)
        if isinstance(definition.emit_range, int) and definition.emit_range > 0:
            return definition.emit_range
        return 15

    def has_listener_in_range(self, item: WorldItem) -> bool:
        """Return whether any connected user is currently inside item hear range."""

        emit_range = self.get_emit_range(item)
        for client in self.clients.values():
            elevator = (
                self.items.get(client.elevator_id) if client.elevator_id else None
            )
            if elevator is not None and ElevatorCar(elevator).phase == "moving":
                continue
            if client.z != item.z:
                continue
            if max(abs(client.x - item.x), abs(client.y - item.y)) <= emit_range:
                return True
        return False

    def get_sound_source(self, item: WorldItem) -> tuple[int, int, int, str]:
        """Resolve position and acoustic zone for an item-emitted one-shot."""

        if item.carrierId:
            carrier = self.get_client_by_id(item.carrierId)
            if carrier is not None:
                return (
                    carrier.x,
                    carrier.y,
                    carrier.z,
                    client_acoustic_zone_id(carrier),
                )
        return item.x, item.y, item.z, floor_acoustic_zone_id(item.z)

    def item_is_on_client_square(
        self, item: WorldItem, client: ClientConnection
    ) -> bool:
        """Return whether one non-carried item shares the client's world cell."""

        if client.elevator_id == item.id:
            return True
        return self.item_service.item_occupies_position(
            item,
            x=client.x,
            y=client.y,
            z=client.z,
        )

    def _item_footprint_in_bounds(self, item: WorldItem) -> bool:
        """Return whether every occupied cell is inside the horizontal grid."""

        return all(
            self.host._is_in_bounds(
                item.x + int(offset.get("x", 0)),
                item.y + int(offset.get("y", 0)),
            )
            for offset in item.occupiedOffsets
        )

    async def send_result(
        self,
        client: ClientConnection,
        ok: bool,
        action: Literal[
            "add",
            "pickup",
            "drop",
            "delete",
            "transfer",
            "use",
            "secondary_use",
            "update",
        ],
        message: str,
        item_id: str | None = None,
    ) -> None:
        """Send a structured item action result to one client."""

        await self.delivery.send(
            client,
            ItemActionResultPacket(
                type="item_action_result",
                ok=ok,
                action=action,
                message=message,
                itemId=item_id,
            ),
        )

    async def broadcast_item(self, item: WorldItem) -> None:
        """Broadcast a full item snapshot update to all connected clients."""

        await self.delivery.broadcast(
            ItemUpsertPacket(type="item_upsert", item=self.outbound_item(item))
        )

    async def _broadcast_wheel_result_after_delay(
        self,
        client: ClientConnection,
        self_message: str,
        others_message: str,
        delay_seconds: float = 3.0,
    ) -> None:
        """Delay then publish wheel result text to self and other users."""

        await asyncio.sleep(delay_seconds)
        await self.delivery.broadcast(
            BroadcastChatMessagePacket(
                type="chat_message", message=others_message, system=True
            ),
            exclude=client,
        )
        if client.websocket in self.clients:
            await self.delivery.send(
                client,
                BroadcastChatMessagePacket(
                    type="chat_message", message=self_message, system=True
                ),
            )
