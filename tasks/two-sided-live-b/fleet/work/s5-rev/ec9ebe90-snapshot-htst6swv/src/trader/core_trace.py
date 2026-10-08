"""Append-only live core decision trace. Explains decisions; not money truth."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from shared.utils.gbm import model_identity_sha256
from shared.utils.log import get_logger
from strategy.policy import Follow300Policy
from strategy.types import FreshnessLimits, InboundEvent, MarketLimits, Plan, StrategyState
from trader.archive_paths import truncate_after_last_newline
from trader.core_persistence import StructuralCheckpoint
from trader.core_trace_codec import (
    CORE_TRACE_SCHEMA_VERSION,
    TraceHeader,
    checkpoint_object,
    digest_state,
    dumps_trace,
    encode_event,
    encode_plan,
    jsonable,
    policy_sha,
)
from trader.game_profile import GAME_PROFILES
from trader.paths import CORE_TRACE_FILENAME
from trader.strict_json import require_int, require_object

logger = get_logger(__name__)

# ponytail: 256 MB per market; gzip or per-episode rotation if maps exceed this
MAX_TRACE_BYTES = 256 * 1024 * 1024


def model_file_sha(game: str) -> str:
    return model_identity_sha256(GAME_PROFILES[game].primary.model_dir)


def _resume_seq(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    size = path.stat().st_size
    if size == 0:
        return 0, 0
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        take = min(end, 16384)
        handle.seek(end - take)
        chunk = handle.read()
    if not chunk.endswith(b"\n"):
        raise OSError("trace tail is not newline-terminated")
    line = chunk.split(b"\n")[-2]
    row = json.loads(line.decode())
    fields = require_object(row, "trace last row")
    seq = require_int(fields, "seq", "trace last row")
    return seq + 1, size


class CoreTrace:
    """Buffered JSONL writer. Flush on reset/close; never fsync. Never raises into trading."""

    def __init__(self, path: Path, handle: TextIO, *, next_seq: int, nbytes: int) -> None:
        self._path = path
        self._handle: TextIO | None = handle
        self._next_seq = next_seq
        self._last_seq = next_seq - 1
        self._bytes = nbytes
        self._failed = False

    @classmethod
    def open(cls, path: Path) -> "CoreTrace":
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            truncate_after_last_newline(path)
        next_seq, nbytes = _resume_seq(path)
        handle = path.open("a", encoding="utf-8", newline="\n")
        return cls(path, handle, next_seq=next_seq, nbytes=nbytes)

    @property
    def last_seq(self) -> int:
        return self._last_seq

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def path(self) -> Path:
        return self._path

    def write_header(self, header: TraceHeader) -> int | None:
        return self._emit(lambda: _header_row(header))

    def write_event(
        self,
        *,
        now_ns: int,
        event: InboundEvent,
        plan: Plan,
        next_wake_ns: int,
        state: StrategyState,
    ) -> int | None:
        return self._emit(
            lambda: {
                "kind": "event",
                "now_ns": now_ns,
                "event": encode_event(event),
                "plan": encode_plan(plan),
                "next_wake_ns": next_wake_ns,
                "digests": digest_state(state),
            }
        )

    def write_reset(
        self, *, now_ns: int, now_wall_s: float, checkpoint: StructuralCheckpoint
    ) -> int | None:
        return self._emit(
            lambda: {
                "kind": "reset",
                "now_ns": now_ns,
                "now_wall_s": now_wall_s,
                "checkpoint": checkpoint_object(checkpoint),
            },
            flush=True,
        )

    def write_last_buy(self, *, token_index: int, last_buy_ns: int) -> int | None:
        return self._emit(
            lambda: {
                "kind": "last_buy",
                "token_index": token_index,
                "last_buy_ns": last_buy_ns,
            }
        )

    def write_revert(self, *, to_seq: int) -> int | None:
        return self._emit(lambda: {"kind": "revert", "to_seq": to_seq})

    def _emit(self, build: Callable[[], dict[str, object]], *, flush: bool = False) -> int | None:
        if self._failed or self._handle is None:
            return None
        assigned = self._next_seq
        try:
            payload = {"seq": assigned, **build()}
            line = dumps_trace(payload) + "\n"
            self._handle.write(line)
            if flush:
                self._handle.flush()
        except (OSError, ValueError, TypeError) as exc:
            self._fail(exc)
            return None
        self._next_seq = assigned + 1
        self._last_seq = assigned
        self._bytes += len(line.encode())
        if self._bytes > MAX_TRACE_BYTES:
            self._truncate_cap()
        return assigned

    def _truncate_cap(self) -> None:
        if self._failed or self._handle is None:
            return
        assigned = self._next_seq
        payload: dict[str, object] = {"seq": assigned, "kind": "truncated", "reason": "size_cap"}
        try:
            line = dumps_trace(payload) + "\n"
            self._handle.write(line)
            self._handle.flush()
        except (OSError, ValueError, TypeError) as exc:
            self._fail(exc)
            return
        self._next_seq = assigned + 1
        self._last_seq = assigned
        self.close()
        self._failed = True

    def _fail(self, exc: BaseException) -> None:
        if not self._failed:
            # One line named the type and nothing else, so a NaN that killed the
            # writer mid-map took a sqlite dig to find. Keep the traceback.
            logger.warning("core trace unavailable: %s", type(exc).__name__, exc_info=exc)
        self._failed = True
        self.close()

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.flush()
            handle.close()
        except OSError as exc:
            if not self._failed:
                logger.warning("core trace close failed: %s", type(exc).__name__)
            self._failed = True


def try_open_trace(archive_dir: Path) -> CoreTrace | None:
    try:
        return CoreTrace.open(archive_dir / CORE_TRACE_FILENAME)
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        logger.warning("core trace unavailable: %s", type(exc).__name__)
        return None


def header_for_market(
    *,
    session_id: str,
    match_id: str,
    game: str,
    execution_mode: str,
    git_commit: str,
    policy: Follow300Policy,
    limits: MarketLimits,
    freshness: FreshnessLimits,
    model_name: str,
    model_trained_at: str,
    yes_token: str,
    no_token: str,
) -> TraceHeader | None:
    try:
        model_sha256 = model_file_sha(game)
    except OSError as exc:
        logger.warning("core trace model hash failed: %s", type(exc).__name__)
        return None
    return TraceHeader(
        session_id=session_id,
        match_id=match_id,
        game=game,
        execution_mode=execution_mode,
        git_commit=git_commit,
        policy=policy,
        policy_sha=policy_sha(policy, limits, freshness),
        limits=limits,
        freshness=freshness,
        model_name=model_name,
        model_trained_at=model_trained_at,
        model_sha256=model_sha256,
        yes_token=yes_token,
        no_token=no_token,
    )


def start_market_trace(trace: CoreTrace | None, header: TraceHeader | None) -> CoreTrace | None:
    if trace is None:
        return None
    if header is None:
        trace.close()
        return None
    if trace.write_header(header) is None:
        return None
    return trace


def _header_row(header: TraceHeader) -> dict[str, object]:
    return {
        "kind": "header",
        "schema_version": CORE_TRACE_SCHEMA_VERSION,
        "session_id": header.session_id,
        "match_id": header.match_id,
        "game": header.game,
        "execution_mode": header.execution_mode,
        "git_commit": header.git_commit,
        "policy": jsonable(header.policy),
        "policy_sha": header.policy_sha,
        "limits": jsonable(header.limits),
        "freshness": jsonable(header.freshness),
        "model": {
            "name": header.model_name,
            "trained_at": header.model_trained_at,
            "sha256": header.model_sha256,
        },
        "yes_token": header.yes_token,
        "no_token": header.no_token,
        "opened_wall_s": time.time(),
        "opened_now_ns": time.monotonic_ns(),
    }
