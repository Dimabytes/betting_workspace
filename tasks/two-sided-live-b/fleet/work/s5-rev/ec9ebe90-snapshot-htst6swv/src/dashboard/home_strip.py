from dataclasses import dataclass
from typing import Literal

from dashboard import diagnostics
from dashboard.diagnostics import FreshnessInput, FreshnessVerdict, freshness
from dashboard.home import berlin_day, fmt_age, fmt_clock, fmt_usd
from dashboard.home_diag import assess_health
from dashboard.hub_types import HubSnapshot


@dataclass(frozen=True)
class FreshMetric:
    title: str
    text: str
    age_text: str
    note: str | None
    verdict: FreshnessVerdict


@dataclass(frozen=True)
class PnlSplit:
    trading: float
    open_mark: float
    rebate: float


@dataclass(frozen=True)
class HealthLine:
    state: Literal["ok", "warn", "error", "unknown"]
    text: str
    details: tuple[str, ...]


@dataclass(frozen=True)
class StripView:
    collateral: FreshMetric
    available: FreshMetric | None
    portfolio: FreshMetric
    pnl: FreshMetric
    pnl_split: PnlSplit | None
    pnl_since: str
    health: HealthLine


def _verdict(
    *,
    now_s: float,
    threshold_s: float,
    has_value: bool,
    value_ts: float | None,
    attempt_failed: bool,
    coverage_complete: bool,
    pending: bool,
    error: str | None,
) -> FreshnessVerdict:
    age = None if value_ts is None else max(0.0, now_s - value_ts)
    return freshness(
        FreshnessInput(
            now_s=now_s,
            threshold_s=threshold_s,
            has_value=has_value,
            value_age_s=age,
            attempt_failed=attempt_failed,
            coverage_complete=coverage_complete,
            pending=pending,
            book_connected=None,
            awaiting_book=False,
            error=error,
        )
    )


def _age_text(verdict: FreshnessVerdict) -> str:
    if verdict.state == "no_data":
        return "нет данных"
    if verdict.age_s is None:
        when = "время неизвестно"
    else:
        when = f"{fmt_age(verdict.age_s)} назад"
    if verdict.state == "stale":
        return f"устарел · {when}"
    if verdict.state == "updating":
        return f"обновляется · {when}"
    return when


def _collateral_metric(snap: HubSnapshot, now_s: float) -> FreshMetric:
    balance = snap.balance
    value = balance.collateral_usdc
    verdict = _verdict(
        now_s=now_s,
        threshold_s=diagnostics.BALANCE_STALE_S,
        has_value=value is not None,
        value_ts=balance.response_at,
        attempt_failed=balance.last_error is not None,
        coverage_complete=balance.evidence_gaps == 0 and not balance.funder_mismatch,
        pending=(
            balance.queued
            or balance.inflight
            or (
                balance.significant_seq is not None
                and (balance.covered_seq is None or balance.significant_seq > balance.covered_seq)
            )
        ),
        error=balance.last_error,
    )
    return FreshMetric(
        title="Кэш",
        text=fmt_usd(value),
        age_text=_age_text(verdict),
        note=None,
        verdict=verdict,
    )


def _available_metric(snap: HubSnapshot, now_s: float) -> FreshMetric:
    reserve = snap.reserve
    balance = snap.balance
    value = reserve.available_cash if reserve is not None else None
    wallet_error = None if snap.wallet.ok else snap.wallet.error
    verdict = _verdict(
        now_s=now_s,
        threshold_s=diagnostics.BALANCE_STALE_S,
        has_value=value is not None,
        value_ts=balance.response_at,
        attempt_failed=balance.last_error is not None or wallet_error is not None,
        coverage_complete=snap.wallet.ok,
        pending=balance.queued or balance.inflight,
        error=wallet_error or balance.last_error,
    )
    note = "оценка" if reserve is None or reserve.incomplete else None
    return FreshMetric(
        title="Доступно",
        text=fmt_usd(value),
        age_text=_age_text(verdict),
        note=note,
        verdict=verdict,
    )


def _portfolio_metric(snap: HubSnapshot, now_s: float) -> FreshMetric:
    cash = snap.balance.collateral_usdc
    positions = snap.day.positions
    open_value: float | None = None
    if positions is not None and positions.traversal_complete:
        open_value = sum(pos.size * pos.cur_price for pos in positions.positions if pos.size != 0.0)
    value = None if cash is None or open_value is None else cash + open_value
    day = snap.day
    balance = snap.balance
    verdict = _verdict(
        now_s=now_s,
        threshold_s=diagnostics.DAY_PNL_STALE_S,
        has_value=value is not None,
        value_ts=None if positions is None else positions.as_of,
        attempt_failed=day.error is not None or balance.last_error is not None,
        coverage_complete=positions is not None and positions.traversal_complete,
        pending=day.inflight or balance.queued or balance.inflight,
        error=day.error or balance.last_error,
    )
    return FreshMetric(
        title="портфолио",
        text=fmt_usd(value),
        age_text=_age_text(verdict),
        note=None,
        verdict=verdict,
    )


def _pnl_metric(snap: HubSnapshot, now_s: float) -> FreshMetric:
    day = snap.day
    today = berlin_day(now_s)
    fold_today = day.fold if day.day == today else None
    accrual = day.accrual_total or 0.0
    value = fold_today.pnl + accrual if fold_today is not None else None
    verdict = _verdict(
        now_s=now_s,
        threshold_s=diagnostics.DAY_PNL_STALE_S,
        has_value=fold_today is not None,
        value_ts=day.fold_at,
        attempt_failed=day.error is not None,
        coverage_complete=day.complete,
        pending=day.inflight,
        error=day.error,
    )
    return FreshMetric(
        title="PnL сегодня",
        text=fmt_usd(value, signed=True),
        age_text=_age_text(verdict),
        note=None if day.complete or fold_today is None else "чтение неполное",
        verdict=verdict,
    )


def _pnl_split(snap: HubSnapshot, now_s: float) -> PnlSplit | None:
    day = snap.day
    fold = day.fold if day.day == berlin_day(now_s) else None
    if fold is None:
        return None
    return PnlSplit(
        trading=fold.cash - fold.rebate,
        open_mark=fold.open_mark,
        rebate=fold.rebate + (day.accrual_total or 0.0),
    )


def _payout_since(snap: HubSnapshot) -> str:
    day = snap.day
    if day.payout_known and day.payout_ts is not None:
        return fmt_clock(day.payout_ts)
    if not day.payout_known:
        return "выплата не найдена"
    return "время выплаты неизвестно"


def _show_available(snap: HubSnapshot) -> bool:
    reserve = snap.reserve
    if reserve is None:
        return True
    return reserve.available_cash != snap.balance.collateral_usdc


def build_strip(snap: HubSnapshot, now_s: float) -> StripView:
    assessment = assess_health(snap.health, now_s)
    return StripView(
        collateral=_collateral_metric(snap, now_s),
        available=_available_metric(snap, now_s) if _show_available(snap) else None,
        portfolio=_portfolio_metric(snap, now_s),
        pnl=_pnl_metric(snap, now_s),
        pnl_split=_pnl_split(snap, now_s),
        pnl_since=_payout_since(snap),
        health=HealthLine(
            state=assessment.state,
            text=assessment.text,
            details=assessment.details,
        ),
    )
