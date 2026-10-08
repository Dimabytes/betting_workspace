"""Materialize one session's generated fork config from the trading template."""

import json
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from shared.constants.paths import BASE_DIR
from shared.constants.strategy import MIN_ENTRY_PRICE
from trader.clip_rules import ClipTable, ClipTier
from trader.game_profile import GameProfile, strategy_catalogs
from trader.session_types import TradingDisabled

TEMPLATE_PATH = BASE_DIR / "config" / "trading.toml"


@dataclass(frozen=True)
class MaterializedConfigDir:
    """One session-scoped temporary config directory; cleanup removes it."""

    owner: TemporaryDirectory[str]
    config_dir: Path

    def cleanup(self) -> None:
        """Remove the temporary directory."""
        self.owner.cleanup()


_TEMPLATE_TABLES = frozenset({"engine", "risk", "profiles", "wallet", "clips"})


@dataclass(frozen=True)
class ConfigTemplate:
    """Validated ownership tables; profiles keyed by profile_name, clips by game.

    account_cap_usdc is ours. It is stripped out of risk before the fork config
    is written. daily_loss_kill_usdc stays in risk because poly-maker reads it.
    """

    engine: dict[str, object]
    risk: dict[str, object]
    profiles: dict[str, dict[str, object]]
    wallet: dict[str, object]
    clips: dict[str, ClipTable]  # keyed by profile_name
    account_cap_usdc: float


def read_template(games: tuple[GameProfile, ...]) -> ConfigTemplate:
    """Loaded games must exist in the file; extra profile and clip tables are ignored.

    account_cap_usdc and daily_loss_kill are explicit dollars. poly-maker guards
    sit at 2 * account_cap / MIN_ENTRY_PRICE. merge_min_size still tracks each
    profile's base_size_usdc. account_cap_usdc is not written into the fork.
    """
    if not games:
        raise TradingDisabled("config template games must not be empty")
    try:
        with TEMPLATE_PATH.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        raise TradingDisabled("config template cannot be read") from None
    if frozenset(document) != _TEMPLATE_TABLES:
        raise TradingDisabled(
            "config template must carry exactly the engine, risk, profiles, wallet and clips tables"
        )
    engine_table = cast(object, document.get("engine"))
    risk_table = cast(object, document.get("risk"))
    profiles = cast(object, document.get("profiles"))
    wallet_table = cast(object, document.get("wallet"))
    clips_table = cast(object, document.get("clips"))
    if (
        type(engine_table) is not dict
        or type(risk_table) is not dict
        or type(profiles) is not dict
        or type(wallet_table) is not dict
        or type(clips_table) is not dict
    ):
        raise TradingDisabled("config template tables are malformed")
    profiles_table = cast(dict[str, object], profiles)
    expected = {
        catalog.profile_name
        for game_profile in games
        for catalog in strategy_catalogs(game_profile)
    }
    if not expected.issubset(set(profiles_table)):
        raise TradingDisabled("config template must carry the loaded game profiles")
    engine = cast(dict[str, object], engine_table)
    risk = cast(dict[str, object], risk_table)
    wallet = cast(dict[str, object], wallet_table)
    _require_template_table("engine", engine, _TEMPLATE_SCHEMA["engine"])
    _require_template_table("risk", risk, _TEMPLATE_SCHEMA["risk"])
    _require_template_table("wallet", wallet, _TEMPLATE_SCHEMA["wallet"])
    _require_positive_caps(risk)
    account_cap_usdc = cast(float, risk.pop("account_cap_usdc"))
    signature_type = wallet["signature_type"]
    if signature_type not in {0, 1, 2, 3}:
        raise TradingDisabled("config template wallet.signature_type must be 0, 1, 2 or 3")
    resolved: dict[str, dict[str, object]] = {}
    for game_profile in games:
        name = game_profile.primary.profile_name
        parent = _profile_table(profiles_table, name, _TEMPLATE_SCHEMA["profiles"])
        resolved[name] = parent | _dollar_limits(cast(float, parent["base_size_usdc"]))
        for satellite in game_profile.satellites.values():
            satellite_table = _resolved_satellite(profiles_table, satellite.profile_name, parent)
            resolved[satellite.profile_name] = satellite_table
    guard_floor = _guard_floor_usdc(account_cap_usdc)
    for table in resolved.values():
        table["q_max_usdc"] = guard_floor
    return ConfigTemplate(
        engine=engine,
        risk=risk | dict.fromkeys(_RISK_GUARD_KEYS, guard_floor),
        profiles=resolved,
        wallet=wallet,
        clips=_clip_tables(cast(dict[str, object], clips_table), games, resolved),
        account_cap_usdc=account_cap_usdc,
    )


_TEMPLATE_SCHEMA: dict[str, dict[str, type[object]]] = {
    "engine": {
        "debounce_ms": int,
        "quoter_tick_s": float,
        "catalog_refresh_s": float,
        "reconcile_interval_s": float,
    },
    "risk": {
        "ws_stale_halt_s": float,
        "user_ws_blind_halt_s": float,
        "heartbeat_halt_failures": int,
        "max_order_error_rate": float,
        "account_cap_usdc": float,
        "daily_loss_kill_usdc": float,
    },
    "wallet": {
        "signature_type": int,
    },
    "profiles": {
        "micro_levels": int,
        "flow_ewma_halflife_s": float,
        "vol_short_halflife_s": float,
        "vol_long_halflife_s": float,
        "base_size_usdc": float,
        "event_cooloff_s": float,
        "event_jump_ticks": int,
        "event_sweep_mult": float,
        "event_sweep_frac": float,
        "trend_flow_z": float,
        "trend_vol_ratio": float,
        "end_date_taper_days": float,
        "reduce_only_hours": float,
        "halt_before_hours": float,
        "exit_urgency_s": float,
    },
}

# Satellite tables carry only the clip. Other knobs copy the parent profile.
_SATELLITE_SCHEMA: dict[str, type[object]] = {
    "base_size_usdc": float,
}


DOLLAR_MULTIPLES: dict[str, float] = {
    "merge_min_size": 1.0,
}

# poly-maker reduce-only guards set so they can never engage: inv_util and the
# notional caps value held shares at fv/mark, which can reach
# account_cap / MIN_ENTRY_PRICE while cost stays under the account cap, and
# every cap tapers quote size from 70% of itself. Twice that clears both.
# The kernel's map cap and account cap are the BUY limits.
_RISK_GUARD_KEYS = (
    "max_total_exposure_usdc",
    "max_event_group_loss_usdc",
    "max_market_notional_usdc",
)


def _require_positive_caps(risk: dict[str, object]) -> None:
    account_cap = cast(float, risk["account_cap_usdc"])
    daily_loss = cast(float, risk["daily_loss_kill_usdc"])
    if account_cap <= 0.0 or daily_loss <= 0.0:
        raise TradingDisabled("config template risk caps must be positive")


def _guard_floor_usdc(account_cap_usdc: float) -> float:
    """A dollar value no poly-maker guard can reach before the account cap."""
    return round(2.0 * account_cap_usdc / MIN_ENTRY_PRICE, 2)


def _profile_table(
    profiles_table: dict[str, object], name: str, schema: Mapping[str, type[object]]
) -> dict[str, object]:
    """One raw profile table after the pinned schema check."""
    profile = cast(object, profiles_table.get(name))
    if type(profile) is not dict:
        raise TradingDisabled(f"config template {name} profile is malformed")
    table = cast(dict[str, object], profile)
    _require_template_table(f"profiles.{name}", table, schema)
    return table


def _resolved_satellite(
    profiles_table: dict[str, object], name: str, parent: Mapping[str, object]
) -> dict[str, object]:
    """Parent knobs with this table's clip and the clip's USDC limits."""
    table = _profile_table(profiles_table, name, _SATELLITE_SCHEMA)
    clip = cast(float, table["base_size_usdc"])
    merged = dict(parent)
    merged["base_size_usdc"] = clip
    return merged | _dollar_limits(clip)


def _dollar_limits(base_size_usdc: float) -> dict[str, object]:
    """Profile USDC limits as their pinned multiple of that profile's clip."""
    return {key: round(base_size_usdc * multiple, 2) for key, multiple in DOLLAR_MULTIPLES.items()}


def _clip_tables(
    clips_table: dict[str, object],
    games: tuple[GameProfile, ...],
    profiles: dict[str, dict[str, object]],
) -> dict[str, ClipTable]:
    """One clip table per loaded catalog, keyed by profile name.

    The primary reads clips.<game>; a satellite reads clips.<game>-<feed>,
    which carries tiers only — the satellite profile's clip is the default.
    Tables for catalogs that are not loaded are ignored.
    """
    needed: set[str] = set()
    for game_profile in games:
        needed.add(game_profile.game)
        needed.update(f"{game_profile.game}-{source.value}" for source in game_profile.satellites)
    if not needed.issubset(clips_table):
        raise TradingDisabled(
            "config template must carry a clip table for each loaded game and feed"
        )
    tables: dict[str, ClipTable] = {}
    for game_profile in games:
        tables[game_profile.primary.profile_name] = _clip_table(
            game_profile.game, clips_table[game_profile.game]
        )
        for source, catalog in game_profile.satellites.items():
            key = f"{game_profile.game}-{source.value}"
            default = cast(float, profiles[catalog.profile_name]["base_size_usdc"])
            tables[catalog.profile_name] = _satellite_clip_table(key, default, clips_table[key])
    return tables


def _clip_table(game: str, raw: object) -> ClipTable:
    if type(raw) is not dict:
        raise TradingDisabled(f"config template clips.{game} is malformed")
    table = cast(dict[str, object], raw)
    if set(table) != {"default", "tiers"}:
        raise TradingDisabled(f"config template clips.{game} keys do not match the pinned schema")
    default = _positive_clip(f"clips.{game}.default", table["default"])
    return ClipTable(default, _clip_tiers(game, table["tiers"]))


def _satellite_clip_table(key: str, default: float, raw: object) -> ClipTable:
    """Satellite clip table: tiers only, the satellite profile's clip is the default."""
    if type(raw) is not dict:
        raise TradingDisabled(f"config template clips.{key} is malformed")
    table = cast(dict[str, object], raw)
    if set(table) != {"tiers"}:
        raise TradingDisabled(f"config template clips.{key} keys do not match the pinned schema")
    return ClipTable(default, _clip_tiers(key, table["tiers"]))


def _clip_tiers(key: str, raw: object) -> tuple[ClipTier, ...]:
    if type(raw) is not list:
        raise TradingDisabled(f"config template clips.{key}.tiers must be a list")
    return tuple(_clip_tier(key, index, item) for index, item in enumerate(cast(list[object], raw)))


def _positive_clip(label: str, value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise TradingDisabled(f"config template {label} must be a positive finite number")
    return value


def _clip_tier(game: str, index: int, raw: object) -> ClipTier:
    label = f"clips.{game}.tiers[{index}]"
    if type(raw) is not dict:
        raise TradingDisabled(f"config template {label} is malformed")
    table = cast(dict[str, object], raw)
    if set(table) != {"clip", "names"}:
        raise TradingDisabled(f"config template {label} keys do not match the pinned schema")
    names = table["names"]
    if type(names) is not list or not names:
        raise TradingDisabled(f"config template {label}.names must be a non-empty list")
    cleaned: list[str] = []
    for name in cast(list[object], names):
        if type(name) is not str or not name.strip():
            raise TradingDisabled(f"config template {label}.names must be non-empty strings")
        cleaned.append(name)
    return ClipTier(_positive_clip(f"{label}.clip", table["clip"]), tuple(cleaned))


def _require_template_table(
    name: str, table: Mapping[str, object], schema: Mapping[str, type[object]]
) -> None:
    """Require the exact key set and raw value types; numbers must be non-negative."""
    if set(table) != set(schema):
        raise TradingDisabled(f"config template {name} keys do not match the pinned schema")
    for key, expected in schema.items():
        value = table[key]
        if type(value) is not expected:
            raise TradingDisabled(f"config template {name}.{key} has the wrong value type")
        if isinstance(value, float):
            if not math.isfinite(value) or value < 0.0:
                raise TradingDisabled(
                    f"config template {name}.{key} is not a non-negative finite number"
                )
        elif cast(int, value) < 0:
            raise TradingDisabled(f"config template {name}.{key} must be non-negative")


def materialize_wallet_config_dir(
    db_path: Path,
    journal_dir: Path,
    template: ConfigTemplate,
) -> MaterializedConfigDir:
    """Write process-wide fork config: empty markets, every assigned game's profile."""
    document = template
    owner = TemporaryDirectory(prefix="trader-wallet-config-")
    config_dir = Path(owner.name)
    _write_config_toml(
        config_dir / "config.toml",
        document.engine,
        document.risk,
        document.wallet,
        db_path,
        journal_dir,
    )
    strategy_lines: list[str] = []
    for name, table in document.profiles.items():
        strategy_lines += _toml_table_lines(f"profiles.{name}", table)
    _write_text(config_dir / "strategy.toml", strategy_lines)
    _write_text(config_dir / "markets.toml", [])
    return MaterializedConfigDir(owner, config_dir)


def _write_config_toml(
    path: Path,
    engine_table: Mapping[str, object],
    risk_table: Mapping[str, object],
    wallet_table: Mapping[str, object],
    db_path: Path,
    journal_dir: Path,
) -> None:
    """Write config.toml: the template engine/risk/wallet tables plus process paths."""
    lines = _toml_table_lines("wallet", wallet_table)
    lines += _toml_table_lines("engine", engine_table)
    lines += _toml_table_lines("risk", risk_table)
    lines.append("[paths]")
    lines.append(f"db = {_toml_string(str(db_path))}")
    lines.append(f"journal_dir = {_toml_string(str(journal_dir))}")
    _write_text(path, lines)


def _toml_table_lines(name: str, table: Mapping[str, object]) -> list[str]:
    """Serialize one table header plus its key/value lines."""
    return [f"[{name}]", *[f"{key} = {_toml_scalar(value)}" for key, value in table.items()]]


def _toml_scalar(value: object) -> str:
    """Render one template tuning value (bool, int, or float after schema pin)."""
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        return repr(value)
    raise TradingDisabled(f"config template carries an unsupported value {type(value).__name__}")


def _toml_string(value: str) -> str:
    """Render one basic TOML string via JSON escaping (a compatible subset)."""
    return json.dumps(value, ensure_ascii=False)


def _write_text(path: Path, lines: list[str]) -> None:
    """Write one generated TOML file as newline-terminated UTF-8 text."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
