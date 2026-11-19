# Item Types

This is behavior-focused documentation for item types and their defaults.

## Shared Item Behavior

- Items are server-authoritative.
- A user may carry up to `items.max_carried_items` items (default `2`, positive integer in server config). Pickup permissions still apply; reaching the limit rejects further pickups without changing held items.
- A user may own up to `items.max_items_per_user` items (default `500`). Placing another item is rejected until they delete one, and transfers to a user already at the limit are rejected. Transfers must name an existing account.
- Adding and deleting items share a per-connection rate limit (default 10 per 10 seconds); edits have their own limit (default 60 per second). Both are under `[rate_limits]` in server config.
- All held items follow movement, teleport, and elevator travel, and drop at the final tile on disconnect. Ownership is unchanged.
- Use, secondary use, edit, inspect, and management offer held items alongside items on the current square.
- “Hand to user” moves a held item without changing ownership. Recipients must be online, on the same floor within five squares (including diagonals), have a free carrying slot, and have pickup permission for that item.
- “Transfer ownership” changes ownership of an item on your square or in your hands without moving it or changing its carrier. Recipients may be offline, distant, or carrying their maximum number of items.
- Global per-type fields are injected by the server and are not persisted per-instance:
  - `capabilities`
  - `useSound`
  - `emitSound`
  - `useCooldownMs` (from item catalog)
  - `emitRange` (spatial range in squares)
  - `directional` (directional attenuation enabled)
- Instance fields are persisted in `server/runtime/items.json`.
- Read-only inspect fields include `createdBy` and `updatedBy` for ownership/change tracking.
- Every item has a server-owned `z` height and footprint. Item interaction compares the full `x`, `y`, `z` position.

## Shared emitted sound controls

Every item type except `radio_station` supports an editable emitted sound through the shared property editor. The clock retains its ticking sound by default, and teleporters emit `/sounds/whirr.ogg`; other types start with no emitted sound. Empty text, `none`, or `off` disables the emitter.

Emitted sound appears first in the audio settings, followed by its volume, range, speed/pitch, tempo, delays, effect, and direction settings.

Volume, range, speed/pitch, tempo, initial delay, loop delay, effect, and directional controls appear only when an emitted sound is set. Facing additionally requires directional audio; effect amount requires an effect other than `off`. Existing type-specific ranges remain in place, including the piano's `5..20` range shared with its notes, which remains visible even without an emitted sound. Emitted audio uses the Items layer and follows the item's location, including while carried.

Radio retains its stream, volume, effects, range, and facing controls; it has no separate emitted sound or emitted-sound effects.

## `teleporter`

- Default name: `Teleporter`; destination: `0, 0, 0`.
- Default emitted sound: `/sounds/whirr.ogg`, with an emit range of `5` squares.
- Editable, deletable, usable, and stationary. Edit its name and **Destination** using the shared item property editor.
- Destination is one text field containing exactly three integers: `x, y, z`. Spaces are allowed. `z` is the floor elevation (currently `0` or `40`), not a floor index.
- Use from its square to teleport to any in-bounds square on a configured floor. Walls along the route do not affect teleportation.
- The transition lasts two seconds: current location audio fades out for one second, the server switches position, then destination audio fades in for one second. The existing teleport start and arrival sounds play.
- Facing and held items follow the user. Ordinary item use permissions apply. Movement and further item use are blocked during the transition; elevator riders must leave the elevator before using it.
- Destination syntax is checked when editing; grid bounds and supported floor are checked again before use. Editing or deleting the device after activation does not change the captured destination.

## `radio_station`

### Defaults
- Title: `radio`
- Params:
  - `streamUrl=""`
  - `enabled=true`
  - `mediaChannel="stereo"`
  - `mediaVolume=50`
  - `mediaEffect="off"`
  - `mediaEffectValue=50`
  - `stationName=""` (server-managed, read-only)
  - `nowPlaying=""` (server-managed, read-only)
  - `facing=0`
  - `emitRange=10`
- Global:
  - `useSound=none`
  - `emitSound=none`
  - `useCooldownMs=1000`
  - `emitRange=10`
  - `directional=true`

### Use
- `use` toggles `enabled` on/off and broadcasts chat status.
- `secondary use` reports now-playing metadata (`Playing <song> from <station>`), or `<title> is off` when disabled.

### Validation
- `mediaChannel`: `stereo | mono | left | right`
- `mediaVolume`: integer `0..100`
- `mediaEffect`: `reverb | echo | flanger | high_pass | low_pass | off`
- `mediaEffectValue`: number `0..100` with `0.1` precision
  - Visible only when `mediaEffect != off` (`visibleWhen: {"mediaEffect": "!off"}`)
- `facing`: number `0..360` with step `1`
- `emitRange`: integer `5..20`
- `stationName` / `nowPlaying`: server-fetched metadata fields; not editable by clients.

## `dice`

### Defaults
- Title: `Dice`
- Params:
  - `sides=6`
  - `number=2`
- Global:
  - `useSound=sounds/roll.ogg`
  - `emitSound=none`
  - `useCooldownMs=1000`
  - `emitRange=15`
  - `directional=false`

### Use
- Rolls `number` dice with `sides` sides and reports values + total.

### Validation
- `sides`: integer `1..100`
- `number`: integer `1..100`

## `wheel`

### Defaults
- Title: `wheel`
- Params:
  - `spaces="yes, no"`
- Global:
  - `useSound=sounds/spin.ogg`
  - `emitSound=none`
  - `useCooldownMs=4000`
  - `emitRange=15`
  - `directional=false`

### Use
- Announces spin immediately.
- Result is sent after delay.

### Validation
- `spaces`: comma-delimited values
- At least 1 entry
- Max 100 entries
- Max 80 chars per entry

## `clock`

### Defaults
- Title: `clock`
- Params:
  - `timeZone="America/Detroit"`
  - `use24Hour=false`
  - `topOfHourAnnounce=true`
  - `alarmEnabled=false`
  - `alarmTime="12:00 AM"`
- Global:
  - `useSound=none`
  - `emitSound=sounds/clock.ogg`
  - `useCooldownMs=1000`
  - `emitRange=10`
  - `directional=false`

### Use
- Broadcasts a spoken EL640-style time announcement as spatial audio from the clock position.
- No text chat line is emitted for clock `use`.

### Validation
- `timeZone`: one of `CLOCK_TIME_ZONE_OPTIONS` in `server/app/item_catalog.py`; the concise list covers every modern standard UTC offset globally plus regional coverage per continent. Antarctica uses station zones, and `Etc/GMT+12` represents the uninhabited UTC−12 offset.
- `use24Hour`: boolean or on/off style input
- `topOfHourAnnounce`: boolean or on/off style input
- `alarmEnabled`: boolean or on/off style input
- `alarmTime`: `HH:MM` when `use24Hour=true`, otherwise `H:MM AM/PM`
  - Visible only when `alarmEnabled=true` (`visibleWhen: {"alarmEnabled": true}`)

### Audio
- Spoken clock assets live under `client/public/sounds/clock/el640/`.
- Top-of-hour routine (when enabled) uses `hour1.ogg` + time phrase + `hour2.ogg`.
- Alarm routine (when enabled and time matches) uses `announcement.ogg` + time phrase + `alarm.ogg`.

## `widget`

### Defaults
- Title: `widget`
- Params:
  - `enabled=true`
  - `directional=false`
  - `facing=0`
  - `emitRange=15`
  - `emitVolume=100`
  - `emitSoundSpeed=50`
  - `emitSoundTempo=50`
  - `emitInitialDelay=0`
  - `emitLoopDelay=0`
  - `emitEffect="off"`
  - `emitEffectValue=50`
  - `useSound=""`
  - `emitSound=""`
- Global:
  - `useSound=none`
  - `emitSound=none`
  - `useCooldownMs=1000`
  - `emitRange=15`
  - `directional=false`
  - `emitSoundSpeed=50`
  - `emitSoundTempo=50`
  - `emitInitialDelay=0`
  - `emitLoopDelay=0`

### Use
- `use` toggles `enabled` on/off and plays `useSound` when configured.

### Validation
- `enabled`: boolean or on/off style input
- `directional`: boolean or on/off style input
- `facing`: number `0..360` with step `1`
- `emitRange`: integer `1..20`
- `emitVolume`: integer `0..100`
- `emitSoundSpeed`: integer `0..100` (`0=0.5x`, `50=1.0x`, `100=2.0x`) for speed/pitch
- `emitSoundTempo`: integer `0..100` (`0=0.5x`, `50=1.0x`, `100=2.0x`) for tempo
- `emitInitialDelay`: number `0..300` with `0.1` step/precision; delay in seconds before emitted audio starts after enable
- `emitLoopDelay`: number `0..300` with `0.1` step/precision; delay in seconds between each emitted loop playback
- `emitEffect`: `reverb | echo | flanger | high_pass | low_pass | off`
- `emitEffectValue`: number `0..100` with `0.1` precision
- `useSound`: empty, filename (assumed under `sounds/`), or full URL
- `emitSound`: empty, filename (assumed under `sounds/`), or full URL

## `piano`

### Defaults
- Title: `piano`
- Params:
  - `instrument="piano"`
  - `voiceMode="poly"`
  - `octave=0`
  - `attack=15`
  - `decay=45`
  - `release=35`
  - `brightness=55`
  - `emitRange=15`
- Global:
  - `useSound=none`
  - `emitSound=none`
  - `useCooldownMs=1000`
  - `emitRange=15`
  - `directional=false`

### Use
- Announces that the user begins playing the piano (client enters piano key mode).
- Piano mode controls include `,` to start/stop recording (max 30s) and `.` to play saved recording.
- Recordings are stored on the item (server-authoritative), so nearby users hear playback.
- Notes and release tails follow the live item position, listener facing, and shared wall/door transmission. Standard and HRTF modes use the same routing; held items on the listener square stay centered.

### Validation
- `instrument`: `piano | electric_piano | guitar | organ | bass | violin | synth_lead | brass | nintendo | drum_kit`
- `voiceMode`: `poly | mono`
- `octave`: integer `-2..2`
- `attack`: integer `0..100`
- `decay`: integer `0..100`
- `release`: integer `0..100`
- `brightness`: integer `0..100`
- `emitRange`: integer `5..20`
- Instrument changes reset `voiceMode`/`octave`/`attack`/`decay`/`release`/`brightness` to instrument defaults.

## `elevator`

### Defaults
- Title: `Elevator`
- Floors: ground at `z=0`, second at `z=40`
- Footprint: one square, present at the same coordinate on both landings
- Fully open dwell: editable `doorOpenSeconds`, default 5 seconds
- Door transition sounds: `/sounds/elevator_open.ogg` (about 2.56 seconds) and `/sounds/elevator_close.ogg` (about 3.77 seconds)
- Floor travel time: editable `travelSeconds`, default 5 seconds
- Built-in interior ambience: `/sounds/elevator_inside.ogg`
- Emitter defaults: nondirectional at facing `0`, range `15`, volume `100`, normal speed/tempo, no delays, effect off, and empty sound
- Door-open direction cue: `/sounds/elevator_up.ogg` for the next upward trip or `/sounds/elevator_down.ogg` for the next downward trip

### Use
- Use once to call the car or open its door when it is already present.
- Opening and closing are non-traversable phases. Use again only after the opening sound finishes to enter. With two floors, entering selects the other floor.
- The fully open door closes after the configured `doorOpenSeconds` from the latest completed opening or entry. This dwell starts after the opening sound. The car starts its configured `travelSeconds` trip only after the closing sound finishes.
- On arrival, the next-direction beep and opening sound begin together. Once opening finishes, riders join the destination floor, receive a floor-and-door announcement, and the interior ambience becomes audible outside nearby.
- Use once after arrival to exit through the fully open door. If the configured dwell expires first, one use starts reopening it; use again after opening finishes to exit.
- Secondary use reports the current landing and door state, or the destination and travel direction while moving.
- Away-floor calls made while the car is moving or a door is transitioning are queued.

### Rules
- Each placed elevator has its own car and state machine; multiple elevators are allowed.
- Elevators cannot be carried. They cannot be deleted while moving or occupied.
- Riders cannot move or teleport while inside.
- During travel, rider `z` advances progressively between the two floors. Every intermediate height is hidden from both floors and blocks all floor audio.
- The built-in interior ambience loops from a randomized offset. Passengers hear it throughout their time inside; nearby users hear it spatially only while the door is fully open on their landing.
- A configured emitted sound stays on the elevator shaft object and emits from its anchor on both floors. A rider can hear it normally while the door is open; it is suppressed only while that rider is inside with the door closed.

### Validation
- `doorOpenSeconds`, `travelSeconds`: number `0..300`, rounded to 0.1 seconds
- `directional`: boolean; `facing`: degrees `0..360`
- `emitRange`: integer `1..20`
- `emitVolume`: integer `0..100`
- `emitSoundSpeed`, `emitSoundTempo`, `emitEffectValue`: number `0..100`
- `emitInitialDelay`, `emitLoopDelay`: seconds `0..300`
- `emitEffect`: `reverb | echo | flanger | high_pass | low_pass | off`
- `emitSound`: empty, filename (assumed under `sounds/`), or full URL

## Adding A New Item Type (Plugin Discovery)

Server is the source of truth for item type definitions and metadata. The client consumes server `welcome.uiDefinitions` and only provides UX/runtime behavior.

For a full copy/paste example with plain-English explanation, see `docs/item-type-template.md`.

1. Server item package: add a new folder under `server/app/items/types/<item_type>/` with:
   - `definition.py` (defaults/capabilities/metadata/options)
   - `validator.py` (`validate_update`)
   - `actions.py` (`use_item`)
2. Server plugin: add `server/app/items/types/<item_type>/plugin.py` exporting `ITEM_TYPE_PLUGIN` with:
   - `type`
   - `order`
   - `module`
   The server auto-discovers plugins at boot, so no central registry edit is needed.
3. Server/client protocol/state models are now string-based for item type ids; for generic types no enum/union list updates are required.
5. Client runtime behavior: add `client/src/items/types/<item_type>/behavior.ts` only if custom client runtime is needed (for example piano mode).
6. Tests: add or update server tests under `server/tests/` for use/update validation, unknown-key stripping, and `uiDefinitions` completeness.

### Example Shape

A minimal new item type usually needs:

- Catalog defaults:
  - `default_title`
  - `default_params`
  - `use_sound` / `emit_sound`
  - `use_cooldown_ms`
- Handler behavior:
  - validate params on update
  - build self/others use messages
  - optionally return delayed result text
