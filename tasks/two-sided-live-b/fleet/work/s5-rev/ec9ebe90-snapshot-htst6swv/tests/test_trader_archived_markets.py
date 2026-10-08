"""match.json is the durable token index; the process keeps no bounded cache."""

import json
from pathlib import Path

from trader.archived_markets import ArchivedMarketIndex, iter_archived_markets, read_archived_market

YES = "yes-token"
NO = "no-token"
CID = "0xcondition"
MATCH = "grid-3002598-m4"


def _write_meta(archive_dir: Path, match_id: str, yes: str, no: str) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "match_id": match_id,
        "market": {"condition_id": CID, "yes_token_id": yes, "no_token_id": no},
    }
    (archive_dir / "match.json").write_text(json.dumps(document), encoding="utf-8")
    return archive_dir


def test_index_resolves_a_token_written_before_this_process_started(tmp_path: Path) -> None:
    """A fill for a match that closed before the restart still finds its archive."""
    archive = _write_meta(tmp_path / MATCH, MATCH, YES, NO)
    index = ArchivedMarketIndex((tmp_path,))
    market = index.find(NO)
    assert market is not None
    assert market.archive_dir == archive
    assert market.tokens == frozenset({YES, NO})


def test_index_rescans_for_a_match_created_after_the_first_miss(tmp_path: Path) -> None:
    """The index is not sealed by an early miss; a later match still resolves."""
    index = ArchivedMarketIndex((tmp_path,))
    assert index.find(YES) is None
    _write_meta(tmp_path / MATCH, MATCH, YES, NO)
    market = index.find(YES)
    assert market is not None
    assert market.match_id == MATCH


def test_unreadable_and_wallet_dirs_are_skipped(tmp_path: Path) -> None:
    """A wallet dir and a truncated match.json do not break the scan."""
    _write_meta(tmp_path / MATCH, MATCH, YES, NO)
    (tmp_path / "wallet").mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "match.json").write_text("{not json", encoding="utf-8")
    assert read_archived_market(broken) is None
    found = iter_archived_markets((tmp_path,))
    assert [market.match_id for market in found] == [MATCH]
