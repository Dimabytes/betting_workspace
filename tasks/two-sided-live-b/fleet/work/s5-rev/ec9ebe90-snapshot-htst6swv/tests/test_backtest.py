"""Unit tests for Dota backtest timing, settlement hooks, and batching."""

# The settlement harness stands in for untyped Nautilus Cython APIs.
# pyright: reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from prediction_market_extensions.adapters.polymarket.fee_model import PolymarketFeeModel

import backtest.run as backtest_run
from backtest.context import (
    POLYMARKET_VENUE,
    REPLAY_LEAD,
    MarketContext,
    calculate_clock_end,
    calculate_replay_window,
)
from backtest.feed_schedules import GridV1Plan
from backtest.results import assert_manifest_matches
from backtest.run import (
    BACKTEST_LEVEL_USDC,
    MAX_MATCHES_PER_BATCH,
    Game,
    ReplayEndBoundary,
    assert_every_leg_loaded,
    build_kernels,
    build_order_latency,
    build_report_dir,
    build_run_manifest,
    build_strategy_configs,
    install_settlement_compatibility,
    install_skip_market_artifacts,
    parse_args,
    resolve_run_policies,
    skip_market_artifacts,
    split_into_batches,
    warm_replay_cache,
)
from backtest.signals import MatchSignals, assert_expected_feature_names
from backtest.strategy import (
    BUY_LEVEL_COUNT,
    BUY_LEVEL_STEP_TICKS,
)
from shared.constants.dataset import BACKTEST_LAG_SECONDS, TRAIN_LAG_SECONDS
from shared.constants.lol import LOL_REPLAY_LEAD, LOL_SOURCE_LAG_SECONDS
from shared.constants.strategy import (
    BACKTEST_DOTA_MAX_POSITION_LEVELS,
    BACKTEST_LOL_MAX_POSITION_LEVELS,
    BACKTEST_MAX_POSITION_LEVELS,
    BUY_CUTOFF_SECOND,
    BUY_POLICY_VERSION,
    EXIT_ABS_DELTA,
    MAX_ENTRY_PRICE,
    MIN_ABS_DELTA,
    MIN_ENTRY_PRICE,
)
from shared.utils.dota_features import (
    DOTA_XP_FEATURE_COLUMNS,
)
from shared.utils.engine_cadence import read_engine_cadence
from shared.utils.gbm import model_identity_sha256
from shared.utils.match_time import NS_PER_SECOND, datetime_to_ns
from shared.utils.model_registry import write_model_meta
from strategy.policy import Follow300Policy, follow300_policy


def _current_policy(game: Game) -> Follow300Policy:
    cadence = read_engine_cadence()
    return follow300_policy(
        level_usdc=BACKTEST_LEVEL_USDC[game],
        debounce_ms=cadence.debounce_ms,
        fallback_timer_s=cadence.quoter_tick_s,
    )


VENUE = Venue("POLYMARKET")
RADIANT_ID = InstrumentId(Symbol("0xabc-111"), VENUE)
DIRE_ID = InstrumentId(Symbol("0xabc-222"), VENUE)
MVP_SECONDS_DELAY = 3


def build_context(
    *,
    match_id: int = 1,
    condition_id: str = "0xabc",
    market_slug: str = "demo-market",
    token_ids: tuple[str, str] = ("111", "222"),
    radiant_token_index: int = 0,
    seconds_delay: int = MVP_SECONDS_DELAY,
    market_closed_at: datetime = datetime(2026, 1, 1, 1, tzinfo=UTC),
) -> MarketContext:
    """A minimal market context for tests that only read identity and timing."""
    horn_at = datetime(2026, 1, 1, tzinfo=UTC)
    game_ended_at = datetime(2026, 1, 1, 1, tzinfo=UTC)
    replay = calculate_replay_window(horn_at=horn_at, game_ended_at=game_ended_at)
    return MarketContext(
        match_id=match_id,
        condition_id=condition_id,
        event_id="event-1",
        market_slug=market_slug,
        token_ids=token_ids,
        radiant_token_index=radiant_token_index,
        radiant_win=True,
        seconds_delay=seconds_delay,
        horn_at=horn_at,
        game_ended_at=game_ended_at,
        market_closed_at=market_closed_at,
        replay_start=replay.start,
        replay_end=replay.end,
        clock_end=calculate_clock_end(
            game_ended_at=game_ended_at, market_closed_at=market_closed_at
        ),
    )


def test_books_stop_at_game_end_while_the_clock_reaches_settlement() -> None:
    """Book replay ends just after the game; only the clock runs on to settlement."""
    horn_at = datetime(2026, 4, 19, 10, 24, 23, 599000, tzinfo=UTC)
    game_ended_at = datetime(2026, 4, 19, 10, 38, 18, 197000, tzinfo=UTC)
    market_closed_at = datetime(2026, 4, 19, 13, 13, 48, tzinfo=UTC)

    replay = calculate_replay_window(horn_at=horn_at, game_ended_at=game_ended_at)
    clock_end = calculate_clock_end(game_ended_at=game_ended_at, market_closed_at=market_closed_at)

    assert replay.start == datetime(2026, 4, 19, 10, 22, 23, 599000, tzinfo=UTC)
    assert replay.end == datetime(2026, 4, 19, 10, 39, 18, 197000, tzinfo=UTC)
    assert clock_end == datetime(2026, 4, 19, 13, 14, 48, tzinfo=UTC)


def test_replay_leads_stay_equal() -> None:
    """Dota and LoL book lead are one constant under two names."""
    assert REPLAY_LEAD == LOL_REPLAY_LEAD == timedelta(minutes=2)


def test_skip_market_artifacts_seam_is_installed() -> None:
    """run_batch installs the empty post-run artifacts hook next to settlement."""
    backtest = SimpleNamespace()
    install_skip_market_artifacts(backtest)
    assert backtest._build_market_artifacts is skip_market_artifacts
    assert backtest._build_market_artifacts(unused=1) == {}


def test_order_latency_matches_venue_release_offsets() -> None:
    """Insert wake delay matches the venue model; cancel delay is owned by the strategy."""
    latency_config, order_latency_ns, cancel_latency_ns = build_order_latency()
    assert order_latency_ns == 175_000_000
    assert cancel_latency_ns == 60_000_000
    latency_model = latency_config.build_latency_model()
    assert latency_model is not None
    assert latency_model.insert_latency_nanos == order_latency_ns
    # Strategy defers cancel_order by cancel_latency_ns; model cancel is zero to avoid 2x delay.
    assert latency_model.cancel_latency_nanos == 0


@dataclass(frozen=True)
class LoadedSim:
    """Minimal loaded replay accepted by the boundary installer."""

    instrument: Any
    records: tuple[Any, ...]


def build_loaded_sim(context: MarketContext, token_index: int) -> LoadedSim:
    """A loaded replay carrying the instrument the framework would build for one leg."""
    values = BinaryOption.to_dict(TestInstrumentProvider.binary_option())
    instrument_id = context.instrument_ids[token_index]
    values["id"] = instrument_id
    values["raw_symbol"] = instrument_id.removesuffix(f".{POLYMARKET_VENUE}")
    # Non-zero so rewrite_replay_instrument's taker_fee=0 assertion is not vacuous.
    values["taker_fee"] = "0.05"
    return LoadedSim(
        instrument=BinaryOption.from_dict(values),
        records=(SimpleNamespace(ts_event=NS_PER_SECOND),),
    )


def install_and_load(
    contexts: list[MarketContext], loaded_sims: list[LoadedSim], boundary_ns: int
) -> list[LoadedSim]:
    """Run the settlement wrapper over a fixed set of loaded replays."""

    async def load_sims() -> list[LoadedSim]:
        """Return the fake loaded replays."""
        return list(loaded_sims)

    backtest = SimpleNamespace(_load_sims_async=load_sims)
    install_settlement_compatibility(backtest, contexts, boundary_ns)
    return asyncio.run(backtest._load_sims_async())


def test_each_market_settles_on_its_own_close_with_one_batch_boundary() -> None:
    """Every leg gets its own expiration and the batch shares one clock boundary."""
    first = build_context(
        match_id=1,
        condition_id="0xabc",
        market_closed_at=datetime(2026, 4, 19, 13, 13, 48, tzinfo=UTC),
    )
    second = build_context(
        match_id=2,
        condition_id="0xdef",
        token_ids=("333", "444"),
        market_closed_at=datetime(2026, 4, 19, 15, 0, 0, tzinfo=UTC),
    )
    boundary_ns = datetime_to_ns(second.clock_end)
    loaded_sims = [
        build_loaded_sim(context, token_index)
        for context in (first, second)
        for token_index in (0, 1)
    ]

    settled = install_and_load([first, second], loaded_sims, boundary_ns)

    expirations = [loaded_sim.instrument.expiration_ns for loaded_sim in settled]
    assert expirations == [
        datetime_to_ns(first.market_closed_at),
        datetime_to_ns(first.market_closed_at),
        datetime_to_ns(second.market_closed_at),
        datetime_to_ns(second.market_closed_at),
    ]
    assert all(loaded_sim.instrument.taker_fee == Decimal("0") for loaded_sim in settled)
    boundary = settled[0].records[-1]
    assert boundary.ts_event == boundary_ns
    assert isinstance(boundary.data, ReplayEndBoundary)
    assert boundary.instrument_id == settled[0].instrument.id
    assert [len(loaded_sim.records) for loaded_sim in settled] == [2, 1, 1, 1]


def test_rewritten_instrument_earns_zero_engine_commission() -> None:
    """Zero taker_fee makes PolymarketFeeModel return zero money on a LIMIT fill."""
    context = build_context()
    loaded = build_loaded_sim(context, 0)
    assert loaded.instrument.taker_fee == Decimal("0.05")
    settled = install_and_load([context], [loaded, build_loaded_sim(context, 1)], NS_PER_SECOND)
    instrument = settled[0].instrument
    assert instrument.taker_fee == Decimal("0")

    commission = PolymarketFeeModel().get_commission(
        SimpleNamespace(),
        Quantity.from_str("5"),
        Price.from_str("0.50"),
        instrument,
    )
    assert commission.as_decimal() == Decimal("0")


def test_a_market_missing_one_leg_stops_the_batch() -> None:
    """A half-loaded market names the match instead of silently trading one side."""
    first = build_context(match_id=1, condition_id="0xabc")
    second = build_context(match_id=2, condition_id="0xdef", token_ids=("333", "444"))
    loaded_sims = [
        build_loaded_sim(first, 0),
        build_loaded_sim(first, 1),
        build_loaded_sim(second, 0),
    ]

    with pytest.raises(ValueError, match=r"both market legs for matches \[2\]"):
        install_and_load([first, second], loaded_sims, NS_PER_SECOND)


def test_every_leg_loaded_accepts_a_complete_batch() -> None:
    """A batch with both legs of every market passes the pre-run check."""
    context = build_context()
    assert_every_leg_loaded([context], [build_loaded_sim(context, 0), build_loaded_sim(context, 1)])


def test_model_feature_names_must_match_expected() -> None:
    """Model feature names must equal the training columns in order."""
    assert_expected_feature_names(list(DOTA_XP_FEATURE_COLUMNS), DOTA_XP_FEATURE_COLUMNS)
    with pytest.raises(ValueError, match="feature names"):
        assert_expected_feature_names(["second", "radiant_nw_adv"], DOTA_XP_FEATURE_COLUMNS)


REMOVED_POLICY_FLAGS = (
    "--layers",
    "--layer-step-ticks",
    "--base-size-usdc",
    "--buy-lifecycle",
    "--ladder-reprice-mode",
    "--buy-after-first-fill",
    "--buy-depth-cap-multiple",
    "--sell-drop-block-delta",
    "--sell-drop-block-window-seconds",
    "--sell-drop-block-max-seconds",
    "--sell-drop-block-stop-delta",
    "--buy-levels",
    "--buy-level-usdc",
    "--policy",
    "--live-since",
)


def test_backtest_cli_exposes_only_operational_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Backtest CLI has run selectors and the cadence seed, and no strategy switches."""
    monkeypatch.setattr(sys, "argv", ["backtest", "--help"])
    with pytest.raises(SystemExit) as raised:
        parse_args()
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    for kept in (
        "--match-id",
        "--validation",
        "--since-match",
        "--game",
        "--limit",
        "--resume",
        "--shard",
        "--merge-shards",
        "--warm-cache",
        "--name",
        "--signal-cadence-seed",
        "--model-dir",
        "--min-abs-delta",
        "--exit-abs-delta",
        "--lag-seconds",
        "--validation-dataset",
        "--cadence-mean-interval",
    ):
        assert kept in help_text
    for removed in (
        *REMOVED_POLICY_FLAGS,
        "--fill-model",
        "--exit-settle",
        "--buy-cutoff-second",
        "--unwind-after-seconds",
        "--burst-cooloff-seconds",
        "--min-entry-price",
        "--size-alpha",
        "--max-signal-age-seconds",
        "--max-abs-nw-delta-30",
    ):
        assert removed not in help_text


def _parquet_sha(_path: Path) -> str:
    return "parquet-sha"


def write_test_catalog(directory: Path, name: str) -> None:
    """Write a one-member catalog so manifest tests do not need the live research dir."""
    (directory / "member_00.txt").write_text("a")
    write_model_meta(
        {
            "name": name,
            "trained_at": "2026-09-11T00:00:00Z",
            "train_dataset_sha256": "0" * 64,
            "validation_dataset_sha256": "1" * 64,
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "source_lag_seconds": TRAIN_LAG_SECONDS,
            "train_matches": 1,
            "metrics": None,
            "members": ["member_00.txt"],
            "member_trees": [1],
            "ensemble_arm": "default_boot",
            "ensemble_k": 1,
            "ensemble_sampling": "boot",
        },
        directory / "model.json",
    )


def test_run_manifest_records_model_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Record the model registry name and pin the catalog by member identity."""
    write_test_catalog(tmp_path, "20260814T000000Z")
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)

    manifest = build_run_manifest(
        model_dir=tmp_path,
        game="dota",
        signal_cadence_seed=0,
        run_policy=_current_policy("dota"),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(101, 202),
        whitelist_keys={},
        plans={},
        exclusions={},
        since_match=None,
        archives_only=False,
    )

    assert manifest["model_name"] == "20260814T000000Z"
    assert manifest["fill_model"] == "queue"
    assert manifest["buy_cutoff_second"] == BUY_CUTOFF_SECOND
    assert manifest["train_lag_seconds"] == TRAIN_LAG_SECONDS
    assert manifest["backtest_lag_seconds"] == BACKTEST_LAG_SECONDS
    assert manifest["min_entry_price"] == MIN_ENTRY_PRICE
    assert manifest["max_entry_price"] == MAX_ENTRY_PRICE
    assert manifest["layer_usdc"] == BACKTEST_LEVEL_USDC["dota"]
    assert manifest["layers"] == BUY_LEVEL_COUNT
    assert manifest["layer_step_ticks"] == BUY_LEVEL_STEP_TICKS
    assert manifest["max_position_levels"] == BACKTEST_DOTA_MAX_POSITION_LEVELS
    assert manifest["buy_ladder_policy"] == BUY_POLICY_VERSION
    assert "buy_lifecycle" not in manifest
    assert "ladder_reprice_mode" not in manifest
    assert "buy_after_first_fill" not in manifest
    assert "buy_depth_cap_multiple" not in manifest
    assert "buy_size_policy" not in manifest
    assert "sell_drop_block_delta" not in manifest
    assert "size_alpha" not in manifest
    assert "unwind_after_seconds" not in manifest
    assert manifest["min_abs_delta"] == MIN_ABS_DELTA
    assert manifest["exit_abs_delta"] == EXIT_ABS_DELTA
    assert "max_abs_nw_delta_30" not in manifest
    assert "adverse_taker" not in manifest
    assert "signal_cadence" not in manifest
    assert "profiles" not in manifest
    assert manifest["signal_cadence_seed"] == 0
    assert "archives_only" not in manifest
    archive_manifest = build_run_manifest(
        model_dir=tmp_path,
        game="dota",
        signal_cadence_seed=0,
        run_policy=_current_policy("dota"),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(101, 202),
        whitelist_keys={},
        plans={},
        exclusions={},
        since_match=None,
        archives_only=True,
    )
    assert archive_manifest["archives_only"] is True
    assert "signal_cadence_seed" not in archive_manifest
    assert manifest["max_signal_age_seconds"] == 16.0
    assert manifest["validation_dataset_sha256"] == "parquet-sha"
    assert manifest["selected_matches"] == 2
    assert manifest["max_exit_age_seconds"] == 45.0
    assert "sell_full_age_seconds" not in manifest
    assert "sell_ask_age_seconds" not in manifest
    assert manifest["model_sha256"] == model_identity_sha256(tmp_path)
    assert "game" not in manifest
    assert "cutoff_rule" not in manifest
    assert "signals_sha256" not in manifest
    assert manifest["model_path"] == str(tmp_path)


def test_resume_rejects_other_buy_ladder_policies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither a B200 checkpoint nor a follow300-v4 run can resume under the new policy."""
    write_test_catalog(tmp_path, "20260814T000000Z")
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)
    expected = build_run_manifest(
        model_dir=tmp_path,
        game="dota",
        signal_cadence_seed=0,
        run_policy=_current_policy("dota"),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(),
        whitelist_keys={},
        plans={},
        exclusions={},
        since_match=None,
        archives_only=False,
    )
    b200 = {
        **expected,
        "buy_ladder_policy": "buy-ladder-v2",
        "layers": 2,
    }
    with pytest.raises(ValueError, match="layers"):
        assert_manifest_matches(b200, expected)
    previous_policy = {**expected, "buy_ladder_policy": "follow300-v4"}
    with pytest.raises(ValueError, match="buy_ladder_policy"):
        assert_manifest_matches(previous_policy, expected)


def test_lol_run_manifest_fingerprints_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """LoL resume fingerprints the catalog and never a sibling bank."""
    (tmp_path / "member_00.txt").write_text("booster")
    write_model_meta(
        {
            "name": "lol-fixture",
            "trained_at": "2026-09-11T00:00:00Z",
            "train_dataset_sha256": "0" * 64,
            "validation_dataset_sha256": "1" * 64,
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "source_lag_seconds": 0,
            "train_matches": 1,
            "metrics": None,
            "members": ["member_00.txt"],
            "member_trees": [1],
            "ensemble_arm": "default_boot",
            "ensemble_k": 1,
            "ensemble_sampling": "boot",
        },
        tmp_path / "model.json",
    )
    dummy = tmp_path / "dummy.parquet"
    dummy.write_text("x")
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "LOL_SPLIT_PATH", dummy)
    monkeypatch.setattr(backtest_run, "LOL_VALIDATION_PATH", dummy)
    monkeypatch.setattr(backtest_run, "LOL_BACKTEST_MARKET_SECONDS_PATH", dummy)
    monkeypatch.setattr(backtest_run, "LOL_BACKTEST_AUDIT_PATH", dummy)

    manifest = build_run_manifest(
        model_dir=tmp_path,
        game="lol",
        signal_cadence_seed=0,
        run_policy=_current_policy("lol"),
        max_position_levels=BACKTEST_LOL_MAX_POSITION_LEVELS,
        selected_ids=(),
        whitelist_keys={},
        plans={},
        exclusions={},
        since_match=None,
        archives_only=False,
    )

    assert manifest["model_sha256"] == model_identity_sha256(tmp_path)
    assert manifest["model_path"] == str(tmp_path)
    assert "bank_sha256" not in manifest


def test_resolve_max_position_levels_follows_the_game(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(backtest_run.BACKTEST_MAX_POSITION_LEVELS, "dota", 9)
    monkeypatch.setitem(backtest_run.BACKTEST_MAX_POSITION_LEVELS, "lol", 6)
    assert backtest_run.resolve_max_position_levels("dota", None) == 9
    assert backtest_run.resolve_max_position_levels("lol", None) == 6


def test_ensemble_manifest_points_at_the_catalog_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An ensemble catalog records the directory, and SHA is not model.txt."""
    (tmp_path / "member_00.txt").write_text("a")
    (tmp_path / "member_01.txt").write_text("b")
    write_model_meta(
        {
            "name": "ens-k10",
            "trained_at": "2026-09-11T00:00:00Z",
            "train_dataset_sha256": "0" * 64,
            "validation_dataset_sha256": "1" * 64,
            "features": list(DOTA_XP_FEATURE_COLUMNS),
            "source_lag_seconds": TRAIN_LAG_SECONDS,
            "train_matches": 1,
            "metrics": None,
            "members": ["member_00.txt", "member_01.txt"],
            "member_trees": [1, 1],
            "ensemble_arm": "default_boot",
            "ensemble_k": 2,
            "ensemble_sampling": "boot",
        },
        tmp_path / "model.json",
    )
    monkeypatch.setattr(backtest_run, "read_framework_commit", lambda: "framework")
    monkeypatch.setattr(backtest_run, "sha256_file", _parquet_sha)
    manifest = build_run_manifest(
        model_dir=tmp_path,
        game="dota",
        signal_cadence_seed=0,
        run_policy=_current_policy("dota"),
        max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        selected_ids=(),
        whitelist_keys={},
        plans={},
        exclusions={},
        since_match=None,
        archives_only=False,
    )
    assert manifest["model_path"] == str(tmp_path)
    assert manifest["model_sha256"] == model_identity_sha256(tmp_path)
    assert not manifest["model_path"].endswith("model.txt")
    (tmp_path / "member_01.txt").write_text("c")
    with pytest.raises(ValueError, match="model_sha256"):
        assert_manifest_matches(
            {**manifest, "model_sha256": model_identity_sha256(tmp_path)},
            manifest,
        )


def test_parse_args_game_defaults_to_dota(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI without --game stays Dota with cadence seed 0 and no policy options."""
    monkeypatch.setattr(sys, "argv", ["backtest", "--validation", "--name", "run1"])
    args = parse_args()
    assert args.game == "dota"
    assert args.signal_cadence_seed == 0


def test_make_backtest_recipe_has_no_game_flag() -> None:
    """make backtest must keep invoking the module CLI with only $(ARGS)."""
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    text = makefile.read_text()
    recipe_start = text.index("backtest:")
    recipe_end = text.index("\nlol-backtest:")
    recipe = text[recipe_start:recipe_end]
    assert "python -m backtest.run $(ARGS)" in recipe
    assert "--game" not in recipe
    assert "--model-dir" not in recipe
    assert "--layers" not in recipe
    assert "--base-size-usdc" not in recipe
    assert "--buy-lifecycle" not in recipe


def test_parse_args_rejects_warm_cache_with_merge_or_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--warm-cache cannot merge shards or resume a results checkpoint."""
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--validation", "--warm-cache", "--merge-shards", "3"]
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()
    monkeypatch.setattr(sys, "argv", ["backtest", "--validation", "--warm-cache", "--resume"])
    with pytest.raises(SystemExit, match="2"):
        parse_args()


def test_parse_args_accepts_warm_cache_with_shard(monkeypatch: pytest.MonkeyPatch) -> None:
    """--warm-cache uses the same --shard slice as a validation run."""
    monkeypatch.setattr(
        sys, "argv", ["backtest", "--validation", "--game", "lol", "--warm-cache", "--shard", "0/3"]
    )
    args = parse_args()
    assert args.warm_cache is True
    assert args.shard is not None
    assert args.shard.index == 0
    assert args.shard.count == 3


def test_warm_replay_cache_loads_without_running_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warm-cache materializes Telonex sims and never starts the Nautilus engine."""
    loaded: list[str] = []

    class FakeBacktest:
        async def _load_sims_async(self) -> list[Any]:
            loaded.append("load")
            return [
                SimpleNamespace(instrument=SimpleNamespace(id="0xabc-111.POLYMARKET")),
                SimpleNamespace(instrument=SimpleNamespace(id="0xabc-222.POLYMARKET")),
            ]

        def run(self) -> None:
            raise AssertionError("engine must not run")

    class FakeTree:
        def __enter__(self) -> Path:
            return tmp_path / "tree"

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_tree(
        _contexts: object,
        _capture_root: Path,
        _schedule_archives: object,
    ) -> FakeTree:
        """Stand in for the throwaway Telonex symlink tree."""
        return FakeTree()

    def fake_experiment(**_kwargs: object) -> SimpleNamespace:
        """Skip building a real ReplayExperiment."""
        return SimpleNamespace()

    def fake_backtest(_exp: object) -> FakeBacktest:
        """Return the load-only fake engine."""
        return FakeBacktest()

    monkeypatch.setattr(backtest_run, "create_telonex_source_tree", fake_tree)
    monkeypatch.setattr(backtest_run, "build_replay_experiment", fake_experiment)
    monkeypatch.setattr(backtest_run, "build_backtest_for_experiment", fake_backtest)

    warm_replay_cache(
        contexts=(build_context(),),
        capture_root=tmp_path,
        schedule_archives={},
    )
    assert loaded == ["load"]


def test_parse_args_records_signal_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cadence seed lands on parsed args."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--validation",
            "--name",
            "run1",
            "--signal-cadence-seed",
            "2",
        ],
    )
    args = parse_args()
    assert args.signal_cadence_seed == 2


def test_parse_args_rejects_bad_signal_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative cadence seed is a CLI error."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest", "--validation", "--name", "run1", "--signal-cadence-seed", "-1"],
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()


@pytest.mark.parametrize("flag", REMOVED_POLICY_FLAGS)
def test_parse_args_rejects_every_removed_policy_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str
) -> None:
    """No deleted strategy switch survives as a CLI escape hatch."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest", "--validation", "--name", "run1", flag, "1"],
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()


@pytest.mark.parametrize("game", ["dota", "lol"])
def test_parse_args_accepts_the_ordinary_validation_run(
    monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """One normal CLI command runs Follow300 for either game."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest", "--validation", "--name", "cleanup-check", "--game", game],
    )
    args = parse_args()
    assert args.game == game
    assert args.name == "cleanup-check"
    assert args.model_dir is None
    assert args.min_abs_delta is None
    assert not hasattr(args, "layers")
    assert not hasattr(args, "buy_lifecycle")
    assert not hasattr(args, "buy_levels")
    assert not hasattr(args, "buy_level_usdc")


def test_parse_args_accepts_model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--model-dir pins a catalog; a directory without model.json is a CLI error."""
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    argv = [
        "backtest",
        "--validation",
        "--game",
        "lol",
        "--name",
        "run1",
        "--model-dir",
        str(catalog),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit, match="2"):
        parse_args()
    (catalog / "model.json").write_text(json.dumps({"features": DOTA_XP_FEATURE_COLUMNS}))
    monkeypatch.setattr(sys, "argv", list(argv))
    args = parse_args()
    assert args.model_dir == catalog.resolve()


def test_parse_args_accepts_min_abs_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest", "--validation", "--name", "delta015", "--min-abs-delta", "0.015"],
    )
    args = parse_args()
    assert args.min_abs_delta == 0.015


def test_parse_args_accepts_exit_abs_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Schmitt exit flag defaults to None and parses as a float."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest", "--validation", "--name", "run1"],
    )
    args = parse_args()
    assert args.exit_abs_delta is None
    assert not hasattr(args, "max_abs_nw_delta_30")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--validation",
            "--name",
            "hyst",
            "--exit-abs-delta",
            "0.015",
        ],
    )
    args = parse_args()
    assert args.exit_abs_delta == 0.015


def test_parse_args_rejects_exit_above_the_entry_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """exit > entry would invert the Schmitt gate; the parser refuses it."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backtest",
            "--validation",
            "--name",
            "bad",
            "--min-abs-delta",
            "0.01",
            "--exit-abs-delta",
            "0.02",
        ],
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest", "--validation", "--name", "bad", "--exit-abs-delta", "0.03"],
    )
    with pytest.raises(SystemExit, match="2"):
        parse_args()


def test_parse_args_validation_requires_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """--validation without --warm-cache must pass --name so runs do not collide."""
    monkeypatch.setattr(sys, "argv", ["backtest", "--validation"])
    with pytest.raises(SystemExit, match="2"):
        parse_args()
    monkeypatch.setattr(sys, "argv", ["backtest", "--validation", "--merge-shards", "5"])
    with pytest.raises(SystemExit, match="2"):
        parse_args()


def test_batches_never_mix_venue_delays_and_cap_their_size() -> None:
    """Markets split by secondsDelay first, then into batches the engine can hold."""
    cap = MAX_MATCHES_PER_BATCH
    fast = [build_context(match_id=index, seconds_delay=1) for index in range(cap * 2 + 3)]
    slow = [build_context(match_id=100 + index, seconds_delay=3) for index in range(5)]

    batches = split_into_batches([*slow, *fast])

    assert all(1 <= len(batch) <= cap for batch in batches)
    assert all(len({context.seconds_delay for context in batch}) == 1 for batch in batches)
    n_fast_batches = (len(fast) + cap - 1) // cap
    n_slow_batches = (len(slow) + cap - 1) // cap
    assert [batch[0].seconds_delay for batch in batches] == (
        [1] * n_fast_batches + [3] * n_slow_batches
    )
    flattened_fast = [
        context.match_id for batch in batches if batch[0].seconds_delay == 1 for context in batch
    ]
    assert flattened_fast == [context.match_id for context in fast]


def test_instrument_ids_follow_token_order() -> None:
    """Both legs resolve to the framework's `<condition>-<token>.POLYMARKET` ids."""
    context = build_context()
    assert context.instrument_ids == ("0xabc-111.POLYMARKET", "0xabc-222.POLYMARKET")


def test_book_replays_use_distinct_side_labels() -> None:
    """Each token leg gets a unique sim_label so report series do not collide."""
    context = build_context()
    replays = context.as_book_replays()
    labels: list[str] = []
    for replay in replays:
        metadata = replay.metadata
        assert metadata is not None
        labels.append(str(metadata["sim_label"]))
    assert len(set(labels)) == 2
    assert labels[context.radiant_token_index].endswith("-radiant")
    assert labels[1 - context.radiant_token_index].endswith("-dire")


def test_build_report_dir() -> None:
    """Single-match stays flat; --limit and full validation nest under seedN."""
    root = Path("data/backtests/dota_maker")
    policy = _current_policy("dota")
    assert (
        build_report_dir(
            report_root=root,
            match_id=1234,
            limit=None,
            name=None,
            signal_cadence_seed=0,
            run_policy=policy,
            archives_only=False,
        )
        == root / "match_1234"
    )
    assert (
        build_report_dir(
            report_root=root,
            match_id=None,
            limit=2,
            name="x",
            signal_cadence_seed=0,
            run_policy=policy,
            archives_only=False,
        )
        == root / "validation_limit_2_x" / "seed0"
    )
    full = build_report_dir(
        report_root=root,
        match_id=None,
        limit=None,
        name="x",
        signal_cadence_seed=1,
        run_policy=policy,
        archives_only=False,
    )
    assert full == root / "validation_join_delta02_x015_cut480_p45_x" / "seed1"
    one_cent = build_report_dir(
        report_root=root,
        match_id=None,
        limit=None,
        name="x",
        signal_cadence_seed=1,
        run_policy=replace(policy, min_abs_delta=0.01, exit_abs_delta=0.01),
        archives_only=False,
    )
    assert one_cent == root / "validation_join_delta01_cut480_p45_x" / "seed1"
    one_and_half = build_report_dir(
        report_root=root,
        match_id=None,
        limit=None,
        name="x",
        signal_cadence_seed=0,
        run_policy=replace(policy, min_abs_delta=0.015, exit_abs_delta=0.015),
        archives_only=False,
    )
    assert one_and_half == root / "validation_join_delta015_cut480_p45_x" / "seed0"
    hysteresis = build_report_dir(
        report_root=root,
        match_id=None,
        limit=None,
        name="x",
        signal_cadence_seed=0,
        run_policy=policy,
        archives_only=False,
    )
    assert hysteresis == root / "validation_join_delta02_x015_cut480_p45_x" / "seed0"
    archive = build_report_dir(
        report_root=root,
        match_id=None,
        limit=None,
        name="x",
        signal_cadence_seed=0,
        run_policy=policy,
        archives_only=True,
    )
    assert archive == root / "validation_join_delta02_x015_cut480_p45_x" / "_archive"


def test_resolve_run_policies_keeps_factory_hysteresis_without_cli() -> None:
    policies = resolve_run_policies(
        level_usdc=BACKTEST_LEVEL_USDC["dota"],
        match_ids=(1,),
        min_abs_delta=None,
        exit_abs_delta=None,
    )
    policy = policies[1]
    assert policy.min_abs_delta == MIN_ABS_DELTA
    assert policy.exit_abs_delta == EXIT_ABS_DELTA


def test_strategy_configs_carry_no_policy_switches() -> None:
    """Every per-match strategy config holds market and signal data only."""
    context = build_context()
    signals = MatchSignals(
        feed_timestamps_ns=(0,),
        timestamps_ns=(0,),
        source_timestamps_ns=(0,),
        predicted_deltas=(0.05,),
        dataset_market_ps=(0.5,),
        deaths_radiant=(),
        deaths_dire=(),
        kill_gates=(),
        board_tick_ns=(),
    )
    policies = {context.match_id: _current_policy("dota")}
    plans = {context.match_id: GridV1Plan(match_id=context.match_id, model_dir=Path("research"))}
    configs = build_strategy_configs(
        game="dota",
        contexts=(context,),
        signals={context.match_id: signals},
        pauses_by_match={context.match_id: []},
        order_latency_ns=0,
        cancel_latency_ns=0,
        kernels=build_kernels(
            contexts=(context,),
            policies=policies,
            plans=plans,
            max_position_levels=BACKTEST_DOTA_MAX_POSITION_LEVELS,
        ),
        plans=plans,
    )
    assert len(configs) == 1
    keys = set(configs[0]["config"])
    assert keys.isdisjoint(
        {
            "layers",
            "layer_step_ticks",
            "base_size_usdc",
            "buy_lifecycle",
            "ladder_reprice_mode",
            "buy_after_first_fill",
            "buy_depth_cap_multiple",
            "sell_drop_block_delta",
        }
    )
    assert "match_id" in keys
    assert "instrument_ids" in keys
    kernel = configs[0]["config"]["kernel"]
    assert kernel.policy.level_usdc == BACKTEST_LEVEL_USDC["dota"]
    assert kernel.limits.radiant_token_index == context.radiant_token_index


def test_lol_grid_v1_shifts_cutoff_and_game_end_by_source_lag() -> None:
    """LoL grid-v1 decisions land 11s late, so the cutoff clocks move with them."""
    context = build_context()
    signals = MatchSignals(
        feed_timestamps_ns=(0,),
        timestamps_ns=(0,),
        source_timestamps_ns=(0,),
        predicted_deltas=(0.05,),
        dataset_market_ps=(0.5,),
        deaths_radiant=(),
        deaths_dire=(),
        kill_gates=(),
        board_tick_ns=(),
    )
    plans = {context.match_id: GridV1Plan(match_id=context.match_id, model_dir=Path("research"))}

    def clocks(game: Game) -> tuple[int, int]:
        policies = {context.match_id: _current_policy(game)}
        config = build_strategy_configs(
            game=game,
            contexts=(context,),
            signals={context.match_id: signals},
            pauses_by_match={context.match_id: []},
            order_latency_ns=0,
            cancel_latency_ns=0,
            kernels=build_kernels(
                contexts=(context,),
                policies=policies,
                plans=plans,
                max_position_levels=BACKTEST_MAX_POSITION_LEVELS[game],
            ),
            plans=plans,
        )[0]["config"]
        return config["buy_cutoff_ns"], config["game_end_ns"]

    dota_cutoff, dota_end = clocks("dota")
    lol_cutoff, lol_end = clocks("lol")
    lag_ns = LOL_SOURCE_LAG_SECONDS * NS_PER_SECOND
    assert lol_cutoff - dota_cutoff == lag_ns
    assert lol_end - dota_end == lag_ns
