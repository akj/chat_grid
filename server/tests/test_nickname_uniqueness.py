from __future__ import annotations

from app.nicknames import Nicknames


def test_nickname_taken_is_case_insensitive(world) -> None:
    server = world.server
    world.join("Jage", client_id="1")
    world.join("Alice", client_id="2")

    assert server.nicknames.is_taken("jage", exclude_client_id="2")
    assert server.nicknames.is_taken("JAGE", exclude_client_id="2")
    assert not server.nicknames.is_taken("jage", exclude_client_id="1")


def test_nickname_key_uses_casefold() -> None:
    assert Nicknames.key("Jage") == Nicknames.key("jage")
