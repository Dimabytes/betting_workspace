import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

import pandas as pd

from shared.types.dataset import MatchCatalogRow
from shared.types.opendota import OpenDotaPause, RadiantTokenIndex
from shared.utils.match_time import parse_utc
from shared.utils.polymarket import GammaMarket

MATCH_CATALOG_COLUMNS: tuple[str, ...] = tuple(MatchCatalogRow.__annotations__)


@dataclass(frozen=True)
class CatalogEntry:
    """One catalog row as a typed object; the horn is the timing contract anchor.

    `spawn_at` is GRID map-load or None on archive-only rows; `anchor_at` is the
    earliest confirmed pre-game instant (spawn when known, else the horn).
    `pauses` is always a list for catalog rows — unknown pauses never publish.
    """

    match_id: int
    condition_id: str
    event_id: str
    radiant_token_index: RadiantTokenIndex
    playback_available: bool
    start_time: int
    duration: int
    radiant_prior: float
    spawn_at: datetime | None
    horn_at: datetime
    pauses: list[OpenDotaPause]
    ended_at: datetime
    radiant_win: bool
    archive_id: str | None
    archive_root: str | None
    archive_feed_source: str | None
    schedule_fingerprint: str | None
    gamma: GammaMarket

    @property
    def anchor_at(self) -> datetime:
        """Earliest confirmed pre-game instant: GRID spawn, else the archive horn."""
        return self.spawn_at if self.spawn_at is not None else self.horn_at


def catalog_entry_from_row(row: MatchCatalogRow) -> CatalogEntry:
    token_index = int(row["radiant_token_index"])
    horn_at = parse_utc(str(row["horn_at"]))
    spawn_cell = row["spawn_at"]
    spawn_at = parse_utc(str(spawn_cell)) if isinstance(spawn_cell, str) else None
    pauses_raw = row["pauses_json"]
    if not isinstance(pauses_raw, str):
        raise ValueError(f"catalog row {row['match_id']} has null pauses_json")
    pauses = cast(list[OpenDotaPause], json.loads(pauses_raw))
    return CatalogEntry(
        match_id=int(row["match_id"]),
        condition_id=str(row["condition_id"]),
        event_id=str(row["event_id"]),
        radiant_token_index=cast(RadiantTokenIndex, token_index),
        playback_available=bool(row["playback_available"]),
        start_time=int((spawn_at if spawn_at is not None else horn_at).timestamp()),
        duration=int(row["duration"]),
        radiant_prior=float(row["radiant_prior"]),
        spawn_at=spawn_at,
        horn_at=horn_at,
        pauses=pauses,
        ended_at=parse_utc(str(row["ended_at"])),
        radiant_win=bool(row["radiant_win"]),
        archive_id=row["archive_id"],
        archive_root=row["archive_root"],
        archive_feed_source=row["archive_feed_source"],
        schedule_fingerprint=row["schedule_fingerprint"],
        gamma=GammaMarket(
            slug=str(row["market_slug"]),
            closed_at=parse_utc(str(row["market_closed_at"])),
            seconds_delay=int(row["seconds_delay"]),
            token_ids=(str(row["token_id_0"]), str(row["token_id_1"])),
        ),
    )


class MatchCatalog(Mapping[int, CatalogEntry]):
    def __init__(self, by_match_id: Mapping[int, CatalogEntry]) -> None:
        self._by_match_id = dict(
            sorted(by_match_id.items(), key=lambda item: (item[1].start_time, item[0]))
        )

    def __getitem__(self, match_id: int) -> CatalogEntry:
        return self._by_match_id[match_id]

    def __iter__(self) -> Iterator[int]:
        return iter(self._by_match_id)

    def __len__(self) -> int:
        return len(self._by_match_id)


def load_match_catalog(path: Path) -> MatchCatalog:
    frame = pd.read_parquet(path, columns=list(MATCH_CATALOG_COLUMNS))
    rows = cast(list[MatchCatalogRow], frame.to_dict("records"))
    entries = [catalog_entry_from_row(row) for row in rows]
    return MatchCatalog({entry.match_id: entry for entry in entries})
