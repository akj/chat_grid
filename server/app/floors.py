"""Canonical world floor elevations and display names."""

FLOOR_DEFINITIONS: tuple[dict[str, str | int], ...] = (
    {"id": "ground", "name": "Ground floor", "z": 0},
    {"id": "second", "name": "Second floor", "z": 40},
)
FLOOR_ELEVATIONS = frozenset(int(floor["z"]) for floor in FLOOR_DEFINITIONS)


def is_floor(z: int) -> bool:
    """Return whether a height is a configured floor elevation."""

    return z in FLOOR_ELEVATIONS


def floor_name(z: int) -> str:
    """Return the configured floor name, or the height for an unknown floor."""

    for floor in FLOOR_DEFINITIONS:
        if int(floor["z"]) == z:
            return str(floor["name"])
    return f"z {z}"
