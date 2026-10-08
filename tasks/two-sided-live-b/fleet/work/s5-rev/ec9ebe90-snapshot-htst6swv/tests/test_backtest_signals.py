"""Unit tests for the maker signal contract: deltas, as-of lookup, and fair value."""

# pyright: reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false
# pyright: reportUnknownVariableType=false

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backtest.feed_schedules import grid_exit_age_seconds
from backtest.marks import MidSeries
from backtest.signals import (
    ANCHOR_TOLERANCE,
    GRID_FEED_MAX_AGE_SECONDS,
    LIVE_GRID_TIMING,
    BacktestGame,
    MatchSignals,
    SignalTiming,
    build_match_signals,
    calculate_book_p_radiant,
    calculate_fair_radiant,
    calculate_token_fair,
    find_signal_asof,
    grid_v1_scoreboard_delay_seconds,
    grid_v1_unit_interval,
    load_usable_signal_rows,
    passes_anchor_gate,
)
from shared.constants.dataset import BACKTEST_LAG_SECONDS
from shared.constants.lol import LOL_SOURCE_LAG_SECONDS
from shared.constants.strategy import KILL_GATE_HOLD_S, KILL_GATE_MAX_BOARD_AGE_S
from shared.utils.dota_features import (
    DOTA_XP_FEATURE_COLUMNS,
)
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.telonex_book import NS_PER_US, PAIR_SUM_TOLERANCE


def build_signal_frame(
    *,
    match_id: int = 1,
    seconds: tuple[int, ...] = (0, 1, 899),
    market_status: str = "ok",
    market_p: float | None = 0.55,
    state_ts_us_base: int = 1_000_000,
) -> pd.DataFrame:
    """Minimal validation-shaped rows for signal loading and prediction tests."""
    rows: list[dict[str, Any]] = []
    for second in seconds:
        rows.append(
            {
                "match_id": match_id,
                "second": second,
                "market_status": market_status,
                "market_p_radiant": market_p,
                "state_ts_us": state_ts_us_base + second * 1_000_000,
                "radiant_nw_adv": 0.0,
                "radiant_nw": 0.0,
                "dire_nw": 0.0,
                "radiant_xp_adv": 0.0,
                "deaths_radiant": 0,
                "deaths_dire": 0,
                "top1_nw_adv": 0,
                "radiant_top1_nw_ratio": 0.0,
                "dire_top1_nw_ratio": 0.0,
                "top3_nw_adv": 0,
                "radiant_top3_nw_ratio": 0.0,
                "dire_top3_nw_ratio": 0.0,
                "market_radiant_prior": 0.5,
            }
        )
    return pd.DataFrame(rows)


def _mids_from_rows(rows: pd.DataFrame, game: BacktestGame) -> dict[int, MidSeries]:
    """One mid at each row's received time, equal to that row's market_p_radiant."""
    lag_ns = LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND if game == "lol" else 0
    ordered = rows.sort_values(["match_id", "state_ts_us"])
    return {
        int(part["match_id"].to_numpy(dtype=np.int64)[0]): MidSeries(
            timestamps_ns=tuple(int(ts) * NS_PER_US + lag_ns for ts in part["state_ts_us"]),
            market_ps=tuple(float(price) for price in part["market_p_radiant"]),
        )
        for _match_key, part in ordered.groupby("match_id", sort=True)
    }


def predict_signals(
    match_ids: tuple[int, ...],
    rows: pd.DataFrame,
    model_dir: Path,
    *,
    lag_seconds: int = BACKTEST_LAG_SECONDS,
    game: BacktestGame = "dota",
    timing: SignalTiming = LIVE_GRID_TIMING,
    mids: dict[int, MidSeries] | None = None,
) -> dict[int, MatchSignals]:
    """build_match_signals with live grid-v1 Dota timing unless a test overrides it."""
    resolved_mids = _mids_from_rows(rows, game) if mids is None else mids
    return build_match_signals(match_ids, rows, model_dir, lag_seconds, game, timing, resolved_mids)


# The tests below install a fake booster, so the path is never opened.
FAKE_MODEL_DIR = Path("unused-model-dir")


@pytest.fixture
def model_dir(tmp_path: Path) -> Path:
    """Catalog directory with member_00.txt and model.json listing DOTA_XP_FEATURE_COLUMNS."""
    (tmp_path / "member_00.txt").write_text("unused")
    (tmp_path / "model.json").write_text(
        json.dumps(
            {
                "features": list(DOTA_XP_FEATURE_COLUMNS),
                "members": ["member_00.txt"],
                "member_trees": [1],
            }
        )
    )
    return tmp_path


def write_board_model_dir(path: Path) -> Path:
    """Catalog directory whose model.json lists the 81-column scoreboard catalog."""
    (path / "member_00.txt").write_text("unused")
    (path / "model.json").write_text(
        json.dumps(
            {
                "features": list(DOTA_XP_FEATURE_COLUMNS),
                "members": ["member_00.txt"],
                "member_trees": [1],
            }
        )
    )
    return path


class FakeBooster:
    """LightGBM stand-in that returns a fixed delta per row and records call kwargs."""

    def __init__(self, model_file: str, names: list[str] | None = None) -> None:
        self.model_file = model_file
        self.names = list(DOTA_XP_FEATURE_COLUMNS) if names is None else list(names)
        self.predict_calls: list[dict[str, Any]] = []

    def feature_name(self) -> list[str]:
        """Return the training feature order the real model must match."""
        return list(self.names)

    def num_trees(self) -> int:
        return 1

    def predict(self, features: pd.DataFrame, **kwargs: Any) -> list[float]:
        """Return one delta per row and remember how predict was called."""
        self.predict_calls.append(
            {
                "columns": list(features.columns),
                "kwargs": kwargs,
                "seconds": features["second"].tolist(),
                "deaths_radiant": features["deaths_radiant"].tolist(),
                "pending_deaths_radiant": (
                    features["pending_deaths_radiant"].tolist()
                    if "pending_deaths_radiant" in features.columns
                    else []
                ),
            }
        )
        return [0.0161] * len(features)


def install_fake_booster(monkeypatch: pytest.MonkeyPatch, fake: FakeBooster) -> None:
    """Point LightGBM Booster construction at a fixed fake instance."""

    def build_booster(model_file: str) -> FakeBooster:
        """Ignore the model path and return the shared fake booster."""
        _ = model_file
        return fake

    monkeypatch.setattr("shared.utils.gbm.lgb.Booster", build_booster)


def test_build_match_signals_keeps_raw_delta_without_sigmoid(
    monkeypatch: pytest.MonkeyPatch,
    model_dir: Path,
) -> None:
    """Model output stays a delta: no raw_score, and numbers reach MatchSignals unchanged."""
    fake = FakeBooster("unused")
    install_fake_booster(monkeypatch, fake)
    # The first feed tick only connects; 67 is seed 0's next cadence tick.
    rows = build_signal_frame(seconds=(58, 67))

    signals = predict_signals((1,), rows, model_dir)
    sample = signals[1]

    assert sample.predicted_deltas == (0.0161,)
    assert sample.dataset_market_ps == (0.55,)
    assert len(fake.predict_calls) == 1
    assert fake.predict_calls[0]["columns"] == DOTA_XP_FEATURE_COLUMNS
    assert fake.predict_calls[0]["kwargs"] == {}
    assert fake.predict_calls[0]["seconds"] == [67 - BACKTEST_LAG_SECONDS]


def test_predict_model_deltas_always_loads_listed_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A sibling bank.json cannot redirect canonical prediction to another booster."""
    seen: list[str] = []

    def build_booster(model_file: str) -> FakeBooster:
        """Record the one booster path LightGBM receives."""
        seen.append(model_file)
        return FakeBooster("unused")

    monkeypatch.setattr("shared.utils.gbm.lgb.Booster", build_booster)
    (tmp_path / "member_00.txt").write_text("unused")
    (tmp_path / "model.json").write_text(
        json.dumps(
            {
                "features": list(DOTA_XP_FEATURE_COLUMNS),
                "members": ["member_00.txt"],
                "member_trees": [1],
            }
        )
    )
    (tmp_path / "late.txt").write_text("late")
    (tmp_path / "bank.json").write_text(
        json.dumps({"routes": [{"max_second": 7200, "model": "late.txt"}]})
    )

    predict_signals((1,), build_signal_frame(seconds=(58, 67)), tmp_path)

    assert seen == [str(tmp_path / "member_00.txt")]


def test_signal_timestamp_is_state_ts_us_times_thousand(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """signal_ts_ns equals state_ts_us * NS_PER_US."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    state_ts_us_base = 1_780_563_592_000_000
    rows = build_signal_frame(seconds=(58, 67), state_ts_us_base=state_ts_us_base)

    signals = predict_signals((1,), rows, model_dir)

    assert signals[1].timestamps_ns == ((state_ts_us_base + 67 * 1_000_000) * NS_PER_US,)


def test_build_match_signals_raises_for_match_without_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested match with no usable rows is named in the error."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    rows = build_signal_frame(match_id=1, seconds=(58, 59))

    with pytest.raises(ValueError, match="matches \\[99\\]"):
        predict_signals((1, 99), rows, FAKE_MODEL_DIR)


def test_load_usable_signal_rows_drops_unusable_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stale and missing market rows drop; seconds through map duration stay."""
    frame = pd.concat(
        [
            build_signal_frame(seconds=(0, 899), market_status="ok"),
            build_signal_frame(seconds=(10,), market_status="stale_quote"),
            build_signal_frame(seconds=(11,), market_status="missing_quote"),
            build_signal_frame(seconds=(900,), market_status="ok"),
        ],
        ignore_index=True,
    )
    path = tmp_path / "validation_dataset.parquet"
    frame.to_parquet(path)
    monkeypatch.setattr("backtest.signals.VALIDATION_DATASET_PATH", path)

    usable = load_usable_signal_rows()

    assert sorted(usable["second"].tolist()) == [0, 899, 900]
    assert (usable["market_status"] == "ok").all()


def test_find_signal_asof_is_no_lookahead_and_stale_after_age_budget() -> None:
    """As-of returns the last row at or before now, and None past the age gate."""
    signals = MatchSignals(
        feed_timestamps_ns=(1_000_000_000, 2_000_000_000, 3_000_000_000),
        timestamps_ns=(1_000_000_000, 2_000_000_000, 3_000_000_000),
        source_timestamps_ns=(1_000_000_000, 2_000_000_000, 3_000_000_000),
        predicted_deltas=(0.01, 0.02, 0.03),
        dataset_market_ps=(0.40, 0.50, 0.60),
        deaths_radiant=(),
        deaths_dire=(),
        kill_gates=(),
        board_tick_ns=(),
    )
    age_budget_ns = int(1.0 * 1_000_000_000)

    assert find_signal_asof(signals, 999_999_999, 1.0) is None
    at_exact = find_signal_asof(signals, 2_000_000_000, 1.0)
    assert at_exact is not None
    assert at_exact.predicted_delta == 0.02
    assert at_exact.dataset_market_p == 0.50
    # Between 2s and 3s: as-of is still the 2s row (no lookahead to 3s).
    between = find_signal_asof(signals, 2_500_000_000, 1.0)
    assert between is not None
    assert between.predicted_delta == 0.02
    within_age = find_signal_asof(signals, 3_000_000_000 + age_budget_ns, 1.0)
    assert within_age is not None
    assert within_age.predicted_delta == 0.03
    stale = find_signal_asof(signals, 3_000_000_000 + age_budget_ns + 1, 1.0)
    assert stale is None


def test_asof_between_polls_reuses_last_delta_until_age_one() -> None:
    """Between 1s polls the last delta is reused until the 1s age budget."""
    poll_ns = NS_PER_SECOND
    signals = MatchSignals(
        feed_timestamps_ns=(poll_ns, 2 * poll_ns),
        timestamps_ns=(poll_ns, 2 * poll_ns),
        source_timestamps_ns=(poll_ns, 2 * poll_ns),
        predicted_deltas=(0.01, 0.02),
        dataset_market_ps=(0.40, 0.50),
        deaths_radiant=(),
        deaths_dire=(),
        kill_gates=(),
        board_tick_ns=(),
    )
    age_budget_ns = int(1.0 * 1_000_000_000)

    between = find_signal_asof(signals, poll_ns + poll_ns // 2, 1.0)
    assert between is not None
    assert between.predicted_delta == 0.01
    at_next_poll = find_signal_asof(signals, 2 * poll_ns, 1.0)
    assert at_next_poll is not None
    assert at_next_poll.predicted_delta == 0.02
    at_age_limit = find_signal_asof(signals, 2 * poll_ns + age_budget_ns, 1.0)
    assert at_age_limit is not None
    assert at_age_limit.predicted_delta == 0.02
    assert find_signal_asof(signals, 2 * poll_ns + age_budget_ns + 1, 1.0) is None


def test_anchor_gate_boundary() -> None:
    """A drift of exactly ANCHOR_TOLERANCE passes; one tick over blocks."""
    assert passes_anchor_gate(0.50, 0.50 + ANCHOR_TOLERANCE) is True
    assert passes_anchor_gate(0.50, 0.50 + ANCHOR_TOLERANCE + 0.0001) is False


def test_fair_value_adds_delta_and_clips() -> None:
    """Fair is book mid plus delta, clipped to [0, 1]."""
    assert calculate_fair_radiant(0.55, 0.10) == pytest.approx(0.65)
    assert calculate_fair_radiant(0.95, 0.10) == 1.0
    assert calculate_fair_radiant(0.05, -0.10) == 0.0


def test_token_fair_flips_for_dire_under_both_radiant_indexes() -> None:
    """calculate_token_fair maps Dire as 1 - fair_radiant for either Radiant leg."""
    fair_radiant = 0.62
    assert calculate_token_fair(fair_radiant, token_index=0, radiant_token_index=0) == 0.62
    assert calculate_token_fair(
        fair_radiant, token_index=1, radiant_token_index=0
    ) == pytest.approx(0.38)
    assert calculate_token_fair(fair_radiant, token_index=1, radiant_token_index=1) == 0.62
    assert calculate_token_fair(
        fair_radiant, token_index=0, radiant_token_index=1
    ) == pytest.approx(0.38)


def test_calculate_book_p_radiant_rejects_broken_pair() -> None:
    """Mids that break PAIR_SUM_TOLERANCE yield None."""
    assert calculate_book_p_radiant(0.50, 0.50) == pytest.approx(0.50)
    broken = 0.50 + PAIR_SUM_TOLERANCE + 0.01
    assert calculate_book_p_radiant(broken, 0.50) is None


GRID_TIMING = LIVE_GRID_TIMING


def test_grid_v1_drops_rows_before_the_archive_first_tick_second(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """A row at game-second 47 (dataset 57) is absent; game-second 48 stays the
    connect tick and the next cadence row decides."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    rows = build_signal_frame(seconds=(57, 58, 67))

    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)

    assert _feed_seconds(signals[1]) == (58, 67)
    assert _seconds_from_signals(signals[1]) == (67,)


def test_grid_v1_match_with_rows_only_before_first_tick_gets_empty_signals(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """A map whose usable rows all predate the feed's first tick keeps a slot
    with empty signals: live never decided there, so the run records zero
    decisions instead of crashing on the missing-rows check."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    rows = pd.concat(
        [
            build_signal_frame(match_id=1, seconds=(45, 50)),
            # 58 is always-kept connect; 61 is seed 0's first cadence tick for match 2.
            build_signal_frame(match_id=2, seconds=(58, 61)),
        ],
        ignore_index=True,
    )

    signals = predict_signals((1, 2), rows, model_dir, timing=GRID_TIMING)

    empty = signals[1]
    assert empty.feed_timestamps_ns == ()
    assert empty.timestamps_ns == ()
    assert empty.predicted_deltas == ()
    assert _seconds_from_signals(signals[2]) == (61,)


def _seconds_from_signals(signals: MatchSignals) -> tuple[int, ...]:
    """Decision seconds implied by 1 Hz state_ts_us in the fixture frame."""
    return tuple((ts // NS_PER_US - 1_000_000) // 1_000_000 for ts in signals.timestamps_ns)


def _feed_seconds(signals: MatchSignals) -> tuple[int, ...]:
    """Feed seconds implied by 1 Hz state_ts_us in the fixture frame."""
    return tuple((ts // NS_PER_US - 1_000_000) // 1_000_000 for ts in signals.feed_timestamps_ns)


def test_grid_v1_always_keeps_the_first_row(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """The first available second of a match is a feed tick even when the hash would drop it."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    first = 0
    for second in range(59, 400):
        unit = grid_v1_unit_interval(seed=0, game="dota", match_id=1, second=second)
        if unit >= 1.0 / 11.0:
            first = second
            break
    assert first >= 59
    rows = build_signal_frame(seconds=(first, first + 1))
    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    assert _feed_seconds(signals[1])[0] == first


def test_grid_v1_seed_is_stable_across_input_order(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """One seed yields the same seconds regardless of row order."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    seconds = tuple(range(58, 98))
    rows = build_signal_frame(seconds=seconds)
    shuffled = rows.sample(frac=1.0, random_state=4).reset_index(drop=True)
    left = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    right = predict_signals((1,), shuffled, model_dir, timing=GRID_TIMING)
    assert left[1].feed_timestamps_ns == right[1].feed_timestamps_ns
    assert left[1].timestamps_ns == right[1].timestamps_ns


def test_grid_v1_other_seed_changes_schedule(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """A different seed must not replay seed 0's synthetic GRID schedule."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    rows = build_signal_frame(seconds=tuple(range(58, 138)))
    seed0 = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    seed1 = predict_signals(
        (1,),
        rows,
        model_dir,
        timing=SignalTiming(seed=1, max_age_seconds=GRID_FEED_MAX_AGE_SECONDS),
    )
    assert seed0[1].feed_timestamps_ns != seed1[1].feed_timestamps_ns


def test_grid_v1_shard_matches_full_set_for_one_match(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """Sampling a match alone or beside another match selects the same seconds."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    left = build_signal_frame(match_id=1, seconds=tuple(range(58, 98)))
    right = build_signal_frame(match_id=2, seconds=tuple(range(58, 98)))
    both = pd.concat([left, right], ignore_index=True)
    solo = predict_signals((1,), left, model_dir, timing=GRID_TIMING)
    together = predict_signals((1, 2), both, model_dir, timing=GRID_TIMING)
    assert solo[1].feed_timestamps_ns == together[1].feed_timestamps_ns
    assert together[1].feed_timestamps_ns != together[2].feed_timestamps_ns


def test_cadence_interval_1_keeps_every_second(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """A mean interval of 1 keeps every match second; the cadence seed does not drop ticks."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    seconds = tuple(range(58, 98))
    rows = build_signal_frame(seconds=seconds)
    timing = SignalTiming(
        seed=0, max_age_seconds=GRID_FEED_MAX_AGE_SECONDS, mean_interval_seconds=1
    )
    signals = predict_signals((1,), rows, model_dir, timing=timing)
    assert _feed_seconds(signals[1]) == seconds


def test_grid_v1_keeps_death_change_rows(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    """A second where either side's deaths grew is always a feed tick."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    dropped: list[int] = []
    for second in range(59, 130):
        unit = grid_v1_unit_interval(seed=0, game="dota", match_id=1, second=second)
        if unit >= 1.0 / 11.0:
            dropped.append(second)
        if len(dropped) == 2:
            break
    assert len(dropped) == 2
    rows = build_signal_frame(seconds=(58, *dropped))
    rows.loc[rows["second"] == dropped[0], "deaths_radiant"] = 1
    rows.loc[rows["second"] == dropped[1], "deaths_dire"] = 1
    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    assert set(_feed_seconds(signals[1])) == {58, *dropped}


def test_grid_v1_dota_and_lol_use_different_bands(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """The same rows keep different seconds for Dota 11s vs LoL 8s early-game bands."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    rows = build_signal_frame(seconds=tuple(range(76, 116)))
    dota = predict_signals((1,), rows, model_dir, game="dota", timing=GRID_TIMING)
    lol = predict_signals(
        (1,),
        rows,
        model_dir,
        game="lol",
        lag_seconds=0,
        timing=GRID_TIMING,
        mids={1: MidSeries(timestamps_ns=(), market_ps=())},
    )
    dota_seconds = _feed_seconds(dota[1])
    lol_seconds = tuple(second - LOL_SOURCE_LAG_SECONDS for second in _feed_seconds(lol[1]))
    assert dota_seconds != lol_seconds


def test_grid_v1_cadence_uses_feature_second(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """Dota band lookup uses row.second - BACKTEST_LAG_SECONDS, not the parquet key."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    # Decision 189 → feature 179 (<180, 11s). Decision 190 → feature 180 (8s).
    early = 189
    late = 190
    early_unit = grid_v1_unit_interval(seed=0, game="dota", match_id=1, second=early)
    late_unit = grid_v1_unit_interval(seed=0, game="dota", match_id=1, second=late)
    # Death-kept cover rows tape the warmup pivots 189/190 need to stay valid.
    rows = build_signal_frame(seconds=(58, 70, 120, early, late))
    rows.loc[rows["second"] == 70, "deaths_radiant"] = 1
    rows.loc[rows["second"] == 120, "deaths_radiant"] = 2
    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    kept = set(_feed_seconds(signals[1]))
    if early_unit < 1.0 / 11.0:
        assert early in kept
    else:
        assert early not in kept
    if late_unit < 1.0 / 8.0:
        assert late in kept
    else:
        assert late not in kept


def test_grid_exit_age_never_below_entry() -> None:
    """Recovery/SELL age is the live 45s exit timeout, unless entry is already wider."""
    assert grid_exit_age_seconds(16.0) == pytest.approx(45.0)
    assert grid_exit_age_seconds(60.0) == pytest.approx(60.0)


def test_entry_gap_is_still_a_model_tick(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    """A gap between entry and exit age still calls the model, matching live."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    mid: int | None = None
    for second in range(75, 103):
        unit = grid_v1_unit_interval(seed=0, game="dota", match_id=1, second=second)
        if unit < 1.0 / 11.0:
            mid = second
            break
    assert mid is not None
    rows = build_signal_frame(seconds=(58, mid))
    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    assert mid in _seconds_from_signals(signals[1])


def test_recovery_stale_is_feed_not_model(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    """v5: a gap wider than the exit age is a feed tick and not a model decision."""
    fake = FakeBooster("unused")
    install_fake_booster(monkeypatch, fake)
    # interval=1 keeps every second so the gap is controlled exactly: 55s stale, 20s resume.
    timing = SignalTiming(
        seed=0, max_age_seconds=GRID_FEED_MAX_AGE_SECONDS, mean_interval_seconds=1
    )
    stale_at = 130
    resume_at = 150
    rows = build_signal_frame(seconds=(58, 75, stale_at, resume_at))
    signals = predict_signals((1,), rows, model_dir, timing=timing)
    assert stale_at in _feed_seconds(signals[1])
    assert stale_at not in _seconds_from_signals(signals[1])
    assert resume_at in _seconds_from_signals(signals[1])
    predicted_feature_seconds = fake.predict_calls[0]["seconds"]
    assert stale_at - BACKTEST_LAG_SECONDS not in predicted_feature_seconds
    assert resume_at - BACKTEST_LAG_SECONDS in predicted_feature_seconds


def test_duplicate_seconds_raise(monkeypatch: pytest.MonkeyPatch, model_dir: Path) -> None:
    """Two rows for the same match second are a contract break, not a silent drop."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    rows = pd.concat(
        [build_signal_frame(seconds=(58,)), build_signal_frame(seconds=(58,))],
        ignore_index=True,
    )
    with pytest.raises(ValueError, match="unique seconds"):
        predict_signals((1,), rows, model_dir)


def test_signal_timing_rejects_bad_values() -> None:
    """Seed and age are validated on the timing contract, not later in sampling."""
    with pytest.raises(ValueError, match="seed"):
        SignalTiming(seed=-1, max_age_seconds=1.0)
    with pytest.raises(ValueError, match="positive"):
        SignalTiming(seed=0, max_age_seconds=0.0)


def test_grid_v1_kill_gate_board_to_decision_gap_dota(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """The board marker lands `delay` after the kill; the death-carrying decision
    lands the 10s dataset lag after the kill, so board -> decision is 10 - delay."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    second = 61
    delay_s = grid_v1_scoreboard_delay_seconds(seed=0, game="dota", match_id=1, second=second)
    assert delay_s < KILL_GATE_MAX_BOARD_AGE_S
    rows = build_signal_frame(seconds=(58, second))
    rows.loc[rows["second"] == second, "deaths_radiant"] = 1
    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    gates = signals[1].kill_gates
    assert len(gates) == 1
    gate = gates[0]
    kill_market_ns = (1_000_000 + second * 1_000_000) * NS_PER_US
    assert gate.now_ns == kill_market_ns - BACKTEST_LAG_SECONDS * NS_PER_SECOND + round(
        delay_s * NS_PER_SECOND
    )
    decision_ns = signals[1].timestamps_ns[-1]
    assert decision_ns == kill_market_ns
    assert decision_ns - gate.now_ns == pytest.approx(
        (BACKTEST_LAG_SECONDS - delay_s) * NS_PER_SECOND, abs=1
    )
    assert gate.gate.radiant.awaited_deaths == 1
    assert gate.gate.radiant.until_ns == gate.now_ns + round(KILL_GATE_HOLD_S * NS_PER_SECOND)
    assert gate.gate.dire.until_ns == gate.now_ns
    assert signals[1].deaths_radiant == (1,)


def test_grid_v1_kill_gate_board_to_decision_gap_lol(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """LoL decides LOL_SOURCE_LAG_SECONDS after the frame, so the board marker
    leads the death-carrying decision by 11 - delay."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    second = 77
    delay_s = grid_v1_scoreboard_delay_seconds(seed=0, game="lol", match_id=1, second=second)
    assert delay_s < KILL_GATE_MAX_BOARD_AGE_S
    rows = build_signal_frame(seconds=(76, second))
    rows.loc[rows["second"] == second, "deaths_radiant"] = 1
    first_ns = 1_000_000 * NS_PER_US + LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    kill_ns = (1_000_000 + second * 1_000_000) * NS_PER_US + (
        LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    )
    mids = {1: MidSeries(timestamps_ns=(first_ns, kill_ns), market_ps=(0.6, 0.6))}
    signals = predict_signals(
        (1,), rows, model_dir, game="lol", lag_seconds=0, timing=GRID_TIMING, mids=mids
    )
    gates = signals[1].kill_gates
    assert len(gates) == 1
    gate = gates[0]
    decision_ns = signals[1].timestamps_ns[-1]
    assert decision_ns == kill_ns
    assert decision_ns - gate.now_ns == pytest.approx(
        (LOL_SOURCE_LAG_SECONDS - delay_s) * NS_PER_SECOND, abs=1
    )
    assert gate.gate.radiant.awaited_deaths == 1


def test_grid_v1_stale_board_lag_opens_no_kill_gate(
    monkeypatch: pytest.MonkeyPatch, model_dir: Path
) -> None:
    """A drawn board lag at or past the 9s freshness cutoff yields no gate."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    second = 69
    delay_s = grid_v1_scoreboard_delay_seconds(seed=0, game="dota", match_id=1, second=second)
    assert delay_s >= KILL_GATE_MAX_BOARD_AGE_S
    rows = build_signal_frame(seconds=(58, second))
    rows.loc[rows["second"] == second, "deaths_radiant"] = 1
    signals = predict_signals((1,), rows, model_dir, timing=GRID_TIMING)
    assert signals[1].kill_gates == ()
    assert signals[1].deaths_radiant == (1,)


def test_board_ticks_skip_lag_at_or_past_freshness_cutoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A drawn board lag >= 9s opens no marker: no forced row, pending stays 0."""
    board_dir = write_board_model_dir(tmp_path)
    fake = FakeBooster("unused", list(DOTA_XP_FEATURE_COLUMNS))
    install_fake_booster(monkeypatch, fake)
    second = 69
    assert (
        grid_v1_scoreboard_delay_seconds(seed=0, game="dota", match_id=1, second=second)
        >= KILL_GATE_MAX_BOARD_AGE_S
    )
    rows = build_signal_frame(seconds=(58, 60, second))
    rows.loc[rows["second"] == second, "deaths_radiant"] = 1
    timing = SignalTiming(
        seed=0, max_age_seconds=GRID_FEED_MAX_AGE_SECONDS, mean_interval_seconds=3600
    )

    signals = build_match_signals(
        (1,), rows, board_dir, BACKTEST_LAG_SECONDS, "dota", timing, _mids_from_rows(rows, "dota")
    )

    assert signals[1].board_tick_ns == ()
    assert _seconds_from_signals(signals[1]) == (second,)
    assert fake.predict_calls[0]["pending_deaths_radiant"] == [0]
    assert signals[1].deaths_radiant == (1,)
