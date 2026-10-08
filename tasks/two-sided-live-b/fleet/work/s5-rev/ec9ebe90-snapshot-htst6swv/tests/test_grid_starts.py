import json
from pathlib import Path

import pytest

from collect import s04_fetch_grid_starts as grid_starts
from collect.s04_fetch_grid_starts import (
    MAX_MAP_LOAD_AFTER_MATCH_START_SECONDS,
    GridGame,
    LinkedMap,
    fetch_series_states,
    index_games_by_clock,
    load_grid_games,
    match_game,
    resolve_window,
)

MATCH_START = 1_700_000_000
DURATION = 2214
MATCH_ID = 101


def build_game(game_id: str, clock_seconds: int, map_load_after_start: int) -> GridGame:
    """Build one GRID game whose map load is `map_load_after_start` seconds after OpenDota start."""
    spawn_ts = MATCH_START + map_load_after_start
    return GridGame(
        game_id=game_id,
        spawn_at="2023-11-14T22:13:20Z",
        spawn_ts=spawn_ts,
        clock_seconds=clock_seconds,
    )


def build_link() -> LinkedMap:
    """The fixed OpenDota map used by the GRID matching tests."""
    return LinkedMap(
        condition_id="map-1",
        match_id=MATCH_ID,
        match_start_time=MATCH_START,
        grid_clock_seconds=DURATION,
    )


def match(games: list[GridGame]) -> GridGame | None:
    """Match the fixed OpenDota match against the given GRID games."""
    return match_game(build_link(), index_games_by_clock(games))


def test_exact_clock_matches() -> None:
    """One exact-clock game inside the map-load window is the answer."""
    result = match([build_game("wanted", DURATION, 800)])
    assert result is not None
    assert result.game_id == "wanted"


def test_one_second_clock_drift_does_not_match() -> None:
    """A one-second clock drift is not an exact GRID/OpenDota match."""
    assert match([build_game("drifted", DURATION - 1, 800)]) is None


def test_two_exact_games_raise() -> None:
    """Two exact-clock games inside the map-load window are ambiguous and fail."""
    games = [build_game("first", DURATION, 500), build_game("second", DURATION, 900)]
    with pytest.raises(ValueError, match="multiple exact GRID games"):
        match(games)


def test_two_second_clock_drift_does_not_match() -> None:
    """A larger clock drift is not a different acceptable match."""
    assert match([build_game("other", DURATION + 2, 800)]) is None


def test_game_outside_the_map_load_window_does_not_match() -> None:
    """A game before or long after the OpenDota start is rejected."""
    assert match([build_game("before", DURATION, -60)]) is None
    late = MAX_MAP_LOAD_AFTER_MATCH_START_SECONDS + 1
    assert match([build_game("late", DURATION, late)]) is None


def test_resolve_window_emits_the_grid_started_at() -> None:
    """A matched game becomes a window row carrying its GRID startedAt verbatim."""
    resolved = resolve_window(
        build_link(), index_games_by_clock([build_game("wanted", DURATION, 800)])
    )
    assert resolved is not None
    assert resolved["condition_id"] == "map-1"
    assert resolved["spawn_at"] == "2023-11-14T22:13:20Z"


def test_resolve_window_without_a_grid_hit_is_none() -> None:
    """No fallback: an unmatched link simply has no GRID window."""
    assert resolve_window(build_link(), {}) is None


SERIES_STATE_PAYLOAD: dict[str, object] = {
    "data": {
        "seriesState": {
            "id": "series-1",
            "started": True,
            "finished": True,
            "startedAt": "2023-11-14T22:10:00Z",
            "updatedAt": "2023-11-14T23:00:00Z",
            "duration": "PT50M",
            "format": "best-of-three",
            "teams": [
                {"id": "t1", "name": "Team One", "score": 2, "won": True},
                {"id": "t2", "name": "Team Two", "score": 1, "won": False},
            ],
            "games": [
                {
                    "id": "game-1",
                    "sequenceNumber": 1,
                    "started": True,
                    "finished": True,
                    "paused": False,
                    "startedAt": "2023-11-14T22:13:20Z",
                    "duration": "PT36M54S",
                    "clock": {
                        "type": "GAME",
                        "ticking": False,
                        "ticksBackwards": False,
                        "currentSeconds": DURATION,
                    },
                    "teams": [
                        {
                            "id": "t1",
                            "name": "Team One",
                            "side": "radiant",
                            "won": True,
                            "score": 30,
                            "kills": 30,
                        },
                        {
                            "id": "t2",
                            "name": "Team Two",
                            "side": "dire",
                            "won": False,
                            "score": 12,
                            "kills": 12,
                        },
                    ],
                }
            ],
        }
    }
}


def test_series_state_cache_keeps_fields_unused_by_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw state cache archives teams, score and duration even though windows ignore them."""
    monkeypatch.setattr(grid_starts, "SERIES_STATE_DIR", tmp_path)
    monkeypatch.setattr(grid_starts, "GRID_REQUEST_SLEEP_SECONDS", 0)

    def fake_graphql(
        token: str, url: str, query: str, variables: dict[str, object]
    ) -> dict[str, object]:
        return SERIES_STATE_PAYLOAD

    monkeypatch.setattr(grid_starts, "grid_graphql", fake_graphql)

    fetch_series_states("token", ["series-1"])

    cached = json.loads((tmp_path / "series-1.json").read_text(encoding="utf-8"))
    assert cached == SERIES_STATE_PAYLOAD
    state = cached["data"]["seriesState"]
    assert state["duration"] == "PT50M"
    assert [team["score"] for team in state["teams"]] == [2, 1]
    assert state["games"][0]["duration"] == "PT36M54S"
    assert [team["name"] for team in state["games"][0]["teams"]] == ["Team One", "Team Two"]

    games = load_grid_games(["series-1"])
    assert len(games) == 1
    assert games[0].game_id == "game-1"
    assert games[0].clock_seconds == DURATION


def test_state_cache_without_series_state_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older caches saved with an empty data object are still read and skipped."""
    monkeypatch.setattr(grid_starts, "SERIES_STATE_DIR", tmp_path)
    (tmp_path / "series-2.json").write_text(json.dumps({"data": {}}), encoding="utf-8")
    assert load_grid_games(["series-2"]) == []


def test_state_cache_with_null_data_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cached payload with data: null is skipped instead of raising."""
    monkeypatch.setattr(grid_starts, "SERIES_STATE_DIR", tmp_path)
    (tmp_path / "series-3.json").write_text(json.dumps({"data": None}), encoding="utf-8")
    assert load_grid_games(["series-3"]) == []
