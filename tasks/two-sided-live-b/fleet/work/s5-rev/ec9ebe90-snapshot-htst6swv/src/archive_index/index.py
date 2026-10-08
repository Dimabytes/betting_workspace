"""Archive index: scan archive roots, audit admission, publish feed schedules.

Three separate verdicts per archive, per the agreed contract: identity
established, feed suitable for admission, record sufficient to replay the
requested interval. Nothing here reads orders, fills, PnL, or saved model
signals — feed admission never depends on trading outcome or model features.
"""

from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pandas as pd

from archive_index.report import IndexSummary, format_report, write_report
from archive_index.schedule import (
    EXTRACTION_RULES_VERSION,
    FEED_FILENAMES,
    SCHEDULE_SCHEMA_VERSION,
    FeedSchedule,
    ScheduleExtractionError,
    ScheduleStats,
    compute_fingerprint,
    extract_schedule,
    iter_archive_dirs,
    schedule_path_for,
    write_schedule,
)
from archive_index.universe import UniverseMarket, load_universe
from shared.utils.hashing import sha256_file
from shared.utils.jsonl_io import decompressed_size, resolve_jsonl
from shared.utils.log import get_logger
from shared.utils.parquet_io import write_parquet
from shared.utils.parsing import opt_bool, opt_float, opt_int, opt_str
from shared.utils.team_names import orient_outcomes
from trader.archive_types import MatchMeta
from trader.game_profile import GAME_PROFILES
from trader.match_meta import read_match_meta
from trader.paths import MATCH_META_FILENAME
from trader.source_picker import MAX_FEED_DELAY_SECONDS

logger = get_logger(__name__)

INDEX_FILENAME = "index.parquet"

STATUS_OK = "ok"
STATUS_NOT_EVALUATED = "not_evaluated"

# Schedule-contributed row columns: the fixed fingerprints plus every stats
# field — one source of truth, so a new ScheduleStats member flows through.
_SCHEDULE_ROW_KEYS = (
    "schedule_fingerprint",
    "schedule_rules_version",
    "schedule_schema_version",
    "feed_sha256",
    "feed_size",
    "feed_mtime_ns",
    "horn_at_utc",
    *ScheduleStats.__dataclass_fields__,
)


@dataclass(frozen=True)
class IdentityAudit:
    """Identity verdict plus the universe fields an established identity yields."""

    status: str
    detail: str
    event_id: str | None
    yes_token_index: int | None
    contract_kind: str | None
    universe_game_number: int | None
    universe_team_a: str | None
    universe_team_b: str | None


_EMPTY_IDENTITY = IdentityAudit(STATUS_NOT_EVALUATED, "", None, None, None, None, None, None)


@dataclass
class _Work:
    """Per-archive build state: scan, audit, exclusion, extraction outcome."""

    archive_dir: Path
    archive_id: str
    root_label: str
    meta: MatchMeta | None
    meta_error: str | None
    match_json_sha256: str
    identity: IdentityAudit = _EMPTY_IDENTITY
    exclusion: str | None = None
    feed_status: str = STATUS_NOT_EVALUATED
    feed_detail: str = ""
    record_status: str = STATUS_NOT_EVALUATED
    record_detail: str = ""
    schedule_path: Path | None = None
    schedule_fields: dict[str, object] | None = None


@dataclass(frozen=True)
class IndexResult:
    """build_index output: the materialized frame plus headline counts."""

    frame: pd.DataFrame
    summary: IndexSummary


def scan_root(root: Path, root_label: str) -> tuple[list[_Work], int]:
    """Read every direct child's match.json; return works + count of bare dirs.

    A directory holding feed files but no match.json still yields a row flagged
    `meta:missing` — an archive-shaped folder is audit material, not noise.
    """
    works: list[_Work] = []
    dirs_without_meta = 0
    for archive_dir in iter_archive_dirs(root):
        meta_path = archive_dir / MATCH_META_FILENAME
        if not meta_path.is_file():
            if any(resolve_jsonl(archive_dir / name).is_file() for name in FEED_FILENAMES.values()):
                works.append(
                    _Work(
                        archive_dir=archive_dir,
                        archive_id=archive_dir.name,
                        root_label=root_label,
                        meta=None,
                        meta_error="missing",
                        match_json_sha256="",
                    )
                )
            else:
                dirs_without_meta += 1
            continue
        meta: MatchMeta | None = None
        meta_error: str | None = None
        try:
            meta = read_match_meta(meta_path)
        except (OSError, ValueError, RecursionError) as exc:
            meta_error = f"{type(exc).__name__}: {exc}"
        works.append(
            _Work(
                archive_dir=archive_dir,
                archive_id=archive_dir.name,
                root_label=root_label,
                meta=meta,
                meta_error=meta_error,
                match_json_sha256=sha256_file(meta_path),
            )
        )
    return works, dirs_without_meta


def audit_identity(meta: MatchMeta, universe: UniverseMarket | None) -> IdentityAudit:
    """Check the archive's identifiers and sides against the universe row.

    Orientation comes from token ids and `orient_outcomes` on team names —
    YES is never assumed to sit at token index 0.
    """
    market = meta["market"]
    game = meta.get("game") or "dota"
    if game not in GAME_PROFILES:
        return replace(_EMPTY_IDENTITY, status="unsupported_game", detail=game)
    if universe is None:
        return replace(
            _EMPTY_IDENTITY,
            status="condition_not_in_universe",
            detail=market["condition_id"],
        )
    audited = IdentityAudit(
        status=STATUS_OK,
        detail="",
        event_id=universe.event_id,
        yes_token_index=(
            universe.token_ids.index(market["yes_token_id"])
            if market["yes_token_id"] in universe.token_ids
            else None
        ),
        contract_kind=universe.contract_kind,
        universe_game_number=universe.game_number,
        universe_team_a=universe.team_a,
        universe_team_b=universe.team_b,
    )
    if set(universe.token_ids) != {market["yes_token_id"], market["no_token_id"]}:
        return replace(audited, status="token_mismatch", detail="archive token pair differs")
    names = universe.outcome_names or ("", "")
    side_a = universe.team_a or names[0]
    side_b = universe.team_b or (names[1] if len(names) > 1 else "")
    aliases = GAME_PROFILES[game].aliases
    if (
        orient_outcomes(market["outcome_0_name"], market["outcome_1_name"], side_a, side_b, aliases)
        is None
    ):
        return replace(audited, status="side_mismatch", detail="outcome names != universe teams")
    teams = meta["teams"]
    sides = orient_outcomes(
        market["outcome_0_name"],
        market["outcome_1_name"],
        teams["radiant"],
        teams["dire"],
        aliases,
    )
    if sides is None or sides != market["yes_is_radiant"]:
        return replace(
            audited, status="sides_conflict", detail="yes_is_radiant disagrees with team sides"
        )
    if universe.game_number is not None and universe.game_number != meta["map_number"]:
        return replace(
            audited,
            status="map_mismatch",
            detail=f"archive map {meta['map_number']} != universe game {universe.game_number}",
        )
    if universe.market_slug is not None and universe.market_slug != market["market_slug"]:
        return replace(audited, status="slug_mismatch", detail=universe.market_slug)
    if universe.event_slug is not None and universe.event_slug != market["event_slug"]:
        return replace(audited, status="slug_mismatch", detail=universe.event_slug)
    return audited


def _identity_tuple(work: _Work) -> tuple[object, ...]:
    """The fields duplicate archives must agree on to be the same map."""
    meta = work.meta
    assert meta is not None
    market = meta["market"]
    return (
        meta.get("game") or "dota",
        meta["match_id"],
        meta["map_number"],
        meta["steam_match_id"],
        market["condition_id"].lower(),
        market["yes_token_id"],
        market["no_token_id"],
    )


def _duplicate_rank(work: _Work) -> tuple[object, ...]:
    """Winner order: finalized, dir name == doc match_id, latest join, name."""
    meta = work.meta
    assert meta is not None
    return (
        meta["final"] is not None,
        work.archive_id == meta["match_id"],
        meta["joined_at_utc"],
        work.archive_id,
    )


def _mark_conflict(work: _Work, detail: str) -> None:
    """Flag one archive as an identity conflict."""
    work.exclusion = "identity_conflict"
    work.identity = replace(work.identity, status="identity_conflict", detail=detail)


def _resolve_match_group(group: Sequence[_Work]) -> None:
    """Same match_id in several dirs: identical identity tuples mean
    re-recordings (one winner); a disagreement conflicts every member."""
    if len({_identity_tuple(work) for work in group}) > 1:
        for work in group:
            _mark_conflict(work, "archives disagree on match/map/market identity")
        return
    winner = max(group, key=_duplicate_rank)
    for work in group:
        if work is not winner and work.exclusion is None:
            work.exclusion = f"duplicate_of:{winner.archive_id}"


def resolve_duplicates(works: Sequence[_Work]) -> None:
    """Mark duplicate archives and identity conflicts in place.

    A shared condition_id across different match_ids is always a conflict —
    the market binds one match.
    """
    by_match: dict[str, list[_Work]] = {}
    by_condition: dict[str, list[_Work]] = {}
    for work in works:
        if work.meta is None:
            continue
        by_match.setdefault(work.meta["match_id"], []).append(work)
        by_condition.setdefault(work.meta["market"]["condition_id"].lower(), []).append(work)
    for group in by_match.values():
        if len(group) >= 2:
            _resolve_match_group(group)
    for group in by_condition.values():
        if len({work.meta["match_id"] for work in group if work.meta is not None}) < 2:
            continue
        for work in group:
            if work.exclusion is None:
                _mark_conflict(work, "condition_id shared by different match ids")


def _prior_row_map(index_path: Path) -> dict[tuple[str, str], Mapping[str, object]]:
    """Read the previous index for schedule cache reuse; absent/corrupt is empty."""
    if not index_path.is_file():
        return {}
    try:
        frame = pd.read_parquet(index_path)
    except (OSError, ValueError):
        return {}
    rows: dict[tuple[str, str], Mapping[str, object]] = {}
    for record in frame.to_dict(orient="records"):
        key = (str(record.get("archive_root")), str(record.get("archive_id")))
        rows[key] = {str(column): value for column, value in record.items()}
    return rows


def _cached_schedule_fields(
    work: _Work,
    prior_row: Mapping[str, object],
    feed_stat_size: int,
    feed_stat_mtime_ns: int,
    path: Path,
) -> dict[str, object] | None:
    """Reuse the prior row's schedule columns when nothing they depend on moved.

    Unchanged feed (size + mtime), unchanged extraction rules, and a recomputed
    fingerprint over the current match.json identity fields prove the stored
    schedule file is still the right artifact — the feed is not replayed.
    """
    meta = work.meta
    assert meta is not None
    if prior_row.get("schedule_rules_version") != EXTRACTION_RULES_VERSION:
        return None
    # Rows written before the schema column existed re-extract: their schedule
    # files may predate fields a newer schema added (e.g. kill_gates in v4).
    if opt_int(prior_row.get("schedule_schema_version")) != SCHEDULE_SCHEMA_VERSION:
        return None
    if (
        opt_int(prior_row.get("feed_size")) != feed_stat_size
        or opt_int(prior_row.get("feed_mtime_ns")) != feed_stat_mtime_ns
    ):
        return None
    feed_sha = opt_str(prior_row.get("feed_sha256"))
    stored_fingerprint = opt_str(prior_row.get("schedule_fingerprint"))
    if feed_sha is None or stored_fingerprint is None:
        return None
    fingerprint = compute_fingerprint(
        feed_sha256=feed_sha,
        meta=meta,
        rules_version=EXTRACTION_RULES_VERSION,
        event_id=work.identity.event_id,
        yes_token_index=work.identity.yes_token_index,
    )
    if fingerprint != stored_fingerprint:
        return None
    if not path.is_file():
        return None
    return {key: prior_row.get(key) for key in _SCHEDULE_ROW_KEYS}


def _extracted_schedule_fields(schedule: FeedSchedule, feed_mtime_ns: int) -> dict[str, object]:
    """The row columns a fresh extraction contributes; stats go in verbatim."""
    return {
        "schedule_fingerprint": schedule.fingerprint,
        "schedule_rules_version": schedule.rules_version,
        "schedule_schema_version": schedule.schema_version,
        "feed_sha256": schedule.feed_sha256,
        "feed_size": schedule.feed_size,
        "feed_mtime_ns": feed_mtime_ns,
        "horn_at_utc": schedule.identity.horn_at_utc,
        **asdict(schedule.stats),
    }


@dataclass(frozen=True)
class _Resolved:
    """Schedule row fields plus the extracted schedule (None on a cache hit)."""

    fields: dict[str, object]
    schedule: FeedSchedule | None
    path: Path


def _resolve_schedule(
    work: _Work,
    out_dir: Path,
    prior_row: Mapping[str, object] | None,
) -> _Resolved | None:
    """The schedule's row fields — cache hit or fresh extraction — or None.

    On extraction failure the feed and record verdicts are set to the error
    reason and nothing is published.
    """
    meta = work.meta
    assert meta is not None
    path = schedule_path_for(out_dir, work.root_label, meta.get("game") or "dota", work.archive_id)
    feed_name = FEED_FILENAMES.get(meta["feed_source"])
    feed_path = resolve_jsonl(work.archive_dir / feed_name) if feed_name is not None else None
    feed_stat = feed_path.stat() if feed_path is not None and feed_path.is_file() else None
    if prior_row is not None and feed_path is not None and feed_stat is not None:
        try:
            feed_size: int | None = decompressed_size(feed_path)
        except OSError:
            feed_size = None
        fields = (
            _cached_schedule_fields(work, prior_row, feed_size, feed_stat.st_mtime_ns, path)
            if feed_size is not None
            else None
        )
        if fields is not None:
            return _Resolved(fields=fields, schedule=None, path=path)
    try:
        schedule = extract_schedule(
            work.archive_dir,
            meta,
            event_id=work.identity.event_id,
            yes_token_index=work.identity.yes_token_index,
        )
    except ScheduleExtractionError as exc:
        if exc.reason == "orientation_failed":
            work.identity = replace(work.identity, status="orientation_failed", detail=exc.detail)
        work.feed_status = exc.reason
        work.feed_detail = exc.detail
        work.record_status = exc.reason
        work.record_detail = exc.detail
        return None
    return _Resolved(
        fields=_extracted_schedule_fields(
            schedule, feed_stat.st_mtime_ns if feed_stat is not None else 0
        ),
        schedule=schedule,
        path=path,
    )


def _set_feed_status(work: _Work, fields: Mapping[str, object]) -> None:
    """First failing feed gate, or ok when the replay can be admitted."""
    tick_count = opt_int(fields.get("tick_count")) or 0
    window_ticks = opt_int(fields.get("window_ticks")) or 0
    delay_s = opt_float(fields.get("delay_s"))
    if tick_count == 0:
        work.feed_status = "no_accepted_updates"
        work.feed_detail = "replayed the feed with zero accepted ticks"
    elif window_ticks == 0:
        work.feed_status = "no_window_updates"
        work.feed_detail = "no accepted ticks in the model window"
    elif delay_s is None:
        work.feed_status = "delay_unknown"
        work.feed_detail = "no declared or measurable feed delay"
    elif delay_s > MAX_FEED_DELAY_SECONDS:
        work.feed_status = "delay_over_limit"
        work.feed_detail = f"delay {delay_s:.1f}s > {MAX_FEED_DELAY_SECONDS}s"
    elif opt_str(fields.get("horn_at_utc")) is None:
        work.feed_status = "no_horn"
        work.feed_detail = "no tick with a positive clock"
    else:
        work.feed_status = STATUS_OK


def audit_feed_and_record(
    work: _Work,
    out_dir: Path,
    prior_row: Mapping[str, object] | None,
) -> None:
    """Extract or reuse the schedule, then set the feed and record verdicts.

    Feed gate: accepted ticks exist, some land in the model window, the
    declared-or-measured delay is inside the live picker's bound, and a tick
    has a positive clock. Record gate: the replay ends on a terminal tick.
    The schedule file publishes only when
    both verdicts pass — a truncated or unfit feed never lands in schedules/.
    """
    meta = work.meta
    assert meta is not None
    resolved = _resolve_schedule(work, out_dir, prior_row)
    if resolved is None:
        return
    work.schedule_fields = resolved.fields
    fields = resolved.fields
    _set_feed_status(work, fields)
    terminal = opt_bool(fields.get("terminal")) is True
    terminal_interrupted = opt_bool(fields.get("terminal_interrupted")) is True
    if not terminal:
        work.record_status = "no_terminal"
        work.record_detail = "feed ends without a terminal tick"
    elif terminal_interrupted:
        work.record_status = "feed_gone"
        work.record_detail = "terminal tick comes from a feed-gone marker, not a finish"
    else:
        work.record_status = STATUS_OK
    if work.feed_status == STATUS_OK and work.record_status == STATUS_OK:
        if resolved.schedule is not None:
            write_schedule(resolved.schedule, resolved.path)
        work.schedule_path = resolved.path


def admission_of(work: _Work) -> str:
    """Single admission column: admitted or the first failing stage's reason."""
    if work.meta is None:
        return f"meta:{work.meta_error or 'unreadable'}"
    if work.exclusion is not None:
        return work.exclusion
    if work.identity.status != STATUS_OK:
        return f"identity:{work.identity.status}"
    if work.feed_status != STATUS_OK:
        return f"feed:{work.feed_status}"
    if work.record_status != STATUS_OK:
        return f"record:{work.record_status}"
    return "admitted"


def _meta_columns(work: _Work) -> dict[str, object]:
    """match.json-derived row columns; empty when the document is unreadable."""
    meta = work.meta
    if meta is None:
        return {}
    market = meta["market"]
    teams = meta["teams"]
    final = meta["final"]
    return {
        "game": meta.get("game") or "dota",
        "match_id": meta["match_id"],
        "dir_name_match": work.archive_id == meta["match_id"],
        "feed_source": meta["feed_source"],
        "schema_version": meta["schema_version"],
        "steam_match_id": meta["steam_match_id"],
        "map_number": meta["map_number"],
        "joined_at_utc": meta["joined_at_utc"],
        "joined_at_second": meta["joined_at_second"],
        "condition_id": market["condition_id"],
        "market_slug": market["market_slug"],
        "event_slug": market["event_slug"],
        "yes_token_id": market["yes_token_id"],
        "no_token_id": market["no_token_id"],
        "yes_is_radiant": market["yes_is_radiant"],
        "outcome_0_name": market["outcome_0_name"],
        "outcome_1_name": market["outcome_1_name"],
        "team_radiant": teams["radiant"],
        "team_dire": teams["dire"],
        "has_final": final is not None,
        "winner": final["winner"] if final is not None else None,
        "duration_seconds": final["duration_seconds"] if final is not None else None,
    }


def _identity_columns(identity: IdentityAudit) -> dict[str, object]:
    """Universe-derived row columns plus the identity verdict."""
    return {
        "event_id": identity.event_id,
        "contract_kind": identity.contract_kind,
        "universe_game_number": identity.universe_game_number,
        "universe_team_a": identity.universe_team_a,
        "universe_team_b": identity.universe_team_b,
        "yes_token_index": identity.yes_token_index,
        "identity_status": identity.status,
        "identity_detail": identity.detail,
    }


def _to_row(work: _Work) -> dict[str, object]:
    """Flatten the per-archive build state into one index.parquet row."""
    return {
        "archive_root": work.root_label,
        "archive_id": work.archive_id,
        "meta_ok": work.meta is not None,
        "meta_error": work.meta_error,
        "match_json_sha256": work.match_json_sha256,
        **_meta_columns(work),
        **_identity_columns(work.identity),
        "feed_status": work.feed_status,
        "feed_detail": work.feed_detail,
        "record_status": work.record_status,
        "record_detail": work.record_detail,
        "admission": admission_of(work),
        "schedule_published": work.schedule_path is not None,
        "schedule_path": str(work.schedule_path) if work.schedule_path is not None else None,
        **(work.schedule_fields or {}),
    }


def _unpublish(work: _Work, out_dir: Path) -> None:
    """Delete a previously published schedule for an archive that is not admitted.

    An archive that passed an older rules version and now fails — or whose feed
    corrupted since — must not leave a file a consumer could mistake for valid.
    """
    if work.meta is None:
        games = list(GAME_PROFILES)
    else:
        games = [work.meta.get("game") or "dota"]
    for game in games:
        schedule_path_for(out_dir, work.root_label, game, work.archive_id).unlink(missing_ok=True)


def _audit_one(work: _Work, out_dir: Path, prior_row: Mapping[str, object] | None) -> _Work:
    """Pool worker: run the feed/record audit for one archive; returns a copy."""
    audit_feed_and_record(work, out_dir, prior_row)
    return work


def build_index(roots: Mapping[str, Path], out_dir: Path, jobs: int) -> IndexResult:
    """Scan every root, audit all archives, write index.parquet and schedules."""
    prior_rows = _prior_row_map(out_dir / INDEX_FILENAME)
    works: list[_Work] = []
    dirs_without_meta = 0
    for label, root in roots.items():
        scanned, skipped = scan_root(root, label)
        works.extend(scanned)
        dirs_without_meta += skipped
    games = {w.meta.get("game") or "dota" for w in works if w.meta is not None}
    universes = {game: load_universe(game) for game in sorted(games) if game in GAME_PROFILES}
    for work in works:
        if work.meta is None:
            continue
        universe = universes.get(work.meta.get("game") or "dota", {})
        work.identity = audit_identity(
            work.meta, universe.get(work.meta["market"]["condition_id"].lower())
        )
    resolve_duplicates(works)
    targets = [
        work
        for work in works
        if work.meta is not None and work.exclusion is None and work.identity.status == STATUS_OK
    ]
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            audited = list(
                pool.map(
                    _audit_one,
                    targets,
                    [out_dir] * len(targets),
                    [prior_rows.get((w.root_label, w.archive_id)) for w in targets],
                )
            )
        audited_by_key = {(w.root_label, w.archive_id): w for w in audited}
        works = [audited_by_key.get((w.root_label, w.archive_id), w) for w in works]
        targets = audited
    else:
        for work in targets:
            audit_feed_and_record(work, out_dir, prior_rows.get((work.root_label, work.archive_id)))
    for work in targets:
        logger.info("index %s/%s -> %s", work.root_label, work.archive_id, admission_of(work))
    for work in works:
        if work.schedule_path is None:
            _unpublish(work, out_dir)
    frame = pd.DataFrame([_to_row(work) for work in works])
    write_parquet(frame, out_dir / INDEX_FILENAME)
    summary = IndexSummary(
        dirs_scanned=len(works) + dirs_without_meta,
        dirs_without_meta=dirs_without_meta,
        meta_ok=sum(1 for work in works if work.meta is not None),
        meta_unreadable=sum(1 for work in works if work.meta is None),
        identity_ok=sum(1 for work in works if work.identity.status == STATUS_OK),
        feed_ok=sum(1 for work in works if work.feed_status == STATUS_OK),
        record_ok=sum(1 for work in works if work.record_status == STATUS_OK),
        admitted=sum(1 for work in works if admission_of(work) == "admitted"),
        schedules_published=sum(1 for work in works if work.schedule_path is not None),
        unique_match_ids=len({w.meta["match_id"] for w in works if w.meta is not None}),
        unique_condition_ids=len(
            {w.meta["market"]["condition_id"].lower() for w in works if w.meta is not None}
        ),
    )
    return IndexResult(frame=frame, summary=summary)


def run_index(roots: Mapping[str, Path], out_dir: Path, jobs: int) -> IndexSummary:
    """Build the index, publish schedules, write the report; return the summary."""
    result = build_index(roots, out_dir, jobs)
    write_report(out_dir, format_report(roots, out_dir, result.frame, result.summary))
    return result.summary
