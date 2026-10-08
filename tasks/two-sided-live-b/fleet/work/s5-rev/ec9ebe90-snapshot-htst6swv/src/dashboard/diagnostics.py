import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from shared.utils.match_time import parse_utc
from viewer.archive_read import as_float, as_str

BALANCE_STALE_S = 120.0
DAY_PNL_STALE_S = 600.0
SELL_WEDGE_SECONDS = 30.0

FreshnessState = Literal["no_data", "fresh", "updating", "stale"]

_STATE_LABELS: dict[FreshnessState, str] = {
    "no_data": "Нет данных",
    "fresh": "Свежий",
    "updating": "Обновляется",
    "stale": "Устарел",
}


@dataclass(frozen=True)
class FreshnessInput:
    now_s: float
    threshold_s: float
    has_value: bool
    value_age_s: float | None
    attempt_failed: bool
    coverage_complete: bool
    pending: bool
    book_connected: bool | None
    awaiting_book: bool
    error: str | None


@dataclass(frozen=True)
class FreshnessVerdict:
    state: FreshnessState
    label: str
    age_s: float | None
    detail: str | None


def freshness(inputs: FreshnessInput) -> FreshnessVerdict:
    if not inputs.has_value:
        return FreshnessVerdict(
            state="no_data",
            label=_STATE_LABELS["no_data"],
            age_s=None,
            detail=inputs.error,
        )
    stale_reason: str | None = None
    if inputs.attempt_failed:
        stale_reason = inputs.error or "last attempt failed"
    elif not inputs.coverage_complete:
        stale_reason = "coverage incomplete"
    elif inputs.value_age_s is not None and inputs.value_age_s > inputs.threshold_s:
        stale_reason = f"age {inputs.value_age_s:.0f}s > {inputs.threshold_s:.0f}s"
    elif inputs.book_connected is False:
        stale_reason = "book disconnected"
    elif inputs.awaiting_book:
        stale_reason = "awaiting post-reconnect book"
    if stale_reason is not None:
        return FreshnessVerdict(
            state="stale",
            label=_STATE_LABELS["stale"],
            age_s=inputs.value_age_s,
            detail=stale_reason,
        )
    if inputs.pending:
        return FreshnessVerdict(
            state="updating",
            label=_STATE_LABELS["updating"],
            age_s=inputs.value_age_s,
            detail=None,
        )
    return FreshnessVerdict(
        state="fresh",
        label=_STATE_LABELS["fresh"],
        age_s=inputs.value_age_s,
        detail=None,
    )


_NONTERMINAL_SELL_STATUSES = frozenset({"pending", "canceling", "unknown"})


@dataclass(frozen=True)
class SellObservation:
    session_key: str
    token_id: str
    order_id: str
    status: str
    price: float
    remaining_qty: float
    venue_id: str | None
    held_qty: float


@dataclass(frozen=True)
class SellWatchState:
    key: tuple[str, str, str, str] | None
    first_seen_s: float | None


@dataclass(frozen=True)
class SellWedgeVerdict:
    wedged: bool
    continuous_s: float
    order_id: str | None
    status: str | None


def observe_sell(
    state: SellWatchState, obs: SellObservation | None, now_s: float
) -> tuple[SellWatchState, SellWedgeVerdict]:
    candidate: tuple[str, str, str, str] | None = None
    if (
        obs is not None
        and obs.status in _NONTERMINAL_SELL_STATUSES
        and obs.held_qty > 0.0
        and obs.remaining_qty > 0.0
    ):
        candidate = (obs.session_key, obs.token_id, obs.order_id, obs.status)
    if candidate is None:
        return SellWatchState(None, None), SellWedgeVerdict(False, 0.0, None, None)
    first_seen = state.first_seen_s if state.key == candidate else now_s
    if first_seen is None:
        first_seen = now_s
    age = max(0.0, now_s - first_seen)
    verdict = SellWedgeVerdict(
        wedged=age > SELL_WEDGE_SECONDS,
        continuous_s=age,
        order_id=obs.order_id if obs is not None else None,
        status=obs.status if obs is not None else None,
    )
    return SellWatchState(candidate, first_seen), verdict


ExitKind = Literal[
    "no_position",
    "dust",
    "sell_live",
    "sell_pending",
    "wedge",
    "blocked",
    "unknown",
]


@dataclass(frozen=True)
class ExitAssessment:
    kind: ExitKind
    label: str
    detail: str | None


def explain_exit(
    *,
    held_qty: float,
    min_order_size: float | None,
    sells: Sequence[SellObservation],
    sell_only: bool | None,
    recovery_pending: bool | None,
    unconfirmed: int,
    pending_ownership: int,
    wedge: SellWedgeVerdict | None,
) -> ExitAssessment:
    if held_qty <= 0.0:
        return ExitAssessment("no_position", "позиция не открыта", None)
    live_sells = [sell for sell in sells if sell.status == "live"]
    if live_sells:
        sell = live_sells[0]
        return ExitAssessment(
            "sell_live",
            "SELL в стакане",
            f"order {sell.order_id} @ {sell.price:g} осталось {sell.remaining_qty:g}",
        )
    pending = [sell for sell in sells if sell.status in _NONTERMINAL_SELL_STATUSES]
    if pending:
        if wedge is not None and wedge.wedged:
            return ExitAssessment(
                "wedge",
                "возможное зависание выхода",
                f"SELL {wedge.status} уже {wedge.continuous_s:.0f}с подряд",
            )
        sell = pending[0]
        return ExitAssessment(
            "sell_pending",
            f"SELL {sell.status}",
            f"order {sell.order_id} @ {sell.price:g}",
        )
    if min_order_size is not None and held_qty < min_order_size:
        return ExitAssessment(
            "dust",
            "остаток ниже минимального ордера",
            f"{held_qty:g} < min_order_size {min_order_size:g}",
        )
    if sell_only or recovery_pending:
        return ExitAssessment(
            "blocked",
            "режим только выхода / восстановление",
            f"sell_only={sell_only} recovery_pending={recovery_pending}",
        )
    if unconfirmed > 0 or pending_ownership > 0:
        return ExitAssessment(
            "blocked",
            "неподтверждённые сделки",
            f"unconfirmed={unconfirmed} pending_ownership={pending_ownership}",
        )
    if min_order_size is None:
        return ExitAssessment(
            "unknown",
            "продажи в стакане нет",
            "min_order_size не сохранён",
        )
    return ExitAssessment("unknown", "продажи в стакане нет", None)


_REASON_LABELS: dict[str, str] = {
    "model": "решение модели",
    "pre_horn": "матч ещё не начался",
    "paused": "игра на паузе",
    "finished": "карта завершена",
    "stale": "фид устарел",
    "missing_book": "нет книги YES/NO",
    "own_liquidity_only": "в стакане только наши ордера",
    "missing_prior": "нет приора карты",
    "resume_locked": "вход заблокирован после рестарта",
    "one_sided_book": "односторонняя книга",
    "crossed_book": "пересечённая книга",
    "nonfinite_pair": "невалидные цены книги",
    "out_of_range_pair": "цены книги вне диапазона",
    "pair_out_of_tolerance": "пара вне допуска суммы",
    "model_error": "ошибка модели",
    "sidecar_fault": "сайдкар рынка недоступен или изменился",
    "trading_error": "ошибка трейдера",
    "outside_window": "вне окна модели",
    "history_gap": "разрыв истории",
}


def reason_label(reason: str | None) -> str:
    if reason is None or reason == "":
        return "нет данных"
    return _REASON_LABELS.get(reason, reason)


_ENTRY_BLOCKS: dict[str, str] = {
    "none": "вход разрешён",
    "cutoff": "после cutoff входа",
    "nw_velocity": "скачок net-worth выше лимита",
    "missing_nw": "нет данных net-worth",
    "off_grid": "шаг цены грубее сетки",
    "position_open": "позиция уже открыта",
    "no_edge": "нет преимущества у join-цены",
    "no_cash": "нет свободного кэша",
    "position_cap": "лимит позиции карты",
    "account_cap": "лимит счёта",
    "paused": "игра на паузе",
    "halt": "halt",
    "mid_spike": "всплеск мид-прайса",
    "kill": "kill-гейт по убийствам",
    "recovery": "восстановление сессии",
    "min_price": "цена ниже диапазона входа",
    "max_price": "цена выше диапазона входа",
    "wide_spread": "слишком широкий спред",
    "winding_down": "карта сворачивается",
    "ownership_unresolved": "владение ордером не подтверждено",
}


def _fmt_delta(value: float) -> str:
    return f"{value:.4g}"


def entry_block_label(
    block: str | None,
    *,
    model_evaluated: bool | None,
    raw_delta: float | None,
    min_abs_delta: float | None,
) -> str:
    if block is None or block == "":
        return "нет данных"
    if block != "min_delta":
        return _ENTRY_BLOCKS.get(block, block)
    if (
        model_evaluated is True
        and raw_delta is not None
        and math.isfinite(raw_delta)
        and min_abs_delta is not None
        and math.isfinite(min_abs_delta)
    ):
        delta_abs = abs(raw_delta)
        if delta_abs < min_abs_delta:
            return f"|Δ| {_fmt_delta(delta_abs)} < порога входа {_fmt_delta(min_abs_delta)}"
        return f"min_delta: |Δ| {_fmt_delta(delta_abs)}, порог входа {_fmt_delta(min_abs_delta)}"
    return "min_delta"


def stamp_age_s(now_s: float, stamp: object) -> float | None:
    text = as_str(stamp)
    if text is None:
        return None
    try:
        parsed = parse_utc(text)
    except ValueError:
        return None
    return max(0.0, now_s - parsed.timestamp())


def signal_freshness(
    signal: Mapping[str, object] | None, *, now_s: float
) -> tuple[float | None, float | None]:
    if signal is None:
        return None, None
    decision_age = stamp_age_s(now_s, signal.get("recorded_at_utc"))
    feed_age = stamp_age_s(now_s, signal.get("feed_received_at_utc"))
    return decision_age, feed_age


def signal_second(signal: Mapping[str, object] | None) -> float | None:
    if signal is None:
        return None
    return as_float(signal.get("second"))
