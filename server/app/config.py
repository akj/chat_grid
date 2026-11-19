"""Configuration models and loader for the signaling server."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Annotated

from pydantic import BaseModel, Field


StructurePresetId = Annotated[str, Field(min_length=1, max_length=40)]


class ServerConfigSection(BaseModel):
    """Bind address and port options for websocket serving."""

    bind_ip: str = "127.0.0.1"
    port: int = 8765
    base_path: str = "/"
    grid_name: str = "Chat Grid"
    welcome_message: str = (
        "Welcome to the Chat Grid, your immersive audio playground. "
        "Configure your audio, then Log in or register to join the grid."
    )


class NetworkConfigSection(BaseModel):
    """Network transport and safety limits."""

    max_message_bytes: int = Field(default=2_000_000, gt=0)
    allow_insecure_ws: bool = False


class TlsConfigSection(BaseModel):
    """TLS certificate/key file configuration."""

    cert_file: str = ""
    key_file: str = ""


class LoggingConfigSection(BaseModel):
    """Runtime logging verbosity options."""

    level: str = "INFO"


class StorageConfigSection(BaseModel):
    """Persistent state file location."""

    state_file: str = "runtime/items.json"
    state_save_debounce_ms: int = Field(default=200, gt=0)
    state_save_max_delay_ms: int = Field(default=1000, gt=0)


class StructurePresetConfig(BaseModel):
    """Editable defaults used when a builder creates a wall structure."""

    title: str = Field(default="Wall", min_length=1, max_length=80)
    movement_blocked: bool = True
    sound_transmission: float = Field(default=0.0, ge=0.0, le=1.0)
    occlusion_lowpass_hz: int = Field(default=800, ge=20, le=20_000)
    height: int = Field(default=40, ge=0)
    contact_sound: str = "/sounds/wall.ogg"


class WorldConfigSection(BaseModel):
    """Authoritative world geometry options."""

    grid_size: int = Field(default=41, ge=1)
    structure_presets: dict[StructurePresetId, StructurePresetConfig] = Field(
        default_factory=lambda: {
            "brick": StructurePresetConfig(title="Brick"),
            "curtain": StructurePresetConfig(
                title="Curtain",
                movement_blocked=False,
                sound_transmission=0.5,
                occlusion_lowpass_hz=2_200,
                contact_sound="/sounds/curtain.ogg",
            ),
            "glass": StructurePresetConfig(
                title="Glass",
                sound_transmission=0.35,
                occlusion_lowpass_hz=4_500,
                contact_sound="/sounds/glass.ogg",
            ),
            "fence": StructurePresetConfig(
                title="Fence",
                sound_transmission=0.7,
                occlusion_lowpass_hz=9_000,
                contact_sound="/sounds/fence.ogg",
            ),
        }
    )


class AuthConfigSection(BaseModel):
    """Authentication persistence and validation settings."""

    db_file: str = "runtime/chatgrid.db"
    password_min_length: int = Field(default=8, ge=1)
    password_max_length: int = Field(default=32, ge=1)
    username_min_length: int = Field(default=2, ge=1)
    username_max_length: int = Field(default=32, ge=1)


class LiveKitConfigSection(BaseModel):
    """Non-secret LiveKit room settings for voice transport."""

    room_name: str = "chatgrid"


class ItemsConfigSection(BaseModel):
    """Authoritative item interaction limits."""

    max_carried_items: int = Field(default=2, gt=0)
    max_items_per_user: int = Field(default=500, gt=0)


class RateLimitsConfigSection(BaseModel):
    """Per-connection fixed-window limits for chat, piano notes, and item edits."""

    chat_messages: int = Field(default=5, gt=0)
    chat_window_ms: int = Field(default=5_000, gt=0)
    piano_notes: int = Field(default=40, gt=0)
    piano_window_ms: int = Field(default=1_000, gt=0)
    item_changes: int = Field(default=10, gt=0)
    item_window_ms: int = Field(default=10_000, gt=0)
    # Property editing sends one update per arrow-key press, so allow key repeat.
    item_updates: int = Field(default=60, gt=0)
    item_update_window_ms: int = Field(default=1_000, gt=0)


class AppConfig(BaseModel):
    """Top-level application configuration document."""

    server: ServerConfigSection = ServerConfigSection()
    network: NetworkConfigSection = NetworkConfigSection()
    tls: TlsConfigSection = TlsConfigSection()
    logging: LoggingConfigSection = LoggingConfigSection()
    storage: StorageConfigSection = StorageConfigSection()
    world: WorldConfigSection = WorldConfigSection()
    auth: AuthConfigSection = AuthConfigSection()
    livekit: LiveKitConfigSection = LiveKitConfigSection()
    items: ItemsConfigSection = ItemsConfigSection()
    rate_limits: RateLimitsConfigSection = RateLimitsConfigSection()


def load_config(path: Path | None) -> AppConfig:
    """Load and validate config TOML, applying defaults and TLS checks."""

    if path is None:
        return AppConfig()

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("rb") as fp:
        data = tomllib.load(fp)

    config = AppConfig.model_validate(data)

    cert = config.tls.cert_file.strip()
    key = config.tls.key_file.strip()

    if not config.network.allow_insecure_ws and (not cert or not key):
        raise ValueError(
            "TLS is required when network.allow_insecure_ws=false; set tls.cert_file and tls.key_file"
        )

    return config
