"""Shared [engine] cadence knobs. Adapters read this; strategy does not."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from shared.constants.paths import BASE_DIR

TEMPLATE_PATH = BASE_DIR / "config" / "trading.toml"


@dataclass(frozen=True)
class EngineCadence:
    debounce_ms: int
    quoter_tick_s: float


def read_engine_cadence(*, path: Path = TEMPLATE_PATH) -> EngineCadence:
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    engine = document["engine"]
    debounce_ms = engine["debounce_ms"]
    quoter_tick_s = engine["quoter_tick_s"]
    if type(debounce_ms) is not int or debounce_ms < 0:
        raise ValueError("engine.debounce_ms must be a non-negative int")
    if type(quoter_tick_s) is not float or quoter_tick_s < 0.0:
        raise ValueError("engine.quoter_tick_s must be a non-negative float")
    return EngineCadence(debounce_ms=debounce_ms, quoter_tick_s=quoter_tick_s)
