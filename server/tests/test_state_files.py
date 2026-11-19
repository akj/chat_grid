"""World state files survive interrupted saves and files that fail to load."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from .conftest import World

STATE_FILES = ["items.json", "structures.json", "ambiances.json", "piano_songs.json"]
# A save cut off partway through, and well-formed JSON of the wrong shape.
UNREADABLE_CONTENTS = ['[{"id":"i1","type":"dice"', '{"not":"the expected shape"}']
PIANO_SONGS_UNREADABLE_CONTENTS = [
    '{"s1":{"notes":[',
    '["not","the","expected","shape"]',
]


@pytest.fixture
def make_grid(make_world, tmp_path: Path) -> Callable[[], World]:
    """Build a server whose world state lives in tmp_path."""

    sounds_dir = tmp_path / "ambiance-sounds"
    sounds_dir.mkdir()
    (sounds_dir / "city.ogg").write_bytes(b"")

    def make_grid() -> World:
        return make_world(
            state_file=tmp_path / "items.json",
            auth_db_path=tmp_path / "auth.db",
            ambiance_sounds_dir=sounds_dir,
            structure_presets={"brick": {"title": "Brick", "movementBlocked": True}},
        )

    return make_grid


def save_everything(world: World) -> None:
    """Run the same saves the server performs after world and piano edits."""

    world.server._flush_state_save()
    world.server.item_service.save_piano_songs()


def change_everything(world: World) -> None:
    """Make one edit to every kind of persisted world state."""

    server = world.server
    edit = len(server.item_service.items)
    builder = world.connect(
        f"builder{edit}", client_id=f"builder{edit}", x=2 + edit * 2, y=4
    )
    server.item_service.add_item(server.item_service.default_item(builder, "dice"))
    server.structure_service.add_wall(builder, preset_id="brick", direction="north")
    server.ambiance_service.add_ambiance(builder)
    server.item_service.piano_songs[f"song-{edit}"] = {"notes": []}


def set_aside_copies(directory: Path, name: str) -> list[Path]:
    """Return the copies of an unreadable state file kept for recovery."""

    return sorted(directory.glob(f"{name}.unreadable-*"))


@pytest.mark.parametrize("name", STATE_FILES)
@pytest.mark.parametrize("variant", [0, 1], ids=["truncated", "wrong-shape"])
def test_unreadable_state_file_is_kept_when_the_world_is_next_saved(
    make_grid: Callable[[], World], tmp_path: Path, name: str, variant: int
) -> None:
    contents = (
        PIANO_SONGS_UNREADABLE_CONTENTS
        if name == "piano_songs.json"
        else UNREADABLE_CONTENTS
    )[variant]
    (tmp_path / name).write_text(contents, encoding="utf-8")

    world = make_grid()
    change_everything(world)
    save_everything(world)

    kept = set_aside_copies(tmp_path, name)
    assert [path.read_text(encoding="utf-8") for path in kept] == [contents]


def test_readable_state_files_are_not_set_aside(
    make_grid: Callable[[], World], tmp_path: Path
) -> None:
    world = make_grid()
    change_everything(world)
    save_everything(world)

    reloaded = make_grid()
    change_everything(reloaded)
    save_everything(reloaded)

    assert list(tmp_path.glob("*.unreadable-*")) == []
    assert len(reloaded.server.item_service.items) == 2
    assert len(reloaded.server.structure_service.structures) == 2
    assert len(reloaded.server.ambiance_service.ambiances) == 2
    assert len(reloaded.server.item_service.piano_songs) == 2


def test_interrupted_save_leaves_every_previous_file_intact(
    make_grid: Callable[[], World], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_grid()
    change_everything(world)
    save_everything(world)
    before = {name: (tmp_path / name).read_bytes() for name in STATE_FILES}

    change_everything(world)

    def crash_before_data_is_durable(fd: int) -> None:
        raise OSError("simulated power loss")

    monkeypatch.setattr(os, "fsync", crash_before_data_is_durable)
    save_everything(world)

    assert {name: (tmp_path / name).read_bytes() for name in STATE_FILES} == before
    assert sorted(path.name for path in tmp_path.iterdir() if path.is_file()) == sorted(
        [*STATE_FILES, "auth.db"]
    )
