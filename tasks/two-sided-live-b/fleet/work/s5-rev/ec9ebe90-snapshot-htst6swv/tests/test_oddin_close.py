"""Boot close writes final and session_end, and leaves cleanup for the fence."""

import json
from pathlib import Path

import pytest

from trader.match_meta import read_match_meta
from trader.oddin_close import close_ended_oddin_archives
from trader.paths import EXECUTION_CLEANUP_FILENAME, MATCH_META_FILENAME, SESSION_JOURNAL_FILENAME


def _meta(match_id: str, oddin_match_id: str) -> dict[str, object]:
    return {
        "schema_version": 9,
        "match_id": match_id,
        "game": "dota",
        "steam_match_id": match_id,
        "server_steam_id": None,
        "league_id": None,
        "tournament": None,
        "teams": {"radiant": "A", "dire": "B"},
        "map_number": 3,
        "joined_at_second": 0,
        "joined_at_utc": "2026-10-06T12:00:00Z",
        "horn_at_utc": "2026-10-06T12:00:00Z",
        "market": {
            "condition_id": "c",
            "market_slug": "s",
            "event_slug": "e",
            "yes_token_id": "yes",
            "no_token_id": "no",
            "yes_is_radiant": False,
            "outcome_0_name": "A",
            "outcome_1_name": "B",
            "tick_size": None,
            "min_order_size": None,
            "neg_risk": False,
            "grid_series_id": None,
        },
        "model": {"name": "m", "trained_at": "t"},
        "feed_source": "oddin",
        "steam_delay_s": None,
        "steam_observed_lag_s": None,
        "grid_delay_s": None,
        "oddin_match_id": oddin_match_id,
        "oddin_delay_s": None,
        "record_only": True,
        "final": None,
    }


def _write(root: Path, match_id: str, oddin_match_id: str) -> None:
    archive = root / match_id
    archive.mkdir()
    (archive / MATCH_META_FILENAME).write_text(
        json.dumps(_meta(match_id, oddin_match_id)), encoding="utf-8"
    )


def test_boot_close_writes_final_without_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("trader.match_meta.TRADER_DIR", tmp_path)
    _write(tmp_path, "9001", "od:match:closed")
    _write(tmp_path, "9002", "od:match:live")
    wallet = tmp_path / "wallet"
    wallet.mkdir()
    (wallet / MATCH_META_FILENAME).write_text("{", encoding="utf-8")

    def ended(oddin_match_id: str, map_number: int) -> bool:
        return oddin_match_id == "od:match:closed" and map_number == 3

    def size(_token_id: str) -> float:
        return 0.0

    def cash(_tokens: set[str]) -> float:
        return -50.0

    close_ended_oddin_archives(tmp_path, ended, size, cash, "live")

    closed = read_match_meta(tmp_path / "9001" / MATCH_META_FILENAME)
    assert closed["final"] is not None
    journal = (tmp_path / "9001" / SESSION_JOURNAL_FILENAME).read_text(encoding="utf-8")
    assert '"kind": "session_end"' in journal or '"kind":"session_end"' in journal
    assert not (tmp_path / "9001" / EXECUTION_CLEANUP_FILENAME).exists()
    still_open = read_match_meta(tmp_path / "9002" / MATCH_META_FILENAME)
    assert still_open["final"] is None
