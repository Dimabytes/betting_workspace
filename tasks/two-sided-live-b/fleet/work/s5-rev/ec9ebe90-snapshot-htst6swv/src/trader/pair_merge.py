# pyright: reportPrivateUsage=false, reportMissingTypeStubs=false

import asyncio
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

from shared.utils.log import get_logger
from trader.ctf_merge import SHARE_BASE_UNITS, MergeOutcome, merge_pairs_via_adapter
from trader.notify import notify_in_background
from trader.session_core import LiveCore
from trader.trading_mode import ExecutionMode
from trader.wallet_store import WalletStateStore

if TYPE_CHECKING:
    from trader.wallet_host import WalletHost

logger = get_logger(__name__)

MERGE_AT_HELD_USDC = 130.0
MERGE_MIN_PAIRS = 5.0
FINAL_MERGE_MIN_RAW = 10_000
MERGE_PAUSES_S = (30.0, 60.0, 120.0, 300.0)
MERGE_CALL_TIMEOUT_S = 190.0


def pick_merge_pause_s(failures: int) -> float:
    return MERGE_PAUSES_S[min(failures, len(MERGE_PAUSES_S)) - 1]


@dataclass(frozen=True)
class _PendingMerge:
    tx_hash: str
    qty: float


class MergeAccountingError(Exception):
    def __init__(self, *, tx_hash: str, qty: float) -> None:
        self.tx_hash = tx_hash
        self.qty = qty
        super().__init__(tx_hash)


class PairMerger:
    def __init__(
        self,
        *,
        host: "WalletHost",
        cid: str,
        match_id: str,
        yes_token: str,
        no_token: str,
        mode: ExecutionMode,
    ) -> None:
        self._host = host
        self._cid = cid
        self._match_id = match_id
        self._yes = yes_token
        self._no = no_token
        self._mode = mode
        self._core: LiveCore | None = None
        self._quiesced = False
        self._disabled = False
        self._cancelled = False
        self._failures = 0
        self._paused_until_s = 0.0
        self._pending: _PendingMerge | None = None
        self._task: asyncio.Task[None] | None = None

    def attach_core(self, core: LiveCore) -> None:
        self._core = core

    def mark_quiesced(self) -> None:
        self._quiesced = True

    def cancel(self) -> None:
        self._cancelled = True

    def schedule(self) -> None:
        self._start(final=False)

    async def wait_inflight(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        await asyncio.shield(task)

    async def merge_all(self) -> None:
        await self.wait_inflight()
        if await self._merge_final_once():
            await self._merge_final_once()

    async def _merge_final_once(self) -> bool:
        had_pending = self._pending is not None
        self._start(final=True)
        await self.wait_inflight()
        pending = self._pending
        if pending is not None:
            raise MergeAccountingError(tx_hash=pending.tx_hash, qty=pending.qty)
        return had_pending

    def _start(self, *, final: bool) -> None:
        if self._task is not None and not self._task.done():
            return
        if self._pending is not None:
            self._task = asyncio.get_running_loop().create_task(self._finish_pending())
            return
        if not self._eligible(final=final):
            return
        self._task = asyncio.get_running_loop().create_task(self._merge_pairs(final=final))

    def _blocked(self) -> bool:
        return (
            self._disabled or self._cancelled or self._mode != "live" or self._pending is not None
        )

    def _threshold_ready(self) -> bool:
        if self._quiesced or time.monotonic() < self._paused_until_s:
            return False
        if not self._core_ready() or not self._held_enough():
            return False
        return not self._host.store.has_unacked_matched({self._yes, self._no})

    def _positions_settled(self) -> bool:
        if not self._core_ready():
            return False
        return not self._host.store.has_unacked_matched({self._yes, self._no})

    def _core_ready(self) -> bool:
        core = self._core
        return core is not None and not core.state.recovery_pending

    def _eligible(self, *, final: bool) -> bool:
        if self._blocked():
            return False
        if final:
            return self._positions_settled()
        return self._threshold_ready()

    def _held_enough(self) -> bool:
        store = self._host.store
        yes = store.position(self._yes)
        no = store.position(self._no)
        held_usdc = yes.size * yes.avg_price + no.size * no.avg_price
        return held_usdc >= MERGE_AT_HELD_USDC and min(yes.size, no.size) >= MERGE_MIN_PAIRS

    async def _merge_pairs(self, *, final: bool) -> None:
        refresh = False
        async with self._host.engine._chain_lock:
            if not self._eligible(final=final):
                return
            balances = await self._host.read_fresh_balances([self._yes, self._no])
            if balances is None or not self._eligible(final=final):
                return
            store = self._host.store
            amount_raw = _amount_raw(balances, self._yes, self._no, store)
            min_raw = FINAL_MERGE_MIN_RAW if final else 1
            if amount_raw < min_raw:
                return
            token_ids = (self._yes, self._no)
            store.hold_merge(token_ids=token_ids)
            keep_hold = False
            try:
                outcome = await self._call_adapter(amount_raw)
                keep_hold = self._apply_outcome(outcome, amount_raw)
                refresh = outcome.status == "merged" and not keep_hold
            except Exception as exc:
                self._disable_merges(
                    MergeOutcome(status="unknown", tx_hash="", reason=type(exc).__name__)
                )
            finally:
                if not keep_hold:
                    store.release_merge(token_ids=token_ids)
        if refresh:
            await self._refresh_collateral()

    async def _finish_pending(self) -> None:
        booked = False
        async with self._host.engine._chain_lock:
            booked = self._book_pending()
            if booked:
                self._host.store.release_merge(token_ids=(self._yes, self._no))
        if booked:
            await self._refresh_collateral()

    async def _call_adapter(self, amount_raw: int) -> MergeOutcome:
        deadline_s = time.monotonic() + MERGE_CALL_TIMEOUT_S
        return await asyncio.to_thread(
            merge_pairs_via_adapter,
            cfg=self._host.engine.cfg,
            condition_id=self._cid,
            amount_raw=amount_raw,
            deadline_s=deadline_s,
        )

    def _apply_outcome(self, outcome: MergeOutcome, amount_raw: int) -> bool:
        if outcome.status == "merged":
            self._pending = _PendingMerge(
                tx_hash=outcome.tx_hash, qty=amount_raw / SHARE_BASE_UNITS
            )
            return not self._book_pending()
        if outcome.status == "failed":
            self._pause_merges(outcome)
            return False
        self._disable_merges(outcome)
        return False

    def _book_pending(self) -> bool:
        pending = self._pending
        if pending is None:
            return True
        try:
            applied = self._host.store.apply_merge(
                tx_hash=pending.tx_hash, token_ids=(self._yes, self._no), qty=pending.qty
            )
        except Exception as exc:
            logger.error(
                "merge accounting match=%s tx=%s err=%s",
                self._match_id,
                pending.tx_hash,
                type(exc).__name__,
            )
            notify_in_background(
                f"trader merge accounting: match {self._match_id} tx {pending.tx_hash}: "
                f"{type(exc).__name__}"
            )
            return False
        try:
            self._host._consume_core_outbox(self._yes)
        except Exception as exc:
            logger.warning(
                "merge core outbox failed match=%s err=%s", self._match_id, type(exc).__name__
            )
        self._pending = None
        self._failures = 0
        self._host.engine._wake_cid(self._cid)
        logger.info(
            "merge match=%s pairs=%.6f tx=%s applied=%s",
            self._match_id,
            pending.qty,
            pending.tx_hash,
            applied,
        )
        if applied:
            notify_in_background(
                f"trader merge: match {self._match_id} pairs {pending.qty:.2f} tx {pending.tx_hash}"
            )
        return True

    def _pause_merges(self, outcome: MergeOutcome) -> None:
        self._failures += 1
        pause_s = pick_merge_pause_s(self._failures)
        self._paused_until_s = time.monotonic() + pause_s
        tx = outcome.tx_hash or "-"
        logger.warning(
            "merge failed match=%s pause=%.0fs tx=%s reason=%s",
            self._match_id,
            pause_s,
            tx,
            outcome.reason,
        )
        notify_in_background(
            f"trader merge failed: match {self._match_id} pause {pause_s:.0f}s tx {tx}: "
            f"{outcome.reason[:200]}"
        )

    def _disable_merges(self, outcome: MergeOutcome) -> None:
        self._disabled = True
        tx = outcome.tx_hash or "-"
        logger.error("merge unknown match=%s tx=%s reason=%s", self._match_id, tx, outcome.reason)
        notify_in_background(
            f"trader merge unknown: match {self._match_id} merges off for this map tx {tx}: "
            f"{outcome.reason[:200]}"
        )

    async def _refresh_collateral(self) -> None:
        gateway = self._host.engine.gateway
        params = BalanceAllowanceParams(asset_type=cast(AssetType, AssetType.COLLATERAL))
        try:
            await gateway._io(gateway._client.update_balance_allowance, params)
        except Exception as exc:
            logger.warning(
                "merge allowance refresh failed match=%s err=%s", self._match_id, type(exc).__name__
            )
        balance = await gateway.collateral_balance()
        if balance > 0.0:
            self._host._budget_cache.value = balance


def _amount_raw(balances: dict[str, float], yes: str, no: str, store: WalletStateStore) -> int:
    pairs = min(
        store.position(yes).size,
        store.position(no).size,
        balances[yes],
        balances[no],
    )
    return math.floor(pairs * SHARE_BASE_UNITS)
