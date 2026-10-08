"""Dota feature contracts: 81/70 order, history math, tape/live parity."""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from backtest.feed_schedules import walk_feed_ticks
from shared.types.dataset import DotaSnapshotFeatures
from shared.utils.board_features import BOARD_LEAD_SECONDS, BoardHistory, board_model_values
from shared.utils.dota_features import (
    BOARD_DEATH_AGE_CAP_SECONDS,
    DOTA_BASE_HISTORY_FIELDS,
    DOTA_HISTORY_COLUMN_NAMES,
    DOTA_HISTORY_FIELDS,
    DOTA_LAG_OFFSETS_SECONDS,
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_TOP3_FIELDS,
    DOTA_XP_FEATURE_COLUMNS,
    GRID_HISTORY_POLICY,
    ODDIN_HISTORY_POLICY,
    HistoryPolicy,
    SnapshotHistory,
    attach_board_deaths,
    attach_catalog_features,
    attach_history_features,
    catalog_has_board,
    history_feature_block,
    history_policy_for_columns,
    market_derived_values,
    replay_tape_history,
    snapshot_features,
    snapshot_history_levels,
)
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.top_players import TopPlayerFeatures

LEVELS = tuple(range(1, len(DOTA_HISTORY_FIELDS) + 1))


def _policy(gap: float, *, start: int = -60) -> HistoryPolicy:
    """A tape policy with the test's gap reach; a full tape by default."""
    return HistoryPolicy(start_second=start, max_pivot_gap_seconds=gap, drop_gap_ticks=True)


def test_xp_feature_columns_exact_order_and_size() -> None:
    """81 columns: table state, history, market transforms, pending deaths and death ages."""
    assert [
        "second",
        "radiant_nw_adv",
        "radiant_nw",
        "dire_nw",
        "radiant_xp_adv",
        "deaths_radiant",
        "deaths_dire",
        "top1_nw_adv",
        "radiant_top1_nw_ratio",
        "dire_top1_nw_ratio",
        "market_radiant_prior",
        *[
            f"game_change_{minute}m_{field}"
            for minute in (1, 2, 3, 4, 5)
            for field in DOTA_BASE_HISTORY_FIELDS
        ],
        *[f"game_total_5m_{field}" for field in DOTA_BASE_HISTORY_FIELDS],
        "top3_nw_adv",
        "radiant_top3_nw_ratio",
        "dire_top3_nw_ratio",
        *[
            f"game_change_{minute}m_{field}"
            for minute in (1, 2, 3, 4, 5)
            for field in DOTA_TOP3_FIELDS
        ],
        *[f"game_total_5m_{field}" for field in DOTA_TOP3_FIELDS],
        "logit_market_p_radiant",
        "market_vs_prior",
        "market_p_radiant",
        "pending_deaths_radiant",
        "pending_deaths_dire",
        "board_death_age_radiant_s",
        "board_death_age_dire_s",
    ] == DOTA_XP_FEATURE_COLUMNS
    assert len(DOTA_XP_FEATURE_COLUMNS) == 81
    assert DOTA_XP_FEATURE_COLUMNS[-1] == "board_death_age_dire_s"
    assert not any("tower" in column or "building" in column for column in DOTA_XP_FEATURE_COLUMNS)


def test_noxp_feature_columns_drop_every_xp_column() -> None:
    """70 columns: the XP list minus the current XP field and its six history columns."""
    assert [
        column
        for column in DOTA_XP_FEATURE_COLUMNS[:-4]
        if column != "radiant_xp_adv" and not column.endswith("_radiant_xp_adv")
    ] == DOTA_NOXP_FEATURE_COLUMNS
    assert len(DOTA_NOXP_FEATURE_COLUMNS) == 70
    assert DOTA_NOXP_FEATURE_COLUMNS[-1] == "market_p_radiant"
    assert not any("xp" in column for column in DOTA_NOXP_FEATURE_COLUMNS)


def test_history_column_count_and_names() -> None:
    """60 derived columns: five minute changes per field, then 5m totals."""
    assert len(DOTA_HISTORY_FIELDS) == 10
    assert len(DOTA_HISTORY_COLUMN_NAMES) == 60
    assert DOTA_HISTORY_COLUMN_NAMES[0] == "game_change_1m_radiant_nw_adv"
    assert DOTA_HISTORY_COLUMN_NAMES[5 * 10 - 1] == "game_change_5m_dire_top3_nw_ratio"
    assert DOTA_HISTORY_COLUMN_NAMES[-1] == "game_total_5m_dire_top3_nw_ratio"


def test_derived_history_walks_minute_pivots() -> None:
    """change_km subtracts consecutive lag levels; total_5m subtracts the -300 level."""
    tape = SnapshotHistory(_policy(60))
    current = tuple(float(10 * index + 1000) for index in range(len(DOTA_HISTORY_FIELDS)))
    for lag in reversed(DOTA_LAG_OFFSETS_SECONDS):
        tape.record(600 - lag, tuple(float(10 * index + lag) for index in range(len(LEVELS))))

    derived = tape.derived(600, current)

    field = DOTA_HISTORY_FIELDS[0]
    assert derived[f"game_change_1m_{field}"] == pytest.approx(1000 - 60)
    assert derived[f"game_change_2m_{field}"] == pytest.approx(60 - 120)
    assert derived[f"game_change_5m_{field}"] == pytest.approx(240 - 300)
    assert derived[f"game_total_5m_{field}"] == pytest.approx(1000 - 300)


def test_derived_history_missing_and_stale_pivots_are_nan() -> None:
    """No recorded pivot or one beyond the gap budget leaves NaN."""
    tape = SnapshotHistory(_policy(0))
    assert math.isnan(tape.derived(600, LEVELS)["game_change_1m_radiant_nw_adv"])

    tape.record(239, LEVELS)  # 1s short of the -60 target, gap budget 0
    derived = tape.derived(300, LEVELS)
    assert math.isnan(derived["game_change_1m_radiant_nw_adv"])

    exact = SnapshotHistory(_policy(0))
    exact.record(240, LEVELS)
    derived_exact = exact.derived(300, tuple(value + 1.0 for value in LEVELS))
    assert derived_exact["game_change_1m_radiant_nw_adv"] == 1.0
    assert math.isnan(derived_exact["game_change_2m_radiant_nw_adv"])


def test_derived_history_missing_pivot_cascades() -> None:
    """A missing 2m pivot NaNs its own diff and the remaining minute chain."""
    tape = SnapshotHistory(_policy(0))
    tape.record(540, LEVELS)  # -60 only
    derived = tape.derived(600, LEVELS)
    assert derived["game_change_1m_radiant_nw_adv"] == 0.0
    for minute in (2, 3, 4, 5):
        assert math.isnan(derived[f"game_change_{minute}m_radiant_nw_adv"])
    assert math.isnan(derived["game_total_5m_radiant_nw_adv"])


def test_snapshot_history_uses_the_nearest_taped_second_within_budget() -> None:
    """Pivots resolve to the nearest taped second; an out-of-budget target is NaN."""
    tape = SnapshotHistory(_policy(5))
    tape.record(540, tuple(1.0 for _ in LEVELS))
    tape.record(541, tuple(2.0 for _ in LEVELS))
    derived = tape.derived(601, LEVELS)
    assert derived["game_change_1m_radiant_nw_adv"] == pytest.approx(LEVELS[0] - 2.0)
    assert math.isnan(tape.derived(547, LEVELS)["game_change_1m_radiant_nw_adv"])


def test_market_derived_values_clip_the_logit() -> None:
    """logit clips the mid at [0.001, 0.999]; vs-prior is the raw difference."""
    derived = market_derived_values(0.6, 0.5)
    assert derived["logit_market_p_radiant"] == pytest.approx(math.log(0.6 / 0.4))
    assert derived["market_vs_prior"] == pytest.approx(0.1)

    clipped = market_derived_values(1.0, 0.4)
    assert clipped["logit_market_p_radiant"] == pytest.approx(math.log(0.999 / 0.001))
    assert clipped["market_vs_prior"] == pytest.approx(0.6)


def _tape_frame(match_id: int, seconds: list[int], levels: list[tuple[float, ...]]) -> pd.DataFrame:
    rows: dict[str, list[int] | list[float]] = {
        "match_id": [match_id] * len(seconds),
        "game_second": seconds,
    }
    for index, field in enumerate(DOTA_HISTORY_FIELDS):
        rows[field] = [level[index] for level in levels]
    return pd.DataFrame(rows)


def test_attach_history_matches_live_ring_on_a_dense_tape() -> None:
    """Vectorized attach equals walking SnapshotHistory on per-second tape levels."""
    seconds = list(range(0, 400, 30))
    levels = [tuple(float(second + index) for index in range(len(LEVELS))) for second in seconds]
    tape = _tape_frame(1, seconds, levels)
    decision_levels = [tuple(1.0 for _ in LEVELS), tuple(2.0 for _ in LEVELS)]
    frame = pd.DataFrame(
        {
            "match_id": [1, 1],
            "second": [300, 390],
            **{f: [levels[0] for levels in decision_levels] for f in DOTA_HISTORY_FIELDS},
        }
    )

    attached = attach_history_features(
        frame,
        tape,
        key_seconds=frame["second"],
        start_second=-60,
    )

    by_second = dict(zip(seconds, levels, strict=True))
    expected: list[dict[str, float]] = []
    for decision_second, current in zip((300, 390), decision_levels, strict=True):
        live = SnapshotHistory(_policy(0.0))
        for second in seconds:
            if second <= decision_second:
                live.record(second, by_second[second])
        expected.append(live.derived(decision_second, current))
    for row_index, block in enumerate(expected):
        for column, value in block.items():
            assert attached[column].iloc[row_index] == pytest.approx(value, nan_ok=True)


def test_attach_history_matches_live_ring_on_a_sparse_tape() -> None:
    """A sparse feed tape keeps the same as-of math; late lags go NaN identically."""
    seconds = [0, 45, 90, 300]
    levels = [tuple(float(second) for _ in LEVELS) for second in seconds]
    tape = _tape_frame(2, seconds, levels)
    frame = pd.DataFrame(
        {"match_id": [2], "second": [300], **{f: [7.0] for f in DOTA_HISTORY_FIELDS}}
    )

    attached = attach_history_features(
        frame,
        tape,
        key_seconds=frame["second"],
        start_second=-60,
    )
    live = SnapshotHistory(_policy(0.0))
    for second, level in zip(seconds, levels, strict=True):
        live.record(second, level)
    expected = live.derived(300, tuple(7.0 for _ in LEVELS))
    for column in DOTA_HISTORY_COLUMN_NAMES:
        assert attached[column].iloc[0] == pytest.approx(expected[column], nan_ok=True)


def test_attach_history_keeps_interleaved_matches_independent() -> None:
    """Two matches on the same seconds build separate tapes; no cross-match pivots."""
    seconds = [0, 60, 120]
    level_a = [tuple(float(index) for _ in LEVELS) for index in (1, 2, 3)]
    level_b = [tuple(float(index * 100) for _ in LEVELS) for index in (1, 2, 3)]
    tape = pd.concat(
        [_tape_frame(1, seconds, level_a), _tape_frame(2, seconds, level_b)],
        ignore_index=True,
    )
    frame = pd.DataFrame(
        {
            "match_id": [1, 2],
            "second": [120, 120],
            **{f: [10.0, 20.0] for f in DOTA_HISTORY_FIELDS},
        }
    )

    attached = attach_history_features(
        frame,
        tape,
        key_seconds=frame["second"],
        start_second=-60,
    )

    assert attached.loc[0, "game_change_1m_radiant_nw_adv"] == pytest.approx(10.0 - 2.0)
    assert attached.loc[1, "game_change_1m_radiant_nw_adv"] == pytest.approx(20.0 - 200.0)
    assert attached.loc[0, "game_change_2m_radiant_nw_adv"] == pytest.approx(2.0 - 1.0)
    assert attached.loc[1, "game_change_2m_radiant_nw_adv"] == pytest.approx(200.0 - 100.0)


def test_attach_history_raises_when_match_missing_from_tape() -> None:
    """A frame match absent from the tape is a mismatched artifact, not NaN rows."""
    tape = _tape_frame(1, [0], [LEVELS])
    frame = pd.DataFrame(
        {"match_id": [9], "second": [60], **{f: [1.0] for f in DOTA_HISTORY_FIELDS}}
    )
    with pytest.raises(ValueError, match="match 9: missing from history tape"):
        attach_history_features(
            frame,
            tape,
            key_seconds=frame["second"],
            start_second=-60,
        )


def test_attach_catalog_features_adds_history_and_market_columns() -> None:
    """One call yields the 60 history columns plus logit/vs-prior market columns."""
    tape = _tape_frame(1, [0, 60, 120], [LEVELS, LEVELS, LEVELS])
    frame = pd.DataFrame(
        {
            "match_id": [1],
            "second": [120],
            "market_p_radiant": [0.6],
            "market_radiant_prior": [0.5],
            **{f: [2.0] for f in DOTA_HISTORY_FIELDS},
        }
    )

    attached = attach_catalog_features(
        frame,
        tape,
        board_tape=None,
        key_seconds=frame["second"],
        start_second=-60,
    )

    assert attached["game_change_1m_radiant_nw_adv"].iloc[0] == pytest.approx(1.0)
    assert attached["logit_market_p_radiant"].iloc[0] == pytest.approx(math.log(0.6 / 0.4))
    assert attached["market_vs_prior"].iloc[0] == pytest.approx(0.1)


def test_history_feature_block_vectorized_matches_walk() -> None:
    """The block builder is a faithful vectorization of SnapshotHistory."""
    rng = np.random.default_rng(7)
    tape_seconds = np.sort(rng.integers(-60, 900, size=40))
    tape_levels = rng.normal(size=(40, len(DOTA_HISTORY_FIELDS)))
    decision_seconds = np.sort(rng.integers(0, 900, size=8))
    decision_levels = rng.normal(size=(8, len(DOTA_HISTORY_FIELDS)))

    block = history_feature_block(
        tape_seconds, tape_levels, decision_seconds, decision_levels, -60, 4.0
    )

    for row, second in enumerate(decision_seconds):
        tape = SnapshotHistory(_policy(4.0))
        for position, tape_second in enumerate(tape_seconds):
            if tape_second <= second:
                tape.record(int(tape_second), tape_levels[position])
        expected = tape.derived(int(second), decision_levels[row])
        for column in DOTA_HISTORY_COLUMN_NAMES:
            assert block[column][row] == pytest.approx(expected[column], nan_ok=True)


def test_replay_tape_history_records_before_deciding_and_skips_terminal() -> None:
    """A recording tick joins its own pivots; a non-recording decision does not."""
    seconds = [540, 600, 660]
    levels = np.asarray([[float(index)] * len(LEVELS) for index in (1, 2, 3)])

    block = replay_tape_history(seconds, levels, [True, False, True], [0, 1, 2], _policy(90.0))

    # First tick's -60 target pivots to itself: the only snapshot is 60s away.
    assert block["game_change_1m_radiant_nw_adv"].iloc[0] == pytest.approx(0.0)
    # Second tick decides against the recorded 540 pivot but never records.
    assert block["game_change_1m_radiant_nw_adv"].iloc[1] == pytest.approx(2.0 - 1.0)
    # Third tick's -60 pivot ties 540/660 at 60s each; the earlier second wins.
    assert block["game_change_1m_radiant_nw_adv"].iloc[2] == pytest.approx(3.0 - 1.0)


def test_snapshot_history_tolerates_rewound_seconds() -> None:
    """A tape whose seconds rewind keeps the latest inserted level as the pivot."""
    tape = SnapshotHistory(_policy(0.0))
    tape.record(540, tuple(1.0 for _ in LEVELS))
    tape.record(500, tuple(9.0 for _ in LEVELS))
    derived = tape.derived(600, tuple(0.0 for _ in LEVELS))
    # as-of on the second-sorted buffer: both 500 and 540 are <= 540 target,
    # the maximum second wins, matching the vectorized pivot.
    assert derived["game_change_1m_radiant_nw_adv"] == pytest.approx(0.0 - 1.0)


def test_history_policy_clips_the_tape_before_start_second() -> None:
    """Snapshots below start_second never join the tape; sub-start targets stay NaN."""
    tape = SnapshotHistory(GRID_HISTORY_POLICY)
    tape.record(30, tuple(1.0 for _ in LEVELS))
    tape.record(50, tuple(2.0 for _ in LEVELS))
    derived = tape.derived(120, LEVELS)
    assert all(math.isnan(value) for value in derived.values())


def test_history_policy_uses_the_nearest_snapshot_after_the_target() -> None:
    """A later received snapshot inside the gap budget can pivot its target."""
    tape = SnapshotHistory(GRID_HISTORY_POLICY)
    tape.record(300, tuple(5.0 for _ in LEVELS))
    derived = tape.derived(330, tuple(9.0 for _ in LEVELS))
    # Target 270 has no earlier snapshot; 300 is 30s away, inside the 30s reach.
    assert derived["game_change_1m_radiant_nw_adv"] == pytest.approx(9.0 - 5.0)


def test_check_required_lags_keeps_warmup_ticks_valid() -> None:
    """Targets before the first taped second are warmup: the tick stays valid."""
    tape = SnapshotHistory(GRID_HISTORY_POLICY)
    tape.record(300, LEVELS)
    assert tape.check_required_lags(340) is True


def test_check_required_lags_flags_an_interior_gap() -> None:
    """A lag target inside a tape gap makes the tick invalid under drop_gap_ticks."""
    tape = SnapshotHistory(GRID_HISTORY_POLICY)
    for second in (60, 300, 700):
        tape.record(second, LEVELS)
    assert tape.check_required_lags(700) is False


def test_check_required_lags_turns_valid_again_after_the_gap_fills() -> None:
    """Once fresh ticks restore every required pivot the tick decides again."""
    tape = SnapshotHistory(GRID_HISTORY_POLICY)
    for second in (60, 120, 400):
        tape.record(second, LEVELS)
    assert tape.check_required_lags(700) is False
    for second in (460, 520, 580, 640):
        tape.record(second, LEVELS)
    assert tape.check_required_lags(700) is True


def test_check_required_lags_is_disabled_when_the_policy_never_drops() -> None:
    """Oddin keeps every tick: a tape gap is a NaN column, not a dropped tick."""
    tape = SnapshotHistory(ODDIN_HISTORY_POLICY)
    tape.record(60, LEVELS)
    tape.record(700, LEVELS)
    assert tape.check_required_lags(700) is True


def test_history_policy_requires_exact_current_catalogs() -> None:
    assert history_policy_for_columns(DOTA_XP_FEATURE_COLUMNS) is GRID_HISTORY_POLICY
    assert history_policy_for_columns(DOTA_NOXP_FEATURE_COLUMNS) is ODDIN_HISTORY_POLICY
    for columns in (
        DOTA_XP_FEATURE_COLUMNS[:-4],
        DOTA_XP_FEATURE_COLUMNS[::-1],
        ["second", "radiant_xp_adv"],
    ):
        with pytest.raises(ValueError, match="canonical"):
            history_policy_for_columns(columns)


def test_feed_walk_marks_gap_ticks_but_keeps_their_snapshots() -> None:
    """An invalid tick drops the decision while its snapshot stays on the tape."""
    seconds = [60, 120, 400, 460, 520, 580, 640, 700]
    levels = np.asarray([[float(second)] * len(LEVELS) for second in seconds])
    ticks = list(
        walk_feed_ticks(
            seconds,
            levels,
            [True] * len(seconds),
            [0] * len(seconds),
            float("inf"),
            GRID_HISTORY_POLICY,
        )
    )
    assert [tick.valid for tick in ticks] == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    # At 700 the -60 target pivots to 640 even though the in-between ticks were
    # dropped as invalid; their snapshots stayed on the tape.
    block = ticks[7].derived(700, levels[7])
    assert block["game_change_1m_radiant_nw_adv"] == pytest.approx(700.0 - 640.0)


def test_history_policy_per_second_tape_keeps_exact_pivots() -> None:
    """A per-second tape resolves the same pivots as the old as-of rule."""
    tape = SnapshotHistory(ODDIN_HISTORY_POLICY)
    for second in range(-60, 301):
        tape.record(second, tuple(float(second + index) for index in range(len(LEVELS))))
    derived = tape.derived(300, tuple(300 + index for index in range(len(LEVELS))))
    assert derived["game_change_1m_radiant_nw_adv"] == pytest.approx(60.0)
    assert derived["game_change_5m_radiant_nw_adv"] == pytest.approx(60.0)
    assert derived["game_total_5m_radiant_nw_adv"] == pytest.approx(300.0)


@dataclass(frozen=True)
class _StateReading:
    """Smallest SnapshotState shape: catalog-named state fields plus top."""

    radiant_nw_adv: int
    radiant_nw: int
    dire_nw: int
    radiant_xp_adv: int
    deaths_radiant: int
    deaths_dire: int
    top: TopPlayerFeatures


def test_snapshot_conversions_read_state_fields_under_catalog_names() -> None:
    """One state object feeds both the 12-field block and the tape levels."""
    top = TopPlayerFeatures(
        top1_nw_adv=70,
        radiant_top1_nw_ratio=0.4,
        dire_top1_nw_ratio=0.3,
        top3_nw_adv=90,
        radiant_top3_nw_ratio=0.7,
        dire_top3_nw_ratio=0.6,
    )
    state = _StateReading(11, 1000, 989, 13, 1, 2, top)

    features = snapshot_features(state)
    assert set(features) == set(DotaSnapshotFeatures.__annotations__)
    assert features["radiant_nw"] == 1000
    assert features["deaths_dire"] == 2
    assert features["top3_nw_adv"] == 90
    assert features["dire_top1_nw_ratio"] == 0.3

    levels = snapshot_history_levels(state)
    assert len(levels) == len(DOTA_HISTORY_FIELDS)
    assert levels[DOTA_HISTORY_FIELDS.index("radiant_nw")] == 1000.0
    assert levels[DOTA_HISTORY_FIELDS.index("radiant_xp_adv")] == 13.0
    assert levels[DOTA_HISTORY_FIELDS.index("radiant_top3_nw_ratio")] == 0.7


def test_canonical_xp_catalog_includes_pending_and_age() -> None:
    assert DOTA_XP_FEATURE_COLUMNS[-4:] == [
        "pending_deaths_radiant",
        "pending_deaths_dire",
        "board_death_age_radiant_s",
        "board_death_age_dire_s",
    ]
    assert len(DOTA_XP_FEATURE_COLUMNS) == 81


def test_board_catalog_uses_the_grid_history_policy() -> None:
    """history_policy_for_columns pins the board catalog on its XP marker."""
    assert history_policy_for_columns(DOTA_XP_FEATURE_COLUMNS) == GRID_HISTORY_POLICY


def test_attach_board_deaths_rewrites_deaths_to_the_board_clock() -> None:
    """Board deaths read the tape at key+8; pending is the gap clipped at 0."""
    seconds = list(range(100, 112))
    levels = [tuple(0.0 for _ in DOTA_HISTORY_FIELDS) for _ in seconds]
    tape = _tape_frame(1, seconds, levels).assign(
        deaths_radiant=[1 if second >= 108 else 0 for second in seconds],
        deaths_dire=0,
    )
    frame = pd.DataFrame(
        {
            "match_id": [1, 1],
            "second": [100, 110],
            "deaths_radiant": [0, 1],
            "deaths_dire": [0, 0],
            "market_p_radiant": [0.6, 0.6],
            "market_radiant_prior": [0.5, 0.5],
            **{field: [0.0, 0.0] for field in DOTA_HISTORY_FIELDS},
        }
    )

    attached = attach_catalog_features(
        frame,
        tape,
        key_seconds=frame["second"],
        start_second=-60,
        board_tape=tape,
    )

    # Second 100's board target 108 already shows the kill its own row lacks;
    # second 110's row has it in the table, so pending is back to zero.
    assert attached["deaths_radiant"].tolist() == [1, 1]
    assert attached["deaths_dire"].tolist() == [0, 0]
    assert attached["pending_deaths_radiant"].tolist() == [1, 0]
    assert attached["pending_deaths_dire"].tolist() == [0, 0]


def test_attach_board_deaths_ages_since_the_last_board_death() -> None:
    """Age is the cap before a side's first death, 0 on that board second, then
    grows — including after pending returns to 0 — until the cap. Sides are
    independent, and a match with no deaths stays at the cap."""
    seconds = list(range(50))
    tape = pd.DataFrame(
        {
            "match_id": [1] * len(seconds) + [2] * len(seconds),
            "game_second": seconds + seconds,
            "deaths_radiant": [0 if second < 10 else 1 for second in seconds] + [0] * len(seconds),
            "deaths_dire": [0 if second < 25 else 1 for second in seconds] + [0] * len(seconds),
        }
    )
    keys = [0, 5, 2, 10, 17, 33]
    frame = pd.DataFrame(
        {
            "match_id": [1, 2, 1, 1, 1, 1],
            "deaths_radiant": [0, 0, 0, 1, 1, 1],
            "deaths_dire": [0, 0, 0, 0, 0, 1],
        }
    )

    attached = attach_board_deaths(frame, tape, key_seconds=pd.Series(keys, index=frame.index))

    # Targets are key + 8. Radiant dies at tape second 10; dire at 25.
    # Key 2 (target 10) still has table deaths 0, so pending is 1 and age is 0.
    # Key 10 (target 18) has the kill in the table; age has kept counting.
    assert attached["pending_deaths_radiant"].tolist() == [0, 0, 1, 0, 0, 0]
    assert attached["pending_deaths_dire"].tolist() == [0, 0, 0, 0, 1, 0]
    assert attached["board_death_age_radiant_s"].tolist() == [
        BOARD_DEATH_AGE_CAP_SECONDS,
        BOARD_DEATH_AGE_CAP_SECONDS,
        0,
        8,
        15,
        BOARD_DEATH_AGE_CAP_SECONDS,
    ]
    assert attached["board_death_age_dire_s"].tolist() == [
        BOARD_DEATH_AGE_CAP_SECONDS,
        BOARD_DEATH_AGE_CAP_SECONDS,
        BOARD_DEATH_AGE_CAP_SECONDS,
        BOARD_DEATH_AGE_CAP_SECONDS,
        0,
        16,
    ]


def test_catalog_has_board_follows_the_scoreboard_columns() -> None:
    assert catalog_has_board(DOTA_XP_FEATURE_COLUMNS)
    assert not catalog_has_board(DOTA_NOXP_FEATURE_COLUMNS)


def test_board_history_matches_death_tape_when_board_leads_by_the_lead() -> None:
    """Pending, age, and deaths agree when the board shows a kill BOARD_LEAD_SECONDS early."""
    death_second = 20
    decision_second = death_second - BOARD_LEAD_SECONDS
    seconds = list(range(death_second + BOARD_LEAD_SECONDS + 1))
    tape = pd.DataFrame(
        {
            "match_id": [1] * len(seconds),
            "game_second": seconds,
            "deaths_radiant": [1 if second >= death_second else 0 for second in seconds],
            "deaths_dire": [0] * len(seconds),
        }
    )
    frame = pd.DataFrame(
        {
            "match_id": [1, 1],
            "deaths_radiant": [0, 1],
            "deaths_dire": [0, 0],
        }
    )
    attached = attach_board_deaths(
        frame, tape, key_seconds=pd.Series([decision_second, death_second])
    )
    history = BoardHistory()
    board_ns = decision_second * NS_PER_SECOND
    history.record_board(board_ns, 1, 0)
    at_lead = history.derive(board_ns, 0, 0)
    table_ns = death_second * NS_PER_SECOND
    history.record_table(table_ns, 1, 0)
    at_table = history.derive(table_ns, 1, 0)
    for features, index in ((at_lead, 0), (at_table, 1)):
        values = board_model_values(features)
        assert values["deaths_radiant"] == attached["deaths_radiant"].iloc[index]
        assert values["deaths_dire"] == attached["deaths_dire"].iloc[index]
        assert values["pending_deaths_radiant"] == attached["pending_deaths_radiant"].iloc[index]
        assert values["pending_deaths_dire"] == attached["pending_deaths_dire"].iloc[index]
        assert values["board_death_age_radiant_s"] == pytest.approx(
            float(attached["board_death_age_radiant_s"].iloc[index])
        )
        assert values["board_death_age_dire_s"] == pytest.approx(
            float(attached["board_death_age_dire_s"].iloc[index])
        )
