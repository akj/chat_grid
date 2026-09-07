from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
import uuid

import pytest
from websockets.asyncio.server import ServerConnection

from app.client import ClientConnection
from app.auth_service import AuthError
from app.models import (
    WelcomePacket,
    ItemUpsertPacket,
    BroadcastChatMessagePacket,
    BroadcastNicknamePacket,
    BroadcastPositionPacket,
    BroadcastTeleportCompletePacket,
    AdminActionResultPacket,
    AdminUsersListResultPacket,
    AuthResultPacket,
    ItemActionResultPacket,
    ItemHandTargetsResultPacket,
    ItemTransferTargetsResultPacket,
    LiveKitTokenPacket,
    PongPacket,
    WorldSoundPacket,
)
from app.server import (
    AUTH_LOGIN_FAILURE_MESSAGE,
    AUTH_RESUME_FAILURE_MESSAGE,
    SignalingServer,
)

from .conftest import World


def test_client_ip_prefers_forwarded_for_from_loopback_proxy(world: World) -> None:
    server = world.server
    ws = cast(
        ServerConnection,
        SimpleNamespace(
            remote_address=("127.0.0.1", 12345),
            request=SimpleNamespace(
                headers={"X-Forwarded-For": "203.0.113.10, 198.51.100.25"}
            ),
        ),
    )
    client = world.connect("tester", websocket=ws, client_id="u1")
    assert server._client_ip(client) == "198.51.100.25"


def test_last_seen_persistence_is_debounced(
    make_world, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    world = make_world(auth_db_path=tmp_path / "auth.db")
    server = world.server
    client = world.join("tester", user_id="1", client_id="u1")
    timestamps = iter((100_000, 110_000, 131_000))
    persisted: list[tuple[str, int]] = []
    monkeypatch.setattr(server.item_service, "now_ms", lambda: next(timestamps))
    monkeypatch.setattr(
        server.auth_service,
        "touch_last_seen",
        lambda user_id, seen_at_ms: persisted.append((user_id, seen_at_ms)),
    )

    server._persist_client_last_seen(client)
    server._persist_client_last_seen(client)
    server._persist_client_last_seen(client)

    assert persisted == [("1", 100_000), ("1", 131_000)]
    assert client.last_seen_at_ms == 131_000


def test_client_ip_ignores_forwarded_for_from_non_loopback_peer(world: World) -> None:
    server = world.server
    ws = cast(
        ServerConnection,
        SimpleNamespace(
            remote_address=("203.0.113.20", 12345),
            request=SimpleNamespace(headers={"X-Forwarded-For": "198.51.100.25"}),
        ),
    )
    client = world.connect("tester", websocket=ws, client_id="u1")
    assert server._client_ip(client) == "203.0.113.20"


def test_resolve_client_version_metadata_reads_release_and_revision() -> None:
    version_text = """
window.CHGRID_RELEASE_VERSION = "0.1.1";
window.CHGRID_CLIENT_REVISION = "R350";
""".strip()
    resolved = SignalingServer._client_version_metadata_from_web_version_text(
        version_text
    )

    assert resolved == ("0.1.1", "R350")


@pytest.mark.asyncio
async def test_update_position_rejects_out_of_bounds(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=6, client_id="u1")

    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 200, "y": -5, "z": 0})
    )

    assert client.x == 5
    assert client.y == 6
    assert transport.packets_to(observer) == []


@pytest.mark.asyncio
async def test_update_position_cannot_change_floor(
    make_world,
) -> None:
    """Horizontal movement packets must preserve the server-owned floor."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join("tester", x=5, y=6, z=0, client_id="u1")

    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 6, "y": 6, "z": 40})
    )

    assert (client.x, client.y, client.z) == (5, 6, 0)
    correction = transport.last_packet_of_type(client, BroadcastPositionPacket)
    assert (correction.x, correction.y, correction.z) == (5, 6, 0)


@pytest.mark.asyncio
async def test_successful_movement_preserves_facing(make_world) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5, client_id="u1", facing_deg=225)

    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 6, "y": 6, "z": 0})
    )

    assert client.facing_deg == 225
    position = transport.last_packet_of_type(observer, BroadcastPositionPacket)
    assert position.facingDeg == 225


@pytest.mark.asyncio
async def test_blocked_movement_does_not_update_facing(make_world) -> None:
    world = make_world(
        structure_presets={
            "solid": {
                "title": "Wall",
                "movementBlocked": True,
                "soundTransmission": 0.0,
                "height": 40,
                "contactSound": "/sounds/wall.ogg",
            }
        }
    )
    server, transport = world.server, world.transport
    client = world.join("tester", x=5, y=5, client_id="u1", facing_deg=270)
    server.structure_service.add_wall(client, preset_id="solid", direction="east")

    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 6, "y": 5, "z": 0})
    )

    assert (client.x, client.y, client.facing_deg) == (5, 5, 270)
    correction = transport.last_packet_of_type(client, BroadcastPositionPacket)
    assert correction.facingDeg == 270


@pytest.mark.asyncio
async def test_update_facing_broadcasts_canonical_position(make_world) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5, client_id="u1")

    await server._handle_message(
        client, json.dumps({"type": "update_facing", "facingDeg": 135})
    )

    assert client.facing_deg == 135
    self_packet = transport.last_packet_of_type(client, BroadcastPositionPacket)
    observer_packet = transport.last_packet_of_type(observer, BroadcastPositionPacket)
    assert self_packet.facingDeg == 135
    assert observer_packet.facingDeg == 135


@pytest.mark.asyncio
async def test_turn_applies_each_relative_request_and_wraps(make_world) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5, client_id="u1")

    for direction in ("right", "right", "left", "left", "left", "right"):
        await server._handle_message(
            client, json.dumps({"type": "turn", "direction": direction})
        )

    assert (client.x, client.y, client.facing_deg) == (5, 5, 0)
    assert [
        packet.facingDeg
        for packet in transport.packets_of_type(observer, BroadcastPositionPacket)
    ] == [45, 90, 45, 0, 315, 0]


@pytest.mark.asyncio
async def test_update_facing_requires_authenticated_ready_client(make_world) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    unauthenticated = world.connect("unauthenticated", client_id="u1")
    pre_ready = world.connect("pre-ready", client_id="u2")
    pre_ready.authenticated = True

    await server._handle_message(
        unauthenticated, json.dumps({"type": "update_facing", "facingDeg": 90})
    )
    await server._handle_message(
        pre_ready, json.dumps({"type": "update_facing", "facingDeg": 180})
    )
    await server._handle_message(
        unauthenticated, json.dumps({"type": "turn", "direction": "right"})
    )
    await server._handle_message(
        pre_ready, json.dumps({"type": "turn", "direction": "right"})
    )

    assert unauthenticated.facing_deg == 0
    assert pre_ready.facing_deg == 0
    auth_result = transport.last_packet_of_type(unauthenticated, AuthResultPacket)
    assert auth_result.ok is False
    assert transport.packets_to(pre_ready) == []


@pytest.mark.asyncio
async def test_update_facing_does_not_sync_carried_items(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5, client_id="u1")
    item = server.item_service.default_item(client, "dice")
    item.carrierId = client.id
    server.item_service.add_item(item)
    sync_calls: list[str] = []

    async def record_sync(called_client: ClientConnection) -> None:
        sync_calls.append(called_client.id)

    monkeypatch.setattr(server.item_runtime, "sync_carried_items", record_sync)
    transport.clear()

    await server._handle_message(
        client, json.dumps({"type": "update_facing", "facingDeg": 90})
    )

    assert sync_calls == []
    assert (item.x, item.y, item.z) == (5, 5, 0)
    assert transport.packets_of_type(observer, ItemUpsertPacket) == []

    transport.clear()
    await server._handle_message(
        client, json.dumps({"type": "turn", "direction": "right"})
    )

    assert sync_calls == []
    assert (item.x, item.y, item.z) == (5, 5, 0)
    assert transport.packets_of_type(observer, ItemUpsertPacket) == []


@pytest.mark.asyncio
async def test_welcome_presence_includes_facing(make_world) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40, facing_deg=315)
    client = world.join("tester", x=5, y=5, client_id="u1", facing_deg=225)

    await server._send_welcome(client)

    welcome = transport.last_packet_of_type(client, WelcomePacket)
    assert welcome.player.facingDeg == 225
    assert welcome.users[0].id == observer.id
    assert welcome.users[0].facingDeg == 315


@pytest.mark.asyncio
async def test_turn_persists_and_reconnect_restores_facing(
    make_world,
    tmp_path: Path,
) -> None:
    world = make_world(auth_db_path=tmp_path / "auth.db")
    server, transport = world.server, world.transport
    username = f"turn_{uuid.uuid4().hex[:8]}"
    server.auth_service.register(username, "password99")
    client = world.connect("tester", client_id="u1")

    await server._handle_message(
        client,
        json.dumps(
            {"type": "auth_login", "username": username, "password": "password99"}
        ),
    )
    await server._handle_message(client, json.dumps({"type": "welcome_ready"}))
    transport.clear()

    await server._handle_message(
        client, json.dumps({"type": "turn", "direction": "right"})
    )

    assert client.facing_deg == 45
    saved = server.auth_service.get_user_by_id(client.user_id or "")
    assert saved is not None
    assert saved.last_facing_deg == 45

    reconnect = world.connect("reconnect", client_id="u2")
    await server._handle_message(
        reconnect,
        json.dumps(
            {"type": "auth_login", "username": username, "password": "password99"}
        ),
    )

    assert reconnect.facing_deg == 45
    welcome = transport.last_packet_of_type(reconnect, WelcomePacket)
    assert welcome.player.facingDeg == 45


@pytest.mark.asyncio
async def test_welcome_includes_livekit_token_when_configured(
    make_world,
) -> None:
    world = make_world(
        livekit_url="wss://livekit.example.test",
        livekit_api_key="key",
        livekit_api_secret="test-livekit-secret-with-at-least-32-bytes",
    )
    server, transport = world.server, world.transport
    client = world.join("tester", permissions={"voice.send"}, client_id="connection-1")

    await server._send_welcome(client)

    token_packet = transport.last_packet_of_type(client, LiveKitTokenPacket)
    assert token_packet.url == "wss://livekit.example.test"
    assert token_packet.token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authenticated", "world_ready", "enabled", "expected_types"),
    [
        (True, True, True, ["livekit_token"]),
        (False, False, True, ["auth_result"]),
        (True, False, True, []),
        (True, True, False, []),
    ],
)
async def test_livekit_token_request(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    authenticated: bool,
    world_ready: bool,
    enabled: bool,
    expected_types: list[str],
) -> None:
    """Fresh voice credentials go only to an authenticated, ready requester."""

    world = make_world(
        auth_db_path=tmp_path / "auth.db",
        livekit_url="wss://livekit.example.test" if enabled else None,
        livekit_api_key="key" if enabled else None,
        livekit_api_secret="test-livekit-secret-with-at-least-32-bytes"
        if enabled
        else None,
    )
    server, transport = world.server, world.transport
    if authenticated:
        client = world.join("tester", client_id="requester", permissions={"voice.send"})
    else:
        client = world.connect("tester", client_id="requester")
    client.world_ready = world_ready
    other = world.join("other")

    await server._handle_message(client, json.dumps({"type": "livekit_token_request"}))

    deliveries = transport.all_deliveries()
    assert [getattr(packet, "type", "") for _, packet in deliveries] == expected_types
    assert all(recipient is client for recipient, _ in deliveries)
    assert transport.packets_to(other) == []
    if authenticated and world_ready and enabled:
        token_packet = transport.last_packet_of_type(client, LiveKitTokenPacket)
        assert token_packet.url == server.livekit_url
        assert token_packet.token
        transport.clear()
        monkeypatch.setattr(server, "_generate_livekit_token", lambda _: "fresh-token")
        await server._handle_message(
            client, json.dumps({"type": "livekit_token_request"})
        )
        assert transport.all_deliveries() == [
            (
                client,
                LiveKitTokenPacket(
                    type="livekit_token", token="fresh-token", url=server.livekit_url
                ),
            )
        ]
    elif not authenticated:
        assert transport.last_packet_of_type(client, AuthResultPacket).ok is False


@pytest.mark.asyncio
async def test_radio_metadata_refresh_updates_station_and_title(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, client_id="u1")

    radio = server.item_service.default_item(client, "radio_station")
    radio.params["streamUrl"] = "http://example.com/stream"
    radio.params["enabled"] = True
    radio.params["emitRange"] = 10
    radio.params["stationName"] = ""
    radio.params["nowPlaying"] = ""
    server.item_service.add_item(radio)

    def fake_fetch(url: str) -> tuple[str, str]:
        assert url == "http://example.com/stream"
        return ("Test Station", "Test Song")

    monkeypatch.setattr(server.item_runtime.radio, "_fetch_stream_metadata", fake_fetch)

    await server.item_runtime.radio.refresh_once()

    assert radio.params["stationName"] == "Test Station"
    assert radio.params["nowPlaying"] == "Test Song"


@pytest.mark.asyncio
async def test_radio_metadata_refresh_skips_when_no_listener_in_range(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=0, y=0, client_id="u1")

    radio = server.item_service.default_item(client, "radio_station")
    radio.x = 30
    radio.y = 30
    radio.params["streamUrl"] = "http://example.com/stream"
    radio.params["enabled"] = True
    radio.params["emitRange"] = 5
    server.item_service.add_item(radio)

    called = False

    def fake_fetch(url: str) -> tuple[str, str]:
        nonlocal called
        called = True
        return ("X", "Y")

    monkeypatch.setattr(server.item_runtime.radio, "_fetch_stream_metadata", fake_fetch)

    await server.item_runtime.radio.refresh_once()

    assert called is False


@pytest.mark.asyncio
async def test_radio_metadata_refresh_continues_after_one_stream_fails(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=10, y=10, client_id="u1")

    failed_radio = server.item_service.default_item(client, "radio_station")
    failed_radio.params["streamUrl"] = "https://failed.example/stream"
    failed_radio.params["enabled"] = True
    failed_radio.params["emitRange"] = 10
    failed_radio.params["stationName"] = "Previous Station"
    failed_radio.params["nowPlaying"] = "Previous Song"
    server.item_service.add_item(failed_radio)

    working_radio = server.item_service.default_item(client, "radio_station")
    working_radio.params["streamUrl"] = "https://working.example/stream"
    working_radio.params["enabled"] = True
    working_radio.params["emitRange"] = 10
    server.item_service.add_item(working_radio)

    def fake_fetch(url: str) -> tuple[str, str]:
        if url == "https://failed.example/stream":
            raise RuntimeError("upstream disconnected")
        return ("Working Station", "Working Song")

    monkeypatch.setattr(server.item_runtime.radio, "_fetch_stream_metadata", fake_fetch)

    await server.item_runtime.radio.refresh_once()

    assert working_radio.params["stationName"] == "Working Station"
    assert working_radio.params["nowPlaying"] == "Working Song"
    assert failed_radio.params["stationName"] == "Previous Station"
    assert failed_radio.params["nowPlaying"] == "Previous Song"


@pytest.mark.asyncio
async def test_item_secondary_use_radio_reports_now_playing(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5, permissions={"item.use"}, client_id="u1")

    radio = server.item_service.default_item(client, "radio_station")
    radio.x = 5
    radio.y = 5
    radio.params["enabled"] = True
    radio.params["stationName"] = "Station X"
    radio.params["nowPlaying"] = "Song Y"
    server.item_service.add_item(radio)

    await server._handle_message(
        client, json.dumps({"type": "item_secondary_use", "itemId": radio.id})
    )

    results = transport.packets_of_type(client, ItemActionResultPacket)
    assert results
    assert results[-1].ok is True
    assert results[-1].action == "secondary_use"
    assert "Playing Song Y from Station X." in results[-1].message
    assert transport.packets_to(observer) == []


@pytest.mark.asyncio
async def test_item_secondary_use_radio_fetches_missing_now_playing(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join("tester", x=5, y=5, permissions={"item.use"}, client_id="u1")

    radio = server.item_service.default_item(client, "radio_station")
    radio.x = 5
    radio.y = 5
    radio.params["streamUrl"] = "https://radio.example/stream"
    radio.params["enabled"] = True
    radio.params["stationName"] = ""
    radio.params["nowPlaying"] = ""
    server.item_service.add_item(radio)

    def fake_fetch(url: str) -> tuple[str, str]:
        assert url == "https://radio.example/stream"
        return ("Station X", "Song Y")

    monkeypatch.setattr(server.item_runtime.radio, "_fetch_stream_metadata", fake_fetch)

    await server._handle_message(
        client, json.dumps({"type": "item_secondary_use", "itemId": radio.id})
    )

    result = transport.last_packet_of_type(client, ItemActionResultPacket)
    assert result.ok is True
    assert result.message == "Playing Song Y from Station X."


@pytest.mark.asyncio
async def test_item_secondary_use_missing_handler_returns_message(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join("tester", x=5, y=5, permissions={"item.use"}, client_id="u1")

    dice = server.item_service.default_item(client, "dice")
    dice.x = 5
    dice.y = 5
    server.item_service.add_item(dice)

    await server._handle_message(
        client, json.dumps({"type": "item_secondary_use", "itemId": dice.id})
    )

    results = transport.packets_of_type(client, ItemActionResultPacket)
    assert results
    assert results[-1].ok is False
    assert results[-1].action == "secondary_use"
    assert "No secondary action" in results[-1].message


def test_clock_alarm_announcement_sequence_shape(make_world) -> None:
    world = make_world(grid_size=41)
    server = world.server
    params = {"timeZone": "America/Detroit", "use24Hour": False}

    alarm_sounds = server.item_runtime.clock._build_clock_announcement_sounds(
        params, top_of_hour=False, alarm=True
    )
    assert alarm_sounds
    assert alarm_sounds[0] == "/sounds/clock/el640/announcement.ogg"
    assert alarm_sounds[-1] == "/sounds/clock/el640/alarm.ogg"

    top_of_hour_sounds = server.item_runtime.clock._build_clock_announcement_sounds(
        params, top_of_hour=True, alarm=False
    )
    assert top_of_hour_sounds
    assert top_of_hour_sounds[0] == "/sounds/clock/el640/hour1.ogg"
    assert top_of_hour_sounds[-1] == "/sounds/clock/el640/hour2.ogg"


@pytest.mark.asyncio
async def test_auth_login_uses_hash_offload(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, transport = world.server, world.transport
    username = f"alpha_{uuid.uuid4().hex[:8]}"
    server.auth_service.register(username, "password99")
    client = world.connect("tester", client_id="u1")

    offload_calls: list[str] = []

    async def fake_run_auth_hash_task(func, /, *args, **kwargs):
        offload_calls.append(getattr(func, "__name__", "unknown"))
        return func(*args, **kwargs)

    monkeypatch.setattr(server, "_run_auth_hash_task", fake_run_auth_hash_task)

    await server._handle_message(
        client,
        json.dumps(
            {"type": "auth_login", "username": username, "password": "password99"}
        ),
    )

    assert "login" in offload_calls
    auth_results = transport.packets_of_type(client, AuthResultPacket)
    assert auth_results
    assert auth_results[-1].ok is True


@pytest.mark.asyncio
async def test_auth_rate_limit_blocks_before_hash(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    client = world.connect("tester", client_id="u1")

    called_login = False

    def fake_login(username: str, password: str):  # pragma: no cover - should never run
        nonlocal called_login
        called_login = True
        raise RuntimeError("unexpected login call")

    monkeypatch.setattr(server, "_sleep_auth_failure_jitter", lambda: asyncio.sleep(0))
    monkeypatch.setattr(server.auth_service, "login", fake_login)
    monkeypatch.setattr(server, "_is_auth_rate_limited", lambda _client, _packet: True)

    await server._handle_message(
        client,
        json.dumps(
            {"type": "auth_login", "username": "alpha", "password": "wrongpass"}
        ),
    )

    assert called_login is False
    assert transport.packets_to(client)
    auth_result = transport.last_packet_of_type(client, AuthResultPacket)
    assert auth_result.ok is False
    assert "too many" in auth_result.message.lower()


@pytest.mark.asyncio
async def test_auth_login_failure_message_is_generic(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    client = world.connect("tester", client_id="u1")

    monkeypatch.setattr(server, "_sleep_auth_failure_jitter", lambda: asyncio.sleep(0))

    def fake_login(_username: str, _password: str):
        raise AuthError("Account is disabled.")

    monkeypatch.setattr(server.auth_service, "login", fake_login)

    await server._handle_message(
        client,
        json.dumps(
            {"type": "auth_login", "username": "alpha", "password": "wrongpass"}
        ),
    )

    auth_results = transport.packets_of_type(client, AuthResultPacket)
    assert auth_results
    assert auth_results[-1].ok is False
    assert auth_results[-1].message == AUTH_LOGIN_FAILURE_MESSAGE


@pytest.mark.asyncio
async def test_auth_login_defers_activation_until_welcome_ready(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    username = f"ready_{uuid.uuid4().hex[:8]}"
    server.auth_service.register(username, "password99")
    client = world.connect("tester", client_id="u1")

    await server._handle_message(
        client,
        json.dumps(
            {"type": "auth_login", "username": username, "password": "password99"}
        ),
    )

    assert client.authenticated is True
    assert client.world_ready is False
    assert client.websocket not in server.clients
    assert transport.packets_of_type(client, WelcomePacket)
    assert not any(
        "has logged in" in getattr(packet, "message", "")
        for packet in transport.packets_to(observer)
    )

    transport.clear()
    await server._handle_message(client, json.dumps({"type": "welcome_ready"}))

    assert client.world_ready is True
    assert server.clients.get(client.websocket) is client
    assert transport.packets_to(client) == []
    assert [getattr(packet, "type", "") for packet in transport.packets_to(observer)][
        -3:
    ] == [
        "update_position",
        "update_nickname",
        "chat_message",
    ]
    presence = transport.last_packet_of_type(observer, BroadcastPositionPacket)
    assert (presence.id, presence.x, presence.y, presence.z) == (
        client.id,
        client.x,
        client.y,
        client.z,
    )
    nickname = transport.last_packet_of_type(observer, BroadcastNicknamePacket)
    assert nickname.id == client.id
    assert nickname.nickname == client.nickname
    assert any(
        "has logged in" in getattr(packet, "message", "")
        for packet in transport.packets_to(observer)
    )


@pytest.mark.asyncio
async def test_ping_works_before_welcome_ready(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    username = f"ping_{uuid.uuid4().hex[:8]}"
    server.auth_service.register(username, "password99")
    client = world.connect("tester", client_id="u1")

    await server._handle_message(
        client,
        json.dumps(
            {"type": "auth_login", "username": username, "password": "password99"}
        ),
    )
    assert client.world_ready is False

    await server._handle_message(
        client, json.dumps({"type": "ping", "clientSentAt": -1})
    )

    pong_packets = transport.packets_of_type(client, PongPacket)
    assert pong_packets
    assert pong_packets[-1].clientSentAt == -1


@pytest.mark.asyncio
async def test_auth_resume_failure_message_is_generic(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    client = world.connect("tester", client_id="u1")

    monkeypatch.setattr(server, "_sleep_auth_failure_jitter", lambda: asyncio.sleep(0))

    def fake_resume(_token: str):
        raise AuthError("Session has expired.")

    monkeypatch.setattr(server.auth_service, "resume", fake_resume)

    await server._handle_message(
        client, json.dumps({"type": "auth_resume", "sessionToken": "expired-token"})
    )

    auth_results = transport.packets_of_type(client, AuthResultPacket)
    assert auth_results
    assert auth_results[-1].ok is False
    assert auth_results[-1].message == AUTH_RESUME_FAILURE_MESSAGE


@pytest.mark.asyncio
async def test_item_drop_rejects_out_of_bounds(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join(
        "tester", x=5, y=6, permissions={"item.pickup_drop.any"}, client_id="u1"
    )
    item = server.item_service.default_item(client, "dice")
    item.carrierId = client.id
    server.item_service.add_item(item)

    await server._handle_message(
        client,
        json.dumps(
            {"type": "item_drop", "itemId": item.id, "x": 999, "y": 999, "z": 0}
        ),
    )

    assert item.carrierId == client.id
    item_result = transport.last_packet_of_type(client, ItemActionResultPacket)
    assert item_result.ok is False
    assert "out of bounds" in item_result.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_item_pickup_honors_configured_limit(make_world, limit: int) -> None:
    """Pickup should honor the configured carrying limit per client."""

    world = make_world(grid_size=41, max_carried_items=limit)
    server, transport = world.server, world.transport
    client = world.join(
        "tester",
        x=5,
        y=6,
        client_id="u1",
        permissions={"item.pickup_drop.any"},
    )
    items = [server.item_service.default_item(client, "dice") for _ in range(limit + 1)]
    for item in items:
        server.item_service.add_item(item)

    for item in items[:limit]:
        await server._handle_message(
            client, json.dumps({"type": "item_pickup", "itemId": item.id})
        )
    await server._handle_message(
        client, json.dumps({"type": "item_pickup", "itemId": items[0].id})
    )
    await server._handle_message(
        client, json.dumps({"type": "item_pickup", "itemId": items[-1].id})
    )

    assert [item.carrierId for item in items] == ["u1"] * limit + [None]
    results = transport.packets_of_type(client, ItemActionResultPacket)
    assert [result.ok for result in results] == [True] * (limit + 1) + [False]
    assert f"at most {limit}" in results[-1].message

    await server._handle_message(
        client,
        json.dumps(
            {"type": "item_drop", "itemId": items[0].id, "x": 5, "y": 6, "z": 0}
        ),
    )
    await server._handle_message(
        client, json.dumps({"type": "item_pickup", "itemId": items[-1].id})
    )
    assert items[0].carrierId is None
    assert [item.carrierId for item in items[1:]] == ["u1"] * limit


@pytest.mark.asyncio
async def test_all_carried_items_follow_movement_and_teleport(
    make_world,
) -> None:
    """Every carried item should track ordinary movement and teleport completion."""

    world = make_world(grid_size=41)
    server = world.server
    client = world.join("tester", x=5, y=6, client_id="u1")
    items = [server.item_service.default_item(client, "dice") for _ in range(2)]
    for item in items:
        item.carrierId = client.id
        server.item_service.add_item(item)

    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 6, "y": 7, "z": 0})
    )
    assert [(item.x, item.y, item.z) for item in items] == [(6, 7, 0), (6, 7, 0)]

    await server._handle_message(
        client,
        json.dumps({"type": "teleport_complete", "x": 10, "y": 11, "z": 0}),
    )
    assert [(item.x, item.y, item.z) for item in items] == [(10, 11, 0), (10, 11, 0)]


@pytest.mark.asyncio
async def test_disconnect_drops_all_carried_items(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnect cleanup should drop every item carried by the client."""

    world = make_world()
    server = world.server
    client = world.connect("tester", client_id="u1", x=8, y=9, z=40)
    items = [server.item_service.default_item(client, "dice") for _ in range(2)]
    for item in items:
        item.carrierId = client.id
        server.item_service.add_item(item)

    broadcasted: list[object] = []

    async def fake_broadcast_item(item: object) -> None:
        broadcasted.append(item)

    monkeypatch.setattr(server.item_runtime, "broadcast_item", fake_broadcast_item)
    monkeypatch.setattr(server.item_runtime, "request_state_save", lambda: None)

    await server.item_runtime.finish_client_disconnect(client)

    assert [item.carrierId for item in items] == [None, None]
    assert [(item.x, item.y, item.z) for item in items] == [(8, 9, 40), (8, 9, 40)]
    assert broadcasted == items


@pytest.mark.asyncio
async def test_item_transfer_updates_item_owner(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    owner = world.join(
        "owner",
        user_id="1",
        username="owner_user",
        permissions={"item.transfer.own"},
        x=5,
        y=6,
        client_id="u1",
    )
    target = world.join(
        "target",
        user_id="2",
        username="target_user",
        permissions=set(),
        x=10,
        y=10,
        client_id="u2",
    )
    item = server.item_service.default_item(owner, "dice")
    item.x = owner.x
    item.y = owner.y
    server.item_service.add_item(item)

    await server._handle_message(
        owner,
        json.dumps(
            {"type": "item_transfer", "itemId": item.id, "targetUserId": target.user_id}
        ),
    )

    assert item.createdBy == target.user_id
    assert item.createdByName == target.username
    upsert = transport.last_packet_of_type(observer, ItemUpsertPacket)
    assert upsert.item.id == item.id
    assert upsert.item.createdBy == target.user_id
    assert transport.packets_of_type(owner, ItemUpsertPacket) == [upsert]
    assert transport.packets_to(owner)
    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is True
    assert result.action == "transfer"
    assert "you transferred" in result.message.lower()
    assert transport.packets_to(observer)
    chat_packet = transport.last_packet_of_type(observer, BroadcastChatMessagePacket)
    assert "owner transferred" in chat_packet.message.lower()
    assert transport.packets_of_type(owner, BroadcastChatMessagePacket) == []


@pytest.mark.asyncio
async def test_item_transfer_allows_self_target_for_transfer_any(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    owner = world.join(
        "owner",
        user_id="1",
        username="owner_user",
        permissions=set(),
        x=5,
        y=6,
        client_id="u1",
    )
    actor = world.join(
        "actor",
        user_id="3",
        username="actor_user",
        permissions={"item.transfer.any"},
        x=5,
        y=6,
        client_id="u3",
    )
    item = server.item_service.default_item(owner, "dice")
    item.x = actor.x
    item.y = actor.y
    server.item_service.add_item(item)

    await server._handle_message(
        actor,
        json.dumps(
            {"type": "item_transfer", "itemId": item.id, "targetUserId": actor.user_id}
        ),
    )

    assert item.createdBy == actor.user_id
    assert item.createdByName == actor.username
    upsert = transport.last_packet_of_type(observer, ItemUpsertPacket)
    assert upsert.item.id == item.id
    assert upsert.item.createdBy == actor.user_id
    assert transport.packets_of_type(actor, ItemUpsertPacket) == [upsert]
    result = transport.last_packet_of_type(actor, ItemActionResultPacket)
    assert result.ok is True
    assert result.action == "transfer"


@pytest.mark.asyncio
async def test_item_transfer_accepts_offline_target_user_id(
    make_world, tmp_path: Path
) -> None:
    world = make_world(auth_db_path=tmp_path / "auth.db", grid_size=41)
    server, transport = world.server, world.transport
    owner_session = server.auth_service.register("owner_test", "password99")
    actor_session = server.auth_service.register("actor_test", "password99")
    offline_session = server.auth_service.register("offline_test", "password99")
    owner = world.join(
        "owner",
        user_id=owner_session.user.id,
        username=owner_session.user.username,
        permissions=set(),
        x=5,
        y=6,
        client_id="u1",
    )
    actor = world.join(
        "actor",
        user_id=actor_session.user.id,
        username=actor_session.user.username,
        permissions={"item.transfer.any"},
        x=5,
        y=6,
        client_id="u3",
    )
    item = server.item_service.default_item(owner, "dice")
    item.x = actor.x
    item.y = actor.y
    server.item_service.add_item(item)

    await server._handle_message(
        actor,
        json.dumps(
            {
                "type": "item_transfer",
                "itemId": item.id,
                "targetUserId": offline_session.user.id,
            }
        ),
    )

    assert item.createdBy == offline_session.user.id
    assert item.createdByName == offline_session.user.username
    result = transport.last_packet_of_type(actor, ItemActionResultPacket)
    assert result.ok is True
    assert result.action == "transfer"


@pytest.mark.asyncio
@pytest.mark.parametrize("carried_by_actor", [False, True], ids=["ground", "held"])
async def test_item_transfer_targets_lists_online_and_offline(
    make_world, tmp_path: Path, carried_by_actor: bool
) -> None:
    world = make_world(auth_db_path=tmp_path / "auth.db", grid_size=41)
    server, transport = world.server, world.transport
    owner_session = server.auth_service.register("owner_menu", "password99")
    actor_session = server.auth_service.register("actor_menu", "password99")
    online_session = server.auth_service.register("online_menu", "password99")
    offline_session = server.auth_service.register("offline_menu", "password99")
    owner = world.join(
        "owner",
        user_id=owner_session.user.id,
        username=owner_session.user.username,
        permissions=set(),
        x=5,
        y=6,
        client_id="u1",
    )
    actor = world.join(
        "actor",
        user_id=actor_session.user.id,
        username=actor_session.user.username,
        permissions={"item.transfer.any"},
        x=5,
        y=6,
        client_id="u3",
    )
    online = world.join(
        "online",
        user_id=online_session.user.id,
        username=online_session.user.username,
        permissions=set(),
        x=10,
        y=10,
        client_id="u4",
    )
    item = server.item_service.default_item(owner, "dice")
    item.x = actor.x
    item.y = actor.y
    if carried_by_actor:
        item.carrierId = actor.id
    server.item_service.add_item(item)
    online_carried_items = [
        server.item_service.default_item(online, "dice") for _ in range(2)
    ]
    for online_item in online_carried_items:
        online_item.carrierId = online.id
        server.item_service.add_item(online_item)

    await server._handle_message(
        actor, json.dumps({"type": "item_transfer_targets", "itemId": item.id})
    )

    assert transport.packets_to(actor)
    result = transport.last_packet_of_type(actor, ItemTransferTargetsResultPacket)
    usernames = {entry.username for entry in result.targets}
    assert owner_session.user.username not in usernames
    assert online_session.user.username in usernames
    assert offline_session.user.username in usernames
    by_username = {entry.username: entry for entry in result.targets}
    assert by_username[online_session.user.username].online is True
    assert by_username[offline_session.user.username].online is False


@pytest.mark.asyncio
async def test_item_delete_sends_others_notification(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    owner = world.join(
        "owner",
        user_id="1",
        username="owner_user",
        permissions={"item.delete.own"},
        x=5,
        y=6,
        client_id="u1",
    )
    watcher = world.join(
        "watcher",
        user_id="2",
        username="watcher_user",
        permissions=set(),
        x=5,
        y=6,
        client_id="u2",
    )
    item = server.item_service.default_item(owner, "dice")
    item.x = owner.x
    item.y = owner.y
    server.item_service.add_item(item)

    await server._handle_message(
        owner, json.dumps({"type": "item_delete", "itemId": item.id})
    )

    result_packets = transport.packets_of_type(owner, ItemActionResultPacket)
    assert result_packets
    assert result_packets[-1].ok is True
    assert "you deleted" in result_packets[-1].message.lower()
    chat_packets = transport.packets_of_type(observer, BroadcastChatMessagePacket)
    assert chat_packets
    assert "owner deleted" in getattr(chat_packets[-1], "message", "").lower()
    assert (
        transport.packets_of_type(watcher, BroadcastChatMessagePacket) == chat_packets
    )
    assert transport.packets_of_type(owner, BroadcastChatMessagePacket) == []


@pytest.mark.asyncio
async def test_item_transfer_rejects_when_not_authorized(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    owner = world.join(
        "owner",
        user_id="1",
        username="owner_user",
        permissions={"item.use"},
        x=5,
        y=6,
        client_id="u1",
    )
    target = world.join(
        "target",
        user_id="2",
        username="target_user",
        permissions=set(),
        x=10,
        y=10,
        client_id="u2",
    )
    item = server.item_service.default_item(owner, "dice")
    item.x = owner.x
    item.y = owner.y
    server.item_service.add_item(item)

    await server._handle_message(
        owner,
        json.dumps(
            {"type": "item_transfer", "itemId": item.id, "targetUserId": target.user_id}
        ),
    )

    assert item.createdBy == owner.user_id
    assert transport.packets_to(owner)
    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is False
    assert result.action == "transfer"
    assert "not authorized" in result.message.lower()


@pytest.mark.asyncio
async def test_hand_item_moves_item_to_nearby_recipient_without_changing_owner(
    make_world,
) -> None:
    """A handoff moves a carried item while ownership stays with its owner."""

    world = make_world(grid_size=41, max_carried_items=2)
    server, transport = world.server, world.transport
    owner = world.join(
        "owner",
        client_id="u1",
        user_id="1",
        username="owner_user",
        permissions={"item.pickup_drop.own"},
        x=5,
        y=6,
        z=0,
    )
    target = world.join(
        "target",
        client_id="u2",
        user_id="2",
        username="target_user",
        permissions={"item.pickup_drop.any"},
        x=8,
        y=9,
        z=0,
    )
    item = server.item_service.default_item(owner, "dice")
    item.carrierId = owner.id
    item.x = owner.x
    item.y = owner.y
    item.z = owner.z
    server.item_service.add_item(item)
    existing_item = server.item_service.default_item(target, "dice")
    existing_item.carrierId = target.id
    server.item_service.add_item(existing_item)
    original_version = item.version

    await server._handle_message(
        owner,
        json.dumps({"type": "item_hand", "itemId": item.id, "targetUserId": "2"}),
    )

    assert item.carrierId == target.id
    assert (item.x, item.y, item.z) == (target.x, target.y, target.z)
    assert item.createdBy == owner.user_id
    assert item.createdByName == owner.username
    assert item.version == original_version + 1
    assert server.item_service.find_carried_items(owner.id) == []
    assert {held.id for held in server.item_service.find_carried_items(target.id)} == {
        item.id,
        existing_item.id,
    }
    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is True
    assert result.action == "hand"


@pytest.mark.asyncio
async def test_hand_targets_require_nearby_same_floor_recipient_permission(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hand targets include only nearby online users who can receive the item."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    actor = world.join(
        "actor",
        client_id="u1",
        user_id="1",
        username="actor_user",
        permissions={"item.pickup_drop.any"},
        x=5,
        y=6,
        z=0,
    )
    owner = world.join(
        "owner",
        client_id="u2",
        user_id="2",
        username="owner_user",
        permissions={"item.pickup_drop.own"},
        x=10,
        y=6,
        z=0,
    )
    world.join(
        "far",
        client_id="u3",
        user_id="3",
        username="far_user",
        permissions={"item.pickup_drop.any"},
        x=11,
        y=6,
        z=0,
    )
    world.join(
        "other floor",
        client_id="u4",
        user_id="4",
        username="other_floor_user",
        permissions={"item.pickup_drop.any"},
        x=10,
        y=6,
        z=40,
    )
    world.join(
        "no permission",
        client_id="u5",
        user_id="5",
        username="no_permission_user",
        permissions=set(),
        x=10,
        y=5,
        z=0,
    )
    item = server.item_service.default_item(owner, "dice")
    item.carrierId = actor.id
    item.x = actor.x
    item.y = actor.y
    item.z = actor.z
    server.item_service.add_item(item)
    monkeypatch.setattr(
        server.auth_service,
        "list_users_for_admin",
        lambda: [
            {"id": "1", "username": "actor_user", "status": "active"},
            {"id": "2", "username": "owner_user", "status": "active"},
            {"id": "3", "username": "far_user", "status": "active"},
            {"id": "4", "username": "other_floor_user", "status": "active"},
            {"id": "5", "username": "no_permission_user", "status": "active"},
        ],
    )
    await server._handle_message(
        actor, json.dumps({"type": "item_hand_targets", "itemId": item.id})
    )

    result = transport.last_packet_of_type(actor, ItemHandTargetsResultPacket)
    assert [entry.userId for entry in result.targets] == [owner.user_id]
    assert result.targets[0].online is True
    owner.x = 11
    original_state = (item.carrierId, item.x, item.y, item.z, item.version)

    await server._handle_message(
        actor,
        json.dumps({"type": "item_hand", "itemId": item.id, "targetUserId": "2"}),
    )

    assert (item.carrierId, item.x, item.y, item.z, item.version) == original_state
    hand_result = transport.last_packet_of_type(actor, ItemActionResultPacket)
    assert hand_result.ok is False
    assert "nearby" in hand_result.message.lower()


@pytest.mark.asyncio
async def test_hand_item_can_return_borrowed_item_to_owner(
    make_world,
) -> None:
    """The item's owner can receive a borrowed item with own pickup permission."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    actor = world.join(
        "borrower",
        client_id="u1",
        user_id="1",
        username="borrower_user",
        permissions={"item.pickup_drop.any"},
        x=5,
        y=6,
        z=0,
    )
    owner = world.join(
        "owner",
        client_id="u2",
        user_id="2",
        username="owner_user",
        permissions={"item.pickup_drop.own"},
        x=10,
        y=6,
        z=0,
    )
    item = server.item_service.default_item(owner, "dice")
    item.carrierId = actor.id
    item.x = actor.x
    item.y = actor.y
    item.z = actor.z
    server.item_service.add_item(item)
    original_version = item.version

    await server._handle_message(
        actor,
        json.dumps({"type": "item_hand", "itemId": item.id, "targetUserId": "2"}),
    )

    assert item.createdBy == owner.user_id
    assert item.carrierId == owner.id
    assert (item.x, item.y, item.z) == (owner.x, owner.y, owner.z)
    assert item.version == original_version + 1
    result = transport.last_packet_of_type(actor, ItemActionResultPacket)
    assert result.ok is True
    assert result.action == "hand"


@pytest.mark.asyncio
async def test_hand_item_rejects_full_recipient_without_mutation(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full recipient rejects a handoff before changing the item."""

    world = make_world(grid_size=41, max_carried_items=2)
    server, transport = world.server, world.transport
    owner = world.join(
        "owner",
        client_id="u1",
        user_id="1",
        username="owner_user",
        permissions={"item.pickup_drop.own"},
        x=5,
        y=6,
        z=0,
    )
    target = world.join(
        "target",
        client_id="u2",
        user_id="2",
        username="target_user",
        permissions={"item.pickup_drop.any"},
        x=8,
        y=9,
        z=0,
    )
    item = server.item_service.default_item(owner, "dice")
    item.carrierId = owner.id
    item.x = owner.x
    item.y = owner.y
    item.z = owner.z
    server.item_service.add_item(item)
    monkeypatch.setattr(
        server.auth_service,
        "list_users_for_admin",
        lambda: [
            {"id": "1", "username": "owner_user", "status": "active"},
            {"id": "2", "username": "target_user", "status": "active"},
            {"id": "3", "username": "offline_user", "status": "active"},
        ],
    )
    await server._handle_message(
        owner,
        json.dumps({"type": "item_hand_targets", "itemId": item.id}),
    )
    targets_result = transport.last_packet_of_type(owner, ItemHandTargetsResultPacket)
    assert [entry.userId for entry in targets_result.targets] == [target.user_id]
    target_items = [server.item_service.default_item(target, "dice") for _ in range(2)]
    for target_item in target_items:
        target_item.carrierId = target.id
        server.item_service.add_item(target_item)
    await server._handle_message(
        owner, json.dumps({"type": "item_hand_targets", "itemId": item.id})
    )
    assert (
        transport.last_packet_of_type(owner, ItemHandTargetsResultPacket).targets == []
    )
    original_state = (
        item.carrierId,
        item.x,
        item.y,
        item.z,
        item.createdBy,
        item.version,
    )

    await server._handle_message(
        owner,
        json.dumps({"type": "item_hand", "itemId": item.id, "targetUserId": "2"}),
    )

    assert (
        item.carrierId,
        item.x,
        item.y,
        item.z,
        item.createdBy,
        item.version,
    ) == original_state
    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is False
    assert result.action == "hand"
    assert "receive" in result.message.lower()


@pytest.mark.asyncio
async def test_hand_item_rejects_offline_recipient_without_mutation(
    make_world,
) -> None:
    """A carried item cannot be handed to an offline account."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    owner = world.join(
        "owner",
        client_id="u1",
        user_id="1",
        username="owner_user",
        permissions={"item.pickup_drop.own"},
        x=5,
        y=6,
        z=1,
    )
    item = server.item_service.default_item(owner, "dice")
    item.carrierId = owner.id
    item.x = owner.x
    item.y = owner.y
    item.z = owner.z
    server.item_service.add_item(item)
    original_state = (item.carrierId, item.createdBy, item.version)

    await server._handle_message(
        owner,
        json.dumps({"type": "item_hand", "itemId": item.id, "targetUserId": "2"}),
    )

    assert (item.carrierId, item.createdBy, item.version) == original_state
    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is False
    assert result.action == "hand"
    assert "receive" in result.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_online", [True, False], ids=["online", "offline"])
async def test_transfer_allows_item_carried_by_sender_without_moving_it(
    make_world, tmp_path: Path, target_online: bool
) -> None:
    """Ownership transfer changes only ownership for an item held by the sender."""

    world = make_world(auth_db_path=tmp_path / "auth.db", grid_size=41)
    server, transport = world.server, world.transport
    owner_session = server.auth_service.register("held_owner", "password99")
    target_session = server.auth_service.register("held_target", "password99")
    owner = world.join(
        "owner",
        client_id="u1",
        user_id=owner_session.user.id,
        username=owner_session.user.username,
        permissions={"item.transfer.own"},
        x=5,
        y=6,
        z=0,
    )
    if target_online:
        target = world.join(
            "target",
            client_id="u2",
            user_id=target_session.user.id,
            username=target_session.user.username,
            permissions=set(),
            x=20,
            y=20,
            z=40,
        )
    item = server.item_service.default_item(owner, "dice")
    item.carrierId = owner.id
    item.x = owner.x
    item.y = owner.y
    item.z = owner.z
    server.item_service.add_item(item)
    if target_online:
        target_items = [
            server.item_service.default_item(target, "dice") for _ in range(2)
        ]
        for target_item in target_items:
            target_item.carrierId = target.id
            server.item_service.add_item(target_item)
    original_position = (item.x, item.y, item.z)
    original_version = item.version

    await server._handle_message(
        owner,
        json.dumps(
            {
                "type": "item_transfer",
                "itemId": item.id,
                "targetUserId": target_session.user.id,
            }
        ),
    )

    assert item.createdBy == target_session.user.id
    assert item.createdByName == target_session.user.username
    assert item.carrierId == owner.id
    assert (item.x, item.y, item.z) == original_position
    assert item.version == original_version + 1
    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is True
    assert result.action == "transfer"


@pytest.mark.asyncio
async def test_hand_rejects_item_not_held_by_sender(
    make_world,
) -> None:
    """A handoff requires the sender to be carrying the selected item."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    owner = world.join(
        "owner",
        client_id="u1",
        user_id="1",
        username="owner_user",
        permissions={"item.pickup_drop.own"},
        x=5,
        y=6,
        z=0,
    )
    item = server.item_service.default_item(owner, "dice")
    server.item_service.add_item(item)

    await server._handle_message(
        owner,
        json.dumps({"type": "item_hand", "itemId": item.id, "targetUserId": "2"}),
    )

    result = transport.last_packet_of_type(owner, ItemActionResultPacket)
    assert result.ok is False
    assert result.action == "hand"
    assert "carrying" in result.message.lower()


@pytest.mark.asyncio
async def test_held_item_transfer_rejects_item_carried_by_someone_else(
    make_world,
) -> None:
    """Transfer-any permission cannot take an item from another carrier."""

    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    actor = world.join(
        "actor",
        client_id="u1",
        user_id="1",
        username="actor_user",
        permissions={"item.transfer.any"},
        x=5,
        y=6,
    )
    carrier = world.join(
        "carrier",
        client_id="u2",
        user_id="2",
        username="carrier_user",
        permissions=set(),
        x=5,
        y=6,
    )
    item = server.item_service.default_item(carrier, "dice")
    item.carrierId = carrier.id
    server.item_service.add_item(item)
    original_state = (item.carrierId, item.createdBy, item.version)

    await server._handle_message(
        actor,
        json.dumps({"type": "item_transfer", "itemId": item.id, "targetUserId": "1"}),
    )

    assert (item.carrierId, item.createdBy, item.version) == original_state
    result = transport.last_packet_of_type(actor, ItemActionResultPacket)
    assert result.ok is False
    assert "carried by another" in result.message.lower()


@pytest.mark.asyncio
async def test_admin_user_delete_requires_permission(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    client = world.join(
        "Tester",
        user_id="1",
        username="tester",
        permissions={"user.ban_unban"},
        client_id="u1",
    )

    await server._handle_message(
        client, json.dumps({"type": "admin_user_delete", "username": "alpha"})
    )

    assert transport.packets_to(client)
    packet = transport.last_packet_of_type(client, AdminActionResultPacket)
    assert packet.ok is False
    assert packet.action == "user_delete"
    assert "not authorized" in packet.message.lower()


@pytest.mark.asyncio
async def test_user_list_permission_allows_read_only_registered_user_list(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    client = world.join(
        "Tester",
        user_id="1",
        username="tester",
        permissions={"user.list"},
        client_id="u1",
    )

    monkeypatch.setattr(
        server.auth_service,
        "list_users_for_admin",
        lambda: [
            {
                "id": "1",
                "username": "tester",
                "role": "user",
                "status": "active",
                "lastSeenAt": 1_800_000_000_000,
            }
        ],
    )

    await server._handle_message(client, json.dumps({"type": "admin_users_list"}))

    result = transport.last_packet_of_type(client, AdminUsersListResultPacket)
    assert result.users[0].username == "tester"
    assert result.users[0].online is True


@pytest.mark.asyncio
async def test_user_list_permission_does_not_allow_admin_target_lists(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    client = world.join(
        "Tester",
        user_id="1",
        username="tester",
        permissions={"user.list"},
        client_id="u1",
    )

    await server._handle_message(
        client,
        json.dumps({"type": "admin_users_list", "action": "ban"}),
    )

    result = transport.last_packet_of_type(client, AdminActionResultPacket)
    assert result.ok is False
    assert result.action == "user_ban"


@pytest.mark.asyncio
async def test_admin_user_delete_calls_auth_service(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    client = world.join(
        "Tester",
        user_id="1",
        username="tester",
        permissions={"account.delete.any"},
        client_id="u1",
    )

    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        server.auth_service, "get_user_id_by_username", lambda _username: None
    )

    def fake_delete_user(username: str, *, actor_user_id: str | None = None) -> str:
        calls.append((username, actor_user_id))
        return username

    monkeypatch.setattr(server.auth_service, "delete_user", fake_delete_user)

    await server._handle_message(
        client, json.dumps({"type": "admin_user_delete", "username": "alpha"})
    )

    assert calls == [("alpha", "1")]
    assert transport.packets_to(client)
    packet = transport.last_packet_of_type(client, AdminActionResultPacket)
    assert packet.ok is True
    assert packet.action == "user_delete"


@pytest.mark.asyncio
async def test_broadcast_fanout_is_concurrent(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = world.join("first", client_id="u1")
    second = world.join("second", client_id="u2")
    second_started = asyncio.Event()
    record = world.transport.deliver

    async def delayed_delivery(client: ClientConnection, packet: object) -> None:
        if client is first:
            await second_started.wait()
        else:
            second_started.set()
        await record(client, packet)

    monkeypatch.setattr(world.transport, "deliver", delayed_delivery)
    packet = {"type": "noop"}
    await asyncio.wait_for(world.server.delivery.broadcast(packet), timeout=1)

    assert world.transport.packets_to(first) == [packet]
    assert world.transport.packets_to(second) == [packet]


@pytest.mark.asyncio
async def test_item_add_rejects_unknown_type(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    client = world.join("tester", x=5, y=6, permissions={"item.create"}, client_id="u1")

    await server._handle_message(
        client, json.dumps({"type": "item_add", "itemType": "not_a_type"})
    )

    assert transport.packets_to(client)
    item_result = transport.last_packet_of_type(client, ItemActionResultPacket)
    assert item_result.ok is False
    assert "unknown item type" in item_result.message.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preset_id", "movement_blocked", "expected_x", "sound_x"),
    (("curtain", False, 6, 6), ("solid", True, 5, 5)),
)
async def test_wall_contact_broadcasts_world_sound(
    make_world,
    preset_id: str,
    movement_blocked: bool,
    expected_x: int,
    sound_x: int,
) -> None:
    world = make_world(
        structure_presets={
            preset_id: {
                "title": "Curtain" if preset_id == "curtain" else "Wall",
                "movementBlocked": movement_blocked,
                "soundTransmission": 0.5 if preset_id == "curtain" else 0.0,
                "height": 40,
                "contactSound": "/sounds/wall.ogg",
            }
        }
    )
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5, client_id="u1")
    server.structure_service.add_wall(client, preset_id=preset_id, direction="east")

    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 6, "y": 5, "z": 0})
    )

    assert client.x == expected_x
    sound = transport.last_packet_of_type(observer, WorldSoundPacket)
    assert sound.sound == "/sounds/wall.ogg"
    assert (sound.x, sound.y, sound.z) == (sound_x, 5, 0)
    assert sound.acousticZoneId == "floor:0"


@pytest.mark.asyncio
async def test_update_position_enforces_cumulative_budget_per_tick(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    server.movement_tick_ms = 100
    server.movement_max_steps_per_tick = 2
    client = world.join("tester", x=5, y=5, client_id="u1")

    fixed_now = 10_000
    monkeypatch.setattr(server.item_service, "now_ms", lambda: fixed_now)

    # First 1-step move in this tick: allowed.
    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 6, "y": 5, "z": 0})
    )
    # Second 1-step move in the same tick: allowed (budget now exhausted at 2).
    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 7, "y": 5, "z": 0})
    )
    # Third 1-step move in the same tick: must be rejected.
    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 8, "y": 5, "z": 0})
    )

    assert client.x == 7
    assert client.y == 5
    assert len(transport.packets_to(observer)) == 2


@pytest.mark.asyncio
async def test_teleport_complete_broadcasts_spatial_event(
    make_world,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=12, y=13, client_id="u1")

    await server._handle_message(
        client,
        json.dumps({"type": "teleport_complete", "x": 12, "y": 13, "z": 0}),
    )

    position_packets = transport.packets_of_type(observer, BroadcastPositionPacket)
    teleport_packets = transport.packets_of_type(
        observer, BroadcastTeleportCompletePacket
    )
    assert len(position_packets) == 1
    assert len(teleport_packets) == 1
    assert position_packets[0].id == "u1"
    assert position_packets[0].x == 12
    assert position_packets[0].y == 13
    assert teleport_packets[0].id == "u1"
    assert teleport_packets[0].x == 12
    assert teleport_packets[0].y == 13
    assert teleport_packets[0].facingDeg == client.facing_deg
    assert teleport_packets[0].acousticZoneId == "floor:0"
    assert (
        transport.packets_of_type(client, BroadcastPositionPacket) == position_packets
    )
    assert transport.packets_of_type(client, BroadcastTeleportCompletePacket) == []


@pytest.mark.asyncio
async def test_teleport_preserves_facing(make_world) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=12, y=13, client_id="u1", facing_deg=225)

    await server._handle_message(
        client,
        json.dumps({"type": "teleport_complete", "x": 20, "y": 21, "z": 0}),
    )

    assert client.facing_deg == 225
    position = transport.last_packet_of_type(observer, BroadcastPositionPacket)
    teleport = transport.last_packet_of_type(observer, BroadcastTeleportCompletePacket)
    assert position.facingDeg == 225
    assert teleport.facingDeg == 225


@pytest.mark.asyncio
async def test_update_position_rate_reject_sends_self_correction(
    make_world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = make_world(grid_size=41)
    server, transport = world.server, world.transport
    client = world.join("tester", x=5, y=5, client_id="u1")
    server.movement_tick_ms = 100
    server.movement_max_steps_per_tick = 1

    fixed_now = 10_000
    monkeypatch.setattr(server.item_service, "now_ms", lambda: fixed_now)

    # 2-tile move exceeds per-window budget and should be rejected with correction.
    await server._handle_message(
        client, json.dumps({"type": "update_position", "x": 7, "y": 5, "z": 0})
    )

    assert client.x == 5
    assert client.y == 5
    assert transport.packets_to(client)
    correction = transport.last_packet_of_type(client, BroadcastPositionPacket)
    assert correction.id == "u1"
    assert correction.x == 5
    assert correction.y == 5


@pytest.mark.asyncio
async def test_chat_me_command_broadcasts_action(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("Tester", permissions={"chat.send"}, client_id="u1")

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": "/Me waves hello"})
    )

    assert len(transport.packets_to(observer)) == 1
    packet = transport.last_packet_of_type(observer, BroadcastChatMessagePacket)
    assert transport.packets_to(client) == [packet]
    assert packet.action is True
    assert packet.system is False
    assert packet.message == "Tester waves hello"


@pytest.mark.asyncio
async def test_chat_up_command_sends_sender_only(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("Tester", permissions={"chat.send"}, client_id="u1")

    monkeypatch.setattr(server, "_format_uptime", lambda: "1h 2m 3s")

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": "/UP"})
    )

    assert transport.packets_to(observer) == []
    assert len(transport.packets_to(client)) == 1
    packet = transport.last_packet_of_type(client, BroadcastChatMessagePacket)
    assert packet.system is True
    assert packet.message == "Server uptime: 1h 2m 3s"


@pytest.mark.asyncio
async def test_chat_command_requires_leading_slash(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("Tester", permissions={"chat.send"}, client_id="u1")

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": " /up"})
    )

    assert len(transport.packets_to(observer)) == 1
    packet = transport.last_packet_of_type(observer, BroadcastChatMessagePacket)
    assert packet.system is False
    assert packet.action is False
    assert packet.message == " /up"


@pytest.mark.asyncio
async def test_chat_version_command_is_sender_only(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("Tester", permissions={"chat.send"}, client_id="u1")
    server.server_version = "2026.02.27 R293"

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": "/version"})
    )

    assert transport.packets_to(observer) == []
    assert len(transport.packets_to(client)) == 1
    packet = transport.last_packet_of_type(client, BroadcastChatMessagePacket)
    assert packet.system is True
    assert packet.message == "Server version: 2026.02.27 R293"


@pytest.mark.asyncio
async def test_chat_reboot_requires_permission(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, transport = world.server, world.transport
    client = world.join(
        "Tester", user_id="1", permissions={"chat.send"}, client_id="u1"
    )

    monkeypatch.setattr(
        server, "_schedule_reboot", lambda _requested_by, _message: True
    )

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": "/reboot patching"})
    )

    assert transport.packets_to(client)
    packet = transport.last_packet_of_type(client, BroadcastChatMessagePacket)
    assert packet.system is True
    assert "not authorized" in packet.message.lower()


@pytest.mark.asyncio
async def test_chat_reboot_schedules_and_broadcasts_message(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join(
        "Tester",
        user_id="1",
        username="tester",
        permissions={"chat.send", "server.allow_reboot"},
        client_id="u1",
    )

    monkeypatch.setattr(
        server,
        "_schedule_reboot",
        lambda requested_by, message: (
            requested_by == "tester" and message == "maintenance"
        ),
    )

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": "/reboot maintenance"})
    )

    assert len(transport.packets_to(observer)) == 1
    packet = transport.last_packet_of_type(observer, BroadcastChatMessagePacket)
    assert packet.system is True
    assert packet.message == "Server rebooting in 5 seconds. maintenance"


@pytest.mark.asyncio
async def test_chat_reboot_already_in_progress_sends_sender_only_notice(
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join(
        "Tester",
        user_id="1",
        username="tester",
        permissions={"chat.send", "server.allow_reboot"},
        client_id="u1",
    )

    monkeypatch.setattr(
        server, "_schedule_reboot", lambda _requested_by, _message: False
    )

    await server._handle_message(
        client, json.dumps({"type": "chat_message", "message": "/reboot maintenance"})
    )

    assert transport.packets_to(observer) == []
    assert len(transport.packets_to(client)) == 1
    packet = transport.last_packet_of_type(client, BroadcastChatMessagePacket)
    assert packet.system is True
    assert packet.message == "Server reboot already in progress."
