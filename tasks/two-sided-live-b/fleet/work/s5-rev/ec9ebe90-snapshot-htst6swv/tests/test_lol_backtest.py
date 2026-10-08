"""Fixture tests for LoL backtest dispatch, Telonex source pick, and shared report path."""

import gzip
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

import backtest.lol_inputs as lol_inputs
import backtest.run as backtest_run
from backtest.context import (
    MarketContext,
    ReplayWindow,
    calculate_clock_end,
    calculate_replay_window,
)
from backtest.extraction_identity import sha256_text
from backtest.lol_inputs import (
    assert_lol_source_lag,
    build_lol_book_weights,
    load_lol_replay_lookups,
    load_lol_selection,
    synthetic_cutoff_pauses,
)
from backtest.marks import MidSeries
from backtest.paths import BACKTESTS_DIR
from backtest.results import assert_manifest_matches
from backtest.run import (
    BACKTEST_LEVEL_USDC,
    EXPERIMENT_NAME,
    build_run_manifest,
    build_whitelist_manifest_keys,
    parse_args,
)
from backtest.shared_archive import empty_shared_archive
from backtest.signals import LIVE_GRID_TIMING, build_match_signals
from backtest.telonex_local import (
    ONCHAIN_CHANNEL,
    book_rows_in_window,
    has_local_telonex_days,
    link_market_tokens,
)
from shared.constants.lol import LOL_REPLAY_LEAD, LOL_SOURCE_LAG_SECONDS
from shared.constants.strategy import BACKTEST_LOL_MAX_POSITION_LEVELS, BUY_CUTOFF_SECOND
from shared.utils.dota_features import DOTA_XP_FEATURE_COLUMNS
from shared.utils.engine_cadence import read_engine_cadence
from shared.utils.json_io import write_json
from shared.utils.lol_leagues import read_league_whitelist
from shared.utils.match_time import NS_PER_SECOND
from shared.utils.telonex_book import NS_PER_US, US_PER_SECOND
from strategy.policy import follow300_policy

ID_A = 101
ID_B = 102
TOKEN_A0 = "tok-a-0"
TOKEN_A1 = "tok-a-1"
TOKEN_B0 = "tok-b-0"
TOKEN_B1 = "tok-b-1"
COND_A = "0x" + "aa" * 32
COND_B = "0x" + "bb" * 32
SLUG_A = "lol-map-a"
SLUG_B = "lol-map-b"
EVENT_A = "event-a"
EVENT_B = "event-b"
REPLAY_START_TS = 1_700_000_000
HORN_TS = REPLAY_START_TS + 2 * 3600
GAME_END_TS = HORN_TS + 600
REPLAY_END_TS = GAME_END_TS + 60
MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


class FakeBooster:
    """LightGBM stand-in that records feature seconds."""

    def __init__(self, model_file: str) -> None:
        self.model_file = model_file
        self.predict_calls: list[dict[str, Any]] = []

    def feature_name(self) -> list[str]:
        """Return the training feature order the real model must match."""
        return list(DOTA_XP_FEATURE_COLUMNS)

    def num_trees(self) -> int:
        return 1

    def predict(self, features: pd.DataFrame, **kwargs: Any) -> list[float]:
        """Return one delta per row and remember feature seconds."""
        self.predict_calls.append(
            {
                "seconds": features["second"].tolist(),
                "markets": features["market_p_radiant"].tolist(),
                "kwargs": kwargs,
            }
        )
        return [0.01] * len(features)


def install_fake_booster(monkeypatch: pytest.MonkeyPatch, fake: FakeBooster) -> None:
    """Point LightGBM Booster construction at a fixed fake instance."""

    def build_booster(model_file: str) -> FakeBooster:
        """Ignore the model path and return the shared fake booster."""
        _ = model_file
        return fake

    monkeypatch.setattr("shared.utils.gbm.lgb.Booster", build_booster)


def cid_market(
    *,
    market_id: str,
    condition_id: str,
    slug: str,
    token_0: str,
    token_1: str,
) -> dict[str, object]:
    """Archived Gamma market dict with replay fields parse_market requires."""
    return {
        "id": market_id,
        "conditionId": condition_id,
        "slug": slug,
        "closedTime": "2026-04-19 13:13:48+00",
        "secondsDelay": 3,
        "outcomes": json.dumps(["Blue", "Red"]),
        "clobTokenIds": json.dumps([token_0, token_1]),
    }


def write_gamma_page(
    events_dir: Path,
    *,
    event_id: str,
    condition_id: str,
    slug: str,
    token_0: str,
    token_1: str,
) -> None:
    """Write one gzipped Gamma events page under events_dir/<source>/."""
    source_dir = events_dir / "tag_65_closed"
    source_dir.mkdir(parents=True, exist_ok=True)
    event = {
        "id": event_id,
        "slug": f"event-{event_id}",
        "title": "LoL: A vs B",
        "startTime": "2026-03-01T12:00:00Z",
        "markets": [
            cid_market(
                market_id=event_id,
                condition_id=condition_id,
                slug=slug,
                token_0=token_0,
                token_1=token_1,
            )
        ],
    }
    page = {"events": [event]}
    with gzip.open(source_dir / f"page_{event_id}.json.gz", "wt", encoding="utf-8") as stream:
        json.dump(page, stream)


def audit_row(
    *,
    match_id: int,
    event_id: str,
    condition_id: str,
    slug: str,
    token_0: str,
    token_1: str,
    eligible: bool = True,
    reason: str = "accepted",
    source_lag_seconds: int = LOL_SOURCE_LAG_SECONDS,
) -> dict[str, object]:
    """One Stage 07 audit row for fixture parquets."""
    return {
        "match_id": match_id,
        "event_id": event_id,
        "condition_id": condition_id,
        "slug": slug,
        "token_id_0": token_0,
        "token_id_1": token_1,
        "radiant_token_index": 0,
        "resolved_outcome": "Blue",
        "resolved_outcome_index": 0,
        "radiant_win": True,
        "replay_start_ts": REPLAY_START_TS,
        "game_ended_at_ts": GAME_END_TS,
        "replay_end_ts": REPLAY_END_TS,
        "has_books": True,
        "has_onchain_fills": True,
        "signal_row_count": 1,
        "market_second_count": 1,
        "source_lag_seconds": source_lag_seconds,
        "eligible": eligible,
        "reason": reason,
        "ok_quote_fraction": 1.0,
    }


def signal_row(match_id: int, second: int = 0) -> dict[str, object]:
    """One Stage 07 signal row with the shared catalog's snapshot fields populated."""
    row: dict[str, object] = {
        "match_id": match_id,
        "start_time": HORN_TS,
        "event_id": EVENT_A if match_id == ID_A else EVENT_B,
        "second": second,
        "state_ts_us": (HORN_TS + second) * US_PER_SECOND,
        "radiant_win": True,
        "radiant_nw_adv": 0,
        "radiant_nw": 500,
        "dire_nw": 500,
        "radiant_xp_adv": 0,
        "deaths_radiant": 0,
        "deaths_dire": 0,
        "top1_nw_adv": 0,
        "radiant_top1_nw_ratio": 0.2,
        "dire_top1_nw_ratio": 0.2,
        "top3_nw_adv": 0,
        "radiant_top3_nw_ratio": 0.6,
        "dire_top3_nw_ratio": 0.6,
        "market_radiant_prior": 0.5,
        "market_p_radiant": 0.55,
        "signal_market_p_radiant_300s": None,
    }
    return row


def market_second_row(match_id: int, second: int) -> dict[str, object]:
    """One ok Stage 07 market-second row at spawn+second wall."""
    return {
        "match_id": match_id,
        "event_id": EVENT_A if match_id == ID_A else EVENT_B,
        "condition_id": COND_A if match_id == ID_A else COND_B,
        "second": second,
        "state_ts_us": (HORN_TS + second) * US_PER_SECOND,
        "market_status": "ok",
        "market_p_radiant": 0.55,
    }


def write_stage07(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    audit_rows: list[dict[str, object]],
    model_lag: int = LOL_SOURCE_LAG_SECONDS,
) -> Path:
    """Write Stage 07 parquets, Gamma pages, and model.json under tmp_path."""
    backtest_dir = tmp_path / "processed" / "backtest"
    backtest_dir.mkdir(parents=True)
    signals = pd.DataFrame([signal_row(ID_A, 76), signal_row(ID_B, 76)])
    market_seconds = pd.DataFrame(
        [
            market_second_row(ID_A, 0),
            market_second_row(ID_A, BUY_CUTOFF_SECOND),
            market_second_row(ID_B, 0),
            market_second_row(ID_B, BUY_CUTOFF_SECOND),
        ]
    )
    audit = pd.DataFrame(audit_rows)
    signals_path = backtest_dir / "signals.parquet"
    market_path = backtest_dir / "market_seconds.parquet"
    audit_path = backtest_dir / "audit.parquet"
    signals.to_parquet(signals_path, index=False)
    market_seconds.to_parquet(market_path, index=False)
    audit.to_parquet(audit_path, index=False)
    gamma_dir = tmp_path / "raw" / "polymarket" / "gamma" / "events"
    write_gamma_page(
        gamma_dir,
        event_id=EVENT_A,
        condition_id=COND_A,
        slug=SLUG_A,
        token_0=TOKEN_A0,
        token_1=TOKEN_A1,
    )
    write_gamma_page(
        gamma_dir,
        event_id=EVENT_B,
        condition_id=COND_B,
        slug=SLUG_B,
        token_0=TOKEN_B0,
        token_1=TOKEN_B1,
    )
    model_dir = tmp_path / "models" / "research"
    model_dir.mkdir(parents=True)
    write_json(
        model_dir / "model.json",
        {
            "name": "lol-fixture",
            "source_lag_seconds": model_lag,
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "members": ["member_00.txt"],
            "member_trees": [1],
        },
    )
    (model_dir / "member_00.txt").write_text("booster", encoding="utf-8")
    split_path = tmp_path / "processed" / "datasets" / "split.parquet"
    split_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"match_id": [ID_A, ID_B]}).to_parquet(split_path, index=False)
    capture_root = tmp_path / "raw" / "telonex" / "polymarket"
    capture_root.mkdir(parents=True)
    monkeypatch.setattr(lol_inputs, "LOL_VALIDATION_PATH", signals_path)
    monkeypatch.setattr(lol_inputs, "LOL_BACKTEST_MARKET_SECONDS_PATH", market_path)
    monkeypatch.setattr(lol_inputs, "LOL_BACKTEST_AUDIT_PATH", audit_path)
    monkeypatch.setattr(lol_inputs, "LOL_RAW_GAMMA_DIR", tmp_path / "raw" / "polymarket" / "gamma")
    monkeypatch.setattr(lol_inputs, "LOL_RAW_TELONEX_DIR", capture_root)
    monkeypatch.setattr(lol_inputs, "LOL_MAKER_BACKTESTS_DIR", tmp_path / "backtests" / "lol_maker")
    monkeypatch.setattr(backtest_run, "LOL_VALIDATION_PATH", signals_path)
    monkeypatch.setattr(backtest_run, "LOL_BACKTEST_MARKET_SECONDS_PATH", market_path)
    monkeypatch.setattr(backtest_run, "LOL_BACKTEST_AUDIT_PATH", audit_path)
    monkeypatch.setattr(backtest_run, "LOL_SPLIT_PATH", split_path)
    return model_dir


def two_eligible_audit_rows() -> list[dict[str, object]]:
    """Two eligible maps in event_id, match_id order."""
    return [
        audit_row(
            match_id=ID_A,
            event_id=EVENT_A,
            condition_id=COND_A,
            slug=SLUG_A,
            token_0=TOKEN_A0,
            token_1=TOKEN_A1,
        ),
        audit_row(
            match_id=ID_B,
            event_id=EVENT_B,
            condition_id=COND_B,
            slug=SLUG_B,
            token_0=TOKEN_B0,
            token_1=TOKEN_B1,
        ),
    ]


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a parquet at path, creating parents. Empty rows keep a timestamp column."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        pd.DataFrame({"timestamp_us": pd.Series(dtype="int64")}).to_parquet(path, index=False)
        return
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_lol_dispatch_writes_under_lol_maker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--game lol uses lol_maker; Dota report root stays dota_maker."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    selection = load_lol_selection(None, None, model_dir, None)
    assert selection.report_root == tmp_path / "backtests" / "lol_maker"
    assert "dota_maker" not in str(selection.report_root)
    dota_root = BACKTESTS_DIR / EXPERIMENT_NAME
    assert dota_root == BACKTESTS_DIR / "dota_maker"
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--game", "lol", "--validation", "--name", "run1"]
    )
    args = parse_args()
    assert args.game == "lol"
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--game", "dota", "--validation", "--name", "run1"]
    )
    dota = parse_args()
    assert dota.game == "dota"


def test_make_lol_backtest_recipe_passes_game_lol() -> None:
    """make lol-backtest must pass --game lol."""
    text = MAKEFILE.read_text()
    target_start = text.index("\nlol-backtest:")
    recipe = text[target_start : text.index("\npromote-backtest:")]
    assert "--game lol" in recipe
    assert "--model-dir" not in recipe


def test_synthetic_cutoff_pauses_rounds_subsecond_spawn_skew() -> None:
    """Integer replay_start vs microsecond Stage 07 wall is not a hard error."""
    horn_at = datetime(2026, 6, 4, 15, 12, 47, tzinfo=UTC)
    cut = BUY_CUTOFF_SECOND
    wall_us = int((horn_at.timestamp() - 0.037 + cut) * US_PER_SECOND)
    frame = pd.DataFrame(
        [
            {"match_id": ID_A, "second": 0, "state_ts_us": wall_us - cut * US_PER_SECOND},
            {"match_id": ID_A, "second": cut, "state_ts_us": wall_us},
        ]
    )
    match_rows = frame[frame["match_id"] == ID_A]
    assert synthetic_cutoff_pauses(horn_at, match_rows, ID_A, BUY_CUTOFF_SECOND) == []


def test_lol_context_uses_gamma_slug_not_shared_event_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bo3 maps share Stage 07 event_slug; BookReplay must use the unique Gamma slug."""
    rows = two_eligible_audit_rows()
    rows[0]["slug"] = "shared-event"
    rows[1]["slug"] = "shared-event"
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=rows)
    selection = load_lol_selection(None, None, model_dir, None)
    lookups = load_lol_replay_lookups(
        selection.selected_ids,
        selection.audit,
        selection.market_seconds,
        selection.gamma_markets,
        BUY_CUTOFF_SECOND,
        selection.signal_rows,
    )
    assert lookups.context_by_match[ID_A].market_slug == SLUG_A
    assert lookups.context_by_match[ID_B].market_slug == SLUG_B
    assert lookups.context_by_match[ID_A].as_book_replays()[0].market_slug == SLUG_A


def test_lol_horn_ignores_stale_audit_replay_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spawn start_time is horn; an old 2h audit replay_start_ts does not move it."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    selection = load_lol_selection(None, None, model_dir, None)
    lookups = load_lol_replay_lookups(
        selection.selected_ids,
        selection.audit,
        selection.market_seconds,
        selection.gamma_markets,
        BUY_CUTOFF_SECOND,
        selection.signal_rows,
    )
    context = lookups.context_by_match[ID_A]
    horn_at = datetime.fromtimestamp(HORN_TS, tz=UTC)
    audit_replay_start = datetime.fromtimestamp(REPLAY_START_TS, tz=UTC)
    assert context.horn_at == horn_at
    assert context.replay_start == horn_at - LOL_REPLAY_LEAD
    assert context.replay_start != audit_replay_start
    audit_start = selection.audit.loc[selection.audit["match_id"] == ID_A, "replay_start_ts"]
    assert int(audit_start.iloc[0]) == REPLAY_START_TS


def test_execution_source_rewrites_onchain_else_without_local_telonex(
    tmp_path: Path,
) -> None:
    """Onchain day files are rewritten with `side`; a missing day is without_local_telonex."""
    capture = tmp_path / "capture"
    token_id = "111"
    sibling = "222"
    day = "2026-04-19"
    for token in (token_id, sibling):
        write_parquet(
            capture / ONCHAIN_CHANNEL / f"asset_id={token}" / f"{day}.parquet",
            [
                {
                    "block_timestamp_us": 1,
                    "asset_id": token,
                    "taker_asset_id": token,
                    "taker_side": "buy",
                    "price": "0.5",
                    "amount": "1.0",
                }
            ],
        )
        write_parquet(
            capture / "book_snapshot_full" / f"asset_id={token}" / f"{day}.parquet",
            [{"timestamp_us": 1}],
        )
    horn_at = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    game_ended_at = datetime(2026, 4, 19, 11, 0, tzinfo=UTC)
    replay = calculate_replay_window(horn_at=horn_at, game_ended_at=game_ended_at)
    closed_at = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)
    context = MarketContext(
        match_id=1,
        condition_id="0xabc",
        event_id="e1",
        market_slug="demo-market",
        token_ids=(token_id, sibling),
        radiant_token_index=0,
        radiant_win=True,
        seconds_delay=3,
        horn_at=horn_at,
        game_ended_at=game_ended_at,
        market_closed_at=closed_at,
        replay_start=replay.start,
        replay_end=replay.end,
        clock_end=calculate_clock_end(game_ended_at=game_ended_at, market_closed_at=closed_at),
    )
    root = tmp_path / "tree"
    link_market_tokens(root, context, capture)
    onchain_dir = root / "polymarket" / ONCHAIN_CHANNEL / "demo-market" / "outcome_id=0"
    assert not onchain_dir.is_symlink()
    day_file = onchain_dir / f"{day}.parquet"
    assert day_file.is_file()
    assert "side" in pd.read_parquet(day_file).columns
    assert not (root / "polymarket" / "trades").exists()
    window = ReplayWindow(start=context.replay_start, end=context.replay_end)
    assert has_local_telonex_days(context.token_ids, window, capture)
    (capture / ONCHAIN_CHANNEL / f"asset_id={token_id}" / f"{day}.parquet").unlink()
    assert not has_local_telonex_days(context.token_ids, window, capture)


def test_book_rows_in_window_sums_tokens_over_window_days(tmp_path: Path) -> None:
    """Shard weight sums book rows of both tokens; absent day files count as zero."""
    capture = tmp_path / "capture"
    day = "2026-04-19"
    write_parquet(
        capture / "book_snapshot_full" / "asset_id=111" / f"{day}.parquet",
        [{"timestamp_us": 1}] * 3,
    )
    write_parquet(
        capture / "book_snapshot_full" / "asset_id=222" / f"{day}.parquet",
        [{"timestamp_us": 1}] * 2,
    )
    window = ReplayWindow(
        start=datetime(2026, 4, 19, 10, tzinfo=UTC),
        end=datetime(2026, 4, 19, 11, tzinfo=UTC),
    )
    assert book_rows_in_window(("111", "222"), window, capture) == 5
    next_day = ReplayWindow(
        start=datetime(2026, 4, 20, 10, tzinfo=UTC),
        end=datetime(2026, 4, 20, 11, tzinfo=UTC),
    )
    assert book_rows_in_window(("111", "222"), next_day, capture) == 0


def test_build_lol_book_weights_reads_audit_window(tmp_path: Path) -> None:
    """LoL weights come from audit replay window and token ids."""
    capture = tmp_path / "capture"
    write_parquet(
        capture / "book_snapshot_full" / "asset_id=111" / "2026-04-19.parquet",
        [{"timestamp_us": 1}] * 4,
    )
    audit = pd.DataFrame(
        [
            {
                "match_id": ID_A,
                "replay_start_ts": int(datetime(2026, 4, 19, 10, tzinfo=UTC).timestamp()),
                "replay_end_ts": int(datetime(2026, 4, 19, 11, tzinfo=UTC).timestamp()),
                "token_id_0": "111",
                "token_id_1": "222",
            }
        ]
    )
    assert build_lol_book_weights([ID_A], audit, capture) == {ID_A: 4}


def test_model_lag_mismatch_rejects_before_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model.json lag 25 vs Stage 07 lag 10 fails before run_batch."""

    def fail_run_batch(**_kwargs: object) -> None:
        """Fail if replay starts after a lag mismatch."""
        raise AssertionError("run_batch should not be called")

    monkeypatch.setattr(backtest_run, "run_batch", fail_run_batch)
    model_dir = write_stage07(
        tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows(), model_lag=25
    )
    with pytest.raises(ValueError, match="source_lag_seconds"):
        load_lol_selection(None, None, model_dir, None)
    with pytest.raises(ValueError, match="source_lag_seconds"):
        assert_lol_source_lag(model_dir, pd.read_parquet(lol_inputs.LOL_BACKTEST_AUDIT_PATH))


def test_incompatible_resume_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LoL resume rejects a different game, model hash, signals hash, or lag."""

    def read_meta(_path: Path) -> dict[str, object]:
        """Stub model meta for the LoL manifest."""
        return {"name": "lol-fixture", "source_lag_seconds": LOL_SOURCE_LAG_SECONDS}

    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    monkeypatch.setattr(backtest_run, "read_model_meta", read_meta)
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    expected = build_run_manifest(
        model_dir=model_dir,
        game="lol",
        signal_cadence_seed=0,
        run_policy=follow300_policy(
            level_usdc=BACKTEST_LEVEL_USDC["lol"],
            debounce_ms=read_engine_cadence().debounce_ms,
            fallback_timer_s=read_engine_cadence().quoter_tick_s,
        ),
        max_position_levels=BACKTEST_LOL_MAX_POSITION_LEVELS,
        selected_ids=(),
        whitelist_keys={},
        plans={},
        exclusions={},
        since_match=None,
        archives_only=False,
    )
    assert expected["game"] == "lol"
    assert expected["execution_priority"] == ["onchain_fills"]
    assert "validation_dataset_path" not in expected
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**expected, "game": "dota"}, expected)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**expected, "model_sha256": "deadbeef"}, expected)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**expected, "signals_sha256": "deadbeef"}, expected)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**expected, "source_lag_seconds": 25}, expected)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**expected, "max_signal_age_seconds": 99.0}, expected)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**expected, "max_exit_age_seconds": 99.0}, expected)
    whitelist_path = tmp_path / "wl.json"
    write_json(whitelist_path, {"leagues": ["LCK"], "aliases": {}})
    filtered = {
        **expected,
        **build_whitelist_manifest_keys(read_league_whitelist(whitelist_path), (ID_A,)),
    }
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches(expected, filtered)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches({**filtered, "selected_matches": 2}, filtered)
    dota_shaped = {key: value for key, value in expected.items() if key != "game"}
    dota_shaped.pop("model_sha256")
    dota_shaped.pop("signals_sha256")
    dota_shaped.pop("source_lag_seconds")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        assert_manifest_matches(dota_shaped, expected)


def test_lol_validation_keeps_nautilus_zero_fill_maps_in_the_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selection does not hide crash maps; --match-id still selects them."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    monkeypatch.setattr(lol_inputs, "NAUTILUS_ZERO_FILL_SKIP_IDS", frozenset({ID_A}))
    selection = load_lol_selection(None, None, model_dir, None)
    assert selection.selected_ids == (ID_A, ID_B)
    assert selection.coverage is not None
    assert selection.coverage.eligible == 2
    one = load_lol_selection(ID_A, None, model_dir, None)
    assert one.selected_ids == (ID_A,)


def test_lol_selection_orders_maps_and_builds_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LoL selection keeps map order and builds a signal series per selected id."""
    install_fake_booster(monkeypatch, FakeBooster("unused"))
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    selection = load_lol_selection(None, None, model_dir, None)
    assert selection.selected_ids == (ID_A, ID_B)
    assert selection.report_root == tmp_path / "backtests" / "lol_maker"
    lookups = load_lol_replay_lookups(
        selection.selected_ids,
        selection.audit,
        selection.market_seconds,
        selection.gamma_markets,
        BUY_CUTOFF_SECOND,
        selection.signal_rows,
    )
    contexts = [lookups.context_by_match[match_id] for match_id in selection.selected_ids]
    assert [context.match_id for context in contexts] == [ID_A, ID_B]
    assert contexts[0].token_ids == (TOKEN_A0, TOKEN_A1)
    assert contexts[0].market_slug == SLUG_A
    signals = build_match_signals(
        selection.selected_ids,
        selection.signal_rows,
        model_dir,
        0,
        "lol",
        LIVE_GRID_TIMING,
        lookups.mids,
    )
    assert set(signals) == {ID_A, ID_B}


def test_lol_grid_v1_drops_rows_before_the_archive_first_tick_second(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A row at game-second 75 is absent; 76 stays the connect tick and the next
    cadence row (85 for seed 0, match 101) decides."""
    fake = FakeBooster("unused")
    install_fake_booster(monkeypatch, fake)
    rows = pd.DataFrame([signal_row(ID_A, 75), signal_row(ID_A, 76), signal_row(ID_A, 85)])
    (tmp_path / "member_00.txt").write_text("unused")
    write_json(
        tmp_path / "model.json",
        {
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "members": ["member_00.txt"],
            "member_trees": [1],
        },
    )
    connect_ns = int(rows["state_ts_us"].iloc[1]) * NS_PER_US + (
        LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    )
    decision_ns = int(rows["state_ts_us"].iloc[2]) * NS_PER_US + (
        LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    )
    delayed = MidSeries(timestamps_ns=(decision_ns,), market_ps=(0.72,))

    signals = build_match_signals(
        (ID_A,), rows, tmp_path, 0, "lol", LIVE_GRID_TIMING, {ID_A: delayed}
    )

    assert signals[ID_A].feed_timestamps_ns == (connect_ns, decision_ns)
    assert signals[ID_A].timestamps_ns == (decision_ns,)


def test_lol_predict_does_not_lag_second(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """LoL grid-v1 acts 11s after the frame, on that mid, and keeps the frame second."""
    fake = FakeBooster("unused")
    install_fake_booster(monkeypatch, fake)
    # The first feed tick only connects; 85 is seed 0's next cadence tick.
    rows = pd.DataFrame([signal_row(ID_A, 76), signal_row(ID_A, 85)])
    (tmp_path / "member_00.txt").write_text("unused")
    write_json(
        tmp_path / "model.json",
        {
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "members": ["member_00.txt"],
            "member_trees": [1],
        },
    )
    connect_ns = int(rows["state_ts_us"].iloc[0]) * NS_PER_US + (
        LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    )
    decision_ns = int(rows["state_ts_us"].iloc[1]) * NS_PER_US + (
        LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    )
    delayed = MidSeries(timestamps_ns=(decision_ns,), market_ps=(0.72,))
    signals = build_match_signals(
        (ID_A,), rows, tmp_path, 0, "lol", LIVE_GRID_TIMING, {ID_A: delayed}
    )
    assert signals[ID_A].feed_timestamps_ns == (connect_ns, decision_ns)
    assert signals[ID_A].timestamps_ns == (decision_ns,)
    assert signals[ID_A].dataset_market_ps == (0.72,)
    assert fake.predict_calls[0]["seconds"] == [85]
    assert fake.predict_calls[0]["markets"] == [0.72]

    later_only = MidSeries(
        timestamps_ns=(decision_ns + NS_PER_SECOND,),
        market_ps=(0.72,),
    )
    dropped = build_match_signals(
        (ID_A,), rows, tmp_path, 0, "lol", LIVE_GRID_TIMING, {ID_A: later_only}
    )
    assert dropped[ID_A].feed_timestamps_ns == (connect_ns, decision_ns)
    assert dropped[ID_A].timestamps_ns == ()
    assert len(fake.predict_calls) == 1


def test_context_ends_at_final_livestats_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every LoL context ends at the audit row's last livestats frame."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    selection = load_lol_selection(None, None, model_dir, None)
    lookups = load_lol_replay_lookups(
        selection.selected_ids,
        selection.audit,
        selection.market_seconds,
        selection.gamma_markets,
        BUY_CUTOFF_SECOND,
        selection.signal_rows,
    )
    for context in lookups.context_by_match.values():
        assert context.game_ended_at.timestamp() == GAME_END_TS


def test_league_whitelist_filters_the_replay_population(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only maps of allowed PM events are selected, and coverage counts the filtered set."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())

    selection = load_lol_selection(None, None, model_dir, frozenset({EVENT_A}))

    assert selection.selected_ids == (ID_A,)
    assert selection.coverage is not None
    assert selection.coverage.validation_matches == 1
    assert selection.coverage.eligible == 1


def test_league_whitelist_blocks_match_id_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--match-id cannot replay a map the whitelist excludes."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())

    assert load_lol_selection(ID_A, None, model_dir, frozenset({EVENT_A})).selected_ids == (ID_A,)
    with pytest.raises(ValueError, match="not an eligible backtest card"):
        load_lol_selection(ID_B, None, model_dir, frozenset({EVENT_A}))


def test_league_whitelist_filters_before_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--limit counts allowed maps, so it cannot pull an excluded map back in."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())

    selection = load_lol_selection(None, 1, model_dir, frozenset({EVENT_B}))

    assert selection.selected_ids == (ID_B,)


def test_whitelist_manifest_keys_fingerprint_the_selected_maps(tmp_path: Path) -> None:
    """The manifest records the whitelist and the exact map set, and stays absent without it."""
    whitelist_path = tmp_path / "wl.json"
    write_json(whitelist_path, {"leagues": ["LCK"], "aliases": {}})
    whitelist = read_league_whitelist(whitelist_path)

    keys = build_whitelist_manifest_keys(whitelist, (ID_B, ID_A))

    assert keys["league_whitelist_sha256"] == whitelist.sha256
    assert keys["selected_matches"] == 2
    assert keys["selected_matches_sha256"] == sha256_text(f"{ID_A},{ID_B}")
    assert build_whitelist_manifest_keys(None, (ID_A,)) == {}


@pytest.mark.parametrize("game", ["dota", "lol"])
def test_league_flag_is_removed(monkeypatch: pytest.MonkeyPatch, game: str) -> None:
    """Neither game accepts a whitelist override."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--game",
            game,
            "--validation",
            "--name",
            "run1",
            "--league-whitelist",
            "config/lol_league_whitelist.json",
        ],
    )
    with pytest.raises(SystemExit):
        parse_args()


@pytest.mark.parametrize(
    "options",
    [
        ["--validation", "--name", "test"],
        ["--validation", "--name", "test", "--limit", "1"],
        ["--validation", "--name", "test", "--shard", "0/2"],
        ["--match-id", str(ID_B)],
        ["--validation", "--warm-cache"],
        ["--validation", "--warm-cache", "--limit", "1"],
        ["--validation", "--warm-cache", "--shard", "0/2"],
        ["--validation", "--warm-cache", "--shard", "1/2"],
        ["--match-id", str(ID_B), "--warm-cache"],
    ],
)
def test_main_always_filters_lol_before_plan_or_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: list[str]
) -> None:
    """Real CLI and map selection stop before replay; no model training or replay runs."""
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    universe = tmp_path / "universe.parquet"
    pd.DataFrame(
        [{"event_id": EVENT_A, "league": "LCK Cup"}, {"event_id": EVENT_B, "league": "LEC"}]
    ).to_parquet(universe)
    monkeypatch.setattr(backtest_run, "LOL_RESEARCH_MODEL_DIR", model_dir)
    monkeypatch.setattr(backtest_run, "LOL_UNIVERSE_PATH", universe)
    monkeypatch.setattr(sys, "argv", ["backtest", "--game", "lol", *options])
    with (
        patch.object(backtest_run, "install_nautilus_logging"),
        patch.object(backtest_run, "plan_replay_ids", return_value=None) as planner,
        patch.object(backtest_run, "warm_replay_cache") as warmer,
    ):
        backtest_run.main()
    if "--warm-cache" in options:
        planner.assert_not_called()
        if "1/2" in options:
            warmer.assert_not_called()
        else:
            warmer.assert_called_once()
            assert [context.match_id for context in warmer.call_args.kwargs["contexts"]] == [ID_B]
    else:
        warmer.assert_not_called()
        planner.assert_called_once()
        assert planner.call_args.args[1] == (ID_B,)
        manifest = planner.call_args.args[4]
        assert manifest["selected_matches_sha256"] == sha256_text(str(ID_B))
        assert manifest["league_whitelist_sha256"]


@pytest.mark.parametrize("warm", [False, True])
def test_main_rejects_excluded_match_before_any_replay_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warm: bool
) -> None:
    model_dir = write_stage07(tmp_path, monkeypatch, audit_rows=two_eligible_audit_rows())
    universe = tmp_path / "universe.parquet"
    pd.DataFrame(
        [{"event_id": EVENT_A, "league": "LCK Cup"}, {"event_id": EVENT_B, "league": "LEC"}]
    ).to_parquet(universe)
    monkeypatch.setattr(backtest_run, "LOL_RESEARCH_MODEL_DIR", model_dir)
    monkeypatch.setattr(backtest_run, "LOL_UNIVERSE_PATH", universe)
    options = ["--warm-cache"] if warm else []
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--game", "lol", "--match-id", str(ID_A), *options]
    )
    with (
        patch.object(backtest_run, "install_nautilus_logging"),
        patch.object(backtest_run, "warm_replay_cache") as warmer,
        patch.object(backtest_run, "plan_replay_ids") as planner,
        pytest.raises(ValueError, match="not an eligible backtest card"),
    ):
        backtest_run.main()
    warmer.assert_not_called()
    planner.assert_not_called()


@pytest.mark.parametrize(
    "options", [["--resume"], [], ["--shard", "0/2"], ["--shard", "0/2", "--resume"]]
)
def test_old_lol_directory_is_rejected_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: list[str]
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--game", "lol", "--validation", "--name", "test", *options]
    )
    args = parse_args()
    write_json(tmp_path / "manifest.json", {"game": "lol"})
    artifact = tmp_path / "results.parquet"
    artifact.write_bytes(b"old results must survive")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    expected = {
        "game": "lol",
        "league_whitelist_sha256": "new",
        "selected_matches_sha256": "selected",
    }
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        backtest_run.plan_replay_ids(
            args,
            [ID_A],
            None,
            tmp_path,
            expected,
            lambda _: pytest.fail("no lookups"),
            lambda ids: dict.fromkeys(ids, 1),
            empty_shared_archive(),
            False,
        )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("changed_key", ["league_whitelist_sha256", "selected_matches_sha256"])
def test_manifest_rejects_changed_policy_or_selection(changed_key: str) -> None:
    expected = {
        "game": "lol",
        "league_whitelist_sha256": "whitelist",
        "selected_matches_sha256": "selection",
    }
    with pytest.raises(ValueError, match=changed_key):
        assert_manifest_matches({**expected, changed_key: "old"}, expected)
    with pytest.raises(ValueError, match="league_whitelist_sha256"):
        assert_manifest_matches(expected, {"game": "lol"})
