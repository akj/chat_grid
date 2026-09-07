# Runtime Flow

## Connect Flow

1. User clicks connect.
2. Client validates auth form and sets up local media.
3. Client connects signaling websocket from the configured app origin.
4. Server accepts the socket only on the configured instance websocket path and when the browser `Origin` matches `CHGRID_HOST_ORIGIN`, then attempts cookie-based session resume from the instance-scoped websocket handshake cookie.
5. If resume does not authenticate, server sends `auth_required`.
   - includes `gridName` and `welcomeMessage` for pre-login branding.
   - includes `serverVersion` and `expectedClientRevision` for stale-client detection before login.
   - includes `authPolicy` limits for username/password.
6. Client sends `auth_login` or `auth_register` (or explicit `auth_resume` if provided by caller).
7. Server sends `auth_result`.
   - includes role + permissions for authenticated session.
8. Client persists authenticated session into instance-scoped server-managed `HttpOnly` cookie helpers under the active app base path via `GET <base_path>auth/session/set` (`Authorization: Bearer <sessionToken>`, `X-Chgrid-Auth-Client: 1`), and clears it via `GET <base_path>auth/session/clear` on logout/session errors.
   - the optional PHP media proxy validates that same cookie through `GET <base_path>auth/session/check` before relaying media
9. Server sends `welcome` with users, items, structures, and structure-preset snapshots, followed by a short-lived `livekit_token` when LiveKit is configured.
   - `livekit.room_name` is a deployment setting in `server/config.toml`.
   - `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` are required server environment values.
   - Server startup fails with a clear configuration error when any of these values is absent.
10. Client:
   - applies `welcome.worldConfig.gridSize` for authoritative grid bounds/rendering
   - applies `welcome.worldConfig.floors` for floor names and elevations
   - applies `welcome.worldConfig.movementTickMs` as movement pacing guidance
   - applies `welcome.worldConfig.movementMaxStepsPerTick` for movement-rate parity
   - applies `welcome.worldConfig.structurePresets` and the canonical wall snapshot for World Builder, rendering, and collision prediction
   - uses `welcome.player` as authoritative starting position and `facingDeg` (restored from account state on reconnect; new accounts start facing north)
   - records `welcome.serverInfo` (`instanceId`, `releaseVersion`, `serverVersion`, `expectedClientRevision`, `gridName`, `welcomeMessage`) for restart detection and client branding
   - if `welcome.serverInfo.expectedClientRevision` differs from the running client revision, auto-reloads the page
   - applies `welcome.uiDefinitions` for item menus/properties/options, server-backed command metadata, item-management metadata, and admin menu labels/order
   - sends initial `update_position` echo from server-assigned starting tile
   - sends initial `update_nickname`
   - creates peer runtimes for known users
   - joins the LiveKit room with the server-issued token and publishes the processed microphone track
   - syncs item runtimes (`radio`, `emit`)
   - applies audio layer state
   - starts signaling heartbeat monitor
   - starts game loop

## Main Loop

Each frame:

- Handle local movement input.
- Predict bounds and wall collisions, then send movement intents; server remains authoritative on accepted movement updates.
- While connected to the grid, the client's 10-second heartbeat refreshes its
  server-owned last-seen timestamp. Database writes are debounced to 30 seconds,
  with immediate updates on grid activation and disconnect.
- Update spatial voice audio.
- Update spatial radio audio.
- Update spatial item emit audio.
- Trace intervening wall edges for positional audio, multiply their transmission gains, and apply the lowest crossed low-pass cutoff.
- Recompute active sampled world sounds through the shared world-audio router using source/listener acoustic zones, wall transmission, range, distance, and 3D source direction.
- A ray grazing the actual endpoint of a wall run applies half-strength occlusion; exact corners inside a continuing run or formed by two walls remain fully occluded.
- Draw canvas scene.

Radio metadata polling is limited to stations near a listener, deduplicated by stream URL, and uses bounded concurrent fetches so slow stations do not hold up the others. Failed fetches preserve the last known title. Requesting now-playing triggers one immediate fetch when no metadata has been collected yet.

## Spatial Audio And Facing

- Voice, world samples and locate tones, radios, item emitters, elevator ambience, and piano notes use the same positional renderer in `audio/spatial.ts`.
- Both standard and HRTF modes use a browser `PannerNode`; only its panning model changes (`equalpower` or `HRTF`). The application owns distance gain, source directionality, wall filtering, and acoustic-zone transmission. Browser distance attenuation is disabled to avoid applying a second distance curve.
- Listener orientation uses AudioParams where available and `setOrientation` on browsers such as Firefox that expose only the method. Both APIs feed the same spatial renderer.
- Positions use listener-relative world coordinates, mapping grid north (`+y`) to audio forward (`-z`) and world height to audio up. Standard-mode arrows retain compass movement; HRTF-mode arrows move relative to facing without changing it. `Q` and `E` turn facing left or right by 45 degrees in either audio mode, and `F` announces the current facing. Only HRTF uses facing to rotate audio; standard audio remains north-aligned.
- `Shift+4` switches active and future sources immediately and saves the listener's preference in browser storage. Standard mode is the default. Mono centers and downmixes positional sources while preserving the HRTF preference for a return to stereo.
- `Shift+E` opens effect selection in both standard and HRTF modes.
- Sustained piano notes and release tails update their source position and transmission as the listener or item moves. The shared world transmission resolver also serves voice, sampled world sounds, radio, and emit audio.
- Co-located held sounds and interior elevator ambience stay centered. Multi-floor item sounds use the appropriate landing height. Elevator landing cues retain their floor source ownership and door transmission rules; HRTF does not connect otherwise isolated floors or cabins.
- UI cues, local footsteps, and microphone monitoring remain non-positional.

## Message Handling

Core incoming message effects:

- `livekit_token`: connects the authenticated browser to the LiveKit audio room.
- `auth_required`: prompt client to authenticate before gameplay messages.
- `auth_result`: auth success/failure with optional session token + account metadata + `authPolicy`.
- `auth_permissions`: live permission refresh (role + permission set) after role/permission admin changes.
- `admin_roles_list`: role metadata + user counts + permission keys for role management UI.
- `admin_users_list`: registered-user metadata for the read-only Shift+Z list and role/ban administration flows.
- `admin_action_result`: success/error for role/user admin mutations.
- `update_position`: update peer position and authoritative `facingDeg`; movement may route a footstep through the shared world-audio path.
- `teleport_complete`: route the peer landing sound from its final tile and acoustic zone through the shared world-audio path while preserving its `facingDeg`.
- `update_nickname`: update peer display name.
- `chat_message`: append/readable status; optional system sound class.
- `item_upsert`: replace item snapshot and resync item runtimes.
- `item_remove`: remove item and cleanup runtimes.
- `item_action_result`: success/error status for actions.
- `item_use_sound`: route a positional one-shot through the shared world-audio path.
- `item_piano_note`: start/stop synthesized piano notes from remote users (item layer gated).
- `item_piano_status`: structured piano mode/record/playback transitions (client runtime state).
- `item_elevator_status`: track local elevator entry, travel, arrival, and exit state.
- `structure_upsert` / `structure_remove`: apply live wall-run changes used by rendering and collision prediction.
- `structure_action_result`: announce World Builder mutation success/failure.
- `world_sound`: route a server-validated positional structure contact sound for another user's blocked impact or successful crossing.
- `pong`:
  - positive `clientSentAt`: user ping response (`P` command)
  - negative `clientSentAt`: internal heartbeat response

## Stale Connection Recovery

- Voice recovers independently when LiveKit disconnects while the grid session is running. It requests fresh credentials for up to three recovery attempts, with waits of 2, 4, and 8 seconds. It never rejoins with a cached token. After the final request it allows eight seconds for credentials to arrive; a received token cancels that timer while joining. Exhaustion reports failure and asks the user to disconnect and connect to retry.
- Successful voice recovery announces “Voice reconnected.” and resets the attempt limit. Intentional cleanup cancels pending requests and prevents an unfinished join from reviving the voice session. The existing processed microphone track and peer subscription rules are retained during recovery.
- Speaker selection applies to the Web Audio context that outputs voice, item, and effect sounds. The selector is disabled when the browser lacks Web Audio output-device selection.
- An unplugged microphone is announced and capture falls back once to the default input. Replacement preserves user mute and voice permissions; failed fallback reports the unavailable microphone. Late captures after cleanup or a newer device selection are discarded. Device lists refresh on device changes while Settings is open.

- If websocket closes unexpectedly, client starts reconnect flow immediately.
- While running, client also sends heartbeat `ping` every 10 seconds (fallback for silent half-open cases).
- Two consecutive heartbeat intervals without a `pong` start reconnect flow; one missed interval is tolerated. A received pong resets the miss counter.
- Reconnect flow waits 5 seconds before each attempt and retries up to 3 times, then asks the user to press Connect.
- Returning to a visible tab brings a waiting retry forward without exceeding the three-attempt limit.
- Each attempt waits for a complete server welcome: socket opening has a 10-second timeout, followed by an 8-second welcome timeout. Saved-session authentication uses the same welcome deadline.
- Disconnect, logout, and a new manual connection cancel pending retries and connection attempts. Closed, failed, and replaced sockets cannot affect a newer attempt; welcome timers are cleared when their attempt finishes.
- Receiving welcome completes reconnection before microphone setup, so a microphone permission prompt does not consume another retry.
- If reconnect lands on a different `welcome.serverInfo.instanceId`, client announces server restart.
- Connect/reconnect status message is emitted from `welcome` and includes server version.
- Server-only deploys no longer force browser reloads unless `expectedClientRevision` changes.

## Carried Item Presentation

- `H` announces all items held by the local player or says they are holding nothing.
- `L` and `Shift+L` append carried item titles after the user’s location, only when carrying something. These descriptions use the current server-synchronized `carrierId` on items.
- Pickup enforces `items.max_carried_items` from server config, default `2`. Every held item follows walking, teleport, and elevator travel. Disconnect drops all of them at the last tile (or the elevator’s last completed landing); reconnect does not restore carrying.
- Item action menus combine held items with ground items on the current square. `D` selects a pickup or drop action by item; use, secondary use, edit, inspect, and management likewise select one target when there are several.

- Selecting “Hand to user” requests eligible recipients from the server: online, on the same floor within five grid squares (Chebyshev distance), with pickup permission and a free carrying slot. Selecting a name sends the hand request; the server rechecks eligibility before moving the item into that user’s hands, leaving ownership unchanged.
- “Transfer ownership” works for items on your square or in your hands, including offline recipients and confirmation. Only ownership changes; carrier and position stay unchanged, with no recipient range or capacity requirement.

## Authorization Runtime

- Server enforces item/chat/nickname/voice/admin/World Builder permissions for each packet.
- Role and permission changes apply live to connected users without reconnect.
- `voice.send` revocation is pushed immediately via `auth_permissions`; client mutes outbound voice track. Current permissions and the user’s mute setting are reapplied after microphone setup or replacement.
- Successful role creation/deletion triggers a fresh `admin_roles_list` request so menus reflect the server’s current roles.

## Floors And Elevators

- World positions use integer `x`, `y`, and `z`. Ground is `z=0`; the second floor is `z=40`.
- Player facing uses eight headings in degrees: `0` north (`+y`), `45` northeast, `90` east (`+x`), `135` southeast, `180` south, `225` southwest, `270` west, and `315` northwest. Turns in either audio mode change facing; movement, blocked movement, teleports, and elevator travel preserve it.
- Normal movement and teleport packets must keep the server-owned `z`. Only the elevator changes floors.
- The client renders only the current floor. Item lists and interactions are current-floor only; the user list remains global and names each floor.
- Cross-floor user teleport is blocked in the client, and the server rejects any packet that attempts to change `z` directly.
- Presence snapshots and position updates include a server-authoritative `acousticZoneId`. A user belongs to a floor zone while outside and to an elevator-cabin zone after boarding, including during travel.
- Floors remain acoustically isolated. LiveKit subscriptions use coarse zone connectivity, while local gain continuously mixes voice across a connected elevator doorway.
- Each elevator is one independent single-square shaft object at the same coordinate on both landings. Calls, doors, queueing, travel, rider movement, and carried-item movement are server-owned.
- Opening and closing are explicit server-owned, non-traversable states whose durations match their sound assets. After opening finishes, the fully open dwell uses the elevator's editable `doorOpenSeconds`; after closing finishes, floor travel uses its editable `travelSeconds`. Both default to five seconds. During travel, riders are broadcast at progressively changing intermediate heights and belong to neither floor.
- The elevator object's own `z` remains canonical while rider and car travel heights change separately.
- Arrival starts the next-direction cue and opening sound together while riders remain acoustically inside. When opening finishes, the server marks the door open, moves riders onto the destination floor, and sends the arrival announcement. A rider who remains after closing must reopen the door, wait for opening to finish, and use again to exit.
- The client-owned elevator audio runtime loops the built-in interior ambience from a randomized file offset. Passengers hear it at full cabin level. Nearby landing users hear it fade in during opening and fade out during closing.
- Continuous floor item emitters, radios, and voices crossing between a cabin and its current landing use that same door-transmission ramp. Continuous sources remain alive through the ramp instead of restarting at each door state.
- On startup, any incomplete persisted elevator timer is cleared and the car becomes closed and idle at its last completed landing.
- A process restart also drops items whose connection-scoped carrier no longer exists; a carried item caught at an intermediate travel height returns to the ground floor.

## Disconnect/Cleanup

On disconnect:

- Close signaling.
- Stop heartbeat monitor.
- Stop local media tracks.
- Cleanup peers and all audio runtimes.
- Reset UI/mode state and lists.

## Runtime Components

- `Delivery`: routes typed packets to one client or the authenticated roster through a transport. Production uses the websocket transport; server tests use a recording transport so delivery and fanout assertions do not patch server internals.
- `PeerManager`: LiveKit room lifecycle and remote track attach.
- `RadioStationRuntime`: shared stream sources + per-item output/effects/spatialization.
- `ItemEmitRuntime`: per-item looping emit source + spatialization.
- `AudioEngine`: shared audio context, sample playback, effects, and voice graph.
- `WorldAudioRouter`: shared policy and playback entry point for sampled positional world events.
- `AcousticZoneRuntime`: shared zone connectivity and opening/closing transmission gain.
