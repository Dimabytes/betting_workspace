from pathlib import Path

import pandas as pd
import pytest

from collect.common import window_ids


def write_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links: pd.DataFrame) -> None:
    """Point the admitted-worklist loader at temporary links and GRID window tables."""
    windows = pd.DataFrame({"condition_id": ["c1", "c2", "c3"]})
    windows_path = tmp_path / "windows.parquet"
    links_path = tmp_path / "links.parquet"
    windows.to_parquet(windows_path, index=False)
    links.to_parquet(links_path, index=False)
    monkeypatch.setattr(window_ids, "GRID_GAME_WINDOWS_PATH", windows_path)
    monkeypatch.setattr(window_ids, "MATCH_LINKS_PATH", links_path)


def test_admitted_ids_order_by_sort_ts_then_match_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windowed links admit by sort_ts; unwindowed links without an archive drop out."""
    links = pd.DataFrame(
        {
            "match_id": [30, 10, 20, 40],
            "map_condition_id": ["c2", "c1", "c1", "other"],
            "sort_ts": [100, 200, 100, 50],
            "archive_id": [None, None, None, None],
        }
    )
    write_tables(tmp_path, monkeypatch, links)

    assert window_ids.load_admitted_match_ids() == [20, 30, 10]


def test_archive_attached_link_admits_without_a_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link with archive_id but no GRID window is admitted by the archive leg."""
    links = pd.DataFrame(
        {
            "match_id": [30, 50],
            "map_condition_id": ["c2", "other"],
            "sort_ts": [100, 10],
            "archive_id": [None, "grid-1-m1"],
        }
    )
    write_tables(tmp_path, monkeypatch, links)

    assert window_ids.load_admitted_match_ids() == [50, 30]


def test_admitted_ids_empty_when_no_link_is_windowed_or_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No overlap with GRID windows and no archives yields an empty worklist."""
    links = pd.DataFrame(
        {
            "match_id": [1],
            "map_condition_id": ["other"],
            "sort_ts": [10],
            "archive_id": [None],
        }
    )
    write_tables(tmp_path, monkeypatch, links)

    assert window_ids.load_admitted_match_ids() == []
