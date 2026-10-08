import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dashboard.catalog import read_trace_header
from dashboard.tails import RecordTail, TailCache
from shared.utils.jsonl_io import resolve_jsonl
from viewer.archive_read import as_float, as_int, as_str


@dataclass(frozen=True)
class AdvisoryBudget:
    cap_room_usdc: float | None
    seq: int
    wall_ts: float | None


@dataclass(frozen=True)
class AdvisoryTrace:
    path: Path | None
    header_seen: bool
    identity_ok: bool
    budget: AdvisoryBudget | None
    notes: tuple[str, ...]


def _wall_ts(header: Mapping[str, object], now_ns: int | None) -> float | None:
    opened_wall = as_float(header.get("opened_wall_s"))
    opened_ns = as_int(header.get("opened_now_ns"))
    if opened_wall is None or opened_ns is None or now_ns is None:
        return None
    return opened_wall + (now_ns - opened_ns) / 1_000_000_000.0


def _finite(value: object) -> float | None:
    parsed = as_float(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _budget(
    fields: Mapping[str, object], seq: int | None, wall_ts: float | None
) -> AdvisoryBudget | None:
    if seq is None:
        return None
    return AdvisoryBudget(
        cap_room_usdc=_finite(fields.get("cap_room_usdc")),
        seq=seq,
        wall_ts=wall_ts,
    )


@dataclass
class _Fold:
    invalid: bool
    notes: list[str]
    budget: AdvisoryBudget | None = None
    min_seq: int | None = None


def _fold_revert(fold: _Fold, record: Mapping[str, object]) -> None:
    to_seq = as_int(record.get("to_seq"))
    if to_seq is None:
        return
    if fold.budget is not None and fold.budget.seq > to_seq:
        fold.budget = None
    if fold.min_seq is not None and to_seq < fold.min_seq:
        fold.invalid = True
        fold.notes.append("trace: revert уходит за пределы хвоста — оценка не доказана")


def _fold_event(fold: _Fold, record: Mapping[str, object], header: Mapping[str, object]) -> None:
    event = record.get("event")
    if not isinstance(event, dict):
        return
    event_fields = cast(dict[str, object], event)
    seq = as_int(record.get("seq"))
    wall_ts = _wall_ts(header, as_int(record.get("now_ns")))
    event_type = as_str(event_fields.get("type"))
    if event_type == "BudgetUpdate":
        body = event_fields.get("budget")
        if isinstance(body, dict):
            seen = _budget(cast(dict[str, object], body), seq, wall_ts)
            if seen is not None:
                fold.budget = seen


def _fold_record(fold: _Fold, record: Mapping[str, object], header: Mapping[str, object]) -> None:
    seq = as_int(record.get("seq"))
    if seq is not None and (fold.min_seq is None or seq < fold.min_seq):
        fold.min_seq = seq
    kind = as_str(record.get("kind"))
    if kind == "truncated":
        fold.invalid = True
        fold.notes.append("trace: записано усечение size_cap")
        return
    if kind == "revert":
        _fold_revert(fold, record)
        return
    if kind == "reset":
        fold.budget = None
        return
    if kind == "event":
        _fold_event(fold, record, header)


def _tail_notes(tail: RecordTail) -> tuple[list[str], bool]:
    notes: list[str] = []
    invalid = tail.read_error or tail.malformed > 0
    if tail.read_error:
        notes.append("trace: ошибка чтения хвоста")
    if tail.malformed:
        notes.append(f"trace: {tail.malformed} битых строк в хвосте")
    if tail.truncated_start:
        notes.append("trace: хвост обрезан — показана последняя записанная оценка")
    if not tail.records:
        notes.append("trace: записей нет")
    return notes, invalid


def fold_advisory(tail: RecordTail, *, header: Mapping[str, object]) -> AdvisoryTrace:
    notes, invalid = _tail_notes(tail)
    fold = _Fold(invalid=invalid, notes=notes)
    for record in tail.records:
        _fold_record(fold, record, header)
    if fold.invalid:
        fold.budget = None
    return AdvisoryTrace(
        path=tail.path,
        header_seen=True,
        identity_ok=True,
        budget=fold.budget,
        notes=tuple(notes),
    )


def read_advisory(
    tails: TailCache,
    archive_dir: Path,
    *,
    match_id: str,
    session_id: str | None,
    yes_token: str | None,
    no_token: str | None,
    trace_file: str,
) -> AdvisoryTrace:
    path = resolve_jsonl(archive_dir / trace_file)
    header = read_trace_header(path)
    if header is None:
        return AdvisoryTrace(
            path=None,
            header_seen=False,
            identity_ok=False,
            budget=None,
            notes=("trace: заголовок не найден — advisory недоступен",),
        )
    mismatches: list[str] = []
    if as_str(header.get("match_id")) != match_id:
        mismatches.append("match_id")
    if session_id is not None and as_str(header.get("session_id")) != session_id:
        mismatches.append("session_id")
    if yes_token is not None and as_str(header.get("yes_token")) != yes_token:
        mismatches.append("yes_token")
    if no_token is not None and as_str(header.get("no_token")) != no_token:
        mismatches.append("no_token")
    if mismatches:
        return AdvisoryTrace(
            path=path,
            header_seen=True,
            identity_ok=False,
            budget=None,
            notes=(f"trace: чужая сессия ({', '.join(mismatches)}) — advisory отключён",),
        )
    tail = tails.read_records(path)
    if tail is None:
        return AdvisoryTrace(
            path=path,
            header_seen=True,
            identity_ok=True,
            budget=None,
            notes=("trace: файл не читается",),
        )
    return fold_advisory(tail, header=header)
