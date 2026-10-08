from pathlib import Path
from typing import cast

import pytest
from catalog_fixtures import catalog_row

import market_data.build_market_data as cache_module
from prepare_dataset.prepare_dataset import (
    MatchIdSplit,
    MatchInputs,
    build_game_death_rows,
    build_game_feature_rows,
    build_game_history_minute_rows,
    build_minute_rows,
    exclude_missing_market_caches,
    join_validation_rows,
    match_minute_states,
    parse_args,
    select_matches,
    split_rows,
)
from prepare_dataset.stratz_seconds import ExactSecondState, MinuteLeadMismatchError
from shared.constants.dataset import (
    BACKTEST_LAG_SECONDS,
    MAX_TRAIN_HARD_MISSES,
    MAX_VALIDATION_HARD_MISSES,
    MODEL_START_SECOND,
    TRAIN_LAG_SECONDS,
)
from shared.constants.dota import LEVEL_XP
from shared.types.dataset import (
    DatasetRow,
    MarketQuoteStatus,
    MarketSecondRow,
)
from shared.types.stratz import UsableStratzMatch
from shared.utils.match_catalog import MatchCatalog, catalog_entry_from_row
from shared.utils.stratz import death_times
from shared.utils.top_players import TopPlayerFeatures

JOIN_MATCH = cast(UsableStratzMatch, {"id": 7})


def match_inputs(
    match: UsableStratzMatch,
    radiant_prior: float,
    market_rows: list[MarketSecondRow],
    start_time: int,
) -> MatchInputs:
    """Pack one match's prepare inputs the way `load_match_inputs` does."""
    return MatchInputs(
        match=match,
        market_rows=market_rows,
        entry=catalog_entry_from_row(
            catalog_row(int(match["id"]), start_time=start_time, radiant_prior=radiant_prior)
        ),
    )


def build_market_row(match_id: int, second: int) -> MarketSecondRow:
    """Build the market fields needed to exercise the validation join."""
    return cast(
        MarketSecondRow,
        {
            "match_id": match_id,
            "second": second,
            "market_status": "ok",
            "market_p_radiant": 0.5,
        },
    )


def build_train_market_row(
    match_id: int,
    second: int,
    market_status: MarketQuoteStatus,
    market_p_radiant: float | None,
    signal_market_p_radiant_300s: float | None,
) -> MarketSecondRow:
    """Build the market fields needed to exercise train-row filtering."""
    return cast(
        MarketSecondRow,
        {
            "match_id": match_id,
            "second": second,
            "market_status": market_status,
            "market_p_radiant": market_p_radiant,
            "signal_market_p_radiant_300s": signal_market_p_radiant_300s,
        },
    )


def build_state(match_id: int, second: int) -> ExactSecondState:
    """Build one exact-second state for join tests."""
    return ExactSecondState(
        match_id=match_id,
        second=second,
        radiant_win=True,
        radiant_nw_adv=second,
        radiant_nw=second,
        dire_nw=0,
        radiant_xp_adv=second * 2,
        deaths_radiant=0,
        deaths_dire=1,
        top=TopPlayerFeatures(
            top1_nw_adv=second * 3,
            radiant_top1_nw_ratio=1.5,
            dire_top1_nw_ratio=0.5,
            top3_nw_adv=second * 4,
            radiant_top3_nw_ratio=2.5,
            dire_top3_nw_ratio=1.5,
        ),
    )


def _gold_event(time: int, networth: int) -> dict[str, int]:
    """Build one absolute-networth playback event for a train-match fixture."""
    return {
        "time": time,
        "gold": 0,
        "networth": networth,
        "networthDifference": 0,
        "unreliableGold": 0,
    }


def build_train_match(lead_count: int) -> UsableStratzMatch:
    """Build a STRATZ match whose playback, npm, and leads agree on the train grid."""
    n_minutes = max(10, lead_count - 1)
    radiant_npm = [1000 + 100 * minute for minute in range(n_minutes)]
    dire_npm = [400 + 50 * minute for minute in range(n_minutes)]
    nw_leads = [0]
    nw_leads.extend(radiant_npm[minute] - dire_npm[minute] for minute in range(lead_count - 1))
    return cast(
        UsableStratzMatch,
        {
            "id": 123,
            "startDateTime": 1_700_000_100,
            "durationSeconds": (lead_count - 2) * 60,
            "didRadiantWin": True,
            "radiantNetworthLeads": nw_leads,
            "radiantExperienceLeads": list(range(lead_count)),
            "players": [
                {
                    "isRadiant": True,
                    "steamAccountId": 1,
                    "level": 10,
                    "playbackData": {
                        "playerUpdateGoldEvents": [
                            _gold_event(60 * minute, radiant_npm[minute]) for minute in range(10)
                        ]
                    },
                    "stats": {
                        "level": [-1, 60, 120, 180, 240, 300, 360, 420, 480, 540],
                        "deathEvents": [{"time": -30}, {"time": 0}, {"time": 30}],
                        "networthPerMinute": radiant_npm,
                    },
                },
                {
                    "isRadiant": False,
                    "steamAccountId": 2,
                    "level": 1,
                    "playbackData": {
                        "playerUpdateGoldEvents": [
                            _gold_event(60 * minute, dire_npm[minute]) for minute in range(10)
                        ]
                    },
                    "stats": {
                        "level": [-1],
                        "deathEvents": [{"time": 60}],
                        "networthPerMinute": dire_npm,
                    },
                },
            ],
        },
    )


def test_death_times_filters_team_and_sorts_events() -> None:
    """Collect only the requested team's deaths in chronological order."""
    match = cast(
        UsableStratzMatch,
        {
            "players": [
                {
                    "isRadiant": True,
                    "stats": {"deathEvents": [{"time": 120}, {"time": 10}]},
                },
                {
                    "isRadiant": False,
                    "stats": {"deathEvents": [{"time": 70}]},
                },
                {
                    "isRadiant": True,
                    "stats": {"deathEvents": [{"time": 30}]},
                },
            ]
        },
    )

    assert death_times(match, radiant=True) == [10, 30, 120]
    assert death_times(match, radiant=False) == [70]


def test_build_game_death_rows_counts_each_death_second() -> None:
    """Counts are deaths at or before the second, including the model-start pivot."""
    match = cast(
        UsableStratzMatch,
        {
            "players": [
                {"isRadiant": True, "stats": {"deathEvents": [{"time": 65}, {"time": 100}]}},
                {"isRadiant": False, "stats": {"deathEvents": [{"time": 66}]}},
            ]
        },
    )

    rows = build_game_death_rows(7, match)

    assert [row["game_second"] for row in rows] == [MODEL_START_SECOND, 65, 66, 100]
    assert [row["deaths_radiant"] for row in rows] == [0, 1, 1, 2]
    assert [row["deaths_dire"] for row in rows] == [0, 0, 1, 1]
    assert {row["match_id"] for row in rows} == {7}


def test_build_minute_rows_uses_minute_leads_without_playback() -> None:
    """Train rows come from networthPerMinute; playback is not required."""
    match = build_train_match(11)
    for player in match["players"]:
        player["playbackData"] = None
    market_rows = [
        build_train_market_row(123, second, "ok", 0.5, 0.6)
        for second in range(TRAIN_LAG_SECONDS, 600 + TRAIN_LAG_SECONDS, 60)
    ]

    rows = build_minute_rows(
        match_inputs(match, 0.62, market_rows, 1_700_000_100),
        match_minute_states(match_inputs(match, 0.62, market_rows, 1_700_000_100)),
        TRAIN_LAG_SECONDS,
    )

    assert [row["second"] for row in rows] == list(range(0, 600, 60))
    assert [row["radiant_nw"] for row in rows[:2]] == [1000, 1100]
    assert [row["dire_nw"] for row in rows[:2]] == [400, 450]


def test_build_minute_rows_uses_model_window_minute_seconds() -> None:
    """Build minute rows on the 60s grid; skip -60 when its lagged market is missing."""
    match = build_train_match(11)
    market_rows = [
        build_train_market_row(123, second, "ok", 0.5, 0.6)
        for second in range(TRAIN_LAG_SECONDS, 600 + TRAIN_LAG_SECONDS, 60)
    ]

    rows = build_minute_rows(
        match_inputs(match, 0.62, market_rows, 1_700_000_100),
        match_minute_states(match_inputs(match, 0.62, market_rows, 1_700_000_100)),
        TRAIN_LAG_SECONDS,
    )

    assert [row["second"] for row in rows] == list(range(0, 600, 60))
    assert [row["radiant_nw_adv"] for row in rows] == [600 + 50 * minute for minute in range(10)]
    assert [row["radiant_xp_adv"] for row in rows] == list(LEVEL_XP[:10])
    assert [row["deaths_radiant"] for row in rows[:2]] == [2, 3]
    assert [row["deaths_dire"] for row in rows[:2]] == [0, 1]
    assert [row["top1_nw_adv"] for row in rows[:2]] == [600, 650]
    assert [row["radiant_top1_nw_ratio"] for row in rows[:2]] == [0.0, 0.0]
    assert [row["dire_top1_nw_ratio"] for row in rows[:2]] == [0.0, 0.0]
    assert [row["radiant_nw"] for row in rows[:2]] == [1000, 1100]
    assert [row["dire_nw"] for row in rows[:2]] == [400, 450]
    assert {row["start_time"] for row in rows} == {1_700_000_100}
    assert {row["market_radiant_prior"] for row in rows} == {0.62}


def test_build_minute_rows_emits_prehorn_minute_from_playback() -> None:
    """Second -60 is the prehorn minute; player gold comes from playback when present."""
    match = build_train_match(11)
    radiant = match["players"][0]
    dire = match["players"][1]
    radiant_npm = radiant["stats"]["networthPerMinute"]
    dire_npm = dire["stats"]["networthPerMinute"]
    radiant["playbackData"] = {
        "playerUpdateGoldEvents": [_gold_event(-89, 800)]
        + [_gold_event(60 * minute, radiant_npm[minute]) for minute in range(10)]
    }
    dire["playbackData"] = {
        "playerUpdateGoldEvents": [_gold_event(-89, 500)]
        + [_gold_event(60 * minute, dire_npm[minute]) for minute in range(10)]
    }
    match["radiantNetworthLeads"] = [300] + [
        radiant_nw - dire_nw for radiant_nw, dire_nw in zip(radiant_npm, dire_npm, strict=True)
    ]
    market_rows = [
        build_train_market_row(123, MODEL_START_SECOND + TRAIN_LAG_SECONDS, "ok", 0.5, 0.6)
    ]

    rows = build_minute_rows(
        match_inputs(match, 0.62, market_rows, 1_700_000_100),
        match_minute_states(match_inputs(match, 0.62, market_rows, 1_700_000_100)),
        TRAIN_LAG_SECONDS,
    )

    assert len(rows) == 1
    assert rows[0]["second"] == MODEL_START_SECOND
    assert rows[0]["radiant_nw"] == 800
    assert rows[0]["dire_nw"] == 500
    assert rows[0]["radiant_nw_adv"] == rows[0]["radiant_nw"] - rows[0]["dire_nw"]
    assert rows[0]["market_p_radiant"] == 0.5


def test_build_minute_rows_rejects_a_lead_that_disagrees_with_npm() -> None:
    """A minute lead that contradicts the networthPerMinute sums fails loudly."""
    match = build_train_match(11)
    for player in match["players"]:
        player["playbackData"] = None
    leads = match["radiantNetworthLeads"]
    assert leads is not None
    leads[3] = 999_999
    market_rows = [
        build_train_market_row(123, second, "ok", 0.5, 0.6)
        for second in range(TRAIN_LAG_SECONDS, 600 + TRAIN_LAG_SECONDS, 60)
    ]

    with pytest.raises(MinuteLeadMismatchError, match="second=120"):
        build_minute_rows(
            match_inputs(match, 0.62, market_rows, 1_700_000_100),
            match_minute_states(match_inputs(match, 0.62, market_rows, 1_700_000_100)),
            TRAIN_LAG_SECONDS,
        )


def test_build_minute_rows_excludes_a_short_networth_per_minute() -> None:
    """A networthPerMinute shorter than the leads array excludes the match, not the run."""
    match = build_train_match(11)
    for player in match["players"]:
        player["playbackData"] = None
    match["players"][0]["stats"]["networthPerMinute"] = [1000, 1100]
    market_rows = [
        build_train_market_row(123, second, "ok", 0.5, 0.6)
        for second in range(TRAIN_LAG_SECONDS, 600 + TRAIN_LAG_SECONDS, 60)
    ]

    result = build_minute_rows(
        match_inputs(match, 0.62, market_rows, 1_700_000_100),
        match_minute_states(match_inputs(match, 0.62, market_rows, 1_700_000_100)),
        TRAIN_LAG_SECONDS,
    )

    assert result == []


def test_training_row_copies_market_from_source_lag_second() -> None:
    """Keep parquet second at T; copy mid and 300s future from cache T + TRAIN_LAG."""
    inputs = match_inputs(
        build_train_match(2),
        0.62,
        [
            build_train_market_row(123, 0, "missing_quote", 0.11, 0.22),
            build_train_market_row(123, TRAIN_LAG_SECONDS, "ok", 0.41, 0.58),
        ],
        1_700_000_100,
    )
    rows = build_minute_rows(inputs, match_minute_states(inputs), TRAIN_LAG_SECONDS)

    assert len(rows) == 1
    assert rows[0]["second"] == 0
    assert rows[0]["market_p_radiant"] == 0.41
    assert rows[0]["signal_market_p_radiant_300s"] == 0.58


def test_null_future_price_excludes_training_row() -> None:
    """Drop a minute whose lagged 300-second market price is unavailable."""
    inputs = match_inputs(
        build_train_match(3),
        0.62,
        [
            build_train_market_row(123, TRAIN_LAG_SECONDS, "ok", 0.41, None),
            build_train_market_row(123, 60 + TRAIN_LAG_SECONDS, "ok", 0.42, 0.59),
        ],
        1_700_000_100,
    )
    rows = build_minute_rows(inputs, match_minute_states(inputs), TRAIN_LAG_SECONDS)

    assert [row["second"] for row in rows] == [60]


def test_non_ok_market_status_excludes_training_row() -> None:
    """Drop a minute when the lagged market cache reports a non-usable quote."""
    inputs = match_inputs(
        build_train_match(3),
        0.62,
        [
            build_train_market_row(123, TRAIN_LAG_SECONDS, "missing_quote", 0.41, 0.58),
            build_train_market_row(123, 60 + TRAIN_LAG_SECONDS, "ok", 0.42, 0.59),
        ],
        1_700_000_100,
    )
    rows = build_minute_rows(inputs, match_minute_states(inputs), TRAIN_LAG_SECONDS)

    assert [row["second"] for row in rows] == [60]


def touch_market_cache(match_id: int) -> None:
    path = cache_module.market_seconds_cache_path(match_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def split_stub(match_id: int, start_time: int) -> DatasetRow:
    return cast(DatasetRow, {"match_id": match_id, "start_time": start_time})


def test_select_matches_by_cutoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Split the catalog on the cutoff; a post-cutoff match without cache drops out."""
    monkeypatch.setattr(cache_module, "MARKET_SECONDS_DIR", tmp_path)
    catalog = MatchCatalog(
        {
            1: catalog_entry_from_row(catalog_row(1, start_time=100)),
            3: catalog_entry_from_row(catalog_row(3, start_time=200, radiant_prior=0.4)),
        }
    )
    touch_market_cache(1)

    assert select_matches(catalog, 150) == MatchIdSplit(train=(1,), validation=())


def test_select_matches_includes_matches_past_dataset_end_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepare keeps post-END matches in validation; train/backtest cut later."""
    monkeypatch.setattr(cache_module, "MARKET_SECONDS_DIR", tmp_path)
    catalog = MatchCatalog(
        {
            1: catalog_entry_from_row(catalog_row(1, start_time=100)),
            2: catalog_entry_from_row(catalog_row(2, start_time=150, radiant_prior=0.4)),
            3: catalog_entry_from_row(catalog_row(3, start_time=200, radiant_prior=0.4)),
        }
    )
    touch_market_cache(1)
    touch_market_cache(2)
    touch_market_cache(3)

    assert select_matches(catalog, 120) == MatchIdSplit(train=(1,), validation=(2, 3))


def test_select_matches_require_playback() -> None:
    """Fail instead of silently dropping a post-cutoff match without playback."""
    catalog = MatchCatalog(
        {
            1: catalog_entry_from_row(catalog_row(1, start_time=150, playback_available=True)),
            2: catalog_entry_from_row(catalog_row(2, start_time=160, playback_available=False)),
        }
    )

    with pytest.raises(SystemExit, match="2"):
        select_matches(catalog, 150)


def test_build_game_feature_rows_keyed_by_game_second() -> None:
    """Archive-replay features carry the actual game second and no outcome."""
    states = (build_state(7, 0), build_state(7, 1))

    rows = build_game_feature_rows(7, states, 0.61)

    assert [row["game_second"] for row in rows] == [0, 1]
    assert [row["radiant_nw_adv"] for row in rows] == [0, 1]
    assert [row["radiant_xp_adv"] for row in rows] == [0, 2]
    assert [row["top1_nw_adv"] for row in rows] == [0, 3]
    assert {row["market_radiant_prior"] for row in rows} == {0.61}
    assert "radiant_win" not in rows[0]


def test_join_validation_rows_attaches_lagged_state() -> None:
    """Market T keeps the decision second; game fields come from T-2."""
    states = (build_state(7, 0), build_state(7, 1))
    market_rows = [
        build_market_row(7, BACKTEST_LAG_SECONDS),
        build_market_row(7, BACKTEST_LAG_SECONDS + 1),
    ]

    joined = join_validation_rows(
        states,
        match_inputs(JOIN_MATCH, 0.61, market_rows, 1_700_000_100),
        BACKTEST_LAG_SECONDS,
    )

    assert [row["second"] for row in joined] == [BACKTEST_LAG_SECONDS, BACKTEST_LAG_SECONDS + 1]
    assert [row["radiant_nw_adv"] for row in joined] == [0, 1]
    assert [row["radiant_xp_adv"] for row in joined] == [0, 2]
    assert [row["top1_nw_adv"] for row in joined] == [0, 3]
    assert [row["radiant_top1_nw_ratio"] for row in joined] == [1.5, 1.5]
    assert [row["dire_top1_nw_ratio"] for row in joined] == [0.5, 0.5]
    assert [row["radiant_nw"] for row in joined] == [0, 1]
    assert [row["dire_nw"] for row in joined] == [0, 0]
    assert joined[0]["market_p_radiant"] == 0.5
    assert joined[0]["market_radiant_prior"] == 0.61


def test_join_validation_rows_skips_before_lagged_start() -> None:
    """Decision seconds whose T-2 is before MODEL_START never become validation rows."""
    states = (build_state(7, MODEL_START_SECOND),)
    market_rows = [
        build_market_row(7, MODEL_START_SECOND),
        build_market_row(7, MODEL_START_SECOND + BACKTEST_LAG_SECONDS - 1),
    ]

    assert (
        join_validation_rows(
            states, match_inputs(JOIN_MATCH, 0.61, market_rows, 1_700_000_100), BACKTEST_LAG_SECONDS
        )
        == []
    )


def test_join_validation_rows_missing_lagged_state_fails() -> None:
    """Fail closed when the market second has no STRATZ snapshot at T-lag."""
    with pytest.raises(ValueError, match="missing lagged state"):
        join_validation_rows(
            (build_state(7, 0),),
            match_inputs(
                JOIN_MATCH, 0.61, [build_market_row(7, BACKTEST_LAG_SECONDS + 1)], 1_700_000_100
            ),
            BACKTEST_LAG_SECONDS,
        )


def test_validation_rows_include_seconds_past_899() -> None:
    """Join through map duration: drop T before start+lag, keep 900, attach game from T-lag."""
    first_joinable = MODEL_START_SECOND + BACKTEST_LAG_SECONDS
    states = tuple(build_state(7, second) for second in range(MODEL_START_SECOND, 901))
    market_rows = [
        build_market_row(7, second)
        for second in (
            MODEL_START_SECOND,
            first_joinable - 1,
            first_joinable,
            599,
            600,
            899,
            900,
        )
    ]

    joined = join_validation_rows(
        states,
        match_inputs(JOIN_MATCH, 0.61, market_rows, 1_700_000_100),
        BACKTEST_LAG_SECONDS,
    )

    assert [row["second"] for row in joined] == [first_joinable, 599, 600, 899, 900]
    assert [row["radiant_nw_adv"] for row in joined] == [
        MODEL_START_SECOND,
        599 - BACKTEST_LAG_SECONDS,
        600 - BACKTEST_LAG_SECONDS,
        899 - BACKTEST_LAG_SECONDS,
        900 - BACKTEST_LAG_SECONDS,
    ]
    assert max(row["second"] for row in joined) == 900


def test_missing_cache_under_cap_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing cache below the cap is dropped, not a hard failure."""
    monkeypatch.setattr(cache_module, "MARKET_SECONDS_DIR", tmp_path)
    touch_market_cache(1)

    assert exclude_missing_market_caches((1, 2), "train") == (1,)


def test_too_many_missing_caches_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More missing caches than the split cap still abort prepare."""
    monkeypatch.setattr(cache_module, "MARKET_SECONDS_DIR", tmp_path)
    assert (
        exclude_missing_market_caches(tuple(range(MAX_VALIDATION_HARD_MISSES + 1)), "train") == ()
    )
    with pytest.raises(SystemExit):
        exclude_missing_market_caches(tuple(range(MAX_VALIDATION_HARD_MISSES + 1)), "validation")
    with pytest.raises(SystemExit):
        exclude_missing_market_caches(tuple(range(MAX_TRAIN_HARD_MISSES + 1)), "train")


def test_split_rows_from_two_datasets() -> None:
    """Label completed matches and sort the split by chronology."""
    research = split_rows(
        [split_stub(2, 200), split_stub(1, 100)],
        "train",
    ) + split_rows(
        [split_stub(4, 400), split_stub(3, 300)],
        "validation",
    )
    production = split_rows(
        [split_stub(1, 100), split_stub(2, 200), split_stub(3, 300), split_stub(4, 400)],
        "train",
    )

    assert [(row["match_id"], row["split"]) for row in research] == [
        (1, "train"),
        (2, "train"),
        (3, "validation"),
        (4, "validation"),
    ]
    assert [(row["match_id"], row["split"]) for row in production] == [
        (1, "train"),
        (2, "train"),
        (3, "train"),
        (4, "train"),
    ]


def test_split_holds_only_matches_that_produced_saved_rows() -> None:
    """Build the split from saved train rows, excluding matches with no rows."""
    saved_rows = build_minute_rows(
        inputs := match_inputs(
            build_train_match(2),
            0.62,
            [build_train_market_row(123, TRAIN_LAG_SECONDS, "ok", 0.41, 0.58)],
            1_700_000_100,
        ),
        match_minute_states(inputs),
        TRAIN_LAG_SECONDS,
    )

    labeled = split_rows(saved_rows, "train")

    assert [(row["match_id"], row["split"]) for row in labeled] == [(123, "train")]


def test_select_matches_drops_missing_train_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing train cache below the cap drops that match from train."""
    monkeypatch.setattr(cache_module, "MARKET_SECONDS_DIR", tmp_path)
    catalog = MatchCatalog(
        {
            1: catalog_entry_from_row(catalog_row(1, start_time=100)),
            2: catalog_entry_from_row(catalog_row(2, start_time=110)),
        }
    )
    touch_market_cache(1)

    assert select_matches(catalog, 150) == MatchIdSplit(train=(1,), validation=())


def test_build_minute_rows_joins_at_explicit_lag() -> None:
    """Train rows copy the market at state second plus the caller lag, not the live constant."""
    inputs = match_inputs(
        build_train_match(2),
        0.62,
        [
            build_train_market_row(123, TRAIN_LAG_SECONDS, "ok", 0.41, 0.58),
            build_train_market_row(123, 25, "ok", 0.77, 0.88),
        ],
        1_700_000_100,
    )
    rows = build_minute_rows(inputs, match_minute_states(inputs), 25)

    assert len(rows) == 1
    assert rows[0]["second"] == 0
    assert rows[0]["market_p_radiant"] == 0.77
    assert rows[0]["signal_market_p_radiant_300s"] == 0.88


def test_prepare_nondefault_lag_requires_output_dir() -> None:
    """A lag other than 10 cannot write the live parquet paths."""
    with pytest.raises(SystemExit):
        parse_args(["--lag-seconds", "25"])
    args = parse_args(["--lag-seconds", "25", "--output-dir", "data/experiments/pgl-lag-25"])
    assert args.lag_seconds == 25
    assert args.output_dir == Path("data/experiments/pgl-lag-25")


def test_minute_rows_extend_past_600_with_valid_labels() -> None:
    """Minute training rows run through map end wherever the +300s label exists."""
    match = build_train_match(13)
    market_rows = [
        build_train_market_row(123, second + TRAIN_LAG_SECONDS, "ok", 0.5, 0.6)
        for second in range(0, 661, 60)
    ]
    inputs = match_inputs(match, 0.62, market_rows, 1_700_000_100)

    rows = build_minute_rows(inputs, match_minute_states(inputs), TRAIN_LAG_SECONDS)

    assert [row["second"] for row in rows] == list(range(0, 661, 60))


def test_history_minutes_keep_seconds_without_market_rows() -> None:
    """A minute dropped for a bad price still lands in the history tape artifact."""
    inputs = match_inputs(build_train_match(11), 0.62, [], 1_700_000_100)
    states = match_minute_states(inputs)

    rows = build_game_history_minute_rows(123, states)

    assert [row["game_second"] for row in rows] == [state.second for state in states]
    assert [row["second"] for row in build_minute_rows(inputs, states, TRAIN_LAG_SECONDS)] == []
