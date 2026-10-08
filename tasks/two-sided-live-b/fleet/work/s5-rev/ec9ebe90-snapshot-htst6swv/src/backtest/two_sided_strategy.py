# Nautilus 1.226 ships these Cython APIs without static declarations.
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUntypedBaseClass=false

"""Venue adapter for two-sided maker quotes. The quote math lives in strategy.two_sided."""

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.common.component import TimeEvent
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType, LiquiditySide, OrderSide, TimeInForce
from nautilus_trader.model.events import OrderAccepted as VenueAccepted
from nautilus_trader.model.events import OrderCanceled as VenueCanceled
from nautilus_trader.model.events import OrderDenied as VenueDenied
from nautilus_trader.model.events import OrderExpired as VenueExpired
from nautilus_trader.model.events import OrderFilled as VenueFilled
from nautilus_trader.model.events import OrderRejected as VenueRejected
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy, StrategyConfig

from backtest.maker_orders import (
    CANCEL_ALERT_PREFIX,
    CANCEL_SEND_PREFIX,
    EMPTY_QUOTE_CONTEXT,
    LiveOrder,
    QuoteContext,
    SubmittedOrder,
    client_id_from_cancel_alert,
    client_id_from_cancel_send,
    level_column,
    prices_match,
    remaining_buy_notional,
    remaining_qty,
    volume_at_price,
)
from backtest.signals import calculate_book_p_radiant
from backtest.strategy import ObservedClockTape
from backtest.telemetry import (
    FillRecord,
    QuoteEvent,
    UptimeRecord,
    record_fill,
    record_quote_event,
    record_uptime,
)
from backtest.two_sided import Inventory, apply_buy, merge_pairs
from shared.constants.strategy import (
    MID_SPIKE_COOLOFF_S,
    MID_SPIKE_LOOKBACK_S,
    MID_SPIKE_THRESHOLD,
    MIN_ORDER_SIZE,
)
from shared.utils.match_time import NS_PER_SECOND
from strategy.two_sided import (
    QUOTE_FROM_SECOND,
    REPRICE_HOLD_NS,
    REPRICE_NOW_TICKS,
    TICK,
    inventory_skew,
    price_bids,
    scale_order_size,
    tick_gap,
)

PLACEMENT_JOIN = "join"


@dataclass(frozen=True)
class _SignalView:
    predicted_delta: float
    dataset_market_p: float


@dataclass(frozen=True)
class _Clock:
    game_second: int
    paused: bool
    terminal: bool


@dataclass(frozen=True)
class TwoSidedSettings:
    """CLI knobs for one two-sided run. Follow300 ignores this."""

    half_spread_ticks: int
    skew_per_share: float
    net_max_shares: float
    size_shares: float
    merge_min_shares: float
    mid_spike: bool
    debounce_ns: int


class TwoSidedConfig(StrategyConfig, frozen=True):  # type: ignore[call-arg]
    match_id: int
    instrument_ids: tuple[InstrumentId, InstrumentId]
    feed_timestamps_ns: tuple[int, ...]
    signal_timestamps_ns: tuple[int, ...]
    source_timestamps_ns: tuple[int, ...]
    predicted_deltas: tuple[float, ...]
    dataset_market_ps: tuple[float, ...]
    observed_clock: ObservedClockTape
    game_end_ns: int
    order_latency_ns: int
    cancel_latency_ns: int
    radiant_token_index: int
    half_spread_ticks: int
    skew_per_share: float
    net_max_shares: float
    size_shares: float
    merge_min_shares: float
    mid_spike: bool
    debounce_ns: int
    book_stale_s: float
    band_hi: float

    def __post_init__(self) -> None:
        tape = self.observed_clock
        if not (
            len(tape.game_seconds)
            == len(tape.paused)
            == len(tape.terminal)
            == len(self.feed_timestamps_ns)
        ):
            raise ValueError("observed_clock tapes must align with feed_timestamps_ns")


class TwoSidedMakerStrategy(Strategy):
    """Both books, one post-only BUY per token, merge as a cash credit the venue never sees."""

    def __init__(self, config: TwoSidedConfig) -> None:
        super().__init__(config)
        self._config = config
        self._books: dict[InstrumentId, OrderBook] = {}
        self._book_ts_ns: dict[InstrumentId, int] = {}
        self._instruments: dict[InstrumentId, Instrument] = {}
        self._live: dict[str, LiveOrder] = {}
        self._submitted: dict[str, SubmittedOrder] = {}
        self._token_order: dict[int, str] = {}
        self._inventory = Inventory(yes_shares=0.0, no_shares=0.0, cash=0.0)
        self._mids: list[tuple[int, float, float]] = []
        self._cooloff_until_ns = 0
        self._finished = False
        self._requote_armed_ns: int | None = None
        self._requote_generation = 0
        self._cancel_send_seq = 0
        self._last_block_second: int | None = None
        self._reprice_since: dict[int, int] = {}
        self._context = EMPTY_QUOTE_CONTEXT
        self._live_order_ns = 0
        self._live_since_ns: int | None = None

    def on_start(self) -> None:
        """Subscribe both books and arm a requote on every feed tick through game end."""
        for instrument_id in self._config.instrument_ids:
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise ValueError(f"instrument {instrument_id} is not loaded")
            self._instruments[instrument_id] = instrument
            self.subscribe_order_book_deltas(instrument_id)
        seen: set[int] = set()
        for ts_ns in self._config.feed_timestamps_ns:
            if ts_ns in seen or ts_ns > self._config.game_end_ns:
                continue
            seen.add(ts_ns)
            self.clock.set_time_alert_ns(
                name=f"SIGNAL:{self._config.match_id}:{ts_ns}",
                alert_time_ns=ts_ns,
                callback=self._on_signal_alert,
            )
        self.clock.set_time_alert_ns(
            name=f"GAME_END:{self._config.match_id}",
            alert_time_ns=self._config.game_end_ns,
            callback=self._on_game_end,
        )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        if self._finished:
            return
        instrument_id = deltas.instrument_id
        book = self._books.get(instrument_id)
        if book is None:
            book = OrderBook(instrument_id, book_type=BookType.L2_MBP)
            self._books[instrument_id] = book
        book.apply_deltas(deltas)
        self._book_ts_ns[instrument_id] = int(deltas.ts_event)
        self._arm_requote(int(deltas.ts_event))

    def on_order_accepted(self, event: VenueAccepted) -> None:
        now_ns = int(event.ts_event)
        client_order_id = str(event.client_order_id)
        live = self._live.get(client_order_id)
        if live is not None:
            live.accepted = True
        self._record_lifecycle("accepted", now_ns, "", client_order_id)
        if live is None:
            return
        if now_ns >= self._config.game_end_ns:
            self._schedule_cancel(live, reason="game_end")
            return
        if live.awaiting_cancel and live.cancel_released:
            live.awaiting_cancel = False
            self._schedule_cancel(live, reason=live.cancel_reason)

    def on_order_filled(self, event: VenueFilled) -> None:
        token_index = self._token_index_of(event.instrument_id)
        fill_qty = Decimal(str(event.last_qty))
        fill_price = float(event.last_px)
        client_order_id = str(event.client_order_id)
        submitted = self._submitted.get(client_order_id)
        if submitted is None:
            raise ValueError(f"fill on {client_order_id} has no recorded submit")
        now_ns = int(event.ts_event)
        self._credit_fill(token_index, fill_price, float(fill_qty), now_ns)
        live = self._live.get(client_order_id)
        if live is not None:
            live.filled_qty += fill_qty
            if live.filled_qty + Decimal("1e-9") >= Decimal(str(live.order.quantity)):
                self._drop_live(client_order_id)
            else:
                live.partially_filled = True
        record_fill(
            FillRecord(
                match_id=self._config.match_id,
                token_index=token_index,
                side="BUY",
                price=fill_price,
                quantity=float(fill_qty),
                submitted_quantity=submitted.quantity,
                ts_ns=now_ns,
                predicted_delta=submitted.context.predicted_delta,
                fair=submitted.context.fair,
                book_p_radiant=submitted.context.book_p_radiant,
                dataset_market_p=submitted.context.dataset_market_p,
                spread=submitted.context.spread,
                placement=PLACEMENT_JOIN,
                queue_ahead=submitted.context.queue_ahead,
                position_after=self._venue_qty(token_index),
                order_id=client_order_id,
                episode_id=0,
                level_index=0,
                submit_level_index=0,
                level_moves=0,
                fair_at_fill=submitted.context.fair,
                signal_age_seconds=self._signal_age(now_ns),
                gate_reason_at_fill="",
                episode_buy_notional=0.0,
                episode_sell_proceeds=0.0,
                episode_buy_fill_index=0,
                position_cost_basis=0.0,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
                is_maker=getattr(event, "liquidity_side", LiquiditySide.MAKER)
                == LiquiditySide.MAKER,
            )
        )
        self._arm_requote(now_ns)

    def on_order_canceled(self, event: VenueCanceled) -> None:
        client_order_id = str(event.client_order_id)
        live = self._live.get(client_order_id)
        self._record_lifecycle(
            "cancel_ack",
            int(event.ts_event),
            live.cancel_reason if live is not None else "",
            client_order_id,
        )
        self._drop_live(client_order_id)
        if not self._finished:
            self._requote(int(event.ts_event))

    def on_order_rejected(self, event: VenueRejected) -> None:
        self._drop_dead(
            str(event.client_order_id), "rejected", int(event.ts_event), str(event.reason)
        )

    def on_order_denied(self, event: VenueDenied) -> None:
        self._drop_dead(
            str(event.client_order_id), "denied", int(event.ts_event), str(event.reason)
        )

    def on_order_expired(self, event: VenueExpired) -> None:
        self._drop_dead(str(event.client_order_id), "expired", int(event.ts_event), "")

    def _on_signal_alert(self, event: TimeEvent) -> None:
        if self._finished:
            return
        self._arm_requote(int(event.ts_event))

    def _on_game_end(self, event: TimeEvent) -> None:
        now_ns = int(event.ts_event)
        self._finished = True
        self._touch_uptime(now_ns)
        record_uptime(
            UptimeRecord(
                match_id=self._config.match_id,
                live_order_seconds=int(self._live_order_ns / NS_PER_SECOND),
            )
        )
        for live in list(self._live.values()):
            self._schedule_cancel(live, reason="game_end")

    def _on_requote(self, event: TimeEvent) -> None:
        if self._finished:
            return
        marker = event.name.rsplit(":", 1)[-1]
        if marker.isdigit() and int(marker) != self._requote_generation:
            return
        self._requote_armed_ns = None
        self._requote(int(event.ts_event))

    def _arm_requote(self, now_ns: int) -> None:
        if self._finished or self._requote_armed_ns is not None:
            return
        if now_ns >= self._config.game_end_ns:
            return
        self._requote_generation += 1
        wake_ns = now_ns + self._config.debounce_ns
        self._requote_armed_ns = wake_ns
        self.clock.set_time_alert_ns(
            name=f"REQUOTE:{self._config.match_id}:{self._requote_generation}",
            alert_time_ns=wake_ns,
            callback=self._on_requote,
        )

    def _requote(self, now_ns: int) -> None:
        if self._finished or now_ns >= self._config.game_end_ns:
            return
        self._touch_uptime(now_ns)
        desired, reason = self._desired(now_ns)
        resting = {
            token_index: client_id
            for token_index, client_id in self._token_order.items()
            if client_id in self._live
        }
        for token_index, client_id in list(resting.items()):
            live = self._live[client_id]
            want = desired.get(token_index)
            if live.awaiting_cancel or not live.accepted:
                continue
            if want is None:
                self._reprice_since.pop(token_index, None)
                self._schedule_cancel(live, reason=reason or "pull")
                continue
            if self._should_reprice(token_index, live, want[0], now_ns):
                self._schedule_cancel(live, reason=reason or "reprice")
        for token_index, bid in desired.items():
            if token_index in self._token_order:
                continue
            self._submit(now_ns, token_index, bid)
        if not desired:
            self._record_block(now_ns, reason)

    def _should_reprice(
        self, token_index: int, live: LiveOrder, want_price: float, now_ns: int
    ) -> bool:
        live_price = float(live.order.price)
        if prices_match(live_price, want_price):
            self._reprice_since.pop(token_index, None)
            return False
        if tick_gap(live_price=live_price, want_price=want_price) >= REPRICE_NOW_TICKS:
            self._reprice_since.pop(token_index, None)
            return True
        since_ns = self._reprice_since.get(token_index)
        if since_ns is None:
            self._reprice_since[token_index] = now_ns
            return False
        if now_ns - since_ns < REPRICE_HOLD_NS:
            return False
        self._reprice_since.pop(token_index, None)
        return True

    def _pull_reason(self, now_ns: int) -> str:
        clock = self._clock(now_ns)
        if clock.terminal or now_ns >= self._config.game_end_ns:
            return "game_end"
        if clock.game_second < QUOTE_FROM_SECOND:
            return "cutoff"
        if clock.paused:
            return "paused"
        return ""

    def _desired(self, now_ns: int) -> tuple[dict[int, tuple[float, float]], str]:
        pulled = self._pull_reason(now_ns)
        if pulled:
            return {}, pulled
        books = self._mids_now(now_ns)
        if books is None:
            return {}, "stale_book"
        radiant_mid, dire_mid = books
        if self._spiked(now_ns, radiant_mid, dire_mid):
            return {}, "mid_spike"
        sample = self._signal(now_ns)
        if sample is None:
            return {}, "stale_signal"
        band_lo = 1.0 - self._config.band_hi
        if (
            radiant_mid > self._config.band_hi
            or dire_mid > self._config.band_hi
            or radiant_mid < band_lo
            or dire_mid < band_lo
        ):
            return {}, "band"
        book_p = calculate_book_p_radiant(radiant_mid, dire_mid)
        if book_p is None:
            return {}, "pair_tolerance"
        fair = book_p
        self._context = QuoteContext(
            predicted_delta=sample.predicted_delta,
            fair=fair,
            book_p_radiant=book_p,
            dataset_market_p=sample.dataset_market_p,
            spread=0.0,
            queue_ahead=0.0,
        )
        net = self._inventory.yes_shares - self._inventory.no_shares
        skew = inventory_skew(fair=fair, net_shares=net, skew_per_share=self._config.skew_per_share)
        prices = price_bids(fair=fair, half_spread_ticks=self._config.half_spread_ticks, skew=skew)
        desired: dict[int, tuple[float, float]] = {}
        for kernel_index, ticks in ((0, prices.yes_ticks), (1, prices.no_ticks)):
            if ticks <= 0:
                continue
            venue_index = self._venue_index(kernel_index)
            size = scale_order_size(
                size_shares=self._config.size_shares,
                net_shares=net,
                net_max_shares=self._config.net_max_shares,
                token_index=kernel_index,
            )
            if size < MIN_ORDER_SIZE:
                continue
            desired[venue_index] = (round(ticks * TICK, 2), round(size, 2))
        return desired, ""

    def _submit(self, now_ns: int, token_index: int, bid: tuple[float, float]) -> None:
        price, quantity = bid
        instrument_id = self._config.instrument_ids[token_index]
        instrument = self._instruments[instrument_id]
        book = self._books.get(instrument_id)
        queue_ahead = 0.0 if book is None else volume_at_price(book, price, is_buy=True)
        context = QuoteContext(
            predicted_delta=self._context.predicted_delta,
            fair=self._context.fair,
            book_p_radiant=self._context.book_p_radiant,
            dataset_market_p=self._context.dataset_market_p,
            spread=self._context.spread,
            queue_ahead=queue_ahead,
        )
        order = self.order_factory.limit(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.make_qty(Decimal(str(quantity))),
            price=instrument.make_price(price),
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
        client_order_id = str(order.client_order_id)
        self._live[client_order_id] = LiveOrder(order=order, level_index=0)
        self._submitted[client_order_id] = SubmittedOrder(
            quantity=quantity,
            context=context,
            token_index=token_index,
            side=OrderSide.BUY,
            price=price,
            level_index=0,
            episode_id=0,
        )
        self._token_order[token_index] = client_order_id
        self.submit_order(order)
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="submitted",
                token_index=token_index,
                side="BUY",
                price=price,
                reason="",
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=0,
                order_id=client_order_id,
                quantity=quantity,
                level_index=0,
                submit_level_index=0,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _credit_fill(self, token_index: int, price: float, quantity: float, now_ns: int) -> None:
        before = self._inventory
        self._inventory = apply_buy(
            before,
            token_index=self._kernel_index(token_index),
            price=price,
            quantity=quantity,
        )
        merged = merge_pairs(self._inventory, min_shares=self._config.merge_min_shares)
        paired = min(self._inventory.yes_shares, self._inventory.no_shares) - min(
            merged.yes_shares, merged.no_shares
        )
        self._inventory = merged
        if paired <= 0.0:
            return
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="merge",
                token_index=-1,
                side="",
                price=1.0,
                reason="",
                predicted_delta=self._context.predicted_delta,
                fair=self._context.fair,
                book_p_radiant=self._context.book_p_radiant,
                spread=0.0,
                episode_id=0,
                order_id="",
                quantity=paired,
                level_index=-1,
                submit_level_index=-1,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _signal(self, now_ns: int) -> _SignalView | None:
        index = bisect_right(self._config.signal_timestamps_ns, now_ns) - 1
        if index < 0:
            return None
        return _SignalView(
            predicted_delta=self._config.predicted_deltas[index],
            dataset_market_p=self._config.dataset_market_ps[index],
        )

    def _mids_now(self, now_ns: int) -> tuple[float, float] | None:
        mids: list[float] = []
        for instrument_id in self._config.instrument_ids:
            book = self._books.get(instrument_id)
            ts_ns = self._book_ts_ns.get(instrument_id)
            if book is None or ts_ns is None:
                return None
            if (now_ns - ts_ns) / NS_PER_SECOND > self._config.book_stale_s:
                return None
            bid = book.best_bid_price()
            ask = book.best_ask_price()
            if bid is None or ask is None:
                return None
            mids.append((float(bid) + float(ask)) / 2.0)
        radiant = mids[self._config.radiant_token_index]
        dire = mids[1 - self._config.radiant_token_index]
        return radiant, dire

    def _spiked(self, now_ns: int, radiant_mid: float, dire_mid: float) -> bool:
        if not self._config.mid_spike:
            return False
        if now_ns < self._cooloff_until_ns:
            return True
        lookback_ns = int(MID_SPIKE_LOOKBACK_S * NS_PER_SECOND)
        self._mids.append((now_ns, radiant_mid, dire_mid))
        self._mids = [row for row in self._mids if row[0] >= now_ns - lookback_ns]
        oldest = self._mids[0]
        if (
            radiant_mid <= oldest[1] - MID_SPIKE_THRESHOLD
            or dire_mid <= oldest[2] - MID_SPIKE_THRESHOLD
        ):
            self._cooloff_until_ns = now_ns + int(MID_SPIKE_COOLOFF_S * NS_PER_SECOND)
            return True
        return False

    def _clock(self, now_ns: int) -> _Clock:
        tape = self._config.observed_clock
        index = bisect_right(self._config.feed_timestamps_ns, now_ns) - 1
        if index < 0:
            return _Clock(game_second=QUOTE_FROM_SECOND - 1, paused=False, terminal=False)
        return _Clock(
            game_second=tape.game_seconds[index],
            paused=tape.paused[index],
            terminal=tape.terminal[index],
        )

    def _kernel_index(self, venue_index: int) -> int:
        return 0 if venue_index == self._config.radiant_token_index else 1

    def _venue_index(self, kernel_index: int) -> int:
        if kernel_index == 0:
            return self._config.radiant_token_index
        return 1 - self._config.radiant_token_index

    def _venue_qty(self, venue_index: int) -> float:
        if self._kernel_index(venue_index) == 0:
            return self._inventory.yes_shares
        return self._inventory.no_shares

    def _signal_age(self, now_ns: int) -> float:
        index = bisect_right(self._config.source_timestamps_ns, now_ns) - 1
        if index < 0:
            return float("nan")
        return (now_ns - self._config.source_timestamps_ns[index]) / NS_PER_SECOND

    def _token_index_of(self, instrument_id: InstrumentId) -> int:
        for token_index, configured_id in enumerate(self._config.instrument_ids):
            if configured_id == instrument_id:
                return token_index
        raise ValueError(f"instrument {instrument_id} is not part of this strategy")

    def _schedule_cancel(self, live: LiveOrder, *, reason: str) -> None:
        if live.awaiting_cancel:
            live.cancel_reason = reason
            return
        live.awaiting_cancel = True
        live.cancel_released = False
        live.cancel_reason = reason
        client_order_id = str(live.order.client_order_id)
        self._record_lifecycle("cancel_request", self.clock.timestamp_ns(), reason, client_order_id)
        self.clock.set_time_alert_ns(
            f"{CANCEL_ALERT_PREFIX}{client_order_id}",
            self.clock.timestamp_ns() + self._config.cancel_latency_ns,
            callback=self._on_cancel_release,
        )

    def _on_cancel_release(self, event: TimeEvent) -> None:
        client_order_id = client_id_from_cancel_alert(event.name)
        live = self._live.get(client_order_id)
        if live is None:
            return
        now_ns = int(event.ts_event)
        if not live.accepted:
            live.cancel_released = True
            return
        reason = live.cancel_reason
        live.cancel_released = True
        self._cancel_send_seq += 1
        self.clock.set_time_alert_ns(
            f"{CANCEL_SEND_PREFIX}{client_order_id}",
            now_ns + self._cancel_send_seq,
            callback=self._on_cancel_send,
        )
        submitted = self._submitted.get(client_order_id)
        context = submitted.context if submitted is not None else EMPTY_QUOTE_CONTEXT
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="canceled",
                token_index=self._token_index_of(live.order.instrument_id),
                side="BUY",
                price=float(live.order.price),
                reason=reason,
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=0,
                order_id=client_order_id,
                quantity=float(remaining_qty(live)),
                level_index=level_column(live.level_index),
                submit_level_index=0,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _on_cancel_send(self, event: TimeEvent) -> None:
        client_order_id = client_id_from_cancel_send(event.name)
        live = self._live.get(client_order_id)
        if live is None:
            return
        if bool(getattr(live.order, "is_closed", False)) or remaining_qty(live) <= 0:
            self._record_lifecycle(
                "cancel_ack", int(event.ts_event), live.cancel_reason, client_order_id
            )
            self._drop_live(client_order_id)
            return
        self.cancel_order(live.order)

    def _drop_dead(self, client_order_id: str, kind: str, event_ts_ns: int, reason: str) -> None:
        self._record_lifecycle(kind, event_ts_ns, reason, client_order_id)
        self._drop_live(client_order_id)

    def _drop_live(self, client_order_id: str) -> None:
        self._live.pop(client_order_id, None)
        for token_index, order_id in list(self._token_order.items()):
            if order_id == client_order_id:
                del self._token_order[token_index]
                self._reprice_since.pop(token_index, None)

    def _record_lifecycle(
        self, kind: str, event_ts_ns: int, reason: str, client_order_id: str
    ) -> None:
        live = self._live.get(client_order_id)
        submitted = self._submitted.get(client_order_id)
        context = submitted.context if submitted is not None else EMPTY_QUOTE_CONTEXT
        token_index = -1
        price = 0.0
        quantity = 0.0
        if live is not None:
            token_index = self._token_index_of(live.order.instrument_id)
            price = float(live.order.price)
            quantity = float(remaining_qty(live))
        elif submitted is not None:
            token_index = submitted.token_index
            price = submitted.price
            quantity = submitted.quantity
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=event_ts_ns,
                kind=kind,
                token_index=token_index,
                side="BUY",
                price=price,
                reason=reason,
                predicted_delta=context.predicted_delta,
                fair=context.fair,
                book_p_radiant=context.book_p_radiant,
                spread=context.spread,
                episode_id=0,
                order_id=client_order_id,
                quantity=quantity,
                level_index=0,
                submit_level_index=0,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _record_block(self, now_ns: int, reason: str) -> None:
        if not reason:
            return
        second = now_ns // NS_PER_SECOND
        if second == self._last_block_second:
            return
        self._last_block_second = second
        record_quote_event(
            QuoteEvent(
                match_id=self._config.match_id,
                ts_ns=now_ns,
                kind="no_quote",
                token_index=-1,
                side="",
                price=0.0,
                reason=reason,
                predicted_delta=self._context.predicted_delta,
                fair=self._context.fair,
                book_p_radiant=self._context.book_p_radiant,
                spread=0.0,
                episode_id=0,
                order_id="",
                quantity=0.0,
                level_index=-1,
                submit_level_index=-1,
                reserved_buy_notional=float(remaining_buy_notional(self._live)),
            )
        )

    def _touch_uptime(self, now_ns: int) -> None:
        if self._live_since_ns is not None:
            self._live_order_ns += now_ns - self._live_since_ns
            self._live_since_ns = None
        if self._live:
            self._live_since_ns = now_ns
