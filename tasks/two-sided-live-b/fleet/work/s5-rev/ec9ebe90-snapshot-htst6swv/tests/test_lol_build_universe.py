"""Synthetic Gamma fixtures for the LoL universe stage. No network."""

import json
from collections.abc import Mapping, Sequence
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
import pytest

from lol.constants import (
    REASON_GAME_WINNER,
    REASON_MALFORMED_TOKENS,
    REASON_MATCH_WINNER_DECIDER,
    REASON_MISSING_BEST_OF,
    REASON_SERIES_ONLY,
    REASON_UNRESOLVED_MARKET,
    REASON_UNSUPPORTED_BO2,
    REASON_UNSUPPORTED_CONTRACT,
)
from lol.types import LolUniverseMarketRow
from shared.types.polymarket import UniverseEventsPage
from shared.utils.json_io import write_json
from trader.lol_league_filter import read_event_league


class UniverseModule(Protocol):
    """Importable behavior from the numbered LoL universe stage."""

    def build_universe_rows(self, raw_events: Sequence[object]) -> list[LolUniverseMarketRow]:
        """Classify every raw Gamma market into one parquet row."""
        ...

    def count_reason_totals(self, rows: Sequence[LolUniverseMarketRow]) -> dict[str, int]:
        """Count rows per stable reason string."""
        ...

    def write_gzip_json(self, path: Path, payload: UniverseEventsPage) -> None:
        """Write one Gamma cache page with stable gzip metadata."""
        ...

    def build_universe(self, raw_dir: Path, output_path: Path, fetch: bool) -> None:
        """Optionally fetch Gamma pages, then rebuild markets.parquet from cache."""
        ...


def load_universe() -> UniverseModule:
    """Import the numbered LoL universe module through its typed test surface."""
    return cast(UniverseModule, cast(object, import_module("lol.01_build_universe")))


def cid(n: int) -> str:
    """Build a valid 32-byte condition id from a small integer."""
    return "0x" + f"{n:064x}"


def make_market(
    market_id: str,
    condition_id: str,
    question: str,
    outcomes: list[str],
    token_ids: list[str],
    prices: list[str],
    sports_market_type: str | None,
) -> dict[str, object]:
    """Build one raw Gamma market dict in the shape Market.parse_response expects."""
    payload: dict[str, object] = {
        "id": market_id,
        "conditionId": condition_id,
        "question": question,
        "outcomes": json.dumps(outcomes),
        "clobTokenIds": json.dumps(token_ids),
        "outcomePrices": json.dumps(prices),
        "startDate": "2026-03-01T12:00:00Z",
        "endDate": "2026-03-01T14:00:00Z",
        "gameStartTime": "2026-03-01T12:30:00Z",
        "groupItemTitle": "Game",
    }
    if sports_market_type is not None:
        payload["sportsMarketType"] = sports_market_type
    return payload


def make_event(
    event_id: str,
    title: str,
    markets: list[dict[str, object]],
    teams: list[dict[str, str]],
    score: str | None,
    league: str | None,
) -> dict[str, object]:
    """Build one raw Gamma event dict in the shape Event.parse_response expects."""
    payload: dict[str, object] = {
        "id": event_id,
        "slug": f"event-{event_id}",
        "title": title,
        "startTime": "2026-03-01T12:00:00Z",
        "markets": markets,
    }
    if teams:
        payload["teams"] = [{"id": str(i), **team} for i, team in enumerate(teams, start=1)]
    if score is not None:
        payload["score"] = score
    if league is not None:
        payload["eventMetadata"] = {"league": league}
    return payload


def game_n_market(
    market_id: str,
    condition_id: str,
    game_number: int,
    prices: list[str],
) -> dict[str, object]:
    """Resolved-or-not Game N Winner with valid tokens."""
    return make_market(
        market_id,
        condition_id,
        f"LoL: T1 vs Gen.G Game {game_number} Winner",
        ["T1", "Gen.G"],
        [f"tok-{market_id}-a", f"tok-{market_id}-b"],
        prices,
        "child_moneyline",
    )


def match_winner_market(
    market_id: str,
    condition_id: str,
    prices: list[str],
) -> dict[str, object]:
    """Match Winner with valid tokens."""
    return make_market(
        market_id,
        condition_id,
        "LoL: T1 vs Gen.G Match Winner",
        ["T1", "Gen.G"],
        [f"tok-{market_id}-a", f"tok-{market_id}-b"],
        prices,
        "moneyline",
    )


def row_by_condition(
    rows: Sequence[LolUniverseMarketRow], condition_id: str
) -> LolUniverseMarketRow:
    """Return the unique row for a condition id."""
    matched = [row for row in rows if row["condition_id"] == condition_id]
    assert len(matched) == 1, condition_id
    return matched[0]


def test_explicit_game_n_winner_is_included_when_resolved() -> None:
    """Game 2 Winner with a 1/0 price pair is included as game_winner."""
    condition_id = cid(2)
    event = make_event(
        "e-game2",
        "LoL: T1 vs Gen.G (BO3) - LCK",
        [game_n_market("m2", condition_id, 2, ["1", "0"])],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    rows = load_universe().build_universe_rows([event])
    row = row_by_condition(rows, condition_id)
    assert row["included"] is True
    assert row["reason"] == REASON_GAME_WINNER
    assert row["contract_kind"] == "game_winner"
    assert row["game_number"] == 2
    assert row["resolved_outcome"] == "T1"
    assert row["resolved_outcome_index"] == 0
    assert row["team_a"] == "T1"
    assert row["team_b"] == "Gen.G"
    assert row["best_of"] == 3
    assert row["league"] == "LCK"


def test_bo1_match_winner_is_decider_for_game_1() -> None:
    """BO1 Match Winner with no Game N is the Game 1 market."""
    condition_id = cid(1)
    event = make_event(
        "e-bo1",
        "LoL: T1 vs Gen.G (BO1) - LCK",
        [match_winner_market("m1", condition_id, ["1", "0"])],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    row = row_by_condition(load_universe().build_universe_rows([event]), condition_id)
    assert row["included"] is True
    assert row["reason"] == REASON_MATCH_WINNER_DECIDER
    assert row["contract_kind"] == "match_winner"
    assert row["game_number"] == 1


def test_bo3_match_winner_is_decider_when_game_n_exists() -> None:
    """BO3 Game 1 Winner plus Match Winner: both included; Match Winner is Game 3."""
    game_id = cid(11)
    match_id = cid(13)
    event = make_event(
        "e-bo3",
        "LoL: T1 vs Gen.G (BO3) - LCK",
        [
            game_n_market("g1", game_id, 1, ["1", "0"]),
            match_winner_market("mw", match_id, ["0", "1"]),
        ],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    rows = load_universe().build_universe_rows([event])
    game_row = row_by_condition(rows, game_id)
    match_row = row_by_condition(rows, match_id)
    assert game_row["reason"] == REASON_GAME_WINNER
    assert game_row["included"] is True
    assert match_row["reason"] == REASON_MATCH_WINNER_DECIDER
    assert match_row["included"] is True
    assert match_row["game_number"] == 3
    assert match_row["resolved_outcome"] == "Gen.G"
    assert match_row["resolved_outcome_index"] == 1


def test_bo5_match_winner_is_decider_when_game_n_exists() -> None:
    """BO5 Game 1 Winner plus Match Winner: Match Winner is Game 5."""
    game_id = cid(21)
    match_id = cid(25)
    event = make_event(
        "e-bo5",
        "LoL: T1 vs Gen.G (BO5) - Worlds",
        [
            game_n_market("g1", game_id, 1, ["1", "0"]),
            match_winner_market("mw", match_id, ["1", "0"]),
        ],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "Worlds",
    )
    match_row = row_by_condition(load_universe().build_universe_rows([event]), match_id)
    assert match_row["reason"] == REASON_MATCH_WINNER_DECIDER
    assert match_row["game_number"] == 5
    assert match_row["included"] is True


def test_bo2_is_excluded() -> None:
    """BO2 Game N and Match Winner rows are both unsupported_bo2."""
    game_id = cid(31)
    match_id = cid(32)
    event = make_event(
        "e-bo2",
        "LoL: T1 vs Gen.G (BO2) - LCK",
        [
            game_n_market("g1", game_id, 1, ["1", "0"]),
            match_winner_market("mw", match_id, ["1", "0"]),
        ],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    rows = load_universe().build_universe_rows([event])
    assert {row["reason"] for row in rows} == {REASON_UNSUPPORTED_BO2}
    assert all(row["included"] is False for row in rows)
    assert len(rows) == 2


def test_series_only_bo3_and_bo5_are_excluded() -> None:
    """Match Winner only on BO3 and BO5, with zero Game N, is series_only."""
    bo3_id = cid(41)
    bo5_id = cid(42)
    bo3 = make_event(
        "e-so3",
        "LoL: T1 vs Gen.G (BO3) - LCK",
        [match_winner_market("mw3", bo3_id, ["1", "0"])],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    bo5 = make_event(
        "e-so5",
        "LoL: T1 vs Gen.G (BO5) - LCK",
        [match_winner_market("mw5", bo5_id, ["1", "0"])],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    rows = load_universe().build_universe_rows([bo3, bo5])
    assert row_by_condition(rows, bo3_id)["reason"] == REASON_SERIES_ONLY
    assert row_by_condition(rows, bo5_id)["reason"] == REASON_SERIES_ONLY
    assert all(row["included"] is False for row in rows)


def test_malformed_tokens_are_kept_with_reason() -> None:
    """Token/outcome defects stay in the frame as malformed_tokens, including SDK drops."""
    cases: list[tuple[str, dict[str, object]]] = [
        (
            cid(51),
            make_market(
                "one-tok",
                cid(51),
                "LoL: T1 vs Gen.G Game 1 Winner",
                ["T1", "Gen.G"],
                ["only"],
                ["1", "0"],
                "child_moneyline",
            ),
        ),
        (
            cid(52),
            make_market(
                "dup-tok",
                cid(52),
                "LoL: T1 vs Gen.G Game 1 Winner",
                ["T1", "Gen.G"],
                ["same", "same"],
                ["1", "0"],
                "child_moneyline",
            ),
        ),
        (
            cid(53),
            make_market(
                "empty-tok",
                cid(53),
                "LoL: T1 vs Gen.G Game 1 Winner",
                ["T1", "Gen.G"],
                ["tok", ""],
                ["1", "0"],
                "child_moneyline",
            ),
        ),
        (
            cid(54),
            make_market(
                "one-out",
                cid(54),
                "LoL: T1 vs Gen.G Game 1 Winner",
                ["T1"],
                ["a", "b"],
                ["1", "0"],
                "child_moneyline",
            ),
        ),
        (
            cid(55),
            make_market(
                "empty-out",
                cid(55),
                "LoL: T1 vs Gen.G Game 1 Winner",
                ["T1", ""],
                ["a", "b"],
                ["1", "0"],
                "child_moneyline",
            ),
        ),
    ]
    event = make_event(
        "e-malformed",
        "LoL: T1 vs Gen.G (BO3) - LCK",
        [payload for _, payload in cases],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    rows = load_universe().build_universe_rows([event])
    assert len(rows) == len(cases)
    for condition_id, _ in cases:
        row = row_by_condition(rows, condition_id)
        assert row["included"] is False
        assert row["reason"] == REASON_MALFORMED_TOKENS


def test_resolution_requires_exact_one_and_zero() -> None:
    """Only a strict 1/0 pair resolves; every other pair is unresolved_market."""
    resolved_yes = cid(61)
    resolved_no = cid(62)
    unresolved_ids = [cid(n) for n in range(63, 69)]
    price_cases: list[tuple[str, list[str]]] = [
        (resolved_yes, ["1", "0"]),
        (resolved_no, ["0", "1"]),
        (unresolved_ids[0], ["0.6", "0.4"]),
        (unresolved_ids[1], ["1", "1"]),
        (unresolved_ids[2], ["0", "0"]),
        (unresolved_ids[3], ["1", "0.01"]),
        (unresolved_ids[4], ["0.5", "0.5"]),
    ]
    markets = [
        game_n_market(f"m{condition_id[-2:]}", condition_id, 1, prices)
        for condition_id, prices in price_cases
    ]
    missing = game_n_market("m-miss", unresolved_ids[5], 1, ["1", "0"])
    del missing["outcomePrices"]
    markets.append(missing)
    event = make_event(
        "e-res",
        "LoL: T1 vs Gen.G (BO3) - LCK",
        markets,
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    rows = load_universe().build_universe_rows([event])
    yes_row = row_by_condition(rows, resolved_yes)
    no_row = row_by_condition(rows, resolved_no)
    assert yes_row["included"] is True
    assert yes_row["resolved_outcome"] == "T1"
    assert yes_row["resolved_outcome_index"] == 0
    assert no_row["included"] is True
    assert no_row["resolved_outcome"] == "Gen.G"
    assert no_row["resolved_outcome_index"] == 1
    for condition_id in unresolved_ids:
        row = row_by_condition(rows, condition_id)
        assert row["reason"] == REASON_UNRESOLVED_MARKET
        assert row["included"] is False
        assert row["resolved_outcome"] is None
        assert row["resolved_outcome_index"] is None


def test_unsupported_contract_not_relabeled_unresolved() -> None:
    """A season/future question with 1/0 prices is still unsupported_contract."""
    condition_id = cid(70)
    market = make_market(
        "future",
        condition_id,
        "Will T1 win the 2026 World Championship?",
        ["Yes", "No"],
        ["yes-tok", "no-tok"],
        ["1", "0"],
        None,
    )
    event = make_event(
        "e-future",
        "LoL 2026 Worlds Winner",
        [market],
        [],
        None,
        None,
    )
    row = row_by_condition(load_universe().build_universe_rows([event]), condition_id)
    assert row["included"] is False
    assert row["reason"] == REASON_UNSUPPORTED_CONTRACT
    assert row["contract_kind"] == "other"
    assert row["resolved_outcome"] is None


def test_reason_counts_cover_every_row() -> None:
    """Every input market becomes a row and reason counts sum to the frame length."""
    missing_bo = make_event(
        "e-nobo",
        "LoL: T1 vs Gen.G - LCK",
        [match_winner_market("mw-nobo", cid(80), ["1", "0"])],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    mixed = [
        make_event(
            "e-ok",
            "LoL: T1 vs Gen.G (BO3) - LCK",
            [
                game_n_market("g1", cid(81), 1, ["1", "0"]),
                match_winner_market("mw", cid(82), ["1", "0"]),
            ],
            [{"name": "T1"}, {"name": "Gen.G"}],
            None,
            "LCK",
        ),
        make_event(
            "e-bo2",
            "LoL: T1 vs Gen.G (BO2) - LCK",
            [game_n_market("g-bo2", cid(83), 1, ["1", "0"])],
            [{"name": "T1"}, {"name": "Gen.G"}],
            None,
            "LCK",
        ),
        make_event(
            "e-series",
            "LoL: T1 vs Gen.G (BO3) - LCK",
            [match_winner_market("mw-only", cid(84), ["1", "0"])],
            [{"name": "T1"}, {"name": "Gen.G"}],
            None,
            "LCK",
        ),
        make_event(
            "e-unresolved",
            "LoL: T1 vs Gen.G (BO1) - LCK",
            [game_n_market("g-open", cid(85), 1, ["0.5", "0.5"])],
            [{"name": "T1"}, {"name": "Gen.G"}],
            None,
            "LCK",
        ),
        make_event(
            "e-future",
            "LoL 2026 Worlds Winner",
            [
                make_market(
                    "future",
                    cid(86),
                    "Will T1 win Worlds?",
                    ["Yes", "No"],
                    ["a", "b"],
                    ["1", "0"],
                    None,
                )
            ],
            [],
            None,
            None,
        ),
        make_event(
            "e-malformed",
            "LoL: T1 vs Gen.G (BO3) - LCK",
            [
                make_market(
                    "one-tok",
                    cid(87),
                    "LoL: T1 vs Gen.G Game 1 Winner",
                    ["T1", "Gen.G"],
                    ["only"],
                    ["1", "0"],
                    "child_moneyline",
                )
            ],
            [{"name": "T1"}, {"name": "Gen.G"}],
            None,
            "LCK",
        ),
        missing_bo,
    ]
    module = load_universe()
    rows = module.build_universe_rows(mixed)
    input_markets = sum(len(cast(list[object], event["markets"])) for event in mixed)
    assert len(rows) == input_markets
    totals = module.count_reason_totals(rows)
    assert sum(totals.values()) == len(rows)
    assert totals[REASON_MISSING_BEST_OF] == 1
    assert all(row["reason"] for row in rows)


def test_duplicate_condition_id_is_fatal() -> None:
    """Two markets sharing a non-null condition id abort the run."""
    shared = cid(90)
    event = make_event(
        "e-dup",
        "LoL: T1 vs Gen.G (BO3) - LCK",
        [
            game_n_market("g1", shared, 1, ["1", "0"]),
            game_n_market("g2", shared, 2, ["1", "0"]),
        ],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    with pytest.raises(RuntimeError, match="condition_id"):
        load_universe().build_universe_rows([event])


def test_fetch_writes_open_and_closed_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--fetch stores one page per open/closed chain under events/tag_65_*."""
    module = load_universe()
    calls: list[Mapping[str, object]] = []

    def fake_get_json(
        _client: object, url: str, params: Mapping[str, object] | None = None
    ) -> object:
        query = dict(params or {})
        calls.append({"url": url, **query})
        closed = query.get("closed")
        event_id = "closed-1" if closed == "true" else "open-1"
        condition_id = cid(101) if closed == "true" else cid(102)
        event = make_event(
            event_id,
            "LoL: T1 vs Gen.G (BO1) - LCK",
            [match_winner_market(event_id, condition_id, ["1", "0"])],
            [{"name": "T1"}, {"name": "Gen.G"}],
            None,
            "LCK",
        )
        return {"events": [event], "next_cursor": None}

    monkeypatch.setattr(module, "get_json", fake_get_json)
    raw_dir = tmp_path / "gamma"
    output_path = tmp_path / "markets.parquet"
    module.build_universe(raw_dir, output_path, True)
    closed_page = raw_dir / "events" / "tag_65_closed" / "page_00000.json.gz"
    open_page = raw_dir / "events" / "tag_65_open" / "page_00000.json.gz"
    assert closed_page.is_file()
    assert open_page.is_file()
    assert any(call.get("closed") == "true" for call in calls)
    assert any(call.get("closed") == "false" for call in calls)
    assert output_path.is_file()


def test_build_from_cache_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A seeded gzip cache rebuilds parquet and never calls get_json."""
    module = load_universe()

    def fail_get_json(
        _client: object, url: str, params: Mapping[str, object] | None = None
    ) -> object:
        raise AssertionError(f"get_json should not be called: {url} {params}")

    monkeypatch.setattr(module, "get_json", fail_get_json)
    raw_dir = tmp_path / "gamma"
    closed_dir = raw_dir / "events" / "tag_65_closed"
    open_dir = raw_dir / "events" / "tag_65_open"
    event = make_event(
        "cached",
        "LoL: T1 vs Gen.G (BO1) - LCK",
        [match_winner_market("mw", cid(110), ["1", "0"])],
        [{"name": "T1"}, {"name": "Gen.G"}],
        None,
        "LCK",
    )
    page: UniverseEventsPage = {
        "endpoint": "/events/keyset",
        "source": "tag_65_closed",
        "query": {"limit": 500, "closed": "true", "tag_id": 65},
        "request_cursor": None,
        "next_cursor": None,
        "fetched_at": "2026-08-27T00:00:00+00:00",
        "events": [event],
    }
    module.write_gzip_json(closed_dir / "page_00000.json.gz", page)
    open_page: UniverseEventsPage = {
        **page,
        "source": "tag_65_open",
        "query": {"limit": 500, "closed": "false", "tag_id": 65},
        "events": [],
    }
    module.write_gzip_json(open_dir / "page_00000.json.gz", open_page)
    output_path = tmp_path / "markets.parquet"
    module.build_universe(raw_dir, output_path, False)
    assert output_path.is_file()
    frame = pd.read_parquet(output_path)
    assert len(frame) == 1
    assert bool(frame.iloc[0]["included"]) is True
    assert str(frame.iloc[0]["reason"]) == REASON_MATCH_WINNER_DECIDER
    tmp_sibling = output_path.with_suffix(output_path.suffix + ".tmp")
    assert not tmp_sibling.exists()


@pytest.mark.parametrize(
    "metadata,title,expected",
    [
        ("LCK", "LoL: T1 vs Gen.G (BO3) - LEC", "LCK"),
        ("LCK Cup", "LoL: T1 vs Gen.G (BO3) - LCK", "LCK Cup"),
        (
            None,
            "LoL: T1 vs Gen.G (BO 3) - North American Challengers League",
            "North American Challengers League",
        ),
        ("", "LoL: T1 vs Gen.G (BO3) - LEC", "LEC"),
        ("  LCK   Challengers League  ", "LoL: T1 vs Gen.G (BO3)", "LCK Challengers League"),
        (None, "LoL: T1 vs Gen.G (BO3) - ", None),
    ],
)
def test_stage01_and_live_parse_the_same_event(
    tmp_path: Path, metadata: str | None, title: str, expected: str | None
) -> None:
    event = make_event(
        "123", title, [game_n_market("1", cid(1), 1, ["1", "0"])], [], None, metadata
    )
    write_json(tmp_path / "metadata/events/123.json", event)
    observed = read_event_league(tmp_path, "123")
    rows = load_universe().build_universe_rows([event])
    assert rows
    assert rows[0]["league"] == observed.league == expected
    assert observed.reason is None
