"""Load LoL prepare artifacts into the shared maker backtest types."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.context import (
    MarketContext,
    ReplayLookups,
    ReplayWindow,
    calculate_clock_end,
)
from backtest.marks import MidSeries
from backtest.paths import LOL_MAKER_BACKTESTS_DIR
from backtest.selection import ValidationCoverage
from backtest.telonex_local import book_rows_in_window
from shared.constants.lol import (
    LOL_BACKTEST_AUDIT_PATH,
    LOL_BACKTEST_MARKET_SECONDS_PATH,
    LOL_RAW_GAMMA_DIR,
    LOL_RAW_TELONEX_DIR,
    LOL_REPLAY_LEAD,
    LOL_SOURCE_LAG_SECONDS,
    LOL_VALIDATION_PATH,
    REASON_MISSING_REQUIRED_CHANNEL,
    REASON_ZERO_SIGNAL_ROWS,
)
from shared.types.opendota import OpenDotaPause, RadiantTokenIndex
from shared.utils.model_registry import read_model_meta
from shared.utils.polymarket import GammaMarket, index_gamma_markets
from shared.utils.telonex_book import NS_PER_US, US_PER_SECOND

# ponytail: Nautilus 1.226.0 (and 1.x through 1.231) emits a 0-qty trade fill after a
# closed NETTING position, then asserts in PositionOpened (FLAT). Seed- or
# model-dependent. Bulk validation records these as terminated_early instead of
# running the engine. --match-id still replays them. If another run dies the same
# way, add the map id here. Drop the set when the pin is Nautilus 2.0+.
LOL_GEN_DK_2026_07_18 = 116855104460702379
LOL_EDG_TES_2026_08_05_GAME1 = 116566854547769589
LOL_DNF_FOX1_2026_07_30_GAME2 = 115548147900684659
LOL_MKOI_VIT_2026_07_26_GAME1 = 115548681803406192
LOL_SK_FNC_2026_08_15_GAME1 = 115548681803406256
DOTA_HULIGA_LYNX_2026_08_29_GAME1 = 8972289868
DOTA_AUR1_LIQUID_2026_07_11_GAME1 = 8891371442
DOTA_TY_LGD_2026_06_07_GAME3 = 8842722527
LOL_SLY_UCAM1_2026_06_14_GAME2 = 116634566264113554
LOL_ROSS_USE1_2026_07_17 = 116713163972559031
NAUTILUS_ZERO_FILL_SKIP_IDS = frozenset(
    {
        LOL_GEN_DK_2026_07_18,
        LOL_EDG_TES_2026_08_05_GAME1,
        LOL_DNF_FOX1_2026_07_30_GAME2,
        LOL_MKOI_VIT_2026_07_26_GAME1,
        LOL_SK_FNC_2026_08_15_GAME1,
        DOTA_HULIGA_LYNX_2026_08_29_GAME1,
        DOTA_AUR1_LIQUID_2026_07_11_GAME1,
        DOTA_TY_LGD_2026_06_07_GAME3,
        LOL_SLY_UCAM1_2026_06_14_GAME2,
        LOL_ROSS_USE1_2026_07_17,
    }
)
NAUTILUS_ZERO_FILL_STOP_REASON = "nautilus_zero_fill"
# A cadence/schedule pass can leave a map with zero priced decision ticks; bulk
# runs record it as terminated_early instead of letting the strategy guard kill
# the shard. --match-id still raises from on_start.
EMPTY_SIGNAL_TAPE_STOP_REASON = "empty_signal_tape"
NO_REPLAY_STOP_REASONS = frozenset({NAUTILUS_ZERO_FILL_STOP_REASON, EMPTY_SIGNAL_TAPE_STOP_REASON})


@dataclass(frozen=True)
class LolSelection:
    """LoL ids, coverage, prepare frames, and Gamma pages for one run."""

    selected_ids: tuple[int, ...]
    coverage: ValidationCoverage | None
    capture_root: Path
    report_root: Path
    signal_rows: pd.DataFrame
    audit: pd.DataFrame
    market_seconds: pd.DataFrame
    gamma_markets: dict[str, GammaMarket]


def assert_lol_source_lag(model_dir: Path, audit: pd.DataFrame) -> int:
    """Require model.json, backtest audit, and LOL_SOURCE_LAG_SECONDS to match."""
    model_lag = int(read_model_meta(model_dir / "model.json")["source_lag_seconds"])
    audit_lags = {int(value) for value in audit["source_lag_seconds"].tolist()}
    if len(audit_lags) != 1:
        raise ValueError(
            f"backtest audit source_lag_seconds is not a single value: {sorted(audit_lags)}"
        )
    audit_lag = next(iter(audit_lags))
    if model_lag != audit_lag:
        raise ValueError(f"model source_lag_seconds {model_lag} != backtest audit {audit_lag}")
    if model_lag != LOL_SOURCE_LAG_SECONDS:
        raise ValueError(
            f"source_lag_seconds {model_lag} != LOL_SOURCE_LAG_SECONDS {LOL_SOURCE_LAG_SECONDS}"
        )
    return model_lag


def _eligible_mask(audit: pd.DataFrame) -> "pd.Series[bool]":
    """Eligible audit rows that also carry at least one ok-quote market second."""
    return audit["eligible"].astype(bool) & (audit["ok_quote_fraction"].fillna(0) > 0)


def lol_validation_coverage(audit: pd.DataFrame) -> ValidationCoverage:
    """Map prepare backtest-audit reasons onto the Dota ValidationCoverage buckets."""
    eligible_mask = _eligible_mask(audit)
    ineligible = audit.loc[~eligible_mask, "reason"]
    without_signal_rows = int((ineligible == REASON_ZERO_SIGNAL_ROWS).sum())
    without_local_telonex = int((ineligible == REASON_MISSING_REQUIRED_CHANNEL).sum())
    return ValidationCoverage(
        validation_matches=len(audit),
        without_map_market=0,
        without_signal_rows=without_signal_rows,
        without_local_telonex=without_local_telonex,
        archive_excluded=0,
        eligible=int(eligible_mask.sum()),
    )


def select_lol_since_match_ids(
    audit: pd.DataFrame, signal_rows: pd.DataFrame, since_match_id: int
) -> tuple[int, ...]:
    """Whitelist-filtered eligible maps with horn >= the anchor map's horn.

    `audit` must already carry the league filter (load_lol_selection applies it
    before this runs). Horns come from signal_rows start_time, the same value
    that becomes the map's replay clock.
    """
    horns = signal_rows.groupby("match_id", sort=False)["start_time"].first()
    if since_match_id not in horns.index:
        raise ValueError(f"--since-match {since_match_id} has no LoL signal rows")
    anchor_horn = int(horns.loc[since_match_id])
    eligible = audit[_eligible_mask(audit)]["match_id"]
    cohort = [
        int(match_id)
        for match_id in eligible.tolist()
        if match_id in horns.index and int(horns.loc[match_id]) >= anchor_horn
    ]
    if not cohort:
        raise ValueError(f"no eligible matches at/after --since-match {since_match_id}")
    cohort.sort(key=lambda match_id: (int(horns.loc[match_id]), match_id))
    return tuple(cohort)


def synthetic_cutoff_pauses(
    horn_at: datetime, match_rows: pd.DataFrame, match_id: int, buy_cutoff_second: int
) -> list[OpenDotaPause]:
    """One OpenDota-shaped pause so buy_cutoff_ns lands on the pause-adjusted cut wall.

    Takes one match's rows: filtering the whole prepare frame per match rescans
    millions of rows once per map.
    """
    if match_rows.empty:
        raise ValueError(f"match {match_id}: missing market-seconds series")
    max_second = int(match_rows["second"].max())
    cut = min(buy_cutoff_second, max_second)
    cut_rows = match_rows[match_rows["second"] == cut]
    if cut_rows.empty:
        raise ValueError(f"match {match_id}: missing market second {cut}")
    state_ts_us = int(cut_rows["state_ts_us"].iloc[0])
    wall = datetime.fromtimestamp(state_ts_us / US_PER_SECOND, tz=UTC)
    extra = max(0, round((wall - horn_at).total_seconds()) - cut)
    if extra == 0:
        return []
    return [{"time": 0, "duration": extra}]


def load_lol_mid_series(
    match_ids: Sequence[int], market_seconds: pd.DataFrame
) -> dict[int, MidSeries]:
    """Filter LoL market seconds the same way Dota filters its per-match cache.

    Reads the three columns into numpy and sorts once by (match_id, state_ts_us).
    A boolean-mask copy of the whole prepare frame costs half a gigabyte per worker,
    and a filter per match rescans every row once per map.
    """
    usable = market_seconds["market_status"].to_numpy() == "ok"
    match_column = market_seconds["match_id"].to_numpy(dtype=np.int64)[usable]
    timestamps_us = market_seconds["state_ts_us"].to_numpy(dtype=np.int64)[usable]
    prices = market_seconds["market_p_radiant"].to_numpy(dtype=np.float64)[usable]
    order = np.lexsort((timestamps_us, match_column))
    match_column = match_column[order]
    timestamps_us = timestamps_us[order]
    prices = prices[order]

    series_by_match: dict[int, MidSeries] = {}
    for match_id in match_ids:
        start = int(np.searchsorted(match_column, match_id, side="left"))
        stop = int(np.searchsorted(match_column, match_id, side="right"))
        if start == stop:
            raise ValueError(f"match {match_id}: missing market-seconds series")
        series_by_match[match_id] = MidSeries(
            timestamps_ns=tuple(int(ts) * NS_PER_US for ts in timestamps_us[start:stop]),
            market_ps=tuple(float(price) for price in prices[start:stop]),
        )
    return series_by_match


def build_lol_market_context(
    audit_row: pd.Series,  # pyright: ignore[reportMissingTypeArgument]
    gamma_markets: Mapping[str, GammaMarket],
    horn_at: datetime,
) -> MarketContext:
    """Build one MarketContext from an eligible backtest audit row."""
    match_id = int(audit_row["match_id"])
    condition_id = str(audit_row["condition_id"])
    gamma = gamma_markets.get(condition_id)
    if gamma is None:
        raise ValueError(f"market {condition_id} is missing from the archived Gamma pages")
    game_ended_raw = audit_row["game_ended_at_ts"]
    replay_end_raw = audit_row["replay_end_ts"]
    if pd.isna(game_ended_raw) or pd.isna(replay_end_raw):
        raise ValueError(f"match {match_id}: eligible audit is missing replay timestamps")
    game_ended_at = datetime.fromtimestamp(int(game_ended_raw), tz=UTC)
    replay_end = datetime.fromtimestamp(int(replay_end_raw), tz=UTC)
    replay_start = horn_at - LOL_REPLAY_LEAD
    token_index = int(audit_row["radiant_token_index"])
    if token_index not in {0, 1}:
        raise ValueError(f"unsupported radiant token index {token_index}")
    radiant_token_index: RadiantTokenIndex = 0 if token_index == 0 else 1
    context = MarketContext(
        match_id=match_id,
        condition_id=condition_id,
        event_id=str(audit_row["event_id"]),
        market_slug=gamma.slug,
        token_ids=(str(audit_row["token_id_0"]), str(audit_row["token_id_1"])),
        radiant_token_index=radiant_token_index,
        radiant_win=bool(audit_row["radiant_win"]),
        seconds_delay=gamma.seconds_delay,
        horn_at=horn_at,
        game_ended_at=game_ended_at,
        market_closed_at=gamma.closed_at,
        replay_start=replay_start,
        replay_end=replay_end,
        clock_end=calculate_clock_end(
            game_ended_at=game_ended_at, market_closed_at=gamma.closed_at
        ),
    )
    return context


def load_lol_replay_lookups(
    match_ids: Sequence[int],
    audit: pd.DataFrame,
    market_seconds: pd.DataFrame,
    gamma_markets: Mapping[str, GammaMarket],
    buy_cutoff_second: int,
    signal_rows: pd.DataFrame,
) -> ReplayLookups:
    """Build pauses, contexts, and mids for the given LoL match ids."""
    audit_by_match = {int(row["match_id"]): row for _, row in audit.iterrows()}
    # One grouping pass each: filtering these frames per match rescans millions of
    # rows 1292 times on every worker start.
    horn_by_match = signal_rows.groupby("match_id", sort=False)["start_time"].first()
    market_positions = market_seconds.groupby("match_id", sort=False).indices
    pauses_by_match: dict[int, list[OpenDotaPause]] = {}
    context_by_match: dict[int, MarketContext] = {}
    for match_id in match_ids:
        audit_row = audit_by_match.get(match_id)
        if audit_row is None:
            raise ValueError(f"LoL match {match_id} is missing from backtest audit")
        if match_id not in horn_by_match.index:
            raise ValueError(f"LoL match {match_id} is missing from signal rows")
        horn_at = datetime.fromtimestamp(int(horn_by_match.loc[match_id]), tz=UTC)
        context = build_lol_market_context(audit_row, gamma_markets, horn_at=horn_at)
        context_by_match[match_id] = context
        positions = market_positions.get(match_id)
        match_rows = (
            market_seconds.take(list(positions))
            if positions is not None
            else market_seconds.iloc[:0]
        )
        pauses_by_match[match_id] = synthetic_cutoff_pauses(
            context.horn_at, match_rows, match_id, buy_cutoff_second
        )
    return ReplayLookups(
        pauses_by_match=pauses_by_match,
        context_by_match=context_by_match,
        mids=load_lol_mid_series(match_ids, market_seconds),
    )


def build_lol_book_weights(
    match_ids: Sequence[int], audit: pd.DataFrame, capture_root: Path
) -> dict[int, int]:
    """Book day-file rows per map: the shard weight approximating replay cost."""
    audit_by_match = {int(row["match_id"]): row for _, row in audit.iterrows()}
    weights: dict[int, int] = {}
    for match_id in match_ids:
        row = audit_by_match[match_id]
        window = ReplayWindow(
            start=datetime.fromtimestamp(int(row["replay_start_ts"]), tz=UTC),
            end=datetime.fromtimestamp(int(row["replay_end_ts"]), tz=UTC),
        )
        weights[match_id] = book_rows_in_window(
            (str(row["token_id_0"]), str(row["token_id_1"])), window, capture_root
        )
    return weights


def load_lol_selection(
    match_id: int | None,
    limit: int | None,
    model_dir: Path,
    allowed_event_ids: frozenset[str] | None,
) -> LolSelection:
    """Read LoL prepare artifacts locally and pick eligible maps. No network.

    The league filter runs first, so --limit, sharding, signals and --match-id all
    see the same reduced map population.
    """
    audit = pd.read_parquet(LOL_BACKTEST_AUDIT_PATH)
    assert_lol_source_lag(model_dir, audit)
    if allowed_event_ids is not None:
        audit = audit[audit["event_id"].astype(str).isin(allowed_event_ids)]
    signal_rows = pd.read_parquet(LOL_VALIDATION_PATH)
    market_seconds = pd.read_parquet(LOL_BACKTEST_MARKET_SECONDS_PATH)
    gamma_markets = index_gamma_markets(LOL_RAW_GAMMA_DIR / "events")
    signal_match_ids = frozenset(int(value) for value in signal_rows["match_id"].tolist())
    if match_id is not None:
        selected_id = int(match_id)
        rows = audit[audit["match_id"] == selected_id]
        eligible = bool(_eligible_mask(rows).any())
        if not eligible or selected_id not in signal_match_ids:
            raise ValueError(f"LoL match {selected_id} is not an eligible backtest card")
        selected_ids = (selected_id,)
        coverage = None
    else:
        eligible_audit = audit[_eligible_mask(audit)].sort_values(
            ["event_id", "match_id"], ignore_index=True
        )
        ordered = tuple(int(value) for value in eligible_audit["match_id"])
        selected_ids = ordered if limit is None else ordered[: int(limit)]
        coverage = lol_validation_coverage(audit)
    return LolSelection(
        selected_ids=selected_ids,
        coverage=coverage,
        capture_root=LOL_RAW_TELONEX_DIR,
        report_root=LOL_MAKER_BACKTESTS_DIR,
        signal_rows=signal_rows,
        audit=audit,
        market_seconds=market_seconds,
        gamma_markets=gamma_markets,
    )
