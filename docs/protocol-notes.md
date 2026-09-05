# Protocol Notes

This is a behavior guide for packet semantics beyond raw schemas.

## Direction

- Client packet schema lives in `server/app/models.py` (`ClientPacket`).
- Browser-side validation/parsing lives in `client/src/network/protocol.ts`.
- Keep these synchronized on every protocol change.

## Client -> Server

- `auth_register`: create account with username/password and optional email.
- `auth_login`: authenticate with username/password.
- `auth_resume`: resume prior session via stored session token.
- `auth_logout`: revoke current session and disconnect.
- `welcome_ready`: client confirms it accepted `welcome` preflight and is ready to join active roster.
- `admin_roles_list`: request server role list (with user counts + permission sets).
- `admin_role_create`: create role.
- `admin_role_update_permissions`: replace one role permission set.
- `admin_role_delete`: delete role with replacement role reassignment.
- `admin_users_list`: request the read-only registered-user list with no `action` (`user.list` permission), or request targets for an administration action (`action`: `set_role | ban | unban | delete_account`, gated by that action's permission).
- `admin_user_set_role`: set target user role.
- `admin_user_ban` / `admin_user_unban`: disable/enable user account.
- `admin_user_delete`: permanently delete target account.
- `update_position`: client movement intent with `x`, `y`, and `z`; server enforces bounds, wall crossings, rate policy, and an unchanged floor.
- `teleport_complete`: client signals an `x`, `y`, `z` teleport landing; server rejects direct floor changes and rebroadcasts the spatial cue.
- `update_nickname`: nickname change request (server enforces uniqueness).
- `chat_message`: player chat.
- `ping`: latency measurement.
- `item_add`, `item_pickup`, `item_drop`, `item_delete`, `item_use`, `item_update`: item actions.
- `item_transfer_targets`: request transfer target accounts for one item (includes online + offline active users, excluding current owner).
- `item_transfer`: transfer item ownership to another account (`targetUserId` required).
- `item_secondary_use`: trigger type-specific secondary action when implemented.
- `item_piano_note`: realtime piano note on/off for active piano use mode.
- `item_piano_recording`: piano record/playback control (`toggle_record`, `playback`, `stop_playback`).
- `structure_add_wall`: create a one-edge wall from a server preset on the requested side of the builder's current square.
- `structure_resize_wall`: decrease or increase one complete wall run's inclusive first/last occupied edge anchor by one; the result reports only the authoritative anchor `x, y, z`.
- `structure_slide_wall`: move a complete horizontal run along y or a complete vertical run along x by one edge, preserving its length.
- `structure_rotate_wall`: set a run to horizontal or vertical while preserving its canonical start coordinate and length.
- `structure_update_wall`: update one wall run's explicit `soundTransmission`, `occlusionLowpassHz`, and/or `contactSound`; supplying `preset` reapplies all server-owned defaults for that type before optional explicit overrides.
- `structure_delete`: delete one complete wall run.

## Server -> Client

- `auth_required`: authentication challenge after websocket connect.
  - includes `gridName`, `welcomeMessage`, `serverVersion`, and `expectedClientRevision`.
- `auth_result`: auth success/failure and session/account metadata.
- `auth_permissions`: server-pushed live role/permission refresh for current session.
- `admin_roles_list`: role list response payload.
- `admin_users_list`: user list response payload, including `online` and the
  server-owned `lastSeenAt` Unix timestamp in milliseconds.
- `admin_action_result`: structured result for user-list authorization and admin actions.
  - admin mutations include `user_delete` for account deletion.
- `welcome`: initial snapshot with users/items plus server UI/world metadata.
  - Server delays roster activation until `welcome_ready`, then publishes the new user's position and nickname before the login announcement.
- `livekit_token`: short-lived authenticated LiveKit room token and public WebSocket URL.
  - Issued only after authentication when complete LiveKit configuration is enabled.
  - The API secret is never sent to the browser.
- `update_position`, `update_nickname`, `user_left`: presence updates. `welcome.player`, `welcome.users[]`, and `update_position` carry the server-owned `acousticZoneId` (`floor:<z>` or `elevator:<itemId>`). Activation publishes position followed by nickname so existing clients can hydrate a complete peer entry immediately.
- `teleport_complete`: peer teleport landing event with spatial coordinates and source `acousticZoneId`.
- `chat_message`: system and user chat stream.
- `pong`: ping response.
- `nickname_result`: accepted/rejected nickname result.
- `item_upsert`: full item replacement after mutation.
- `item_remove`: item deletion.
- `item_action_result`: action success/failure and user-facing message.
- `item_transfer_targets`: transfer target account list for one item.
- `item_use_sound`: spatial one-shot sound on successful item use (if `useSound` configured), including source `acousticZoneId`.
- `item_clock_announce`: ordered list of clock speech samples to play sequentially as spatial audio, including source `acousticZoneId`.
- `item_piano_note`: broadcast piano note on/off with resolved instrument/envelope/spatial params.
- `item_piano_status`: structured piano mode/record/playback state events for client runtime control.
- `item_elevator_status`: targeted rider state (`entered`, `moving`, `arrived`, or `exited`) with `itemId`, `z`, and an optional user-facing `message`.
- `structure_upsert`: full wall-run replacement after a live create, resize, slide, rotation, or property update.
- `structure_remove`: removal of one wall run.
- `structure_action_result`: success/error and user-facing status for add, resize, slide, rotate, update, or delete. Successful edit results contain only the authoritative new coordinate/property value so shared controls do not receive a second narrative announcement.
- `world_sound`: server-validated positional structure contact sound (`sound`, `x`, `y`, `z`, `acousticZoneId`, optional `range`) sent to users other than the mover.

## Item Packet Behavior

- `item_upsert` is full-state replacement for one item, not partial patch.
- `item_upsert.item.display` is server-owned display text for readonly/system properties (for example: `createdBy`, `updatedBy`, `createdAt`, `updatedAt`, `capabilities`, `useSound`, `emitSound`).
- `item_action_result` messages are intended for direct screen-reader/user status feedback.
  - `action` includes: `add`, `pickup`, `drop`, `delete`, `transfer`, `use`, `secondary_use`, `update`
- Successful `item_pickup` and `item_drop` also emit system chat lines to other users in the room.
- Item transfer ownership is account-based; target accounts do not need to be currently connected.
- Piano runtime control no longer depends on parsing `item_action_result.message` text.
- `item_piano_status` carries machine-readable piano events (`use_mode_entered`, record/playback transitions).
- `item_use_sound` contains absolute item world coordinates (`x`, `y`, `z`), source `acousticZoneId`, and sound path.
  - For carried items, source coordinates and acoustic zone resolve to the carrier's current placement.
- `item_clock_announce` contains:
  - `itemId`
  - `sounds`: ordered sample URLs (EL640 phrase parts)
  - absolute source coordinates `x`, `y`, `z`
  - source `acousticZoneId`
  - generated by server for manual clock `use`, top-of-hour auto announce, and alarm auto announce (when enabled)
- `teleport_complete` contains absolute player world coordinates (`x`, `y`, `z`) and `acousticZoneId` at teleport landing.
- Radio metadata (`params.stationName`, `params.nowPlaying`) is server-managed and delivered through normal `item_upsert` updates.
- `item_piano_note` contains:
  - `itemId`, `senderId`, `keyId`, `midi`, `on`
  - resolved `instrument`, `voiceMode`, `octave`, `attack`, `decay`, `release`, `brightness`, `emitRange`
  - absolute source coordinates `x`, `y`, `z`
- `item_upsert.item.occupiedOffsets` contains the server-owned horizontal footprint relative to the item anchor.
- Elevator travel sends progressive intermediate `update_position.z` values so rider coordinates advance throughout the trip while remaining outside both floor audio and visibility groups until arrival.
- Elevator entry and exit also broadcast `update_position` even when coordinates do not change, so every client updates the rider's acoustic-zone membership immediately.
- Elevator opening, closing, and direction cues send one `item_use_sound` in the current landing's floor zone; the direction cue is sent immediately before opening so both sounds overlap. Landing listeners hear these external sources directly, while riders hear the same source through the opening/closing door transmission.

## Welcome Metadata

- `welcome.auth`: authenticated account identity:
  - `authenticated`
  - `userId`
  - `username`
  - `role`
  - `permissions`
  - `policy` (`usernameMinLength`, `usernameMaxLength`, `passwordMinLength`, `passwordMaxLength`)
- `auth_required.authPolicy`: server auth limits advertised before login/register submit.
- `auth_required.gridName` / `auth_required.welcomeMessage`: server-owned pre-login branding values.
- `auth_required.serverVersion`: server diagnostics version text shown in connect/reconnect messaging.
- `auth_required.expectedClientRevision`: authoritative browser asset revision required by this server instance.
- `auth_result.authPolicy`: server auth limits echoed on auth success/failure responses.
- `auth_result.sessionToken` is used by the client to call the instance-scoped HTTP endpoint `GET <base_path>auth/session/set` (`Authorization: Bearer <sessionToken>`, `X-Chgrid-Auth-Client: 1`) so the server can issue an instance-scoped `HttpOnly` session cookie.
- `welcome.worldConfig.gridSize`: server-authoritative grid size used by clients for bounds/drawing.
- `welcome.worldConfig.movementTickMs`: server movement-rate window used for client movement pacing.
- `welcome.worldConfig.movementMaxStepsPerTick`: max allowed grid steps per movement window.
- `welcome.worldConfig.floors`: server-owned floor ids, display names, and exact `z` elevations.
- `welcome.worldConfig.elevatorDoorClipSeconds`: server-owned open/close clip lengths in seconds used by the client for the door transmission ramp.
- `welcome.worldConfig.structurePresets`: server-configured wall defaults exposed to World Builder.
- `welcome.structures`: canonical wall-run snapshot stored separately from items.
- `welcome.player`: server-assigned spawn/current self position at connect time.
- `welcome.serverInfo`: server process identity/version metadata:
  - `instanceId`: unique id generated at server startup
  - `releaseVersion`: shared public release version
  - `serverVersion`: server diagnostics version text (`release + server revision`)
  - `expectedClientRevision`: browser asset revision required by this server instance
  - `gridName`: server-owned user-facing grid name
  - `welcomeMessage`: server-owned pre-login welcome string
- `welcome.uiDefinitions`: server-provided item UI definitions:
  - `itemTypeOrder`: add-item menu order
  - `itemTypes[].tooltip`: item-level tooltip/help text
  - `itemTypes[].capabilities`: server-declared actions supported by the type
  - `itemTypes[].editableProperties`: editable property keys by item type
  - `itemTypes[].propertyMetadata`: property-level metadata (`valueType`, optional `label`, optional `range`, optional `tooltip`, optional `maxLength`, optional `options`, optional `visibleWhen`)
  - `itemTypes[].globalProperties`: non-editable global values (`useSound`, `emitSound`, `useCooldownMs`, `emitRange`, `directional`, `emitSoundSpeed`, `emitSoundTempo`, `emitInitialDelay`, `emitLoopDelay`)
  - `commandMetadata.mainModeActions`: server-authored labels/tooltips for server-backed main-mode commands used by the client command palette
  - `itemManagement.actions`: server-authored labels/tooltips and permission-key metadata for item-management actions (`transfer`, `delete`)
  - `adminMenu.actions`: server-authored admin root menu labels/tooltips/ordering for the authenticated user
- Maintainer note: the current server-owned command/menu metadata definitions live in `server/app/ui_metadata.py`.
- Client item UI requires this metadata from the server; there is no fallback item definition map.
- Client property help/type rendering is metadata-driven; it does not infer fallback types/tooltips from hardcoded key heuristics.
- `visibleWhen` supports equality checks and string negation via `!` prefix (example: `{"mediaEffect": "!off"}`).

## Validation Boundaries

- Server is authoritative for all action validation and normalization.
- Server is authoritative for movement acceptance (bounds + wall crossing + rate/delta checks) and rejects client attempts to change `z`.
- Cardinal movement is blocked by a wall on its crossed edge. Diagonal movement is blocked when both possible two-step routes around the shared corner contain a blocking wall.
- Acoustic rays distinguish wall endpoints from interior joints: grazing an actual run endpoint contributes half-strength gain/filtering, while a continuing wall or two-wall corner contributes fully.
- Structure mutations require `world.structure.edit`, which defaults to the built-in `editor` and `admin` roles.
- Server persists account state (last nickname + last position) and restores spawn from that state on auth login/resume.
- Server also supports websocket handshake cookie resume:
  - accepts browser sockets only when websocket `Origin` matches `CHGRID_HOST_ORIGIN`
  - websocket and auth helper routes are scoped under the configured `server.base_path`
  - reads the instance-scoped session cookie from the websocket `Cookie` header
  - attempts resume before sending `auth_required`
  - exposes `GET <base_path>auth/session/clear` to expire the `HttpOnly` cookie (`X-Chgrid-Auth-Client: 1` and matching `Origin` required)
- Server applies auth hardening before accepting login/register/resume:
  - login/register PBKDF2 work runs off the event loop in bounded worker concurrency
  - repeated auth failures are rate-limited by IP and IP+identity windows
  - auth failures include small randomized response jitter to reduce high-resolution probing
- Client validates incoming packet shapes and applies runtime behavior.
- Server is authoritative for role/permission checks on every privileged packet.
- `voice.send` permission changes are pushed at runtime via `auth_permissions`.
- LiveKit token publish permission is derived from `voice.send`; Chat Grid remains authoritative for all account and world permissions.
- Sound/media field normalization uses shared server policy helpers:
  - `none/off` normalize to empty values
  - bare filenames normalize to `sounds/<name>` for sound-reference fields
  - media URL-like fields are trimmed/validated consistently
  - radio stream metadata fetches only follow validated public `http`/`https` URLs and revalidate redirect hops
- Client-side item edit validation is convenience only; server remains source of truth.

## Heartbeat/Stale Recovery

- Client sends automatic heartbeat `ping` packets every 10 seconds while connected.
- Heartbeat pings use negative `clientSentAt` ids and are internal (not user-visible ping status).
- If websocket close is observed unexpectedly, client starts reconnect flow.
- If a heartbeat `pong` is missed for one interval (10 seconds), client also starts reconnect flow.
- Reconnect flow waits 5 seconds and retries up to 3 times before stopping.
- After reconnect, if `welcome.serverInfo.instanceId` changed, client announces `Server restarted.`
- Client emits `Connected to server. Version <version>.` on initial `welcome` and
  `Reconnected to server. Version <version>.` after reconnect.
- If `auth_required.expectedClientRevision` or `welcome.serverInfo.expectedClientRevision` differs from the running client revision, client auto-reloads.
- Server-only version changes do not trigger browser reload unless `expectedClientRevision` also changes.
