"""Live archive lookups the backtest inspector still uses."""

# Nautilus ships InstrumentId and StrategyConfig as Cython without static declarations.
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from catalog_fixtures import catalog_row
from nautilus_trader.model.identifiers import InstrumentId

from backtest import live_archives
from backtest.live_archives import resolve_archive_match_id
from backtest.strategy import DotaMakerConfig, MatchKernelConfig
from shared.constants.strategy import BACKTEST_DOTA_MAX_POSITION_LEVELS
from strategy.policy import follow300_policy
from strategy.types import FreshnessLimits, MarketLimits
from trader.core_trace_codec import (
    CORE_TRACE_SCHEMA_VERSION,
    decode_header_row,
    jsonable,
    policy_sha,
)


def build_header_row(
    *, game: str, level_usdc: float, opened_wall_s: float = 1_000.0
) -> dict[str, Any]:
    """A core_trace header row the trader codec accepts, as the live writer emits it."""
    policy = follow300_policy(level_usdc=level_usdc, debounce_ms=100, fallback_timer_s=1.0)
    limits = MarketLimits(
        min_order_size=5.0, tick_size=0.01, pair_sum_tolerance=0.02, radiant_token_index=0
    )
    freshness = FreshnessLimits(book_stale_s=2.0, entry_stale_s=3.0, exit_stale_s=4.0)
    return {
        "kind": "header",
        "seq": 0,
        "schema_version": CORE_TRACE_SCHEMA_VERSION,
        "session_id": "0xcond",
        "match_id": "1",
        "game": game,
        "execution_mode": "live",
        "git_commit": "unknown",
        "policy": jsonable(policy),
        "policy_sha": policy_sha(policy, limits, freshness),
        "limits": jsonable(limits),
        "freshness": jsonable(freshness),
        "model": {"name": "tape", "trained_at": "test", "sha256": "0" * 64},
        "yes_token": "yes",
        "no_token": "no",
        "opened_wall_s": opened_wall_s,
        "opened_now_ns": 0,
    }


def _write_archive(root: Path, name: str, *, slug: str | None = None) -> Path:
    path = root / name
    path.mkdir(parents=True)
    market: dict[str, Any] = {}
    if slug is not None:
        market["market_slug"] = slug
    (path / "match.json").write_text(
        json.dumps(
            {
                "steam_match_id": None,
                "match_id": name,
                "market": market,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_resolve_via_market_slug_when_steam_null(tmp_path: Path) -> None:
    """A catalog slug links an archive whose match.json lacks steam_match_id."""
    archive = _write_archive(tmp_path, "grid-null-m1", slug="dota2-navi-pi-2026-09-13-game2")
    mid = resolve_archive_match_id(
        archive,
        slug_to_match_id={"dota2-navi-pi-2026-09-13-game2": 8996676912},
        condition_to_match_id={},
    )
    assert mid == 8996676912


def test_build_dota_live_indexes_fills_slug_and_condition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both indexes must be non-empty; the old getattr version silently returned two empty dicts."""
    catalog_path = tmp_path / "match_catalog.parquet"
    rows = [
        catalog_row(1, condition_id="0xcond1", market_slug="dota2-a-game1"),
        catalog_row(2, condition_id="0xcond2", market_slug="dota2-b-game1"),
    ]
    pd.DataFrame(rows).to_parquet(catalog_path)
    monkeypatch.setattr(live_archives, "MATCH_CATALOG_PATH", catalog_path)
    indexes = live_archives.build_dota_live_indexes()
    assert indexes.slug_to_match_id == {"dota2-a-game1": 1, "dota2-b-game1": 2}
    assert indexes.condition_to_match_id == {"0xcond1": 1, "0xcond2": 2}


def test_match_kernel_config_survives_msgspec() -> None:
    """The kernel config reaches the Nautilus strategy through JSON without losing a field."""
    header = decode_header_row(build_header_row(game="dota", level_usdc=42.0))
    kernel = MatchKernelConfig(
        policy=header.policy,
        limits=header.limits,
        freshness=header.freshness,
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
    )
    config = DotaMakerConfig(
        match_id=1,
        instrument_ids=(
            InstrumentId.from_str("yes.POLYMARKET"),
            InstrumentId.from_str("no.POLYMARKET"),
        ),
        feed_timestamps_ns=(1,),
        signal_timestamps_ns=(1,),
        source_timestamps_ns=(1,),
        predicted_deltas=(0.03,),
        dataset_market_ps=(0.5,),
        deaths_radiant=(0,),
        deaths_dire=(0,),
        kill_gates=(),
        board_tick_ns=(1,),
        observed_clock=None,
        horn_ns=0,
        buy_cutoff_ns=1,
        game_end_ns=2,
        order_latency_ns=0,
        cancel_latency_ns=0,
        kernel=kernel,
    )
    decoded = DotaMakerConfig.parse(config.json())
    assert decoded.kernel.policy == header.policy
    assert decoded.kernel.limits == header.limits
    assert decoded.kernel.freshness == header.freshness
    assert decoded.board_tick_ns == (1,)
