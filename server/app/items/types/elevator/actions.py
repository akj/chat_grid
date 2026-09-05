"""Elevator item action fallback."""

from __future__ import annotations

from typing import Callable

from ....item_types import ItemUseResult
from ....models import WorldItem
from .car import ElevatorCar


def use_item(
    item: WorldItem, _nickname: str, _clock_formatter: Callable[[dict], str]
) -> ItemUseResult:
    """Return the fallback message when server elevator routing is unavailable."""

    return ItemUseResult(
        self_message=f"{item.title} is unavailable.",
        others_message="",
    )


def secondary_use_item(
    item: WorldItem, _nickname: str, _clock_formatter: Callable[[dict], str]
) -> ItemUseResult:
    """Report the elevator car's landing or travel state."""

    return ItemUseResult(self_message=ElevatorCar(item).describe(), others_message="")
