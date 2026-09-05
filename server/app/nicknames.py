"""Nickname uniqueness, update validation, and change announcements."""

from __future__ import annotations

from collections.abc import Callable
import logging

from websockets.asyncio.server import ServerConnection

from .auth_service import AuthService
from .client import ClientConnection
from .delivery import Delivery
from .models import (
    BroadcastChatMessagePacket,
    BroadcastNicknamePacket,
    ClientPacket,
    NicknameResultPacket,
    UpdateNicknamePacket,
)

LOGGER = logging.getLogger("chgrid.server")


class Nicknames:
    """Nickname uniqueness and the update_nickname path."""

    def __init__(
        self,
        *,
        delivery: Delivery,
        roster: dict[ServerConnection, ClientConnection],
        auth_service: AuthService,
        has_permission: Callable[[ClientConnection, str], bool],
    ) -> None:
        """Bind delivery, the active roster, and account permission services."""

        self.delivery = delivery
        self.roster = roster
        self.auth_service = auth_service
        self.has_permission = has_permission

    @staticmethod
    def key(nickname: str) -> str:
        """Normalize nickname for case-insensitive comparisons."""

        return nickname.casefold()

    def is_taken(self, nickname: str, exclude_client_id: str | None = None) -> bool:
        """Check whether nickname is already used by another active client."""

        wanted = self.key(nickname)
        for other in self.roster.values():
            if exclude_client_id is not None and other.id == exclude_client_id:
                continue
            if self.key(other.nickname) == wanted:
                return True
        return False

    async def handle_packet(
        self, client: ClientConnection, packet: ClientPacket
    ) -> bool:
        """Validate a nickname update and announce any accepted change."""

        if not isinstance(packet, UpdateNicknamePacket):
            return False

        requested = packet.nickname
        accepted = False
        reason = None
        if not self.has_permission(client, "profile.update_nickname"):
            reason = "Not authorized to change nickname."
        elif not packet.nickname.strip():
            reason = "Nickname is required."
        else:
            requested = packet.nickname.strip()
            if self.is_taken(requested, exclude_client_id=client.id):
                reason = "Nickname already in use."
            else:
                accepted = True

        old_nickname = client.nickname
        await self.delivery.send(
            client,
            NicknameResultPacket(
                type="nickname_result",
                accepted=accepted,
                requestedNickname=requested,
                effectiveNickname=requested if accepted else client.nickname,
                reason=reason,
            ),
        )
        if not accepted or requested == old_nickname:
            return True

        client.nickname = requested
        if client.user_id:
            self.auth_service.set_last_nickname(client.user_id, client.nickname)
        if old_nickname == "user...":
            LOGGER.info("user login id=%s nickname=%s", client.id, client.nickname)
        else:
            LOGGER.info(
                "nickname change id=%s old=%s new=%s",
                client.id,
                old_nickname,
                client.nickname,
            )
        await self.delivery.broadcast(
            BroadcastNicknamePacket(
                type="update_nickname", id=client.id, nickname=client.nickname
            ),
            exclude=client,
        )
        if old_nickname == "user...":
            await self.delivery.broadcast(
                BroadcastChatMessagePacket(
                    type="chat_message",
                    message=f"{client.nickname} has logged in.",
                    system=True,
                ),
                exclude=client,
            )
        else:
            await self.delivery.broadcast(
                BroadcastChatMessagePacket(
                    type="chat_message",
                    message=f"{old_nickname} is now known as {client.nickname}.",
                    system=True,
                ),
                exclude=client,
            )
        self_message = (
            f"Welcome. Logged in as {client.nickname}."
            if old_nickname == "user..."
            else f"You are now known as {client.nickname}."
        )
        await self.delivery.send(
            client,
            BroadcastChatMessagePacket(
                type="chat_message",
                message=self_message,
                system=True,
            ),
        )
        return True
