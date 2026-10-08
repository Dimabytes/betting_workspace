"""Reconstruct GRID-style LoL net worth from details inventories and pinned prices."""

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from lol.constants import LOL_CONSUMED_ABSENCE_SECONDS


@dataclass(frozen=True)
class MajorMinor:
    """First two dotted version tokens, compared as integers not prefixes."""

    major: int
    minor: int


@dataclass(frozen=True)
class ItemTable:
    """Pinned item IDs and consumed-item total-gold prices."""

    version: str
    known_item_ids: frozenset[int]
    consumed_item_gold: Mapping[int, int]


@dataclass(frozen=True)
class ItemCatalog:
    """Compact Data Dragon tables keyed by ddragon version string."""

    tables: Mapping[str, ItemTable]


@dataclass(frozen=True)
class ConsumedFrame:
    """Cumulative consumed gold by participant at one details timestamp."""

    by_participant: Mapping[int, int]


@dataclass(frozen=True)
class ConsumedTimeline:
    """Exact details timestamp to cumulative consumed gold."""

    by_stamp: Mapping[str, ConsumedFrame]


@dataclass(frozen=True)
class PendingAbsence:
    """Participant and item whose consumed-count drop is not yet spent."""

    participant_id: int
    item_id: int


DEFAULT_ITEM_CATALOG_DIR = Path(__file__).with_name("ddragon_items")
ZERO_CONSUMED = ConsumedFrame({participant_id: 0 for participant_id in range(1, 11)})


def parse_major_minor(version: str) -> MajorMinor:
    """Take the first two dotted tokens of a Riot client or Data Dragon version."""
    parts = version.split(".")
    if len(parts) < 2:
        raise ValueError(f"version has no major.minor: {version}")
    try:
        major = int(parts[0])
        minor = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"version is not numeric major.minor: {version}") from exc
    return MajorMinor(major, minor)


def compact_ddragon_items(payload: Mapping[str, object]) -> ItemTable:
    """Keep every item id and the total gold of items with consumed is True."""
    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("ddragon item payload has no version")
    raw_data = payload.get("data")
    if not isinstance(raw_data, dict):
        raise ValueError("ddragon item payload has no data")
    known_values: list[int] = []
    consumed_item_gold: dict[int, int] = {}
    for raw_item_id, raw_item in cast(dict[str, object], raw_data).items():
        item_id = int(raw_item_id)
        if not isinstance(raw_item, dict):
            raise ValueError(f"ddragon item {item_id} is not an object")
        item = cast(dict[str, object], raw_item)
        known_values.append(item_id)
        if item.get("consumed") is not True:
            continue
        gold = item.get("gold")
        if not isinstance(gold, dict):
            raise ValueError(f"consumed item {item_id} has no gold")
        total = cast(dict[str, object], gold).get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise ValueError(f"consumed item {item_id} has invalid gold.total")
        consumed_item_gold[item_id] = total
    known_item_ids = frozenset(known_values)
    if len(known_item_ids) != len(known_values):
        raise ValueError("ddragon item payload has duplicate item ids")
    return ItemTable(version, known_item_ids, consumed_item_gold)


def load_item_table(path: Path) -> ItemTable:
    """Load and validate one compact Data Dragon item table."""
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"item table is not an object: {path}")
    body = cast(dict[str, object], payload)
    version = body.get("version")
    raw_known = body.get("known_item_ids")
    raw_consumed = body.get("consumed_item_gold")
    if not isinstance(version, str) or not version:
        raise ValueError(f"item table has no version: {path}")
    if not isinstance(raw_known, list) or not isinstance(raw_consumed, dict):
        raise ValueError(f"item table has invalid collections: {path}")

    known_values: list[int] = []
    for value in cast(list[object], raw_known):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"item table has invalid item id: {value!r}")
        known_values.append(value)
    known_item_ids = frozenset(known_values)
    if len(known_item_ids) != len(known_values):
        raise ValueError("item table has duplicate item ids")

    consumed_item_gold: dict[int, int] = {}
    for raw_item_id, raw_price in cast(dict[str, object], raw_consumed).items():
        item_id = int(raw_item_id)
        if isinstance(raw_price, bool) or not isinstance(raw_price, int) or raw_price < 0:
            raise ValueError(f"item table has invalid price for {item_id}")
        if item_id not in known_item_ids:
            raise ValueError(f"consumed item {item_id} is not a known item")
        consumed_item_gold[item_id] = raw_price
    return ItemTable(version, known_item_ids, consumed_item_gold)


def load_item_catalog(directory: Path) -> ItemCatalog:
    """Load every compact table in a directory; duplicate versions are fatal."""
    if not directory.is_dir():
        raise ValueError(f"item catalog is not a directory: {directory}")
    tables: dict[str, ItemTable] = {}
    by_line: dict[MajorMinor, str] = {}
    for path in sorted(directory.glob("*.json")):
        table = load_item_table(path)
        if table.version in tables:
            raise ValueError(f"item catalog has duplicate version {table.version}")
        line = parse_major_minor(table.version)
        previous = by_line.get(line)
        if previous is not None:
            raise ValueError(
                f"item catalog has two tables for {line.major}.{line.minor}: "
                f"{previous} and {table.version}"
            )
        tables[table.version] = table
        by_line[line] = table.version
    if not tables:
        raise ValueError(f"item catalog is empty: {directory}")
    return ItemCatalog(tables)


def table_for_game_patch(catalog: ItemCatalog, game_patch: str) -> ItemTable:
    """Return the unique catalog table whose major.minor matches the game patch."""
    wanted = parse_major_minor(game_patch)
    matches = [
        table for table in catalog.tables.values() if parse_major_minor(table.version) == wanted
    ]
    if not matches:
        raise ValueError(f"no item table for game patch {game_patch}")
    if len(matches) > 1:
        raise ValueError(f"multiple item tables for game patch {game_patch}")
    return matches[0]


def details_wall_seconds(stamp: str) -> float:
    """Parse rfc460Timestamp to unix seconds."""
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid details timestamp: {stamp!r}") from exc
    return parsed.timestamp()


def count_known_items(
    raw_items: object, item_table: ItemTable, stamp: str, participant_id: int
) -> Counter[int]:
    """Count item ids in one participant inventory; unknown ids are fatal."""
    if not isinstance(raw_items, list):
        raise ValueError(f"details frame {stamp} participant {participant_id} has no items")
    counts: Counter[int] = Counter()
    for raw_item_id in cast(list[object], raw_items):
        if isinstance(raw_item_id, bool) or not isinstance(raw_item_id, int):
            raise ValueError(f"details frame {stamp} has an invalid item id")
        if raw_item_id not in item_table.known_item_ids:
            raise ValueError(f"unknown item id {raw_item_id} at {stamp}")
        counts[raw_item_id] += 1
    return counts


def apply_consumed_inventory(
    participant_id: int,
    counts: Counter[int],
    wall: float,
    item_table: ItemTable,
    committed: dict[int, Counter[int]],
    pending: dict[PendingAbsence, float],
    cumulative: dict[int, int],
) -> None:
    """Spend consumed-item drops that have been absent long enough."""
    before = committed.setdefault(participant_id, Counter())
    relevant = set(before) | {
        item_id for item_id in counts if item_id in item_table.consumed_item_gold
    }
    for item_id in relevant:
        observed = counts.get(item_id, 0)
        old_count = before[item_id]
        key = PendingAbsence(participant_id, item_id)
        if observed >= old_count:
            pending.pop(key, None)
            before[item_id] = observed
            if before[item_id] == 0:
                del before[item_id]
            continue
        if key not in pending:
            pending[key] = wall
        if wall - pending[key] < LOL_CONSUMED_ABSENCE_SECONDS:
            continue
        drop = old_count - observed
        cumulative[participant_id] += drop * item_table.consumed_item_gold[item_id]
        before[item_id] = observed
        pending.pop(key, None)
        if before[item_id] == 0:
            del before[item_id]


def apply_consumed_frame(
    stamp: str,
    wall: float,
    raw_participants: Sequence[object],
    item_table: ItemTable,
    participant_ids: frozenset[int],
    committed: dict[int, Counter[int]],
    pending: dict[PendingAbsence, float],
    cumulative: dict[int, int],
) -> None:
    """Validate ten participants and apply inventory diffs for one details stamp."""
    seen: set[int] = set()
    for raw_participant in raw_participants:
        if not isinstance(raw_participant, dict):
            raise ValueError(f"details frame {stamp} has a non-object participant")
        participant = cast(dict[str, object], raw_participant)
        participant_id = participant.get("participantId")
        if isinstance(participant_id, bool) or not isinstance(participant_id, int):
            raise ValueError(f"details frame {stamp} has an invalid participantId")
        if participant_id not in participant_ids or participant_id in seen:
            raise ValueError(f"details frame {stamp} has participantId {participant_id}")
        seen.add(participant_id)
        counts = count_known_items(participant.get("items"), item_table, stamp, participant_id)
        apply_consumed_inventory(
            participant_id, counts, wall, item_table, committed, pending, cumulative
        )
    if frozenset(seen) != participant_ids:
        raise ValueError(f"details frame {stamp} does not contain participants 1..10")


def build_consumed_timeline(
    frames: Sequence[Mapping[str, object]], item_table: ItemTable
) -> ConsumedTimeline:
    """Track cumulative consumed-item gold for ten participants.

    A consumed-item count drop is spent only after it stays missing for
    LOL_CONSUMED_ABSENCE_SECONDS. Shop flicker (gone then back inside that
    window) is not consumption. A trailing absence shorter than the window is
    not spent.

    ponytail: selling a consumable refunds gold but totalGold stays put, so this
    subtracts the full price; buy+use inside one details interval is a zero
    delta and undercounts. Both are rare. GRID residual is the accuracy ceiling;
    event-level inventory if that residual grows.
    """
    participant_ids = frozenset(range(1, 11))
    committed: dict[int, Counter[int]] = {}
    pending: dict[PendingAbsence, float] = {}
    cumulative = {participant_id: 0 for participant_id in participant_ids}
    by_stamp: dict[str, ConsumedFrame] = {}

    for frame in frames:
        stamp = frame.get("rfc460Timestamp")
        if not isinstance(stamp, str) or not stamp or stamp in by_stamp:
            raise ValueError(f"invalid or duplicate details timestamp: {stamp!r}")
        wall = details_wall_seconds(stamp)
        raw_participants = frame.get("participants")
        if not isinstance(raw_participants, list):
            raise ValueError(f"details frame {stamp} has no participants")
        apply_consumed_frame(
            stamp,
            wall,
            cast(list[object], raw_participants),
            item_table,
            participant_ids,
            committed,
            pending,
            cumulative,
        )
        by_stamp[stamp] = ConsumedFrame(dict(cumulative))

    return ConsumedTimeline(by_stamp)
