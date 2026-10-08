import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pytest

from collect.common import opendota_candidates
from collect.common.catalog_types import (
    MarketContractKind,
    MarketContractRow,
    MarketInventoryStatus,
)
from collect.common.opendota_candidates import (
    build_opendota_candidate_query,
    explorer_sql,
    fetch_opendota_candidate_pages,
    load_opendota_candidate_rows,
    refresh_opendota_candidate_pages,
    validate_opendota_candidate_row,
)
from collect.s02_link_opendota import (
    BestOf,
    OpenDotaMap,
    build_links,
    build_open_dota_series,
    load_event_contexts,
    validate_links,
)
from shared.utils.series_format import series_winner_covers_map

SCHEDULED_TS = 1_700_000_000


@contextmanager
def _fake_http_client() -> Generator[None]:
    """Stand in for the OpenDota HTTP client; the request itself is monkeypatched."""
    yield None


def _stub_explorer_response(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    """Answer every OpenDota explorer request with one fixed payload."""

    def get_json(*_: object) -> object:
        return payload

    monkeypatch.setattr(opendota_candidates, "http_client", _fake_http_client)
    monkeypatch.setattr(opendota_candidates, "get_json", get_json)


def build_contract(
    event_id: str,
    condition_id: str,
    kind: MarketContractKind,
    inventory_status: MarketInventoryStatus,
    best_of: int | None,
    game_number: int | None,
    parse_status: str,
    team_a: str | None,
    team_b: str | None,
) -> MarketContractRow:
    """Build one universe contract carrying only the linker's required values."""
    return {
        "conditionId": condition_id,
        "event_id": event_id,
        "event_title": event_id,
        "contract_kind": kind,
        "team_a": team_a,
        "team_b": team_b,
        "best_of": best_of,
        "game_number": game_number,
        "scheduled_ts": SCHEDULED_TS,
        "parse_status": parse_status,
        "market_slug": None,
        "seconds_delay": None,
        "market_closed_at": None,
        "token_id_0": None,
        "token_id_1": None,
        "inventory_status": inventory_status,
    }


def write_universe(path: Path, contracts: list[MarketContractRow]) -> None:
    """Write test contracts with the nullable integer dtypes used in production."""
    frame = pd.DataFrame(contracts)
    frame[["best_of", "game_number", "scheduled_ts"]] = frame[
        ["best_of", "game_number", "scheduled_ts"]
    ].astype("Int64")
    frame.to_parquet(path, index=False)


def build_map(
    match_id: int,
    start_time: int,
    duration: int,
    league_id: int,
    radiant_team_id: int,
    dire_team_id: int,
    radiant_name: str,
    dire_name: str,
    best_of: BestOf | None,
    radiant_win: bool,
) -> OpenDotaMap:
    """Construct one typed map for the series builder."""
    return OpenDotaMap(
        match_id=match_id,
        start_time=start_time,
        duration=duration,
        league_id=league_id,
        radiant_team_id=radiant_team_id,
        dire_team_id=dire_team_id,
        radiant_name=radiant_name,
        dire_name=dire_name,
        best_of=best_of,
        radiant_win=radiant_win,
    )


def test_series_winner_covers_only_true_deciders() -> None:
    """Match Winner is a map market only on BO1/BO3/BO5 last maps without Game N."""
    assert series_winner_covers_map(1, 1, map_winner_exists=False)
    assert not series_winner_covers_map(1, 1, map_winner_exists=True)
    assert series_winner_covers_map(3, 3, map_winner_exists=False)
    assert not series_winner_covers_map(3, 2, map_winner_exists=False)
    assert not series_winner_covers_map(2, 2, map_winner_exists=False)
    assert series_winner_covers_map(5, 5, map_winner_exists=False)


def test_candidate_validation_keeps_zero_duration_for_series_rejection(tmp_path: Path) -> None:
    """Abandoned maps reach the series builder instead of aborting the whole cache."""
    row: object = {
        "match_id": 1,
        "start_time": 1,
        "duration": 0,
        "leagueid": 1,
        "radiant_team_id": 1,
        "dire_team_id": 2,
        "radiant_name": "A",
        "dire_name": "B",
        "series_id": 1,
        "series_type": 1,
        "radiant_win": True,
    }

    validated = validate_opendota_candidate_row(row, tmp_path / "page.json")

    assert validated["duration"] == 0


def test_candidate_validation_rejects_bool_in_place_of_int(tmp_path: Path) -> None:
    """JSON booleans are never accepted as candidate ids or durations."""
    row: object = {
        "match_id": True,
        "start_time": 1,
        "duration": 0,
        "leagueid": 1,
        "radiant_team_id": 1,
        "dire_team_id": 2,
        "radiant_name": "A",
        "dire_name": "B",
        "series_id": 1,
        "series_type": 1,
        "radiant_win": True,
    }

    with pytest.raises(RuntimeError, match="match_id is not a positive integer"):
        validate_opendota_candidate_row(row, tmp_path / "page.json")


def test_candidate_validation_rejects_a_missing_field(tmp_path: Path) -> None:
    """A page row missing a column fails loudly instead of being padded."""
    row: object = {
        "match_id": 1,
        "start_time": 1,
        "duration": 0,
        "leagueid": 1,
        "radiant_team_id": 1,
        "dire_team_id": 2,
        "radiant_name": "A",
        "series_id": 1,
        "series_type": 1,
        "radiant_win": True,
    }

    with pytest.raises(RuntimeError, match="candidate schema mismatch"):
        validate_opendota_candidate_row(row, tmp_path / "page.json")


def test_explorer_sql_rejects_a_payload_without_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed explorer answer raises instead of looking like the end of the data."""
    _stub_explorer_response(monkeypatch, {"err": "rate limited"})

    with pytest.raises(RuntimeError, match="no rows list"):
        explorer_sql("SELECT 1")


def test_explorer_sql_keeps_a_real_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty rows list is a valid end of the cursor walk."""
    _stub_explorer_response(monkeypatch, {"rows": []})

    assert explorer_sql("SELECT 1") == []


def test_failed_refresh_keeps_the_previous_candidate_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken response leaves the old snapshot in place instead of truncating it."""
    raw_dir = tmp_path / "pages"
    raw_dir.mkdir()
    (raw_dir / "page_after_0000000000.json").write_text("{}")
    _stub_explorer_response(monkeypatch, {"err": "rate limited"})

    with pytest.raises(RuntimeError):
        refresh_opendota_candidate_pages(raw_dir, 1, 2)

    assert [path.name for path in raw_dir.iterdir()] == ["page_after_0000000000.json"]


def test_new_candidate_pages_archive_series_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """series_id is archived for later use even though linking ignores it."""
    row = {
        "match_id": 7,
        "start_time": 5,
        "duration": 1_000,
        "leagueid": 1,
        "radiant_team_id": 1,
        "dire_team_id": 2,
        "radiant_name": "A",
        "dire_name": "B",
        "series_id": 99,
        "series_type": 1,
        "radiant_win": True,
    }
    _stub_explorer_response(monkeypatch, {"rows": [row]})

    fetch_opendota_candidate_pages(tmp_path, 0, 10)

    written = json.loads((tmp_path / "page_after_0000000000.json").read_text())
    assert written["rows"][0]["series_id"] == 99
    assert "m.series_id" in build_opendota_candidate_query(0, 10)


def test_previously_cached_candidate_pages_still_load(tmp_path: Path) -> None:
    """Pages archived by earlier runs stay readable without migration."""
    page = {
        "schema": "opendota_candidates_match_id_cursor_v3",
        "start_ts": 0,
        "end_ts": 10,
        "after": 0,
        "rows": [
            {
                "match_id": 7,
                "start_time": 5,
                "duration": 1_000,
                "leagueid": 1,
                "radiant_team_id": 1,
                "dire_team_id": 2,
                "radiant_name": "A",
                "dire_name": "B",
                "series_id": 99,
                "series_type": 1,
                "radiant_win": True,
            }
        ],
    }
    (tmp_path / "page_after_0000000000.json").write_text(json.dumps(page))

    rows = load_opendota_candidate_rows(tmp_path, 0, 10)

    assert [row["match_id"] for row in rows] == [7]


def test_load_event_contexts_keeps_only_required_fields(tmp_path: Path) -> None:
    """Map ids stay with their event while incomplete and excluded rows disappear."""
    contracts = [
        build_contract(
            "bo3", "series-3", "series_winner", "series_linking_required", 3, None, "ok", "A", "B"
        ),
        build_contract("bo3", "map-1", "map_winner", "candidate", 3, 1, "ok", "A", "B"),
        build_contract("bo3", "map-2", "map_winner", "candidate", 3, 2, "ok", "A", "B"),
        build_contract("bo1", "series-1", "series_winner", "candidate", 1, 1, "ok", "A", "B"),
        build_contract(
            "incomplete",
            "missing-bo",
            "series_winner",
            "series_linking_required",
            None,
            None,
            "missing_best_of",
            "A",
            "B",
        ),
        build_contract("excluded", "other", "other", "excluded", 3, None, "ok", "A", "B"),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)

    contexts = load_event_contexts(universe_path)
    contexts_by_id = {context.event_id: context for context in contexts}

    assert set(contexts_by_id) == {"bo1", "bo3"}
    assert contexts_by_id["bo3"].map_condition_ids == {1: "map-1", 2: "map-2", 3: "series-3"}
    assert contexts_by_id["bo1"].map_condition_ids == {1: "series-1"}


def test_load_event_contexts_drops_ok_rows_missing_link_fields(tmp_path: Path) -> None:
    """parse_status is not enough: a row without teams never becomes an EventContext."""
    contracts = [
        build_contract("no-team", "map-1", "map_winner", "candidate", 1, 1, "ok", None, "B"),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)

    assert load_event_contexts(universe_path) == []


def test_series_builder_closes_all_supported_formats() -> None:
    """Each supported format closes at its explicit map or score endpoint."""
    maps = [
        build_map(1, 100, 1_000, 1, 1, 2, "A", "B", 1, True),
        build_map(2, 200, 1_000, 2, 3, 4, "C", "D", 2, True),
        build_map(3, 300, 1_000, 2, 4, 3, "D", "C", 2, True),
        build_map(4, 400, 1_000, 3, 5, 6, "E", "F", 3, True),
        build_map(5, 500, 1_000, 3, 6, 5, "F", "E", 3, True),
        build_map(6, 600, 1_000, 3, 5, 6, "E", "F", 3, True),
        build_map(7, 700, 1_000, 4, 7, 8, "G", "H", 5, True),
        build_map(8, 800, 1_000, 4, 8, 7, "H", "G", 5, True),
        build_map(9, 900, 1_000, 4, 7, 8, "G", "H", 5, True),
        build_map(10, 1_000, 1_000, 4, 8, 7, "H", "G", 5, True),
        build_map(11, 1_100, 1_000, 4, 7, 8, "G", "H", 5, True),
    ]

    result = build_open_dota_series(list(reversed(maps)))

    assert [(series.best_of, len(series.maps)) for series in result] == [
        (1, 1),
        (2, 2),
        (3, 3),
        (5, 5),
    ]
    assert sum(len(series.maps) for series in result) == len(maps)


def test_series_builder_splits_one_matchup_by_start_gap() -> None:
    """Two BO3 meetings of the same teams stay two series, split by the map gap."""
    maps = [
        build_map(
            8732591809,
            100,
            2_000,
            19255,
            55,
            9895247,
            "PowerRangers",
            "Rune Eaters",
            3,
            True,
        ),
        build_map(
            8732684200,
            4_000,
            2_000,
            19255,
            9895247,
            55,
            "Rune Eaters",
            "PowerRangers",
            3,
            False,
        ),
        build_map(
            8750488026,
            1_000_000,
            2_000,
            19255,
            55,
            9895247,
            "PowerRangers",
            "Rune Eaters",
            3,
            True,
        ),
        build_map(
            8750563954,
            1_004_000,
            2_000,
            19255,
            9895247,
            55,
            "Rune Eaters",
            "PowerRangers",
            3,
            False,
        ),
    ]

    result = build_open_dota_series(maps)

    assert [[game.match_id for game in series.maps] for series in result] == [
        [8732591809, 8732684200],
        [8750488026, 8750563954],
    ]


def test_series_builder_rejects_short_invalid_incomplete_and_large_gap() -> None:
    """Uncertain candidates never leak into the complete-series catalog."""
    maps = [
        build_map(1, 100, 599, 1, 1, 2, "A", "B", 3, True),
        build_map(2, 200, 1_000, 1, 2, 1, "B", "A", 3, False),
        build_map(3, 300, 1_000, 2, 3, 4, "C", "D", 3, True),
        build_map(4, 400, 1_000, 3, 5, 6, "E", "F", None, True),
        build_map(5, 500, 1_000, 4, 7, 8, "G", "H", 3, True),
        build_map(6, 500 + 4 * 3_600 + 1, 1_000, 4, 8, 7, "H", "G", 3, False),
    ]

    result = build_open_dota_series(maps)

    assert result == ()


def test_links_skip_series_only_and_leave_sweep_series_winner_unattached(
    tmp_path: Path,
) -> None:
    """Series-only events stay out; a 2-0 BO3 last map already has Game 2 Winner."""
    contracts = [
        build_contract(
            "series-only",
            "series-only-condition",
            "series_winner",
            "series_linking_required",
            3,
            None,
            "ok",
            "Power Rangers",
            "Inner Circle",
        ),
        build_contract(
            "with-maps",
            "series-condition",
            "series_winner",
            "series_linking_required",
            3,
            None,
            "ok",
            "Power Rangers",
            "Inner Circle",
        ),
        build_contract(
            "with-maps",
            "map-1",
            "map_winner",
            "candidate",
            3,
            1,
            "ok",
            "Power Rangers",
            "Inner Circle",
        ),
        build_contract(
            "with-maps",
            "map-2",
            "map_winner",
            "candidate",
            3,
            2,
            "ok",
            "Power Rangers",
            "Inner Circle",
        ),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    maps = [
        build_map(
            101,
            SCHEDULED_TS + 60,
            1_000,
            1,
            1,
            2,
            "PowerRangers",
            "Inner Circle x Insanity",
            3,
            True,
        ),
        build_map(
            102,
            SCHEDULED_TS + 3_600,
            1_000,
            1,
            2,
            1,
            "Inner Circle x Insanity",
            "PowerRangers",
            3,
            False,
        ),
    ]
    result = build_open_dota_series(maps)

    contexts = load_event_contexts(universe_path)
    links, audit = build_links(contexts, result)

    assert [context.event_id for context in contexts] == ["with-maps"]
    assert links.columns.tolist() == [
        "event_id",
        "game_number",
        "match_id",
        "map_condition_id",
        "match_start_time",
        "grid_clock_seconds",
        "radiant_token_index",
        "opendota_radiant_name",
        "opendota_dire_name",
    ]
    assert links["event_id"].tolist() == ["with-maps", "with-maps"]
    assert links["match_id"].tolist() == [101, 102]
    assert links["map_condition_id"].tolist() == ["map-1", "map-2"]
    assert links["radiant_token_index"].tolist() == [0, 1]
    assert links["opendota_radiant_name"].tolist() == [
        "PowerRangers",
        "Inner Circle x Insanity",
    ]
    assert links["opendota_dire_name"].tolist() == [
        "Inner Circle x Insanity",
        "PowerRangers",
    ]
    assert audit["event_id"].tolist() == ["with-maps"]
    assert audit.loc[0, "match_status"] == "matched"
    assert audit.loc[0, "accepted_link_count"] == 2


def test_links_match_when_opendota_uses_the_shorter_alias(tmp_path: Path) -> None:
    """Polymarket 'Inner Circle x Insanity' still links to OpenDota 'Inner Circle'."""
    contracts = [
        build_contract(
            "e",
            "map-1",
            "map_winner",
            "candidate",
            1,
            1,
            "ok",
            "FTS",
            "Inner Circle x Insanity",
        ),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    maps = [
        build_map(
            101,
            SCHEDULED_TS + 60,
            1_000,
            1,
            1,
            2,
            "FTS",
            "Inner Circle",
            1,
            True,
        ),
    ]
    links, audit = build_links(load_event_contexts(universe_path), build_open_dota_series(maps))

    assert links["match_id"].tolist() == [101]
    assert links["opendota_dire_name"].tolist() == ["Inner Circle"]
    assert audit.loc[0, "match_status"] == "matched"


def test_links_yakutou_versus_tearlaments_are_opponents(tmp_path: Path) -> None:
    """Yakult Brothers vs YB.Tearlaments stays one series, not a same-team tie."""
    contracts = [
        build_contract(
            "e",
            "map-1",
            "map_winner",
            "candidate",
            3,
            1,
            "ok",
            "Yakutou Brothers",
            "Tearlaments",
        ),
        build_contract(
            "e",
            "map-2",
            "map_winner",
            "candidate",
            3,
            2,
            "ok",
            "Yakutou Brothers",
            "Tearlaments",
        ),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    maps = [
        build_map(
            101,
            SCHEDULED_TS + 60,
            1_000,
            1,
            9351740,
            9579337,
            "Yakult Brothers",
            "YB.Tearlaments",
            3,
            True,
        ),
        build_map(
            102,
            SCHEDULED_TS + 3_600,
            1_000,
            1,
            9579337,
            9351740,
            "YB.Tearlaments",
            "Yakult Brothers",
            3,
            False,
        ),
    ]
    links, audit = build_links(load_event_contexts(universe_path), build_open_dota_series(maps))

    assert links["match_id"].tolist() == [101, 102]
    assert audit.loc[0, "match_status"] == "matched"


def test_links_attach_series_winner_to_played_bo3_decider(tmp_path: Path) -> None:
    """A 2-1 BO3 with no Game 3 Winner uses Match Winner as map 3."""
    contracts = [
        build_contract(
            "e", "series", "series_winner", "series_linking_required", 3, None, "ok", "A", "B"
        ),
        build_contract("e", "map-1", "map_winner", "candidate", 3, 1, "ok", "A", "B"),
        build_contract("e", "map-2", "map_winner", "candidate", 3, 2, "ok", "A", "B"),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    maps = [
        build_map(1, SCHEDULED_TS + 60, 1_000, 1, 1, 2, "A", "B", 3, True),
        build_map(2, SCHEDULED_TS + 3_600, 1_000, 1, 1, 2, "A", "B", 3, False),
        build_map(3, SCHEDULED_TS + 7_200, 1_000, 1, 1, 2, "A", "B", 3, True),
    ]
    links, audit = build_links(load_event_contexts(universe_path), build_open_dota_series(maps))

    assert links["match_id"].tolist() == [1, 2, 3]
    assert links["map_condition_id"].tolist() == ["map-1", "map-2", "series"]
    assert audit.loc[0, "accepted_link_count"] == 3


def test_links_prefer_game_n_winner_over_series_winner(tmp_path: Path) -> None:
    """An explicit Game 3 Winner beats Match Winner on a played BO3 decider."""
    contracts = [
        build_contract(
            "e", "series", "series_winner", "series_linking_required", 3, None, "ok", "A", "B"
        ),
        build_contract("e", "map-1", "map_winner", "candidate", 3, 1, "ok", "A", "B"),
        build_contract("e", "map-2", "map_winner", "candidate", 3, 2, "ok", "A", "B"),
        build_contract("e", "map-3", "map_winner", "candidate", 3, 3, "ok", "A", "B"),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    maps = [
        build_map(1, SCHEDULED_TS + 60, 1_000, 1, 1, 2, "A", "B", 3, True),
        build_map(2, SCHEDULED_TS + 3_600, 1_000, 1, 1, 2, "A", "B", 3, False),
        build_map(3, SCHEDULED_TS + 7_200, 1_000, 1, 1, 2, "A", "B", 3, True),
    ]
    links, _audit = build_links(load_event_contexts(universe_path), build_open_dota_series(maps))

    assert links["map_condition_id"].tolist() == ["map-1", "map-2", "map-3"]


def test_links_skip_unlisted_bo5_map_and_attach_decider(tmp_path: Path) -> None:
    """A 3-2 BO5 with Game 1/2/3 Winner plus Match Winner links [1, 2, 3, 5]; map 4 is skipped."""
    contracts = [
        build_contract(
            "e", "series", "series_winner", "series_linking_required", 5, None, "ok", "A", "B"
        ),
        build_contract("e", "map-1", "map_winner", "candidate", 5, 1, "ok", "A", "B"),
        build_contract("e", "map-2", "map_winner", "candidate", 5, 2, "ok", "A", "B"),
        build_contract("e", "map-3", "map_winner", "candidate", 5, 3, "ok", "A", "B"),
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    maps = [
        build_map(1, SCHEDULED_TS + 60, 1_000, 1, 1, 2, "A", "B", 5, True),
        build_map(2, SCHEDULED_TS + 3_600, 1_000, 1, 1, 2, "A", "B", 5, False),
        build_map(3, SCHEDULED_TS + 7_200, 1_000, 1, 1, 2, "A", "B", 5, True),
        build_map(4, SCHEDULED_TS + 10_800, 1_000, 1, 1, 2, "A", "B", 5, False),
        build_map(5, SCHEDULED_TS + 14_400, 1_000, 1, 1, 2, "A", "B", 5, True),
    ]
    links, audit = build_links(load_event_contexts(universe_path), build_open_dota_series(maps))
    validate_links(links)

    assert links["match_id"].tolist() == [1, 2, 3, 5]
    assert links["game_number"].tolist() == [1, 2, 3, 5]
    assert links["map_condition_id"].tolist() == ["map-1", "map-2", "map-3", "series"]
    assert audit.loc[0, "accepted_link_count"] == 4


def test_links_reject_multiple_complete_series(tmp_path: Path) -> None:
    """Two name/time/format candidates remain ambiguous instead of being ranked."""
    contract = build_contract("event", "map-1", "map_winner", "candidate", 1, 1, "ok", "A", "B")
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, [contract])
    maps = [
        build_map(1, SCHEDULED_TS - 60, 1_000, 1, 1, 2, "A", "B", 1, True),
        build_map(2, SCHEDULED_TS + 60, 1_000, 2, 3, 4, "A", "B", 1, True),
    ]
    result = build_open_dota_series(maps)

    links, audit = build_links(load_event_contexts(universe_path), result)

    assert links.empty
    assert audit.loc[0, "match_status"] == "ambiguous"
    assert audit.loc[0, "ambiguity_reason"] == "multiple_complete_series"


def test_links_include_two_hour_boundary_and_reject_duplicate_tie(tmp_path: Path) -> None:
    """The time window is inclusive and equal PM claims do not pick a winner."""
    contracts = [
        build_contract(event_id, event_id, "map_winner", "candidate", 1, 1, "ok", "A", "B")
        for event_id in ("first", "second")
    ]
    universe_path = tmp_path / "universe.parquet"
    write_universe(universe_path, contracts)
    game = build_map(
        1,
        SCHEDULED_TS + 2 * 3_600,
        1_000,
        1,
        1,
        2,
        "A",
        "B",
        1,
        True,
    )
    result = build_open_dota_series([game])

    links, audit = build_links(load_event_contexts(universe_path), result)

    assert links.empty
    assert set(audit["match_status"]) == {"ambiguous"}
    assert set(audit["ambiguity_reason"]) == {"series_claim_tie"}
