"""Completed-match picker, order segments, and token-space conversion."""

# pyright: reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportAttributeAccessIssue=false

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from backtest.inspect.app import plot_game_state, plot_match, plot_token_tape
from backtest.inspect.catalog import list_runs
from backtest.inspect.tail import pick_completed_matches
from backtest.inspect.tape import (
    Series,
    TapeMarker,
    TokenTape,
    _markers,
    build_order_segments,
    empty_game_state,
    event_token_fair,
    game_state_from_frame,
    last_per_second,
    seconds_from_horn,
    token_predicted_delta,
)
from backtest.marks import calculate_cvar_5, cvar_tail_count
from backtest.paths import SUMMARY_FILENAME
from shared.utils.match_time import NS_PER_SECOND

HORN = datetime(2026, 6, 1)  # naive UTC; the plotter drops tz anyway


def _results(*pnls: float, terminated: frozenset[int] = frozenset()) -> pd.DataFrame:
    rows = []
    for index, pnl in enumerate(pnls, start=1):
        rows.append(
            {
                "match_id": index,
                "slug": f"slug-{index}",
                "engine_pnl": pnl,
                "buy_fills": 1,
                "sell_fills": 1,
                "horn_at": "2026-06-01T00:00:00+00:00",
                "game_ended_at": "2026-06-01T00:40:00+00:00",
                "terminated_early": index in terminated,
            }
        )
    return pd.DataFrame(rows)


def test_cvar_tail_count_matches_calculate_cvar_5() -> None:
    """40 completed maps take 2 worst; empty is 0."""
    assert cvar_tail_count(0) == 0
    assert cvar_tail_count(20) == 1
    assert cvar_tail_count(40) == 2
    assert calculate_cvar_5([float(value) for value in range(40)]) == pytest.approx(0.5)


def test_pick_completed_matches_drops_terminated_and_sorts_worst_first() -> None:
    """Terminated maps are out; remaining rows are engine PnL ascending."""
    results = _results(-10.0, -2.0, 1.0, 3.0, 99.0, terminated=frozenset({5}))
    matches = pick_completed_matches(results)
    assert list(matches["match_id"]) == [1, 2, 3, 4]
    assert list(matches["engine_pnl"]) == [-10.0, -2.0, 1.0, 3.0]


def test_event_token_fair_converts_no_quote_radiant() -> None:
    """no_quote fair is Radiant-space; submitted fair is already the token."""
    assert event_token_fair(
        kind="no_quote",
        fair=0.6,
        predicted_delta=0.01,
        book_p_radiant=0.5,
        event_token_index=-1,
        token_index=1,
        radiant_token_index=0,
    ) == pytest.approx(0.4)
    assert event_token_fair(
        kind="submitted",
        fair=0.55,
        predicted_delta=0.01,
        book_p_radiant=0.5,
        event_token_index=0,
        token_index=0,
        radiant_token_index=0,
    ) == pytest.approx(0.55)
    assert (
        event_token_fair(
            kind="no_quote",
            fair=0.0,
            predicted_delta=0.0,
            book_p_radiant=0.0,
            event_token_index=-1,
            token_index=0,
            radiant_token_index=0,
        )
        is None
    )


def test_token_predicted_delta_flips_for_dire() -> None:
    """Dire token pred is the negated Radiant delta."""
    assert token_predicted_delta(0.02, token_index=0, radiant_token_index=0) == pytest.approx(0.02)
    assert token_predicted_delta(0.02, token_index=1, radiant_token_index=0) == pytest.approx(-0.02)


def test_last_per_second_keeps_latest_sample() -> None:
    """Two samples in second 3 keep the later value."""
    series = last_per_second([3.1, 3.9, 4.0], [0.1, 0.2, 0.3])
    assert series.seconds == (3.0, 4.0)
    assert series.y == (0.2, 0.3)


def test_build_order_segments_submit_to_cancel_or_fill() -> None:
    """Cancel ends the rest; a fill with no cancel uses the fill time."""
    horn_ns = 0
    events = pd.DataFrame(
        [
            {
                "ts_ns": 10 * NS_PER_SECOND,
                "kind": "submitted",
                "token_index": 0,
                "side": "BUY",
                "price": 0.51,
                "order_id": "a",
                "level_index": 0,
                "predicted_delta": 0.01,
                "fair": 0.52,
                "book_p_radiant": 0.5,
                "match_id": 1,
            },
            {
                "ts_ns": 40 * NS_PER_SECOND,
                "kind": "canceled",
                "token_index": 0,
                "side": "BUY",
                "price": 0.51,
                "order_id": "a",
                "level_index": 0,
                "predicted_delta": 0.01,
                "fair": 0.52,
                "book_p_radiant": 0.5,
                "match_id": 1,
            },
            {
                "ts_ns": 12 * NS_PER_SECOND,
                "kind": "submitted",
                "token_index": 0,
                "side": "BUY",
                "price": 0.50,
                "order_id": "b",
                "level_index": 1,
                "predicted_delta": 0.01,
                "fair": 0.52,
                "book_p_radiant": 0.5,
                "match_id": 1,
            },
        ]
    )
    fills = pd.DataFrame(
        [
            {
                "ts_ns": 25 * NS_PER_SECOND,
                "token_index": 0,
                "side": "BUY",
                "price": 0.50,
                "order_id": "b",
                "level_index": 1,
                "quantity": 100.0,
                "match_id": 1,
            }
        ]
    )
    segments = build_order_segments(
        events, fills, horn_ns=horn_ns, game_end_ns=100 * NS_PER_SECOND, token_index=0
    )
    by_id = {segment.order_id: segment for segment in segments}
    assert by_id["a"].start_s == pytest.approx(10.0)
    assert by_id["a"].end_s == pytest.approx(40.0)
    assert by_id["b"].start_s == pytest.approx(12.0)
    assert by_id["b"].end_s == pytest.approx(25.0)
    assert by_id["b"].level_index == 1


def test_seconds_from_horn_can_be_negative() -> None:
    """Pre-horn book time is negative seconds."""
    assert seconds_from_horn(0, NS_PER_SECOND) == pytest.approx(-1.0)


def test_list_runs_newest_first(tmp_path: Path) -> None:
    """Runs with a seed summary appear, newest seeds.json first."""
    older = tmp_path / "old-run"
    newer = tmp_path / "new-run"
    for path, net, cvar in ((older, 1.0, -10.0), (newer, 2.0, -20.0)):
        seed_dir = path / "seed0"
        seed_dir.mkdir(parents=True)
        (seed_dir / SUMMARY_FILENAME).write_text(
            json.dumps({"arms": [{"net_pnl": net, "cvar_5": cvar}]})
        )
        (path / "seeds.json").write_text(
            json.dumps({"mean": {"net_pnl": net, "cvar_5": cvar}, "seeds": [], "sd": {}})
        )
    os.utime(older / "seed0" / SUMMARY_FILENAME, (1_000, 1_000))
    os.utime(older / "seeds.json", (1_000, 1_000))
    os.utime(newer / "seed0" / SUMMARY_FILENAME, (2_000, 2_000))
    os.utime(newer / "seeds.json", (2_000, 2_000))
    runs = list_runs(game="dota", root=tmp_path)
    assert [run.name for run in runs] == ["new-run", "old-run"]
    assert runs[0].net_pnl == pytest.approx(2.0)
    assert runs[0].cvar_5 == pytest.approx(-20.0)
    assert runs[0].seeds == (0,)


def test_plot_markers_hover_includes_notional() -> None:
    """Hover shows share qty and dollar notional from price * quantity."""
    empty = Series((), ())
    tape = TokenTape(
        token_index=0,
        side_name="radiant",
        mid=empty,
        bid=empty,
        ask=empty,
        fair=empty,
        pred_cents=empty,
        segments=(),
        submits=(TapeMarker(5.0, 0.68, "BUY", "submit", 0, 147.05),),
        fills=(TapeMarker(12.0, 0.68, "BUY", "fill", 0, 119.25),),
    )
    fig = plot_token_tape(tape, 0.01, False, HORN)
    fill = next(trace for trace in fig.data if trace.name == "BUY fill")
    assert fill.customdata[0][1] == pytest.approx(119.25)
    assert fill.customdata[0][2] == pytest.approx(81.09)
    assert fill.customdata[0][3] == "00:00:12.000"  # true UTC, not the nudged x
    assert "customdata[2]" in fill.hovertemplate


def test_plot_token_tape_lists_fill_in_legend() -> None:
    """BUY fill is its own legend trace, not a same-color tick on the ladder line."""
    empty = Series((), ())
    tape = TokenTape(
        token_index=0,
        side_name="radiant",
        mid=empty,
        bid=empty,
        ask=empty,
        fair=empty,
        pred_cents=empty,
        segments=(),
        submits=(TapeMarker(5.0, 0.50, "BUY", "submit", 0, 0.0),),
        fills=(TapeMarker(12.0, 0.50, "BUY", "fill", 0, 100.0),),
    )
    names = [trace.name for trace in plot_token_tape(tape, 0.01, True, HORN).data]
    assert "BUY fill" in names
    assert "BUY submit" in names
    titles = [
        annotation.text for annotation in plot_token_tape(tape, 0.01, True, HORN).layout.annotations
    ]
    assert "pred ¢" in titles
    hidden = plot_token_tape(tape, 0.01, False, HORN)
    hidden_titles = [annotation.text for annotation in hidden.layout.annotations]
    assert "pred ¢" not in hidden_titles
    assert "pred" not in [trace.name for trace in hidden.data]


def _state_frame(seconds: list[float], **columns: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "second": seconds,
            "state_ts_us": [int(second * 1_000_000) for second in seconds],
            **columns,
        }
    )


def test_game_state_from_frame_orders_by_second() -> None:
    """Validation slice becomes named series sorted by game second."""
    frame = _state_frame(
        [10.0, 0.0],
        radiant_nw=[200.0, 100.0],
        dire_nw=[180.0, 90.0],
        radiant_nw_adv=[20.0, 10.0],
        top1_nw_adv=[5.0, 1.0],
        radiant_xp_adv=[40.0, 0.0],
        deaths_radiant=[1.0, 0.0],
        deaths_dire=[0.0, 0.0],
    )
    state = game_state_from_frame(frame, horn_ns=0)
    assert state.seconds == (0.0, 10.0)
    assert state.game_seconds == (0.0, 10.0)
    assert state.radiant_nw == (100.0, 200.0)
    fig = plot_game_state(state, True, True, HORN)
    names = [trace.name for trace in fig.data]
    assert "radiant NW" in names
    assert "NW adv" in names
    assert "radiant deaths" in names
    gold_only = plot_game_state(state, True, False, HORN)
    assert "radiant deaths" not in [trace.name for trace in gold_only.data]


def test_submit_markers_use_event_quantity() -> None:
    """Submit hover qty comes from the quote-event quantity, not a hardcoded zero."""
    events = pd.DataFrame(
        [
            {
                "ts_ns": 10 * NS_PER_SECOND,
                "kind": "submitted",
                "token_index": 0,
                "side": "SELL",
                "price": 0.51,
                "quantity": 37.5,
                "order_id": "a",
                "level_index": 0,
            }
        ]
    )
    fills = pd.DataFrame()
    submits, fill_marks = _markers(events, fills, token_index=0, horn_ns=0)
    assert fill_marks == ()
    assert submits[0].quantity == pytest.approx(37.5)
    assert submits[0].side == "SELL"


def test_plot_match_shares_x_across_gold() -> None:
    """Gold is the same figure as the token tape so Plotly shares the x-axis."""
    empty = Series((), ())
    tape = TokenTape(
        token_index=0,
        side_name="radiant",
        mid=empty,
        bid=empty,
        ask=empty,
        fair=empty,
        pred_cents=empty,
        segments=(),
        submits=(),
        fills=(),
    )
    state = game_state_from_frame(
        _state_frame(
            [0.0, 10.0],
            radiant_nw=[100.0, 200.0],
            dire_nw=[90.0, 180.0],
            radiant_nw_adv=[10.0, 20.0],
            top1_nw_adv=[1.0, 5.0],
            radiant_xp_adv=[0.0, 40.0],
            deaths_radiant=[0.0, 1.0],
            deaths_dire=[0.0, 0.0],
        ),
        horn_ns=0,
    )
    fig = plot_match((tape,), state, True, True, True, 0.01, HORN)
    titles = [annotation.text for annotation in fig.layout.annotations]
    assert "pred ¢" in titles
    assert "net worth" in titles
    assert "advantage" in titles
    assert "deaths" in titles
    assert fig.layout.xaxis.matches == "x5"
    assert fig.layout.xaxis3.matches == "x5"
    assert fig.layout.xaxis4.matches == "x5"


def test_game_state_from_frame_plots_pause_shifted_wall_seconds() -> None:
    """A 4s pause before game second 10 plots gold at wall 14, same axis as price."""
    frame = _state_frame(
        [0.0, 10.0],
        radiant_nw=[100.0, 200.0],
        dire_nw=[90.0, 180.0],
        radiant_nw_adv=[10.0, 20.0],
        top1_nw_adv=[1.0, 5.0],
        radiant_xp_adv=[0.0, 40.0],
        deaths_radiant=[0.0, 1.0],
        deaths_dire=[0.0, 0.0],
    )
    frame["state_ts_us"] = [0, 14_000_000]
    state = game_state_from_frame(frame, horn_ns=0)
    assert state.seconds == (0.0, 14.0)


def _window_tape(
    submits: tuple[TapeMarker, ...], fills: tuple[TapeMarker, ...], last_s: float
) -> TokenTape:
    seconds = tuple(float(s) for s in range(0, int(last_s) + 1))
    series = Series(seconds, tuple(0.5 for _ in seconds))
    return TokenTape(
        token_index=0,
        side_name="radiant",
        mid=series,
        bid=series,
        ask=series,
        fair=series,
        pred_cents=Series((), ()),
        segments=(),
        submits=submits,
        fills=fills,
    )


def test_plot_match_initial_zoom_traded_window() -> None:
    """Initial zoom: 60s before the first submit to 60s after the last sell."""
    tape = _window_tape(
        submits=(
            TapeMarker(400.0, 0.5, "BUY", "submit", 0, 10.0),
            TapeMarker(700.0, 0.6, "SELL", "submit", 0, 10.0),
        ),
        fills=(TapeMarker(800.0, 0.6, "SELL", "fill", 0, 10.0),),
        last_s=1000.0,
    )
    fig = plot_match((tape,), empty_game_state(), False, False, False, None, HORN)
    assert list(fig.layout.xaxis.range) == [
        datetime(2026, 6, 1, 0, 5, 40),
        datetime(2026, 6, 1, 0, 14, 20),
    ]
    # y fits windowed data: series 0.5 and sell markers at 0.6, padded by 2.5%.
    assert list(fig.layout.yaxis.range) == pytest.approx([0.4975, 0.6025])


def test_plot_match_initial_zoom_clamps_to_data() -> None:
    """The 60s pad is capped at the data extent; no sells leaves the right edge alone."""
    tape = _window_tape(
        submits=(TapeMarker(30.0, 0.5, "BUY", "submit", 0, 10.0),),
        fills=(),
        last_s=200.0,
    )
    fig = plot_match((tape,), empty_game_state(), False, False, False, None, HORN)
    assert list(fig.layout.xaxis.range) == [HORN, datetime(2026, 6, 1, 0, 3, 20)]


def test_plot_match_initial_zoom_off_without_markers() -> None:
    """No submits or sells: keep the default autorange and no zoom button."""
    tape = _window_tape(submits=(), fills=(), last_s=200.0)
    fig = plot_match((tape,), empty_game_state(), False, False, False, None, HORN)
    assert fig.layout.xaxis.range is None
    assert fig.layout.yaxis.range is None
    assert not fig.layout.updatemenus
