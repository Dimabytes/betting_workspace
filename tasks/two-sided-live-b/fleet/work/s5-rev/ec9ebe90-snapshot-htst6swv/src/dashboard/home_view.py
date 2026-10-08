import html
from collections.abc import Callable
from typing import Any, cast

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from dashboard import home
from dashboard.home_diag import Diagnostic, DiagnosticsView, status_text
from dashboard.home_lists import (
    ActiveRow,
    ClosedRow,
    IdleRow,
    ListsView,
    ResidualRow,
)
from dashboard.home_strip import PnlSplit, StripView

_SEVERITY_TEXT = {"fatal": "FATAL", "error": "ОШИБКА", "warn": "ВНИМ", "info": "инфо"}

_dataframe: Callable[..., DeltaGenerator] = cast(Any, st).dataframe

_CHROME = """
<style>
header[data-testid="stHeader"] { background: #1C1C1E; }
.block-container {
  padding-top: 4.5rem;
  padding-bottom: 0.75rem;
  padding-left: 1rem;
  padding-right: 1rem;
  max-width: 100%;
}
[data-testid="stVerticalBlock"] { gap: 0.35rem; }
[data-testid="stHeading"] { margin: 0; }
.sb {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(0, 1.6fr) auto;
  gap: 8px 14px;
  margin-bottom: 18px;
  padding: 8px 12px;
  border-radius: 10px;
  background: rgba(44, 44, 46, 0.92);
  border: 0.5px solid rgba(255, 255, 255, 0.08);
  box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.35), 0 1px 2px rgba(0, 0, 0, 0.28);
  font-variant-numeric: tabular-nums;
}
.sb-k {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #98989D;
  font-size: 11px;
  line-height: 14px;
}
.sb-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #636366;
  flex: none;
}
.sb-cell[data-kind="status"] .sb-dot {
  width: 16px;
  height: 16px;
}
.sb-cell[data-state="fresh"] .sb-dot { background: #30D158; }
.sb-cell[data-state="updating"] .sb-dot { background: #98989D; }
.sb-cell[data-state="stale"] .sb-dot { background: #FF9F0A; }
.sb-v {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 22px;
  color: #F5F5F7;
}
.sb-cell[data-tone="up"] .sb-v { color: #30D158; }
.sb-cell[data-tone="down"] .sb-v { color: #FF453A; }
.sb-cell[data-kind="status"] { justify-self: end; }
.sb-cell[data-kind="status"] .sb-k { font-size: 13px; }
.sb-cell[data-kind="status"] .sb-v {
  font-size: 15px;
  font-weight: 600;
  line-height: 20px;
  letter-spacing: -0.01em;
  max-width: 16rem;
  white-space: normal;
}
.sb-cell[data-kind="status"] .sb-v:empty { display: none; }
.sb-sub { color: #98989D; font-size: 12px; line-height: 16px; }
.sb-loading { color: #98989D; font-size: 13px; padding: 8px 0 12px; }
.sb-loading::before {
  content: "";
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-right: 8px;
  border: 2px solid #3A3A3C;
  border-top-color: #0A84FF;
  border-radius: 50%;
  vertical-align: -2px;
  animation: sb-spin 0.8s linear infinite;
}
@keyframes sb-spin { to { transform: rotate(360deg); } }
@media (max-width: 980px) {
  .sb { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
"""


def inject_chrome() -> None:
    st.html(_CHROME)


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _tone(text: str) -> str:
    if text.startswith("$+") or text.startswith("+"):
        return "up"
    if text.startswith("$-") or (text.startswith("-") and text != "—"):
        return "down"
    return ""


def _subs(*lines: str | None) -> str:
    return "".join(f"<div class='sb-sub'>{_esc(line)}</div>" for line in lines if line)


def _cell(
    title: str,
    value: str,
    state: str,
    kind: str,
    *lines: str | None,
) -> str:
    tone = _tone(value)
    dot = "<span class='sb-dot' aria-hidden='true'></span>" if kind == "status" else ""
    return (
        f"<div class='sb-cell' data-state='{_esc(state)}' data-kind='{_esc(kind)}'"
        f" data-tone='{_esc(tone)}'>"
        f"<div class='sb-k'>{dot}{_esc(title)}</div>"
        f"<div class='sb-v'>{_esc(value)}</div>"
        f"{_subs(*lines)}</div>"
    )


def _split_line(split: PnlSplit) -> str:
    return (
        f"Realized {home.fmt_usd(split.trading, signed=True)}"
        f" · open {home.fmt_usd(split.open_mark, signed=True)}"
        f" · ребейт {home.fmt_usd(split.rebate, signed=True)}"
    )


_HEALTH_DOT = {"ok": "fresh", "warn": "stale", "error": "stale", "unknown": "no_data"}


def render_status(strip: StripView, diag: DiagnosticsView, now_s: float) -> None:
    cash = strip.collateral
    portfolio = strip.portfolio
    pnl = strip.pnl
    portfolio_line = f"портфолио {portfolio.text}"
    if portfolio.verdict.state != "fresh":
        portfolio_line = f"{portfolio_line} · {portfolio.age_text}"
    available_line = None
    if strip.available is not None:
        available_line = f"доступно {strip.available.text}"
        if strip.available.note:
            available_line = f"{available_line} · {strip.available.note}"
    pnl_lines = (
        _split_line(strip.pnl_split) if strip.pnl_split is not None else None,
        f"с {strip.pnl_since}",
        pnl.age_text,
        pnl.note,
    )
    trader = status_text(diag)
    trader_state = "fresh"
    if trader:
        trader_state = _HEALTH_DOT[diag.health_state]
        if trader_state == "fresh":
            trader_state = "stale"
    bar = (
        "<div class='sb'>"
        + _cell(
            "Кэш",
            cash.text,
            cash.verdict.state,
            "money",
            portfolio_line,
            available_line,
            cash.age_text,
        )
        + _cell("PnL сегодня", pnl.text, pnl.verdict.state, "money", *pnl_lines)
        + _cell("system state", trader, trader_state, "status")
        + "</div>"
    )
    st.markdown(bar, unsafe_allow_html=True)
    serious = [finding for finding in diag.findings if finding.severity != "info"]
    if not serious:
        return
    shown = len(serious)
    title = f"Проблемы ({shown} из {diag.total})" if shown < diag.total else f"Проблемы ({shown})"
    with st.expander(title):
        for finding in serious:
            _finding_block(finding, now_s)


def _finding_block(finding: Diagnostic, now_s: float) -> None:
    age = "время ?" if finding.ts is None else home.fmt_age(now_s - finding.ts)
    st.write(f"**{_SEVERITY_TEXT[finding.severity]}** · {finding.label}")
    st.caption(f"{finding.source} · {age} назад")
    for detail in finding.details:
        st.write(f"· {detail}")


def _loading(label: str) -> None:
    st.markdown(f"<div class='sb-loading'>{_esc(label)}</div>", unsafe_allow_html=True)


def render_active(rows: tuple[ActiveRow, ...], *, loading: bool) -> None:
    st.subheader("Торгуем сейчас")
    if loading:
        _loading("загрузка карт")
        return
    if not rows:
        st.caption("нет активных карт")
        return
    _dataframe(
        [
            {
                "карта": row.title,
                "время": home.fmt_second(row.second),
                "статус": _active_status(row),
                "net": home.fmt_usd(row.net, signed=True),
                "realized": home.fmt_usd(row.realized, signed=True),
                "uPnL": home.fmt_usd(row.unrealized_total, signed=True),
                "rebate": home.fmt_usd(row.rebate, signed=True),
                "ссылка": row.match_url,
            }
            for row in rows
        ],
        hide_index=True,
        height="content",
        column_config={
            "карта": st.column_config.TextColumn(width="large"),
            "ссылка": st.column_config.LinkColumn("карта", display_text="открыть"),
        },
    )


def _active_status(row: ActiveRow) -> str:
    bits = [row.reason]
    if row.paused:
        bits.append("пауза")
    if row.stale:
        bits.append("устарело")
    return " · ".join(bit for bit in bits if bit)


def _soon_status(row: IdleRow) -> str:
    if row.reason in ("", "причина неизвестна"):
        return ""
    return row.reason


def _soon_link(row: IdleRow) -> str | None:
    return row.match_url or row.polymarket_url


def _links(match_url: str | None, polymarket_url: str | None) -> None:
    cols = st.columns(2)
    if match_url is not None:
        cols[0].link_button("карта", match_url)
    if polymarket_url is not None:
        cols[1].link_button("polymarket", polymarket_url)


def _closed_block(rows: tuple[ClosedRow, ...]) -> None:
    _dataframe(
        [
            {
                "карта": row.title,
                "игра": row.game or "?",
                "net": home.fmt_usd(row.net, signed=True),
                "realized": home.fmt_usd(row.realized, signed=True),
                "imv": home.fmt_usd(row.imv, signed=True),
                "rebate": home.fmt_usd(row.rebate, signed=True),
                "fills": "—" if row.fill_count is None else str(row.fill_count),
                "закрыта": (
                    home.fmt_age(row.closed_age_s)
                    if row.closed_age_s is not None
                    else "дата неизвестна"
                ),
                "ссылка": row.match_url,
            }
            for row in rows
        ],
        hide_index=True,
        height="content",
        column_config={
            "карта": st.column_config.TextColumn(width="large"),
            "ссылка": st.column_config.LinkColumn("карта", display_text="открыть"),
        },
    )


def _residual_block(row: ResidualRow) -> None:
    st.caption(f"{row.redeem_state} · positions {row.positions_state}")
    if row.legs:
        _dataframe(
            [
                {
                    "исход": leg.outcome,
                    "API": f"{leg.api_size:g}" if leg.api_size is not None else "—",
                    "локально": (f"{leg.local_size:g}" if leg.local_size is not None else "—"),
                    "ср.": f"{leg.local_avg:g}" if leg.local_avg is not None else "—",
                    "мид": f"{leg.mark:g}" if leg.mark is not None else "—",
                    "источник": leg.mark_state,
                    "оценка": home.fmt_usd(leg.value),
                }
                for leg in row.legs
            ],
            hide_index=True,
            height="content",
        )
    for line in row.commitments:
        bits = [
            f"{line.source}",
            f"qty {line.qty:g}" if line.qty is not None else "qty ?",
            f"резерв {home.fmt_usd(line.remaining_notional)}",
            line.status,
        ]
        if line.age_s is not None:
            bits.append(f"возраст {home.fmt_age(line.age_s)}")
        ids = " · ".join(
            part
            for part in (
                line.venue_id,
                line.core_order_id,
                line.token_id,
            )
            if part
        )
        st.write(" · ".join(bits))
        st.caption(ids or "—")
    for line in row.details:
        st.write(f"· {line}")
    _links(row.match_url, row.polymarket_url)


def _soon_table(rows: tuple[IdleRow, ...]) -> None:
    show_status = any(_soon_status(row) for row in rows)
    soon_rows: list[dict[str, str | None]] = []
    for row in rows:
        item: dict[str, str | None] = {
            "карта": row.title,
            "старт": home.fmt_clock(row.starts_at) if row.starts_at is not None else "—",
        }
        if show_status:
            item["статус"] = _soon_status(row)
        item["ссылка"] = _soon_link(row)
        soon_rows.append(item)
    _dataframe(
        soon_rows,
        hide_index=True,
        height="content",
        column_config={
            "карта": st.column_config.TextColumn(width="large"),
            "ссылка": st.column_config.LinkColumn("ссылка", display_text="открыть"),
        },
    )


def render_lists(view: ListsView | None, *, loading: bool) -> None:
    if loading or view is None:
        _loading("загрузка списков")
        return
    st.subheader("Закрытые сегодня")
    st.caption(view.closed_since)
    if not view.closed_dated:
        st.caption("карт нет")
    else:
        _closed_block(view.closed_dated)
    if view.closed_undated:
        st.subheader(f"Без даты закрытия ({len(view.closed_undated)})")
        st.caption("закрытие не записано")
        _closed_block(view.closed_undated)

    st.subheader("Скоро")
    if view.nontrading_note is not None:
        st.caption(f"сканирование: {view.nontrading_note}")
    if not view.idle:
        st.caption("нет ближайших карт")
    else:
        _soon_table(view.idle)

    if view.residuals:
        st.subheader("Остатки завершённых карт")
        for row in view.residuals:
            with st.expander(f"{row.title} — {row.redeem_state}"):
                _residual_block(row)
    elif view.positions_state == "unknown":
        _loading("остатки завершённых карт")
