"""Fixture tests for LoL Stage 05 replay artifacts. No network, no data/lol."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal
from telonex_fixtures import TelonexRawLevels, write_token_book
from test_lol_prepare_dataset import (
    TOK_D,
    TOK_R,
    frame_at,
    make_link,
    run_prepare,
    spawn_frame,
    two_sided,
    write_archive,
)

from lol.constants import (
    LOL_PREPARE_END_SECOND,
    LOL_REPLAY_DRAIN,
    LOL_VALIDATION_START_TIME,
    REASON_ACCEPTED,
)
from lol.types import LolLinkRow
from shared.constants.lol import REASON_MISSING_REQUIRED_CHANNEL
from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS
from shared.utils.telonex_book import US_PER_SECOND

SPAWN_TS = LOL_VALIDATION_START_TIME + 100_000
ANCHOR_TS = SPAWN_TS - 4
SPAWN_DAY = datetime.fromtimestamp(SPAWN_TS, tz=UTC).date().isoformat()
TOK_R3 = "tok-radiant-c"
TOK_D3 = "tok-dire-c"


def hz_frames(spawn: float, last_second: int) -> list[dict[str, object]]:
    """1 Hz spawn-clocked frames with strictly increasing blue gold."""
    frames = [spawn_frame(spawn)]
    for second in range(1, last_second + 1):
        frames.append(frame_at(spawn + second, 500 + second, 500, 1, 1, 0, 0))
    return frames


def coverage_snapshots(spawn_us: int, last_offset_s: int) -> list[tuple[int, float, float]]:
    """Prior at spawn-1s plus quotes every 4s so the 5s age gate never trips."""
    snapshots = [(spawn_us - US_PER_SECOND, 0.60, 0.40)]
    for offset in range(0, last_offset_s + 1, 4):
        snapshots.append((spawn_us + offset * US_PER_SECOND, 0.60, 0.40))
    return snapshots


def write_dated_pair_books(
    root: Path,
    token_r: str,
    token_d: str,
    day: str,
    snapshots: list[tuple[int, float, float]],
) -> None:
    """Write radiant/dire books at the given (timestamp_us, radiant_mid, dire_mid)."""
    radiant_rows: list[tuple[int, TelonexRawLevels, TelonexRawLevels]] = []
    dire_rows: list[tuple[int, TelonexRawLevels, TelonexRawLevels]] = []
    for stamp, radiant_mid, dire_mid in snapshots:
        radiant_bid, radiant_ask = two_sided(radiant_mid)
        dire_bid, dire_ask = two_sided(dire_mid)
        radiant_rows.append((stamp, radiant_bid, radiant_ask))
        dire_rows.append((stamp, dire_bid, dire_ask))
    write_token_book(root, token_id=token_r, rows=radiant_rows, day=day)
    write_token_book(root, token_id=token_d, rows=dire_rows, day=day)


def write_min_trade_days(
    root: Path, token_ids: Sequence[str], day: str, spawn_us: int, _last_second: int
) -> None:
    """Write one fill per token so the onchain channel exists for the replay day."""
    frame = pd.DataFrame({"block_timestamp_us": [spawn_us]})
    for token_id in token_ids:
        path = root / "onchain_fills" / f"asset_id={token_id}" / f"{day}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)


def validation_link(esports_game_id: str, event_id: str, game_number: int = 1) -> LolLinkRow:
    """Accepted Game N winner whose spawn lands in the validation event split."""
    return make_link(esports_game_id, event_id, game_number, ANCHOR_TS, "T1", 0)


def link_with_tokens(game_id: str, event_id: str, token_r: str, token_d: str) -> LolLinkRow:
    """Validation Game N winner with caller-chosen CLOB tokens."""
    link = validation_link(game_id, event_id)
    link["clob_token_ids_json"] = json.dumps([token_r, token_d])
    return link


def prepare_validation_card(
    tmp_path: Path,
    links: list[LolLinkRow],
    windows: Path,
    telonex: Path,
) -> Path:
    """Run Stage 05 so accepted maps land in the validation split."""
    return run_prepare(tmp_path, links, windows, telonex)


def test_inference_rows_past_540_carry_labels(tmp_path: Path) -> None:
    """Post-540 rows carry a +300s label while books reach the target; the tape tail does not."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", hz_frames(float(SPAWN_TS), 1000))
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 1300)
    )
    write_min_trade_days(telonex, [TOK_R, TOK_D], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 1000)
    links = [validation_link("1001", "e1")]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    signals = pd.read_parquet(out / "validation.parquet")
    seconds = set(int(value) for value in signals["second"])
    assert 541 in seconds
    assert 899 in seconds
    label = "signal_market_p_radiant_300s"
    labeled = signals.loc[signals["second"].isin([541, 899])]
    assert labeled[label].notna().all()
    gold_541 = signals.loc[signals["second"] == 541, "radiant_nw"].iloc[0]
    assert int(gold_541) == (500 + 541) * 5
    tail = signals.loc[signals["second"] == 1000]
    assert not tail.empty
    assert tail[label].isna().all()


def test_pause_adjusted_timestamps(tmp_path: Path) -> None:
    """Signal second 1 uses the post-pause frame wall; market second 1 uses spawn+1+pause."""
    windows = tmp_path / "windows"
    loading = float(SPAWN_TS - 6)
    mid = float(SPAWN_TS + 1)
    post_pause = float(SPAWN_TS + 21)
    write_archive(
        windows,
        "1001",
        [
            frame_at(loading, 0, 0, 1, 1, 0, 0),
            spawn_frame(float(SPAWN_TS)),
            frame_at(mid, 700, 500, 1, 1, 0, 0),
            frame_at(post_pause, 700, 500, 1, 1, 0, 0),
            frame_at(float(SPAWN_TS + 300), 800, 500, 1, 1, 0, 0),
        ],
    )
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 400)
    )
    write_min_trade_days(telonex, [TOK_R, TOK_D], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 300)
    links = [validation_link("1001", "e1")]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    signals = pd.read_parquet(out / "validation.parquet")
    markets = pd.read_parquet(out / "market_seconds.parquet")
    signal_ts = int(signals.loc[signals["second"] == 1, "state_ts_us"].iloc[0])
    market_ts = int(markets.loc[markets["second"] == 1, "state_ts_us"].iloc[0])
    spawn_plus_one = round((SPAWN_TS + 1) * 1_000_000)
    assert signal_ts == round(post_pause * 1_000_000)
    assert market_ts == round((SPAWN_TS + 1 + 20) * 1_000_000)
    assert signal_ts != spawn_plus_one
    assert market_ts != spawn_plus_one


def test_map_past_prepare_end_stops_at_last_frame(tmp_path: Path) -> None:
    """Frames through 1500 grid to map end; leftover settles there, not the 7200 cap."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", hz_frames(float(SPAWN_TS), 1500))
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 2200)
    )
    write_min_trade_days(telonex, [TOK_R, TOK_D], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 1500)
    links = [validation_link("1001", "e1")]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    signals = pd.read_parquet(out / "validation.parquet")
    markets = pd.read_parquet(out / "market_seconds.parquet")
    audit = pd.read_parquet(out / "backtest_audit.parquet").iloc[0]
    signal_seconds = set(int(value) for value in signals["second"])
    market_seconds = set(int(value) for value in markets["second"])
    assert 1500 in signal_seconds
    assert LOL_PREPARE_END_SECOND not in signal_seconds
    assert int(signals["second"].max()) <= 1500 + 2
    assert 0 in market_seconds
    last_wall = float(SPAWN_TS + 1500)
    assert 1500 in market_seconds
    assert 1501 not in market_seconds
    assert LOL_PREPARE_END_SECOND not in market_seconds
    assert "still_going" not in audit.index
    assert "cutoff_ts" not in audit.index
    assert int(audit["game_ended_at_ts"]) == round(last_wall)
    assert bool(audit["eligible"]) is True


def test_early_game_end(tmp_path: Path) -> None:
    """Game ending at 400s (one full tape bucket) stops market seconds there and does not apply cutoff."""
    windows = tmp_path / "windows"
    last_wall = float(SPAWN_TS + 400)
    write_archive(windows, "1001", hz_frames(float(SPAWN_TS), 400))
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 800)
    )
    write_min_trade_days(telonex, [TOK_R, TOK_D], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 400)
    links = [validation_link("1001", "e1")]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    signals = pd.read_parquet(out / "validation.parquet")
    markets = pd.read_parquet(out / "market_seconds.parquet")
    audit = pd.read_parquet(out / "backtest_audit.parquet").iloc[0]
    assert int(markets["second"].max()) == 400
    assert int(signals["second"].max()) <= 402
    assert not any(int(value) > 540 for value in signals["second"])
    assert "still_going" not in audit.index
    assert "cutoff_ts" not in audit.index
    assert int(audit["game_ended_at_ts"]) == round(last_wall)
    assert int(audit["replay_end_ts"]) == round(last_wall + LOL_REPLAY_DRAIN.total_seconds())
    assert bool(audit["eligible"]) is True


def test_age_gaps_not_interpolated(tmp_path: Path) -> None:
    """A 3.5s hole after 541 drops that integer second; neighbors keep their own gold."""
    windows = tmp_path / "windows"
    frames = hz_frames(float(SPAWN_TS), 541)
    frames.append(frame_at(float(SPAWN_TS) + 541 + 3.5, 500 + 542, 500, 1, 1, 0, 0))
    for second in range(545, 551):
        frames.append(frame_at(float(SPAWN_TS) + second, 500 + second, 500, 1, 1, 0, 0))
    write_archive(windows, "1001", frames)
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 900)
    )
    write_min_trade_days(telonex, [TOK_R, TOK_D], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 550)
    links = [validation_link("1001", "e1")]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    signals = pd.read_parquet(out / "validation.parquet")
    seconds = set(int(value) for value in signals["second"])
    assert 543 in seconds
    assert 544 not in seconds
    assert 545 in seconds
    gold_543 = int(signals.loc[signals["second"] == 543, "radiant_nw"].iloc[0])
    gold_545 = int(signals.loc[signals["second"] == 545, "radiant_nw"].iloc[0])
    assert gold_543 == (500 + 541) * 5
    assert gold_545 == (500 + 545) * 5


def test_stable_exclusion_reasons(tmp_path: Path) -> None:
    """Missing onchain_fills is a prepare drop; a sibling with fills stays validation-eligible."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", hz_frames(float(SPAWN_TS), 10))
    write_archive(windows, "1003", hz_frames(float(SPAWN_TS), 400))
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 800)
    )
    write_dated_pair_books(
        telonex, TOK_R3, TOK_D3, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 800)
    )
    write_min_trade_days(telonex, [TOK_R3, TOK_D3], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 400)
    links = [
        link_with_tokens("1001", "e1", TOK_R, TOK_D),
        link_with_tokens("1003", "e3", TOK_R3, TOK_D3),
    ]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    prepare_audit = pd.read_parquet(out / "audit.parquet")
    by_prepare = {int(row["match_id"]): row for _, row in prepare_audit.iterrows()}
    assert by_prepare[1001]["reason"] == REASON_MISSING_REQUIRED_CHANNEL
    assert bool(by_prepare[1001]["included"]) is False
    backtest = pd.read_parquet(out / "backtest_audit.parquet")
    assert list(backtest["match_id"]) == [1003]
    accepted = backtest.iloc[0]
    assert accepted["reason"] == REASON_ACCEPTED
    assert bool(accepted["eligible"]) is True
    assert float(accepted["ok_quote_fraction"]) >= 0.0


def test_outcome_leakage_guard(tmp_path: Path) -> None:
    """Opposite resolved outcomes do not change inference features or market_p_radiant."""
    windows = tmp_path / "windows"
    frames = hz_frames(float(SPAWN_TS), 600)
    write_archive(windows, "1001", frames)
    write_archive(windows, "1002", frames)
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 1000)
    )
    write_min_trade_days(telonex, [TOK_R, TOK_D], SPAWN_DAY, SPAWN_TS * US_PER_SECOND, 600)
    links = [
        make_link("1001", "e1", 1, ANCHOR_TS, "T1", 0),
        make_link("1002", "e2", 1, ANCHOR_TS, "Gen.G", 1),
    ]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    signals = pd.read_parquet(out / "validation.parquet")
    left = signals.loc[(signals["match_id"] == 1001) & (signals["second"] > 540)]
    right = signals.loc[(signals["match_id"] == 1002) & (signals["second"] > 540)]
    feature_cols = [c for c in DOTA_XP_FEATURE_COLUMNS if c in signals.columns]
    assert_frame_equal(
        left[feature_cols].reset_index(drop=True),
        right[feature_cols].reset_index(drop=True),
        check_dtype=False,
    )
    assert left["signal_market_p_radiant_300s"].notna().all()
    assert right["signal_market_p_radiant_300s"].notna().all()
    assert (
        left["signal_market_p_radiant_300s"]
        .reset_index(drop=True)
        .equals(right["signal_market_p_radiant_300s"].reset_index(drop=True))
    )
    assert set(left["radiant_win"].unique()) != set(right["radiant_win"].unique())


def test_zero_eligible_still_publishes_audit(tmp_path: Path) -> None:
    """A validation map missing onchain_fills is a prepare drop; backtest_audit is empty."""
    windows = tmp_path / "windows"
    write_archive(windows, "1001", hz_frames(float(SPAWN_TS), 10))
    telonex = tmp_path / "telonex"
    write_dated_pair_books(
        telonex, TOK_R, TOK_D, SPAWN_DAY, coverage_snapshots(SPAWN_TS * US_PER_SECOND, 400)
    )
    links = [validation_link("1001", "e1")]
    out = prepare_validation_card(tmp_path, links, windows, telonex)
    prepare_audit = pd.read_parquet(out / "audit.parquet").iloc[0]
    assert prepare_audit["reason"] == REASON_MISSING_REQUIRED_CHANNEL
    assert bool(prepare_audit["included"]) is False
    backtest = pd.read_parquet(out / "backtest_audit.parquet")
    assert backtest.empty
