"""Per-second model signal contract for the maker backtest.

grid-v1 samples synthetic GRID ticks. Availability gates (as-of, signal age,
pair tolerance, anchor) stay on the sampled feed.
"""

# pyright: reportMissingTypeStubs=false

import hashlib
import itertools
import math
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from archive_index.schedule import (
    MODEL_WINDOWS,
    SNAPSHOT_FEATURE_COLUMNS,
)
from backtest.feed_schedules import (
    MatchFeedPlan,
    SchedulePlan,
    entry_stale_seconds,
)
from backtest.marks import MidSeries
from backtest.signal_replay import ReceivedSnapshot, SignalReplay, replay_received_snapshots
from shared.constants.lol import (
    LOL_GAME_FEATURES_PATH,
    LOL_SOURCE_LAG_SECONDS,
    LOL_VALIDATION_PATH,
)
from shared.constants.paths import GAME_FEATURES_DATASET_PATH, VALIDATION_DATASET_PATH
from shared.constants.strategy import (
    GRID_FEED_STALE_SECONDS,
    KILL_GATE_HOLD_S,
    KILL_GATE_MAX_BOARD_AGE_S,
)
from shared.types.opendota import RadiantTokenIndex
from shared.utils.board_features import board_model_values
from shared.utils.dota_features import (
    HistoryPolicy,
    history_policy_for_columns,
    market_derived_columns,
)
from shared.utils.gbm import load_predictor
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.model_registry import read_model_meta
from shared.utils.telonex_book import NS_PER_US, PAIR_SUM_TOLERANCE, normalize_pair_mids
from strategy.types import KillGate, KillGateUpdate, KillWait
from train_model.train_model import select_usable_validation_rows

GRID_FEED_MAX_AGE_SECONDS = GRID_FEED_STALE_SECONDS
ANCHOR_TOLERANCE = 0.01
GRID_V1_PROFILE = "grid-v1"
BacktestGame = Literal["dota", "lol"]
# Median game second of a GRID archive's first feed tick, measured on the
# recorded schedules (2026-10 investigation). Synthetic grid-v1 feeds start
# there, matching what the archives show live GRID sends first.
GRID_V1_FIRST_TICK_SECOND: dict[BacktestGame, int] = {"dota": 48, "lol": 76}


@dataclass(frozen=True)
class CadenceBand:
    """Inclusive start of a game-second band and its mean GRID interval."""

    start_second: int
    mean_interval_seconds: int


@dataclass(frozen=True)
class SignalTiming:
    """RNG seed, feed-timeout age, and optional fixed mean interval for one backtest run."""

    seed: int
    max_age_seconds: float
    mean_interval_seconds: int | None = None

    def __post_init__(self) -> None:
        """Reject a negative seed, a non-finite / non-positive age, or a non-positive interval."""
        if self.seed < 0:
            raise ValueError("signal cadence seed must be >= 0")
        if not math.isfinite(self.max_age_seconds) or self.max_age_seconds <= 0:
            raise ValueError("max signal age must be a finite positive number")
        if self.mean_interval_seconds is not None and self.mean_interval_seconds < 1:
            raise ValueError("mean interval must be >= 1")


LIVE_GRID_TIMING = SignalTiming(seed=0, max_age_seconds=GRID_FEED_MAX_AGE_SECONDS)
DOTA_GRID_V1_BANDS = (
    CadenceBand(start_second=0, mean_interval_seconds=11),
    CadenceBand(start_second=180, mean_interval_seconds=8),
    CadenceBand(start_second=360, mean_interval_seconds=7),
    CadenceBand(start_second=540, mean_interval_seconds=6),
)
LOL_GRID_V1_BANDS = (
    CadenceBand(start_second=0, mean_interval_seconds=8),
    CadenceBand(start_second=180, mean_interval_seconds=6),
    CadenceBand(start_second=360, mean_interval_seconds=5),
    CadenceBand(start_second=540, mean_interval_seconds=5),
)

# Scoreboard receipt minus occurredAt for a kill-bearing board frame, 21
# quantiles p0…p100 step 5%, measured 2026-09-24 on all GRID archives.
SCOREBOARD_LAG_QUANTILES: dict[BacktestGame, tuple[tuple[float, ...], tuple[float, ...]]] = {
    "lol": (
        tuple(q / 100 for q in range(0, 101, 5)),
        (
            0.95,
            1.86,
            2.05,
            2.17,
            2.27,
            2.36,
            2.44,
            2.52,
            2.6,
            2.67,
            2.74,
            2.82,
            2.9,
            2.99,
            3.09,
            3.21,
            3.39,
            3.67,
            9.07,
            24.71,
            200.07,
        ),
    ),
    "dota": (
        tuple(q / 100 for q in range(0, 101, 5)),
        (
            0.61,
            1.26,
            1.27,
            1.28,
            1.29,
            1.3,
            1.31,
            1.32,
            1.33,
            1.34,
            1.35,
            1.36,
            1.37,
            1.39,
            1.41,
            1.44,
            1.5,
            1.69,
            9.15,
            23.82,
            217.15,
        ),
    ),
}


@dataclass(frozen=True)
class BoardKill:
    """One fresh scoreboard kill marker: board receipt and board-implied deaths."""

    board_ns: int
    deaths_radiant: int
    deaths_dire: int
    grew_radiant: bool
    grew_dire: bool


@dataclass(frozen=True)
class MatchSignals:
    """Feed ticks plus model decisions: deltas/prices/deaths exist only on decisions."""

    feed_timestamps_ns: tuple[int, ...]
    timestamps_ns: tuple[int, ...]
    source_timestamps_ns: tuple[int, ...]
    predicted_deltas: tuple[float, ...]
    dataset_market_ps: tuple[float, ...]
    deaths_radiant: tuple[int, ...]
    deaths_dire: tuple[int, ...]
    kill_gates: tuple[KillGateUpdate, ...]
    board_tick_ns: tuple[int, ...]


class DatasetReadinessError(ValueError):
    """Archive tick has no dataset feature row at its game second."""


@dataclass(frozen=True)
class SignalSample:
    """One as-of signal row the strategy may use at the current clock."""

    predicted_delta: float
    dataset_market_p: float


def load_usable_signal_rows(path: Path | None = None) -> pd.DataFrame:
    """Read validation rows that can feed model prediction through map duration."""
    frame = pd.read_parquet(VALIDATION_DATASET_PATH if path is None else path)
    return select_usable_validation_rows(frame)


def require_catalog_features(model_dir: Path, expected: Sequence[str], flag: str) -> None:
    """Require the exact canonical feature order; legacy catalogs are rejected."""
    if load_model_feature_columns(model_dir) != tuple(expected):
        raise ValueError(f"{flag} is not the {len(expected)}-column catalog: {model_dir}")


@cache
def load_model_feature_columns(model_dir: Path) -> tuple[str, ...]:
    """Read the training feature list from the catalog's model.json."""
    meta_path = model_dir / "model.json"
    features = read_model_meta(meta_path)["features"]
    if not features:
        raise ValueError(f"model.json features empty: {meta_path}")
    return tuple(features)


@cache
def history_policy_for_model_dir(model_dir: Path) -> HistoryPolicy:
    """The catalog's tape policy for <model_dir>, resolved once per dir."""
    return history_policy_for_columns(load_model_feature_columns(model_dir))


def assert_expected_feature_names(feature_names: list[str], expected: Sequence[str]) -> None:
    """Reject a model whose feature names do not match training order."""
    expected_list = list(expected)
    if feature_names != expected_list:
        raise ValueError(f"model feature names {feature_names!r} != expected {expected_list!r}")


def predict_model_deltas(
    model_dir: Path,
    feature_columns: Sequence[str],
    features: pd.DataFrame,
) -> list[float]:
    """Predict every row with the catalog predictor (mean of members)."""
    predictor = load_predictor(model_dir)
    assert_expected_feature_names(list(predictor.feature_names), feature_columns)
    predicted = predictor.predict(features)
    return [float(value) for value in predicted]


def mean_interval_seconds(feature_second: int, bands: tuple[CadenceBand, ...]) -> int:
    """Mean GRID interval for a feature second from a rising band ladder."""
    chosen = bands[0]
    for band in bands:
        if feature_second < band.start_second:
            break
        chosen = band
    return chosen.mean_interval_seconds


def grid_v1_unit_interval(*, seed: int, game: BacktestGame, match_id: int, second: int) -> float:
    """Stable u in [0, 1) for one grid-v1 (seed, game, match, second)."""
    payload = f"{GRID_V1_PROFILE}:{seed}:{game}:{match_id}:{second}".encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def grid_v1_scoreboard_delay_seconds(
    *, seed: int, game: BacktestGame, match_id: int, second: int
) -> float:
    """Draw one scoreboard lag for a kill from the per-game quantile table.

    ponytail: delays draw independently per kill; real GRID board stalls run in
    series inside a map, so consecutive-kill stalls are underrepresented.
    """
    payload = f"{GRID_V1_PROFILE}:{seed}:{game}:{match_id}:{second}:scoreboard".encode()
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    unit = int.from_bytes(digest, "big") / 2**64
    quantiles, values = SCOREBOARD_LAG_QUANTILES[game]
    return float(np.interp(unit, quantiles, values))


def cadence_bands_for_game(game: BacktestGame) -> tuple[CadenceBand, ...]:
    """Versioned grid-v1 bands for Dota or LoL."""
    if game == "dota":
        return DOTA_GRID_V1_BANDS
    return LOL_GRID_V1_BANDS


def select_cadence_rows(
    rows: pd.DataFrame,
    *,
    game: BacktestGame,
    timing: SignalTiming,
    lag_seconds: int,
) -> pd.DataFrame:
    """Sample independent grid-v1 ticks per second, or every second when interval is 1.

    A row where either side's deaths grew versus the previous map row is always
    kept: live GRID pushes the kill-bearing table update on the change, so
    cadence sampling must not hide it behind the mean interval.
    """
    if timing.mean_interval_seconds == 1:
        return rows.sort_values(["match_id", "second"], ignore_index=True)
    bands = cadence_bands_for_game(game)
    keep_indices: list[int] = []
    for _match_key, group in rows.groupby("match_id", sort=True):
        ordered = group.sort_values("second")
        match_id = int(ordered["match_id"].to_numpy(dtype=np.int64)[0])
        first = True
        seconds = ordered["second"].to_numpy(dtype=np.int64)
        deaths_radiant = ordered["deaths_radiant"].to_numpy(dtype=np.int64)
        deaths_dire = ordered["deaths_dire"].to_numpy(dtype=np.int64)
        prev_radiant = 0
        prev_dire = 0
        for idx, second, radiant, dire in zip(
            ordered.index, seconds, deaths_radiant, deaths_dire, strict=True
        ):
            deaths_grew = int(radiant) > prev_radiant or int(dire) > prev_dire
            prev_radiant, prev_dire = int(radiant), int(dire)
            if first:
                keep_indices.append(int(idx))
                first = False
                continue
            if not deaths_grew:
                feature_second = int(second) - lag_seconds
                interval = (
                    timing.mean_interval_seconds
                    if timing.mean_interval_seconds is not None
                    else mean_interval_seconds(feature_second, bands)
                )
                unit = grid_v1_unit_interval(
                    seed=timing.seed, game=game, match_id=int(match_id), second=int(second)
                )
                if unit >= 1.0 / float(interval):
                    continue
            keep_indices.append(int(idx))
    if not keep_indices:
        return rows.iloc[0:0]
    return rows.loc[keep_indices].sort_values(["match_id", "second"], ignore_index=True)


def _grid_v1_board_kills(
    rows: pd.DataFrame,
    *,
    game: BacktestGame,
    timing: SignalTiming,
    lag_seconds: int,
) -> dict[int, list[BoardKill]]:
    """Scoreboard kill markers from every usable signal row, before cadence.

    A row where a side's deaths grew versus the previous map row is a kill at
    the feature second's market time (`state_ts_us - lag_seconds`; Dota rows
    stamp the market second while their deaths come from `second - lag`). The
    board frame lands one drawn quantile lag later; a lag at the freshness
    cutoff or beyond is no marker at all, matching the live reducer.
    """
    kills: dict[int, list[BoardKill]] = {}
    lag_ns = lag_seconds * NS_PER_SECOND
    for _match_key, group in rows.groupby("match_id", sort=True):
        ordered = group.sort_values("second")
        match_id = int(ordered["match_id"].to_numpy(dtype=np.int64)[0])
        prev_radiant: int | None = None
        prev_dire = 0
        for state_ts_us, second, radiant, dire in zip(
            ordered["state_ts_us"].to_numpy(dtype=np.int64),
            ordered["second"].to_numpy(dtype=np.int64),
            ordered["deaths_radiant"].to_numpy(dtype=np.int64),
            ordered["deaths_dire"].to_numpy(dtype=np.int64),
            strict=True,
        ):
            if prev_radiant is None:
                prev_radiant, prev_dire = int(radiant), int(dire)
                continue
            grew_radiant = int(radiant) > prev_radiant
            grew_dire = int(dire) > prev_dire
            prev_radiant, prev_dire = int(radiant), int(dire)
            if not (grew_radiant or grew_dire):
                continue
            delay_s = grid_v1_scoreboard_delay_seconds(
                seed=timing.seed, game=game, match_id=match_id, second=int(second)
            )
            if delay_s >= KILL_GATE_MAX_BOARD_AGE_S:
                continue
            board_ns = int(state_ts_us) * NS_PER_US - lag_ns + round(delay_s * NS_PER_SECOND)
            kills.setdefault(match_id, []).append(
                BoardKill(
                    board_ns=board_ns,
                    deaths_radiant=int(radiant),
                    deaths_dire=int(dire),
                    grew_radiant=grew_radiant,
                    grew_dire=grew_dire,
                )
            )
    for match_kills in kills.values():
        match_kills.sort(key=lambda kill: kill.board_ns)
    return kills


def _grid_v1_kill_gates(
    kills_by_match: Mapping[int, Sequence[BoardKill]],
) -> dict[int, list[KillGateUpdate]]:
    """KillGateUpdate tuples from the same board markers the board ticks use."""
    hold_ns = round(KILL_GATE_HOLD_S * NS_PER_SECOND)
    return {
        match_id: [
            KillGateUpdate(
                now_ns=kill.board_ns,
                gate=KillGate(
                    radiant=KillWait(
                        awaited_deaths=kill.deaths_radiant,
                        until_ns=kill.board_ns + (hold_ns if kill.grew_radiant else 0),
                    ),
                    dire=KillWait(
                        awaited_deaths=kill.deaths_dire,
                        until_ns=kill.board_ns + (hold_ns if kill.grew_dire else 0),
                    ),
                ),
            )
            for kill in kills
        ]
        for match_id, kills in kills_by_match.items()
    }


def _require_constant_prior(values: np.ndarray, match_id: int) -> float:
    """The match's single market_radiant_prior, or an error when the rows disagree."""
    prior = float(values[0])
    if not bool(np.all(values == prior)):
        raise ValueError(f"match {match_id}: market_radiant_prior is not constant")
    return prior


def _assemble_match_signals(
    replays: Mapping[int, SignalReplay],
    gates: Mapping[int, Sequence[KillGateUpdate]],
) -> dict[int, MatchSignals]:
    decisions = [decision for replay in replays.values() for decision in replay.decisions]
    rows: list[dict[str, float]] = []
    for decision in decisions:
        row = dict(decision.snapshot.levels)
        row["market_radiant_prior"] = decision.prior
        row.update(decision.history)
        row.update(board_model_values(decision.board))
        row["second"] = decision.snapshot.second
        row["market_p_radiant"] = decision.anchor_p
        rows.append(row)
    decision_rows = pd.DataFrame(rows)
    deltas = [0.0] * len(decisions)
    if decisions:
        decision_rows = decision_rows.assign(**market_derived_columns(decision_rows))
        for model_dir in sorted({decision.model_dir for decision in decisions}, key=str):
            positions = [
                i for i, decision in enumerate(decisions) if decision.model_dir == model_dir
            ]
            columns = load_model_feature_columns(model_dir)
            predictions = predict_model_deltas(
                model_dir, columns, decision_rows.iloc[positions][list(columns)]
            )
            for index, delta in zip(positions, predictions, strict=True):
                deltas[index] = delta
    predicted_by_match: dict[int, list[float]] = {match_id: [] for match_id in replays}
    for decision, delta in zip(decisions, deltas, strict=True):
        predicted_by_match[decision.match_id].append(delta)
    return {
        match_id: MatchSignals(
            feed_timestamps_ns=replay.feed_timestamps_ns,
            timestamps_ns=tuple(decision.received_ns for decision in replay.decisions),
            source_timestamps_ns=tuple(
                decision.snapshot.received_ns for decision in replay.decisions
            ),
            predicted_deltas=tuple(predicted_by_match[match_id]),
            dataset_market_ps=tuple(decision.anchor_p for decision in replay.decisions),
            deaths_radiant=tuple(
                int(decision.snapshot.levels["deaths_radiant"]) for decision in replay.decisions
            ),
            deaths_dire=tuple(
                int(decision.snapshot.levels["deaths_dire"]) for decision in replay.decisions
            ),
            kill_gates=tuple(gates.get(match_id, ())),
            board_tick_ns=tuple(
                decision.received_ns for decision in replay.decisions if decision.board_tick
            ),
        )
        for match_id, replay in replays.items()
    }


def build_match_signals(
    match_ids: Sequence[int],
    signal_rows: pd.DataFrame,
    model_dir: Path,
    lag_seconds: int,
    game: BacktestGame,
    timing: SignalTiming,
    mids: Mapping[int, MidSeries],
) -> dict[int, MatchSignals]:
    """Sample table cadence, then replay separate board events against received snapshots."""
    selected = signal_rows[signal_rows["match_id"].isin(match_ids)]
    rows = selected.sort_values(["match_id", "second"], ignore_index=True)
    if bool(rows.duplicated(["match_id", "second"]).any()):
        raise ValueError("validation rows must have unique seconds per match")
    present = {int(match_id) for match_id in selected["match_id"]}
    missing = sorted(set(match_ids) - present)
    if missing:
        raise ValueError(f"validation dataset has no usable signal rows for matches {missing}")
    rows = rows[rows["second"] - lag_seconds >= GRID_V1_FIRST_TICK_SECOND[game]]
    kills = _grid_v1_board_kills(rows, game=game, timing=timing, lag_seconds=lag_seconds)
    gates = _grid_v1_kill_gates(kills)
    feed_rows = select_cadence_rows(rows, game=game, timing=timing, lag_seconds=lag_seconds)
    lag_ns = LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND if game == "lol" else 0
    feed_rows = feed_rows.reset_index(drop=True)
    history_policy = history_policy_for_model_dir(model_dir)
    replays = {match_id: SignalReplay((), ()) for match_id in present}
    for _match_key, part in feed_rows.groupby("match_id", sort=True):
        match_id = int(part["match_id"].to_numpy(dtype=np.int64)[0])
        if match_id not in mids:
            raise ValueError(f"match {match_id}: missing market-seconds series")
        series = mids[match_id]
        state_ts_us = part["state_ts_us"].to_numpy(dtype=np.int64)
        seconds = part["second"].to_numpy(dtype=np.int64)
        level_columns = {
            name: part[name].to_numpy(dtype=np.float64) for name in SNAPSHOT_FEATURE_COLUMNS
        }
        prior = _require_constant_prior(
            part["market_radiant_prior"].to_numpy(dtype=np.float64), match_id
        )
        snapshots = [
            ReceivedSnapshot(
                received_ns=int(state_ts_us[index]) * NS_PER_US + lag_ns,
                second=int(seconds[index]) - lag_seconds,
                has_features=True,
                levels={
                    name: float(level_columns[name][index]) for name in SNAPSHOT_FEATURE_COLUMNS
                },
                terminal=False,
                paused=False,
            )
            for index in range(len(part))
        ]
        replays[match_id] = replay_received_snapshots(
            match_id,
            model_dir,
            snapshots,
            gates.get(match_id, ()),
            series,
            history_policy,
            timing.max_age_seconds,
            prior,
        )
    return _assemble_match_signals(replays, gates)


def build_schedule_match_signals(
    plans: Mapping[int, SchedulePlan],
    feature_rows: pd.DataFrame,
    mids: Mapping[int, MidSeries],
) -> dict[int, MatchSignals]:
    """Replay admitted snapshots and GRID boards under the same received-state contract."""
    groups = feature_rows.groupby("match_id", sort=False).indices
    replays: dict[int, SignalReplay] = {}
    gates: dict[int, tuple[KillGateUpdate, ...]] = {}
    for match_id, plan in plans.items():
        schedule = plan.binding.schedule
        ticks = schedule.ticks
        if any(
            later < earlier
            for earlier, later in itertools.pairwise(tick.received_ns for tick in ticks)
        ):
            raise ValueError(f"match {match_id}: schedule received_ns is not non-decreasing")
        positions = groups.get(match_id, ())
        by_second: dict[int, int] = {}
        for position in positions:
            second = int(feature_rows.iloc[position]["game_second"])
            if second in by_second:
                raise ValueError(f"match {match_id}: duplicate feature row at game_second {second}")
            by_second[second] = int(position)
        if not by_second:
            raise DatasetReadinessError(f"match {match_id}: no feature rows for schedule")
        prior = _require_constant_prior(
            feature_rows.iloc[list(positions)]["market_radiant_prior"].to_numpy(dtype=np.float64),
            match_id,
        )
        window_start = MODEL_WINDOWS[schedule.identity.game].start
        # Live drops PRE_MATCH, so a stuck Oddin draft clock never reaches the
        # model. Schedule replay ignores phase and admits game_second >= window_start
        # when that second has a feature row. A frozen clock inside the window
        # still decides.
        snapshots = [
            ReceivedSnapshot(
                received_ns=tick.received_ns,
                second=tick.game_second,
                has_features=tick.game_second >= window_start and tick.game_second in by_second,
                levels={name: float(getattr(tick, name)) for name in SNAPSHOT_FEATURE_COLUMNS},
                terminal=tick.terminal,
                paused=tick.paused,
            )
            for tick in ticks
        ]
        gates[match_id] = tuple(
            KillGateUpdate(
                now_ns=gate.received_ns,
                gate=KillGate(
                    radiant=KillWait(gate.radiant.awaited_deaths, gate.radiant.until_ns),
                    dire=KillWait(gate.dire.awaited_deaths, gate.dire.until_ns),
                ),
            )
            for gate in schedule.kill_gates
        )
        replays[match_id] = replay_received_snapshots(
            match_id,
            plan.model_dir,
            snapshots,
            gates[match_id] if plan.binding.feed_source == "grid" else (),
            mids[match_id],
            history_policy_for_model_dir(plan.model_dir),
            entry_stale_seconds(plan.binding),
            prior,
        )
    return _assemble_match_signals(replays, gates)


def resolve_game_features_path(game: BacktestGame) -> Path:
    """Game-second feature parquet a schedule replay predicts from."""
    if game == "lol":
        return LOL_GAME_FEATURES_PATH
    return GAME_FEATURES_DATASET_PATH


def resolve_plan_dataset_path(game: BacktestGame, plan: MatchFeedPlan) -> Path:
    """Parquet `resolve_run_signals` reads: game features on a schedule, validation rows on grid-v1."""
    if isinstance(plan, SchedulePlan):
        return resolve_game_features_path(game)
    if game == "lol":
        return LOL_VALIDATION_PATH
    return VALIDATION_DATASET_PATH


def load_game_feature_rows(game: BacktestGame, schedule_ids: Sequence[int]) -> pd.DataFrame:
    """Exact game-second feature rows for the schedule matches of this game."""
    path = resolve_game_features_path(game)
    make = "lol-prepare" if game == "lol" else "prepare"
    if not path.is_file():
        raise ValueError(f"{path} missing; run make {make}")
    frame = pd.read_parquet(path)
    return frame[frame["match_id"].isin(list(schedule_ids))]


def is_quote_fresh(now_ns: int, quote_ts_ns: int, max_age_seconds: float) -> bool:
    """True when the quote timestamp is at or before now and within the age budget."""
    if quote_ts_ns > now_ns:
        return False
    age_seconds = (now_ns - quote_ts_ns) / 1_000_000_000
    return age_seconds <= max_age_seconds


def find_signal_asof(
    signals: MatchSignals, now_ns: int, max_age_seconds: float
) -> SignalSample | None:
    """Latest model decision at or before now, or None when missing or older than the age budget."""
    index = bisect_right(signals.timestamps_ns, now_ns) - 1
    if index < 0:
        return None
    source_ns = signals.source_timestamps_ns[index]
    if not is_quote_fresh(now_ns, source_ns, max_age_seconds):
        return None
    return SignalSample(
        predicted_delta=signals.predicted_deltas[index],
        dataset_market_p=signals.dataset_market_ps[index],
    )


def calculate_book_p_radiant(radiant_mid: float, dire_mid: float) -> float | None:
    """Normalize live paired mids into market_p_radiant, or None when the pair breaks tolerance."""
    return normalize_pair_mids(
        radiant_mid=radiant_mid, dire_mid=dire_mid, tolerance=PAIR_SUM_TOLERANCE
    )


def passes_anchor_gate(book_p_radiant: float, dataset_market_p: float) -> bool:
    """True when the live book mid is within the dataset market price tolerance."""
    # 1e-12 absorbs binary float noise so a true 0.01 drift still passes.
    return abs(book_p_radiant - dataset_market_p) <= ANCHOR_TOLERANCE + 1e-12


def calculate_fair_radiant(book_p_radiant: float, predicted_delta: float) -> float:
    """Fair Radiant probability: live book mid plus the model delta, clipped to [0, 1]."""
    return min(1.0, max(0.0, book_p_radiant + predicted_delta))


def calculate_token_fair(
    fair_radiant: float, token_index: int, radiant_token_index: RadiantTokenIndex
) -> float:
    """Map Radiant fair to the instrument at token_index given which leg is Radiant."""
    fair_dire = 1.0 - fair_radiant
    if token_index == radiant_token_index:
        return fair_radiant
    return fair_dire
