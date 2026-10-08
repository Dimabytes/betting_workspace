import json
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from shared.utils.json_read import as_map
from shared.utils.match_time import parse_utc
from trader.collector_sidecars import FreshSidecar, scan_sidecars

LOG_TAIL_LINES = 2000
LOG_SINCE_S = 3 * 3600
LOG_TIMEOUT_S = 15.0
LOG_MAX_BYTES = 1 << 20


@dataclass(frozen=True)
class SkipNote:
    condition_id: str
    reason: str
    ts: float | None
    line: str


@dataclass(frozen=True)
class HaltLine:
    ts: float | None
    line: str


@dataclass(frozen=True)
class ServiceLogs:
    ok: bool
    service: str
    text: str
    truncated: bool
    error: str | None
    read_at: float


@dataclass(frozen=True)
class SkippedMarket:
    condition_id: str
    game: str
    event_slug: str
    market_slug: str
    map_number: int | None
    outcome_names: tuple[str, str]
    reason: str
    reason_source: str
    reason_ts: float | None
    starts_at: float | None


@dataclass(frozen=True)
class NontradingResult:
    markets: tuple[SkippedMarket, ...]
    invalid_sidecars: int
    missing_roots: tuple[str, ...]
    scanned_at: float


_SKIP_MARKERS = ("trader skip:", "live-paper skip:")
_SKIP_KV = re.compile(r"(\w+)=(\S+)")
_LOG_TS = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_HALT_MARKERS = ("risk_halt", "HALTED")


def log_ts(line: str) -> float | None:
    match = _LOG_TS.search(line)
    if match is None:
        return None
    try:
        return parse_utc(match.group(1)).timestamp()
    except ValueError:
        return None


def latest_skip_reasons(log_text: str) -> dict[str, SkipNote]:
    notes: dict[str, SkipNote] = {}
    for line in log_text.splitlines():
        if not any(marker in line for marker in _SKIP_MARKERS):
            continue
        fields = dict(_SKIP_KV.findall(line))
        cid = fields.get("cid") or fields.get("archive_cid") or fields.get("record_cid")
        if cid is None:
            continue
        reason = fields.get("reason", "unknown")
        notes[cid] = SkipNote(
            condition_id=cid,
            reason=reason,
            ts=log_ts(line),
            line=line.strip(),
        )
    return notes


def halt_events(log_text: str) -> tuple[HaltLine, ...]:
    out: list[HaltLine] = []
    for line in log_text.splitlines():
        if any(marker in line for marker in _HALT_MARKERS):
            out.append(HaltLine(ts=log_ts(line), line=line.strip()))
    return tuple(out)


LogRunner = Callable[[Sequence[str], float], bytes]


def docker_runner(argv: Sequence[str], timeout_s: float) -> bytes:
    proc = subprocess.run(list(argv), capture_output=True, timeout=timeout_s, check=False)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[:200]
        raise OSError(f"docker exited {proc.returncode}: {tail}")
    return proc.stdout


def read_service_logs(
    *,
    compose_file: Path,
    service: str,
    since_s: int = LOG_SINCE_S,
    tail: int = LOG_TAIL_LINES,
    timeout_s: float = LOG_TIMEOUT_S,
    max_bytes: int = LOG_MAX_BYTES,
    runner: LogRunner = docker_runner,
) -> ServiceLogs:
    read_at = time.time()
    if not compose_file.is_file():
        return ServiceLogs(
            ok=False,
            service=service,
            text="",
            truncated=False,
            error="compose file missing",
            read_at=read_at,
        )
    argv = [
        "docker",
        "compose",
        "-f",
        str(compose_file),
        "logs",
        "--timestamps",
        "--no-log-prefix",
        "--since",
        f"{since_s}s",
        "--tail",
        str(tail),
        service,
    ]
    try:
        raw = runner(argv, timeout_s)
    except Exception as exc:
        return ServiceLogs(
            ok=False,
            service=service,
            text="",
            truncated=False,
            error=type(exc).__name__,
            read_at=read_at,
        )
    truncated = len(raw) > max_bytes
    text = (
        raw[-max_bytes:].decode("utf-8", errors="replace")
        if truncated
        else raw.decode("utf-8", errors="replace")
    )
    return ServiceLogs(
        ok=True,
        service=service,
        text=text,
        truncated=truncated,
        error=None,
        read_at=read_at,
    )


def _skip_reason(
    sidecar: FreshSidecar, skips: Mapping[str, SkipNote]
) -> tuple[str, str, float | None]:
    note = skips.get(sidecar.condition_id)
    if note is not None:
        return note.reason, "log", note.ts
    if not sidecar.active:
        return "market inactive", "flags", None
    if sidecar.accepting_orders is False:
        return "not accepting orders", "flags", None
    if not sidecar.enable_order_book:
        return "order book disabled", "flags", None
    return "reason unknown", "unknown", None


def _start_stamp(market: Mapping[str, object]) -> float | None:
    sports = as_map(market.get("sports"))
    raw = sports.get("gameStartTime") if sports is not None else None
    if not isinstance(raw, str):
        top = market.get("gameStartTime")
        raw = top if isinstance(top, str) else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return parse_utc(raw).timestamp()
    except ValueError:
        return None


def _event_starts(root: Path, event_id: str) -> dict[str, float]:
    path = root / "metadata" / "events" / f"{event_id}.json"
    try:
        loaded = cast(object, json.loads(path.read_text()))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    document = as_map(loaded)
    if document is None:
        return {}
    event = as_map(document.get("event"))
    if event is None:
        return {}
    markets = event.get("markets")
    if not isinstance(markets, list):
        return {}
    found: dict[str, float] = {}
    for item in cast(list[object], markets):
        market = as_map(item)
        if market is None:
            continue
        condition_id = market.get("conditionId")
        if not isinstance(condition_id, str) or not condition_id:
            continue
        stamp = _start_stamp(market)
        if stamp is not None:
            found[condition_id] = stamp
    return found


def list_nontrading(
    *,
    roots: Mapping[str, Path],
    traded_cids: frozenset[str],
    skips: Mapping[str, SkipNote],
    now_epoch: float,
) -> NontradingResult:
    markets: list[SkippedMarket] = []
    invalid = 0
    missing: list[str] = []
    starts_by_event: dict[tuple[str, str], dict[str, float]] = {}
    for game, root in sorted(roots.items()):
        if not (root / "metadata" / "markets").is_dir():
            missing.append(game)
            continue
        scan = scan_sidecars(root, now_epoch)
        invalid += scan.invalid_count
        for sidecar in scan.sidecars:
            if sidecar.market_kind != "map_winner" or sidecar.closed:
                continue
            if sidecar.condition_id in traded_cids:
                continue
            reason, source, ts = _skip_reason(sidecar, skips)
            event_key = (str(root), sidecar.event_id)
            starts = starts_by_event.get(event_key)
            if starts is None:
                starts = _event_starts(root, sidecar.event_id)
                starts_by_event[event_key] = starts
            markets.append(
                SkippedMarket(
                    condition_id=sidecar.condition_id,
                    game=game,
                    event_slug=sidecar.event_slug,
                    market_slug=sidecar.market_slug,
                    map_number=sidecar.map_number,
                    outcome_names=(sidecar.outcome_0_name, sidecar.outcome_1_name),
                    reason=reason,
                    reason_source=source,
                    reason_ts=ts,
                    starts_at=starts.get(sidecar.condition_id),
                )
            )
    return NontradingResult(
        markets=tuple(markets),
        invalid_sidecars=invalid,
        missing_roots=tuple(missing),
        scanned_at=now_epoch,
    )
