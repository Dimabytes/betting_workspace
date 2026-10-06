
import time
from typing import cast

import streamlit as st

from dashboard import match_view
from dashboard.catalog import MapView
from dashboard.game_state import GameStateReader
from dashboard.game_types import GameSummary
from dashboard.home import find_match, fmt_age
from dashboard.home_diag import build_match_diagnostics
from dashboard.hub_types import HubSnapshot
from dashboard.live_hub import LiveHub
from dashboard.match_history import (
    ChartFacts,
    archive_stamps,
    cached_chart,
    cached_journal,
    project_events,
)
from dashboard.match_state import (
    MatchObserver,
    build_match_state,
    link_session,
    sell_held_map,
    wallet_stale,
)
from dashboard.match_trace import read_advisory
from dashboard.tails import TailCache
from trader.paths import CORE_TRACE_FILENAME, SESSION_JOURNAL_FILENAME
from viewer.plot import plot_match
from viewer.types import empty_game_state

CHART_HEIGHT = 300
_CHART_MEMO_KEY = "mp-chart-memo"
_OBSERVER_KEY = "mp-observer"


@st.cache_resource
def _game_reader() -> GameStateReader:
    return GameStateReader(TailCache())


@st.cache_resource
def _advisory_tails() -> TailCache:
    return TailCache()


def _observer() -> MatchObserver:
    observer = st.session_state.get(_OBSERVER_KEY)
    if not isinstance(observer, MatchObserver):
        observer = MatchObserver()
        st.session_state[_OBSERVER_KEY] = observer
    return observer


def _resolve(snap: HubSnapshot, match_id: str) -> MapView | None:
    matches = find_match(snap, match_id)
    return matches[0] if len(matches) == 1 else None


def _chart_memo() -> dict[str, ChartFacts]:
    memo = cast(dict[str, ChartFacts] | None, st.session_state.get(_CHART_MEMO_KEY))
    if memo is None:
        memo = {}
        st.session_state[_CHART_MEMO_KEY] = memo
    return memo


def _summary_notes(summary: GameSummary | None) -> tuple[str, ...]:
    if summary is None:
        return ()
    notes = list(summary.decision.notes)
    notes.extend(summary.source.evidence)
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
        now_s=now_s,
    )
    diag = build_match_diagnostics(
        snap, view, session, _observer().wedge, _summary_notes(summary), now_s
    )
    match_view.render_header(state, diag)
    cols = st.columns([1.1, 1.0])
    with cols[0]:
        match_view.render_game(summary, key_prefix=f"mp-{match_id}")
    with cols[1]:
        match_view.render_buy(state)
        match_view.render_ages(state)
    match_view.render_details(state, key=f"mp-details-{match_id}")


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
        orders=session.orders if session is not None else (),
        sell_held=sell_held_map(session) if session is not None else {},
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
        now_s=now_s,
    )
    match_view.render_now(state)
    cols = st.columns([1.0, 1.4])
    with cols[0]:
        match_view.render_position(state)
        match_view.render_orders(state, key=f"mp-orders-{match_id}")
    with cols[1]:
        book_cols = st.columns(2)
        for idx, panel in enumerate(state.books):
            with book_cols[idx]:
                match_view.render_book(panel, key=f"mp-book-{match_id}-{panel.side}")


@st.fragment(run_every=15)
def _chart(hub: LiveHub, match_id: str) -> None:
    exp = st.expander("график", on_change="rerun", key=f"mp-chart-{match_id}")
    if not exp.open:
        return
    snap = hub.get_snapshot()
    view = _resolve(snap, match_id)
    if view is None:
        return
    entry = view.entry
    facts = cached_chart(str(entry.archive_dir), archive_stamps(entry.archive_dir))
    memo = _chart_memo()
    now_s = time.time()
    if not facts.ok:
        last = memo.get(str(entry.archive_dir))
        if last is None:
            match_view.render_chart_unavailable(facts.error or "нет данных")
            return
        facts = last
        st.caption(
            f"чтение не удалось — показан график от "
            f"{fmt_age(max(0.0, now_s - facts.read_at))} назад"
        )
    else:
        memo[str(entry.archive_dir)] = facts
        while len(memo) > 4:
            memo.pop(next(iter(memo)))
    with exp:
        if facts.horn_at is None:
            match_view.render_chart_unavailable("horn_at_utc не записан")
            return
        fig = plot_match(
            facts.tapes,
            empty_game_state(),
            show_pred=True,
            show_gold=False,
            show_kills=False,
            min_abs_delta=(entry.params.min_abs_delta if entry.params is not None else None),
            horn_at=facts.horn_at,
        )
        fig.update_layout(height=CHART_HEIGHT, uirevision=str(entry.archive_dir))
        st.plotly_chart(fig, key=f"mp-chart-fig-{match_id}")
        for note in facts.notes:
            st.caption(note)


@st.fragment(run_every=15)
def _events(hub: LiveHub, match_id: str) -> None:
    exp = st.expander("лента событий", on_change="rerun", key=f"mp-events-{match_id}")
    if not exp.open:
        return
    snap = hub.get_snapshot()
    view = _resolve(snap, match_id)
    if view is None:
        return
    entry = view.entry
    with exp:
        full = st.toggle("полная история", key=f"mp-events-full-{match_id}")
        read = cached_journal(
            str(entry.archive_dir),
            SESSION_JOURNAL_FILENAME,
            bool(full),
            archive_stamps(entry.archive_dir),
        )
        if not read.ok:
            match_view.render_events((), read.truncated, read.error)
            return
        rows = project_events(
            read.records,
            now_s=time.time(),
            initial_unknown=read.truncated or not full,
        )
        capped = len(rows) >= 40
        match_view.render_events(rows, read.truncated, None)
        if capped:
            st.caption("показаны последние 40 событий")


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
    _chart(hub, match_id)
    _events(hub, match_id)
