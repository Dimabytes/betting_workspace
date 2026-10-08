import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from polymaker.config import WalletConfig

from dashboard.wallet import FillEvent, OutboxPage
from shared.utils.environment import env_value

MIN_REQUEST_INTERVAL_S = 5.0
PERIODIC_REFRESH_S = 60.0
BACKOFF_SCHEDULE_S = (5.0, 10.0, 20.0, 40.0, 60.0)
BASE_UNITS_PER_USDC = 1_000_000
PENDING_LIMIT = 64


@dataclass(frozen=True)
class BalanceAccount:
    host: str
    chain_id: int
    signature_type: int
    pk: str
    funder: str


@dataclass(frozen=True)
class FillEvidence:
    seq: int
    fill_key: str
    event: str
    expected_cash: float | None


@dataclass(frozen=True)
class BalanceRead:
    ok: bool
    collateral_usdc: float | None
    error: str | None
    status_code: int | None
    retry_after_s: float | None


@dataclass(frozen=True)
class BalanceSnapshot:
    collateral_usdc: float | None
    funder: str | None
    funder_mismatch: bool
    request_started_at: float | None
    response_at: float | None
    last_attempt_at: float | None
    last_error: str | None
    queued: bool
    inflight: bool
    request_cutoff_seq: int | None
    covered_seq: int | None
    significant_seq: int | None
    evidence_gaps: int
    pending: tuple[FillEvidence, ...]
    pending_dropped: int


def _expected_cash(event: FillEvent) -> float | None:
    if event.price is None or event.size is None:
        return None
    if event.side == "BUY":
        return -event.price * event.size
    if event.side == "SELL":
        return event.price * event.size
    return None


class BalanceTracker:
    def __init__(
        self,
        *,
        min_interval_s: float = MIN_REQUEST_INTERVAL_S,
        period_s: float = PERIODIC_REFRESH_S,
        backoffs_s: tuple[float, ...] = BACKOFF_SCHEDULE_S,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._period_s = period_s
        self._backoffs_s = backoffs_s
        self._cursor: int | None = None
        self._awaiting: dict[str, float | None] = {}
        self._pending: dict[int, FillEvidence] = {}
        self._pending_dropped = 0
        self._significant: int | None = None
        self._covered: int | None = None
        self._cutoff: int | None = None
        self._collateral: float | None = None
        self._request_started_at: float | None = None
        self._response_at: float | None = None
        self._last_attempt_at: float | None = None
        self._last_error: str | None = None
        self._last_start_mono: float | None = None
        self._retry_not_before = 0.0
        self._failures = 0
        self._inflight = False
        self._evidence_gaps = 0

    @property
    def cursor(self) -> int | None:
        return self._cursor

    @property
    def collateral(self) -> float | None:
        return self._collateral

    @property
    def awaiting(self) -> frozenset[str]:
        return frozenset(self._awaiting)

    @property
    def inflight(self) -> bool:
        return self._inflight

    def seeded(self) -> bool:
        return self._cursor is not None

    def seed(self, matched_keys: frozenset[str], cursor_seq: int) -> None:
        if self._cursor is not None:
            return
        self._cursor = cursor_seq
        for key in matched_keys:
            self._awaiting[key] = None

    def apply_page(self, page: OutboxPage) -> bool:
        if self._cursor is None:
            return False
        significant_before = self._significant
        cursor = self._cursor
        for event in page.events:
            if event.seq <= cursor:
                continue
            self._apply(event)
            cursor = event.seq
        if page.next_seq is not None and page.next_seq > self._cursor:
            self._cursor = page.next_seq
        return self._significant != significant_before

    def _record(self, evidence: FillEvidence) -> None:
        if evidence.seq not in self._pending:
            while len(self._pending) >= PENDING_LIMIT:
                self._pending.pop(next(iter(self._pending)))
                self._pending_dropped += 1
        self._pending[evidence.seq] = evidence

    def _apply(self, event: FillEvent) -> None:
        if event.event == "matched":
            expected = _expected_cash(event)
            if expected is None:
                self._evidence_gaps += 1
            self._awaiting[event.fill_key] = expected
            self._record(FillEvidence(event.seq, event.fill_key, event.event, expected))
            self._significant = event.seq
        elif event.event == "confirmed":
            if event.fill_key in self._awaiting:
                del self._awaiting[event.fill_key]
            else:
                self._record(FillEvidence(event.seq, event.fill_key, event.event, event.cash_delta))
                self._significant = event.seq
        elif event.event == "failed":
            prior = self._awaiting.pop(event.fill_key, None)
            rollback = None if prior is None else -prior
            self._record(FillEvidence(event.seq, event.fill_key, event.event, rollback))
            self._significant = event.seq
        else:
            self._evidence_gaps += 1
            self._record(FillEvidence(event.seq, event.fill_key, event.event, None))
            self._significant = event.seq

    def _dirty(self) -> bool:
        return self._significant is not None and (
            self._covered is None or self._significant > self._covered
        )

    def wants_request(self, now_mono: float) -> bool:
        if self._cursor is None:
            return False
        if self._covered is None or self._dirty():
            return True
        return self._last_start_mono is None or now_mono - self._last_start_mono >= self._period_s

    def next_delay(self, now_mono: float) -> float | None:
        if self._cursor is None or self._inflight:
            return None
        not_before = self._retry_not_before
        if self._last_start_mono is not None:
            not_before = max(not_before, self._last_start_mono + self._min_interval_s)
        if self._covered is None or self._dirty() or self._last_start_mono is None:
            due = not_before
        else:
            due = max(not_before, self._last_start_mono + self._period_s)
        return max(0.0, due - now_mono)

    def begin_request(self, *, now_mono: float, now_wall: float) -> None:
        self._inflight = True
        self._last_start_mono = now_mono
        self._request_started_at = now_wall
        self._last_attempt_at = now_wall
        self._cutoff = self._cursor

    def _backoff(self, now_mono: float) -> None:
        self._failures += 1
        backoff = self._backoffs_s[min(self._failures - 1, len(self._backoffs_s) - 1)]
        self._retry_not_before = now_mono + backoff

    def finish_request(self, result: BalanceRead, *, now_mono: float, now_wall: float) -> None:
        self._inflight = False
        self._last_attempt_at = now_wall
        if result.ok:
            self._collateral = result.collateral_usdc
            self._response_at = now_wall
            self._last_error = None
            self._failures = 0
            self._retry_not_before = 0.0
            self._covered = self._cutoff
            self._pending = {
                seq: ev
                for seq, ev in self._pending.items()
                if self._covered is None or seq > self._covered
            }
        else:
            self._last_error = result.error or "balance read failed"
            self._backoff(now_mono)
            if result.retry_after_s is not None:
                self._retry_not_before = now_mono + result.retry_after_s

    def abort_request(self, error: str, *, now_mono: float, now_wall: float) -> None:
        self._inflight = False
        self._last_attempt_at = now_wall
        self._last_error = error
        self._backoff(now_mono)

    def snapshot(
        self,
        *,
        now_mono: float,
        funder: str | None,
        funder_mismatch: bool,
        source_error: str | None,
    ) -> BalanceSnapshot:
        return BalanceSnapshot(
            collateral_usdc=None if funder_mismatch else self._collateral,
            funder=funder,
            funder_mismatch=funder_mismatch,
            request_started_at=self._request_started_at,
            response_at=self._response_at,
            last_attempt_at=self._last_attempt_at,
            last_error=self._last_error or source_error,
            queued=not self._inflight and self.wants_request(now_mono),
            inflight=self._inflight,
            request_cutoff_seq=self._cutoff,
            covered_seq=self._covered,
            significant_seq=self._significant,
            evidence_gaps=self._evidence_gaps,
            pending=tuple(self._pending.values()),
            pending_dropped=self._pending_dropped,
        )


def parse_collateral_usdc(response: object) -> float:
    if not isinstance(response, Mapping):
        raise ValueError("collateral response is not a mapping")
    raw = cast(Mapping[object, object], response).get("balance")
    if isinstance(raw, bool) or raw is None:
        raise ValueError("collateral balance missing")
    if isinstance(raw, int):
        base = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        base = int(raw.strip())
    else:
        raise ValueError("collateral balance malformed")
    if base < 0:
        raise ValueError("collateral balance negative")
    return base / BASE_UNITS_PER_USDC


def load_balance_account(
    trading_toml: Path, env: Callable[[str], str | None] = env_value
) -> BalanceAccount | None:
    try:
        with trading_toml.open("rb") as handle:
            document: dict[str, object] = tomllib.load(handle)
    except OSError:
        document = {}
    section_raw = document.get("wallet")
    wallet_cfg = (
        WalletConfig(**cast("dict[str, Any]", section_raw))
        if isinstance(section_raw, dict)
        else WalletConfig()
    )
    pk = env("PK") or ""
    funder = env("BROWSER_ADDRESS") or ""
    if not pk or not funder:
        return None
    return BalanceAccount(
        host=wallet_cfg.clob_host,
        chain_id=wallet_cfg.chain_id,
        signature_type=wallet_cfg.signature_type,
        pk=pk,
        funder=funder,
    )
