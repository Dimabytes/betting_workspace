import gzip
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dashboard import summarize
from dashboard.diagnostics import stamp_age_s
from dashboard.tails import TailView
from shared.utils.jsonl_io import resolve_jsonl
from trader.paths import (
    CORE_TRACE_FILENAME,
    EXECUTION_CLEANUP_FILENAME,
    SESSION_JOURNAL_FILENAME,
)
from viewer.archive_read import as_float, as_int, as_str, market_block, read_match_document

CATALOG_REFRESH_S = 60.0
STALE_WRITE_SECONDS = 900.0

MapStatus = Literal["live", "stale", "terminal", "final", "incomplete"]


@dataclass(frozen=True)
class SessionParams:
    min_abs_delta: float | None
    buy_cutoff_second: int | None
    min_order_size: float | None
    entry_stale_s: float | None
    exit_stale_s: float | None
    level_usdc: float | None


@dataclass(frozen=True)
class DecisionFacts:
    second: float | None
    snapshot_second: float | None
    phase: str | None
    paused: bool | None
    reason: str | None
    entry_block: str | None
    model_evaluated: bool | None
    raw_delta: float | None
    feed_source: str | None
    recorded_at_utc: str | None
    feed_received_at_utc: str | None
    path: Path


@dataclass(frozen=True)
class ArchiveEntry:
    match_id: str
    archive_dir: Path
    tree: str
    game: str | None
    slug: str | None
    event_slug: str | None
    joined_at_utc: str | None
    condition_id: str | None
    yes_token: str | None
    no_token: str | None
    yes_is_radiant: bool | None
    radiant: str | None
    dire: str | None
    map_number: int | None
    outcome_0_name: str | None
    outcome_1_name: str | None
    finished: bool
    cleanup_proven: bool
    session_ended: bool
    has_journal: bool
    record_only: bool
    last_write: float | None
    realized: float | None
    imv: float | None
    rebate: float | None
    net: float | None
    fill_count: int | None
    closed_observed_at: float | None
    last_decision: DecisionFacts | None
    params: SessionParams | None


@dataclass(frozen=True)
class CatalogSnapshot:
    built_at: float
    entries: tuple[ArchiveEntry, ...]


@dataclass(frozen=True)
class MapView:
    entry: ArchiveEntry
    status: MapStatus
    write_age_s: float | None
    decision_age_s: float | None
    feed_age_s: float | None
    decision: DecisionFacts | None
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class _JournalFacts:
    started: bool
    ended: bool
    realized: float | None
    rebate: float
    imv: float | None
    net: float | None
    fill_count: int | None
    closed_observed_at: float | None
    last_decision: DecisionFacts | None


@dataclass(frozen=True)
class _CachedFacts:
    mtime_ns: int
    size: int
    facts: _JournalFacts


@dataclass(frozen=True)
class _CachedParams:
    mtime_ns: int
    size: int
    params: SessionParams | None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def decision_facts(record: Mapping[str, object] | None, *, path: Path) -> DecisionFacts | None:
    if record is None:
        return None
    snap = record.get("game_snapshot")
    snapshot = cast(dict[str, object], snap) if isinstance(snap, dict) else {}
    return DecisionFacts(
        second=as_float(record.get("second")),
        snapshot_second=as_float(snapshot.get("second")),
        phase=as_str(snapshot.get("phase")),
        paused=_as_bool(snapshot.get("paused")),
        reason=as_str(record.get("reason")),
        entry_block=as_str(record.get("entry_block")),
        model_evaluated=_as_bool(record.get("model_evaluated")),
        raw_delta=as_float(record.get("raw_delta")),
        feed_source=as_str(record.get("feed_source")),
        recorded_at_utc=as_str(record.get("recorded_at_utc")),
        feed_received_at_utc=as_str(record.get("feed_received_at_utc")),
        path=path,
    )


def session_params(
    path: Path, *, match_id: str, yes_token: str | None, no_token: str | None
) -> SessionParams | None:
    header = read_trace_header(path)
    if header is None:
        return None
    if as_str(header.get("match_id")) != match_id:
        return None
    if yes_token is not None and as_str(header.get("yes_token")) != yes_token:
        return None
    if no_token is not None and as_str(header.get("no_token")) != no_token:
        return None
    policy = header.get("policy")
    policy_block = cast(dict[str, object], policy) if isinstance(policy, dict) else {}
    limits = header.get("limits")
    limits_block = cast(dict[str, object], limits) if isinstance(limits, dict) else {}
    freshness = header.get("freshness")
    freshness_block = cast(dict[str, object], freshness) if isinstance(freshness, dict) else {}
    return SessionParams(
        min_abs_delta=as_float(policy_block.get("min_abs_delta")),
        buy_cutoff_second=as_int(policy_block.get("buy_cutoff_second")),
        min_order_size=as_float(limits_block.get("min_order_size")),
        entry_stale_s=as_float(freshness_block.get("entry_stale_s")),
        exit_stale_s=as_float(freshness_block.get("exit_stale_s")),
        level_usdc=as_float(policy_block.get("level_usdc")),
    )


def _closed_observed_at(record: Mapping[str, object] | None) -> float | None:
    if record is None:
        return None
    snap = record.get("game_snapshot")
    phase = (
        as_str(cast(Mapping[str, object], snap).get("phase")) if isinstance(snap, dict) else None
    )
    terminal = record.get("reason") == "finished" or phase == "finished"
    if not terminal:
        return None
    stamp = as_str(record.get("recorded_at_utc"))
    if stamp is None:
        return None
    parsed = summarize.parse_utc(stamp)
    return parsed.timestamp() if parsed is not None else None


def _cleanup_proven(archive_dir: Path, match_id: str, condition_id: str | None) -> bool:
    if condition_id is None:
        return False
    path = archive_dir / EXECUTION_CLEANUP_FILENAME
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(loaded, dict):
        return False
    fields = cast(dict[str, object], loaded)
    return (
        fields.get("schema_version") == 1
        and fields.get("match_id") == match_id
        and fields.get("condition_id") == condition_id
    )


class MatchCatalog:
    def __init__(self, root: Path, tree: str, *, refresh_s: float = CATALOG_REFRESH_S) -> None:
        self._root = root
        self._tree = tree
        self._refresh_s = refresh_s
        self._snapshot: CatalogSnapshot | None = None
        self._built_mono: float | None = None
        self._summaries: dict[Path, _CachedFacts] = {}
        self._params: dict[Path, _CachedParams] = {}

    def snapshot(self, *, now_mono: float, now_wall: float) -> CatalogSnapshot:
        if (
            self._snapshot is not None
            and self._built_mono is not None
            and now_mono - self._built_mono < self._refresh_s
        ):
            return self._snapshot
        snap = self._build(now_wall)
        self._snapshot = snap
        self._built_mono = now_mono
        return snap

    def _params_for(
        self,
        archive_dir: Path,
        *,
        match_id: str,
        yes_token: str | None,
        no_token: str | None,
        keep: dict[Path, _CachedParams],
    ) -> SessionParams | None:
        trace = resolve_jsonl(archive_dir / CORE_TRACE_FILENAME)
        try:
            stat = trace.stat()
        except OSError:
            return None
        cached = self._params.get(archive_dir)
        if (
            cached is not None
            and cached.mtime_ns == stat.st_mtime_ns
            and cached.size == stat.st_size
        ):
            keep[archive_dir] = cached
            return cached.params
        params = session_params(trace, match_id=match_id, yes_token=yes_token, no_token=no_token)
        keep[archive_dir] = _CachedParams(stat.st_mtime_ns, stat.st_size, params)
        return params

    def _facts(self, archive_dir: Path, keep: dict[Path, _CachedFacts]) -> _JournalFacts | None:
        journal = resolve_jsonl(archive_dir / SESSION_JOURNAL_FILENAME)
        try:
            stat = journal.stat()
        except OSError:
            return None
        cached = self._summaries.get(archive_dir)
        if (
            cached is not None
            and cached.mtime_ns == stat.st_mtime_ns
            and cached.size == stat.st_size
        ):
            keep[archive_dir] = cached
            return cached.facts
        summary = summarize.summarize_session(archive_dir)
        stamped = _closed_observed_at(summary.last_signal)
        facts = _JournalFacts(
            started=summary.start is not None,
            ended=summary.end is not None,
            realized=summary.realized,
            rebate=summary.rebate,
            imv=summary.imv,
            net=summary.net,
            fill_count=summary.fill_count,
            closed_observed_at=stat.st_mtime if stamped is None else stamped,
            last_decision=decision_facts(summary.last_signal, path=journal),
        )
        keep[archive_dir] = _CachedFacts(stat.st_mtime_ns, stat.st_size, facts)
        return facts

    def _build(self, now_wall: float) -> CatalogSnapshot:
        entries: list[ArchiveEntry] = []
        children = sorted(self._root.iterdir()) if self._root.is_dir() else []
        keep: dict[Path, _CachedFacts] = {}
        keep_params: dict[Path, _CachedParams] = {}
        for child in children:
            if not child.is_dir() or child.name == "wallet":
                continue
            match_id = child.name
            meta = read_match_document(child)
            market = market_block(meta) if meta is not None else None
            condition_id = as_str(market.get("condition_id")) if market else None
            yes_token = as_str(market.get("yes_token_id")) if market else None
            no_token = as_str(market.get("no_token_id")) if market else None
            facts = self._facts(child, keep)
            params = self._params_for(
                child,
                match_id=match_id,
                yes_token=yes_token,
                no_token=no_token,
                keep=keep_params,
            )
            final = meta.get("final") if meta is not None else None
            finished = final is not None
            cleanup = _cleanup_proven(child, match_id, condition_id)
            writes = [entry.stat().st_mtime for entry in child.glob("*") if entry.is_file()]
            last_write = max(writes) if writes else None
            yes_flag = market.get("yes_is_radiant") if market else None
            teams_raw = meta.get("teams") if meta is not None else None
            teams = cast(dict[str, object], teams_raw) if isinstance(teams_raw, dict) else {}
            entries.append(
                ArchiveEntry(
                    match_id=match_id,
                    archive_dir=child,
                    tree=self._tree,
                    game=as_str(meta.get("game")) if meta is not None else None,
                    slug=as_str(market.get("market_slug")) if market else None,
                    event_slug=as_str(market.get("event_slug")) if market else None,
                    joined_at_utc=as_str(meta.get("joined_at_utc")) if meta is not None else None,
                    condition_id=condition_id,
                    yes_token=yes_token,
                    no_token=no_token,
                    yes_is_radiant=yes_flag if isinstance(yes_flag, bool) else None,
                    radiant=as_str(teams.get("radiant")),
                    dire=as_str(teams.get("dire")),
                    map_number=as_int(meta.get("map_number")) if meta is not None else None,
                    outcome_0_name=as_str(market.get("outcome_0_name")) if market else None,
                    outcome_1_name=as_str(market.get("outcome_1_name")) if market else None,
                    finished=finished,
                    cleanup_proven=cleanup,
                    session_ended=facts.ended if facts is not None else False,
                    has_journal=facts.started if facts is not None else False,
                    record_only=bool(meta and meta.get("record_only")),
                    last_write=last_write,
                    realized=facts.realized if facts is not None else None,
                    imv=facts.imv if facts is not None else None,
                    rebate=facts.rebate if facts is not None else None,
                    net=facts.net if facts is not None else None,
                    fill_count=facts.fill_count if facts is not None else None,
                    closed_observed_at=(facts.closed_observed_at if facts is not None else None),
                    last_decision=facts.last_decision if facts is not None else None,
                    params=params,
                )
            )
        self._summaries = keep
        self._params = keep_params
        return CatalogSnapshot(built_at=now_wall, entries=tuple(entries))


def classify_entry(
    entry: ArchiveEntry,
    *,
    now_wall: float,
    tail: TailView | None,
    run_started_at: float | None,
    stale_s: float = STALE_WRITE_SECONDS,
) -> MapView:
    evidence: list[str] = []
    last_write = entry.last_write
    write_age = None if last_write is None else max(0.0, now_wall - last_write)
    writer_gone = (
        run_started_at is not None and last_write is not None and last_write < run_started_at
    )
    tail_record = tail.last_signal if tail is not None else None
    decision = (
        decision_facts(tail_record, path=tail.path)
        if tail is not None and tail_record is not None
        else entry.last_decision
    )
    decision_age = stamp_age_s(now_wall, decision.recorded_at_utc) if decision else None
    feed_age = stamp_age_s(now_wall, decision.feed_received_at_utc) if decision else None
    if entry.cleanup_proven:
        evidence.append("execution_cleanup")
        status: MapStatus = "final"
    elif entry.finished:
        evidence.append("match_final")
        status = "final"
    elif entry.session_ended or (tail is not None and tail.saw_session_end):
        evidence.append("session_end")
        status = "terminal"
    elif not entry.has_journal:
        evidence.append("record_only" if entry.record_only else "no_journal")
        status = "incomplete"
    elif write_age is not None and write_age > stale_s:
        if writer_gone:
            evidence.append("writer_gone")
            status = "terminal"
        else:
            evidence.append("stale_writes")
            status = "stale"
    else:
        evidence.append("recent_writes")
        status = "live"
    return MapView(
        entry=entry,
        status=status,
        write_age_s=write_age,
        decision_age_s=decision_age,
        feed_age_s=feed_age,
        decision=decision,
        evidence=tuple(evidence),
    )


def read_trace_header(path: Path) -> dict[str, object] | None:
    resolved = resolve_jsonl(path)
    try:
        if resolved.suffix == ".gz":
            with gzip.open(resolved, "rt", encoding="utf-8", newline="\n") as handle:
                first = handle.readline()
        else:
            with resolved.open("r", encoding="utf-8", newline="\n") as handle:
                first = handle.readline()
    except OSError:
        return None
    try:
        record = json.loads(first)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    document = cast(dict[str, object], record)
    if document.get("kind") != "header":
        return None
    return document


def trace_limits(path: Path) -> dict[str, float] | None:
    header = read_trace_header(path)
    if header is None:
        return None
    limits = header.get("limits")
    if not isinstance(limits, dict):
        return None
    fields = cast(dict[str, object], limits)
    out: dict[str, float] = {}
    for key in ("min_order_size", "tick_size", "pair_sum_tolerance"):
        value = as_float(fields.get(key))
        if value is not None and math.isfinite(value):
            out[key] = value
    return out or None
