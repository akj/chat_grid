"""Pydantic packet and entity models shared across server message handling."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

FacingDeg: TypeAlias = Literal[0, 45, 90, 135, 180, 225, 270, 315]


class BasePacket(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str


class UpdatePositionPacket(BasePacket):
    type: Literal["update_position"]
    x: int
    y: int
    z: int


class UpdateFacingPacket(BasePacket):
    """Request one of the eight canonical player facing headings."""

    type: Literal["update_facing"]
    facingDeg: FacingDeg


class TurnPacket(BasePacket):
    """Request one relative 45-degree player turn."""

    type: Literal["turn"]
    direction: Literal["left", "right"]


class TeleportCompletePacket(BasePacket):
    type: Literal["teleport_complete"]
    x: int
    y: int
    z: int


class UpdateNicknamePacket(BasePacket):
    type: Literal["update_nickname"]
    nickname: str = Field(min_length=1, max_length=32)


class ChatMessagePacket(BasePacket):
    type: Literal["chat_message"]
    message: str = Field(min_length=1, max_length=500)


class AuthRegisterPacket(BasePacket):
    type: Literal["auth_register"]
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=320)


class AuthLoginPacket(BasePacket):
    type: Literal["auth_login"]
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class AuthResumePacket(BasePacket):
    type: Literal["auth_resume"]
    sessionToken: str = Field(min_length=1, max_length=512)


class AuthLogoutPacket(BasePacket):
    type: Literal["auth_logout"]


class WelcomeReadyPacket(BasePacket):
    type: Literal["welcome_ready"]


class LiveKitTokenRequestPacket(BasePacket):
    """Request fresh voice credentials for the current authenticated session."""

    type: Literal["livekit_token_request"]


class AdminRolesListPacket(BasePacket):
    type: Literal["admin_roles_list"]


class AdminRoleCreatePacket(BasePacket):
    type: Literal["admin_role_create"]
    name: str = Field(min_length=1, max_length=32)


class AdminRoleUpdatePermissionsPacket(BasePacket):
    type: Literal["admin_role_update_permissions"]
    role: str = Field(min_length=1, max_length=32)
    permissions: list[str]


class AdminRoleDeletePacket(BasePacket):
    type: Literal["admin_role_delete"]
    role: str = Field(min_length=1, max_length=32)
    replacementRole: str = Field(min_length=1, max_length=32)


class AdminUsersListPacket(BasePacket):
    type: Literal["admin_users_list"]
    action: Literal["set_role", "ban", "unban", "delete_account"] | None = None


class AdminUserSetRolePacket(BasePacket):
    type: Literal["admin_user_set_role"]
    username: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=32)


class AdminUserBanPacket(BasePacket):
    type: Literal["admin_user_ban"]
    username: str = Field(min_length=1, max_length=128)


class AdminUserUnbanPacket(BasePacket):
    type: Literal["admin_user_unban"]
    username: str = Field(min_length=1, max_length=128)


class AdminUserDeletePacket(BasePacket):
    type: Literal["admin_user_delete"]
    username: str = Field(min_length=1, max_length=128)


class PingPacket(BasePacket):
    type: Literal["ping"]
    clientSentAt: int


class ItemAddPacket(BasePacket):
    type: Literal["item_add"]
    itemType: str = Field(min_length=1)


class ItemPickupPacket(BasePacket):
    type: Literal["item_pickup"]
    itemId: str


class ItemDropPacket(BasePacket):
    type: Literal["item_drop"]
    itemId: str
    x: int
    y: int
    z: int


class ItemDeletePacket(BasePacket):
    type: Literal["item_delete"]
    itemId: str


class ItemTransferPacket(BasePacket):
    type: Literal["item_transfer"]
    itemId: str
    targetUserId: str


class ItemTransferTargetsPacket(BasePacket):
    type: Literal["item_transfer_targets"]
    itemId: str


class ItemHandPacket(BasePacket):
    """Hand a carried item to an eligible user without changing ownership."""

    type: Literal["item_hand"]
    itemId: str
    targetUserId: str


class ItemHandTargetsPacket(BasePacket):
    """Request currently eligible recipients for a carried item."""

    type: Literal["item_hand_targets"]
    itemId: str


class ItemUsePacket(BasePacket):
    type: Literal["item_use"]
    itemId: str


class ItemSecondaryUsePacket(BasePacket):
    type: Literal["item_secondary_use"]
    itemId: str


class ItemPianoNotePacket(BasePacket):
    type: Literal["item_piano_note"]
    itemId: str
    keyId: str = Field(min_length=1, max_length=32)
    midi: int = Field(ge=0, le=127)
    on: bool


class ItemPianoRecordingPacket(BasePacket):
    type: Literal["item_piano_recording"]
    itemId: str
    action: Literal["toggle_record", "playback", "stop_playback", "stop_record"]


class ItemUpdatePacket(BasePacket):
    type: Literal["item_update"]
    itemId: str
    title: str | None = Field(default=None, max_length=80)
    params: dict | None = None


class StructureAddWallPacket(BasePacket):
    """Request a one-edge wall from a configured preset beside the builder."""

    type: Literal["structure_add_wall"]
    preset: str = Field(min_length=1, max_length=40)
    direction: Literal["north", "south", "east", "west"]


class StructureResizeWallPacket(BasePacket):
    """Move one endpoint of an existing wall run by one grid edge."""

    type: Literal["structure_resize_wall"]
    structureId: str
    endpoint: Literal["start", "end"]
    delta: Literal[-1, 1]


class StructureSlideWallPacket(BasePacket):
    """Translate a complete wall run by one perpendicular grid edge."""

    type: Literal["structure_slide_wall"]
    structureId: str
    delta: Literal[-1, 1]


class StructureRotateWallPacket(BasePacket):
    """Rotate a wall run around its canonical start coordinate."""

    type: Literal["structure_rotate_wall"]
    structureId: str
    orientation: Literal["horizontal", "vertical"]


class StructureUpdateWallPacket(BasePacket):
    """Update editable properties or reapply a preset to a wall run."""

    type: Literal["structure_update_wall"]
    structureId: str
    preset: str | None = Field(default=None, min_length=1, max_length=40)
    soundTransmission: float | None = Field(default=None, ge=0.0, le=1.0)
    occlusionLowpassHz: int | None = Field(default=None, ge=20, le=20_000)
    contactSound: str | None = Field(default=None, max_length=200)


class StructureDeletePacket(BasePacket):
    """Delete one complete world structure."""

    type: Literal["structure_delete"]
    structureId: str


ClientPacket = (
    UpdatePositionPacket
    | UpdateFacingPacket
    | TurnPacket
    | TeleportCompletePacket
    | UpdateNicknamePacket
    | ChatMessagePacket
    | AuthRegisterPacket
    | AuthLoginPacket
    | AuthResumePacket
    | AuthLogoutPacket
    | WelcomeReadyPacket
    | LiveKitTokenRequestPacket
    | AdminRolesListPacket
    | AdminRoleCreatePacket
    | AdminRoleUpdatePermissionsPacket
    | AdminRoleDeletePacket
    | AdminUsersListPacket
    | AdminUserSetRolePacket
    | AdminUserBanPacket
    | AdminUserUnbanPacket
    | AdminUserDeletePacket
    | PingPacket
    | ItemAddPacket
    | ItemPickupPacket
    | ItemDropPacket
    | ItemDeletePacket
    | ItemTransferPacket
    | ItemTransferTargetsPacket
    | ItemHandPacket
    | ItemHandTargetsPacket
    | ItemUsePacket
    | ItemSecondaryUsePacket
    | ItemPianoNotePacket
    | ItemPianoRecordingPacket
    | ItemUpdatePacket
    | StructureAddWallPacket
    | StructureResizeWallPacket
    | StructureSlideWallPacket
    | StructureRotateWallPacket
    | StructureUpdateWallPacket
    | StructureDeletePacket
)


class RemoteUser(BaseModel):
    id: str
    userId: str | None = None
    nickname: str
    x: int
    y: int
    z: int
    facingDeg: FacingDeg
    acousticZoneId: str


class WelcomePacket(BasePacket):
    type: Literal["welcome"]
    id: str
    player: RemoteUser
    users: list[RemoteUser]
    items: list[dict] | None = None
    structures: list[dict] | None = None
    worldConfig: dict | None = None
    uiDefinitions: dict | None = None
    serverInfo: dict | None = None
    auth: dict | None = None


class AuthRequiredPacket(BasePacket):
    type: Literal["auth_required"]
    message: str
    authPolicy: dict | None = None
    gridName: str | None = None
    welcomeMessage: str | None = None
    releaseVersion: str | None = None
    expectedClientRevision: str | None = None
    serverVersion: str | None = None


class AuthResultPacket(BasePacket):
    type: Literal["auth_result"]
    ok: bool
    message: str
    sessionToken: str | None = None
    username: str | None = None
    role: str | None = None
    permissions: list[str] | None = None
    adminMenuActions: list[dict[str, str]] | None = None
    nickname: str | None = None
    authPolicy: dict | None = None


class AuthPermissionsPacket(BasePacket):
    type: Literal["auth_permissions"]
    role: str
    permissions: list[str]
    adminMenuActions: list[dict[str, str]] | None = None


class UserLeftPacket(BasePacket):
    type: Literal["user_left"]
    id: str


class BroadcastPositionPacket(BasePacket):
    type: Literal["update_position"]
    id: str
    x: int
    y: int
    z: int
    facingDeg: FacingDeg
    acousticZoneId: str


class BroadcastTeleportCompletePacket(BasePacket):
    type: Literal["teleport_complete"]
    id: str
    x: int
    y: int
    z: int
    facingDeg: FacingDeg
    acousticZoneId: str


class BroadcastNicknamePacket(BasePacket):
    type: Literal["update_nickname"]
    id: str
    nickname: str


class LiveKitTokenPacket(BasePacket):
    """Short-lived LiveKit credentials for one authenticated browser session."""

    type: Literal["livekit_token"]
    token: str
    url: str


class BroadcastChatMessagePacket(BasePacket):
    type: Literal["chat_message"]
    message: str
    senderId: str | None = None
    senderNickname: str | None = None
    system: bool = False
    action: bool = False


class PongPacket(BasePacket):
    type: Literal["pong"]
    clientSentAt: int


class NicknameResultPacket(BasePacket):
    type: Literal["nickname_result"]
    accepted: bool
    requestedNickname: str
    effectiveNickname: str
    reason: str | None = None


class WorldItem(BaseModel):
    id: str
    type: str = Field(min_length=1)
    title: str
    x: int
    y: int
    z: int
    createdBy: str
    createdByName: str
    updatedBy: str
    updatedByName: str
    createdAt: int
    updatedAt: int
    version: int
    capabilities: list[str]
    useSound: str | None = None
    emitSound: str | None = None
    params: dict
    carrierId: str | None = None
    display: dict[str, str] | None = None
    occupiedOffsets: list[dict[str, int]] = Field(
        default_factory=lambda: [{"x": 0, "y": 0}]
    )


class PersistedWorldItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str = Field(min_length=1)
    title: str
    x: int
    y: int
    z: int = 0
    createdBy: str
    createdByName: str | None = None
    updatedBy: str | None = None
    updatedByName: str | None = None
    createdAt: int
    updatedAt: int
    version: int
    params: dict
    carrierId: str | None = None


class ItemUpsertPacket(BasePacket):
    type: Literal["item_upsert"]
    item: WorldItem


class ItemRemovePacket(BasePacket):
    type: Literal["item_remove"]
    itemId: str


class WallStructure(BaseModel):
    """One editable wall run stored separately from world items."""

    id: str
    floorZ: int
    startX: int
    startY: int
    orientation: Literal["horizontal", "vertical"]
    length: int = Field(ge=1)
    title: str = Field(default="Wall", min_length=1, max_length=80)
    movementBlocked: bool = True
    soundTransmission: float = Field(default=0.0, ge=0.0, le=1.0)
    occlusionLowpassHz: int = Field(default=800, ge=20, le=20_000)
    height: int = Field(default=40, ge=0)
    preset: str = Field(default="solid", min_length=1, max_length=40)
    contactSound: str = "/sounds/wall.ogg"


class StructureUpsertPacket(BasePacket):
    """Broadcast a complete canonical structure after a live mutation."""

    type: Literal["structure_upsert"]
    structure: WallStructure


class StructureRemovePacket(BasePacket):
    """Broadcast removal of one canonical structure."""

    type: Literal["structure_remove"]
    structureId: str


class StructureActionResultPacket(BasePacket):
    """Report a World Builder mutation result to its caller."""

    type: Literal["structure_action_result"]
    ok: bool
    action: Literal["add", "resize", "slide", "rotate", "update", "delete"]
    message: str
    structureId: str | None = None


class ItemActionResultPacket(BasePacket):
    type: Literal["item_action_result"]
    ok: bool
    action: Literal[
        "add",
        "pickup",
        "drop",
        "delete",
        "transfer",
        "hand",
        "use",
        "secondary_use",
        "update",
    ]
    message: str
    itemId: str | None = None


class ItemTransferTargetSummary(BaseModel):
    userId: str
    username: str
    online: bool


class ItemTransferTargetsResultPacket(BasePacket):
    type: Literal["item_transfer_targets"]
    itemId: str
    targets: list[ItemTransferTargetSummary]


class ItemHandTargetsResultPacket(BasePacket):
    """List online users who can currently receive the carried item."""

    type: Literal["item_hand_targets"]
    itemId: str
    targets: list[ItemTransferTargetSummary]


class ItemUseSoundPacket(BasePacket):
    type: Literal["item_use_sound"]
    itemId: str
    sound: str
    x: int
    y: int
    z: int
    acousticZoneId: str
    range: int | None = None


class WorldSoundPacket(BasePacket):
    """One server-validated positional sound emitted by world geometry."""

    type: Literal["world_sound"]
    sound: str
    x: int
    y: int
    z: int
    acousticZoneId: str
    range: int | None = None


class ItemClockAnnouncePacket(BasePacket):
    type: Literal["item_clock_announce"]
    itemId: str
    sounds: list[str]
    x: int
    y: int
    z: int
    acousticZoneId: str
    range: int | None = None


class ItemPianoNoteBroadcastPacket(BasePacket):
    type: Literal["item_piano_note"]
    itemId: str
    senderId: str
    keyId: str
    midi: int
    on: bool
    instrument: str
    voiceMode: str
    octave: int
    attack: int
    decay: int
    release: int
    brightness: int
    x: int
    y: int
    z: int
    emitRange: int


class ItemPianoStatusPacket(BasePacket):
    type: Literal["item_piano_status"]
    itemId: str
    event: Literal[
        "use_mode_entered",
        "record_started",
        "record_paused",
        "record_resumed",
        "record_stopped",
        "playback_started",
        "playback_stopped",
    ]
    recordingState: Literal["idle", "recording", "paused", "playback"] | None = None


class ItemElevatorStatusPacket(BasePacket):
    """Targeted elevator occupancy/travel state for one rider."""

    type: Literal["item_elevator_status"]
    itemId: str
    event: Literal["entered", "moving", "arrived", "exited"]
    z: int
    message: str | None = None


class AdminRoleSummary(BaseModel):
    id: int
    name: str
    isSystem: bool
    userCount: int
    permissions: list[str]


class AdminRolesListResultPacket(BasePacket):
    type: Literal["admin_roles_list"]
    roles: list[AdminRoleSummary]
    permissionKeys: list[str]
    permissionTooltips: dict[str, str] | None = None


class AdminUserSummary(BaseModel):
    id: str
    username: str
    role: str
    status: Literal["active", "disabled"]
    lastSeenAt: int
    online: bool = False


class AdminUsersListResultPacket(BasePacket):
    type: Literal["admin_users_list"]
    users: list[AdminUserSummary]


class AdminActionResultPacket(BasePacket):
    type: Literal["admin_action_result"]
    ok: bool
    action: Literal[
        "role_create",
        "role_update_permissions",
        "role_delete",
        "user_list",
        "user_set_role",
        "user_ban",
        "user_unban",
        "user_delete",
    ]
    message: str
