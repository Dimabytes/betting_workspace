"""Shared viewer shapes: plotted series, order segments, markers, and game state."""

from dataclasses import dataclass
from math import floor
from typing import Literal


@dataclass(frozen=True)
class Series:
    """One plotted line: x is seconds from horn."""

    seconds: tuple[float, ...]
    y: tuple[float, ...]


@dataclass(frozen=True)
class OrderSegment:
    """Resting order from submit until cancel, reject, or last fill."""

    order_id: str
    side: str
    token_index: int
    level_index: int
    price: float
    start_s: float
    end_s: float


@dataclass(frozen=True)
class TapeMarker:
    """Submit or fill marker in token price space."""

    second: float
    price: float
    side: str
    kind: Literal["submit", "fill"]
    level_index: int
    quantity: float


@dataclass(frozen=True)
class GameState:
    """Model-window features; x is wall seconds from horn, game_seconds the feed clock."""

    seconds: tuple[float, ...]
    game_seconds: tuple[float, ...]
    radiant_nw: tuple[float, ...]
    dire_nw: tuple[float, ...]
    radiant_nw_adv: tuple[float, ...]
    top1_nw_adv: tuple[float, ...]
    radiant_xp_adv: tuple[float, ...]
    deaths_radiant: tuple[float, ...]
    deaths_dire: tuple[float, ...]


@dataclass(frozen=True)
class TokenTape:
    """Everything drawn for one bought token."""

    token_index: int
    side_name: str
    mid: Series
    bid: Series
    ask: Series
    fair: Series
    pred_cents: Series
    segments: tuple[OrderSegment, ...]
    submits: tuple[TapeMarker, ...]
    fills: tuple[TapeMarker, ...]


def empty_game_state() -> GameState:
    """No game-state rows for this map."""
    return GameState((), (), (), (), (), (), (), (), ())


def token_predicted_delta(
    predicted_delta: float, token_index: int, radiant_token_index: int
) -> float:
    """Map a Radiant-space model delta onto the plotted token."""
    if token_index == radiant_token_index:
        return predicted_delta
    return -predicted_delta


def last_per_second(seconds: list[float], values: list[float]) -> Series:
    """Keep the last sample in each truncated game second."""
    if not seconds:
        return Series(seconds=(), y=())
    by_second: dict[int, float] = {}
    for second, value in zip(seconds, values, strict=True):
        by_second[floor(second)] = value
    ordered = sorted(by_second)
    return Series(
        seconds=tuple(float(key) for key in ordered),
        y=tuple(by_second[key] for key in ordered),
    )
