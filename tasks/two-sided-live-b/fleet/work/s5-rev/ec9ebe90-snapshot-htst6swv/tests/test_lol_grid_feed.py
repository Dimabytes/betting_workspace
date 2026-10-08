"""GRID reducer LoL snapshot: BLUE/RED, LOL_LEVEL_XP, top1 / team total."""

from datetime import UTC, datetime

from lol_grid_widget_fixtures import (
    CBLOL_SCOREBOARD,
    CBLOL_SERIES_ID,
    CBLOL_SERIES_TABLE,
    CBLOL_SERIES_TABLE_BEFORE_LEVEL_UP,
    LEC_SCOREBOARD,
    LEC_SERIES_ID,
    LEC_SERIES_TABLE,
    wrap,
)

from shared.constants.lol import LOL_LEVEL_XP
from shared.utils.level_xp import xp_advantage
from shared.utils.top_players import (
    build_top_player_features,
    build_top_player_features_over_total,
)
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer
from trader.grid_widgets import parse_frame
from trader.live_feed import FeedEvent, KillTick

NOW = datetime(2026, 8, 29, 18, 17, 48, tzinfo=UTC)


def _lol_feed(map_number: int, outcome_0: str, outcome_1: str) -> GridFrameReducer:
    """Pinned map with LoL BLUE/RED sides and LOL_LEVEL_XP."""
    return GridFrameReducer(map_number, outcome_0, outcome_1, GAME_PROFILES["lol"])


def _read(reducer: GridFrameReducer, raw: str) -> FeedEvent | None:
    """Parse one raw frame and fold it into the reducer."""
    event = reducer.reduce_frame(parse_frame(raw), NOW)
    assert not isinstance(event, KillTick)
    return event


def _scoreboard_then_table(
    feed: GridFrameReducer, scoreboard: object, table: object, delay: int, series_id: str
) -> FeedEvent | None:
    """Store the scoreboard (no tick), then ingest one table frame."""
    assert _read(feed, wrap("series_scoreboard_v2", 0, scoreboard, series_id)) is None
    return _read(feed, wrap("series_table", delay, table, series_id))


def test_cblol_map_one_uses_lol_xp_and_top1_over_total() -> None:
    """CBLOL map 1: BLUE=Fluxo is radiant; XP via LOL_LEVEL_XP; ratios over team total."""
    event = _scoreboard_then_table(
        _lol_feed(1, "Fluxo W7M", "LOS"),
        CBLOL_SCOREBOARD,
        CBLOL_SERIES_TABLE,
        8,
        CBLOL_SERIES_ID,
    )
    assert event is not None
    snapshot = event.snapshot
    radiant_nws = [2588, 4824, 4472, 5522, 5039]
    dire_nws = [4615, 2677, 4601, 4064, 5494]
    radiant_levels = [5, 10, 10, 9, 9]
    dire_levels = [9, 6, 9, 10, 9]
    radiant_nw = sum(radiant_nws)
    dire_nw = sum(dire_nws)
    assert snapshot.radiant_nw == radiant_nw == 22445
    assert snapshot.dire_nw == dire_nw == 21451
    assert snapshot.radiant_nw_adv == 994
    assert snapshot.deaths_radiant == 6
    assert snapshot.deaths_dire == 4
    assert snapshot.radiant_xp_adv == xp_advantage(LOL_LEVEL_XP, radiant_levels, dire_levels)
    assert snapshot.radiant_xp_adv == 400
    lol_top = build_top_player_features_over_total(radiant_nws, dire_nws)
    dota_top = build_top_player_features(radiant_nws, dire_nws)
    assert snapshot.top == lol_top
    assert snapshot.top.top1_nw_adv == 28
    assert snapshot.top.radiant_top1_nw_ratio == 5522 / 22445
    assert snapshot.top.dire_top1_nw_ratio == 5494 / 21451
    assert snapshot.top.radiant_top1_nw_ratio != dota_top.radiant_top1_nw_ratio
    assert snapshot.top.dire_top1_nw_ratio != dota_top.dire_top1_nw_ratio


def test_cblol_before_level_up_is_all_level_one_and_zero_xp() -> None:
    """Missing increaseLevel: parser levels are 1; both sides 0 XP; gold is not 5x500."""
    event = _scoreboard_then_table(
        _lol_feed(1, "Fluxo W7M", "LOS"),
        CBLOL_SCOREBOARD,
        CBLOL_SERIES_TABLE_BEFORE_LEVEL_UP,
        8,
        CBLOL_SERIES_ID,
    )
    assert event is not None
    snapshot = event.snapshot
    assert snapshot.radiant_nw == 2450
    assert snapshot.dire_nw == 2350
    assert snapshot.radiant_nw_adv == 100
    assert snapshot.deaths_radiant == 0
    assert snapshot.deaths_dire == 0
    assert snapshot.radiant_xp_adv == 0
    assert snapshot.top.top1_nw_adv == 0
    assert snapshot.top.radiant_top1_nw_ratio == 500 / 2450
    assert snapshot.top.dire_top1_nw_ratio == 500 / 2350


def test_lec_map_two_orients_blue_giantx_and_null_level_is_one() -> None:
    """LEC map 2: BLUE=GIANTX; Jun's null increaseLevel is level 1."""
    event = _scoreboard_then_table(
        _lol_feed(2, "GIANTX", "Natus Vincere"),
        LEC_SCOREBOARD,
        LEC_SERIES_TABLE,
        8,
        LEC_SERIES_ID,
    )
    assert event is not None
    snapshot = event.snapshot
    radiant_nws = [776, 620, 910, 862, 602]
    dire_nws = [829, 836, 904, 1013, 708]
    radiant_levels = [3, 2, 3, 2, 1]
    dire_levels = [3, 3, 2, 3, 2]
    radiant_nw = sum(radiant_nws)
    dire_nw = sum(dire_nws)
    assert snapshot.radiant_nw == radiant_nw == 3770
    assert snapshot.dire_nw == dire_nw == 4290
    assert snapshot.radiant_nw_adv == -520
    assert snapshot.deaths_radiant == 0
    assert snapshot.deaths_dire == 0
    assert snapshot.radiant_xp_adv == xp_advantage(LOL_LEVEL_XP, radiant_levels, dire_levels)
    assert snapshot.radiant_xp_adv == -660
    assert snapshot.top == build_top_player_features_over_total(radiant_nws, dire_nws)
    assert snapshot.top.top1_nw_adv == -103
    assert snapshot.top.radiant_top1_nw_ratio == 910 / 3770
    assert snapshot.top.dire_top1_nw_ratio == 1013 / 4290


def test_dota_profile_on_lol_board_stays_pending() -> None:
    """A Dota-profile reducer never locks BLUE/RED, so the table is silent."""
    feed = GridFrameReducer(1, "Fluxo W7M", "LOS", GAME_PROFILES["dota"])
    assert (
        _scoreboard_then_table(feed, CBLOL_SCOREBOARD, CBLOL_SERIES_TABLE, 8, CBLOL_SERIES_ID)
        is None
    )
