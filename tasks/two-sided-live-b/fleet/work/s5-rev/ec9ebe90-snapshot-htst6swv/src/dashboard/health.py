import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dashboard.logs import LOG_MAX_BYTES, LOG_TIMEOUT_S, LogRunner, log_ts
from shared.utils.match_time import parse_utc

HEALTH_LOG_TAIL = 10000
_HEALTH_EVIDENCE = 8


@dataclass(frozen=True)
class HealthFacts:
    ok: bool
    error: str | None
    attempt_at: float | None
    read_at: float | None
    container_id: str | None
    container_name: str | None
    container_state: str | None
    started_at: float | None
    consistent: bool
    halted: bool | None
    halt_label: str | None
    halt_at: float | None
    cleared_at: float | None
    history_complete: bool
    evidence: tuple[str, ...]


EMPTY_HEALTH = HealthFacts(
    ok=False,
    error=None,
    attempt_at=None,
    read_at=None,
    container_id=None,
    container_name=None,
    container_state=None,
    started_at=None,
    consistent=False,
    halted=None,
    halt_label=None,
    halt_at=None,
    cleared_at=None,
    history_complete=False,
    evidence=(),
)


@dataclass(frozen=True)
class RunFacts:
    halted: bool | None
    halt_label: str | None
    halt_at: float | None
    cleared_at: float | None
    evidence: tuple[str, ...]


_CLEARED = re.compile(r"alert cleared key=(\S+)")
_ALERT_KEY = re.compile(r"key=(\S+)")
_RISK_SCOPE = re.compile(r"risk_halt[:=](\S+)")


def _scope(line: str) -> str | None:
    key = _ALERT_KEY.search(line)
    if key is not None:
        return key.group(1)
    scoped = _RISK_SCOPE.search(line)
    if scoped is not None:
        return f"risk_halt:{scoped.group(1)}"
    if "risk_halt" in line:
        return "risk_halt"
    if "HALTED" in line:
        return "HALTED"
    return None


def reduce_run(text: str, *, started_at: float | None, history_complete: bool) -> RunFacts:
    latched: set[str] = set()
    halt_label: str | None = None
    halt_at: float | None = None
    cleared_at: float | None = None
    evidence: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        ts = log_ts(line)
        if started_at is not None and ts is not None and ts < started_at:
            continue
        cleared = _CLEARED.search(line)
        if cleared is not None:
            scope = cleared.group(1)
            latched.discard(scope)
            cleared_at = ts if ts is not None else cleared_at
            evidence.append(line)
            continue
        if "risk_halt" not in line and "HALTED" not in line:
            continue
        scope = _scope(line)
        if scope is None:
            continue
        latched.add(scope)
        halt_label = scope
        halt_at = ts if ts is not None else halt_at
        evidence.append(line)
    return RunFacts(
        halted=bool(latched) if history_complete else None,
        halt_label=halt_label,
        halt_at=halt_at,
        cleared_at=cleared_at,
        evidence=tuple(evidence[-_HEALTH_EVIDENCE:]),
    )


def _ps_id(compose_file: Path, service: str, runner: LogRunner, timeout_s: float) -> str | None:
    argv = ["docker", "compose", "-f", str(compose_file), "ps", "-q", service]
    raw = runner(argv, timeout_s)
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


@dataclass(frozen=True)
class _Inspect:
    state: str | None
    started_at: float | None
    name: str | None


def _inspect(container_id: str, runner: LogRunner, timeout_s: float) -> _Inspect:
    argv = [
        "docker",
        "inspect",
        "--format",
        "{{.State.Status}}|{{.State.StartedAt}}|{{.Name}}",
        container_id,
    ]
    raw = runner(argv, timeout_s)
    text = raw.decode("utf-8", errors="replace").strip()
    state, _, rest = text.partition("|")
    started_raw, _, name = rest.partition("|")
    started_at: float | None = None
    try:
        started_at = parse_utc(started_raw).timestamp()
    except ValueError:
        started_at = None
    return _Inspect(
        state=state or None,
        started_at=started_at,
        name=name.lstrip("/") or None,
    )


def _run_logs(
    container_id: str,
    *,
    started_at: float | None,
    tail: int,
    runner: LogRunner,
    timeout_s: float,
) -> bytes:
    argv = ["docker", "logs", "--timestamps"]
    if started_at is not None:
        since = datetime.fromtimestamp(started_at, UTC).isoformat()
        argv += ["--since", since]
    argv += ["--tail", str(tail), container_id]
    return runner(argv, timeout_s)


def _read_result(
    *,
    ok: bool,
    error: str | None,
    read_at: float,
    container_id: str | None = None,
    container_name: str | None = None,
    container_state: str | None = None,
    started_at: float | None = None,
    consistent: bool,
    halted: bool | None = None,
    halt_label: str | None = None,
    halt_at: float | None = None,
    cleared_at: float | None = None,
    history_complete: bool,
    evidence: tuple[str, ...] = (),
) -> HealthFacts:
    return HealthFacts(
        ok=ok,
        error=error,
        attempt_at=None,
        read_at=read_at,
        container_id=container_id,
        container_name=container_name,
        container_state=container_state,
        started_at=started_at,
        consistent=consistent,
        halted=halted,
        halt_label=halt_label,
        halt_at=halt_at,
        cleared_at=cleared_at,
        history_complete=history_complete,
        evidence=evidence,
    )


def read_health(
    *,
    compose_file: Path,
    service: str,
    runner: LogRunner,
    timeout_s: float = LOG_TIMEOUT_S,
    tail: int = HEALTH_LOG_TAIL,
    max_bytes: int = LOG_MAX_BYTES,
    wall: Callable[[], float] = time.time,
) -> HealthFacts:
    read_at = wall()
    if not compose_file.is_file():
        return _read_result(
            ok=False,
            error="compose file missing",
            read_at=read_at,
            consistent=False,
            history_complete=False,
        )
    try:
        container_id = _ps_id(compose_file, service, runner, timeout_s)
    except Exception as exc:
        return _read_result(
            ok=False,
            error=type(exc).__name__,
            read_at=read_at,
            consistent=False,
            history_complete=False,
        )
    if container_id is None:
        return _read_result(
            ok=True,
            error=None,
            read_at=read_at,
            consistent=True,
            history_complete=True,
        )
    try:
        info = _inspect(container_id, runner, timeout_s)
        raw = _run_logs(
            container_id,
            started_at=info.started_at,
            tail=tail,
            runner=runner,
            timeout_s=timeout_s,
        )
        second_id = _ps_id(compose_file, service, runner, timeout_s)
    except Exception as exc:
        return _read_result(
            ok=False,
            error=type(exc).__name__,
            read_at=read_at,
            container_id=container_id,
            consistent=False,
            history_complete=False,
        )
    consistent = second_id == container_id
    byte_truncated = len(raw) > max_bytes
    text = (
        raw[-max_bytes:].decode("utf-8", errors="replace")
        if byte_truncated
        else raw.decode("utf-8", errors="replace")
    )
    lines = text.splitlines()
    tail_limited = len(lines) >= tail
    complete = consistent and not byte_truncated and not tail_limited
    running = info.state == "running"
    run = reduce_run(text, started_at=info.started_at, history_complete=complete)
    return _read_result(
        ok=True,
        error=None,
        read_at=read_at,
        container_id=container_id,
        container_name=info.name,
        container_state=info.state,
        started_at=info.started_at,
        consistent=consistent,
        halted=run.halted if running and consistent else None,
        halt_label=run.halt_label,
        halt_at=run.halt_at,
        cleared_at=run.cleared_at,
        history_complete=complete,
        evidence=run.evidence,
    )
