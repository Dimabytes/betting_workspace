import json
import math
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dashboard import game_grid, game_oddin
from dashboard.catalog import ArchiveEntry
from dashboard.game_types import (
    BoardSlice,
    Comparison,
    Continuity,
    DecisionSlice,
    GameIdentity,
    GameSummary,
    Provenance,
    SourceFacts,
    TableSlice,
    TopSummary,
    XpStatus,
)
from dashboard.tails import RecordTail, TailCache
from shared.utils.json_read import as_map
from trader.game_profile import GAME_PROFILES, GameProfile
from trader.paths import (
    GRID_STATE_ARCHIVE_FILENAME,
    MATCH_META_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
    SESSION_JOURNAL_FILENAME,
)
from viewer.archive_read import as_float, as_int, as_str, market_block, read_match_document

DECISION_LABEL = "игровой снимок решения"
ARCHIVE_LABEL = "данные игры"
XP_NOT_USED_TEXT = "не используется этой моделью"
STATE_LIMIT = 64
SEEN_LIMIT = 128
NOTES_LIMIT = 16

FEED_FILES = {
    "grid": GRID_STATE_ARCHIVE_FILENAME,
    "oddin": ODDIN_STATE_ARCHIVE_FILENAME,
}


@dataclass(frozen=True)
class _MetaEntry:
    mtime_ns: int
    size: int
    doc: dict[str, object] | None


@dataclass
class _MapState:
    key: tuple[object, ...]
    last_seen_s: float = 0.0
    continuity: Continuity | None = None
    seen: deque[str] = field(default_factory=lambda: deque[str](maxlen=SEEN_LIMIT))
    feed_key: tuple[str, int, int] | None = None
    grid: game_grid.GridFold | None = None
    oddin: game_oddin.OddinFold | None = None
    cached_key: tuple[object, ...] | None = None
    cached: GameSummary | None = None


@dataclass(frozen=True)
class _Inputs:
    signal_source: str | None
    meta_source: str | None
    source: str | None
    feed_file: str | None
    game: str | None
    map_number: int | None
    profile: GameProfile


def _whole(value: object) -> int | None:
    number = as_float(value)
    if number is None or not math.isfinite(number) or number != int(number):
        return None
    return int(number)


def _finite(value: object) -> float | None:
    number = as_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def _stamp(tail: RecordTail | None) -> tuple[int, int] | None:
    if tail is None:
        return None
    return (tail.mtime_ns, tail.size)


def _last_signal(tail: RecordTail | None) -> Mapping[str, object] | None:
    if tail is None:
        return None
    for record in reversed(tail.records):
        if record.get("kind") == "signal":
            return record
    return None


def _fingerprint(record: Mapping[str, object]) -> str:
    try:
        return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return repr(record)


def _note(notes: list[str], text: str) -> None:
    if len(notes) < NOTES_LIMIT:
        notes.append(text)


def _snap_whole(snap: Mapping[str, object], name: str, notes: list[str]) -> int | None:
    if name not in snap:
        _note(notes, f"game_snapshot.{name} missing")
        return None
    value = _whole(snap.get(name))
    if value is None:
        _note(notes, f"game_snapshot.{name} invalid")
    return value


def _snap_float(snap: Mapping[str, object], name: str, notes: list[str]) -> float | None:
    if name not in snap:
        _note(notes, f"game_snapshot.{name} missing")
        return None
    value = _finite(snap.get(name))
    if value is None:
        _note(notes, f"game_snapshot.{name} invalid")
    return value


def _snap_top(snap: Mapping[str, object], notes: list[str]) -> TopSummary | None:
    raw = snap.get("top")
    top = as_map(raw)
    if top is None:
        _note(notes, "game_snapshot.top missing" if raw is None else "game_snapshot.top invalid")
        return None
    return TopSummary(
        top1_nw_adv=_snap_whole(top, "top1_nw_adv", notes),
        radiant_top1_nw_ratio=_snap_float(top, "radiant_top1_nw_ratio", notes),
        dire_top1_nw_ratio=_snap_float(top, "dire_top1_nw_ratio", notes),
        top3_nw_adv=_snap_whole(top, "top3_nw_adv", notes),
        radiant_top3_nw_ratio=_snap_float(top, "radiant_top3_nw_ratio", notes),
        dire_top3_nw_ratio=_snap_float(top, "dire_top3_nw_ratio", notes),
    )


def _xp_fields(
    source: str | None, snap: Mapping[str, object] | None, notes: list[str]
) -> tuple[XpStatus, int | None, str | None]:
    if snap is None:
        return "unknown", None, None
    if "radiant_xp_adv" in snap:
        xp_raw = _whole(snap.get("radiant_xp_adv"))
        if xp_raw is None:
            _note(notes, "game_snapshot.radiant_xp_adv invalid")
    else:
        xp_raw = None
    if source == "oddin":
        return "not_used", None, XP_NOT_USED_TEXT
    if "radiant_xp_adv" not in snap:
        return "unknown", None, None
    if xp_raw is None:
        return "unknown", None, None
    return "available", xp_raw, None


def _decision_slice(
    tail: RecordTail | None, journal_path: Path, source: str | None
) -> DecisionSlice:
    notes: list[str] = []
    record = _last_signal(tail)
    if record is None:
        if tail is not None:
            _note(notes, "no signal in journal tail")
        return DecisionSlice(
            provenance="none",
            second=None,
            server_timestamp=None,
            phase=None,
            paused=None,
            radiant_nw=None,
            dire_nw=None,
            radiant_nw_adv=None,
            xp_status="unknown",
            radiant_xp_adv=None,
            xp_text=None,
            deaths_radiant=None,
            deaths_dire=None,
            top=None,
            model_evaluated=None,
            raw_delta=None,
            market_radiant_prior=None,
            reason=None,
            entry_block=None,
            feed_source=None,
            feed_received_at_utc=None,
            recorded_at_utc=None,
            journal_path=journal_path,
            notes=tuple(notes),
        )
    snap_raw = record.get("game_snapshot")
    snap = as_map(snap_raw)
    if snap is None:
        provenance: Provenance = "legacy"
        _note(
            notes,
            "missing game_snapshot" if snap_raw is None else "invalid game_snapshot",
        )
        second = _whole(record.get("second"))
        server_timestamp = phase = paused = None
        radiant_nw = dire_nw = radiant_nw_adv = None
        deaths_radiant = deaths_dire = None
        top = None
        xp_status: XpStatus = "unknown"
        xp_value = xp_text = None
    else:
        provenance = "signal"
        second = _snap_whole(snap, "second", notes)
        server_timestamp = _snap_whole(snap, "server_timestamp", notes)
        phase_raw = snap.get("phase")
        phase = as_str(phase_raw)
        if phase is None:
            _note(
                notes,
                "game_snapshot.phase missing"
                if "phase" not in snap
                else "game_snapshot.phase invalid",
            )
        paused_raw = snap.get("paused")
        paused = paused_raw if isinstance(paused_raw, bool) else None
        if paused is None:
            _note(
                notes,
                "game_snapshot.paused missing"
                if "paused" not in snap
                else "game_snapshot.paused invalid",
            )
        radiant_nw = _snap_whole(snap, "radiant_nw", notes)
        dire_nw = _snap_whole(snap, "dire_nw", notes)
        radiant_nw_adv = _snap_whole(snap, "radiant_nw_adv", notes)
        deaths_radiant = _snap_whole(snap, "deaths_radiant", notes)
        deaths_dire = _snap_whole(snap, "deaths_dire", notes)
        top = _snap_top(snap, notes)
        xp_status, xp_value, xp_text = _xp_fields(source, snap, notes)
    me_raw = record.get("model_evaluated")
    model_evaluated = me_raw if isinstance(me_raw, bool) else None
    if me_raw is not None and model_evaluated is None:
        _note(notes, "model_evaluated invalid")
    raw_delta: float | None = None
    if model_evaluated is True:
        raw_delta = _finite(record.get("raw_delta"))
        if raw_delta is None:
            _note(notes, "model_evaluated without valid raw_delta")
    return DecisionSlice(
        provenance=provenance,
        second=second,
        server_timestamp=server_timestamp,
        phase=phase,
        paused=paused,
        radiant_nw=radiant_nw,
        dire_nw=dire_nw,
        radiant_nw_adv=radiant_nw_adv,
        xp_status=xp_status,
        radiant_xp_adv=xp_value,
        xp_text=xp_text,
        deaths_radiant=deaths_radiant,
        deaths_dire=deaths_dire,
        top=top,
        model_evaluated=model_evaluated,
        raw_delta=raw_delta,
        market_radiant_prior=_finite(record.get("market_radiant_prior")),
        reason=as_str(record.get("reason")),
        entry_block=as_str(record.get("entry_block")),
        feed_source=as_str(record.get("feed_source")),
        feed_received_at_utc=as_str(record.get("feed_received_at_utc")),
        recorded_at_utc=as_str(record.get("recorded_at_utc")),
        journal_path=journal_path,
        notes=tuple(notes),
    )


def _acceptance_notes(continuity: Continuity | None) -> tuple[str, ...]:
    if continuity == "warm":
        return ()
    return ("archive reconstruction; acceptance unknown",)


def _compare(decision: DecisionSlice, table: TableSlice | None) -> Comparison:
    if decision.provenance != "signal" or table is None:
        return "unknown"
    if decision.feed_received_at_utc is None or table.received_at_utc is None:
        return "unknown"
    if decision.feed_received_at_utc != table.received_at_utc:
        return "different"
    pairs = (
        (decision.second, table.second),
        (decision.radiant_nw, table.side_0.players_gold),
        (decision.dire_nw, table.side_1.players_gold),
        (decision.deaths_radiant, table.side_0.players_deaths),
        (decision.deaths_dire, table.side_1.players_deaths),
    )
    if any(left is None or right is None for left, right in pairs):
        return "unknown"
    return "aligned" if all(left == right for left, right in pairs) else "different"


def _block_str(block: dict[str, object] | None, key: str) -> str | None:
    return as_str((block or {}).get(key))


def _profile(game: str | None) -> GameProfile:
    return GAME_PROFILES.get(game or "dota", GAME_PROFILES["dota"])


def _resolve_inputs(
    entry: ArchiveEntry,
    meta: dict[str, object] | None,
    signal: Mapping[str, object] | None,
) -> _Inputs:
    signal_source = as_str(signal.get("feed_source")) if signal is not None else None
    meta_source = _block_str(meta, "feed_source")
    source = signal_source or meta_source
    game = entry.game or _block_str(meta, "game")
    map_number = entry.map_number
    if map_number is None and meta is not None:
        map_number = as_int(meta.get("map_number"))
    return _Inputs(
        signal_source=signal_source,
        meta_source=meta_source,
        source=source,
        feed_file=FEED_FILES.get(source or ""),
        game=game,
        map_number=map_number,
        profile=_profile(game),
    )


def _tail_notes(label: str, tail: RecordTail) -> tuple[str, ...]:
    notes: list[str] = []
    if tail.truncated_start:
        notes.append(f"{label} tail truncated")
    if tail.malformed:
        notes.append(f"{label} malformed {tail.malformed}")
    if tail.read_error:
        notes.append(f"{label} read error")
    return tuple(notes)


def _source_note(source: str | None) -> str:
    if source is None:
        return "feed source unknown"
    return f"unsupported feed source {source!r}"


def _feed_complete(tail: RecordTail | None) -> bool:
    return (
        tail is not None and not tail.truncated_start and not tail.malformed and not tail.read_error
    )


def _feed_path(archive: Path, feed_file: str | None, tail: RecordTail | None) -> Path | None:
    if tail is not None:
        return tail.path
    if feed_file is not None:
        return archive / feed_file
    return None


def _yes_side_0(entry: ArchiveEntry, market: dict[str, object] | None) -> bool | None:
    yes_is_side_0 = entry.yes_is_radiant
    if yes_is_side_0 is None and market is not None:
        flag = market.get("yes_is_radiant")
        if isinstance(flag, bool):
            yes_is_side_0 = flag
    return yes_is_side_0


def _identity(
    entry: ArchiveEntry,
    inputs: _Inputs,
    meta: dict[str, object] | None,
    name_0: str | None,
    name_1: str | None,
) -> GameIdentity:
    market = market_block(meta) if meta is not None else None
    return GameIdentity(
        archive_dir=entry.archive_dir,
        match_id=entry.match_id,
        condition_id=entry.condition_id,
        game=inputs.game,
        map_number=inputs.map_number,
        source=inputs.source,
        side_0_label=inputs.profile.side_0_text.capitalize(),
        side_1_label=inputs.profile.side_1_text.capitalize(),
        team_0=entry.radiant or name_0,
        team_1=entry.dire or name_1,
        yes_is_side_0=_yes_side_0(entry, market),
        outcome_0_name=entry.outcome_0_name or _block_str(market, "outcome_0_name"),
        outcome_1_name=entry.outcome_1_name or _block_str(market, "outcome_1_name"),
    )


def build_game_summary(
    *,
    identity: GameIdentity,
    decision: DecisionSlice,
    board: BoardSlice | None,
    table: TableSlice | None,
    continuity: Continuity | None,
    feed_complete: bool,
    evidence: tuple[str, ...],
    control: tuple[str, ...],
    rejected: int,
    terminal: bool,
) -> GameSummary:
    return GameSummary(
        identity=identity,
        decision=decision,
        board=board,
        table=table,
        source=SourceFacts(
            continuity=continuity,
            complete=feed_complete,
            evidence=evidence,
            control=control,
            rejected=rejected,
            terminal=terminal,
            legacy_replay=False,
            comparison=_compare(decision, table),
        ),
        decision_label=DECISION_LABEL,
        archive_label=ARCHIVE_LABEL,
    )


class GameStateReader:
    def __init__(self, tails: TailCache, *, max_states: int = STATE_LIMIT) -> None:
        self._tails = tails
        self._max_states = max_states
        self._lock = threading.Lock()
        self._states: dict[tuple[object, ...], _MapState] = {}
        self._metas: dict[Path, _MetaEntry] = {}

    def read(self, entry: ArchiveEntry, now_s: float) -> GameSummary:
        with self._lock:
            return self._read(entry, now_s)

    def _meta(self, archive: Path) -> tuple[dict[str, object] | None, tuple[int, int] | None]:
        path = archive / MATCH_META_FILENAME
        try:
            stat = path.stat()
        except OSError:
            self._metas.pop(archive, None)
            return None, None
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._metas.get(archive)
        if cached is not None and (cached.mtime_ns, cached.size) == stamp:
            return cached.doc, stamp
        doc = read_match_document(archive)
        self._metas[archive] = _MetaEntry(stat.st_mtime_ns, stat.st_size, doc)
        while len(self._metas) > self._max_states:
            stale = next(key for key in self._metas if key != archive)
            del self._metas[stale]
        return doc, stamp

    def _meta_notes(
        self, entry: ArchiveEntry, meta: Mapping[str, object] | None
    ) -> tuple[str, ...]:
        if meta is None:
            return ()
        notes: list[str] = []
        match_id = as_str(meta.get("match_id"))
        if match_id is not None and match_id != entry.match_id:
            notes.append("match.json match_id differs")
        map_number = as_int(meta.get("map_number"))
        if (
            map_number is not None
            and entry.map_number is not None
            and map_number != entry.map_number
        ):
            notes.append("match.json map_number differs")
        game = as_str(meta.get("game"))
        if game is not None and entry.game is not None and game != entry.game:
            notes.append("match.json game differs")
        return tuple(notes)

    def _evict(self) -> None:
        while len(self._states) > self._max_states:
            oldest = min(self._states, key=lambda key: self._states[key].last_seen_s)
            del self._states[oldest]

    def _apply(
        self,
        state: _MapState,
        record: Mapping[str, object],
        *,
        source: str,
        entry: ArchiveEntry,
        profile: GameProfile,
        map_number: int,
    ) -> None:
        if source == "grid":
            if state.grid is None:
                state.grid = game_grid.GridFold(
                    map_number=map_number,
                    outcome_0_name=entry.outcome_0_name,
                    outcome_1_name=entry.outcome_1_name,
                    profile=profile,
                )
            state.grid.apply(record)
        elif source == "oddin":
            if state.oddin is None:
                state.oddin = game_oddin.OddinFold(
                    map_number=map_number,
                    yes_is_radiant=entry.yes_is_radiant is True,
                )
            state.oddin.apply(record)

    def _fold(
        self,
        state: _MapState,
        tail: RecordTail,
        *,
        source: str,
        entry: ArchiveEntry,
        profile: GameProfile,
        map_number: int,
    ) -> None:
        if not tail.records:
            state.feed_key = (str(tail.path), tail.mtime_ns, tail.size)
            return
        fps = [_fingerprint(record) for record in tail.records]
        overlap: int | None = None
        for index in range(len(fps) - 1, -1, -1):
            if fps[index] in state.seen:
                overlap = index
                break
        replaced = state.feed_key is not None and (
            tail.size < state.feed_key[2] or str(tail.path) != state.feed_key[0]
        )
        if overlap is not None and not replaced:
            start = overlap + 1
            state.continuity = "warm"
        elif state.seen or replaced:
            state.seen.clear()
            state.continuity = "reconstructed"
            start = 0
        else:
            start = 0
            if state.continuity is None:
                state.continuity = "cold"
        for index in range(start, len(tail.records)):
            self._apply(
                state,
                tail.records[index],
                source=source,
                entry=entry,
                profile=profile,
                map_number=map_number,
            )
        state.seen.extend(fps[start:])
        state.feed_key = (str(tail.path), tail.mtime_ns, tail.size)

    def _map_state(
        self, entry: ArchiveEntry, archive: Path, inputs: _Inputs, now_s: float
    ) -> _MapState:
        key = (
            str(archive),
            inputs.source or "-",
            inputs.game or "-",
            inputs.map_number or 0,
            entry.yes_is_radiant,
            entry.outcome_0_name or "-",
            entry.outcome_1_name or "-",
        )
        state = self._states.get(key)
        if state is None:
            state = _MapState(key=key)
            self._states[key] = state
        state.last_seen_s = now_s
        return state

    def _evidence(
        self,
        entry: ArchiveEntry,
        inputs: _Inputs,
        meta: dict[str, object] | None,
        journal_tail: RecordTail | None,
        feed_tail: RecordTail | None,
    ) -> list[str]:
        notes = list(self._meta_notes(entry, meta))
        if (
            inputs.signal_source is not None
            and inputs.meta_source is not None
            and inputs.signal_source != inputs.meta_source
        ):
            notes.append("signal feed_source conflicts match.json")
        if journal_tail is None:
            notes.append("journal unavailable")
        else:
            notes.extend(_tail_notes("journal", journal_tail))
        if inputs.feed_file is None:
            notes.append(_source_note(inputs.source))
        elif inputs.map_number is None:
            notes.append("map number unknown")
        elif feed_tail is None:
            notes.append("feed archive unavailable")
        else:
            notes.extend(_tail_notes("feed", feed_tail))
        return notes

    def _fold_fresh(
        self,
        state: _MapState,
        tail: RecordTail,
        inputs: _Inputs,
        entry: ArchiveEntry,
        map_number: int,
    ) -> None:
        tail_key = (str(tail.path), tail.mtime_ns, tail.size)
        if tail_key == state.feed_key:
            return
        self._fold(
            state,
            tail,
            source=inputs.source or "",
            entry=entry,
            profile=inputs.profile,
            map_number=map_number,
        )

    def _fold_slices(
        self,
        fold: game_grid.GridFold | game_oddin.OddinFold | None,
        feed_path: Path | None,
        continuity: Continuity | None,
    ) -> tuple[BoardSlice | None, TableSlice | None]:
        if fold is None:
            return None, None
        board = fold.board
        if board is not None:
            board = replace(board, archive_path=feed_path)
        table = fold.table
        if table is not None:
            table = replace(
                table,
                archive_path=feed_path,
                notes=_acceptance_notes(continuity),
            )
        return board, table

    def _read(self, entry: ArchiveEntry, now_s: float) -> GameSummary:
        archive = entry.archive_dir
        journal_tail = self._tails.read_records(archive / SESSION_JOURNAL_FILENAME)
        meta, meta_stamp = self._meta(archive)
        inputs = _resolve_inputs(entry, meta, _last_signal(journal_tail))
        state = self._map_state(entry, archive, inputs, now_s)
        feed_tail = (
            self._tails.read_records(archive / inputs.feed_file)
            if inputs.feed_file is not None and inputs.map_number is not None
            else None
        )
        input_key = (
            _stamp(journal_tail),
            _stamp(feed_tail),
            meta_stamp,
            entry.match_id,
            entry.condition_id,
            entry.radiant,
            entry.dire,
        )
        if state.cached_key == input_key and state.cached is not None:
            return state.cached
        evidence = self._evidence(entry, inputs, meta, journal_tail, feed_tail)
        if feed_tail is not None and inputs.map_number is not None:
            self._fold_fresh(state, feed_tail, inputs, entry, inputs.map_number)
        fold = state.grid or state.oddin
        if (
            isinstance(fold, game_grid.GridFold)
            and fold.orientation is not None
            and fold.orientation != entry.yes_is_radiant
        ):
            evidence.append("board orientation conflicts saved yes_is_radiant")
        feed_path = _feed_path(archive, inputs.feed_file, feed_tail)
        board, table = self._fold_slices(fold, feed_path, state.continuity)
        decision = _decision_slice(journal_tail, archive / SESSION_JOURNAL_FILENAME, inputs.source)
        summary = build_game_summary(
            identity=_identity(
                entry,
                inputs,
                meta,
                fold.team_0_name if fold is not None else None,
                fold.team_1_name if fold is not None else None,
            ),
            decision=decision,
            board=board,
            table=table,
            continuity=state.continuity,
            feed_complete=_feed_complete(feed_tail),
            evidence=tuple(evidence[:NOTES_LIMIT]),
            control=fold.control if fold is not None else (),
            rejected=fold.rejected if fold is not None else 0,
            terminal=fold.terminal if fold is not None else False,
        )
        state.cached_key = input_key
        state.cached = summary
        self._evict()
        return summary
