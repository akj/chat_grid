from __future__ import annotations

import pytest

from app.models import BroadcastPositionPacket, WorldSoundPacket

from .conftest import World


@pytest.mark.asyncio
async def test_rider_step_and_teleport_only_send_self_correction(world: World) -> None:
    server, transport = world.server, world.transport
    observer = world.join("observer", x=40, y=40)
    client = world.join("rider", x=5, y=5, elevator_id="elevator")

    for move in (server.movement.step, server.movement.teleport):
        transport.clear()
        await move(client, 6, 5, 0)

        assert (client.x, client.y, client.z) == (5, 5, 0)
        correction = transport.last_packet_of_type(client, BroadcastPositionPacket)
        assert (correction.x, correction.y, correction.z) == (5, 5, 0)
        assert transport.packets_to(client) == [correction]
        assert transport.packets_to(observer) == []


@pytest.mark.asyncio
async def test_blocked_step_sends_contact_sound_then_self_correction(
    world: World,
) -> None:
    server, transport = world.server, world.transport
    server.structure_service.presets["solid"] = {
        "title": "Wall",
        "movementBlocked": True,
        "soundTransmission": 0.0,
        "height": 40,
        "contactSound": "/sounds/wall.ogg",
    }
    observer = world.join("observer", x=40, y=40)
    client = world.join("tester", x=5, y=5)
    server.structure_service.add_wall(client, preset_id="solid", direction="east")

    await server.movement.step(client, 6, 5, 0)

    assert (client.x, client.y, client.z) == (5, 5, 0)
    sound = transport.last_packet_of_type(observer, WorldSoundPacket)
    assert sound.sound == "/sounds/wall.ogg"
    assert (sound.x, sound.y, sound.z, sound.acousticZoneId) == (5, 5, 0, "floor:0")
    correction = transport.last_packet_of_type(client, BroadcastPositionPacket)
    assert (correction.x, correction.y, correction.z) == (5, 5, 0)
    assert transport.all_deliveries() == [(observer, sound), (client, correction)]
