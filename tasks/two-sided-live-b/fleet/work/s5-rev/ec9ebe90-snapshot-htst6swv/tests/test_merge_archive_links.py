"""Merge rules for admitted archives onto the shared match_links table."""

from pathlib import Path

import pandas as pd
import pytest

from collect.common.catalog_types import (
    ArchiveFeedSource,
    ArchiveWinner,
    MatchLinkAuditRow,
    MatchLinkRow,
)
from collect.s03_merge_archive_links import (
    ArchiveCandidate,
    EventInventory,
    load_candidates,
    merge_archive_links,
    validate_match_links,
)
from shared.utils.parsing import parse_ts

HORN = "2026-01-01T00:01:30+00:00"
HORN_UNIX = 1767225690


def opendota_link(
    match_id: int,
    condition_id: str,
    event_id: str = "e1",
    game_number: int = 1,
    start_time: int = 1_000,
) -> dict[str, object]:
    """One OpenDotaLinkRow-shaped record."""
    return {
        "event_id": event_id,
        "game_number": game_number,
        "match_id": match_id,
        "map_condition_id": condition_id,
        "match_start_time": start_time,
        "grid_clock_seconds": 1_800,
        "radiant_token_index": 0,
        "opendota_radiant_name": "Radiant Team",
        "opendota_dire_name": "Dire Team",
    }


def candidate(
    archive_id: str,
    condition_id: str,
    event_id: str = "e1",
    *,
    feed_source: ArchiveFeedSource = "grid",
    contract_kind: str = "map_winner",
    map_number: int | None = 1,
    steam_match_id: int | None = 100,
    horn_at_utc: str = HORN,
    winner: ArchiveWinner | None = "radiant",
    yes_token_index: int | None = 0,
    yes_is_radiant: bool | None = True,
    duration_seconds: int | None = 2_000,
) -> ArchiveCandidate:
    """One admitted archive index row reduced to its link-relevant fields."""
    horn_unix = parse_ts(horn_at_utc)
    assert horn_unix is not None
    return ArchiveCandidate(
        archive_id=archive_id,
        archive_root="trader",
        feed_source=feed_source,
        condition_id=condition_id,
        event_id=event_id,
        contract_kind=contract_kind,
        map_number=map_number,
        steam_match_id=steam_match_id,
        joined_at_second=0,
        horn_at_utc=horn_at_utc,
        horn_unix=horn_unix,
        winner=winner,
        duration_seconds=duration_seconds,
        yes_token_index=yes_token_index,
        yes_is_radiant=yes_is_radiant,
        schedule_fingerprint=f"fp-{archive_id}",
    )


def merge(
    links: list[dict[str, object]],
    candidates: list[ArchiveCandidate],
    inventories: dict[str, EventInventory] | None = None,
    canonical: dict[str, str] | None = None,
    winners: dict[int, bool] | None = None,
) -> tuple[list[MatchLinkRow], list[MatchLinkAuditRow]]:
    rows, audit = merge_archive_links(
        pd.DataFrame(links),
        candidates,
        inventories if inventories is not None else {},
        canonical if canonical is not None else {},
        (lambda match_id: winners.get(match_id)) if winners is not None else (lambda _: None),
    )
    return rows, audit


def resolution_of(audit: list[MatchLinkAuditRow], archive_id: str) -> str:
    (row,) = [row for row in audit if row["archive_id"] == archive_id]
    return row["resolution"]


def test_attach_by_exact_condition_sets_provenance() -> None:
    links = [opendota_link(100, "0xABC")]
    rows, audit = merge(links, [candidate("grid-1-m1", "0xabc", steam_match_id=100)])
    (row,) = rows
    assert row["link_source"] == "opendota+archive"
    assert row["archive_id"] == "grid-1-m1"
    assert row["archive_root"] == "trader"
    assert row["archive_feed_source"] == "grid"
    assert row["archive_horn_at_utc"] == HORN
    assert row["archive_winner"] == "radiant"
    assert row["schedule_fingerprint"] == "fp-grid-1-m1"
    assert row["match_id"] == 100
    assert row["map_condition_id"] == "0xABC"
    assert row["identity_conflict"] is None
    assert row["archive_condition_id"] is None
    assert resolution_of(audit, "grid-1-m1") == "attach_by_condition"


def test_attach_by_condition_accepts_no_steam_archive() -> None:
    """An archive without a steam id still attaches when its condition is exact."""
    rows, audit = merge(
        [opendota_link(100, "0xabc")],
        [candidate("grid-2-m1", "0xabc", steam_match_id=None)],
    )
    assert rows[0]["archive_id"] == "grid-2-m1"
    assert rows[0]["identity_conflict"] is None
    assert resolution_of(audit, "grid-2-m1") == "attach_by_condition"


def test_steam_mismatch_attaches_with_flag_when_winner_agrees() -> None:
    """Same game by outcome: the archive's steam stamp is stale, keep the link's id."""
    rows, audit = merge(
        [opendota_link(100, "0xabc")],
        [candidate("grid-3-m1", "0xabc", steam_match_id=999, winner="radiant")],
        winners={100: True},
    )
    assert rows[0]["archive_id"] == "grid-3-m1"
    assert rows[0]["match_id"] == 100
    assert rows[0]["identity_conflict"] == "archive_steam_mismatch"
    assert resolution_of(audit, "grid-3-m1") == "attach_by_condition"


def test_steam_mismatch_excluded_when_winner_disagrees() -> None:
    """A different recorded outcome means the archive captured another game."""
    rows, audit = merge(
        [opendota_link(100, "0xabc")],
        [candidate("grid-4-m1", "0xabc", steam_match_id=999, winner="dire")],
        winners={100: True},
    )
    assert rows[0]["archive_id"] is None
    assert rows[0]["link_source"] == "opendota"
    assert resolution_of(audit, "grid-4-m1") == "excluded_winner_mismatch"


def test_condition_attach_map_mismatch_excluded() -> None:
    rows, audit = merge(
        [opendota_link(100, "0xabc", game_number=1)],
        [candidate("grid-5-m1", "0xabc", steam_match_id=100, map_number=2)],
    )
    assert rows[0]["archive_id"] is None
    assert resolution_of(audit, "grid-5-m1") == "excluded_map_mismatch"


def test_missing_horn_excluded(tmp_path: Path) -> None:
    """An admitted row with no horn is refused at load."""
    index_path = tmp_path / "index.parquet"
    pd.DataFrame([index_row("grid-6-m1", "100", "admitted", horn_at_utc=None)]).to_parquet(
        index_path
    )
    candidates, rejected, _ = load_candidates(index_path)
    assert candidates == []
    (row,) = rejected
    assert row["archive_id"] == "grid-6-m1"
    assert row["resolution"] == "excluded_horn_missing"


def test_attach_by_match_when_market_differs() -> None:
    """Archive of a sibling market on the same game attaches by steam id."""
    rows, audit = merge(
        [opendota_link(100, "0xmap", game_number=1)],
        [
            candidate(
                "grid-7-m1",
                "0xseries",
                steam_match_id=100,
                map_number=1,
                contract_kind="series_winner",
            )
        ],
        canonical={"0xseries": "0xSeries"},
    )
    row = rows[0]
    assert row["archive_id"] == "grid-7-m1"
    assert row["map_condition_id"] == "0xmap"
    assert row["archive_condition_id"] == "0xSeries"
    assert row["identity_conflict"] == "archive_market_differs"
    assert resolution_of(audit, "grid-7-m1") == "attach_by_match"


def test_attach_by_match_map_mismatch_excluded() -> None:
    rows, audit = merge(
        [opendota_link(100, "0xmap", game_number=1)],
        [candidate("grid-8-m1", "0xother", steam_match_id=100, map_number=2)],
    )
    assert rows[0]["archive_id"] is None
    assert resolution_of(audit, "grid-8-m1") == "excluded_map_mismatch"


def test_new_link_from_steam_only_archive() -> None:
    """An unseen market+match creates an archive-only row with null OpenDota fields."""
    rows, audit = merge(
        [],
        [
            candidate(
                "grid-9-m2",
                "0xnew",
                steam_match_id=555,
                map_number=2,
                yes_token_index=0,
                yes_is_radiant=False,
            )
        ],
        canonical={"0xnew": "0xNew"},
    )
    (row,) = rows
    assert row["match_id"] == 555
    assert row["map_condition_id"] == "0xNew"
    assert row["game_number"] == 2
    assert row["link_source"] == "archive"
    assert row["sort_ts"] == HORN_UNIX
    assert row["radiant_token_index"] == 1
    assert row["match_start_time"] is None
    assert row["grid_clock_seconds"] is None
    assert row["opendota_radiant_name"] is None
    assert row["opendota_dire_name"] is None
    assert resolution_of(audit, "grid-9-m2") == "new_link"


def test_no_steam_no_link_audit_row() -> None:
    """Without steam id or a condition hit the archive can only be audited."""
    rows, audit = merge([], [candidate("grid-10-m2", "0xnone", steam_match_id=None)])
    assert rows == []
    assert resolution_of(audit, "grid-10-m2") == "no_steam_no_link"


def test_series_winner_creates_decider_link() -> None:
    """BO3 series contract stands in for map 3 when no map market exists."""
    inventory = EventInventory(best_of=3, candidate_map_numbers=frozenset({1, 2}))
    rows, audit = merge(
        [],
        [
            candidate(
                "grid-11-m3",
                "0xseries",
                event_id="e2",
                steam_match_id=777,
                map_number=3,
                contract_kind="series_winner",
            )
        ],
        inventories={"e2": inventory},
        canonical={"0xseries": "0xSeries"},
    )
    (row,) = rows
    assert row["game_number"] == 3
    assert row["map_condition_id"] == "0xSeries"
    assert row["link_source"] == "archive"
    assert resolution_of(audit, "grid-11-m3") == "new_link"


def test_series_winner_excluded_when_map_market_exists() -> None:
    """The decider map already has its own market — the series contract adds nothing."""
    inventory = EventInventory(best_of=3, candidate_map_numbers=frozenset({1, 2, 3}))
    rows, audit = merge(
        [],
        [
            candidate(
                "grid-12-m3",
                "0xseries",
                event_id="e2",
                steam_match_id=777,
                map_number=3,
                contract_kind="series_winner",
            )
        ],
        inventories={"e2": inventory},
    )
    assert rows == []
    assert resolution_of(audit, "grid-12-m3") == "excluded_series_rule"


def test_series_winner_excluded_on_non_decider_map() -> None:
    inventory = EventInventory(best_of=3, candidate_map_numbers=frozenset({1, 2}))
    rows, audit = merge(
        [],
        [
            candidate(
                "grid-13-m1",
                "0xseries",
                event_id="e2",
                steam_match_id=777,
                map_number=1,
                contract_kind="series_winner",
            )
        ],
        inventories={"e2": inventory},
    )
    assert rows == []
    assert resolution_of(audit, "grid-13-m1") == "excluded_series_rule"


def test_condition_and_steam_pointing_at_different_rows_conflict() -> None:
    links = [
        opendota_link(1, "0xa", game_number=1),
        opendota_link(2, "0xb", game_number=2, start_time=2_000),
    ]
    rows, audit = merge(
        links,
        [candidate("grid-14-m1", "0xa", steam_match_id=2, map_number=1)],
    )
    assert all(row["archive_id"] is None for row in rows)
    assert resolution_of(audit, "grid-14-m1") == "excluded_steam_condition_conflict"


def test_second_archive_on_taken_row_conflicts() -> None:
    """One archive attaches once; a second archive for the same condition is refused."""
    rows, audit = merge(
        [opendota_link(100, "0xabc")],
        [
            candidate("grid-15-m1", "0xabc", steam_match_id=100),
            candidate("grid-16-m1", "0xabc", steam_match_id=100),
        ],
    )
    assert rows[0]["archive_id"] == "grid-15-m1"
    assert resolution_of(audit, "grid-16-m1") == "excluded_steam_condition_conflict"


def test_two_new_archives_cannot_claim_the_same_slot() -> None:
    """The first archive to create (event, game_number) owns the slot."""
    rows, audit = merge(
        [],
        [
            candidate("grid-17-m1", "0xc1", event_id="e3", steam_match_id=10, map_number=1),
            candidate("grid-18-m1", "0xc2", event_id="e3", steam_match_id=20, map_number=1),
        ],
    )
    (row,) = rows
    assert row["archive_id"] == "grid-17-m1"
    assert resolution_of(audit, "grid-18-m1") == "excluded_steam_condition_conflict"


def test_archive_only_row_with_fabricated_fields_fails_validation() -> None:
    """validate_match_links refuses archive rows that carry OpenDota fields."""
    rows, _ = merge(
        [],
        [candidate("grid-19-m1", "0xc9", steam_match_id=42, map_number=1)],
    )
    rows[0]["match_start_time"] = 1_000
    with pytest.raises(RuntimeError, match="fabricated"):
        validate_match_links(pd.DataFrame(rows))


def index_row(
    archive_id: str,
    steam_match_id: object,
    admission: str,
    *,
    horn_at_utc: object = HORN,
    feed_source: object = "grid",
) -> dict[str, object]:
    """One archive_index parquet row holding only the fields load_candidates reads."""
    return {
        "game": "dota",
        "admission": admission,
        "archive_root": "trader",
        "archive_id": archive_id,
        "feed_source": feed_source,
        "condition_id": "0xabc",
        "event_id": "e1",
        "contract_kind": "map_winner",
        "map_number": 1,
        "steam_match_id": steam_match_id,
        "joined_at_second": 0,
        "horn_at_utc": horn_at_utc,
        "winner": "radiant",
        "duration_seconds": 2_000,
        "yes_token_index": 0,
        "yes_is_radiant": True,
        "schedule_fingerprint": f"fp-{archive_id}",
    }


def test_load_candidates_parses_string_steam_match_id(tmp_path: Path) -> None:
    """The index stores steam_match_id in a string column; it must still parse."""
    index_path = tmp_path / "index.parquet"
    frame = pd.DataFrame(
        [
            index_row("grid-20-m1", "8975647643", "admitted"),
            index_row("grid-21-m1", None, "admitted"),
            index_row("grid-22-m1", "1", "record:no_terminal"),
        ]
    )
    frame.to_parquet(index_path)
    candidates, rejected, refusals = load_candidates(index_path)
    by_id = {candidate.archive_id: candidate for candidate in candidates}
    assert by_id["grid-20-m1"].steam_match_id == 8975647643
    assert by_id["grid-21-m1"].steam_match_id is None
    assert rejected == []
    assert refusals == {"record:no_terminal": 1}


def test_load_candidates_rejects_unknown_feed_source(tmp_path: Path) -> None:
    """A feed source outside the known enum is refused, not cast away."""
    index_path = tmp_path / "index.parquet"
    pd.DataFrame([index_row("grid-23-m1", "1", "admitted", feed_source="telonex")]).to_parquet(
        index_path
    )
    candidates, rejected, _ = load_candidates(index_path)
    assert candidates == []
    (row,) = rejected
    assert row["resolution"] == "excluded_incomplete_identity"
