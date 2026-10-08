import html

import streamlit as st

from dashboard.fresh_view import render_fresh
from dashboard.game_types import GameSummary, Objectives, PlayerSummary, SideSlice
from dashboard.home import fmt_age, fmt_clock, fmt_second, fmt_usd
from dashboard.home_diag import Diagnostic, DiagnosticsView, status_text
from dashboard.match_state import BookPanel, LadderRow, MatchState, PositionBlock

_SEVERITY_TEXT = {"fatal": "FATAL", "error": "ОШИБКА", "warn": "ВНИМ", "info": "инфо"}

_SIDE_CLASS = {"Radiant": "side0", "Blue": "side0", "Dire": "side1", "Red": "side1"}

_CSS = """
.mp { font-variant-numeric: tabular-nums; font-size: 0.92rem; line-height: 1.35; }
.mp-table { border-collapse: collapse; width: 100%; table-layout: fixed; }
@media (max-width: 900px) {
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .mp-decision) {
    flex-wrap: wrap;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .mp-decision)
    > [data-testid="stColumn"] {
    flex: 1 1 100% !important;
    width: 100% !important;
    min-width: 0 !important;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .mp-decision)
    > [data-testid="stColumn"]:has(.mp-decision) {
    order: -1;
  }
}
.mp-table td, .mp-table th {
  padding: 0.05rem 0.35rem; text-align: right; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 8rem;
}
.mp-table th { color: #98989D; font-weight: normal; text-align: right; }
.mp-table td.name { text-align: left; }
.mp > div:first-child { margin-bottom: 4px; }
.mp-side0 { color: #30D158; }
.mp-side1 { color: #FF453A; }
.mp-own { color: #FF9F0A; }
.mp-own-row td { background: rgba(255, 159, 10, 0.14); }
.mp-muted { color: #98989D; }
.mp-book-head { color: #98989D; padding: 0.1rem 0 0.2rem; }
.mp-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 1.15rem;
  line-height: 1.3;
}
.mp-status {
  flex: none;
  font-size: 12px;
  font-weight: 600;
  line-height: 16px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(152, 152, 157, 0.18);
  color: #98989D;
}
.mp-status[data-status="live"] { background: rgba(48, 209, 88, 0.16); color: #30D158; }
.mp-status[data-status="stale"] { background: rgba(255, 159, 10, 0.16); color: #FF9F0A; }
[data-testid="stHorizontalBlock"]:has(.mp-title) {
  align-items: center;
  gap: 8px;
}
[data-testid="stHorizontalBlock"]:has(.mp-title) > [data-testid="stColumn"]:first-child {
  flex: 1 1 auto !important;
  width: auto !important;
  min-width: 0 !important;
}
[data-testid="stHorizontalBlock"]:has(.mp-title) > [data-testid="stColumn"]:not(:first-child) {
  flex: 0 0 auto !important;
  width: auto !important;
  min-width: 0 !important;
}
.mp-now { padding-top: 3.25rem; }
.mp-decision {
  padding: 0;
  background: transparent;
  text-align: left;
}
.mp-decision-row {
  font-size: 18px;
  font-weight: 650;
  letter-spacing: -0.02em;
  line-height: 24px;
  color: #F5F5F7;
}
.mp-decision-k {
  color: #98989D;
  font-weight: 500;
  font-size: 13px;
  margin-right: 8px;
}
.mp-decision-row[data-tone="go"] { color: #30D158; }
.mp-decision-row[data-tone="sell"] { color: #FF9F0A; }
.mp-decision-row[data-tone="unknown"] { color: #98989D; }
.mp-dead td { color: #6e6e73; }
.mp-timer { color: #FF9F0A; font-weight: 600; }
.mp-decision, .mp-decision-sub, .mp-decision-meta {
  overflow-wrap: anywhere;
  white-space: normal;
}
.mp-decision-sub, .mp-decision-meta { color: #98989D; font-size: 12px; line-height: 16px; }
[data-testid="stStatusWidget"] { display: none !important; }
[data-testid="stElementContainer"][data-stale="true"] {
  opacity: 1 !important;
  transition: none !important;
}
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _qty(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _price(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def _status_line(diag: DiagnosticsView) -> str:
    return status_text(diag)


def _status_pill(status: str) -> str:
    return f"<span class='mp-status' data-status='{_esc(status)}'>{_esc(status)}</span>"


def render_header(state: MatchState, diag: DiagnosticsView, now_s: float) -> None:
    cols = st.columns([6, 1, 1], gap="small", vertical_alignment="center")
    with cols[0]:
        st.markdown(
            f"<div class='mp-title'><b>{_esc(state.title)}</b>{_status_pill(state.map_status)}</div>",
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.link_button("← главная", "/")
    with cols[2]:
        if state.poly_url is not None:
            st.link_button("polymarket", state.poly_url)
    trader = _status_line(diag)
    if trader:
        st.caption(_esc(trader))
    if state.orientation_note:
        st.caption(f"⚠ {_esc(state.orientation_note)}")
    for note in state.notes:
        st.caption(f"⚠ {_esc(note)}")
    serious = [finding for finding in diag.findings if finding.severity != "info"]
    if serious:
        shown = len(serious)
        title = (
            f"Проблемы ({shown} из {diag.total})" if shown < diag.total else f"Проблемы ({shown})"
        )
        exp = st.expander(title, on_change="rerun", key="mp-diag")
        if exp.open:
            with exp:
                for finding in serious:
                    _finding_block(finding, now_s)


def _finding_block(finding: Diagnostic, now_s: float) -> None:
    age = "время ?" if finding.ts is None else fmt_age(now_s - finding.ts)
    st.write(f"**{_SEVERITY_TEXT[finding.severity]}** · {_esc(finding.label)}")
    st.caption(f"{_esc(finding.source)} · {age} назад")
    for detail in finding.details:
        st.write(f"· {_esc(detail)}")


def render_now(state: MatchState) -> None:
    line = f"сейчас: {state.now.position_line}"
    if state.now.wedge:
        detail = state.now.sell_detail or state.now.sell_line
        if detail:
            line = f"{line} · {detail}"
        st.markdown("<div class='mp-now'></div>", unsafe_allow_html=True)
        st.warning(_esc(line))
        return
    st.markdown(f"<div class='mp-now'><b>{_esc(line)}</b></div>", unsafe_allow_html=True)


_WORKING = frozenset({"live", "pending", "canceling"})


def _quoting(state: MatchState, side: str) -> bool:
    return any(
        order.side == side and order.status in _WORKING and order.remaining > 0.0
        for order in state.orders
    )


def _buy_label(state: MatchState) -> str:
    if state.buy.block == "нет данных" and not _quoting(state, "BUY"):
        return "нет данных"
    if state.buy.block == "вход разрешён" or _quoting(state, "BUY"):
        return "покупаем"
    return "не покупаем"


def _sell_label(state: MatchState) -> str:
    sell = state.now.sell_line
    if _quoting(state, "SELL") or sell.startswith("SELL") or sell == "возможное зависание выхода":
        return "продаём"
    return "не продаём"


def _tone(label: str) -> str:
    if label == "покупаем":
        return "go"
    if label == "продаём":
        return "sell"
    if label == "нет данных":
        return "unknown"
    return "hold"


def _status_row(kind: str, label: str, tone: str) -> str:
    return (
        f"<div class='mp-decision-row' data-tone='{_esc(tone)}'>"
        f"<span class='mp-decision-k'>{_esc(kind)}</span> · {_esc(label)}</div>"
    )


def _buy_note(state: MatchState) -> str:
    block = state.buy.block
    if block in ("", "нет данных", "вход разрешён"):
        block = ""
    delta = state.buy.delta_text
    if not delta.startswith("Δ ") or "не" in delta or "нет" in delta:
        delta = ""
    return " · ".join(bit for bit in (block, delta) if bit)


def _sell_note(state: MatchState, sell_label: str) -> str:
    if sell_label == "продаём":
        return state.now.sell_detail or state.now.sell_line
    if state.now.sell_line in ("позиция не открыта", "SELL неизвестен", ""):
        return ""
    return state.now.sell_line


def render_buy(state: MatchState) -> None:
    buy_label = _buy_label(state)
    sell_label = _sell_label(state)
    st.markdown(
        "<div class='mp-decision'>"
        + _status_row("покупка", buy_label, _tone(buy_label))
        + f"<div class='mp-decision-sub'>{_esc(_buy_note(state))}</div>"
        + _status_row("продажа", sell_label, _tone(sell_label))
        + f"<div class='mp-decision-sub'>{_esc(_sell_note(state, sell_label))}</div>"
        + "</div>",
        unsafe_allow_html=True,
    )


def _objectives_rows(objectives: Objectives | None) -> str:
    if objectives is None:
        return "<span class='mp-muted'>нет данных</span>"
    towers = float(objectives.towers) if objectives.towers is not None else None
    barracks = float(objectives.barracks) if objectives.barracks is not None else None
    roshans = float(objectives.roshans) if objectives.roshans is not None else None
    return f"башни {_qty(towers)} · бараки {_qty(barracks)} · рошаны {_qty(roshans)}"


def _players_html(players: tuple[PlayerSummary, ...]) -> str:
    # LoL portraits are uuid icons, so the column is only worth its space on Dota.
    show_hero = any(player.hero or player.hero_compact for player in players)
    rows: list[str] = []
    if show_hero:
        rows.append(
            "<colgroup><col style='width:32%'><col style='width:30%'>"
            "<col style='width:18%'><col style='width:20%'></colgroup>"
            "<tr><th class='name'>игрок</th><th>герой</th><th>NW</th><th>K/D/A</th></tr>"
        )
    else:
        rows.append(
            "<colgroup><col style='width:44%'><col style='width:28%'>"
            "<col style='width:28%'></colgroup>"
            "<tr><th class='name'>игрок</th><th>NW</th><th>K/D/A</th></tr>"
        )
    if not players:
        colspan = 4 if show_hero else 3
        rows.append(f"<tr><td class='name mp-muted' colspan='{colspan}'>нет данных</td></tr>")
    for player in players:
        nick = player.nick or player.nick_compact or "?"
        hero = player.hero or player.hero_compact or "—"
        kills = float(player.kills) if player.kills is not None else None
        deaths = float(player.deaths) if player.deaths is not None else None
        assists = float(player.assists) if player.assists is not None else None
        net_worth = float(player.net_worth) if player.net_worth is not None else None
        kda = f"{_qty(kills)}/{_qty(deaths)}/{_qty(assists)}"
        hero_text = _esc(player.hero_compact or hero)
        dead = player.alive is False and player.respawn is not None and player.respawn > 0
        if dead:
            hero_text += f" <span class='mp-timer'>({player.respawn}с)</span>"
        cls = " class='mp-dead'" if dead else ""
        title = _esc(f"{player.nick or ''} · {player.hero or ''}")
        hero_cell = f"<td>{hero_text}</td>" if show_hero else ""
        rows.append(
            f"<tr{cls} title='{title}'>"
            f"<td class='name'>{_esc(player.nick_compact or nick)}</td>"
            f"{hero_cell}"
            f"<td>{_qty(net_worth)}</td>"
            f"<td>{kda}</td></tr>"
        )
    return f"<table class='mp-table'>{''.join(rows)}</table>"


def _side_block(side: SideSlice, objectives: Objectives | None) -> None:
    cls = _SIDE_CLASS.get(side.label, "")
    team = side.team_name or "—"
    # players_gold is the model input on every feed; gold is Oddin's team cell.
    gold_value = side.players_gold if side.players_gold is not None else side.gold
    gold = _qty(float(gold_value) if gold_value is not None else None)
    stats = f"gold {gold} · {_objectives_rows(objectives)}"
    if side.players_deaths is not None:
        stats += f" · смерти {side.players_deaths:g}"
    st.markdown(
        "<div class='mp'>"
        f"<div><span class='mp-{cls or 'muted'}'>"
        f"<b>{_esc(side.label)}</b> {_esc(team)}</span></div>"
        f"<div class='mp-muted'>{stats}</div>"
        f"{_players_html(side.players)}"
        "</div>",
        unsafe_allow_html=True,
    )


def _lead_span(value: int) -> str:
    if value > 0:
        cls = "mp-side0"
    elif value < 0:
        cls = "mp-side1"
    else:
        cls = "mp-muted"
    return f"<span class='{cls}'>{value:+d}</span>"


def _score_span(left: int, right: int) -> str:
    return f"<span class='mp-side0'>{left}</span>:<span class='mp-side1'>{right}</span>"


def _summary_html(summary: GameSummary) -> str:
    decision = summary.decision
    board = summary.board
    second = float(decision.second) if decision.second is not None else None
    parts = [f"t={_esc(fmt_second(second))}"]
    if decision.phase and decision.phase != "in_progress":
        parts.append(_esc(decision.phase))
    if decision.paused:
        parts.append("пауза")
    if board is not None and board.kills_0 is not None and board.kills_1 is not None:
        parts.append(f"счёт {_score_span(board.kills_0, board.kills_1)}")
    if decision.radiant_nw_adv is not None:
        parts.append(f"NW {_lead_span(decision.radiant_nw_adv)}")
    if decision.xp_status != "not_used" and decision.radiant_xp_adv is not None:
        parts.append(f"XP {_lead_span(decision.radiant_xp_adv)}")
    if board is not None and board.series is not None:
        series = board.series
        if series.score_0 is not None and series.score_1 is not None:
            parts.append(f"серия {_score_span(series.score_0, series.score_1)}")
    return " · ".join(parts)


def render_game(summary: GameSummary | None) -> None:
    if summary is None:
        st.caption("сводка игры недоступна — архив не читается")
        return
    st.markdown(_summary_html(summary), unsafe_allow_html=True)
    table = summary.table
    if table is None or (not table.side_0.players and not table.side_1.players):
        st.caption("игроки: нет данных")
        if table is None:
            return
    cols = st.columns(2)
    with cols[0]:
        _side_block(table.side_0, table.objectives_0)
    with cols[1]:
        _side_block(table.side_1, table.objectives_1)


def render_position(state: MatchState) -> None:
    position: PositionBlock = state.position
    st.markdown("**позиция**")
    if not position.wallet_ok:
        st.caption("live.db не читался — позиция неизвестна")
        return
    rows: list[str] = []
    for leg in position.legs:
        if leg.size in (None, 0.0) and leg.held_qty in (None, 0.0):
            continue
        mark = f"{leg.mark:g}" if leg.mark is not None else "—"
        side_cls = _SIDE_CLASS.get(leg.side_label or "", "muted")
        rows.append(
            f"<tr><td class='name mp-{side_cls}'><b>{_esc(leg.side)}</b> {_esc(leg.team or '')}</td>"
            f"<td>{_qty(leg.size)}</td>"
            f"<td>{_price(leg.avg_price)}</td>"
            f"<td title='{_esc(leg.mark_state)}'>{mark}</td>"
            f"<td>{_esc(fmt_usd(leg.unrealized, signed=True))}</td></tr>"
        )
    if rows:
        st.markdown(
            "<div class='mp'><table class='mp-table'>"
            "<tr><th class='name'>исход</th><th>размер</th><th>ср.</th>"
            "<th>мид</th><th>uPnL</th></tr>"
            f"{''.join(rows)}</table></div>",
            unsafe_allow_html=True,
        )
    elif position.closed:
        st.markdown("**закрыто**")
    else:
        st.caption("нет позиции")
    st.caption(
        f"realized {_esc(position.realized_text.replace('$', '\\$'))}"
        f" · rebate {_esc(position.rebate_text.replace('$', '\\$'))}"
        + (f" · net {_esc(position.net_text.replace('$', '\\$'))}" if position.net_text else "")
    )


def _ladder_rows(rows: tuple[LadderRow, ...], *, empty: str) -> str:
    if not rows:
        return f"<tr><td class='mp-muted' colspan='3'>{empty}</td></tr>"
    out: list[str] = []
    for row in rows:
        own = ""
        cls = ""
        if row.own_qty > 0.0:
            own = f"наши {row.own_qty:g}"
            cls = " class='mp-own-row'"
        title = _esc(row.own_note) if row.own_note else ""
        out.append(
            f"<tr{cls} title='{title}'><td>{_price(row.price)}</td>"
            f"<td>{_qty(row.size)}</td><td class='mp-own'>{_esc(own)}</td></tr>"
        )
    return "".join(out)


def render_book(panel: BookPanel, key: str) -> None:
    st.markdown(f"**{_esc(panel.label)}**")
    age = fmt_age(panel.change_age_s) if panel.change_age_s is not None else "возраст ?"
    render_fresh(key, f"{panel.state} · изм. {age} назад", panel.fresh, None)
    asks = _ladder_rows(tuple(reversed(panel.asks)), empty="asks пусто")
    bids = _ladder_rows(panel.bids, empty="bids пусто")
    extra = ""
    if panel.own_extra:
        extra_rows = "".join(
            f"<tr class='mp-own-row'><td>{_price(row.price)}</td>"
            f"<td class='mp-muted'>вне уровней</td>"
            f"<td class='mp-own'>{_esc(row.own_note)}</td></tr>"
            for row in panel.own_extra
        )
        extra = f"<tr><td class='mp-muted' colspan='3'>—</td></tr>{extra_rows}"
    st.markdown(
        "<div class='mp'>"
        "<div class='mp-book-head'>ask ↑</div>"
        f"<table class='mp-table'>{asks}</table>"
        "<div class='mp-book-head'>bid ↓</div>"
        f"<table class='mp-table'>{bids}{extra}</table></div>",
        unsafe_allow_html=True,
    )
    if panel.note:
        st.caption(_esc(panel.note))
    if panel.stale_orders:
        st.caption("наши ордера — последнее известное состояние checkpoint")


_ORDER_STATUS = {
    "pending": "ждём биржу",
    "unknown": "биржа не подтвердила",
    "canceling": "отменяем",
}


def render_orders(state: MatchState) -> None:
    transitional = [order for order in state.orders if order.transitional]
    if transitional:
        st.markdown("**ожидающие ордера**")
        for order in transitional:
            age = f"{order.observed_s:.0f}с" if order.observed_s is not None else "время ?"
            status = _ORDER_STATUS.get(order.status, order.status)
            st.caption(
                f"{_esc(order.side)} {_esc(order.token_label)} {_qty(order.remaining)} "
                f"@{_price(order.price)} · {_esc(status)} {age}"
            )
    if not state.orders:
        st.caption("ордеров нет")
        return
    rows: list[str] = []
    for order in state.orders:
        conflict = " ⚠" if order.binding_conflict else ""
        rows.append(
            f"<tr title='{_esc(order.order_id)}'>"
            f"<td>{_esc(order.side)}</td>"
            f"<td class='name'>{_esc(order.token_label)}</td>"
            f"<td>{_price(order.price)}</td>"
            f"<td>{_qty(order.remaining)}</td>"
            f"<td>{_esc(order.status)}{_esc(conflict)}</td></tr>"
        )
    st.markdown(
        "<div class='mp'><table class='mp-table'>"
        "<tr><th>side</th><th class='name'>токен</th><th>цена</th>"
        f"<th>остаток</th><th>статус</th></tr>{''.join(rows)}</table></div>",
        unsafe_allow_html=True,
    )


def render_fills(state: MatchState) -> None:
    fills = state.fills
    if not fills:
        return
    ordered = sorted(
        enumerate(fills), key=lambda pair: (pair[1].ts is None, pair[1].ts or 0.0, pair[0])
    )
    rows: list[str] = []
    for _idx, row in ordered[-30:]:
        side_cls = "side0" if row.side == "BUY" else "side1"
        when = fmt_clock(row.ts) if row.ts is not None else "—"
        rows.append(
            f"<tr><td class='mp-muted'>{_esc(when)}</td>"
            f"<td class='mp-{side_cls}'>{_esc(row.side)}</td>"
            f"<td class='name'>{_esc(row.token_label)}</td>"
            f"<td>{_qty(row.size)}</td>"
            f"<td>{_price(row.price)}</td>"
            f"<td>{_esc(fmt_usd(row.cash, signed=True))}</td></tr>"
        )
    title = "исполнено"
    if len(fills) > 30:
        title = f"исполнено · последние 30 из {len(fills)}"
    st.markdown(f"**{title}**")
    st.markdown(
        "<div class='mp'><table class='mp-table'>"
        "<tr><th>время</th><th>side</th><th class='name'>исход</th><th>размер</th>"
        f"<th>цена</th><th>кэш</th></tr>{''.join(rows)}</table></div>",
        unsafe_allow_html=True,
    )


def inject_css() -> None:
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
