from pydantic import ValidationError, TypeAdapter
import pytest

from app.models import ClientPacket


def test_update_position_validates() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    packet = adapter.validate_python(
        {"type": "update_position", "x": 10, "y": 12, "z": 0}
    )
    assert packet.type == "update_position"


def test_update_facing_accepts_only_canonical_headings() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)

    packet = adapter.validate_python({"type": "update_facing", "facingDeg": 315})

    assert packet.type == "update_facing"
    assert packet.facingDeg == 315
    for value in (-1, 1, 90.5, 360):
        try:
            adapter.validate_python({"type": "update_facing", "facingDeg": value})
        except ValidationError:
            continue
        assert False, f"heading {value!r} should fail validation"


def test_turn_validates_relative_direction() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)

    assert adapter.validate_python({"type": "turn", "direction": "left"}).type == "turn"
    assert (
        adapter.validate_python({"type": "turn", "direction": "right"}).type == "turn"
    )
    with pytest.raises(ValidationError):
        adapter.validate_python({"type": "turn", "direction": "north"})


def test_unknown_type_rejected() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    try:
        adapter.validate_python({"type": "unknown"})
    except ValidationError:
        return
    assert False, "validation should fail"


def test_item_add_accepts_piano_type() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    packet = adapter.validate_python({"type": "item_add", "itemType": "piano"})
    assert packet.type == "item_add"


def test_item_piano_recording_packet_validates() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    packet = adapter.validate_python(
        {"type": "item_piano_recording", "itemId": "p1", "action": "toggle_record"}
    )
    assert packet.type == "item_piano_recording"
    stop_packet = adapter.validate_python(
        {"type": "item_piano_recording", "itemId": "p1", "action": "stop_record"}
    )
    assert stop_packet.type == "item_piano_recording"


def test_item_transfer_packet_validates() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    packet = adapter.validate_python(
        {"type": "item_transfer", "itemId": "i1", "targetUserId": "u2"}
    )
    assert packet.type == "item_transfer"


def test_item_hand_packets_validate() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    packet = adapter.validate_python(
        {"type": "item_hand", "itemId": "i1", "targetUserId": "u2"}
    )
    assert packet.type == "item_hand"
    targets_packet = adapter.validate_python(
        {"type": "item_hand_targets", "itemId": "i1"}
    )
    assert targets_packet.type == "item_hand_targets"


def test_admin_user_delete_packet_validates() -> None:
    adapter: TypeAdapter[ClientPacket] = TypeAdapter(ClientPacket)
    packet = adapter.validate_python({"type": "admin_user_delete", "username": "alpha"})
    assert packet.type == "admin_user_delete"
