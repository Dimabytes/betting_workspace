import time

import streamlit as st

from dashboard import match_view
from dashboard.catalog import MapView
from dashboard.game_state import GameStateReader
from dashboard.game_types import GameSummary
from dashboard.home import find_match
from dashboard.home_diag import build_match_diagnostics
from dashboard.hub_types import HubSnapshot
from dashboard.live_hub import LiveHub
from dashboard.match_state import (
    FillLog,
    MatchObserver,
    build_match_state,
    link_session,
    wallet_stale,
)
from dashboard.match_trace import read_advisory
from dashboard.tails import TailCache
from trader.paths import CORE_TRACE_FILENAME, SESSION_JOURNAL_FILENAME

_OBSERVER_KEY = "mp-observer"
_FILL_LOG_KEY = "mp-fills"


@st.cache_resource
def _game_reader() -> GameStateReader:
    return GameStateReader(TailCache())


@st.cache_resource
def _advisory_tails() -> TailCache:
    return TailCache()


def _fill_log() -> FillLog:
    log = st.session_state.get(_FILL_LOG_KEY)
    if not isinstance(log, FillLog):
        log = FillLog()
        st.session_state[_FILL_LOG_KEY] = log
    return log


def _observer() -> MatchObserver:
    observer = st.session_state.get(_OBSERVER_KEY)
    if not isinstance(observer, MatchObserver):
        observer = MatchObserver()
        st.session_state[_OBSERVER_KEY] = observer
    return observer


def _resolve(snap: HubSnapshot, match_id: str) -> MapView | None:
    matches = find_match(snap, match_id)
    return matches[0] if len(matches) == 1 else None


def _summary_notes(summary: GameSummary | None) -> tuple[str, ...]:
    if summary is None:
        return ()
    notes = list(summary.decision.notes)
    notes.extend(note for note in summary.source.evidence if not note.endswith("tail truncated"))
    notes.extend(summary.source.control)
    if summary.source.rejected:
        notes.append(f"отклонено записей: {summary.source.rejected}")
    if summary.board is not None:
        notes.extend(summary.board.notes)
    if summary.table is not None:
        notes.extend(summary.table.notes)
    return tuple(notes[:12])


@st.fragment(run_every=1)
def _head(hub: LiveHub, match_id: str) -> None:
    snap = hub.get_snapshot()
    now_s = time.time()
    view = _resolve(snap, match_id)
    if view is None:
        st.caption("карта ушла из каталога — вернитесь на главную")
        return
    summary = _game_reader().read(view.entry, now_s)
    link = link_session(view.entry, snap.wallet)
    session = link.session
    advisory = read_advisory(
        _advisory_tails(),
        view.entry.archive_dir,
        match_id=view.entry.match_id,
        session_id=session.session_id if session is not None else None,
        yes_token=view.entry.yes_token,
        no_token=view.entry.no_token,
        trace_file=CORE_TRACE_FILENAME,
    )
    state = build_match_state(
        snap=snap,
        view=view,
        summary=summary,
        advisory=advisory,
        observed={},
        wedge=_observer().wedge,
        fills=_fill_log().read(view.entry.archive_dir / SESSION_JOURNAL_FILENAME),
        now_s=now_s,
    )
    diag = build_match_diagnostics(
        snap, view, session, _observer().wedge, _summary_notes(summary), now_s
    )
    match_view.render_header(state, diag, now_s)
    cols = st.columns([1.15, 0.85], gap="large", vertical_alignment="top")
    with cols[0]:
        match_view.render_game(summary)
    with cols[1]:
        match_view.render_buy(state)


@st.fragment(run_every=0.5)
def _trade(hub: LiveHub, match_id: str) -> None:
    snap = hub.get_snapshot()
    now_s = time.time()
    view = _resolve(snap, match_id)
    if view is None:
        return
    session = link_session(view.entry, snap.wallet).session
    observer = _observer()
    observer.observe(
        identity=f"{view.entry.archive_dir}:{session.session_id if session else 'none'}",
        generation=snap.generation,
        session=session,
        stale=wallet_stale(snap, now_s),
        now_s=now_s,
    )
    state = build_match_state(
        snap=snap,
        view=view,
        summary=None,
        advisory=None,
        observed=observer.first_seen,
        wedge=observer.wedge,
        fills=_fill_log().read(view.entry.archive_dir / SESSION_JOURNAL_FILENAME),
        now_s=now_s,
    )
    match_view.render_now(state)
    cols = st.columns([1.0, 1.4])
    with cols[0]:
        match_view.render_position(state)
        match_view.render_orders(state)
        match_view.render_fills(state)
    with cols[1]:
        book_cols = st.columns(2)
        for idx, panel in enumerate(state.books):
            with book_cols[idx]:
                match_view.render_book(panel, key=f"mp-book-{match_id}-{panel.side}")


def render_match_page(match_id: str, hub: LiveHub) -> None:
    match_view.inject_css()
    snap = hub.get_snapshot()
    matches = find_match(snap, match_id)
    if len(matches) > 1:
        st.title(match_id)
        st.warning("несколько архивов с этим match id — выбор неоднозначен")
        st.link_button("← на главную", "/")
        return
    if not matches:
        st.title(match_id)
        if (
            snap.subscriptions.catalog_built_at is None
            and snap.subscriptions.error is None
            and snap.error is None
        ):
            st.caption("каталог ещё загружается…")
        else:
            st.warning("карта не найдена в каталоге")
        st.link_button("← на главную", "/")
        return
    _head(hub, match_id)
    _trade(hub, match_id)
