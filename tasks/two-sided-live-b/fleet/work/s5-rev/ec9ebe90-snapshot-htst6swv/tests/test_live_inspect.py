"""Live inspector: journal tape, Steam state, live-only catalog, shared plotter."""

# pyright: reportPrivateUsage=false, reportUnknownVariableType=false

import json
from pathlib import Path
from typing import Any

import pytest

import backtest.inspect.app as backtest_app
import backtest.inspect.tape as backtest_tape
import viewer.plot as shared_plot
import viewer.types as shared_types
from shared.utils.dota_levels import radiant_xp_advantage
from viewer.game_state_replay import FeedClock
from viewer.live_tape import list_live_matches, load_live_game_state, load_live_tapes

YES_TOKEN = "yes-1"
NO_TOKEN = "no-1"


def _write_match(root: Path, match_id: str, joined: str, game: str | None, mode: str) -> Path:
    """One fixture archive dir with a minimal match.json."""
    archive_dir = root / match_id
    archive_dir.mkdir(parents=True)
    document: dict[str, Any] = {
        "match_id": match_id,
        "market": {
            "market_slug": f"slug-{match_id}",
            "yes_token_id": YES_TOKEN,
            "no_token_id": NO_TOKEN,
            "yes_is_radiant": True,
        },
        "joined_at_utc": joined,
        "feed_source": "steam",
        "horn_at_utc": "2026-09-01T00:00:00Z",
    }
    if game is not None:
        document["game"] = game
    (archive_dir / "match.json").write_text(json.dumps(document))
    (archive_dir / "session.jsonl").write_text(
        json.dumps({"kind": "session_start", "execution_mode": mode}) + "\n"
    )
    return archive_dir


def _write_traded_match(root: Path) -> Path:
    """Live match with book signals, one quote, one fill, one late fill, and a session end."""
    archive_dir = _write_match(root, "live-test-1", "2026-09-01T00:00:00Z", "dota", "live")
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "second": 76,
            "yes_best_bid": 0.50,
            "yes_best_ask": 0.52,
            "yes_mid": 0.51,
            "no_best_bid": 0.48,
            "no_best_ask": 0.50,
            "no_mid": 0.49,
            "market_p_radiant": 0.51,
            "radiant_fair": 0.55,
        },
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 76,
            "yes_best_bid": 0.55,
            "yes_best_ask": 0.57,
            "yes_mid": 0.56,
            "no_best_bid": 0.43,
            "no_best_ask": 0.45,
            "no_mid": 0.44,
            "market_p_radiant": 0.56,
            "radiant_fair": 0.60,
        },
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 77,
            "yes_best_bid": 0.56,
            "yes_best_ask": 0.58,
            "yes_mid": 0.57,
            "no_best_bid": 0.42,
            "no_best_ask": 0.44,
            "no_mid": 0.43,
            "market_p_radiant": 0.57,
            "radiant_fair": 0.58,
        },
        {
            "kind": "quote",
            "venue": "kalshi",
            "second": 77,
            "placed": [{"token_id": YES_TOKEN, "side": "BUY", "price": 0.99, "size": 1.0}],
            "canceled": [],
        },
        {
            "kind": "quote",
            "venue": "polymarket",
            "second": 76,
            "placed": [{"token_id": YES_TOKEN, "side": "BUY", "price": 0.55, "size": 10.0}],
            "canceled": [],
        },
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.55,
            "size": 10.0,
            "second": 80,
            "fill_key": "k1",
        },
        {
            "kind": "late_fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.56,
            "size": 5.0,
            "second": 0,
            "fill_key": "k2",
        },
        {"kind": "session_end", "equity": 2.5, "net_cash": 1.5},
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    trace: list[dict[str, Any]] = [
        {
            "kind": "header",
            "schema_version": 1,
            # horn unix + 60s vs monotonic 60s -> offset 0, plot = now_ns / 1e9
            "opened_wall_s": 1788220860.0,
            "opened_now_ns": 60_000_000_000,
        },
        {
            "kind": "event",
            "event": {"type": "Wake"},
            "plan": {
                "places": [
                    {
                        "order_id": "c0",
                        "token_index": 0,
                        "side": "BUY",
                        "price": 0.55,
                        "quantity": 10.0,
                        "level_index": 0,
                    }
                ],
                "cancels": [],
            },
        },
        {
            "kind": "event",
            "event": {"type": "OrderAccepted", "order_id": "c0"},
            "plan": {"places": [], "cancels": []},
        },
        {
            "kind": "event",
            "event": {
                "type": "Fill",
                "fill_id": "k1",
                "order_id": "c0",
                "qty": 10.0,
                "price": 0.55,
            },
            "plan": {"places": [], "cancels": []},
        },
    ]
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(json.dumps(record) for record in trace) + "\n"
    )
    return archive_dir


def _write_steam_state(archive_dir: Path) -> None:
    """One state.jsonl tick: 10k vs 9k net worth, 5 Radiant deaths.

    Horn is 00:00:00Z (unix 1788220800). The three candidate X axes are
    deliberately distinct: game_time 100, server stamp start+timestamp = 115s
    from horn, receipt at 130s from horn — X is the receipt.
    """
    players = [
        {"level": 5, "death_count": 1, "net_worth": 2000},
        {"level": 5, "death_count": 1, "net_worth": 2000},
        {"level": 5, "death_count": 1, "net_worth": 2000},
        {"level": 5, "death_count": 1, "net_worth": 2000},
        {"level": 5, "death_count": 1, "net_worth": 2000},
    ]
    dire_players = [
        {"level": 4, "death_count": 0, "net_worth": 1800},
        {"level": 4, "death_count": 0, "net_worth": 1800},
        {"level": 4, "death_count": 0, "net_worth": 1800},
        {"level": 4, "death_count": 0, "net_worth": 1800},
        {"level": 4, "death_count": 0, "net_worth": 1800},
    ]
    record = {
        "request_started_at_utc": "2026-09-01T00:02:10Z",
        "received_at_utc": "2026-09-01T00:02:10Z",
        "payload": {
            "match": {
                "game_time": 100,
                "timestamp": 200,
                "start_timestamp": 1788220715,
                "game_state": 5,
            },
            "teams": [
                {"team_number": 2, "score": 5, "net_worth": 10000, "players": players},
                {"team_number": 3, "score": 0, "net_worth": 9000, "players": dire_players},
            ],
        },
    }
    (archive_dir / "state.jsonl").write_text(json.dumps(record) + "\n")


def test_live_tape_book_fair_pred_submit_fill(tmp_path: Path) -> None:
    """Journal signals become book/fair/pred series; quote and fills become markers."""
    archive_dir = _write_traded_match(tmp_path)
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    tape = tapes[0]
    assert tape.token_index == 0
    assert tape.side_name == "radiant"
    assert tape.bid.seconds == (76.0, 77.0)
    assert tape.bid.y == (0.55, 0.56)
    assert tape.ask.y == (0.57, 0.58)
    assert tape.mid.y == (0.56, 0.57)
    assert tape.fair.y == (0.60, 0.58)
    assert tape.pred_cents.y == pytest.approx((4.0, 1.0))
    assert [(mark.second, mark.price, mark.side) for mark in tape.submits] == [(76.0, 0.55, "BUY")]
    assert tape.submits[0].level_index == 0
    assert [mark.price for mark in tape.fills] == [0.55, 0.56]
    assert tape.fills[0].second == 80.0
    assert tape.fills[1].second == 80.0
    assert len(tape.segments) == 1
    assert tape.segments[0].order_id == "c0"
    assert tape.segments[0].level_index == 0
    assert tape.segments[0].start_s == 76.0
    assert tape.segments[0].end_s == 80.0


def test_live_game_state_from_steam_snapshot(tmp_path: Path) -> None:
    """One Steam tick becomes gold, top-1 advantage, and deaths on the receipt wall."""
    archive_dir = _write_traded_match(tmp_path)
    match = json.loads((archive_dir / "match.json").read_text())
    match["horn_at_utc"] = "2026-09-01T00:00:00Z"
    (archive_dir / "match.json").write_text(json.dumps(match))
    _write_steam_state(archive_dir)
    state = load_live_game_state(archive_dir)
    assert state.seconds == (130.0,)  # received_at_utc - horn, not server stamp 115
    assert state.game_seconds == (100.0,)  # game_time stays for hover
    assert state.radiant_nw == (10000.0,)
    assert state.dire_nw == (9000.0,)
    assert state.radiant_nw_adv == (1000.0,)
    assert state.top1_nw_adv == (200.0,)
    assert state.radiant_xp_adv == (float(radiant_xp_advantage([5] * 5, [4] * 5)),)
    assert state.deaths_radiant == (5.0,)
    assert state.deaths_dire == (0.0,)


def test_list_live_matches_live_only_freshest_first(tmp_path: Path) -> None:
    """Paper rows are out; missing game is dota; equity comes from session_end."""
    old_live = _write_match(tmp_path, "aaa", "2026-09-01T00:00:00Z", None, "live")
    (old_live / "session.jsonl").write_text(
        json.dumps({"kind": "session_start", "execution_mode": "live"})
        + "\n"
        + json.dumps({"kind": "session_end", "equity": 1.5, "net_cash": 1.5})
        + "\n"
    )
    _write_match(tmp_path, "bbb", "2026-09-02T00:00:00Z", "dota", "paper")
    _write_match(tmp_path, "ccc", "2026-09-03T00:00:00Z", "lol", "live")
    matches = list_live_matches(tmp_path)
    assert [match.match_id for match in matches] == ["ccc", "aaa"]
    assert [match.game for match in matches] == ["lol", "dota"]
    assert [match.equity for match in matches] == [None, 1.5]


def _write_quoted_match(root: Path, match_id: str) -> Path:
    """Live match with one placed quote and no core trace."""
    archive_dir = _write_match(root, match_id, "2026-09-01T00:00:00Z", "dota", "live")
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "quote",
            "venue": "polymarket",
            "second": 76,
            "placed": [{"token_id": YES_TOKEN, "side": "BUY", "price": 0.55, "size": 10.0}],
            "canceled": [],
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    return archive_dir


def test_live_tape_without_core_trace_has_no_segments(tmp_path: Path) -> None:
    """No trace: the quoted token still tapes, with gray submits and no rests."""
    tapes = load_live_tapes(_write_quoted_match(tmp_path, "no-trace"))
    assert len(tapes) == 1
    assert tapes[0].token_index == 0
    assert len(tapes[0].submits) == 1
    assert tapes[0].submits[0].level_index == -1
    assert tapes[0].segments == ()


def test_live_tape_with_drifted_core_trace_has_no_segments(tmp_path: Path) -> None:
    """Core/journal mismatch: markers survive, rests fall back to none."""
    archive_dir = _write_quoted_match(tmp_path, "drifted")
    trace: list[dict[str, Any]] = [
        {
            "kind": "header",
            "schema_version": 1,
            "opened_wall_s": 1788220860.0,
            "opened_now_ns": 60_000_000_000,
        },
        {
            "kind": "event",
            "event": {"type": "Wake"},
            "plan": {
                "places": [
                    {
                        "order_id": "c0",
                        "token_index": 0,
                        "side": "BUY",
                        "price": 0.99,
                        "quantity": 10.0,
                        "level_index": 0,
                    }
                ],
                "cancels": [],
            },
        },
    ]
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(json.dumps(record) for record in trace) + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert len(tapes[0].submits) == 1
    assert tapes[0].submits[0].level_index == -1
    assert tapes[0].segments == ()


def test_backtest_inspect_reexports_shared_viewer() -> None:
    """Backtest app/tape names are the shared viewer objects, not copies."""
    assert backtest_app.plot_match is shared_plot.plot_match
    assert backtest_app.plot_token_tape is shared_plot.plot_token_tape
    assert backtest_app.plot_game_state is shared_plot.plot_game_state
    assert backtest_tape.TokenTape is shared_types.TokenTape
    assert backtest_tape.GameState is shared_types.GameState
    assert backtest_tape.Series is shared_types.Series
    assert backtest_tape.last_per_second is shared_types.last_per_second


def test_fill_markers_use_ts_utc_seconds_from_horn(tmp_path: Path) -> None:
    """Fill X is exchange ts_utc - horn_at_utc, not the stale journal second."""
    archive_dir = _write_match(tmp_path, "ts-utc", "2026-09-01T00:00:00Z", "dota", "live")
    match = json.loads((archive_dir / "match.json").read_text())
    match["horn_at_utc"] = "2026-09-01T00:00:00Z"
    (archive_dir / "match.json").write_text(json.dumps(match))
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.42,
            "size": 1.72,
            "second": 182,
            "ts_utc": "2026-09-01T00:03:03.355Z",
            "fill_key": "a",
        },
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "SELL",
            "price": 0.43,
            "size": 71.41,
            "second": 182,
            "ts_utc": "2026-09-01T00:03:27.097Z",
            "fill_key": "b",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    fills = tapes[0].fills
    assert len(fills) == 2
    assert fills[0].side == "BUY"
    assert fills[0].second == pytest.approx(183.355)
    assert fills[1].side == "SELL"
    assert fills[1].second == pytest.approx(207.097)


def test_spread_fill_xs_nudges_same_second_and_price() -> None:
    """Overlapping fills shift right on X so both triangles stay visible."""
    nudge = shared_plot.FILL_X_NUDGE_S
    xs_same, _ = shared_plot._spread_marker_xs([182.0, 182.0], [0.42, 0.42])
    assert xs_same == [182.0, 182.0 + nudge]
    xs_apart, _ = shared_plot._spread_marker_xs([182.0, 207.0], [0.42, 0.43])
    assert xs_apart == [182.0, 207.0]
    # Partial SELL fills a fraction of a second apart still share the bucket.
    xs_partial, _ = shared_plot._spread_marker_xs([382.01, 382.40], [0.58, 0.58])
    assert xs_partial == [382.01, 382.40 + nudge]
    # SELL submit then SELL fill at the same second/price: fill nudges right.
    xs_submit, seen = shared_plot._spread_marker_xs([234.0], [0.47])
    xs_fill, _seen = shared_plot._spread_marker_xs([234.0], [0.47], seen)
    assert xs_submit == [234.0]
    assert xs_fill == [234.0 + nudge]


def test_sell_segment_end_uses_ts_utc(tmp_path: Path) -> None:
    """A SELL resting line spans submit journal second to exchange fill time."""
    archive_dir = _write_match(tmp_path, "sell-line", "2026-09-01T00:00:00Z", "dota", "live")
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "quote",
            "venue": "polymarket",
            "second": 182,
            "placed": [{"token_id": YES_TOKEN, "side": "SELL", "price": 0.43, "size": 71.41}],
            "canceled": [],
        },
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "SELL",
            "price": 0.43,
            "size": 71.41,
            "second": 182,
            "ts_utc": "2026-09-01T00:03:27.097Z",
            "fill_key": "k-sell",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220860.0,
                    "opened_now_ns": 60_000_000_000,
                },
                {
                    "kind": "event",
                    "event": {"type": "Wake"},
                    "plan": {
                        "places": [
                            {
                                "order_id": "c0",
                                "token_index": 0,
                                "side": "SELL",
                                "price": 0.43,
                                "quantity": 71.41,
                                "level_index": -1,
                            }
                        ],
                        "cancels": [],
                    },
                },
                {
                    "kind": "event",
                    "event": {"type": "OrderAccepted", "order_id": "c0"},
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "event": {
                        "type": "Fill",
                        "fill_id": "k-sell",
                        "order_id": "c0",
                        "qty": 71.41,
                        "price": 0.43,
                    },
                    "plan": {"places": [], "cancels": []},
                },
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert len(tapes[0].segments) == 1
    segment = tapes[0].segments[0]
    assert segment.side == "SELL"
    assert segment.start_s == 182.0
    assert segment.end_s == pytest.approx(207.097)


def test_buy_segment_ends_at_first_cancel_unsettled(tmp_path: Path) -> None:
    """A BUY rest ends at the first CancelUnsettled; venue repeats don't extend it."""
    archive_dir = _write_match(tmp_path, "unsettled", "2026-09-01T00:00:00Z", "dota", "live")
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 120,
            "yes_best_bid": 0.50,
            "yes_best_ask": 0.52,
            "yes_mid": 0.51,
        },
        {
            "kind": "quote",
            "venue": "polymarket",
            "second": 76,
            "placed": [{"token_id": YES_TOKEN, "side": "BUY", "price": 0.55, "size": 10.0}],
            "canceled": [],
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220860.0,
                    "opened_now_ns": 60_000_000_000,
                },
                {
                    "kind": "event",
                    "event": {"type": "Wake"},
                    "plan": {
                        "places": [
                            {
                                "order_id": "c0",
                                "token_index": 0,
                                "side": "BUY",
                                "price": 0.55,
                                "quantity": 10.0,
                                "level_index": 0,
                            }
                        ],
                        "cancels": [],
                    },
                },
                {
                    "kind": "event",
                    "event": {"type": "OrderAccepted", "order_id": "c0"},
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "event": {"type": "CancelUnsettled", "order_id": "c0"},
                    "now_ns": 90_000_000_000,
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "event": {"type": "CancelUnsettled", "order_id": "c0"},
                    "now_ns": 95_000_000_000,
                    "plan": {"places": [], "cancels": []},
                },
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert len(tapes[0].segments) == 1
    segment = tapes[0].segments[0]
    assert segment.side == "BUY"
    assert segment.start_s == 76.0
    assert segment.end_s == 90.0


def test_book_uses_core_bookupdate_time_axis(tmp_path: Path) -> None:
    """Bid/ask X follows BookUpdate now_ns on the header-anchored wall axis."""
    archive_dir = _write_match(tmp_path, "book-axis", "2026-09-01T00:00:00Z", "dota", "live")
    # Header opened 100s after horn while monotonic read 1000s -> offset -900
    fill_now_ns = 1_000_000_000_000
    book_now_ns = 1_050_000_000_000  # -> plot second 150
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 10,
            "yes_best_bid": 0.40,
            "yes_best_ask": 0.41,
            "yes_mid": 0.405,
            "no_best_bid": 0.59,
            "no_best_ask": 0.60,
            "no_mid": 0.595,
            "market_p_radiant": 0.405,
            "radiant_fair": 0.40,
        },
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.40,
            "size": 10.0,
            "second": 10,
            "ts_utc": "2026-09-01T00:01:40Z",
            "fill_key": "anchor",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220900.0,
                    "opened_now_ns": 1_000_000_000_000,
                },
                {
                    "kind": "event",
                    "now_ns": fill_now_ns,
                    "event": {
                        "type": "Fill",
                        "now_ns": fill_now_ns,
                        "fill_id": "anchor",
                        "order_id": "c0",
                        "qty": 10.0,
                        "price": 0.40,
                        "token_index": 0,
                        "side": "BUY",
                    },
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "now_ns": book_now_ns,
                    "event": {
                        "type": "BookUpdate",
                        "now_ns": book_now_ns,
                        "books": {
                            "tokens": [
                                {
                                    "token_index": 0,
                                    "bid": 0.47,
                                    "ask": 0.48,
                                    "bid_size": 1.0,
                                    "ask_size": 1.0,
                                    "ts_ns": book_now_ns,
                                }
                            ]
                        },
                    },
                    "plan": {"places": [], "cancels": []},
                },
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert tapes[0].ask.seconds == (150.0,)
    assert tapes[0].ask.y == (0.48,)
    assert tapes[0].bid.y == (0.47,)


def test_core_book_keeps_intra_second_updates(tmp_path: Path) -> None:
    """Two BookUpdates in one truncated second both plot, so a fill can sit on bid/ask."""
    archive_dir = _write_match(tmp_path, "book-dense", "2026-09-01T00:00:00Z", "dota", "live")
    fill_now_ns = 1_000_000_000_000
    first_book_ns = 1_050_100_000_000  # plot 150.1
    last_book_ns = 1_050_900_000_000  # plot 150.9

    def book_event(now_ns: int, bid: float, ask: float) -> dict[str, object]:
        return {
            "kind": "event",
            "now_ns": now_ns,
            "event": {
                "type": "BookUpdate",
                "now_ns": now_ns,
                "books": {
                    "tokens": [
                        {
                            "token_index": 0,
                            "bid": bid,
                            "ask": ask,
                            "bid_size": 1.0,
                            "ask_size": 1.0,
                            "ts_ns": now_ns,
                        }
                    ]
                },
            },
            "plan": {"places": [], "cancels": []},
        }

    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.47,
            "size": 10.0,
            "second": 10,
            "ts_utc": "2026-09-01T00:01:40Z",
            "fill_key": "anchor",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220900.0,
                    "opened_now_ns": 1_000_000_000_000,
                },
                {
                    "kind": "event",
                    "now_ns": fill_now_ns,
                    "event": {
                        "type": "Fill",
                        "now_ns": fill_now_ns,
                        "fill_id": "anchor",
                        "order_id": "c0",
                        "qty": 10.0,
                        "price": 0.47,
                        "token_index": 0,
                        "side": "BUY",
                    },
                    "plan": {"places": [], "cancels": []},
                },
                book_event(first_book_ns, 0.47, 0.48),
                book_event(last_book_ns, 0.41, 0.42),
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert tapes[0].bid.seconds == pytest.approx((150.1, 150.9))
    assert tapes[0].bid.y == (0.47, 0.41)
    assert tapes[0].ask.y == (0.48, 0.42)


def test_pred_fair_use_signalupdate_time_axis(tmp_path: Path) -> None:
    """Fair/pred keep journal values but X follows SignalUpdate now_ns."""
    archive_dir = _write_match(tmp_path, "pred-axis", "2026-09-01T00:00:00Z", "dota", "live")
    fill_now_ns = 1_000_000_000_000
    signal_now_ns = 1_080_000_000_000  # plot second 180 with offset -900
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 50,
            "yes_best_bid": 0.50,
            "yes_best_ask": 0.52,
            "yes_mid": 0.51,
            "no_best_bid": 0.48,
            "no_best_ask": 0.50,
            "no_mid": 0.49,
            "market_p_radiant": 0.51,
            "radiant_fair": 0.56,
        },
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.50,
            "size": 10.0,
            "second": 50,
            "ts_utc": "2026-09-01T00:01:40Z",
            "fill_key": "anchor",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220900.0,
                    "opened_now_ns": 1_000_000_000_000,
                },
                {
                    "kind": "event",
                    "now_ns": fill_now_ns,
                    "event": {
                        "type": "Fill",
                        "now_ns": fill_now_ns,
                        "fill_id": "anchor",
                        "order_id": "c0",
                        "qty": 10.0,
                        "price": 0.50,
                        "token_index": 0,
                        "side": "BUY",
                    },
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "now_ns": signal_now_ns,
                    "event": {
                        "type": "SignalUpdate",
                        "now_ns": signal_now_ns,
                        "signal": {
                            "predicted_delta": 0.05,
                            "nw_delta_30": None,
                            "received_ns": signal_now_ns,
                            "anchor_p": 0.51,
                        },
                    },
                    "plan": {"places": [], "cancels": []},
                },
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert tapes[0].pred_cents.seconds == (180.0,)
    assert tapes[0].pred_cents.y == pytest.approx((5.0,))
    assert tapes[0].fair.seconds == (180.0,)
    assert tapes[0].fair.y == pytest.approx((0.56,))


def test_fair_retiming_survives_one_extra_journal_signal(tmp_path: Path) -> None:
    """Off-by-one vs SignalUpdate still uses the wall axis, not journal game seconds."""
    archive_dir = _write_match(tmp_path, "fair-zip", "2026-09-01T00:00:00Z", "dota", "live")
    fill_now_ns = 1_000_000_000_000
    signal_now_ns = 1_080_000_000_000
    signal = {
        "kind": "signal",
        "venue": "polymarket",
        "second": 50,
        "yes_best_bid": 0.50,
        "yes_best_ask": 0.52,
        "yes_mid": 0.51,
        "no_best_bid": 0.48,
        "no_best_ask": 0.50,
        "no_mid": 0.49,
        "market_p_radiant": 0.51,
        "radiant_fair": 0.56,
    }
    extra = dict(signal)
    extra["second"] = 51
    extra["radiant_fair"] = 0.57
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        signal,
        extra,
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.50,
            "size": 10.0,
            "second": 50,
            "ts_utc": "2026-09-01T00:01:40Z",
            "fill_key": "anchor",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220900.0,
                    "opened_now_ns": 1_000_000_000_000,
                },
                {
                    "kind": "event",
                    "now_ns": fill_now_ns,
                    "event": {
                        "type": "Fill",
                        "now_ns": fill_now_ns,
                        "fill_id": "anchor",
                        "order_id": "c0",
                        "qty": 10.0,
                        "price": 0.50,
                        "token_index": 0,
                        "side": "BUY",
                    },
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "now_ns": signal_now_ns,
                    "event": {
                        "type": "SignalUpdate",
                        "now_ns": signal_now_ns,
                        "signal": {
                            "predicted_delta": 0.05,
                            "nw_delta_30": None,
                            "received_ns": signal_now_ns,
                            "anchor_p": 0.51,
                        },
                    },
                    "plan": {"places": [], "cancels": []},
                },
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert tapes[0].fair.seconds == pytest.approx((180.0,))
    assert tapes[0].fair.y == pytest.approx((0.56,))


def test_core_trace_header_anchors_book_and_pred_without_fills(tmp_path: Path) -> None:
    """Zero fills: book/pred X still comes from the header's opened_wall_s anchor."""
    archive_dir = _write_match(tmp_path, "header-axis", "2026-09-01T00:00:00Z", "dota", "live")
    signal_now_ns = 1_080_000_000_000  # plot second 180 with offset -900
    book_now_ns = 1_050_000_000_000  # plot second 150
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 50,
            "yes_best_bid": 0.50,
            "yes_best_ask": 0.52,
            "yes_mid": 0.51,
            "no_best_bid": 0.48,
            "no_best_ask": 0.50,
            "no_mid": 0.49,
            "market_p_radiant": 0.51,
            "radiant_fair": 0.56,
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    (archive_dir / "core_trace.jsonl").write_text(
        "\n".join(
            json.dumps(record)
            for record in [
                {
                    "kind": "header",
                    "schema_version": 1,
                    "opened_wall_s": 1788220900.0,
                    "opened_now_ns": 1_000_000_000_000,
                },
                {
                    "kind": "event",
                    "now_ns": book_now_ns,
                    "event": {
                        "type": "BookUpdate",
                        "now_ns": book_now_ns,
                        "books": {
                            "tokens": [
                                {
                                    "token_index": 0,
                                    "bid": 0.47,
                                    "ask": 0.48,
                                    "bid_size": 1.0,
                                    "ask_size": 1.0,
                                    "ts_ns": book_now_ns,
                                }
                            ]
                        },
                    },
                    "plan": {"places": [], "cancels": []},
                },
                {
                    "kind": "event",
                    "now_ns": signal_now_ns,
                    "event": {
                        "type": "SignalUpdate",
                        "now_ns": signal_now_ns,
                        "signal": {
                            "predicted_delta": 0.05,
                            "nw_delta_30": None,
                            "received_ns": signal_now_ns,
                            "anchor_p": 0.51,
                        },
                    },
                    "plan": {"places": [], "cancels": []},
                },
            ]
        )
        + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert tapes[0].bid.seconds == (150.0,)  # not journal second 50
    assert tapes[0].pred_cents.seconds == (180.0,)


def test_dire_yes_token_fair_is_one_minus_radiant(tmp_path: Path) -> None:
    """YES is Dire: plotted fair is 1 - radiant_fair, not the Radiant line."""
    archive_dir = _write_match(tmp_path, "dire-yes", "2026-09-01T00:00:00Z", "dota", "live")
    match = json.loads((archive_dir / "match.json").read_text())
    match["market"]["yes_is_radiant"] = False
    (archive_dir / "match.json").write_text(json.dumps(match))
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 80,
            "yes_best_bid": 0.39,
            "yes_best_ask": 0.41,
            "yes_mid": 0.40,
            "no_best_bid": 0.59,
            "no_best_ask": 0.61,
            "no_mid": 0.60,
            "market_p_radiant": 0.55,
            "radiant_fair": 0.60,
        },
        {
            "kind": "quote",
            "venue": "polymarket",
            "second": 80,
            "placed": [{"token_id": YES_TOKEN, "side": "BUY", "price": 0.39, "size": 10.0}],
            "canceled": [],
        },
        {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": YES_TOKEN,
            "side": "BUY",
            "price": 0.39,
            "size": 10.0,
            "second": 80,
            "fill_key": "k-dire",
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    assert tapes[0].token_index == 0
    assert tapes[0].side_name == "dire"
    assert tapes[0].fair.y == pytest.approx((0.40,))
    assert tapes[0].pred_cents.y == pytest.approx((-5.0,))


def test_live_game_state_uses_receipt_time_from_horn(tmp_path: Path) -> None:
    """Gold/deaths X is the VPS receipt - horn; game_time stays on the hover."""
    archive_dir = _write_traded_match(tmp_path)
    match = json.loads((archive_dir / "match.json").read_text())
    match["horn_at_utc"] = "2026-09-01T00:00:00Z"
    match["feed_source"] = "steam"
    (archive_dir / "match.json").write_text(json.dumps(match))
    _write_steam_state(archive_dir)  # game_time 100, server stamp 115, receipt 130
    state = load_live_game_state(archive_dir)
    assert state.seconds == (130.0,)
    assert state.game_seconds == (100.0,)
    assert state.radiant_nw == (10000.0,)


def _write_oddin_state(
    archive_dir: Path,
    *,
    game_time: int = 100,
    last_updated_at: str = "2026-09-01 00:01:55.000000000 +0000 UTC",
    received: str = "2026-09-01T00:02:10Z",
) -> None:
    """One oddin_state.jsonl ws tick: game_time 100, server stamp 115s, receipt 130s."""

    def player(prefix: str, index: int, net_worth: int, deaths: int) -> dict[str, object]:
        return {
            "player": {"nickname": f"{prefix}{index}"},
            "hero": {"name": "Kez"},
            "netWorth": net_worth,
            "kills": 0,
            "deaths": deaths,
            "assists": 0,
            "alive": True,
            "hasAegis": False,
        }

    def side(name: str, faction: str, net_worth: int, deaths: int) -> dict[str, object]:
        return {
            "team": {"name": name},
            "faction": faction,
            "kills": 0,
            "netWorth": 0,
            "players": [player(name[0], index, net_worth, deaths) for index in range(5)],
        }

    payload = {
        "matchStatus": "LIVE",
        "dataStatus": "VALID_DATA",
        "lastUpdatedAt": last_updated_at,
        "mapPaused": False,
        "homeTeam": {"name": "Aurora"},
        "awayTeam": {"name": "Secret"},
        "homeScore": 0,
        "awayScore": 0,
        "currentMap": {
            "id": "map-1",
            "mapOrder": 1,
            "gameTime": game_time,
            "homeTeam": side("Aurora", "RADIANT", 2000, 1),
            "awayTeam": side("Secret", "DIRE", 1800, 0),
        },
    }
    (archive_dir / "oddin_state.jsonl").write_text(
        json.dumps({"received_at_utc": received, "event": "ws", "payload": payload}) + "\n"
    )


def test_live_game_state_from_oddin_archive(tmp_path: Path) -> None:
    """oddin_state.jsonl replay fills the live viewer on receipt time; XP stays 0."""
    archive_dir = _write_traded_match(tmp_path)
    match = json.loads((archive_dir / "match.json").read_text())
    match["feed_source"] = "oddin"
    match["map_number"] = 1
    match["horn_at_utc"] = "2026-09-01T00:00:00Z"
    match["market"]["yes_is_radiant"] = True
    (archive_dir / "match.json").write_text(json.dumps(match))
    _write_oddin_state(archive_dir)
    state = load_live_game_state(archive_dir)
    assert state.seconds == (130.0,)  # received - horn, not lastUpdatedAt 115 or game_time 100
    assert state.game_seconds == (100.0,)
    assert state.radiant_nw == (10000.0,)
    assert state.dire_nw == (9000.0,)
    assert state.deaths_radiant == (5.0,)
    assert state.deaths_dire == (0.0,)
    assert state.radiant_xp_adv == (0.0,)


def test_feed_clock_wall_at_fallbacks() -> None:
    """Mapped seconds win; unmapped shift by the median delay; empty is identity."""
    clock = FeedClock({100: 130.0}, 15.0)
    assert clock.wall_at(100) == 130.0
    assert clock.wall_at(200) == 215.0
    assert FeedClock({}, None).wall_at(7) == 7.0


def test_live_tape_journal_seconds_land_on_feed_receipt_wall(tmp_path: Path) -> None:
    """No fills/trace: journal seconds map onto the wall axis via the feed archive."""
    archive_dir = _write_match(tmp_path, "tape-clock", "2026-09-01T00:00:00Z", "dota", "live")
    match = json.loads((archive_dir / "match.json").read_text())
    match["horn_at_utc"] = "2026-09-01T00:00:00Z"
    match["feed_source"] = "oddin"
    match["map_number"] = 1
    (archive_dir / "match.json").write_text(json.dumps(match))
    _write_oddin_state(archive_dir)  # game_time 100 -> receipt 130
    records = [
        {"kind": "session_start", "execution_mode": "live"},
        {
            "kind": "signal",
            "venue": "polymarket",
            "second": 100,
            "yes_best_bid": 0.50,
            "yes_best_ask": 0.52,
            "yes_mid": 0.51,
            "no_best_bid": 0.48,
            "no_best_ask": 0.50,
            "no_mid": 0.49,
            "market_p_radiant": 0.51,
            "radiant_fair": 0.55,
        },
        {
            "kind": "quote",
            "venue": "polymarket",
            "second": 100,
            "placed": [{"token_id": YES_TOKEN, "side": "BUY", "price": 0.55, "size": 10.0}],
            "canceled": [],
        },
    ]
    (archive_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    tapes = load_live_tapes(archive_dir)
    assert len(tapes) == 1
    tape = tapes[0]
    assert tape.mid.seconds == (130.0,)
    assert tape.fair.seconds == (130.0,)
    assert tape.submits[0].second == 130.0
