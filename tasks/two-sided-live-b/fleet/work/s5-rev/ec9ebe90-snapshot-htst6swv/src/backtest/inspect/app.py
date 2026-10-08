"""Streamlit inspector: pick a maker backtest, seed, and match."""

# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false

from pathlib import Path

import pandas as pd
import streamlit as st

from backtest.inspect.catalog import BacktestRun, Game, game_root, list_runs
from backtest.inspect.metrics import HeadlineMetrics, load_run_metrics, load_seed_metrics
from backtest.inspect.tail import SeedTail, TailMatch, load_seed_tail
from backtest.inspect.tape import load_game_state, load_match_tapes
from shared.utils.match_time import parse_utc
from viewer.plot import (
    plot_game_state as plot_game_state,
)
from viewer.plot import (
    plot_match,
)
from viewer.plot import (
    plot_token_tape as plot_token_tape,
)
from viewer.types import GameState, TokenTape, empty_game_state


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 0:
        return f"-${abs(value):.2f}"
    return f"${value:.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _seconds(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0f}s"


def _count(value: int | None) -> str:
    if value is None:
        return "n/a"
    return str(value)


def render_metrics(metrics: HeadlineMetrics, *, title: str) -> None:
    """Report-style headline block above the match table."""
    st.markdown(f"**{title}**")
    rows = [
        ("completed", _count(metrics.completed)),
        ("traded", _count(metrics.traded)),
        ("no-trade", _count(metrics.no_trades)),
        ("terminated", _count(metrics.terminated)),
        ("buy fills", _count(metrics.buy_fills)),
        ("sell fills", _count(metrics.sell_fills)),
        ("turnover", _money(metrics.buy_turnover)),
        ("pnl before rebate", _money(metrics.pnl_before_rebate)),
        ("rebate", _money(metrics.maker_rebate)),
        ("net", _money(metrics.net_pnl)),
        ("median match", _money(metrics.median_match_pnl)),
        ("pnl / match", _money(metrics.pnl_per_match)),
        ("deposit w/ reserves", _money(metrics.deposit_with_reserves)),
        ("ROI with rebate", _pct(metrics.roi_with_rebate)),
        ("loss rate", _pct(metrics.loss_match_rate)),
        ("CVaR 5%", _money(metrics.cvar_5)),
        ("worst match", _money(metrics.worst_match)),
        ("hold p50", _seconds(metrics.hold_p50_seconds)),
    ]
    if metrics.live_equity_sum is not None:
        rows.extend(
            [
                ("Σ live equity", _money(metrics.live_equity_sum)),
                ("vs live", _money(metrics.vs_live)),
            ]
        )
    # Drop rows that are entirely n/a so live-cohort stays compact.
    visible = [(label, value) for label, value in rows if value != "n/a"]
    if not visible:
        return
    cols = st.columns(4)
    for index, (label, value) in enumerate(visible):
        cols[index % 4].metric(label, value)


def _run_label(run: BacktestRun) -> str:
    net = _money(run.net_pnl)
    cvar = _money(run.cvar_5)
    return f"{run.name}  ·  {len(run.seeds)} seeds  ·  net {net}  ·  CVaR {cvar}"


_VS_LIVE_EPS = 1e-6


def _vs_live_delta(match: TailMatch) -> float | None:
    """Sim PnL minus live equity; None when no live ref or effectively equal."""
    if match.live_equity is None:
        return None
    delta = float(match.engine_pnl) - float(match.live_equity)
    if abs(delta) < _VS_LIVE_EPS:
        return None
    return round(delta, 4)


def _tail_frame(tail: SeedTail) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    has_live = any(match.live_equity is not None for match in tail.matches)
    for match in tail.matches:
        row: dict[str, object] = {
            "match_id": match.match_id,
            "slug": match.slug,
            "engine_pnl": match.engine_pnl,
            "buy_fills": match.buy_fills,
            "sell_fills": match.sell_fills,
            "horn_at": match.horn_at,
        }
        if has_live:
            # Blank when ~0 so the column only lights up on real gaps.
            row["vs_live"] = _vs_live_delta(match)
        rows.append(row)
    return pd.DataFrame(rows)


@st.cache_data(max_entries=32, ttl="1m")
def _cached_runs(game: Game, root: str) -> tuple[BacktestRun, ...]:
    return list_runs(game=game, root=Path(root))


@st.cache_data(max_entries=64)
def _cached_seed_metrics(seed_dir: str, seed: int) -> HeadlineMetrics | None:
    return load_seed_metrics(Path(seed_dir), seed=seed)


@st.cache_data(max_entries=64)
def _cached_run_metrics(run_dir: str, seeds: tuple[int, ...]) -> HeadlineMetrics | None:
    return load_run_metrics(Path(run_dir), seeds)


@st.cache_data(max_entries=64, ttl="2m")
def _cached_tail(seed_dir: str, seed: int) -> SeedTail:
    return load_seed_tail(Path(seed_dir), seed)


@st.cache_data(max_entries=32, ttl="2m", show_spinner="Loading match tape…")
def _cached_tapes(
    game: Game, seed_dir: str, match_id: int, horn_at: str, game_ended_at: str
) -> tuple[TokenTape, ...]:
    return load_match_tapes(
        game=game,
        seed_dir=Path(seed_dir),
        match_id=match_id,
        horn_at=horn_at,
        game_ended_at=game_ended_at,
    )


@st.cache_data(max_entries=32, show_spinner="Loading game state…")
def _cached_state(game: Game, match_id: int, horn_at: str) -> GameState:
    return load_game_state(game, match_id, horn_at)


PANEL_PRED = "Pred"
PANEL_GOLD = "Gold / XP"
PANEL_KILLS = "Deaths"


def render_match(
    game: Game, seed_dir: Path, match: TailMatch, min_abs_delta: float | None, panels_key: str
) -> None:
    """Load and plot one map."""
    st.subheader(match.slug)
    vs = _vs_live_delta(match)
    vs_bit = f"  ·  vs live {_money(vs)}" if vs is not None else ""
    st.caption(
        f"match {match.match_id}  ·  engine PnL {_money(match.engine_pnl)}  ·  "
        f"buy {match.buy_fills} / sell {match.sell_fills}{vs_bit}"
    )
    selected = st.pills(
        "Panels",
        [PANEL_PRED, PANEL_GOLD, PANEL_KILLS],
        selection_mode="multi",
        default=[PANEL_PRED, PANEL_GOLD, PANEL_KILLS],
        key=panels_key,
        help="Deselect to hide a chart.",
    )
    visible = set(selected or ())
    show_pred = PANEL_PRED in visible
    show_gold = PANEL_GOLD in visible
    show_kills = PANEL_KILLS in visible
    tapes = _cached_tapes(game, str(seed_dir), match.match_id, match.horn_at, match.game_ended_at)
    if not tapes:
        st.info("No tape for this map.")
        return
    state = empty_game_state()
    if show_gold or show_kills:
        state = _cached_state(game, match.match_id, match.horn_at)
        if not state.seconds:
            st.info("No gold/XP/deaths in the validation dataset for this map.")
            show_gold = False
            show_kills = False
    horn_dt = parse_utc(match.horn_at)
    zoom_key = f"{panels_key}-zoom"
    if st.button("Reset zoom", key=f"{panels_key}-reset", icon=":material/zoom_out_map:"):
        st.session_state[zoom_key] = st.session_state.get(zoom_key, 0) + 1
    st.plotly_chart(
        plot_match(tapes, state, show_pred, show_gold, show_kills, min_abs_delta, horn_dt),
        width="stretch",
        key=f"{panels_key}-chart-{st.session_state.get(zoom_key, 0)}",
    )
    st.caption(
        "Blue / orange / green bars are resting BUY rungs. "
        "Empty triangle = submit. Black triangle = fill (we got hit). "
        "Deaths are the model features (Radiant/Dire deaths), not opponent kills. "
        "All panels: bot receipt time, UTC. "
        "Hover on gold/adv/deaths shows the feed game second. "
        "Reset zoom returns to the traded window."
    )


def render_seed(game: Game, run: BacktestRun, seed: int, *, show_seed_metrics: bool) -> None:
    """Match table and picker for one seed."""
    seed_dir = run.path / f"seed{seed}"
    if show_seed_metrics:
        seed_metrics = _cached_seed_metrics(str(seed_dir), seed)
        if seed_metrics is not None:
            render_metrics(seed_metrics, title=f"Seed{seed}")
    tail = _cached_tail(str(seed_dir), seed)
    if not tail.matches:
        st.info("No completed maps.")
        return
    table = st.dataframe(
        _tail_frame(tail),
        hide_index=True,
        width="stretch",
        key=f"match-table-{run.name}-{seed}",
        on_select="rerun",
        selection_mode="single-row",
        selection_default={"selection": {"rows": [0], "columns": [], "cells": []}},
    )
    rows = table.selection.rows
    match = tail.matches[rows[0] if rows else 0]
    render_match(
        game,
        seed_dir,
        match,
        tail.min_abs_delta,
        panels_key=f"panels-{game}-{run.name}-{seed}",
    )


def render_game(game: Game) -> None:
    """Run list for one title, then seed tabs."""
    root = game_root(game)
    runs = _cached_runs(game, str(root))
    if not runs:
        st.info(f"No backtests in {root}")
        return
    labels = [_run_label(run) for run in runs]
    chosen = st.selectbox("Backtest", labels, key=f"run-{game}")
    run = runs[labels.index(chosen)]
    run_metrics = _cached_run_metrics(str(run.path), run.seeds)
    if run_metrics is not None:
        title = "Run" if len(run.seeds) > 1 else "Run / seed0"
        render_metrics(run_metrics, title=title)
    show_seed_metrics = len(run.seeds) > 1
    if len(run.seeds) == 1:
        render_seed(game, run, run.seeds[0], show_seed_metrics=False)
        return
    seed_labels = [f"seed{seed}" for seed in run.seeds]
    tabs = st.tabs(seed_labels, on_change="rerun", key=f"seeds-{game}-{run.name}")
    for tab, seed in zip(tabs, run.seeds, strict=True):
        if tab.open:
            with tab:
                render_seed(game, run, seed, show_seed_metrics=show_seed_metrics)


def main() -> None:
    """Home: Dota / LoL backtest picker."""
    st.set_page_config(page_title="Backtest match inspector", layout="wide")
    st.title("Backtest match inspector")
    st.caption("Completed maps by engine PnL, worst first, before rebate.")
    dota_tab, lol_tab = st.tabs(["Dota", "LoL"], on_change="rerun", key="game-tabs")
    if dota_tab.open:
        with dota_tab:
            render_game("dota")
    if lol_tab.open:
        with lol_tab:
            render_game("lol")


if __name__ == "__main__":
    main()
