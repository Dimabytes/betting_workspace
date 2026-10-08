"""Shared match figure: price, pred, gold, and deaths panels that zoom together."""

# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from shared.constants.strategy import QUOTE_GRID
from viewer.types import GameState, Series, TapeMarker, TokenTape, empty_game_state

LEVEL_COLORS = {-1: "#7f7f7f", 0: "#1f77b4", 1: "#ff7f0e", 2: "#2ca02c"}
BID_COLOR = "#9ecae1"
ASK_COLOR = "#fc9272"
MID_COLOR = "#222222"
FAIR_COLOR = "#6a3d9a"


def _utc_x(horn_at: datetime, seconds: Iterable[float]) -> list[datetime]:
    """Seconds from horn as naive UTC datetimes, so plotly renders UTC as-is."""
    epoch = horn_at.replace(tzinfo=None)
    return [epoch + timedelta(seconds=second) for second in seconds]


def _utc_label(horn_at: datetime, second: float) -> str:
    """HH:MM:SS.fff UTC for one second-from-horn."""
    return (horn_at + timedelta(seconds=second)).strftime("%H:%M:%S.%f")[:-3]


def _add_line(
    fig: go.Figure,
    series: Series,
    name: str,
    color: str,
    row: int,
    horn_at: datetime,
    dash: str | None = None,
    shape: str = "linear",
    customdata: tuple[float, ...] | None = None,
) -> None:
    if not series.seconds:
        return
    trace = go.Scatter(
        x=_utc_x(horn_at, series.seconds),
        y=list(series.y),
        name=name,
        mode="lines",
        line={"color": color, "width": 1.5, "dash": dash or "solid", "shape": shape},
    )
    if customdata is not None:
        trace.customdata = list(customdata)
        trace.hovertemplate = "%{x|%H:%M:%S.%L} · game %{customdata}s · %{y}<extra></extra>"
    fig.add_trace(trace, row=row, col=1)


def _marker_symbol(side: str, kind: str) -> str:
    up = side == "BUY"
    if kind == "submit":
        return "triangle-up-open" if up else "triangle-down-open"
    return "triangle-up" if up else "triangle-down"


FILL_X_NUDGE_S = 0.45


def _spread_marker_xs(
    seconds: list[float],
    prices: list[float],
    seen: dict[tuple[int, int], int] | None = None,
) -> tuple[list[float], dict[tuple[int, int], int]]:
    """Nudge markers that share the same truncated second and price tick.

    Partial SELL fills often differ by <1s on ts_utc; bucket by floor(second) so
    they still fan out on X like same-timestamp BUY splits.
    """
    counts = {} if seen is None else seen
    stacked: list[float] = []
    for second, price in zip(seconds, prices, strict=True):
        key = (int(second), round(price / QUOTE_GRID))
        index = counts.get(key, 0)
        counts[key] = index + 1
        stacked.append(second + index * FILL_X_NUDGE_S)
    return stacked, counts


def _add_markers(
    fig: go.Figure,
    markers: tuple[TapeMarker, ...],
    *,
    kind: str,
    side: str,
    row: int,
    prefix: str,
    horn_at: datetime,
    overlap_counts: dict[tuple[int, int], int] | None = None,
) -> dict[tuple[int, int], int]:
    """One legend entry per (kind, side). Fills are black so they sit on the ladder line."""
    subset = [marker for marker in markers if marker.kind == kind and marker.side == side]
    counts = {} if overlap_counts is None else overlap_counts
    if not subset:
        return counts
    is_fill = kind == "fill"
    true_xs = [marker.second for marker in subset]
    xs, counts = _spread_marker_xs(true_xs, [marker.price for marker in subset], counts)
    ys = [marker.price for marker in subset]
    fig.add_trace(
        go.Scatter(
            x=_utc_x(horn_at, xs),
            y=ys,
            mode="markers",
            name=f"{prefix}{side} {kind}",
            marker={
                "symbol": _marker_symbol(side, kind),
                "size": 16 if is_fill else 9,
                "color": (
                    "#111111"
                    if is_fill
                    else [LEVEL_COLORS.get(marker.level_index, "#7f7f7f") for marker in subset]
                ),
                "line": {"color": "#ffffff", "width": 1.5} if is_fill else {"width": 1.5},
            },
            customdata=[
                [
                    marker.level_index,
                    marker.quantity,
                    marker.price * marker.quantity,
                    _utc_label(horn_at, marker.second),
                ]
                for marker in subset
            ],
            hovertemplate=(
                f"{side} {kind} L%{{customdata[0]}} "
                "qty=%{customdata[1]:.2f} ($%{customdata[2]:.2f}) "
                "%{customdata[3]} UTC<extra></extra>"
            ),
        ),
        row=row,
        col=1,
    )
    return counts


@dataclass(frozen=True)
class PanelRow:
    kind: Literal["price", "pred", "gold", "adv", "deaths"]
    tape: TokenTape | None
    title: str
    weight: float


def _panel_rows(
    tapes: tuple[TokenTape, ...], show_pred: bool, show_gold: bool, show_kills: bool
) -> tuple[PanelRow, ...]:
    rows: list[PanelRow] = []
    for tape in tapes:
        rows.append(
            PanelRow(
                "price",
                tape,
                f"{tape.side_name} token {tape.token_index}",
                3.0,
            )
        )
        if show_pred:
            rows.append(PanelRow("pred", tape, "pred ¢", 1.0))
    if show_gold:
        rows.append(PanelRow("adv", None, "advantage", 1.5))
    if show_kills:
        rows.append(PanelRow("deaths", None, "deaths", 1.5))
    if show_gold:
        rows.append(PanelRow("gold", None, "net worth", 1.5))
    return tuple(rows)


def _state_line(state: GameState, values: tuple[float, ...]) -> Series:
    return Series(state.seconds, values)


def _initial_xrange(tapes: tuple[TokenTape, ...], state: GameState) -> tuple[float, float] | None:
    """Zoom to the traded window: 60s before the first submit to 60s after the last sell."""
    submits = [marker.second for tape in tapes for marker in tape.submits]
    sells = [
        marker.second
        for tape in tapes
        for marker in (*tape.submits, *tape.fills)
        if marker.side == "SELL"
    ]
    if not submits and not sells:
        return None
    xs = [
        second
        for tape in tapes
        for series in (tape.bid, tape.ask, tape.mid, tape.fair, tape.pred_cents)
        for second in series.seconds
    ]
    xs += [marker.second for tape in tapes for marker in (*tape.submits, *tape.fills)]
    xs += list(state.seconds)
    lo, hi = min(xs), max(xs)
    left = max(min(submits) - 60.0, lo) if submits else lo
    right = min(max(sells) + 60.0, hi) if sells else hi
    if left >= right:
        return None
    return left, right


def _windowed_ys(
    seconds: tuple[float, ...], ys: tuple[float, ...], left: float, right: float
) -> list[float]:
    """Y values inside [left, right]; the last point left of the window carries in."""
    values: list[float] = []
    carry: float | None = None
    for second, y in zip(seconds, ys, strict=True):
        if second < left:
            carry = y
        elif second <= right:
            values.append(y)
        else:
            break
    if carry is not None:
        values.append(carry)
    return values


def _padded_extent(ys: list[float]) -> tuple[float, float] | None:
    """Range where the data spans ~95% of the axis."""
    if not ys:
        return None
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.025 or max(abs(hi), 1.0) * 0.05
    return lo - pad, hi + pad


def _panel_yrange(
    panel: PanelRow, state: GameState, left: float, right: float
) -> list[float] | None:
    """Y extent of what this panel draws inside the x window."""
    ys: list[float] = []
    if panel.kind == "price":
        tape = panel.tape
        assert tape is not None
        for series in (tape.bid, tape.ask, tape.mid, tape.fair):
            ys += _windowed_ys(series.seconds, series.y, left, right)
        ys += [
            segment.price
            for segment in tape.segments
            if segment.start_s <= right and segment.end_s >= left
        ]
        ys += [
            marker.price
            for marker in (*tape.submits, *tape.fills)
            if left <= marker.second <= right
        ]
    elif panel.kind == "pred":
        tape = panel.tape
        assert tape is not None
        ys += _windowed_ys(tape.pred_cents.seconds, tape.pred_cents.y, left, right)
    elif panel.kind == "gold":
        ys += _windowed_ys(state.seconds, state.radiant_nw, left, right)
        ys += _windowed_ys(state.seconds, state.dire_nw, left, right)
    elif panel.kind == "adv":
        ys += _windowed_ys(state.seconds, state.radiant_nw_adv, left, right)
        ys += _windowed_ys(state.seconds, state.top1_nw_adv, left, right)
        ys += _windowed_ys(state.seconds, state.radiant_xp_adv, left, right)
    else:
        ys += _windowed_ys(state.seconds, state.deaths_radiant, left, right)
        ys += _windowed_ys(state.seconds, state.deaths_dire, left, right)
    extent = _padded_extent(ys)
    return None if extent is None else list(extent)


def _add_price_panel(
    fig: go.Figure, tape: TokenTape, row: int, prefix: str, horn_at: datetime
) -> None:
    _add_line(fig, tape.bid, f"{prefix}bid", BID_COLOR, row, horn_at, shape="hv")
    _add_line(fig, tape.ask, f"{prefix}ask", ASK_COLOR, row, horn_at, shape="hv")
    _add_line(fig, tape.mid, f"{prefix}mid", MID_COLOR, row, horn_at, shape="hv")
    _add_line(fig, tape.fair, f"{prefix}fair", FAIR_COLOR, row, horn_at, dash="dash")
    legend_levels: set[int] = set()
    for segment in tape.segments:
        color = LEVEL_COLORS.get(segment.level_index, "#7f7f7f")
        show = segment.level_index not in legend_levels
        legend_levels.add(segment.level_index)
        fig.add_trace(
            go.Scatter(
                x=_utc_x(horn_at, (segment.start_s, segment.end_s)),
                y=[segment.price, segment.price],
                mode="lines",
                name=f"{prefix}L{segment.level_index} {segment.side}",
                line={"color": color, "width": 3},
                showlegend=show,
                hovertext=segment.order_id,
            ),
            row=row,
            col=1,
        )
    for side in ("BUY", "SELL"):
        # Submit first, then fill, sharing overlap counts so same-second SELL
        # submit+fill (and stacked fills) both nudge on X like the BUY case.
        overlap: dict[tuple[int, int], int] = {}
        overlap = _add_markers(
            fig,
            tape.submits,
            kind="submit",
            side=side,
            row=row,
            prefix=prefix,
            horn_at=horn_at,
            overlap_counts=overlap,
        )
        _add_markers(
            fig,
            tape.fills,
            kind="fill",
            side=side,
            row=row,
            prefix=prefix,
            horn_at=horn_at,
            overlap_counts=overlap,
        )
    fig.update_yaxes(title_text="price", row=row, col=1)


def _add_pred_panel(
    fig: go.Figure, tape: TokenTape, row: int, min_abs_delta: float | None, horn_at: datetime
) -> None:
    _add_line(fig, tape.pred_cents, "pred", FAIR_COLOR, row, horn_at)
    if min_abs_delta is not None and tape.pred_cents.seconds:
        gate = min_abs_delta * 100.0
        x_start = tape.pred_cents.seconds[0]
        x_end = tape.pred_cents.seconds[-1]
        for y in (gate, -gate):
            fig.add_trace(
                go.Scatter(
                    x=_utc_x(horn_at, (x_start, x_end)),
                    y=[y, y],
                    mode="lines",
                    name=f"gate {gate:.1f}¢",
                    line={"color": "#888888", "width": 1, "dash": "dot"},
                    showlegend=False,
                ),
                row=row,
                col=1,
            )
    fig.update_yaxes(title_text="¢", row=row, col=1)


def _add_gold_panel(fig: go.Figure, state: GameState, row: int, horn_at: datetime) -> None:
    _add_line(
        fig,
        _state_line(state, state.radiant_nw),
        "radiant NW",
        "#2ca02c",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    _add_line(
        fig,
        _state_line(state, state.dire_nw),
        "dire NW",
        "#d62728",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    fig.update_yaxes(title_text="NW", row=row, col=1)


def _add_adv_panel(fig: go.Figure, state: GameState, row: int, horn_at: datetime) -> None:
    _add_line(
        fig,
        _state_line(state, state.radiant_nw_adv),
        "NW adv",
        "#1f77b4",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    _add_line(
        fig,
        _state_line(state, state.top1_nw_adv),
        "top1 NW adv",
        "#ff7f0e",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    _add_line(
        fig,
        _state_line(state, state.radiant_xp_adv),
        "XP adv",
        "#9467bd",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    fig.add_hline(y=0, line={"color": "#888888", "width": 1, "dash": "dot"}, row=row, col=1)
    fig.update_yaxes(title_text="adv", row=row, col=1)


def _add_deaths_panel(fig: go.Figure, state: GameState, row: int, horn_at: datetime) -> None:
    _add_line(
        fig,
        _state_line(state, state.deaths_radiant),
        "radiant deaths",
        "#2ca02c",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    _add_line(
        fig,
        _state_line(state, state.deaths_dire),
        "dire deaths",
        "#d62728",
        row,
        horn_at,
        customdata=state.game_seconds,
    )
    fig.update_yaxes(title_text="deaths", row=row, col=1)


def plot_match(
    tapes: tuple[TokenTape, ...],
    state: GameState,
    show_pred: bool,
    show_gold: bool,
    show_kills: bool,
    min_abs_delta: float | None,
    horn_at: datetime,
) -> go.Figure:
    """One figure so price, pred, gold, and deaths zoom and pan together."""
    panels = _panel_rows(tapes, show_pred, show_gold, show_kills)
    n_rows = len(panels)
    total_weight = sum(panel.weight for panel in panels)
    spacing = min(0.06, 0.12 / max(n_rows - 1, 1))
    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=True,
        row_heights=[panel.weight / total_weight for panel in panels],
        subplot_titles=tuple(panel.title for panel in panels),
        vertical_spacing=spacing,
    )
    prefix_sides = len(tapes) > 1
    for index, panel in enumerate(panels, start=1):
        if panel.kind == "price":
            tape = panel.tape
            assert tape is not None
            prefix = f"{tape.side_name} " if prefix_sides else ""
            _add_price_panel(fig, tape, index, prefix, horn_at)
        elif panel.kind == "pred":
            tape = panel.tape
            assert tape is not None
            _add_pred_panel(fig, tape, index, min_abs_delta, horn_at)
        elif panel.kind == "gold":
            _add_gold_panel(fig, state, index, horn_at)
        elif panel.kind == "adv":
            _add_adv_panel(fig, state, index, horn_at)
        else:
            _add_deaths_panel(fig, state, index, horn_at)
    fig.update_xaxes(title_text="UTC", row=n_rows, col=1)
    window = _initial_xrange(tapes, state)
    if window is not None:
        left, right = window
        for index, panel in enumerate(panels, start=1):
            extent = _panel_yrange(panel, state, left, right)
            if extent is not None:
                fig.update_yaxes(range=extent, row=index, col=1)
        fig.update_xaxes(range=_utc_x(horn_at, (left, right)))
    fig.update_layout(
        height=max(360, int(200 * total_weight)),
        legend={"orientation": "h"},
        margin={"t": 40, "b": 40},
    )
    return fig


def plot_token_tape(
    tape: TokenTape, min_abs_delta: float | None, show_pred: bool, horn_at: datetime
) -> go.Figure:
    """Price panel, plus predicted-delta cents when show_pred is on."""
    return plot_match((tape,), empty_game_state(), show_pred, False, False, min_abs_delta, horn_at)


def plot_game_state(
    state: GameState, show_gold: bool, show_kills: bool, horn_at: datetime
) -> go.Figure:
    """Net worth, advantage, and team deaths panels."""
    return plot_match((), state, False, show_gold, show_kills, None, horn_at)
