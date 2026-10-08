"""Marketable FAK dump of inventory below the CLOB resting minimum.

The quoter only posts GTC joins, which the exchange rejects under min_order_size.
A crossing SELL has no share floor, so leftover dust can still be sold. This
module never blocks buys or quoting.
"""

# pyright: reportPrivateUsage=false

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polymaker.domain import Side

from shared.utils.log import get_logger
from shared.utils.trading import share_floor
from strategy.lifecycle import is_settling
from strategy.types import OwnershipResolved
from trader.execution_policy import SELL_REJECT_HOLD_SECONDS
from trader.session_core import LiveCore, core_now_ns
from trader.trading_mode import ExecutionMode

# Taker dump of leftover below min_order_size. 30s is the double-sell guard:
# user-WS fill is usually <2s; if it lags past cooldown, CLOB balance-reject
# freezes the token. Back off to 5min on repeated fails so this never rides
# the 2s quote tick. Five consecutive misses stop the token until the session ends.
DUST_SWEEP_COOLDOWN_S = 30.0

DUST_SWEEP_MAX_COOLDOWN_S = 300.0

DUST_SWEEP_MAX_FAILURES = 5


if TYPE_CHECKING:
    from trader.wallet_host import WalletHost

logger = get_logger(__name__)

SendSell = Callable[[str, float], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DustToken:
    token_id: str
    qty: float
    bid_size: float
    blocked: bool


@dataclass(frozen=True)
class DustTarget:
    token_id: str
    qty: float


@dataclass(frozen=True)
class FakOutcome:
    filled: bool
    balance_reject: bool
    error: str


@dataclass
class DustSweepTracker:
    """Per-token cooldown and consecutive-fail cap."""

    next_ok_s: dict[str, float] = field(default_factory=dict)
    fail_count: dict[str, int] = field(default_factory=dict)

    def due(self, token_id: str, now_s: float) -> bool:
        if self.fail_count.get(token_id, 0) >= DUST_SWEEP_MAX_FAILURES:
            return False
        return now_s >= self.next_ok_s.get(token_id, 0.0)

    def note_attempt(self, token_id: str, now_s: float, *, filled: bool) -> None:
        if filled:
            self.fail_count.pop(token_id, None)
            self.next_ok_s[token_id] = now_s + DUST_SWEEP_COOLDOWN_S
            return
        fails = self.fail_count.get(token_id, 0) + 1
        self.fail_count[token_id] = fails
        delay = min(DUST_SWEEP_COOLDOWN_S * (2 ** (fails - 1)), DUST_SWEEP_MAX_COOLDOWN_S)
        self.next_ok_s[token_id] = now_s + delay


def dust_to_sweep(
    *, tokens: tuple[DustToken, ...], min_order_size: float
) -> tuple[DustTarget, ...]:
    """Tokens whose leftover is below the resting minimum and can take the bid."""
    targets: list[DustTarget] = []
    for token in tokens:
        if token.blocked:
            continue
        qty = share_floor(token.qty)
        if qty <= 0.0 or qty >= min_order_size:
            continue
        hit = min(qty, share_floor(token.bid_size))
        if hit <= 0.0:
            continue
        targets.append(DustTarget(token_id=token.token_id, qty=hit))
    return tuple(targets)


def read_fak(resp: dict[str, Any]) -> FakOutcome:
    status = str(resp.get("status", ""))
    error = str(resp.get("error", status))[:80]
    return FakOutcome(
        filled=status == "matched",
        balance_reject="not enough balance" in str(resp.get("error", "")).lower(),
        error=error,
    )


async def sweep_dust(
    *,
    tokens: tuple[DustToken, ...],
    min_order_size: float,
    now_s: float,
    tracker: DustSweepTracker,
    send_sell: SendSell,
    freeze_sell: Callable[[str], None],
    force: bool,
) -> tuple[DustTarget, ...]:
    """Send one FAK per due dust token. Returns the attempts that went to the venue."""
    sent: list[DustTarget] = []
    for target in dust_to_sweep(tokens=tokens, min_order_size=min_order_size):
        if not force and not tracker.due(target.token_id, now_s):
            continue
        logger.info("dust fak token=%s qty=%s", target.token_id[:12], target.qty)
        try:
            outcome = read_fak(await send_sell(target.token_id, target.qty))
        except Exception as exc:
            logger.warning(
                "dust fak failed token=%s err=%s",
                target.token_id[:12],
                type(exc).__name__,
            )
            tracker.note_attempt(target.token_id, now_s, filled=False)
            continue
        tracker.note_attempt(target.token_id, now_s, filled=outcome.filled)
        if outcome.balance_reject:
            freeze_sell(target.token_id)
        if not outcome.filled:
            logger.warning("dust fak missed token=%s err=%s", target.token_id[:12], outcome.error)
        sent.append(target)
    return tuple(sent)


class DustSweeper:
    """Live FAK dump of leftover below min_order_size. Paper is a no-op."""

    def __init__(
        self,
        host: "WalletHost",
        cid: str,
        yes_token: str,
        no_token: str,
        mode: ExecutionMode,
    ) -> None:
        self._host = host
        self._cid = cid
        self._yes = yes_token
        self._no = no_token
        self._mode = mode
        self._core: LiveCore | None = None
        self._quiesced = False
        self._tracker = DustSweepTracker()
        self._task: asyncio.Task[None] | None = None

    def attach_core(self, core: LiveCore) -> None:
        self._core = core

    def mark_quiesced(self) -> None:
        self._quiesced = True

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def schedule(self) -> None:
        """Kick a FAK dump if one is not already running."""
        if self._quiesced or self._mode != "live":
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self.sweep(force=False))

    async def wait_inflight(self) -> None:
        if self._task is not None and not self._task.done():
            with suppress(Exception, asyncio.CancelledError):
                await self._task

    async def sweep(self, *, force: bool) -> None:
        """FAK leftover below min_order_size. force=True is the last dump before fence."""
        if self._mode != "live" or self._halted():
            return
        meta = self._host.engine.metas[self._cid]

        async def send_sell(token_id: str, qty: float) -> dict[str, Any]:
            resp = await self._host.engine.gateway.market_order(
                token_id, Side.SELL, qty, meta, fak=True
            )
            self._bind_taker_sell(token_id=token_id, qty=qty, resp=resp)
            return resp

        await sweep_dust(
            tokens=self._tokens(force=force),
            min_order_size=meta.min_order_size,
            now_s=time.monotonic(),
            tracker=self._tracker,
            send_sell=send_sell,
            freeze_sell=self._freeze_sell,
            force=force,
        )

    def _freeze_sell(self, token_id: str) -> None:
        self._host.store.freeze_sell(token_id, SELL_REJECT_HOLD_SECONDS)

    def _bind_taker_sell(self, *, token_id: str, qty: float, resp: dict[str, Any]) -> None:
        """Give the FAK a core record so its backfilled fill is a known SELL.

        The venue answer arrives before the position poll picks up the fill, so
        `note_fill` finds the order instead of parking it in pending_ownership.
        """
        venue_id = _venue_order_id(resp)
        core = self._core
        if core is None or venue_id is None:
            return
        token_index = core.token_index(token_id)
        if token_index is None:
            return
        core.bind_venue(core_id=venue_id, venue_id=venue_id)
        core.enqueue(
            OwnershipResolved(
                now_ns=core_now_ns(),
                order_id=venue_id,
                episode_id=core.state.episode_id,
                token_index=token_index,
                side="SELL",
                price=_bid_price(self._host, token_id),
                submitted_qty=qty,
                filled_qty=0.0,
                level_index=None,
                terminal=True,
            )
        )
        self._host.engine._wake_cid(self._cid)

    def _halted(self) -> bool:
        if self._cid in self._host.engine._halted:
            return True
        core = self._core
        return core is not None and core.state.permissions.halt

    def _tokens(self, *, force: bool) -> tuple[DustToken, ...]:
        store = self._host.store
        now_ns = core_now_ns()
        core = self._core
        tokens: list[DustToken] = []
        for token_index, token_id in ((0, self._yes), (1, self._no)):
            tokens.append(
                DustToken(
                    token_id=token_id,
                    qty=store.position(token_id).size,
                    bid_size=_bid_size(self._host, token_id),
                    blocked=self._blocked(
                        token_id=token_id,
                        token_index=token_index,
                        core=core,
                        now_ns=now_ns,
                        force=force,
                    ),
                )
            )
        return tuple(tokens)

    def _blocked(
        self,
        *,
        token_id: str,
        token_index: int,
        core: LiveCore | None,
        now_ns: int,
        force: bool,
    ) -> bool:
        if self._host.store.is_sell_frozen(token_id):
            return True
        if force or core is None:
            return False
        working = False
        for order in core.state.orders:
            if order.token_index == token_index:
                working = True
                break
        if working:
            return True
        return is_settling(
            state=core.state, policy=core.policy, now_ns=now_ns, token_index=token_index
        )


def _venue_order_id(resp: dict[str, Any]) -> str | None:
    for key in ("orderID", "orderId", "order_id", "id", "hash"):
        value = resp.get(key)
        if value:
            return str(value)
    return None


def _bid_price(host: "WalletHost", token_id: str) -> float:
    book = host.engine.md.book(token_id)
    if book is None:
        return 0.0
    bid = book.best_bid()
    if bid is None:
        return 0.0
    return bid.price


def _bid_size(host: "WalletHost", token_id: str) -> float:
    book = host.engine.md.book(token_id)
    if book is None:
        return 0.0
    bid = book.best_bid()
    if bid is None:
        return 0.0
    return bid.size
