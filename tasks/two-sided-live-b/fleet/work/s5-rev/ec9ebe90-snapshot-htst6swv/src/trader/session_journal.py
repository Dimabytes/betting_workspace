"""The concise per-session JSONL journal and its type-only fault reporter."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from polymaker.domain import Fill, OpenOrder, Side

from shared.utils.log import get_logger
from shared.utils.match_time import parse_utc
from shared.utils.trading import maker_rebate_usdc
from trader.archive_paths import FsyncedJsonlWriter
from trader.archive_types import (
    SessionEndRecord,
    SessionFillRecord,
    SessionHistoryGapRecord,
    SessionLateFillRecord,
    SessionQuoteRecord,
    SessionRecord,
    SessionSignalRecord,
    SessionSignalSnapshot,
    SessionStartRecord,
    SessionTickSizeChangeRecord,
    SessionTradingErrorRecord,
)
from trader.bindings import MatchStart
from trader.clip_rules import ClipChoice
from trader.collector_sidecars import FreshSidecar
from trader.notify import notify_in_background
from trader.paths import SESSION_JOURNAL_FILENAME
from trader.session_binding import (
    parse_sidecar_binding,
    sidecar_binding,
    sidecar_binding_record,
)
from trader.session_types import (
    SessionEndSnapshot,
    SidecarBinding,
    SignalDecision,
    TradingDisabled,
)
from trader.strict_json import (
    StrictJsonError,
    require_bool,
    require_exact_keys,
    require_int,
    require_nonempty_str,
    require_number,
    require_object,
    require_str,
)
from trader.trading_mode import ExecutionMode

logger = get_logger(__name__)

GIT_COMMIT_UNKNOWN = "unknown"
_GIT_SYMREF_PREFIX = "ref: "
TERMINAL_REASON_FINISHED = "finished"
SESSION_SCHEMA_VERSION = 7
ACCEPTED_SESSION_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5, 6, 7})
_START_WITH_EXECUTION_MODE = frozenset({3, 4, 5, 6, 7})
_START_WITH_CLIP = frozenset({7})
_PROVENANCE_LABEL = "session journal provenance"
_FILL_LABEL = "session journal fill"

_SESSION_START_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "match_id",
        "condition_id",
        "git_commit",
        "model",
        "sidecar_binding",
    }
)
_SESSION_START_KEYS_V3 = _SESSION_START_KEYS | {"execution_mode"}
_SESSION_START_KEYS_V7 = _SESSION_START_KEYS_V3 | {"clip_usdc", "clip_reason"}

SessionVenue = Literal["polymarket", "kalshi"]


def record_venue(record: dict[str, object]) -> SessionVenue:
    """Missing/null venue is polymarket (schema 1-5). Unknown venue is corrupt."""
    raw = record.get("venue")
    if raw is None or raw == "polymarket":
        return "polymarket"
    if raw == "kalshi":
        return "kalshi"
    raise StrictJsonError(f"{_FILL_LABEL} venue is not polymarket or kalshi")


def _start_keys(schema_version: int) -> frozenset[str]:
    if schema_version in _START_WITH_CLIP:
        return _SESSION_START_KEYS_V7
    if schema_version in _START_WITH_EXECUTION_MODE:
        return _SESSION_START_KEYS_V3
    return _SESSION_START_KEYS


def _pinned_clip(record: dict[str, object], schema_version: int) -> ClipChoice | None:
    """Schema 7 pins the clip. Older journals leave it unset."""
    if schema_version not in _START_WITH_CLIP:
        return None
    return ClipChoice(
        require_number(record, "clip_usdc", _PROVENANCE_LABEL),
        require_nonempty_str(record, "clip_reason", _PROVENANCE_LABEL),
    )


@dataclass(frozen=True)
class JournalStart:
    """Pinned session_start fields used on resume: mode, model, sidecar binding, clip.

    clip is None on journals written before the clip pin (schema 1-6).
    """

    binding: SidecarBinding | None
    schema_version: int
    execution_mode: ExecutionMode
    model_name: str
    model_trained_at: str
    clip: ClipChoice | None


@dataclass(frozen=True, slots=True)
class JournalRound:
    """Open-round sizes and last BUY time reconstructed from journal fills."""

    yes_size: float
    no_size: float
    last_buy_unix: float | None


def read_git_head_commit(git_dir: Path) -> str:
    """Return the HEAD commit from the files in `git_dir`, or the fixed "unknown".

    Reads files, not `git rev-parse`: the image has no git binary, and compose
    mounts `.git` read-only. Covers a detached HEAD and a branch ref that
    `git gc` moved into packed-refs.
    """
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith(_GIT_SYMREF_PREFIX):
            return head or GIT_COMMIT_UNKNOWN
        ref_name = head.removeprefix(_GIT_SYMREF_PREFIX)
        loose_ref = git_dir / ref_name
        if loose_ref.is_file():
            commit = loose_ref.read_text(encoding="utf-8").strip()
        else:
            commit = _find_packed_ref_commit(git_dir / "packed-refs", ref_name)
    except OSError:
        return GIT_COMMIT_UNKNOWN
    return commit or GIT_COMMIT_UNKNOWN


def _find_packed_ref_commit(packed_refs: Path, ref_name: str) -> str:
    """Return the commit of `ref_name` in packed-refs, or "" when it is absent."""
    packed_lines = packed_refs.read_text(encoding="utf-8").splitlines()
    for line in packed_lines:
        commit, _, name = line.partition(" ")
        if name == ref_name:
            return commit
    return ""


class SessionJournal:
    """Append-only concise session JSONL owned by the single event loop."""

    def __init__(self, archive_dir: Path) -> None:
        """Open `<archive_dir>/session.jsonl` in append mode, truncating a crash tail."""
        self._writer = FsyncedJsonlWriter(archive_dir / SESSION_JOURNAL_FILENAME)
        self._path = self._writer.path

    def write(self, record: SessionRecord) -> None:
        """Append one compact LF-terminated record, then flush and fsync."""
        self._writer.write_record(record)

    def is_fresh(self) -> bool:
        """True when the file has no completed record: provenance must be written."""
        return self._writer.is_fresh()

    def write_start(
        self,
        start: MatchStart,
        git_commit: str,
        sidecar_binding: SidecarBinding | None,
        execution_mode: ExecutionMode = "paper",
        *,
        clip: ClipChoice,
    ) -> None:
        """Write the first provenance record; only ever run on a fresh journal."""
        record: SessionStartRecord = {
            "schema_version": SESSION_SCHEMA_VERSION,
            "kind": "session_start",
            "match_id": start.match_id,
            "condition_id": start.market.condition_id,
            "git_commit": git_commit,
            "model": {"name": start.model.name, "trained_at": start.model.trained_at},
            "execution_mode": execution_mode,
            "sidecar_binding": (
                sidecar_binding_record(sidecar_binding) if sidecar_binding is not None else None
            ),
            "clip_usdc": clip.clip_usdc,
            "clip_reason": clip.reason,
        }
        self.write(record)

    def first_binding(self, match_id: str, condition_id: str) -> SidecarBinding | None:
        """Read the pinned sidecar binding from the first provenance record."""
        return self.first_start(match_id, condition_id).binding

    def first_start(self, match_id: str, condition_id: str) -> JournalStart:
        """Read the pinned start record: mode, model, sidecar binding, clip.

        Schema 1/2 have no execution_mode and pin paper. Schema 7 also pins
        the clip. A resume does not rewrite the start record.
        """
        try:
            with self._path.open("r", encoding="utf-8", newline="\n") as handle:
                first_line = handle.readline()
            document = json.loads(first_line)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            raise TradingDisabled("session journal provenance cannot be read") from None
        try:
            record = require_object(document, _PROVENANCE_LABEL)
            schema_version = require_int(record, "schema_version", _PROVENANCE_LABEL)
            if schema_version not in ACCEPTED_SESSION_SCHEMA_VERSIONS:
                raise TradingDisabled("session journal provenance schema does not match")
            require_exact_keys(record, _start_keys(schema_version), _PROVENANCE_LABEL)
            kind = require_str(record, "kind", _PROVENANCE_LABEL)
            record_match_id = require_str(record, "match_id", _PROVENANCE_LABEL)
            record_condition = require_str(record, "condition_id", _PROVENANCE_LABEL)
            model = require_object(record.get("model"), f"{_PROVENANCE_LABEL} model")
            model_name = require_nonempty_str(model, "name", _PROVENANCE_LABEL)
            model_trained_at = require_nonempty_str(model, "trained_at", _PROVENANCE_LABEL)
            require_nonempty_str(record, "git_commit", _PROVENANCE_LABEL)
            if schema_version in _START_WITH_EXECUTION_MODE:
                mode_raw = require_str(record, "execution_mode", _PROVENANCE_LABEL)
                if mode_raw not in {"paper", "live"}:
                    raise StrictJsonError(
                        f"{_PROVENANCE_LABEL} execution_mode must be paper or live"
                    )
                mode: ExecutionMode = "live" if mode_raw == "live" else "paper"
            else:
                mode = "paper"
            clip = _pinned_clip(record, schema_version)
        except StrictJsonError as exc:
            raise TradingDisabled(f"{exc}") from None
        if kind != "session_start":
            raise TradingDisabled("session journal provenance cannot be read")
        if record_match_id != match_id:
            raise TradingDisabled("session journal provenance match does not match")
        if record_condition != condition_id:
            raise TradingDisabled("session journal provenance condition does not match")
        binding_block = record.get("sidecar_binding")
        binding = None if binding_block is None else parse_sidecar_binding(binding_block)
        return JournalStart(
            binding,
            schema_version,
            mode,
            model_name,
            model_trained_at,
            clip,
        )

    def scan_open_round(
        self, yes_token_id: str, no_token_id: str, min_order_size: float
    ) -> JournalRound:
        """Read fills once; a round is open only while a side is at least min_order_size."""
        sizes: dict[str, float] = {yes_token_id: 0.0, no_token_id: 0.0}
        last_buy_unix: float | None = None
        try:
            with self._path.open("r", encoding="utf-8", newline="\n") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    document = json.loads(line)
                    record = require_object(document, _FILL_LABEL)
                    if record.get("kind") not in {"fill", "late_fill"}:
                        continue
                    if record_venue(record) == "kalshi":
                        continue
                    token_id = require_str(record, "token_id", _FILL_LABEL)
                    side = require_str(record, "side", _FILL_LABEL)
                    if side not in {"BUY", "SELL"}:
                        raise StrictJsonError(f"{_FILL_LABEL} side must be BUY or SELL")
                    if token_id not in sizes:
                        raise StrictJsonError(f"{_FILL_LABEL} token_id is not a market token")
                    position_after = require_number(record, "position_after", _FILL_LABEL)
                    filled_at = parse_utc(require_str(record, "ts_utc", _FILL_LABEL))
                    filled_unix = filled_at.timestamp()
                    sizes[token_id] = position_after
                    held = (
                        sizes[yes_token_id] >= min_order_size
                        or sizes[no_token_id] >= min_order_size
                    )
                    if not held:
                        last_buy_unix = None
                        continue
                    if side == "BUY":
                        last_buy_unix = filled_unix
        except (OSError, ValueError):
            raise TradingDisabled("session journal open round cannot be read") from None
        return JournalRound(
            sizes[yes_token_id],
            sizes[no_token_id],
            last_buy_unix,
        )

    def write_signal(self, decision: SignalDecision) -> None:
        """Write one signal record: decision inputs only, never the book."""
        raw = decision.raw
        snapshot = decision.snapshot
        top = snapshot.top
        game_snapshot: SessionSignalSnapshot = {
            "second": snapshot.second,
            "server_timestamp": snapshot.server_timestamp,
            "phase": snapshot.phase,
            "paused": snapshot.paused,
            "radiant_nw_adv": snapshot.radiant_nw_adv,
            "radiant_nw": snapshot.radiant_nw,
            "dire_nw": snapshot.dire_nw,
            "radiant_xp_adv": snapshot.radiant_xp_adv,
            "deaths_radiant": snapshot.deaths_radiant,
            "deaths_dire": snapshot.deaths_dire,
            "top": {
                "top1_nw_adv": top.top1_nw_adv,
                "radiant_top1_nw_ratio": top.radiant_top1_nw_ratio,
                "dire_top1_nw_ratio": top.dire_top1_nw_ratio,
                "top3_nw_adv": top.top3_nw_adv,
                "radiant_top3_nw_ratio": top.radiant_top3_nw_ratio,
                "dire_top3_nw_ratio": top.dire_top3_nw_ratio,
            },
        }
        record: SessionSignalRecord = {
            "kind": "signal",
            "venue": "polymarket",
            "second": snapshot.second,
            "yes_best_bid": raw.yes_best_bid,
            "yes_best_ask": raw.yes_best_ask,
            "yes_mid": raw.yes_mid,
            "no_best_bid": raw.no_best_bid,
            "no_best_ask": raw.no_best_ask,
            "no_mid": raw.no_mid,
            "market_p_radiant": decision.market_p_radiant,
            "market_radiant_prior": decision.market_radiant_prior,
            "radiant_fair": decision.radiant_fair,
            "yes_fair": decision.yes_fair,
            "reason": decision.reason,
            "entry_block": decision.entry_block,
            "exit_state": decision.exit_state,
            "pos_yes": decision.pos_yes,
            "pos_no": decision.pos_no,
            "recorded_at_utc": datetime.now(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "feed_source": decision.feed_source,
            "feed_received_at_utc": decision.feed_received_at_utc,
            "game_snapshot": game_snapshot,
            "model_evaluated": decision.model_evaluated,
            "raw_delta": decision.raw_delta,
        }
        self.write(record)

    def write_quote(
        self,
        decision: Literal["normal", "reduce_only"],
        fv_source: Literal["model", "engine"],
        placed: list[OpenOrder],
        canceled: list[str],
        second: int,
    ) -> None:
        """Write one quote record for a placement/cancellation batch."""
        record: SessionQuoteRecord = {
            "kind": "quote",
            "venue": "polymarket",
            "decision": decision,
            "fv_source": fv_source,
            "placed": [
                {
                    "token_id": order.token_id,
                    "side": order.side.value,
                    "price": order.price,
                    "size": order.size,
                }
                for order in placed
            ],
            "canceled": canceled,
            "second": second,
        }
        self.write(record)

    def write_fill(
        self,
        fill: Fill,
        position_after: float,
        net_cash: float,
        second: int,
        ts_utc: str,
        fill_key: str,
    ) -> None:
        """Write one durable fill.

        `second` is the last feed game second (markout as-of on the signal tape).
        `ts_utc` is the exchange trade time from Fill.ts, not CONFIRMED receipt.
        `fill_key` is the ledger key for crash correlation.
        """
        record: SessionFillRecord = {
            "kind": "fill",
            "venue": "polymarket",
            "token_id": fill.token_id,
            "side": fill.side.value,
            "price": fill.price,
            "size": fill.size,
            "is_maker": fill.is_maker,
            "position_after": position_after,
            "net_cash": net_cash,
            "second": second,
            "ts_utc": ts_utc,
            "fill_key": fill_key,
        }
        self.write(record)

    def write_tick_change(self, old_tick_size: str, new_tick_size: str) -> None:
        """Write one tick change; the values stay the collector decimal strings."""
        record: SessionTickSizeChangeRecord = {
            "kind": "tick_size_change",
            "old_tick_size": old_tick_size,
            "new_tick_size": new_tick_size,
        }
        self.write(record)

    def write_history_gap(self, second: int) -> None:
        """Record a tick dropped by the history-gap check; its snapshot stays on tape."""
        record: SessionHistoryGapRecord = {
            "kind": "history_gap",
            "venue": "polymarket",
            "second": second,
        }
        self.write(record)

    def write_error(self, phase: str, error_type: str) -> None:
        """Write one trading fault: phase and exception type only."""
        record: SessionTradingErrorRecord = {
            "kind": "trading_error",
            "venue": "polymarket",
            "phase": phase,
            "error_type": error_type,
        }
        self.write(record)

    def write_end(self, terminal_reason: str, snapshot: "SessionEndSnapshot | None") -> None:
        """Write the final record; the cash block is null without a usable engine."""
        if snapshot is None:
            record: SessionEndRecord = {
                "kind": "session_end",
                "venue": "polymarket",
                "terminal_reason": terminal_reason,
                "positions": {},
                "net_cash": None,
                "inventory_value": None,
                "equity": None,
            }
        else:
            record = {
                "kind": "session_end",
                "venue": "polymarket",
                "terminal_reason": terminal_reason,
                "positions": snapshot.positions,
                "net_cash": snapshot.net_cash,
                "inventory_value": snapshot.inventory_value,
                "equity": snapshot.equity,
            }
        self.write(record)

    def close(self) -> None:
        """Flush and close the journal file."""
        self._writer.close()


def append_late_fill(
    archive_dir: Path,
    fill: Fill,
    position_after: float,
    net_cash: float,
    ts_utc: str,
    fill_key: str,
    source: Literal["user_ws", "rest_backfill"],
) -> None:
    """Append one late_fill to a closed session.jsonl, then fsync and close."""
    writer = FsyncedJsonlWriter(archive_dir / SESSION_JOURNAL_FILENAME)
    side: Literal["BUY", "SELL"] = "SELL" if fill.side is Side.SELL else "BUY"
    record: SessionLateFillRecord = {
        "kind": "late_fill",
        "venue": "polymarket",
        "token_id": fill.token_id,
        "side": side,
        "price": fill.price,
        "size": fill.size,
        "is_maker": fill.is_maker,
        "position_after": position_after,
        "net_cash": net_cash,
        "second": 0,
        "ts_utc": ts_utc,
        "fill_key": fill_key,
        "source": source,
    }
    writer.write_record(record)
    writer.close()


def read_maker_rebate_usdc(archive_dir: Path) -> float:
    """Sum maker rebates from fill and late_fill rows. A bad fill row raises."""
    path = archive_dir / SESSION_JOURNAL_FILENAME
    if not path.is_file():
        return 0.0
    total = 0.0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = require_object(json.loads(line), _FILL_LABEL)
            if record.get("kind") not in {"fill", "late_fill"}:
                continue
            if not require_bool(record, "is_maker", _FILL_LABEL):
                continue
            price = require_number(record, "price", _FILL_LABEL)
            size = require_number(record, "size", _FILL_LABEL)
            total += maker_rebate_usdc(price=price, size=size)
    return total


def read_market_kind(archive_dir: Path) -> str:
    """Market kind pinned on session_start, or unknown when the journal has none."""
    path = archive_dir / SESSION_JOURNAL_FILENAME
    if not path.is_file():
        return "unknown"
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = require_object(json.loads(line), _PROVENANCE_LABEL)
            if record.get("kind") != "session_start":
                continue
            binding = record.get("sidecar_binding")
            if binding is None:
                return "unknown"
            return parse_sidecar_binding(binding).market_kind
    return "unknown"


def open_session_journal(
    archive_dir: Path,
    start: MatchStart,
    preview_sidecar: FreshSidecar | None,
    prior_archive: bool,
    git_commit: str,
    mode: ExecutionMode,
    *,
    clip: ClipChoice,
) -> SessionJournal | None:
    """Open the session journal and pin or read provenance; None on a fault."""
    try:
        journal_path = archive_dir / SESSION_JOURNAL_FILENAME
        if prior_archive and not journal_path.exists():
            raise TradingDisabled("session journal baseline is missing for an existing archive")
        journal = SessionJournal(archive_dir)
        try:
            if journal.is_fresh():
                if prior_archive:
                    raise TradingDisabled(
                        "session journal baseline is missing for an existing archive"
                    )
                binding = sidecar_binding(preview_sidecar) if preview_sidecar is not None else None
                journal.write_start(
                    start,
                    git_commit,
                    binding,
                    mode,
                    clip=clip,
                )
        except BaseException:
            journal.close()  # never leak an opened journal on a failed provenance write
            raise
    except Exception as exc:
        logger.warning("trader session journal unavailable: %s", type(exc).__name__)
        notify_in_background(f"trader session journal unavailable: {type(exc).__name__}")
        return None
    try:
        journal.first_binding(start.match_id, start.market.condition_id)
    except TradingDisabled as exc:
        logger.warning("trader session provenance unavailable: %s", exc)
        notify_in_background("trader session provenance unavailable: TradingDisabled")
        journal.close()
        return None
    return journal


class FaultReporter:
    """Type-only trading fault reporting: log + journal + deduped Telegram."""

    def __init__(self, journal: SessionJournal) -> None:
        self._journal = journal
        self._alerted: set[tuple[str, str]] = set()

    def report(self, phase: str, error_type: str) -> None:
        """Report one fault: warning log, best-effort journal, deduped background alert."""
        logger.warning("trader trading %s failed: %s", phase, error_type)
        try:
            self._journal.write_error(phase, error_type)
        except Exception as journal_exc:
            logger.warning("trader trading fault journal failed: %s", type(journal_exc).__name__)
        key = (phase, error_type)
        if key in self._alerted:
            return
        self._alerted.add(key)
        notify_in_background(f"trader trading {phase} failed: {error_type}")
