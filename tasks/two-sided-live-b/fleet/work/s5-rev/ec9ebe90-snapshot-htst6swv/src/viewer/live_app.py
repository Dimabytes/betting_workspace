"""Streamlit inspector: pick a live trader match from data/trader."""

# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportArgumentType=false

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from shared.constants.paths import TRADER_DIR
from viewer.archive_read import horn_at_utc, read_match_document
from viewer.live_sync import (
    SYNC_TIMEOUT_SECONDS,
    SyncResult,
    sync_collector_parquet,
    sync_live_matches,
)
from viewer.live_tape import (
    Game,
    LiveMatch,
    list_live_matches,
    load_live_game_state,
    load_live_tapes,
)
from viewer.plot import plot_match
from viewer.types import GameState, TokenTape, empty_game_state

REPO_ROOT = Path(__file__).resolve().parents[2]


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value < 0:
        return f"-${abs(value):.2f}"
    return f"${value:.2f}"


def _matches_frame(matches: tuple[LiveMatch, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_id": match.match_id,
                "slug": match.slug,
                "equity": match.equity,
                "net_cash": match.net_cash,
                "buy_fills": match.buy_fills,
                "sell_fills": match.sell_fills,
                "joined_at_utc": match.joined_at_utc,
            }
            for match in matches
        ]
    )


@st.cache_data(max_entries=2, ttl="1m")
def _cached_matches(root: str) -> tuple[LiveMatch, ...]:
    return list_live_matches(Path(root))


@st.cache_data(max_entries=32, show_spinner="Loading match tape…")
def _cached_tapes(archive_dir: str) -> tuple[TokenTape, ...]:
    return load_live_tapes(Path(archive_dir))


@st.cache_data(max_entries=32, show_spinner="Loading game state…")
def _cached_state(archive_dir: str) -> GameState:
    return load_live_game_state(Path(archive_dir))


@st.cache_data(max_entries=32)
def _cached_horn(archive_dir: str) -> datetime | None:
    document = read_match_document(Path(archive_dir))
    return horn_at_utc(document) if document is not None else None


PANEL_PRED = "Pred"
PANEL_GOLD = "Gold / XP"
PANEL_KILLS = "Deaths"


def render_match(match: LiveMatch, panels_key: str) -> None:
    """Load and plot one live map."""
    st.subheader(match.slug)
    st.caption(
        f"match {match.match_id}  ·  equity {_money(match.equity)}  ·  "
        f"net {_money(match.net_cash)}  ·  buy {match.buy_fills} / sell {match.sell_fills}  ·  "
        f"joined {match.joined_at_utc}"
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
    tapes = _cached_tapes(str(match.archive_dir))
    if not tapes:
        st.info("No tape for this map.")
        return
    state = empty_game_state()
    if show_gold or show_kills:
        state = _cached_state(str(match.archive_dir))
        if not state.seconds:
            st.info("No gold/XP/deaths in the feed archive for this map.")
            show_gold = False
            show_kills = False
    horn_at = _cached_horn(str(match.archive_dir))
    if horn_at is None:
        st.info("match.json has no horn_at_utc.")
        return
    zoom_key = f"{panels_key}-zoom"
    if st.button("Reset zoom", key=f"{panels_key}-reset", icon=":material/zoom_out_map:"):
        st.session_state[zoom_key] = st.session_state.get(zoom_key, 0) + 1
    st.plotly_chart(
        plot_match(tapes, state, show_pred, show_gold, show_kills, None, horn_at),
        width="stretch",
        key=f"{panels_key}-chart-{st.session_state.get(zoom_key, 0)}",
    )
    st.caption(
        "Bars are exact rests from the core trace; matches without one show markers only. "
        "Empty triangle = submit. Black triangle = fill (we got hit). "
        "Deaths are the model features (Radiant/Dire deaths), not opponent kills. "
        "All panels: bot receipt time, UTC. "
        "Hover on gold/adv/deaths shows the feed game second. "
        "Reset zoom returns to the traded window."
    )


def render_game(game: Game, matches: tuple[LiveMatch, ...]) -> None:
    """Match table and picker for one title."""
    game_matches = tuple(match for match in matches if match.game == game)
    if not game_matches:
        st.info(f"No live matches in {TRADER_DIR}")
        return
    table = st.dataframe(
        _matches_frame(game_matches),
        hide_index=True,
        width="stretch",
        key=f"live-match-table-{game}",
        on_select="rerun",
        selection_mode="single-row",
        selection_default={"selection": {"rows": [0], "columns": [], "cells": []}},
    )
    rows = table.selection.rows
    match = game_matches[rows[0] if rows else 0]
    render_match(match, panels_key=f"live-panels-{game}")


LOG_TAIL_LINES = 40


@dataclass
class _LogTail:
    """Rolling rsync log: CR overwrites the current line, keep the last N."""

    lines: deque[str] = field(default_factory=lambda: deque(maxlen=LOG_TAIL_LINES))
    partial: str = ""

    def consume(self, text: str) -> str:
        """Fold new bytes into the visible tail and return it."""
        for char in text:
            if char == "\r":
                self.partial = ""
            elif char == "\n":
                self.lines.append(self.partial)
                self.partial = ""
            else:
                self.partial += char
        visible = list(self.lines)
        if self.partial:
            visible.append(self.partial)
        return "\n".join(visible)


def _render_sync_step(label: str, run: Callable[[Callable[[str], None]], SyncResult]) -> SyncResult:
    """Run one VPS sync inside a timeline step, streaming the log as it arrives."""
    with st.status(label, expanded=True, type="step") as status:
        log_slot = st.empty()
        tail = _LogTail()

        def on_output(text: str) -> None:
            rendered = tail.consume(text)
            if rendered:
                log_slot.code(rendered)

        result = run(on_output)
        rendered = tail.consume("")
        if result.error:
            rendered = "\n".join(part for part in (rendered, result.error) if part)
        if rendered:
            log_slot.code(rendered)
        if result.timed_out:
            status.update(label=f"{label} timed out", state="error")
            return result
        if result.returncode != 0:
            status.update(label=f"{label} failed", state="error")
            return result
        status.update(label=f"{label} synced", state="complete")
        return result


def render_sync() -> None:
    """Offer a button that pulls trader archives and collector parquet from sun."""
    if not st.button(
        "Sync matches",
        key="sync-live-matches",
        type="primary",
        icon=":material/sync:",
        help="Pull live trader archives and collector parquet from sun.",
    ):
        return
    matches = _render_sync_step(
        "Matches",
        lambda on_output: sync_live_matches(REPO_ROOT, SYNC_TIMEOUT_SECONDS, on_output),
    )
    _render_sync_step(
        "Parquet dota",
        lambda on_output: sync_collector_parquet(
            REPO_ROOT, "dota", SYNC_TIMEOUT_SECONDS, on_output
        ),
    )
    _render_sync_step(
        "Parquet lol",
        lambda on_output: sync_collector_parquet(REPO_ROOT, "lol", SYNC_TIMEOUT_SECONDS, on_output),
    )
    if matches.returncode == 0:
        st.cache_data.clear()


def main() -> None:
    """Home: Dota / LoL live match picker."""
    st.set_page_config(page_title="Live match inspector", layout="wide")
    st.title("Live match inspector")
    st.caption(
        "Live trader archives in data/trader and collector parquet — "
        "use Sync matches to refresh them from sun."
    )
    render_sync()
    matches = _cached_matches(str(TRADER_DIR))
    dota_tab, lol_tab = st.tabs(["Dota", "LoL"], on_change="rerun", key="live-game-tabs")
    if dota_tab.open:
        with dota_tab:
            render_game("dota", matches)
    if lol_tab.open:
        with lol_tab:
            render_game("lol", matches)


if __name__ == "__main__":
    main()
