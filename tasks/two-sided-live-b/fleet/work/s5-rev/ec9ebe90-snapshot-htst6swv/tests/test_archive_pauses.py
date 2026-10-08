"""`pauses_from_schedule` reconstructs OpenDota-shape pauses from paused-tick runs."""

from archive_index.schedule import (
    FeedSchedule,
    ScheduleIdentity,
    ScheduleStats,
    ScheduleTick,
    pauses_from_schedule,
)


def tick(seq: int, game_second: int, paused: bool, source_ts_unix: int) -> ScheduleTick:
    return ScheduleTick(
        seq=seq,
        received_at_utc="2026-01-01T00:00:00+00:00",
        received_ns=0,
        game_second=game_second,
        phase="live",
        paused=paused,
        terminal=False,
        horn_unix_seconds=0,
        source_ts_unix=source_ts_unix,
        radiant_nw_adv=0,
        radiant_nw=0,
        dire_nw=0,
        radiant_xp_adv=0,
        deaths_radiant=0,
        deaths_dire=0,
        top1_nw_adv=0,
        radiant_top1_nw_ratio=0.0,
        dire_top1_nw_ratio=0.0,
        top3_nw_adv=0,
        radiant_top3_nw_ratio=0.0,
        dire_top3_nw_ratio=0.0,
    )


def make_schedule(ticks: list[ScheduleTick]) -> FeedSchedule:
    identity = ScheduleIdentity(
        game="dota",
        archive_id="a-1",
        match_id="m-1",
        feed_source="grid",
        condition_id="cond",
        market_slug="slug",
        event_slug="event",
        event_id="e-1",
        map_number=1,
        steam_match_id="123",
        yes_token_id="yes",
        no_token_id="no",
        yes_is_radiant=True,
        yes_token_index=0,
        joined_at_utc="2026-01-01T00:00:00+00:00",
        joined_at_second=0,
        horn_at_utc="2026-01-01T00:01:30+00:00",
    )
    stats = ScheduleStats(
        tick_count=len(ticks),
        interruption_count=0,
        terminal=False,
        terminal_interrupted=False,
        first_received_at_utc=None,
        last_received_at_utc=None,
        first_game_second=None,
        last_game_second=None,
        window_ticks=0,
        warmup_ticks=0,
        paused_ticks=0,
        max_arrival_gap_seconds=None,
        delay_s=None,
        delay_evidence="",
    )
    return FeedSchedule(
        schema_version=1,
        rules_version="r1",
        admission_rules_version="a1",
        max_feed_delay_seconds=0,
        fingerprint="fp",
        identity=identity,
        stats=stats,
        interruptions=(),
        kill_gates=(),
        match_json_sha256="",
        feed_sha256="",
        feed_file="feed.jsonl",
        feed_size=0,
        ticks=tuple(ticks),
    )


def test_pause_run_becomes_one_pause() -> None:
    """A maximal paused run is one pause; duration brackets it by server stamps."""
    schedule = make_schedule(
        [
            tick(0, 100, paused=False, source_ts_unix=1_000),
            tick(1, 100, paused=True, source_ts_unix=1_010),
            tick(2, 100, paused=True, source_ts_unix=1_020),
            tick(3, 100, paused=False, source_ts_unix=1_050),
            tick(4, 101, paused=False, source_ts_unix=1_051),
        ]
    )

    assert pauses_from_schedule(schedule) == [{"time": 100, "duration": 50}]


def test_prehorn_pause_keeps_negative_game_second() -> None:
    """A pause observed before game time 0 keeps its negative `time`."""
    schedule = make_schedule(
        [
            tick(0, -40, paused=False, source_ts_unix=1_000),
            tick(1, -30, paused=True, source_ts_unix=1_010),
            tick(2, -30, paused=False, source_ts_unix=1_020),
            tick(3, -29, paused=False, source_ts_unix=1_021),
        ]
    )

    assert pauses_from_schedule(schedule) == [{"time": -30, "duration": 20}]


def test_leading_pause_is_unknown_not_empty() -> None:
    """A record that opens already paused never pretends to be pause-free."""
    schedule = make_schedule(
        [
            tick(0, 100, paused=True, source_ts_unix=1_000),
            tick(1, 100, paused=False, source_ts_unix=1_010),
        ]
    )

    assert pauses_from_schedule(schedule) is None


def test_unterminated_pause_is_unknown_not_empty() -> None:
    """A pause run open at record end never pretends to be pause-free."""
    schedule = make_schedule(
        [
            tick(0, 100, paused=False, source_ts_unix=1_000),
            tick(1, 100, paused=True, source_ts_unix=1_010),
        ]
    )

    assert pauses_from_schedule(schedule) is None


def test_pause_free_record_returns_empty_list() -> None:
    """A complete run-free record means 'observed: no pauses'."""
    schedule = make_schedule(
        [
            tick(0, 0, paused=False, source_ts_unix=1_000),
            tick(1, 60, paused=False, source_ts_unix=1_060),
        ]
    )

    assert pauses_from_schedule(schedule) == []


def test_two_runs_become_two_pauses() -> None:
    schedule = make_schedule(
        [
            tick(0, 10, paused=False, source_ts_unix=1_000),
            tick(1, 10, paused=True, source_ts_unix=1_010),
            tick(2, 10, paused=False, source_ts_unix=1_020),
            tick(3, 50, paused=True, source_ts_unix=1_060),
            tick(4, 50, paused=False, source_ts_unix=1_100),
        ]
    )

    assert pauses_from_schedule(schedule) == [
        {"time": 10, "duration": 20},
        {"time": 50, "duration": 80},
    ]
