"""Oddin reducer: strict ticks, no zero-fill, archive replay, finish, reconnect."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared.utils.top_players import ZERO_TOP, build_top_player_features
from trader.archive_types import MatchMeta
from trader.grid_feed import received_at_utc
from trader.live_feed import FeedEvent, FeedSource, MatchPhase, unix_seconds_to_iso_z
from trader.oddin_archive import iter_oddin_archive_records, summarize
from trader.oddin_feed import (
    ODDIN_FEED_STALE_SECONDS,
    RECONNECT_EVENT,
    SNAPSHOT_EVENT,
    WS_EVENT,
    OddinSnapshotReducer,
    estimated_horn_unix,
    parse_oddin_timestamp,
    replay_oddin_records,
)
from trader.oddin_live_feed import OddinLiveFeed
from trader.oddin_types import OddinStateArchiveRecord
from trader.paths import ODDIN_STATE_ARCHIVE_FILENAME

MAP_ORDER = 3
RECEIVED = datetime(2026, 9, 19, 16, 57, 50, tzinfo=UTC)
UPDATED = "2026-09-19 16:57:34.676000000 +0000 UTC"
UPDATED_NEXT = "2026-09-19 16:57:35.141000000 +0000 UTC"
UPDATED_LATER = "2026-09-19 16:57:36.148000000 +0000 UTC"
UPDATED_AFTER_STALL = "2026-09-19 17:10:05.000000000 +0000 UTC"
UPDATED_AFTER_STALL_NEXT = "2026-09-19 17:10:06.000000000 +0000 UTC"


def _player(nickname: str, net_worth: int, deaths: int = 0) -> dict[str, object]:
    return {
        "player": {"nickname": nickname},
        "hero": {"name": "Kez"},
        "netWorth": net_worth,
        "kills": 0,
        "deaths": deaths,
        "assists": 0,
        "alive": True,
        "hasAegis": False,
    }


def _five(prefix: str, base_nw: int, deaths_base: int = 0) -> list[dict[str, object]]:
    return [_player(f"{prefix}{index}", base_nw + index, deaths_base + index) for index in range(5)]


def _side(
    name: str,
    faction: str,
    players: list[dict[str, object]],
    won: bool | None = None,
) -> dict[str, object]:
    team_nw = 0
    for player in players:
        parsed = player.get("netWorth")
        if isinstance(parsed, int):
            team_nw += parsed
    fields: dict[str, object] = {
        "team": {"name": name},
        "faction": faction,
        "kills": 0,
        "netWorth": 0,
        "netWorthNullable": team_nw,
        "towers": 0,
        "barracks": 0,
        "barracksNullable": 0,
        "roshans": 0,
        "players": players,
    }
    if won is not None:
        fields["won"] = won
    return fields


def _payload(
    *,
    map_order: int = MAP_ORDER,
    game_time: int = 998,
    status: str = "LIVE",
    data_status: str = "VALID_DATA",
    paused: bool = False,
    updated: str = UPDATED,
    map_id: str = "map-3",
    minimal: bool = False,
    previous: list[dict[str, object]] | None = None,
    radiant_players: list[dict[str, object]] | None = None,
    dire_players: list[dict[str, object]] | None = None,
    home_faction: str = "DIRE",
) -> dict[str, object]:
    radiant = radiant_players if radiant_players is not None else _five("R", 5000, 0)
    dire = dire_players if dire_players is not None else _five("D", 7000, 1)
    away_faction = "RADIANT" if home_faction == "DIRE" else "DIRE"
    payload: dict[str, object] = {
        "matchStatus": status,
        "dataStatus": data_status,
        "lastUpdatedAt": updated,
        "mapPaused": paused,
        "minimal": minimal,
        "homeScore": 1,
        "awayScore": 1,
        "homeTeam": {"name": "LGD Gaming"},
        "awayTeam": {"name": "Yakult's Brothers"},
        "currentMap": {
            "id": map_id,
            "mapOrder": map_order,
            "gameTime": game_time,
            "homeTeam": _side(
                "LGD Gaming", home_faction, dire if home_faction == "DIRE" else radiant
            ),
            "awayTeam": _side(
                "Yakult's Brothers", away_faction, radiant if away_faction == "RADIANT" else dire
            ),
        },
    }
    if previous is not None:
        payload["previousMaps"] = previous
    return payload


def _reducer() -> OddinSnapshotReducer:
    return OddinSnapshotReducer(MAP_ORDER, True)


def _apply(
    reducer: OddinSnapshotReducer,
    payload: dict[str, object],
    received: datetime = RECEIVED,
) -> FeedEvent | None:
    return reducer.apply_payload(payload, received)


def _phase_of(reducer: OddinSnapshotReducer, payload: dict[str, object]) -> MatchPhase:
    event = _apply(reducer, payload)
    assert event is not None
    return event.snapshot.phase


def test_parse_go_timestamp_with_nanos_and_iso() -> None:
    go = parse_oddin_timestamp("2026-09-19 17:16:15.866807591 +0000 UTC")
    assert go is not None
    assert go == datetime(2026, 9, 19, 17, 16, 15, 866807, tzinfo=UTC)
    iso = parse_oddin_timestamp("2026-09-19T17:16:15.866807Z")
    assert iso == go.replace(microsecond=866807)


def test_home_dire_live_tick_sums_player_nw_and_zeros_xp() -> None:
    event = _apply(_reducer(), _payload())
    assert event is not None
    assert event.source is FeedSource.ODDIN
    snapshot = event.snapshot
    radiant_nws = [5000 + i for i in range(5)]
    dire_nws = [7000 + i for i in range(5)]
    assert snapshot.second == 998
    assert snapshot.phase is MatchPhase.IN_PROGRESS
    assert snapshot.radiant_nw == sum(radiant_nws)
    assert snapshot.dire_nw == sum(dire_nws)
    assert snapshot.radiant_nw_adv == snapshot.radiant_nw - snapshot.dire_nw
    assert snapshot.radiant_xp_adv == 0
    assert snapshot.deaths_radiant == sum(range(5))
    assert snapshot.deaths_dire == sum(i + 1 for i in range(5))
    assert snapshot.top == build_top_player_features(radiant_nws, dire_nws)
    assert snapshot.paused is False
    updated = parse_oddin_timestamp(UPDATED)
    assert updated is not None
    assert snapshot.server_timestamp == int(updated.timestamp())
    assert event.horn_unix_seconds == estimated_horn_unix(updated, 998)


def test_stale_http_map2_is_not_a_tick_for_map3() -> None:
    reducer = _reducer()
    seed = _payload(map_order=2, map_id="map-2", game_time=-82, updated=UPDATED)
    assert _apply(reducer, seed) is None
    live = _apply(reducer, _payload(updated=UPDATED_NEXT))
    assert live is not None
    assert live.snapshot.second == 998


def test_missing_player_or_nw_or_deaths_is_not_zero_filled() -> None:
    reducer = _reducer()
    four = _five("R", 5000)[:4]
    assert _apply(reducer, _payload(radiant_players=four)) is None
    missing_nw = _five("R", 5000)
    del missing_nw[0]["netWorth"]
    assert _apply(reducer, _payload(radiant_players=missing_nw, updated=UPDATED_NEXT)) is None
    missing_deaths = _five("D", 7000, 1)
    del missing_deaths[0]["deaths"]
    assert _apply(reducer, _payload(dire_players=missing_deaths, updated=UPDATED_LATER)) is None


def test_invalid_data_and_bool_fields_are_not_ticks() -> None:
    reducer = _reducer()
    assert _apply(reducer, _payload(data_status="NO_DATA")) is None
    bool_deaths = _five("R", 5000)
    bool_deaths[0]["deaths"] = True
    assert _apply(reducer, _payload(radiant_players=bool_deaths, updated=UPDATED_NEXT)) is None


def test_minimal_payload_with_full_sides_is_a_tick() -> None:
    event = _apply(_reducer(), _payload(minimal=True))
    assert event is not None
    assert event.snapshot.second == 998
    assert event.snapshot.radiant_nw == sum(5000 + i for i in range(5))


def test_duplicate_nickname_is_not_five_unique_players() -> None:
    players = _five("R", 5000)
    players[1]["player"] = {"nickname": "R0"}
    assert _apply(_reducer(), _payload(radiant_players=players)) is None


def test_duplicate_timestamp_does_not_refresh_and_out_of_order_is_dropped() -> None:
    reducer = _reducer()
    first = _apply(reducer, _payload(game_time=998))
    assert first is not None
    assert _apply(reducer, _payload(game_time=999, updated=UPDATED)) is None
    older = "2026-09-19 16:57:30.000000000 +0000 UTC"
    assert _apply(reducer, _payload(game_time=1000, updated=older)) is None


def test_same_second_updates_and_pause_flag() -> None:
    reducer = _reducer()
    first = _apply(reducer, _payload(game_time=998, paused=False))
    second = _apply(reducer, _payload(game_time=998, paused=True, updated=UPDATED_NEXT))
    assert first is not None and second is not None
    assert second.snapshot.second == 998
    assert second.snapshot.paused is True
    resumed = _apply(reducer, _payload(game_time=999, paused=False, updated=UPDATED_LATER))
    assert resumed is not None
    assert resumed.snapshot.paused is False
    assert resumed.snapshot.second == 999


def test_horn_freezes_on_first_positive_clock() -> None:
    reducer = _reducer()
    first = _apply(reducer, _payload(game_time=10))
    moved_at = RECEIVED.replace(second=55)
    moved = reducer.apply_payload(_payload(game_time=11, updated=UPDATED_NEXT), moved_at)
    later = RECEIVED.replace(second=58)
    after = reducer.apply_payload(_payload(game_time=11, updated=UPDATED_LATER), later)
    assert first is not None and moved is not None and after is not None
    first_stamp = parse_oddin_timestamp(UPDATED)
    assert first_stamp is not None
    assert first.horn_unix_seconds == estimated_horn_unix(first_stamp, 10)
    assert moved.horn_unix_seconds == first.horn_unix_seconds
    assert after.horn_unix_seconds == first.horn_unix_seconds


def test_horn_keeps_moving_until_clock_is_positive() -> None:
    reducer = _reducer()
    stalled = _apply(reducer, _payload(game_time=-90))
    nudged = _apply(reducer, _payload(game_time=-89, updated=UPDATED_NEXT))
    live = _apply(reducer, _payload(game_time=1, updated=UPDATED_AFTER_STALL))
    later = _apply(reducer, _payload(game_time=2, updated=UPDATED_AFTER_STALL_NEXT))
    assert stalled is not None and nudged is not None and live is not None and later is not None
    stall_stamp = parse_oddin_timestamp(UPDATED)
    nudge_stamp = parse_oddin_timestamp(UPDATED_NEXT)
    live_stamp = parse_oddin_timestamp(UPDATED_AFTER_STALL)
    assert stall_stamp is not None and nudge_stamp is not None and live_stamp is not None
    assert stalled.horn_unix_seconds == estimated_horn_unix(stall_stamp, -90)
    assert nudged.horn_unix_seconds == estimated_horn_unix(nudge_stamp, -89)
    assert live.horn_unix_seconds == estimated_horn_unix(live_stamp, 1)
    assert live.horn_unix_seconds != nudged.horn_unix_seconds
    assert later.horn_unix_seconds == live.horn_unix_seconds


def test_first_negative_frame_is_pre_match() -> None:
    assert _phase_of(_reducer(), _payload(game_time=-35)) is MatchPhase.PRE_MATCH


def test_stuck_negative_clock_stays_pre_match() -> None:
    reducer = _reducer()
    assert _phase_of(reducer, _payload(game_time=-35)) is MatchPhase.PRE_MATCH
    assert _phase_of(reducer, _payload(game_time=-35, updated=UPDATED_NEXT)) is MatchPhase.PRE_MATCH


def test_rising_negative_clock_starts_pre_horn_and_holds() -> None:
    reducer = _reducer()
    assert _phase_of(reducer, _payload(game_time=-35)) is MatchPhase.PRE_MATCH
    assert _phase_of(reducer, _payload(game_time=-34, updated=UPDATED_NEXT)) is MatchPhase.PRE_HORN
    assert _phase_of(reducer, _payload(game_time=-34, updated=UPDATED_LATER)) is MatchPhase.PRE_HORN
    assert (
        _phase_of(reducer, _payload(game_time=-33, updated=UPDATED_AFTER_STALL))
        is MatchPhase.PRE_HORN
    )


def test_reset_keeps_started_countdown() -> None:
    reducer = _reducer()
    assert _phase_of(reducer, _payload(game_time=-35)) is MatchPhase.PRE_MATCH
    assert _phase_of(reducer, _payload(game_time=-34, updated=UPDATED_NEXT)) is MatchPhase.PRE_HORN
    reducer.reset()
    assert _phase_of(reducer, _payload(game_time=-34, updated=UPDATED_LATER)) is MatchPhase.PRE_HORN


def test_map_order_increase_finishes_with_zero_features() -> None:
    reducer = _reducer()
    live = _apply(reducer, _payload(game_time=2000))
    finished = _apply(
        reducer, _payload(map_order=4, map_id="map-4", game_time=-90, updated=UPDATED_NEXT)
    )
    assert live is not None and finished is not None
    assert finished.snapshot.phase is MatchPhase.FINISHED
    assert finished.snapshot.second == live.snapshot.second
    assert finished.snapshot.radiant_nw == 0
    assert finished.snapshot.dire_nw == 0
    assert finished.snapshot.radiant_xp_adv == 0
    assert finished.snapshot.top == ZERO_TOP
    assert finished.snapshot.finished is True


def _finished_previous(*, dire_won: bool) -> list[dict[str, object]]:
    home = _side("LGD Gaming", "DIRE", [_player(f"d{i}", 0) for i in range(5)], won=dire_won)
    away = _side(
        "Yakult's Brothers", "RADIANT", [_player(f"r{i}", 0) for i in range(5)], won=not dire_won
    )
    home["netWorthNullable"] = 40000
    away["netWorthNullable"] = 30000
    return [{"id": "map-3", "mapOrder": 3, "homeTeam": home, "awayTeam": away}]


def test_previous_maps_with_zero_player_nw_still_set_winner() -> None:
    reducer = _reducer()
    live = _apply(reducer, _payload(map_id="map-3", game_time=1800))
    finished = _apply(
        reducer,
        _payload(
            map_order=4,
            map_id="map-4",
            game_time=-80,
            updated=UPDATED_NEXT,
            previous=_finished_previous(dire_won=True),
        ),
    )
    assert live is not None and finished is not None
    assert finished.snapshot.finished is True
    assert reducer.winner == "dire"


def test_match_status_finished_is_terminal() -> None:
    reducer = _reducer()
    live = _apply(reducer, _payload())
    finished = _apply(
        reducer,
        _payload(status="FINISHED", map_order=MAP_ORDER, updated=UPDATED_NEXT),
    )
    assert live is not None and finished is not None
    assert finished.snapshot.finished is True
    assert _apply(reducer, _payload(updated=UPDATED_LATER)) is None


def test_reconnect_keeps_horn_and_still_accepts_the_next_snapshot() -> None:
    reducer = _reducer()
    first = _apply(reducer, _payload(game_time=10))
    moved = _apply(reducer, _payload(game_time=11, updated=UPDATED_NEXT))
    reducer.reset()
    after = _apply(reducer, _payload(game_time=12, updated=UPDATED_LATER))
    assert first is not None and moved is not None and after is not None
    assert after.horn_unix_seconds == moved.horn_unix_seconds
    assert after.snapshot.second == 12


def test_seed_skipped_replay_matches_live_reduce() -> None:
    seed = _payload(map_order=2, map_id="map-2", game_time=-82)
    live_a = _payload(updated=UPDATED_NEXT, game_time=998)
    live_b = _payload(updated=UPDATED_LATER, game_time=999)
    received = received_at_utc(RECEIVED)
    records: list[OddinStateArchiveRecord] = [
        {"received_at_utc": received, "event": SNAPSHOT_EVENT, "payload": seed},
        {"received_at_utc": received, "event": WS_EVENT, "payload": live_a},
        {"received_at_utc": received, "event": WS_EVENT, "payload": live_b},
    ]
    live_reducer = _reducer()
    live_events = [
        event
        for payload in (live_a, live_b)
        if (event := live_reducer.apply_payload(payload, RECEIVED)) is not None
    ]
    replayed = list(replay_oddin_records(records, _reducer()))
    assert [event.snapshot.second for event in replayed] == [998, 999]
    assert [event.snapshot.radiant_nw for event in replayed] == [
        event.snapshot.radiant_nw for event in live_events
    ]
    assert replayed[0].source is FeedSource.ODDIN


def test_replay_reconnect_and_stop_on_finished() -> None:
    received = received_at_utc(RECEIVED)
    later = received_at_utc(RECEIVED.replace(second=51))
    records: list[OddinStateArchiveRecord] = [
        {"received_at_utc": received, "event": WS_EVENT, "payload": _payload()},
        {"received_at_utc": received, "event": RECONNECT_EVENT, "payload": None},
        {
            "received_at_utc": later,
            "event": WS_EVENT,
            "payload": _payload(status="FINISHED", updated=UPDATED_NEXT),
        },
        {"received_at_utc": later, "event": WS_EVENT, "payload": _payload(updated=UPDATED_LATER)},
    ]
    events = list(replay_oddin_records(records, _reducer()))
    assert len(events) == 2
    assert events[0].snapshot.phase is MatchPhase.IN_PROGRESS
    assert events[1].snapshot.finished is True


def _meta() -> MatchMeta:
    return {
        "schema_version": 7,
        "match_id": "9006715157",
        "steam_match_id": "9006715157",
        "server_steam_id": None,
        "league_id": None,
        "tournament": None,
        "teams": {"radiant": "Yakult's Brothers", "dire": "LGD Gaming"},
        "map_number": MAP_ORDER,
        "joined_at_second": 0,
        "joined_at_utc": "2026-09-19T16:57:49Z",
        "horn_at_utc": "2026-09-19T16:40:56Z",
        "market": {
            "condition_id": "c",
            "market_slug": "s",
            "event_slug": "e",
            "yes_token_id": "y",
            "no_token_id": "n",
            "yes_is_radiant": True,
            "outcome_0_name": "Yakult's Brothers",
            "outcome_1_name": "LGD Gaming",
            "tick_size": None,
            "min_order_size": None,
            "neg_risk": False,
            "grid_series_id": None,
        },
        "model": {"name": "x", "trained_at": "t"},
        "feed_source": "oddin",
        "steam_delay_s": None,
        "steam_observed_lag_s": None,
        "grid_delay_s": None,
        "final": None,
    }


def test_summarize_replay_and_winner(tmp_path: Path) -> None:
    previous = _finished_previous(dire_won=True)
    received = received_at_utc(RECEIVED)
    lines = [
        json.dumps(
            {"received_at_utc": received, "event": SNAPSHOT_EVENT, "payload": _payload(map_order=2)}
        )
        + "\n",
        json.dumps(
            {
                "received_at_utc": received,
                "event": WS_EVENT,
                "payload": _payload(game_time=-90),
            }
        )
        + "\n",
        json.dumps(
            {
                "received_at_utc": received,
                "event": WS_EVENT,
                "payload": _payload(updated=UPDATED_NEXT),
            }
        )
        + "\n",
        json.dumps(
            {
                "received_at_utc": received,
                "event": WS_EVENT,
                "payload": _payload(
                    map_order=4, map_id="map-4", updated=UPDATED_LATER, previous=previous
                ),
            }
        )
        + "\n",
    ]
    archive_path = tmp_path / ODDIN_STATE_ARCHIVE_FILENAME
    archive_path.write_text("".join(lines), encoding="utf-8")
    rows = list(iter_oddin_archive_records(archive_path))
    assert len(rows) == 4
    outcome = summarize(tmp_path, _meta())
    assert outcome.summary.snapshot_count == 3
    assert outcome.summary.finished is True
    assert outcome.summary.winner == "dire"
    assert outcome.summary.duration_seconds == 998
    live_stamp = parse_oddin_timestamp(UPDATED_NEXT)
    assert live_stamp is not None
    assert outcome.trusted_horn == unix_seconds_to_iso_z(estimated_horn_unix(live_stamp, 998))


def test_malformed_archive_line_raises(tmp_path: Path) -> None:
    path = tmp_path / ODDIN_STATE_ARCHIVE_FILENAME
    path.write_text("{not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed archive record"):
        list(iter_oddin_archive_records(path))


def _keep_open(_oddin_match_id: str, _map_number: int) -> bool:
    return False


def test_oddin_live_feed_uses_fifteen_second_stale() -> None:
    feed = OddinLiveFeed("od:match:3211331", MAP_ORDER, "9006715157", True, _keep_open)
    assert feed.source is FeedSource.ODDIN
    assert feed.stale_seconds == ODDIN_FEED_STALE_SECONDS == 15.0
