# Item Schema

## World Item (server-authoritative)

```json
{
  "id": "string",
  "type": "radio_station | dice | wheel | clock | widget | piano | elevator | teleporter",
  "title": "string",
  "x": 0,
  "y": 0,
  "z": 0,
  "createdBy": "user-id",
  "createdByName": "username",
  "updatedBy": "user-id",
  "updatedByName": "username",
  "createdAt": 1735689600000,
  "updatedAt": 1735689600000,
  "version": 1,
  "capabilities": ["editable", "carryable", "deletable", "usable"],
  "useSound": "sounds/roll.ogg",
  "emitSound": "sounds/clock.ogg",
  "params": {},
  "carrierId": null,
  "occupiedOffsets": [{"x": 0, "y": 0}]
}
```

- `useSound`: optional client-played one-shot sound when item `use` succeeds; global item field and not user-editable in V1.
- Top-level `emitSound` is the type default. Every non-radio type supports an editable, persisted `params.emitSound` override; empty text, `none`, and `off` normalize to an empty string and disable playback.
- `capabilities`, `useSound`, and `emitSound` are derived from global item-type definitions at runtime (not stored per-instance in persisted state).
- `carrierId`: carrying connection ID or `null` when on the grid. Several items may reference one carrier, up to the server’s `items.max_carried_items` limit (default `2`). Carrying is separate from ownership and is cleared on disconnect/restart.
- Handing an item updates `carrierId`, position, update audit fields, and version without changing ownership. The server checks range, floor, online status, carrying capacity, and pickup permissions both when listing recipients and when handing it over. Ownership transfer applies to items on the sender’s square or held by the sender; it leaves position and carrier unchanged and does not depend on recipient carrying capacity or proximity.
- `createdBy` / `updatedBy` are stable user IDs. `item_add` is rejected once the user owns `items.max_items_per_user` items by `createdBy` (default `500`).
- `createdByName` / `updatedByName` are display-name snapshots used for inspect/readout text.
- `useCooldownMs`: global per item type (`radio_station=1000`, `dice=1000`, `wheel=4000`, `clock=1000`, `widget=1000`, `piano=1000`), not per-instance editable.
- `emitRange`: global spatial range default per item type (`radio_station=10`, `dice=15`, `wheel=15`, `clock=10`, `widget=15`, `piano=15`).
  - `radio_station` can override this per instance via `params.emitRange` (`5..20`).
- `directional`: global directional attenuation flag per item type (`radio_station=true`, others `false`); every non-radio type can override per instance via `params.directional`.
- `z`: authoritative floor height. Ordinary items use `0` or `40`.
- `occupiedOffsets`: server-owned horizontal footprint relative to the item anchor. Elevators and existing items use one cell; the footprint model remains available for future larger objects.

## Persisted Item State (`server/runtime/items.json`)

```json
{
  "id": "string",
  "type": "radio_station | dice | wheel | clock | widget | piano | elevator | teleporter",
  "title": "string",
  "x": 0,
  "y": 0,
  "z": 0,
  "createdBy": "user-id",
  "createdByName": "username",
  "updatedBy": "user-id",
  "updatedByName": "username",
  "createdAt": 1735689600000,
  "updatedAt": 1735689600000,
  "version": 1,
  "params": {},
  "carrierId": null
}
```

- Persisted state stores only instance data.
- Global/type-level properties are loaded from server registry in `server/app/item_catalog.py`.
- Footprints are derived from the item type when state loads and are not duplicated in persisted state.
- Per-type use/update validation and message behavior are implemented in per-item modules under `server/app/items/types/*/definition.py`, `validator.py`, and `actions.py`, discovered via plugins in `server/app/items/types/*/plugin.py`.
- Client-side add/edit metadata is consumed from `welcome.uiDefinitions` via `client/src/items/itemRegistry.ts` (no local fallback definitions).
- End-to-end add-item template: `docs/item-type-template.md`.

## Type Params

### Shared emitter params (all types except `radio_station`)

The server composes these defaults and controls into eligible plugins: `emitSound` (type default: clock ticking, teleporter whirr, or empty), `emitVolume=100`, `emitRange` (type default), `emitSoundSpeed=50`, `emitSoundTempo=50`, `emitInitialDelay=0`, `emitLoopDelay=0`, `emitEffect="off"`, `emitEffectValue=50`, `directional` (type default), and `facing=0`. Authored type defaults take precedence. Loading saved items fills missing parameters from the current type defaults; explicit saved values, including empty sounds and zero volume, take precedence. Changing volume does not clear the sound.

Standard validation uses range `1..20`, volume `0..100`, speed/tempo `0..100` (50 normal), delays `0..300` seconds, effect amount `0..100`, and effects `reverb | echo | flanger | high_pass | low_pass | off`. Existing type restrictions still apply, such as piano range `5..20`. Sound references accept filenames under `sounds/` or full URLs.

Metadata hides dependent controls with `visibleWhen: {"emitSound": "!"}`. Facing also requires `directional=true`; effect amount also requires `emitEffect != off`. Disabling a sound preserves its stored settings. Piano range remains visible without an emitted sound because it also controls notes. Radio keeps only its existing stream controls.

### `teleporter`

```json
{
  "destination": "0, 0, 0",
  "emitSound": "/sounds/whirr.ogg",
  "emitRange": 5
}
```

- `destination`: text containing exactly three integers, normalized to `x, y, z`; maximum 100 characters. The server checks horizontal grid bounds and configured floor elevation before use.
- The normal `title` field provides its editable name. Its own `x`, `y`, and `z` locate the device; they are separate from its destination.
- Capabilities: `editable`, `deletable`, `usable`. Cooldown: 1000 ms. No persistent per-item runtime state or custom client behavior is required.

### `radio_station`

```json
{
  "streamUrl": "",
  "enabled": true,
  "mediaChannel": "stereo",
  "mediaVolume": 50,
  "mediaEffect": "off",
  "mediaEffectValue": 50,
  "stationName": "",
  "nowPlaying": "",
  "facing": 0,
  "emitRange": 10
}
```

- `streamUrl`: string, empty allowed until configured.
- `enabled`: boolean on/off flag.
  - UI behavior: in property menu, `Enter` toggles on/off directly.
- `mediaVolume`: integer, range `0-100`, default `50`.
- `mediaChannel`: one of `stereo | mono | left | right`, default `stereo`.
- `mediaEffect`: one of `reverb | echo | flanger | high_pass | low_pass | off`, default `off`.
- `mediaEffectValue`: number, range `0-100`, precision `0.1`.
- UI visibility: `mediaEffectValue` is shown only when `mediaEffect != off` (`visibleWhen: {"mediaEffect": "!off"}`).
- `stationName`: server-managed station label derived from ICY metadata when available.
- `nowPlaying`: server-managed stream title derived from ICY metadata when available.
- `facing`: number, range `0-360`, step `1` (used when `directional=true`).
- UI visibility: `facing` is shown only when `directional=true` (`visibleWhen` metadata).
- `emitRange`: integer, range `5-20`, default `10`.

### `dice`

```json
{
  "sides": 6,
  "number": 2
}
```

- `sides`: integer, range `1-100`.
- `number`: integer, range `1-100`.

### `wheel`

```json
{
  "spaces": "yes, no"
}
```

- `spaces`: comma-delimited string of values.
- Server validation:
  - must include at least 1 value
  - max 100 values
  - each value max 80 chars

### `clock`

```json
{
  "timeZone": "America/Detroit",
  "use24Hour": false,
  "topOfHourAnnounce": true,
  "alarmEnabled": false,
  "alarmTime": "12:00 AM"
}
```

- `timeZone`: one of the representative IANA zones in `CLOCK_TIME_ZONE_OPTIONS`.
  The concise menu covers every modern standard UTC offset globally and at least one
  city or region for each standard offset used within Africa, Asia, Europe, North
  America, South America, and Australia/Oceania. Antarctica is represented by its
  research-station zones. `Etc/GMT+12` covers UTC−12 because that offset has no
  inhabited representative city. Daylight-saving changes are supplied by each IANA
  zone rather than listed as separate offsets.
- `use24Hour`: boolean (or `on/off` in updates), default `false`.
- `topOfHourAnnounce`: boolean (or `on/off` in updates), default `true`.
- `alarmEnabled`: boolean (or `on/off` in updates), default `false`.
- `alarmTime`: default `12:00 AM`; accepts `HH:MM` (24-hour mode) or `H:MM AM/PM` (12-hour mode).
- UI visibility: `alarmTime` is shown only when `alarmEnabled=true` (`visibleWhen` metadata).
- Global defaults: `useSound=none`, `emitSound=sounds/clock.ogg`.
- Clock speech announcement audio is emitted via `item_clock_announce` packets using `/sounds/clock/el640/*.ogg`.

### `widget`

```json
{
  "enabled": true,
  "directional": false,
  "facing": 0,
  "emitRange": 15,
  "emitVolume": 100,
  "emitSoundSpeed": 50,
  "emitSoundTempo": 50,
  "emitInitialDelay": 0,
  "emitLoopDelay": 0,
  "emitEffect": "off",
  "emitEffectValue": 50,
  "useSound": "",
  "emitSound": ""
}
```

- `enabled`: boolean (or `on/off` in updates), default `true`.
- `directional`: boolean (or `on/off` in updates), default `false`.
- `facing`: number, range `0-360`, step `1`.
- UI visibility: `facing` is shown only when `directional=true` (`visibleWhen` metadata).
- `emitRange`: integer, range `1-20`, default `15`.
- `emitVolume`: integer, range `0-100`, default `100`.
- `emitSoundSpeed`: integer, range `0-100`, default `50`; controls emitted sound speed/pitch (`0=0.5x`, `50=1.0x`, `100=2.0x`).
- `emitSoundTempo`: integer, range `0-100`, default `50`; controls emitted sound tempo (`0=0.5x`, `50=1.0x`, `100=2.0x`).
- `emitInitialDelay`: number, range `0-300`, precision `0.1`, default `0`; delay in seconds before emitted audio starts after enable.
- `emitLoopDelay`: number, range `0-300`, precision `0.1`, default `0`; delay in seconds between each emitted playback.
- `emitEffect`: one of `reverb | echo | flanger | high_pass | low_pass | off`, default `off`.
- `emitEffectValue`: number, range `0-100`, precision `0.1`, default `50`.
- `useSound`: empty, filename (assumed under `sounds/`), or full URL.
- `emitSound`: empty, filename (assumed under `sounds/`), or full URL.

### `piano`

```json
{
  "instrument": "piano",
  "voiceMode": "poly",
  "octave": 0,
  "attack": 15,
  "decay": 45,
  "release": 35,
  "brightness": 55,
  "emitRange": 15
}
```

- `instrument`: one of
  `piano | electric_piano | guitar | organ | bass | violin | synth_lead | brass | nintendo | drum_kit`.
- `voiceMode`: one of `poly | mono`.
- `octave`: integer, range `-2..2` (default `0`; bass defaults to `-1`).
- Selecting a new instrument resets `voiceMode`/`octave`/`attack`/`decay`/`release`/`brightness` to that instrument's defaults.
- `attack`: integer, range `0-100`, default `15`.
- `decay`: integer, range `0-100`, default `45`.
- `release`: integer, range `0-100`, default `35`.
- `brightness`: integer, range `0-100`, default `55`.
- `emitRange`: integer, range `5-20`, default `15`.
- `songId`: server-managed song reference used for piano demo/playback content.
- Piano sound uses the live item coordinates and acoustic zone throughout notes and release tails. HRTF is a listener preference, not an item parameter.
- Recorded/demo song payload is stored in server song registry (`runtime/piano_songs.json`) using compact format:
  - `meta`: shared synth parameters
  - `keys`: keyId dictionary
  - `states`: parameter-state dictionary (for mid-song instrument/param changes)
  - `events`: `[t, keyIndex, midi, on, stateIndex]`

### `elevator`

```json
{
  "floorZs": [0, 40],
  "currentZ": 0,
  "targetZ": null,
  "queuedZ": null,
  "departOnCloseZ": null,
  "state": "idle",
  "doorOpen": false,
  "doorOpenSeconds": 5,
  "travelSeconds": 5,
  "directional": false,
  "facing": 0,
  "emitRange": 15,
  "emitVolume": 100,
  "emitSoundSpeed": 50,
  "emitSoundTempo": 50,
  "emitInitialDelay": 0,
  "emitLoopDelay": 0,
  "emitEffect": "off",
  "emitEffectValue": 50,
  "emitSound": ""
}
```

- The server owns the floor, movement, queue, and door fields. The display title and standard emitter properties, including direction and facing, are editable.
- Each elevator is an independent object; more than one may be placed.
- The shaft occupies its single anchor square on both configured floors.
- Runtime `state` is one of `idle`, `opening`, `door_open`, `closing`, `moving`, or `arriving`; `doorOpen` becomes true only after the opening sound has finished.
- The full standard emitter control set is editable. It emits from the shaft object's anchor on both configured floors and does not follow the car.
- `doorOpenSeconds` and `travelSeconds` are editable numbers from `0..300`, with defaults of 5. The open dwell begins after opening audio finishes; floor travel begins after closing audio finishes.
- Door transitions play `/sounds/elevator_open.ogg` and `/sounds/elevator_close.ogg` spatially. Entry and exit are rejected during either transition.
- `/sounds/elevator_up.ogg` or `/sounds/elevator_down.ogg` begins with the opening clip to announce the next travel direction without adding another delay.
- `/sounds/elevator_inside.ogg` loops from a randomized offset for passengers and is spatially audible to nearby users only through a fully open door.
- Elevator runtime timers are not resumed after a server restart. A loaded elevator returns to a closed, idle state at its last completed floor.

## Packet Shapes

- `item_upsert`:

```json
{
  "type": "item_upsert",
  "item": { "..." : "World Item" }
}
```

- `item_remove`:

```json
{
  "type": "item_remove",
  "itemId": "item-id"
}
```

- `item_action_result`:

```json
{
  "type": "item_action_result",
  "ok": true,
  "action": "add | pickup | drop | delete | transfer | hand | use | secondary_use | update",
  "message": "human-readable status",
  "itemId": "optional-item-id"
}
```

- `item_use_sound`:

```json
{
  "type": "item_use_sound",
  "itemId": "item-id",
  "sound": "sounds/roll.ogg",
  "x": 12,
  "y": 8,
  "z": 0,
  "acousticZoneId": "floor:0"
}
```

- `item_clock_announce`:

```json
{
  "type": "item_clock_announce",
  "itemId": "item-id",
  "sounds": ["/sounds/clock/el640/its.ogg", "/sounds/clock/el640/2.ogg", "/sounds/clock/el640/PM.ogg"],
  "x": 12,
  "y": 8,
  "z": 0,
  "acousticZoneId": "floor:0"
}
```

- `item_piano_note`:

```json
{
  "type": "item_piano_note",
  "itemId": "item-id",
  "senderId": "user-id",
  "keyId": "KeyA",
  "midi": 60,
  "on": true,
  "instrument": "piano",
  "voiceMode": "poly",
  "octave": 0,
  "attack": 15,
  "decay": 45,
  "release": 35,
  "brightness": 55,
  "x": 12,
  "y": 8,
  "z": 0,
  "emitRange": 15
}
```

- `item_elevator_status` is sent only to a rider and reports `entered`, `moving`, `arrived`, or `exited`, with the current or intermediate `z`. It may include a user-facing `message`; arrival uses it to name the destination floor and open door.
