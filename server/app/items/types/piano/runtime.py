"""Authoritative piano recording, playback, and note runtime."""

from __future__ import annotations

import asyncio
import time
from typing import Literal, Protocol, TypedDict

from ....client import ClientConnection, RateLimitName
from ....delivery import Delivery
from ....item_service import ItemService
from ....models import (
    ItemPianoNoteBroadcastPacket,
    ItemPianoNotePacket,
    ItemPianoRecordingPacket,
    ItemPianoStatusPacket,
    WorldItem,
)

MAX_ACTIVE_PIANO_KEYS_PER_CLIENT = 12
PIANO_RECORDING_MAX_MS = 30_000
PIANO_RECORDING_MAX_EVENTS = 4096


class PianoRecordingEvent(TypedDict):
    """One normalized note event captured during a piano recording."""

    t: int
    keyId: str
    midi: int
    on: bool
    instrument: str
    voiceMode: str
    attack: int
    decay: int
    release: int
    brightness: int
    emitRange: int


class PianoRecordingSession(TypedDict, total=False):
    """Mutable state for one active piano recording session."""

    ownerClientId: str
    elapsedMs: int
    paused: bool
    lastResumeMonotonic: float
    events: list[PianoRecordingEvent]
    autoStopTask: asyncio.Task[None]


class PianoRuntimeHost(Protocol):
    """Server operations required by the piano runtime."""

    delivery: Delivery

    @property
    def item_service(self) -> ItemService: ...

    @property
    def items(self) -> dict[str, WorldItem]: ...

    def get_client_by_id(self, client_id: str) -> ClientConnection | None: ...

    def client_has_permission(self, client: ClientConnection, key: str) -> bool: ...

    def consume_rate_limit(
        self, client: ClientConnection, name: RateLimitName
    ) -> bool: ...

    def item_is_on_client_square(
        self, item: WorldItem, client: ClientConnection
    ) -> bool: ...

    def request_state_save(self) -> None: ...

    async def broadcast_item(self, item: WorldItem) -> None: ...

    async def send_result(
        self,
        client: ClientConnection,
        ok: bool,
        action: Literal["use"],
        message: str,
        item_id: str | None = None,
    ) -> None: ...


class PianoRuntime:
    """Own piano note limits, recording sessions, and playback tasks."""

    def __init__(self, host: PianoRuntimeHost) -> None:
        """Create an empty piano runtime bound to server delivery operations."""

        self.host = host
        self.delivery = host.delivery
        self.active_keys_by_client: dict[str, set[str]] = {}
        self.recording_state_by_item: dict[str, PianoRecordingSession] = {}
        self.playback_tasks_by_item: dict[str, asyncio.Task[None]] = {}

    @property
    def items(self) -> dict[str, WorldItem]:
        """Return the current authoritative item mapping."""

        return self.host.items

    @property
    def item_service(self) -> ItemService:
        """Return item persistence used for stored recordings."""

        return self.host.item_service

    async def handle_note(
        self, client: ClientConnection, packet: ItemPianoNotePacket
    ) -> None:
        """Validate, optionally record, and broadcast one live piano note."""
        if not self.host.client_has_permission(client, "item.use"):
            return
        piano_item = self.items.get(packet.itemId)
        if not piano_item or piano_item.type != "piano":
            return
        if piano_item.carrierId not in (None, client.id):
            return
        if piano_item.carrierId is None and (
            not self.host.item_is_on_client_square(piano_item, client)
        ):
            return
        active_keys = self.active_keys_by_client.setdefault(client.id, set())
        if packet.on:
            if (
                packet.keyId not in active_keys
                and len(active_keys) >= MAX_ACTIVE_PIANO_KEYS_PER_CLIENT
            ):
                return
            if not self.host.consume_rate_limit(client, "piano"):
                return
            active_keys.add(packet.keyId)
        elif packet.keyId in active_keys:
            # Releasing a held key is never rate-limited, so listeners cannot be
            # left with a stuck note.
            active_keys.discard(packet.keyId)
        else:
            # Listeners never heard this key start, so there is nothing to release.
            return
        recording_state = self.recording_state_by_item.get(piano_item.id)
        if (
            recording_state
            and recording_state.get("ownerClientId") == client.id
            and recording_state.get("paused") is not True
        ):
            elapsed_ms = max(
                0,
                min(
                    PIANO_RECORDING_MAX_MS,
                    self._recording_elapsed_ms(recording_state),
                ),
            )
            events = recording_state.get("events")
            if isinstance(events, list) and len(events) < PIANO_RECORDING_MAX_EVENTS:
                instrument = (
                    str(piano_item.params.get("instrument", "piano")).strip().lower()
                )
                voice_mode = (
                    str(piano_item.params.get("voiceMode", "poly")).strip().lower()
                )
                if voice_mode not in {"poly", "mono"}:
                    voice_mode = "poly"
                attack = (
                    int(piano_item.params.get("attack", 15))
                    if isinstance(piano_item.params.get("attack", 15), (int, float))
                    else 15
                )
                decay = (
                    int(piano_item.params.get("decay", 45))
                    if isinstance(piano_item.params.get("decay", 45), (int, float))
                    else 45
                )
                release = (
                    int(piano_item.params.get("release", 35))
                    if isinstance(piano_item.params.get("release", 35), (int, float))
                    else 35
                )
                brightness = (
                    int(piano_item.params.get("brightness", 55))
                    if isinstance(piano_item.params.get("brightness", 55), (int, float))
                    else 55
                )
                emit_range = (
                    int(piano_item.params.get("emitRange", 15))
                    if isinstance(piano_item.params.get("emitRange", 15), (int, float))
                    else 15
                )
                events.append(
                    {
                        "t": elapsed_ms,
                        "keyId": packet.keyId[:32],
                        "midi": packet.midi,
                        "on": packet.on,
                        "instrument": instrument,
                        "voiceMode": voice_mode,
                        "attack": max(0, min(100, attack)),
                        "decay": max(0, min(100, decay)),
                        "release": max(0, min(100, release)),
                        "brightness": max(0, min(100, brightness)),
                        "emitRange": max(5, min(20, emit_range)),
                    }
                )
            if elapsed_ms >= PIANO_RECORDING_MAX_MS:
                await self._finalize_piano_recording(piano_item.id, notify_owner=True)
        await self._broadcast_item_piano_note(
            piano_item,
            sender_id=client.id,
            key_id=packet.keyId,
            midi=packet.midi,
            on=packet.on,
            exclude=client,
        )
        return

    async def handle_recording_action(
        self, client: ClientConnection, packet: ItemPianoRecordingPacket
    ) -> None:
        """Apply one recording or playback control action."""
        if not self.host.client_has_permission(client, "item.use"):
            await self.host.send_result(
                client, False, "use", "Not authorized to use items."
            )
            return
        recording_item = self.items.get(packet.itemId)
        if not recording_item or recording_item.type != "piano":
            await self.host.send_result(client, False, "use", "Piano not found.")
            return
        if recording_item.carrierId not in (None, client.id):
            await self.host.send_result(
                client, False, "use", "Piano is not available.", recording_item.id
            )
            return
        if recording_item.carrierId is None and (
            not self.host.item_is_on_client_square(recording_item, client)
        ):
            await self.host.send_result(
                client,
                False,
                "use",
                "Piano is not on your square.",
                recording_item.id,
            )
            return

        if packet.action == "toggle_record":
            existing = self.recording_state_by_item.get(recording_item.id)
            if existing and existing.get("ownerClientId") != client.id:
                await self.host.send_result(
                    client,
                    False,
                    "use",
                    "This piano is already recording.",
                    recording_item.id,
                )
                return
            if existing and existing.get("ownerClientId") == client.id:
                if existing.get("paused") is True:
                    existing["paused"] = False
                    existing["lastResumeMonotonic"] = time.monotonic()
                    await self.send_status(
                        client,
                        item_id=recording_item.id,
                        event="record_resumed",
                        recording_state="recording",
                    )
                    await self.host.send_result(
                        client, True, "use", "Recording resumed.", recording_item.id
                    )
                else:
                    existing["elapsedMs"] = self._recording_elapsed_ms(existing)
                    existing["paused"] = True
                    existing.pop("lastResumeMonotonic", None)
                    await self.send_status(
                        client,
                        item_id=recording_item.id,
                        event="record_paused",
                        recording_state="paused",
                    )
                    await self.host.send_result(
                        client, True, "use", "Recording paused.", recording_item.id
                    )
                return
            self._cancel_piano_playback(recording_item.id)
            new_recording_state: PianoRecordingSession = {
                "ownerClientId": client.id,
                "elapsedMs": 0,
                "paused": False,
                "lastResumeMonotonic": time.monotonic(),
                "events": [],
            }
            self.recording_state_by_item[recording_item.id] = new_recording_state
            auto_stop_task = asyncio.create_task(
                self._auto_stop_piano_recording(recording_item.id)
            )
            new_recording_state["autoStopTask"] = auto_stop_task
            await self.send_status(
                client,
                item_id=recording_item.id,
                event="record_started",
                recording_state="recording",
            )
            await self.host.send_result(
                client, True, "use", "Recording started.", recording_item.id
            )
            return

        if packet.action == "stop_record":
            existing = self.recording_state_by_item.get(recording_item.id)
            if existing and existing.get("ownerClientId") != client.id:
                await self.host.send_result(
                    client,
                    False,
                    "use",
                    "This piano is already recording.",
                    recording_item.id,
                )
                return
            if existing and existing.get("ownerClientId") == client.id:
                await self._finalize_piano_recording(
                    recording_item.id, notify_owner=True
                )
                return
            await self.send_status(
                client,
                item_id=recording_item.id,
                event="record_stopped",
                recording_state="idle",
            )
            await self.host.send_result(
                client, True, "use", "Recording stopped.", recording_item.id
            )
            return

        if packet.action == "playback":
            if recording_item.id in self.recording_state_by_item:
                await self.host.send_result(
                    client,
                    False,
                    "use",
                    "Stop recording before playback.",
                    recording_item.id,
                )
                return
            song_id = str(recording_item.params.get("songId", "")).strip()
            has_song = (
                isinstance(self.item_service.piano_songs.get(song_id), dict)
                if song_id
                else False
            )
            if not has_song:
                await self.host.send_result(
                    client,
                    False,
                    "use",
                    "No recording saved on this piano.",
                    recording_item.id,
                )
                return
            self._cancel_piano_playback(recording_item.id)
            playback_task = asyncio.create_task(
                self._start_piano_playback(recording_item)
            )
            self.playback_tasks_by_item[recording_item.id] = playback_task
            await self.send_status(
                client,
                item_id=recording_item.id,
                event="playback_started",
                recording_state="playback",
            )
            await self.host.send_result(
                client, True, "use", "Playback started.", recording_item.id
            )
            return

        if packet.action == "stop_playback":
            self._cancel_piano_playback(recording_item.id)
            await self.send_status(
                client,
                item_id=recording_item.id,
                event="playback_stopped",
                recording_state="idle",
            )
            await self.host.send_result(
                client, True, "use", "Playback stopped.", recording_item.id
            )
            return
        return

    def _get_piano_source_position(self, item: WorldItem) -> tuple[int, int, int]:
        """Resolve world position used for piano note spatial broadcasts."""

        if item.carrierId:
            carrier = self.host.get_client_by_id(item.carrierId)
            if carrier is not None:
                return carrier.x, carrier.y, carrier.z
        return item.x, item.y, item.z

    async def _broadcast_item_piano_note(
        self,
        item: WorldItem,
        *,
        sender_id: str,
        key_id: str,
        midi: int,
        on: bool,
        instrument_override: str | None = None,
        voice_mode_override: str | None = None,
        attack_override: int | None = None,
        decay_override: int | None = None,
        release_override: int | None = None,
        brightness_override: int | None = None,
        emit_range_override: int | None = None,
        exclude: ClientConnection | None = None,
    ) -> None:
        """Broadcast one piano note event using current item synth settings."""

        instrument = (
            (
                instrument_override
                if isinstance(instrument_override, str)
                else str(item.params.get("instrument", "piano"))
            )
            .strip()
            .lower()
        )
        voice_mode = (
            (
                voice_mode_override
                if isinstance(voice_mode_override, str)
                else str(item.params.get("voiceMode", "poly"))
            )
            .strip()
            .lower()
        )
        if voice_mode not in {"poly", "mono"}:
            voice_mode = "poly"
        octave = (
            int(item.params.get("octave", 0))
            if isinstance(item.params.get("octave", 0), (int, float))
            else 0
        )
        attack = (
            int(attack_override)
            if isinstance(attack_override, int)
            else int(item.params.get("attack", 15))
            if isinstance(item.params.get("attack", 15), (int, float))
            else 15
        )
        decay = (
            int(decay_override)
            if isinstance(decay_override, int)
            else int(item.params.get("decay", 45))
            if isinstance(item.params.get("decay", 45), (int, float))
            else 45
        )
        release = (
            int(release_override)
            if isinstance(release_override, int)
            else int(item.params.get("release", 35))
            if isinstance(item.params.get("release", 35), (int, float))
            else 35
        )
        brightness = (
            int(brightness_override)
            if isinstance(brightness_override, int)
            else int(item.params.get("brightness", 55))
            if isinstance(item.params.get("brightness", 55), (int, float))
            else 55
        )
        emit_range = (
            int(emit_range_override)
            if isinstance(emit_range_override, int)
            else int(item.params.get("emitRange", 15))
            if isinstance(item.params.get("emitRange", 15), (int, float))
            else 15
        )
        source_x, source_y, source_z = self._get_piano_source_position(item)
        await self.delivery.broadcast(
            ItemPianoNoteBroadcastPacket(
                type="item_piano_note",
                itemId=item.id,
                senderId=sender_id,
                keyId=key_id,
                midi=max(0, min(127, int(midi))),
                on=on,
                instrument=instrument,
                voiceMode=voice_mode,
                octave=max(-2, min(2, octave)),
                attack=max(0, min(100, attack)),
                decay=max(0, min(100, decay)),
                release=max(0, min(100, release)),
                brightness=max(0, min(100, brightness)),
                x=source_x,
                y=source_y,
                z=source_z,
                emitRange=max(5, min(20, emit_range)),
            ),
            exclude=exclude,
        )

    def _cancel_piano_playback(self, item_id: str) -> None:
        """Cancel active playback task for one piano item, if any."""

        task = self.playback_tasks_by_item.pop(item_id, None)
        if task is not None and not task.done():
            task.cancel()

    @staticmethod
    def _recording_elapsed_ms(
        session: PianoRecordingSession, now_monotonic: float | None = None
    ) -> int:
        """Compute effective recorded duration, including currently active segment."""

        elapsed_ms = (
            int(session.get("elapsedMs", 0))
            if isinstance(session.get("elapsedMs"), (int, float))
            else 0
        )
        paused = session.get("paused") is True
        if paused:
            return max(0, elapsed_ms)
        last_resume = session.get("lastResumeMonotonic")
        if isinstance(last_resume, (int, float)):
            now_value = (
                now_monotonic
                if isinstance(now_monotonic, (int, float))
                else time.monotonic()
            )
            elapsed_ms += max(0, int((now_value - float(last_resume)) * 1000))
        return max(0, elapsed_ms)

    async def _finalize_piano_recording(
        self, item_id: str, *, notify_owner: bool = False
    ) -> None:
        """Persist and broadcast one active recording session, then clear runtime state."""

        session = self.recording_state_by_item.pop(item_id, None)
        if not session:
            return
        auto_stop_task = session.get("autoStopTask")
        if isinstance(auto_stop_task, asyncio.Task) and not auto_stop_task.done():
            auto_stop_task.cancel()
        item = self.items.get(item_id)
        if not item or item.type != "piano":
            return
        elapsed_ms = max(
            0, min(PIANO_RECORDING_MAX_MS, self._recording_elapsed_ms(session))
        )
        events = list(session.get("events", []))
        song_id = f"item:{item.id}:recording"
        keys: list[str] = []
        key_to_index: dict[str, int] = {}
        states: list[list[object]] = []
        state_to_index: dict[tuple[object, ...], int] = {}
        compact_events: list[list[int]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            t = (
                int(event.get("t", 0))
                if isinstance(event.get("t"), (int, float))
                else 0
            )
            key_id = str(event.get("keyId", "")).strip()
            midi = (
                int(event.get("midi", 0))
                if isinstance(event.get("midi"), (int, float))
                else 0
            )
            on = 1 if event.get("on") is True else 0
            instrument = (
                str(event.get("instrument", "piano")).strip().lower() or "piano"
            )
            voice_mode = str(event.get("voiceMode", "poly")).strip().lower()
            if voice_mode not in {"mono", "poly"}:
                voice_mode = "poly"
            attack = (
                int(event.get("attack", 15))
                if isinstance(event.get("attack"), (int, float))
                else 15
            )
            decay = (
                int(event.get("decay", 45))
                if isinstance(event.get("decay"), (int, float))
                else 45
            )
            release = (
                int(event.get("release", 35))
                if isinstance(event.get("release"), (int, float))
                else 35
            )
            brightness = (
                int(event.get("brightness", 55))
                if isinstance(event.get("brightness"), (int, float))
                else 55
            )
            emit_range = (
                int(event.get("emitRange", 15))
                if isinstance(event.get("emitRange"), (int, float))
                else 15
            )
            state_key = (
                instrument,
                voice_mode,
                max(0, min(100, attack)),
                max(0, min(100, decay)),
                max(0, min(100, release)),
                max(0, min(100, brightness)),
                max(5, min(20, emit_range)),
            )
            if not key_id:
                continue
            index = key_to_index.get(key_id)
            if index is None:
                index = len(keys)
                keys.append(key_id)
                key_to_index[key_id] = index
            state_index = state_to_index.get(state_key)
            if state_index is None:
                state_index = len(states)
                states.append(list(state_key))
                state_to_index[state_key] = state_index
            compact_events.append(
                [
                    max(0, min(PIANO_RECORDING_MAX_MS, t)),
                    index,
                    max(0, min(127, midi)),
                    on,
                    state_index,
                ]
            )
        compact_events.sort(key=lambda row: row[0])
        first_state = states[0] if states else ["piano", "poly", 15, 45, 35, 55, 15]
        self.item_service.piano_songs[song_id] = {
            "meta": {
                "instrument": first_state[0],
                "voiceMode": first_state[1],
                "attack": first_state[2],
                "decay": first_state[3],
                "release": first_state[4],
                "brightness": first_state[5],
                "emitRange": first_state[6],
                "recordingLengthMs": elapsed_ms,
            },
            "keys": keys,
            "states": states,
            "events": compact_events,
        }
        self.item_service.save_piano_songs()
        owner_id = str(session.get("ownerClientId", ""))
        owner = self.host.get_client_by_id(owner_id) if owner_id else None
        item.params["songId"] = song_id
        item.params.pop("recording", None)
        item.params.pop("recordingLengthMs", None)
        item.updatedAt = self.item_service.now_ms()
        item.updatedBy = owner.user_id if owner and owner.user_id else "system"
        item.updatedByName = owner.username if owner and owner.username else "system"
        item.version += 1
        self.host.request_state_save()
        await self.host.broadcast_item(item)
        if owner and notify_owner:
            await self.send_status(
                owner,
                item_id=item.id,
                event="record_stopped",
                recording_state="idle",
            )
            await self.host.send_result(
                owner, True, "use", "Recording stopped.", item.id
            )

    async def _auto_stop_piano_recording(self, item_id: str) -> None:
        """Stop a recording automatically at the max recording duration."""

        try:
            while True:
                session = self.recording_state_by_item.get(item_id)
                if session is None:
                    return
                if self._recording_elapsed_ms(session) >= PIANO_RECORDING_MAX_MS:
                    await self._finalize_piano_recording(item_id, notify_owner=True)
                    return
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            return

    async def _start_piano_playback(self, item: WorldItem) -> None:
        """Run one piano recording playback task and broadcast note events."""

        sender_id = f"item:{item.id}:playback"
        events: list[PianoRecordingEvent] = []
        song_id = str(item.params.get("songId", "")).strip()
        song_payload = self.item_service.piano_songs.get(song_id) if song_id else None
        if isinstance(song_payload, dict):
            keys = song_payload.get("keys")
            states = song_payload.get("states")
            compact_events = song_payload.get("events")
            meta = song_payload.get("meta")
            if isinstance(keys, list) and isinstance(compact_events, list):
                base_state = None
                if isinstance(meta, dict):
                    instrument = (
                        str(meta.get("instrument", "")).strip().lower() or "piano"
                    )
                    raw_voice_mode = str(meta.get("voiceMode", "")).strip().lower()
                    voice_mode = (
                        raw_voice_mode if raw_voice_mode in {"mono", "poly"} else "poly"
                    )
                    attack = (
                        int(meta.get("attack", 15))
                        if isinstance(meta.get("attack"), (int, float))
                        else 15
                    )
                    decay = (
                        int(meta.get("decay", 45))
                        if isinstance(meta.get("decay"), (int, float))
                        else 45
                    )
                    release = (
                        int(meta.get("release", 35))
                        if isinstance(meta.get("release"), (int, float))
                        else 35
                    )
                    brightness = (
                        int(meta.get("brightness", 55))
                        if isinstance(meta.get("brightness"), (int, float))
                        else 55
                    )
                    emit_range = (
                        int(meta.get("emitRange", 15))
                        if isinstance(meta.get("emitRange"), (int, float))
                        else 15
                    )
                    base_state = (
                        instrument,
                        voice_mode,
                        max(0, min(100, attack)),
                        max(0, min(100, decay)),
                        max(0, min(100, release)),
                        max(0, min(100, brightness)),
                        max(5, min(20, emit_range)),
                    )
                for row in compact_events:
                    if not isinstance(row, list) or len(row) < 4:
                        continue
                    raw_time, raw_key_idx, raw_midi, raw_on = row[:4]
                    if (
                        not isinstance(raw_time, (int, float))
                        or not isinstance(raw_key_idx, (int, float))
                        or not isinstance(raw_midi, (int, float))
                    ):
                        continue
                    key_idx = int(raw_key_idx)
                    if key_idx < 0 or key_idx >= len(keys):
                        continue
                    raw_key = keys[key_idx]
                    if not isinstance(raw_key, str) or not raw_key.strip():
                        continue
                    state = base_state
                    if (
                        len(row) >= 5
                        and isinstance(states, list)
                        and isinstance(row[4], (int, float))
                    ):
                        state_idx = int(row[4])
                        if 0 <= state_idx < len(states):
                            state_row = states[state_idx]
                            if isinstance(state_row, list) and len(state_row) >= 7:
                                candidate_instrument = (
                                    str(state_row[0]).strip().lower() or "piano"
                                )
                                candidate_voice_mode = str(state_row[1]).strip().lower()
                                state = (
                                    candidate_instrument,
                                    candidate_voice_mode
                                    if candidate_voice_mode in {"mono", "poly"}
                                    else "poly",
                                    max(
                                        0,
                                        min(
                                            100,
                                            int(state_row[2])
                                            if isinstance(state_row[2], (int, float))
                                            else 15,
                                        ),
                                    ),
                                    max(
                                        0,
                                        min(
                                            100,
                                            int(state_row[3])
                                            if isinstance(state_row[3], (int, float))
                                            else 45,
                                        ),
                                    ),
                                    max(
                                        0,
                                        min(
                                            100,
                                            int(state_row[4])
                                            if isinstance(state_row[4], (int, float))
                                            else 35,
                                        ),
                                    ),
                                    max(
                                        0,
                                        min(
                                            100,
                                            int(state_row[5])
                                            if isinstance(state_row[5], (int, float))
                                            else 55,
                                        ),
                                    ),
                                    max(
                                        5,
                                        min(
                                            20,
                                            int(state_row[6])
                                            if isinstance(state_row[6], (int, float))
                                            else 15,
                                        ),
                                    ),
                                )
                    if state is None:
                        continue
                    events.append(
                        {
                            "t": max(0, min(PIANO_RECORDING_MAX_MS, int(raw_time))),
                            "keyId": raw_key[:32],
                            "midi": max(0, min(127, int(raw_midi))),
                            "on": bool(raw_on),
                            "instrument": state[0],
                            "voiceMode": state[1],
                            "attack": state[2],
                            "decay": state[3],
                            "release": state[4],
                            "brightness": state[5],
                            "emitRange": state[6],
                        }
                    )
        events.sort(key=lambda entry: int(entry["t"]))
        if not events:
            return

        active_keys: dict[str, int] = {}
        previous_at_ms = 0
        try:
            for event in events:
                current_at_ms = int(event["t"])
                delay_ms = max(0, current_at_ms - previous_at_ms)
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000)
                current_item = self.items.get(item.id)
                if not current_item or current_item.type != "piano":
                    break
                key_id = str(event["keyId"])
                midi = int(event["midi"])
                on = bool(event["on"])
                if on:
                    active_keys[key_id] = midi
                else:
                    active_keys.pop(key_id, None)
                await self._broadcast_item_piano_note(
                    current_item,
                    sender_id=sender_id,
                    key_id=key_id,
                    midi=midi,
                    on=on,
                    instrument_override=event.get("instrument")
                    if isinstance(event.get("instrument"), str)
                    else None,
                    voice_mode_override=event.get("voiceMode")
                    if isinstance(event.get("voiceMode"), str)
                    else None,
                    attack_override=event.get("attack")
                    if isinstance(event.get("attack"), int)
                    else None,
                    decay_override=event.get("decay")
                    if isinstance(event.get("decay"), int)
                    else None,
                    release_override=event.get("release")
                    if isinstance(event.get("release"), int)
                    else None,
                    brightness_override=event.get("brightness")
                    if isinstance(event.get("brightness"), int)
                    else None,
                    emit_range_override=event.get("emitRange")
                    if isinstance(event.get("emitRange"), int)
                    else None,
                )
                previous_at_ms = current_at_ms
        except asyncio.CancelledError:
            pass
        finally:
            current_item = self.items.get(item.id)
            if current_item and current_item.type == "piano":
                for key_id, midi in list(active_keys.items()):
                    await self._broadcast_item_piano_note(
                        current_item,
                        sender_id=sender_id,
                        key_id=key_id,
                        midi=midi,
                        on=False,
                    )
            current_task = self.playback_tasks_by_item.get(item.id)
            if current_task is asyncio.current_task():
                self.playback_tasks_by_item.pop(item.id, None)

    async def send_status(
        self,
        client: ClientConnection,
        *,
        item_id: str,
        event: Literal[
            "use_mode_entered",
            "record_started",
            "record_paused",
            "record_resumed",
            "record_stopped",
            "playback_started",
            "playback_stopped",
        ],
        recording_state: Literal["idle", "recording", "paused", "playback"]
        | None = None,
    ) -> None:
        """Send structured piano state transitions without relying on status-message text."""

        await self.delivery.send(
            client,
            ItemPianoStatusPacket(
                type="item_piano_status",
                itemId=item_id,
                event=event,
                recordingState=recording_state,
            ),
        )

    def remove_item(self, item: WorldItem) -> None:
        """Clear runtime and persisted recording state for a deleted piano."""

        self._cancel_piano_playback(item.id)
        recording_state = self.recording_state_by_item.pop(item.id, None)
        if recording_state is not None:
            auto_stop_task = recording_state.get("autoStopTask")
            if isinstance(auto_stop_task, asyncio.Task) and not auto_stop_task.done():
                auto_stop_task.cancel()
        song_id = str(item.params.get("songId", "")).strip()
        if song_id and song_id in self.item_service.piano_songs:
            self.item_service.piano_songs.pop(song_id, None)
            self.item_service.save_piano_songs()

    async def client_disconnected(self, client: ClientConnection) -> None:
        """Release active keys and finalize recordings owned by a client."""

        self.active_keys_by_client.pop(client.id, None)
        for item_id, session in list(self.recording_state_by_item.items()):
            if session.get("ownerClientId") == client.id:
                await self._finalize_piano_recording(item_id)

    async def shutdown(self) -> None:
        """Cancel and await all active piano background tasks."""

        tasks = list(self.playback_tasks_by_item.values())
        tasks.extend(
            task
            for session in self.recording_state_by_item.values()
            if isinstance((task := session.get("autoStopTask")), asyncio.Task)
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.playback_tasks_by_item.clear()
        self.recording_state_by_item.clear()
        self.active_keys_by_client.clear()
