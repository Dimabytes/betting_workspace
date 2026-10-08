"""Duplicate live_paper / trader_live dirs are one market in the fill-gap report."""

import json
from pathlib import Path

from backfill_trader_fills import (
    ClobChainCash,
    build_gap_rows,
    group_by_match,
    journal_cash,
    market_by_token,
)

from trader.archived_markets import ArchivedMarket

YES = "yes-token"
NO = "no-token"
CID = "0xcondition"
MATCH = "8974185539"
FILL_KEY = "fill-a"


def _market(root: Path, match_id: str, name: str) -> ArchivedMarket:
    archive = root / name
    archive.mkdir(parents=True)
    return ArchivedMarket(match_id, archive, CID, YES, NO)


def _write_journal(archive: Path, lines: list[dict[str, object]]) -> None:
    (archive / "session.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in lines),
        encoding="utf-8",
    )


def test_gap_rows_merge_duplicate_match_dirs(tmp_path: Path) -> None:
    """Empty trader_live clone plus live_paper fills is one row, not a $0.77 hole."""
    live = _market(tmp_path, MATCH, "trader_live")
    paper = _market(tmp_path, MATCH, "live_paper")
    _write_journal(live.archive_dir, [{"kind": "session_start", "execution_mode": "live"}])
    _write_journal(
        paper.archive_dir,
        [
            {"kind": "session_start", "execution_mode": "live"},
            {
                "kind": "fill",
                "token_id": YES,
                "side": "BUY",
                "price": 0.65,
                "size": 1.1846153846153846,
                "fill_key": FILL_KEY,
            },
        ],
    )
    chain = ClobChainCash({YES: -0.77, NO: 0.0})
    rows = build_gap_rows(group_by_match((live, paper)), chain, None)
    assert len(rows) == 1
    assert rows[0].match_id == MATCH
    assert abs(rows[0].journal - -0.77) < 0.01
    assert abs(rows[0].chain - -0.77) < 0.01


def test_journal_cash_counts_a_copied_late_fill_once(tmp_path: Path) -> None:
    """The same fill_key in both copies does not double journal cash."""
    live = _market(tmp_path, MATCH, "a")
    paper = _market(tmp_path, MATCH, "b")
    fill: dict[str, object] = {
        "kind": "fill",
        "token_id": YES,
        "side": "SELL",
        "price": 0.5,
        "size": 2.0,
        "fill_key": FILL_KEY,
    }
    late = {**fill, "kind": "late_fill"}
    _write_journal(live.archive_dir, [late])
    _write_journal(paper.archive_dir, [fill])
    assert journal_cash((live.archive_dir, paper.archive_dir), frozenset({YES, NO})) == 1.0


def test_market_by_token_keeps_the_first_archive(tmp_path: Path) -> None:
    """Apply writes into trader_live when the leftover tape also has the match."""
    live = _market(tmp_path, MATCH, "trader_live")
    paper = _market(tmp_path, MATCH, "live_paper")
    mapped = market_by_token((live, paper))
    assert mapped[YES] is live
    assert mapped[NO] is live
