"""Tests for `trader.session_config`: the generated fork config of one session.

The trading template is the only source of tuning values, so these tests
pin its schema, the three generated TOML files and the rule that no tuning
number is ever written in Python.
"""

# The session composes private engine seams by design; pinning them is the
# point of these tests. The production session modules are the only place
# that uses them.
# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

import inspect
import json
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from polymaker.config import Config, MarketEntry, StrategyProfile
from trader_session_fixtures import CONDITION_ID

from shared.constants.strategy import MIN_ENTRY_PRICE
from trader import (
    session_config,
    session_types,
)
from trader.clip_rules import choose_clip
from trader.game_profile import GAME_PROFILES, GameProfile

DOTA = (GAME_PROFILES["dota"],)
LOL = (GAME_PROFILES["lol"],)
BOTH = (GAME_PROFILES["dota"], GAME_PROFILES["lol"])


def _guard_floor(account_cap_usdc: float) -> float:
    return round(2.0 * account_cap_usdc / MIN_ENTRY_PRICE, 2)


def _wallet(tmp_path: Path, games: tuple[GameProfile, ...]) -> session_config.MaterializedConfigDir:
    """Load the current template once, then write one wallet config dir."""
    return session_config.materialize_wallet_config_dir(
        tmp_path / "wallet.db", tmp_path / "journal", session_config.read_template(games)
    )


def _write_template(
    path: Path, template: dict[str, Any], extra: dict[str, dict[str, object]] | None = None
) -> None:
    """Write one trading.toml from an in-memory template dict."""
    lines = session_config._toml_table_lines("wallet", template["wallet"])
    lines += session_config._toml_table_lines("engine", template["engine"])
    lines += session_config._toml_table_lines("risk", template["risk"])
    for name, table in template["profiles"].items():
        lines += session_config._toml_table_lines(f"profiles.{name}", table)
    clips = template.get("clips")
    if type(clips) is dict:
        lines += _clip_lines(cast(dict[str, Any], clips))
    if extra is not None:
        for name, table in extra.items():
            lines += session_config._toml_table_lines(name, table)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clip_lines(clips: dict[str, Any]) -> list[str]:
    """Serialize [clips.*] tiers, which _toml_table_lines cannot."""
    lines: list[str] = []
    for game, table in clips.items():
        lines.append(f"[clips.{game}]")
        if "default" in table:
            lines.append(f"default = {table['default']!r}")
        tiers = table["tiers"]
        if not tiers:
            lines.append("tiers = []")
            continue
        lines.append("tiers = [")
        for tier in tiers:
            names = ", ".join(json.dumps(name) for name in tier["names"])
            lines.append(f"  {{ clip = {tier['clip']!r}, names = [{names}] }},")
        lines.append("]")
    return lines


def test_materialized_config_follows_the_real_template_and_loads(tmp_path: Path) -> None:
    """Generated files mirror the committed template; real Config.load resolves them."""
    document = session_config.read_template(BOTH)
    template = {
        "engine": document.engine,
        "risk": document.risk,
        "wallet": document.wallet,
        "profiles": document.profiles,
    }
    materialized = _wallet(tmp_path, BOTH)
    try:
        generated_config = tomllib.loads(
            (materialized.config_dir / "config.toml").read_text(encoding="utf-8")
        )
        assert generated_config["engine"] == template["engine"]
        assert generated_config["risk"] == template["risk"]
        assert generated_config["wallet"] == template["wallet"]
        assert set(generated_config["paths"]) == {"db", "journal_dir"}
        assert generated_config["paths"]["db"] == str(tmp_path / "wallet.db")
        assert generated_config["paths"]["journal_dir"] == str(tmp_path / "journal")
        generated_strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        assert generated_strategy == {"profiles": template["profiles"]}
        generated_markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        assert generated_markets.get("markets", []) == []
        assert "kalshi" not in generated_config
        assert "kalshi" not in generated_strategy
        assert "kalshi" not in generated_markets

        cfg = Config.load(materialized.config_dir, load_env=False)
        profile = cfg.profile_for(MarketEntry(condition_id=CONDITION_ID, profile="dota-map"))
        assert profile == StrategyProfile(**cast(dict[str, Any], template["profiles"]["dota-map"]))
        assert cfg.paths.db == str(tmp_path / "wallet.db")
        assert cfg.engine.journal is True
        assert cfg.wallet.signature_type == template["wallet"]["signature_type"]
    finally:
        materialized.cleanup()


def test_materialized_values_follow_a_modified_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tuned values flow through; the clip size drives every derived USDC limit."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    template["engine"]["debounce_ms"] = 77
    template["risk"]["ws_stale_halt_s"] = 43.5
    del template["profiles"]["lol-map"]
    floor = _guard_floor(float(template["risk"]["account_cap_usdc"]))
    daily_loss = template["risk"]["daily_loss_kill_usdc"]
    template["profiles"]["dota-map"]["base_size_usdc"] = 200.0
    template["profiles"]["dota-map"]["micro_levels"] = 7
    template["profiles"]["dota-map"]["event_cooloff_s"] = 11.0
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    materialized = _wallet(tmp_path, DOTA)
    try:
        generated_config = tomllib.loads(
            (materialized.config_dir / "config.toml").read_text(encoding="utf-8")
        )
        generated_strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        assert generated_config["engine"]["debounce_ms"] == 77
        assert generated_config["risk"]["ws_stale_halt_s"] == 43.5
        assert generated_strategy["profiles"]["dota-map"]["micro_levels"] == 7
        assert generated_strategy["profiles"]["dota-map"]["event_cooloff_s"] == 11.0
        # One knob: the new clip size drives this profile's USDC limits; risk
        # rides the game's largest clip, here the edited $200 dota-map clip.
        profile = generated_strategy["profiles"]["dota-map"]
        assert profile["base_size_usdc"] == 200.0
        assert profile["q_max_usdc"] == floor
        assert profile["merge_min_size"] == 200.0
        assert generated_config["risk"]["daily_loss_kill_usdc"] == daily_loss
        assert generated_config["risk"]["max_total_exposure_usdc"] == floor
        assert generated_config["risk"]["max_event_group_loss_usdc"] == floor
        assert generated_config["risk"]["max_market_notional_usdc"] == floor
        generated_markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        assert generated_markets.get("markets", []) == []
        assert "kalshi" not in generated_config
        assert "kalshi" not in generated_strategy
        assert "kalshi" not in generated_markets
    finally:
        materialized.cleanup()


def test_dota_only_template_writes_dota_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dota-map-only file uses dota's largest clip for risk and writes dota-map."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    floor = _guard_floor(float(template["risk"]["account_cap_usdc"]))
    daily_loss = template["risk"]["daily_loss_kill_usdc"]
    del template["profiles"]["lol-map"]
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    document = session_config.read_template(DOTA)
    assert document.risk["daily_loss_kill_usdc"] == daily_loss
    assert document.profiles["dota-map"]["q_max_usdc"] == floor
    materialized = _wallet(tmp_path, DOTA)
    try:
        generated_strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        generated_markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        assert set(generated_strategy["profiles"]) == {
            "dota-map",
            "dota-oddin-map",
        }
        assert generated_markets.get("markets", []) == []
    finally:
        materialized.cleanup()


def test_lol_only_template_writes_lol_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A lol-map-only file uses the LoL clip for risk and writes lol-map."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    floor = _guard_floor(float(template["risk"]["account_cap_usdc"]))
    daily_loss = template["risk"]["daily_loss_kill_usdc"]
    del template["profiles"]["dota-map"]
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    document = session_config.read_template(LOL)
    assert document.risk["daily_loss_kill_usdc"] == daily_loss
    assert document.risk["max_total_exposure_usdc"] == floor
    assert document.risk["max_event_group_loss_usdc"] == floor
    assert document.risk["max_market_notional_usdc"] == floor
    assert document.profiles["lol-map"]["q_max_usdc"] == floor
    materialized = _wallet(tmp_path, LOL)
    try:
        generated_strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        generated_markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        assert set(generated_strategy["profiles"]) == {"lol-map"}
        assert generated_markets.get("markets", []) == []
    finally:
        materialized.cleanup()


def test_joint_template_writes_both_profiles(tmp_path: Path) -> None:
    """Joint file: risk from the per-game max sum; every assigned profile written."""
    document = session_config.read_template(BOTH)
    floor = _guard_floor(document.account_cap_usdc)
    assert document.profiles["dota-map"]["q_max_usdc"] == floor
    assert document.profiles["lol-map"]["q_max_usdc"] == floor
    materialized = _wallet(tmp_path, BOTH)
    try:
        generated_strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        generated_markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        generated_config = tomllib.loads(
            (materialized.config_dir / "config.toml").read_text(encoding="utf-8")
        )
        assert set(generated_strategy["profiles"]) == {
            "dota-map",
            "dota-oddin-map",
            "lol-map",
        }
        assert generated_markets.get("markets", []) == []
        assert (
            generated_config["risk"]["daily_loss_kill_usdc"]
            == document.risk["daily_loss_kill_usdc"]
        )
    finally:
        materialized.cleanup()


@pytest.mark.parametrize(
    "keep, games",
    [
        (("dota-map",), BOTH),
        (("dota-map",), LOL),
        (("dota-map",), DOTA),
        (("dota-map", "lol-map"), BOTH),
    ],
)
def test_read_template_rejects_missing_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    keep: tuple[str, ...],
    games: tuple[GameProfile, ...],
) -> None:
    """Missing profile tables are a trading setup fault; extra tables are ignored."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    template["profiles"] = {name: template["profiles"][name] for name in keep}
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    with pytest.raises(session_types.TradingDisabled, match="loaded game profiles"):
        session_config.read_template(games)


def test_read_template_committed_file_loads_assigned_clips() -> None:
    """Committed trading.toml: extra profiles ignored; risk follows assigned clips."""
    raw = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    floor = _guard_floor(float(raw["risk"]["account_cap_usdc"]))
    dota = session_config.read_template(DOTA)
    assert dota.risk["daily_loss_kill_usdc"] == raw["risk"]["daily_loss_kill_usdc"]
    assert set(dota.profiles) == {"dota-map", "dota-oddin-map"}
    assert (
        dota.profiles["dota-oddin-map"]["base_size_usdc"]
        == raw["profiles"]["dota-oddin-map"]["base_size_usdc"]
    )
    assert dota.profiles["dota-oddin-map"]["q_max_usdc"] == floor
    lol = session_config.read_template(LOL)
    assert lol.risk["daily_loss_kill_usdc"] == raw["risk"]["daily_loss_kill_usdc"]
    assert set(lol.profiles) == {"lol-map"}


def test_read_template_satellite_clips_default_to_profile_clip() -> None:
    """clips.dota-oddin carries tiers only; the satellite profile's clip is the default."""
    raw = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    dota = session_config.read_template(DOTA)
    assert set(dota.clips) == {"dota-map", "dota-oddin-map"}
    satellite = dota.clips["dota-oddin-map"]
    assert satellite.default_usdc == raw["profiles"]["dota-oddin-map"]["base_size_usdc"]
    title = "Dota 2: MOUZ vs Nemiga (BO3) - PARI Universe Closed Qualifier Playoffs"
    pari_clip = raw["clips"]["dota-oddin"]["tiers"][0]["clip"]
    assert choose_clip(satellite, title).clip_usdc == pari_clip
    assert (
        choose_clip(satellite, "Dota 2: A vs B (BO3) - Other League").clip_usdc
        == satellite.default_usdc
    )


def test_read_template_requires_satellite_clip_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping clips.dota-oddin while dota loads is a trading setup fault."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    del template["clips"]["dota-oddin"]
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    with pytest.raises(session_types.TradingDisabled, match="clip table"):
        session_config.read_template(DOTA)


def test_materialize_wallet_config_dir_picks_lol_map_when_only_lol(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LoL-only template writes profiles.lol-map into the wallet strategy file."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    del template["profiles"]["dota-map"]
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    materialized = session_config.materialize_wallet_config_dir(
        tmp_path / "wallet.db", tmp_path / "journal", session_config.read_template(LOL)
    )
    try:
        generated_strategy = tomllib.loads(
            (materialized.config_dir / "strategy.toml").read_text(encoding="utf-8")
        )
        markets = tomllib.loads(
            (materialized.config_dir / "markets.toml").read_text(encoding="utf-8")
        )
        assert set(generated_strategy["profiles"]) == {"lol-map"}
        assert (
            generated_strategy["profiles"]["lol-map"]["base_size_usdc"]
            == template["profiles"]["lol-map"]["base_size_usdc"]
        )
        assert markets.get("markets", []) == []
    finally:
        materialized.cleanup()


def test_materializer_never_hands_the_template_to_config_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config.load only ever sees the generated directory, never the template file."""
    seen: list[Path] = []
    real_load = Config.load

    def recording_load(config_dir: str | Path, *, load_env: bool = True) -> Config:
        seen.append(Path(config_dir))
        return real_load(config_dir, load_env=load_env)

    monkeypatch.setattr(Config, "load", recording_load)
    materialized = _wallet(tmp_path, BOTH)
    try:
        assert seen == []  # materialization itself never loads config
        Config.load(materialized.config_dir, load_env=False)
        assert seen == [materialized.config_dir]
        assert session_config.TEMPLATE_PATH not in seen
        assert session_config.TEMPLATE_PATH.parent not in seen
    finally:
        materialized.cleanup()


def test_materializer_has_no_tuning_literals() -> None:
    """The materializer's source carries no copy of the template's tuning values."""
    source = inspect.getsource(session_config.materialize_wallet_config_dir)
    source += inspect.getsource(session_config._write_config_toml)
    source += inspect.getsource(session_config._toml_scalar)
    source += inspect.getsource(session_config._toml_table_lines)
    for token in (
        "3600",
        "1000",
        "20.0",
        "3.0",
        "base_size_usdc",
        "q_max_usdc",
        "reconcile_interval_s",
        "debounce_ms",
        "catalog_refresh_s",
        "event_jump_ticks",
        "kalshi",
        "base_size_usd",
    ):
        assert token not in source


def test_template_validation_rejects_foreign_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A template with extra tables or values is a trading setup fault."""
    bad = tmp_path / "bad.toml"
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    _write_template(bad, template, extra={"foo": {"chain_id": 1}})
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", bad)
    with pytest.raises(
        session_types.TradingDisabled, match="engine, risk, profiles, wallet and clips"
    ):
        session_config.read_template(BOTH)


@pytest.mark.parametrize(
    "table, key, value",
    [
        ("engine", "debounc_ms", 100),  # typo key: silently ignored by EngineConfig
        ("engine", "debounce_ms", 100.0),  # int field fed a float literal
        ("risk", "ws_stale_halt_s", -1.0),  # negative tuning value
        ("risk", "account_cap_usd", 2500.0),  # typo of the explicit account cap
        ("profiles.dota-map", "q_max_usdc", 400.0),  # derived limit hand-written back in
        ("profiles.dota-map", "micro_levels", -1),  # negative profile value
        ("profiles.dota-map", "micro_levelss", 1),  # typo key in the profile
        ("profiles.lol-map", "q_max_usdc", 80.0),  # derived LoL limit hand-written back in
        ("profiles.dota-oddin-map", "event_jump_ticks", 15),  # satellite is clip-only
    ],
)
def test_template_schema_rejects_typo_keys_and_bad_literals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    table: str,
    key: str,
    value: object,
) -> None:
    """A typo'd key or wrong literal in the template fails closed."""
    template = tomllib.loads(session_config.TEMPLATE_PATH.read_text(encoding="utf-8"))
    if table.startswith("profiles."):
        name = table.removeprefix("profiles.")
        cast(dict[str, Any], template["profiles"][name])[key] = value
    else:
        cast(dict[str, Any], template[table])[key] = value
    modified = tmp_path / "trading.toml"
    _write_template(modified, template)
    monkeypatch.setattr(session_config, "TEMPLATE_PATH", modified)
    with pytest.raises(session_types.TradingDisabled):
        session_config.read_template(BOTH)
