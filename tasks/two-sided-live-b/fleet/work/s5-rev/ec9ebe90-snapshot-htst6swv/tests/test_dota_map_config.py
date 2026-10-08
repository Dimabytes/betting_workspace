"""Offline test of the committed tuning template `config/trading.toml`.

The repository template is the US-010 source of truth: each `[profiles.*]`
table must load through the fork's real StrategyProfile under extra="forbid",
and every engine/risk value must land in its actual owner table. The file is
read with stdlib tomllib from BASE_DIR, so the test is CWD-independent and
never touches Config.load, Engine, network, or Docker.

Each profile's `base_size_usdc` sizes merge. The live rung is `[clips]`.
`daily_loss_kill_usdc` and `account_cap_usdc` are explicit. poly-maker guards
sit at 2 * account_cap / min entry price. `dota-oddin-map` is a satellite.
"""

import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from polymaker.config import EngineConfig, RiskConfig, StrategyProfile, WalletConfig
from pydantic import ValidationError

from shared.constants.paths import BASE_DIR
from shared.constants.strategy import MIN_ENTRY_PRICE
from trader import session_config
from trader.game_profile import GAME_PROFILES, strategy_profile_name
from trader.live_feed import FeedSource

TEMPLATE_PATH = BASE_DIR / "config" / "trading.toml"

BOTH = (GAME_PROFILES["dota"], GAME_PROFILES["lol"])

EXPECTED_ENGINE = {
    "debounce_ms": 100,
    "quoter_tick_s": 2.0,
    "catalog_refresh_s": 3600.0,
    "reconcile_interval_s": 20.0,
}

PINNED_RISK = {
    "ws_stale_halt_s": 30.0,
    "user_ws_blind_halt_s": 15.0,
    "heartbeat_halt_failures": 3,
    "max_order_error_rate": 0.25,
}
LIMIT_RISK_KEYS = ("account_cap_usdc", "daily_loss_kill_usdc")

# merge_min_size still tracks the profile clip. Guards ride the account cap.
EXPECTED_DOLLAR_MULTIPLES = {
    "profiles": {
        "merge_min_size": 1.0,
    },
}
EXPECTED_GUARD_KEYS = (
    "max_total_exposure_usdc",
    "max_event_group_loss_usdc",
    "max_market_notional_usdc",
)

EXPECTED_WALLET = {
    "signature_type": 2,
}

EXPECTED_PROFILE = {
    # fair value
    "micro_levels": 3,
    "flow_ewma_halflife_s": 30.0,
    # volatility
    "vol_short_halflife_s": 10.0,
    "vol_long_halflife_s": 120.0,
    # regimes
    "event_cooloff_s": 20.0,
    "event_jump_ticks": 15,
    "event_sweep_mult": 4.0,
    "event_sweep_frac": 0.8,
    "trend_flow_z": 1.5,
    "trend_vol_ratio": 2.0,
    # lifecycle / exits
    "end_date_taper_days": 7.0,
    "reduce_only_hours": 0.0,
    "halt_before_hours": 0.0,
    "exit_urgency_s": 300.0,
}

PROFILE_INT_KEYS = {
    "micro_levels",
    "event_jump_ticks",
}

PRIMARY_PROFILES = ("dota-map", "lol-map")
FILE_PROFILES = ("dota-map", "dota-oddin-map", "lol-map")
SATELLITE_PROFILES = ("dota-oddin-map",)
SATELLITE_FILE_KEYS = frozenset({"base_size_usdc"})
SATELLITE_DIVERGED_KEYS = frozenset({"base_size_usdc", "merge_min_size"})


def load_template() -> dict[str, Any]:
    """Read the committed trading.toml as parsed TOML tables."""
    with TEMPLATE_PATH.open("rb") as fh:
        return tomllib.load(fh)


def _base_sizes(parsed: dict[str, Any]) -> dict[str, float]:
    profiles = cast(dict[str, Any], parsed["profiles"])
    return {
        name: float(cast(dict[str, Any], profiles[name])["base_size_usdc"])
        for name in FILE_PROFILES
    }


def _guard_floor(account_cap_usdc: float) -> float:
    return round(2.0 * account_cap_usdc / MIN_ENTRY_PRICE, 2)


def _assert_raw_profile(profile_raw: dict[str, Any], base_size_usdc: float) -> None:
    """Pin one committed profile table's keys, clip, and literal types."""
    assert set(profile_raw) == set(EXPECTED_PROFILE) | {"base_size_usdc"}
    assert type(profile_raw["base_size_usdc"]) is float
    assert profile_raw["base_size_usdc"] == base_size_usdc
    assert "event_sweep_levels" not in profile_raw
    assert "q_max_usdc" not in profile_raw
    assert "merge_min_size" not in profile_raw
    for key, expected in EXPECTED_PROFILE.items():
        assert profile_raw[key] == expected
        expected_type = int if key in PROFILE_INT_KEYS else float
        assert type(profile_raw[key]) is expected_type


def test_template_splits_and_profile() -> None:
    """The exact four-table split; both profiles load under extra=forbid."""
    parsed = load_template()
    sizes = _base_sizes(parsed)
    assert set(parsed) == {"engine", "risk", "profiles", "wallet", "clips"}
    assert set(cast(dict[str, Any], parsed["profiles"])) == set(FILE_PROFILES)

    profiles_raw = cast(dict[str, Any], parsed["profiles"])
    for name in PRIMARY_PROFILES:
        _assert_raw_profile(cast(dict[str, Any], profiles_raw[name]), sizes[name])
    for name in SATELLITE_PROFILES:
        satellite_raw = cast(dict[str, Any], profiles_raw[name])
        assert set(satellite_raw) == SATELLITE_FILE_KEYS
        assert satellite_raw["base_size_usdc"] == sizes[name]

    assert StrategyProfile.model_config.get("extra") == "forbid"
    document = session_config.read_template(BOTH)
    for name in FILE_PROFILES:
        resolved = document.profiles[name]
        with pytest.raises(ValidationError):
            StrategyProfile.model_validate({**resolved, "typo": 1})
        profile = StrategyProfile.model_validate(resolved)
        for key, expected in EXPECTED_PROFILE.items():
            assert getattr(profile, key) == expected
            expected_type = int if key in PROFILE_INT_KEYS else float
            assert type(getattr(profile, key)) is expected_type
        assert profile.event_sweep_levels == 3
        assert profile.base_size_usdc == sizes[name]


def test_engine_table() -> None:
    """Engine numbers land in EngineConfig with the requested values and types."""
    parsed = load_template()
    engine_raw = cast(dict[str, Any], parsed["engine"])
    fork_engine = {key: engine_raw[key] for key in EXPECTED_ENGINE}
    assert set(engine_raw) == set(EXPECTED_ENGINE)
    for key, expected in EXPECTED_ENGINE.items():
        assert engine_raw[key] == expected
        expected_type = int if key == "debounce_ms" else float
        assert type(engine_raw[key]) is expected_type

    engine = EngineConfig(**fork_engine)
    for key, expected in EXPECTED_ENGINE.items():
        assert getattr(engine, key) == expected
        expected_type = int if key == "debounce_ms" else float
        assert type(getattr(engine, key)) is expected_type


def test_risk_table() -> None:
    """Risk numbers land in RiskConfig with the requested values and types."""
    parsed = load_template()
    risk_raw = cast(dict[str, Any], parsed["risk"])
    assert set(risk_raw) == set(PINNED_RISK) | set(LIMIT_RISK_KEYS)
    for key, expected in PINNED_RISK.items():
        assert risk_raw[key] == expected
        expected_type = int if key == "heartbeat_halt_failures" else float
        assert type(risk_raw[key]) is expected_type
    for key in LIMIT_RISK_KEYS:
        assert type(risk_raw[key]) is float
        assert risk_raw[key] > 0.0

    materialized = session_config.read_template(BOTH)
    assert materialized.account_cap_usdc == risk_raw["account_cap_usdc"]
    assert "account_cap_usdc" not in materialized.risk
    risk = RiskConfig(**cast(dict[str, Any], materialized.risk))
    for key, expected in PINNED_RISK.items():
        assert getattr(risk, key) == expected
        expected_type = int if key == "heartbeat_halt_failures" else float
        assert type(getattr(risk, key)) is expected_type
    assert risk.daily_loss_kill_usdc == risk_raw["daily_loss_kill_usdc"]


def test_dollar_limits_ride_base_size() -> None:
    """Joint file: daily loss from the largest-clip sum; guards at the session floor."""
    assert EXPECTED_DOLLAR_MULTIPLES["profiles"] == session_config.DOLLAR_MULTIPLES
    parsed = load_template()
    sizes = _base_sizes(parsed)
    floor = _guard_floor(float(cast(dict[str, Any], parsed["risk"])["account_cap_usdc"]))
    daily_loss = cast(dict[str, Any], parsed["risk"])["daily_loss_kill_usdc"]
    document = session_config.read_template(BOTH)
    dota = document.profiles["dota-map"]
    oddin = document.profiles["dota-oddin-map"]
    lol = document.profiles["lol-map"]
    assert dota["base_size_usdc"] == sizes["dota-map"]
    assert oddin["base_size_usdc"] == sizes["dota-oddin-map"]
    assert lol["base_size_usdc"] == sizes["lol-map"]
    assert document.risk["daily_loss_kill_usdc"] == daily_loss
    assert document.risk["max_total_exposure_usdc"] == floor
    assert document.risk["max_event_group_loss_usdc"] == floor
    assert document.risk["max_market_notional_usdc"] == floor
    assert dota["q_max_usdc"] == floor
    assert dota["merge_min_size"] == sizes["dota-map"]
    assert oddin["q_max_usdc"] == floor
    assert oddin["merge_min_size"] == sizes["dota-oddin-map"]
    assert lol["q_max_usdc"] == floor
    assert lol["merge_min_size"] == sizes["lol-map"]
    file_risk = cast(dict[str, Any], parsed["risk"])
    file_profiles = cast(dict[str, Any], parsed["profiles"])
    for key in EXPECTED_GUARD_KEYS:
        assert key not in file_risk
        value = document.risk[key]
        assert type(value) is float
    for name in FILE_PROFILES:
        file_table = cast(dict[str, Any], file_profiles[name])
        for key in (*EXPECTED_DOLLAR_MULTIPLES["profiles"], "q_max_usdc"):
            assert key not in file_table
            value = document.profiles[name][key]
            assert type(value) is float


def test_wallet_table() -> None:
    """Wallet signature_type lands in WalletConfig; secrets stay out of the file."""
    parsed = load_template()
    wallet_raw = cast(dict[str, Any], parsed["wallet"])
    assert set(wallet_raw) == set(EXPECTED_WALLET)
    assert wallet_raw["signature_type"] == 2
    assert type(wallet_raw["signature_type"]) is int

    wallet = WalletConfig(**wallet_raw)
    assert wallet.signature_type == 2


def test_template_has_no_kalshi_table() -> None:
    """The template is Polymarket only; no kalshi table and no USD clip keys."""
    parsed = load_template()
    assert "kalshi" not in parsed
    assert "kalshi" not in session_config.TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    document = session_config.read_template(BOTH)
    assert "dota_base_size_usd" not in document.profiles["dota-map"]
    assert "lol_base_size_usd" not in document.profiles["lol-map"]
    assert "base_size_usd" not in document.profiles["dota-map"]
    assert "base_size_usd" not in document.profiles["lol-map"]


def test_dollar_limits_scale_when_a_clip_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Editing one profile clip changes that merge size. Guards stay on the account cap."""
    parsed = load_template()
    risk = cast(dict[str, Any], parsed["risk"])
    sizes = _base_sizes(parsed)
    floor = _guard_floor(float(risk["account_cap_usdc"]))
    current = sizes["dota-map"]
    modified = tmp_path / "trading.toml"
    modified.write_text(
        TEMPLATE_PATH.read_text(encoding="utf-8").replace(
            f"base_size_usdc = {current}", "base_size_usdc = 200.0", 1
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    document = session_config.read_template(BOTH)
    assert document.profiles["dota-map"]["base_size_usdc"] == 200.0
    assert document.profiles["dota-map"]["q_max_usdc"] == floor
    assert document.profiles["dota-map"]["merge_min_size"] == 200.0
    assert document.profiles["dota-oddin-map"]["base_size_usdc"] == sizes["dota-oddin-map"]
    assert document.profiles["dota-oddin-map"]["q_max_usdc"] == floor
    assert document.profiles["lol-map"]["q_max_usdc"] == floor
    assert document.risk["daily_loss_kill_usdc"] == risk["daily_loss_kill_usdc"]
    assert document.risk["max_total_exposure_usdc"] == floor
    assert document.risk["max_event_group_loss_usdc"] == floor
    assert document.risk["max_market_notional_usdc"] == floor


def test_single_game_caps_use_that_clip_only() -> None:
    """Dota-only load keeps the file clip. Guards sit on the account cap."""
    parsed = load_template()
    risk = cast(dict[str, Any], parsed["risk"])
    sizes = _base_sizes(parsed)
    floor = _guard_floor(float(risk["account_cap_usdc"]))
    document = session_config.read_template((GAME_PROFILES["dota"],))
    assert document.profiles["dota-map"]["base_size_usdc"] == sizes["dota-map"]
    assert document.risk["daily_loss_kill_usdc"] == risk["daily_loss_kill_usdc"]
    assert document.risk["max_total_exposure_usdc"] == floor
    assert document.risk["max_event_group_loss_usdc"] == floor
    assert document.risk["max_market_notional_usdc"] == floor
    assert document.profiles["dota-map"]["q_max_usdc"] == floor
    assert document.profiles["dota-oddin-map"]["q_max_usdc"] == floor
    assert set(document.profiles) == {"dota-map", "dota-oddin-map"}


def test_satellites_inherit_dota_map_except_clip() -> None:
    """Resolved Oddin copies dota-map; only the clip and its USDC limits may diverge."""
    document = session_config.read_template(BOTH)
    dota = document.profiles["dota-map"]
    for name in SATELLITE_PROFILES:
        satellite = document.profiles[name]
        assert set(satellite) == set(dota)
        diverged = {key for key in dota if dota[key] != satellite[key]}
        assert diverged <= SATELLITE_DIVERGED_KEYS


def test_oddin_satellite_follows_dota_map_knob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dota-map regime change lands on Oddin; the satellite file has no copy to drift."""
    modified = tmp_path / "trading.toml"
    modified.write_text(
        TEMPLATE_PATH.read_text(encoding="utf-8").replace(
            "event_jump_ticks = 15", "event_jump_ticks = 9", 1
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    document = session_config.read_template(BOTH)
    assert document.profiles["dota-map"]["event_jump_ticks"] == 9
    assert document.profiles["dota-oddin-map"]["event_jump_ticks"] == 9
    assert document.profiles["lol-map"]["event_jump_ticks"] == 15


def test_dota_feed_selects_satellite_clip() -> None:
    """GRID stays on dota-map; Oddin is the $5 satellite; LoL is unchanged."""
    assert strategy_profile_name(game="dota", feed_source=FeedSource.GRID) == "dota-map"
    assert strategy_profile_name(game="dota", feed_source=FeedSource.ODDIN) == "dota-oddin-map"
    assert strategy_profile_name(game="lol", feed_source=FeedSource.GRID) == "lol-map"
