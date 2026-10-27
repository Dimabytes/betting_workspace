"""Backfill final.winner on finished Oddin Dota maps that recorded null.

Run from the esports-trader checkout that already has commit 7506bf9e:

    PYTHONPATH=src uv run python <this file> data/trader_live          # dry run
    PYTHONPATH=src uv run python <this file> data/trader_live --write  # write

Winner sources, in order:
1. replay: oddin_state.jsonl through the current reducer (previousMaps, currentMap.won, catalogScore).
2. next_map: previousMaps of the next map's archive in the same Oddin series.

A winner that disagrees with the last book mid in session.jsonl is listed, not written.
--write replaces only final.winner, and only while it is still null.
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from shared.utils.jsonl_io import open_maybe_gz, resolve_jsonl
from trader.match_meta import _atomic_write_json, read_match_meta
from trader.oddin_archive import iter_oddin_archive_records, summarize
from trader.oddin_feed import winner_from_previous_maps
from trader.paths import MATCH_META_FILENAME, ODDIN_STATE_ARCHIVE_FILENAME


@dataclass(frozen=True)
class Candidate:
    archive_dir: Path
    match_id: str
    map_number: int
    joined_at_utc: str
    oddin_match_id: str | None
    yes_is_radiant: bool


@dataclass(frozen=True)
class Verdict:
    candidate: Candidate
    winner: str | None
    source: str
    book_p_radiant: float | None
    action: str


def load_oddin_dota_archives(root: Path) -> list[tuple[Path, dict[str, object]]]:
    archives = []
    for meta_path in sorted(root.glob(f"*/{MATCH_META_FILENAME}")):
        document = json.loads(meta_path.read_text(encoding="utf-8"))
        if document.get("feed_source") != "oddin":
            continue
        if document.get("game", "dota") != "dota":
            continue
        archives.append((meta_path.parent, document))
    return archives


def index_by_series_map(
    archives: list[tuple[Path, dict[str, object]]],
) -> dict[tuple[str, int], Path]:
    index: dict[tuple[str, int], Path] = {}
    for archive_dir, document in archives:
        oddin_match_id = document.get("oddin_match_id")
        map_number = document.get("map_number")
        if isinstance(oddin_match_id, str) and isinstance(map_number, int):
            index[(oddin_match_id, map_number)] = archive_dir
    return index


def select_candidates(archives: list[tuple[Path, dict[str, object]]]) -> list[Candidate]:
    candidates = []
    for archive_dir, document in archives:
        final = document.get("final")
        if not isinstance(final, dict) or final.get("winner") is not None:
            continue
        market = document["market"]
        assert isinstance(market, dict)
        oddin_match_id = document.get("oddin_match_id")
        candidates.append(
            Candidate(
                archive_dir=archive_dir,
                match_id=str(document["match_id"]),
                map_number=int(document["map_number"]),
                joined_at_utc=str(document.get("joined_at_utc") or ""),
                oddin_match_id=oddin_match_id if isinstance(oddin_match_id, str) else None,
                yes_is_radiant=bool(market["yes_is_radiant"]),
            )
        )
    return candidates


def replay_winner(candidate: Candidate) -> str | None:
    document = read_match_meta(candidate.archive_dir / MATCH_META_FILENAME)
    return summarize(candidate.archive_dir, document).summary.winner


def next_map_winner(candidate: Candidate, index: dict[tuple[str, int], Path]) -> str | None:
    if candidate.oddin_match_id is None:
        return None
    sibling = index.get((candidate.oddin_match_id, candidate.map_number + 1))
    if sibling is None:
        return None
    archive_path = resolve_jsonl(sibling / ODDIN_STATE_ARCHIVE_FILENAME)
    if not archive_path.exists():
        return None
    for record in iter_oddin_archive_records(archive_path):
        payload = record["payload"]
        if not isinstance(payload, dict):
            continue
        previous = payload.get("previousMaps")
        if not isinstance(previous, list):
            continue
        entries = [item for item in previous if isinstance(item, dict)]
        winner = winner_from_previous_maps(entries, candidate.map_number, None)
        if winner is not None:
            return winner
    return None


def read_last_book_p_radiant(candidate: Candidate) -> float | None:
    session_path = resolve_jsonl(candidate.archive_dir / "session.jsonl")
    if not session_path.exists():
        return None
    last_yes_mid: float | None = None
    with open_maybe_gz(session_path) as handle:
        for line in handle:
            if '"kind":"signal"' not in line:
                continue
            row = json.loads(line)
            bid = row.get("yes_best_bid")
            ask = row.get("yes_best_ask")
            if bid and ask:
                last_yes_mid = (bid + ask) / 2
    if last_yes_mid is None:
        return None
    return last_yes_mid if candidate.yes_is_radiant else 1.0 - last_yes_mid


def judge(candidate: Candidate, index: dict[tuple[str, int], Path]) -> Verdict:
    source = "none"
    try:
        winner = replay_winner(candidate)
        if winner is not None:
            source = "replay"
    except ValueError as exc:
        winner = None
        source = f"replay_error:{exc}"[:60]
    if winner is None:
        winner = next_map_winner(candidate, index)
        if winner is not None:
            source = "next_map"
    book_p = read_last_book_p_radiant(candidate)
    if winner is None:
        action = "unresolved"
    elif book_p is not None and (book_p > 0.5) != (winner == "radiant"):
        action = "skip_book_disagrees"
    else:
        action = "write"
    return Verdict(candidate, winner, source, book_p, action)


def write_winner(verdict: Verdict) -> str:
    archive_dir = verdict.candidate.archive_dir
    document = read_match_meta(archive_dir / MATCH_META_FILENAME)
    final = document["final"]
    if final is None or final["winner"] is not None:
        return "skip_changed"
    updated = dict(document)
    updated["final"] = {**final, "winner": verdict.winner}
    _atomic_write_json(archive_dir, updated)  # type: ignore[arg-type]
    return "written"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    archives = load_oddin_dota_archives(args.root)
    index = index_by_series_map(archives)
    verdicts = [judge(candidate, index) for candidate in select_candidates(archives)]
    for verdict in sorted(verdicts, key=lambda item: item.candidate.joined_at_utc):
        outcome = verdict.action
        if args.write and verdict.action == "write":
            outcome = write_winner(verdict)
        book = "-" if verdict.book_p_radiant is None else f"{verdict.book_p_radiant:.3f}"
        print(
            f"{verdict.candidate.match_id:<18} map={verdict.candidate.map_number} "
            f"joined={verdict.candidate.joined_at_utc[:19]} winner={verdict.winner} "
            f"source={verdict.source} book_p_radiant={book} -> {outcome}"
        )
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.action] = counts.get(verdict.action, 0) + 1
    mode = "WRITE" if args.write else "DRY RUN"
    print(f"{mode}: {len(verdicts)} maps with final.winner=null; {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
