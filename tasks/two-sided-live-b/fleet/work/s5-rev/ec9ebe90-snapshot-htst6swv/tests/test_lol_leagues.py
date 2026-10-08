"""Fixture tests for the shared LoL league whitelist filter."""

import json
from pathlib import Path

import pandas as pd
import pytest

from shared.utils.json_io import write_json
from shared.utils.lol_leagues import (
    is_allowed_league,
    load_canonical_league_whitelist,
    load_event_leagues,
    read_league_whitelist,
    resolve_base_league,
    select_backtest_event_ids,
)

BASE_LEAGUE = "LCK"
ALIASED_LEAGUE = "NACL"


def write_whitelist(path: Path) -> Path:
    """Write a two-league whitelist with one alias."""
    write_json(
        path,
        {
            "leagues": [BASE_LEAGUE, ALIASED_LEAGUE],
            "aliases": {"North American Challengers League": ALIASED_LEAGUE},
        },
    )
    return path


def write_universe(path: Path, rows: list[dict[str, object]]) -> Path:
    """Write a universe parquet with only the columns the filter reads."""
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_alias_resolves_and_unknown_name_stays(tmp_path: Path) -> None:
    """An observed name maps onto its base league; anything else passes through."""
    whitelist = read_league_whitelist(write_whitelist(tmp_path / "wl.json"))
    assert resolve_base_league(whitelist, "North American Challengers League") == ALIASED_LEAGUE
    assert resolve_base_league(whitelist, "LCK Cup Playoffs") == "LCK Cup Playoffs"


def test_aliases_must_point_at_whitelisted_leagues(tmp_path: Path) -> None:
    """An alias target outside the whitelist is a broken table, not a silent drop."""
    path = tmp_path / "wl.json"
    write_json(path, {"leagues": [BASE_LEAGUE], "aliases": {"LCK Cup": "LCK Cup Series"}})
    with pytest.raises(ValueError, match="outside the whitelist"):
        read_league_whitelist(path)


def test_alias_cannot_shadow_a_whitelisted_name(tmp_path: Path) -> None:
    """A name cannot be both a base league and an alias key."""
    path = tmp_path / "wl.json"
    write_json(path, {"leagues": [BASE_LEAGUE], "aliases": {BASE_LEAGUE: BASE_LEAGUE}})
    with pytest.raises(ValueError, match="repeat whitelisted league names"):
        read_league_whitelist(path)


def test_separate_tournaments_and_missing_names_are_excluded(tmp_path: Path) -> None:
    """Only exact whitelist names and aliases pass; suffixed tournaments do not."""
    whitelist = read_league_whitelist(write_whitelist(tmp_path / "wl.json"))
    universe = write_universe(
        tmp_path / "markets.parquet",
        [
            {"event_id": "e1", "league": BASE_LEAGUE},
            {"event_id": "e2", "league": "LCK Cup Playoffs"},
            {"event_id": "e3", "league": "North American Challengers League"},
            {"event_id": "e4", "league": None},
            {"event_id": "e5", "league": "LCP"},
        ],
    )
    assert select_backtest_event_ids(whitelist, universe) == frozenset({"e1", "e3"})


def test_no_live_feed_leagues_leave_the_backtest_population(tmp_path: Path) -> None:
    """A whitelisted league with no live GRID feed still passes live admission, not the backtest."""
    path = tmp_path / "wl.json"
    write_json(
        path,
        {
            "leagues": [BASE_LEAGUE, ALIASED_LEAGUE],
            "no_live_feed": [BASE_LEAGUE],
            "aliases": {"North American Challengers League": ALIASED_LEAGUE},
        },
    )
    whitelist = read_league_whitelist(path)
    universe = write_universe(
        tmp_path / "markets.parquet",
        [
            {"event_id": "e1", "league": BASE_LEAGUE},
            {"event_id": "e2", "league": "North American Challengers League"},
        ],
    )
    assert is_allowed_league(whitelist, BASE_LEAGUE)
    assert select_backtest_event_ids(whitelist, universe) == frozenset({"e2"})


def test_no_live_feed_must_name_whitelisted_leagues(tmp_path: Path) -> None:
    """A no_live_feed entry outside the whitelist is a broken table."""
    path = tmp_path / "wl.json"
    write_json(path, {"leagues": [BASE_LEAGUE], "no_live_feed": ["LCP"], "aliases": {}})
    with pytest.raises(ValueError, match="no_live_feed names leagues outside"):
        read_league_whitelist(path)


def test_two_league_names_on_one_event_raise(tmp_path: Path) -> None:
    """One PM event must carry one league name."""
    universe = write_universe(
        tmp_path / "markets.parquet",
        [{"event_id": "e1", "league": BASE_LEAGUE}, {"event_id": "e1", "league": "LCP"}],
    )
    with pytest.raises(ValueError, match="two league names"):
        load_event_leagues(universe)


def test_shipped_whitelist_holds_twenty_three_leagues() -> None:
    """The committed whitelist is the 23-league table from the 2026-10-03 all-leagues run."""
    whitelist = read_league_whitelist(Path("config/lol_league_whitelist.json"))
    assert len(whitelist.leagues) == 23
    assert "NACL" in whitelist.leagues
    assert whitelist.aliases["North American Challengers League"] == "NACL"
    for excluded in ("LCK Cup Group Stage", "LEC Versus Regular Season", "LCS Lock In Group Stage"):
        assert resolve_base_league(whitelist, excluded) not in whitelist.leagues
    assert whitelist.no_live_feed == frozenset(
        {"LPL", "LCK Challengers League", "World Star Challengers Invitational"}
    )


@pytest.mark.parametrize(
    "league",
    [
        "",
        " ",
        "lck",
        "LCK Cup",
        "LCK Cup Playoffs",
        "LEC Versus",
        "LPL Group Ascend Extra",
        "LCS Lock In",
        None,
    ],
)
def test_canonical_filter_rejects_nonmembers(league: str | None) -> None:
    assert not is_allowed_league(load_canonical_league_whitelist(), league)


def test_all_canonical_names_and_aliases_pass_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    whitelist = load_canonical_league_whitelist()
    for name in (*whitelist.leagues, *whitelist.aliases):
        assert is_allowed_league(whitelist, name)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"leagues": [], "aliases": {}},
        {"leagues": "LCK", "aliases": {}},
        {"leagues": [1], "aliases": {}},
        {"leagues": [""], "aliases": {}},
        {"leagues": [" LCK"], "aliases": {}},
        {"leagues": ["LCK", "LCK"], "aliases": {}},
        {"leagues": ["LCK"], "aliases": []},
        {"leagues": ["LCK"], "aliases": {"alias": 1}},
        {"leagues": ["LCK"], "no_live_feed": "LCK", "aliases": {}},
        {"leagues": ["LCK"], "no_live_feed": [1], "aliases": {}},
    ],
)
def test_invalid_whitelist_configuration_raises(tmp_path: Path, payload: object) -> None:
    import_path = tmp_path / "whitelist.json"
    import_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_league_whitelist(import_path)
