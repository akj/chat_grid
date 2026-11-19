from pathlib import Path

import pytest

from app.config import load_config


def test_load_config_defaults_when_path_none() -> None:
    cfg = load_config(None)
    assert cfg.server.bind_ip == "127.0.0.1"
    assert cfg.server.base_path == "/"
    assert cfg.network.allow_insecure_ws is False
    assert cfg.storage.state_file == "runtime/items.json"
    assert cfg.storage.state_save_debounce_ms == 200
    assert cfg.storage.state_save_max_delay_ms == 1000
    assert cfg.world.grid_size == 41
    assert set(cfg.world.structure_presets) == {"brick", "curtain", "glass", "fence"}
    assert cfg.world.structure_presets["curtain"].movement_blocked is False
    assert cfg.world.structure_presets["curtain"].contact_sound == "/sounds/curtain.ogg"
    assert cfg.world.structure_presets["glass"].sound_transmission == 0.35
    assert cfg.world.structure_presets["glass"].occlusion_lowpass_hz == 4500
    assert cfg.world.structure_presets["glass"].contact_sound == "/sounds/glass.ogg"
    assert cfg.world.structure_presets["fence"].contact_sound == "/sounds/fence.ogg"
    assert cfg.world.structure_presets["fence"].sound_transmission == 0.7
    assert cfg.livekit.room_name == "chatgrid"
    assert cfg.items.max_carried_items == 2
    assert cfg.items.max_items_per_user == 500
    limits = cfg.rate_limits
    assert (limits.chat_messages, limits.chat_window_ms) == (5, 5_000)
    assert (limits.piano_notes, limits.piano_window_ms) == (40, 1_000)
    assert (limits.item_changes, limits.item_window_ms) == (10, 10_000)
    assert (limits.item_updates, limits.item_update_window_ms) == (60, 1_000)


def test_load_config_requires_tls_when_insecure_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[network]
allow_insecure_ws = false
""".strip()
    )
    with pytest.raises(ValueError):
        load_config(config_path)


def test_load_config_reads_state_save_timing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[network]
allow_insecure_ws = true

[storage]
state_file = "runtime/items.json"
state_save_debounce_ms = 150
state_save_max_delay_ms = 900
""".strip()
    )
    cfg = load_config(config_path)
    assert cfg.storage.state_save_debounce_ms == 150
    assert cfg.storage.state_save_max_delay_ms == 900


def test_load_config_reads_server_base_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[network]
allow_insecure_ws = true

[server]
base_path = "/ttgrid/"
""".strip()
    )
    cfg = load_config(config_path)
    assert cfg.server.base_path == "/ttgrid/"


def test_load_config_reads_grid_name_and_welcome_message(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[network]
allow_insecure_ws = true

[server]
grid_name = "TT Grid"
welcome_message = "Welcome to TT Grid."
""".strip()
    )
    cfg = load_config(config_path)
    assert cfg.server.grid_name == "TT Grid"
    assert cfg.server.welcome_message == "Welcome to TT Grid."


@pytest.mark.parametrize("preset_id", ["", "x" * 41])
def test_load_config_rejects_invalid_structure_preset_ids(
    tmp_path: Path,
    preset_id: str,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'''\
[network]
allow_insecure_ws = true

[world.structure_presets."{preset_id}"]
title = "Invalid preset"
'''.strip()
    )

    with pytest.raises(ValueError):
        load_config(config_path)


def test_load_config_reads_livekit_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[network]
allow_insecure_ws = true

[livekit]
room_name = "grid-room"
""".strip()
    )
    cfg = load_config(config_path)
    assert cfg.livekit.room_name == "grid-room"


def test_load_config_reads_max_carried_items(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[network]
allow_insecure_ws = true

[items]
max_carried_items = 4
""".strip()
    )

    cfg = load_config(config_path)
    assert cfg.items.max_carried_items == 4


@pytest.mark.parametrize("value", [0, -1])
def test_load_config_rejects_invalid_max_carried_items(
    tmp_path: Path, value: int
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[network]
allow_insecure_ws = true

[items]
max_carried_items = {value}
""".strip()
    )

    with pytest.raises(ValueError):
        load_config(config_path)
