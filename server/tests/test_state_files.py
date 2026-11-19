"""World state files survive interrupted saves and files that fail to load."""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from .conftest import World

STATE_FILES = ["items.json", "structures.json", "ambiances.json", "piano_songs.json"]
# A save cut off partway through, well-formed JSON of the wrong shape, and the
# right shape holding an invalid entry.
UNREADABLE_CONTENTS = {
    "truncated": '[{"id":"i1","type":"dice"',
    "wrong-shape": '{"not":"the expected shape"}',
    "bad-entry": '[{"id":"i1"}]',
}
PIANO_SONGS_UNREADABLE_CONTENTS = {
    "truncated": '{"s1":{"notes":[',
    "wrong-shape": '["not","the","expected","shape"]',
    "bad-entry": '{"s1":[]}',
}


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
    server.item_service.piano_songs[f"song-{uuid.uuid4().hex}"] = {"notes": []}


def set_aside_copies(directory: Path, name: str) -> list[Path]:
    """Return the copies of an unreadable state file kept for recovery."""

    return sorted(directory.glob(f"{name}.unreadable-*"))


@pytest.mark.parametrize("name", STATE_FILES)
@pytest.mark.parametrize("variant", list(UNREADABLE_CONTENTS))
def test_unreadable_state_file_is_kept_when_the_world_is_next_saved(
    make_grid: Callable[[], World], tmp_path: Path, name: str, variant: str
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


@pytest.mark.parametrize("name", STATE_FILES)
def test_unreadable_file_that_cannot_be_set_aside_is_never_saved_over(
    make_grid: Callable[[], World],
    tmp_path: Path,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contents = UNREADABLE_CONTENTS["truncated"]
    (tmp_path / name).write_text(contents, encoding="utf-8")
    real_replace = os.replace

    def refuse_set_aside(src: str | Path, dst: str | Path) -> None:
        if ".unreadable-" in str(dst):
            raise PermissionError("simulated read-only directory")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", refuse_set_aside)
    world = make_grid()
    change_everything(world)
    save_everything(world)

    assert (tmp_path / name).read_text(encoding="utf-8") == contents
    assert set_aside_copies(tmp_path, name) == []


def test_state_path_that_is_a_directory_is_left_in_place(
    make_world, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "chatgrid.db").write_bytes(b"accounts")

    world = make_world(state_file=runtime, auth_db_path=tmp_path / "auth.db")
    save_everything(world)

    assert list(tmp_path.glob("*.unreadable-*")) == []
    assert (runtime / "chatgrid.db").read_bytes() == b"accounts"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs POSIX directory permissions that apply to this user",
)
def test_state_directory_without_access_starts_without_saving(
    make_world, tmp_path: Path
) -> None:
    state_dir = tmp_path / "state"
    sounds_dir = tmp_path / "ambiance-sounds"
    state_dir.mkdir()
    sounds_dir.mkdir()
    (sounds_dir / "city.ogg").write_bytes(b"")

    def make_grid() -> World:
        return make_world(
            state_file=state_dir / "items.json",
            auth_db_path=tmp_path / "auth.db",
            ambiance_sounds_dir=sounds_dir,
            structure_presets={"brick": {"title": "Brick", "movementBlocked": True}},
        )

    world = make_grid()
    change_everything(world)
    save_everything(world)
    before = {name: (state_dir / name).read_bytes() for name in STATE_FILES}

    state_dir.chmod(0)
    try:
        locked_out = make_grid()
    finally:
        state_dir.chmod(0o700)
    change_everything(locked_out)
    save_everything(locked_out)

    assert {name: (state_dir / name).read_bytes() for name in STATE_FILES} == before
    assert sorted(path.name for path in state_dir.iterdir()) == sorted(STATE_FILES)


def test_symlinked_state_file_keeps_its_link(
    make_grid: Callable[[], World], tmp_path: Path
) -> None:
    volume = tmp_path / "volume"
    volume.mkdir()
    for name in STATE_FILES:
        (volume / name).write_text("[]" if name != "piano_songs.json" else "{}")
        (tmp_path / name).symlink_to(volume / name)

    world = make_grid()
    change_everything(world)
    save_everything(world)

    assert all((tmp_path / name).is_symlink() for name in STATE_FILES)
    reloaded = make_grid()
    assert len(reloaded.server.item_service.items) == 1
    assert len(reloaded.server.structure_service.structures) == 1
    assert len(reloaded.server.ambiance_service.ambiances) == 1
    assert len(reloaded.server.item_service.piano_songs) == 1


def test_unreadable_symlinked_state_file_is_set_aside_at_its_target(
    make_grid: Callable[[], World], tmp_path: Path
) -> None:
    volume = tmp_path / "volume"
    volume.mkdir()
    contents = UNREADABLE_CONTENTS["truncated"]
    (volume / "items.json").write_text(contents, encoding="utf-8")
    (tmp_path / "items.json").symlink_to(volume / "items.json")

    world = make_grid()
    change_everything(world)
    save_everything(world)

    assert (tmp_path / "items.json").is_symlink()
    kept = set_aside_copies(volume, "items.json")
    assert [path.read_text(encoding="utf-8") for path in kept] == [contents]
    assert len(make_grid().server.item_service.items) == 1


def test_save_through_a_link_to_a_missing_directory_creates_nothing(
    make_grid: Callable[[], World], tmp_path: Path
) -> None:
    unmounted = tmp_path / "volume" / "state"
    (tmp_path / "items.json").symlink_to(unmounted / "items.json")

    world = make_grid()
    change_everything(world)
    save_everything(world)

    assert not (tmp_path / "volume").exists()
    assert (tmp_path / "items.json").readlink() == unmounted / "items.json"
    assert list(tmp_path.glob(".items.json.*.tmp")) == []


@pytest.mark.skipif(os.name != "posix", reason="requires directory fsync")
def test_backup_sync_failure_disables_saves(
    make_grid: Callable[[], World], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "items.json"
    path.write_text("invalid", encoding="utf-8")
    real_fsync = os.fsync

    def fail_directory_sync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory sync failed")
        real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail_directory_sync)
        world = make_grid()

    change_everything(world)
    save_everything(world)
    assert not path.exists()
    (backup,) = set_aside_copies(tmp_path, path.name)
    assert backup.read_text(encoding="utf-8") == "invalid"
