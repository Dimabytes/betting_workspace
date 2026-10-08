import gzip
import json
from collections.abc import Mapping
from pathlib import Path

import pandas as pd
import pytest

from collect import s05a_fetch_prices_history as prices_history
from shared.constants.api import QUOTE_TRAILING_SECONDS
from shared.types.opendota import OpenDotaPause
from shared.types.polymarket import PricePoint, PricesHistoryPayload
from shared.utils.match_time import HORN_OFFSET_SECONDS
from shared.utils.parsing import parse_ts
from shared.utils.price_history import last_aligned_pre_anchor_pair, last_pre_anchor_quote


def _point(quote_ts: int, price: float) -> PricePoint:
    """One prices-history point."""
    return {"t": quote_ts, "p": price}


def test_last_pre_anchor_quote_takes_the_latest_point_strictly_before_anchor() -> None:
    """A point on the anchor is ignored; the newest earlier point wins."""
    history = [_point(10, 0.40), _point(20, 0.51), _point(30, 0.60), _point(25, 0.55)]

    quote = last_pre_anchor_quote(history, 30)

    assert quote is not None
    assert quote.quote_ts == 25
    assert quote.price == 0.55


def test_last_pre_anchor_quote_returns_none_when_every_point_is_on_or_after_anchor() -> None:
    """No quote exists when the history starts at the anchor."""
    history = [_point(30, 0.50), _point(40, 0.60)]

    assert last_pre_anchor_quote(history, 30) is None
    assert last_pre_anchor_quote([], 30) is None


def test_last_aligned_pre_anchor_pair_uses_the_older_print() -> None:
    """Independent lasts that glue two minutes realign to the older second."""
    anchor = 100
    yes_history = [_point(anchor - 60, 0.53), _point(anchor + 2, 0.475)]
    no_history = [_point(anchor - 61, 0.47), _point(anchor - 1, 0.525)]

    pair = last_aligned_pre_anchor_pair(yes_history, no_history, anchor)

    assert pair is not None
    assert pair.left.quote_ts == anchor - 60
    assert pair.left.price == 0.53
    assert pair.right.quote_ts == anchor - 61
    assert pair.right.price == 0.47


def test_last_aligned_pre_anchor_pair_keeps_same_bar_jitter() -> None:
    """CLOB same-bar stamps a few seconds apart stay independent lasts."""
    anchor = 1787655041
    yes_history = [_point(1787654953, 0.29), _point(1787655020, 0.37)]
    no_history = [_point(1787654952, 0.71), _point(1787655016, 0.63)]

    pair = last_aligned_pre_anchor_pair(yes_history, no_history, anchor)

    assert pair is not None
    assert pair.left.quote_ts == 1787655020
    assert pair.left.price == 0.37
    assert pair.right.quote_ts == 1787655016
    assert pair.right.price == 0.63


@pytest.fixture
def price_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the loader at temporary universe, link, window and cache paths."""
    monkeypatch.setattr(prices_history, "UNIVERSE_PATH", tmp_path / "universe.parquet")
    monkeypatch.setattr(prices_history, "MATCH_LINKS_PATH", tmp_path / "links.parquet")
    monkeypatch.setattr(prices_history, "GRID_GAME_WINDOWS_PATH", tmp_path / "windows.parquet")
    monkeypatch.setattr(
        prices_history, "RAW_POLYMARKET_DOTA_PRICES_HISTORY_DIR", tmp_path / "prices"
    )
    return tmp_path


def write_universe(tmp_path: Path, rows: list[dict[str, str | None]]) -> None:
    """Write the three universe columns the price loader reads."""
    pd.DataFrame(rows, columns=["conditionId", "token_id_0", "token_id_1"]).to_parquet(
        tmp_path / "universe.parquet", index=False
    )


def write_links(tmp_path: Path, radiant_token_index: int) -> None:
    """Write one link for condition `cond-1` and match 1."""
    pd.DataFrame(
        [
            {
                "map_condition_id": "cond-1",
                "match_id": 1,
                "radiant_token_index": radiant_token_index,
            }
        ]
    ).to_parquet(tmp_path / "links.parquet", index=False)


def write_cache(tmp_path: Path, token_id: str, history: list[PricePoint], anchor_ts: int) -> None:
    """Write one raw prices-history payload covering the whole quote window."""
    path = tmp_path / "prices" / f"cond-1_{token_id}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: PricesHistoryPayload = {
        "conditionId": "cond-1",
        "token_id": token_id,
        "startTs": anchor_ts - QUOTE_TRAILING_SECONDS,
        "endTs": anchor_ts,
        "fetched_at": "2026-01-01T00:00:00Z",
        "history": history,
    }
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)


def test_load_anchors_skips_no_spawn_rows_with_unobserved_pauses(
    price_paths: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw horn is never the anchor: pauses=None skips the row, and a known
    pause list anchors at horn - 90 - pre-horn pause seconds."""
    pd.DataFrame([{"condition_id": "cond-spawn", "spawn_at": "2026-01-01T00:10:00Z"}]).to_parquet(
        price_paths / "windows.parquet", index=False
    )
    horn = "2026-01-01T00:20:00Z"
    pd.DataFrame(
        [
            {"map_condition_id": "cond-spawn", "match_id": 3, "archive_horn_at_utc": horn},
            {"map_condition_id": "cond-paused", "match_id": 1, "archive_horn_at_utc": horn},
            {"map_condition_id": "cond-unseen", "match_id": 2, "archive_horn_at_utc": horn},
        ]
    ).to_parquet(price_paths / "links.parquet", index=False)
    pauses: dict[int, list[OpenDotaPause] | None] = {
        1: [{"time": -40, "duration": 7}],
        2: None,
    }

    def fake_pauses(
        _links: pd.DataFrame, _spawn: Mapping[str, int]
    ) -> dict[int, list[OpenDotaPause] | None]:
        return pauses

    monkeypatch.setattr(prices_history, "_fallback_pauses", fake_pauses)

    anchors = prices_history.load_anchors()

    horn_unix = parse_ts(horn)
    assert horn_unix is not None
    assert anchors == {
        "cond-spawn": parse_ts("2026-01-01T00:10:00Z"),
        "cond-paused": horn_unix - HORN_OFFSET_SECONDS - 7,
    }


def build_target(anchor_ts: int) -> prices_history.PriceTarget:
    """One price target for condition `cond-1`."""
    return prices_history.PriceTarget(
        condition_id="cond-1",
        match_id=1,
        anchor_ts=anchor_ts,
        radiant_token_id="tok-r",
        dire_token_id="tok-d",
    )


def test_token_pairs_skip_a_contract_without_both_tokens(price_paths: Path) -> None:
    """A half-filled pair is dropped instead of turning into 'None' strings."""
    write_universe(
        price_paths,
        [
            {"conditionId": "cond-1", "token_id_0": "tok-a", "token_id_1": "tok-b"},
            {"conditionId": "cond-2", "token_id_0": "tok-c", "token_id_1": None},
        ],
    )

    pairs = prices_history.load_universe_token_pairs({"cond-1", "cond-2"})

    assert pairs == {"cond-1": ["tok-a", "tok-b"]}


def test_build_targets_orients_the_pair_by_the_radiant_token_index(price_paths: Path) -> None:
    """Radiant index 1 makes token_id_1 the radiant side."""
    write_links(price_paths, radiant_token_index=1)

    targets = prices_history.build_targets({"cond-1": 1_000_000}, {"cond-1": ["tok-a", "tok-b"]})

    assert len(targets) == 1
    assert targets[0].radiant_then_dire == ("tok-b", "tok-a")
    assert targets[0].window_start_ts == 1_000_000 - QUOTE_TRAILING_SECONDS


def test_build_targets_rejects_a_linked_condition_without_a_token_pair(price_paths: Path) -> None:
    """A linked market with no published pair is an error, not a silent skip."""
    write_links(price_paths, radiant_token_index=0)

    with pytest.raises(ValueError, match="no complete token pair"):
        prices_history.build_targets({"cond-1": 1_000_000}, {})


@pytest.mark.parametrize(
    ("cached_start", "cached_end", "covered"),
    [(-1, 0, True), (0, 0, True), (1, 0, False), (0, -1, False)],
)
def test_cache_covers_only_a_window_it_fully_contains(
    cached_start: int, cached_end: int, covered: bool
) -> None:
    """The window edges themselves count as covered."""
    anchor = 1_000_000
    target = build_target(anchor)
    payload: PricesHistoryPayload = {
        "conditionId": "cond-1",
        "token_id": "tok-r",
        "startTs": target.window_start_ts + cached_start,
        "endTs": anchor + cached_end,
        "fetched_at": "2026-01-01T00:00:00Z",
        "history": [],
    }

    assert prices_history.cache_covers_window(payload, target) is covered
    assert prices_history.cache_covers_window(None, target) is False


def test_collect_quotes_publishes_the_last_aligned_pair_before_the_anchor(
    price_paths: Path,
) -> None:
    """A point exactly on the anchor is ignored; the earlier aligned pair wins."""
    anchor = 1_000_000
    write_cache(price_paths, "tok-r", [_point(anchor - 60, 0.6), _point(anchor, 0.9)], anchor)
    write_cache(price_paths, "tok-d", [_point(anchor - 60, 0.4), _point(anchor, 0.1)], anchor)

    rows = prices_history.collect_quotes([build_target(anchor)])

    assert rows == [
        {
            "condition_id": "cond-1",
            "match_id": 1,
            "anchor_ts": anchor,
            "radiant_prior": 0.6,
            "radiant_price": 0.6,
            "dire_price": 0.4,
            "radiant_quote_ts": anchor - 60,
            "dire_quote_ts": anchor - 60,
        }
    ]


def test_collect_quotes_skips_a_missing_cache_side(price_paths: Path) -> None:
    """One cached token without its counterpart yields no prior."""
    anchor = 1_000_000
    write_cache(price_paths, "tok-r", [_point(anchor - 60, 0.6)], anchor)

    assert prices_history.collect_quotes([build_target(anchor)]) == []


def test_collect_quotes_skips_an_inconsistent_pair_sum(price_paths: Path) -> None:
    """Two prices that do not complement each other cannot become a prior."""
    anchor = 1_000_000
    write_cache(price_paths, "tok-r", [_point(anchor - 60, 0.6)], anchor)
    write_cache(price_paths, "tok-d", [_point(anchor - 60, 0.6)], anchor)

    assert prices_history.collect_quotes([build_target(anchor)]) == []


def test_collect_quotes_skips_a_pair_quoted_only_on_the_anchor(price_paths: Path) -> None:
    """Without a strictly earlier print there is no pre-game prior."""
    anchor = 1_000_000
    write_cache(price_paths, "tok-r", [_point(anchor, 0.6)], anchor)
    write_cache(price_paths, "tok-d", [_point(anchor, 0.4)], anchor)

    assert prices_history.collect_quotes([build_target(anchor)]) == []
