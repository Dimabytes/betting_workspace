"""Frozen per-game constants. Dota and LoL differ; callers import GAME_PROFILES."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from shared.constants.dataset import TRAIN_LAG_SECONDS
from shared.constants.dota import LEVEL_XP
from shared.constants.lol import (
    LOL_LEVEL_XP,
    LOL_PRODUCTION_MODEL_DIR,
    LOL_SOURCE_LAG_SECONDS,
    LOL_TEAM_ALIASES,
)
from shared.constants.paths import PRODUCTION_MODEL_DIR, PRODUCTION_NOXP_MODEL_DIR
from shared.utils.dota_features import (
    DOTA_NOXP_FEATURE_COLUMNS,
    DOTA_XP_FEATURE_COLUMNS,
    HistoryPolicy,
    history_policy_for_columns,
)
from shared.utils.team_names import TEAM_ALIASES
from trader.live_feed import FeedSource


@dataclass(frozen=True)
class StrategyCatalog:
    """One loadable model catalog: clip profile name, directory, features, and lag."""

    profile_name: str
    model_dir: Path
    features: tuple[str, ...]
    source_lag_seconds: int

    @property
    def history(self) -> HistoryPolicy:
        """The tape policy this catalog's feature columns imply."""
        return history_policy_for_columns(self.features)


@dataclass(frozen=True)
class GameProfile:
    """One game's trader constants. All fields required; no lookup methods."""

    game: str
    archive_root_env: str
    mode_env: str
    level_xp: tuple[int, ...]
    side_0_text: str
    side_1_text: str
    primary: StrategyCatalog
    satellites: Mapping[FeedSource, StrategyCatalog]
    uses_steam: bool
    aliases: Mapping[str, tuple[str, ...]]


GAME_PROFILES: dict[str, GameProfile] = {
    "dota": GameProfile(
        game="dota",
        archive_root_env="DOTA_ARCHIVE_ROOT",
        mode_env="DOTA_TRADING_MODE",
        level_xp=LEVEL_XP,
        side_0_text="RADIANT",
        side_1_text="DIRE",
        primary=StrategyCatalog(
            "dota-map",
            PRODUCTION_MODEL_DIR,
            tuple(DOTA_XP_FEATURE_COLUMNS),
            TRAIN_LAG_SECONDS,
        ),
        satellites=MappingProxyType(
            {
                FeedSource.ODDIN: StrategyCatalog(
                    "dota-oddin-map",
                    PRODUCTION_NOXP_MODEL_DIR,
                    tuple(DOTA_NOXP_FEATURE_COLUMNS),
                    TRAIN_LAG_SECONDS,
                ),
            }
        ),
        uses_steam=True,
        aliases=MappingProxyType(TEAM_ALIASES),
    ),
    "lol": GameProfile(
        game="lol",
        archive_root_env="LOL_ARCHIVE_ROOT",
        mode_env="LOL_TRADING_MODE",
        level_xp=LOL_LEVEL_XP,
        side_0_text="BLUE",
        side_1_text="RED",
        primary=StrategyCatalog(
            "lol-map",
            LOL_PRODUCTION_MODEL_DIR,
            tuple(DOTA_XP_FEATURE_COLUMNS),
            LOL_SOURCE_LAG_SECONDS,
        ),
        satellites=MappingProxyType({}),
        uses_steam=False,
        aliases=MappingProxyType(LOL_TEAM_ALIASES),
    ),
}


def strategy_catalog(*, game: str, feed_source: FeedSource) -> StrategyCatalog:
    """The catalog a feed trades under: satellite when quoted, else primary."""
    profile = GAME_PROFILES[game]
    return profile.satellites.get(feed_source) or profile.primary


def strategy_profile_name(*, game: str, feed_source: FeedSource) -> str:
    """A feed with its own clip quotes that clip; every other feed keeps the game profile."""
    return strategy_catalog(game=game, feed_source=feed_source).profile_name


def strategy_catalogs(profile: GameProfile) -> tuple[StrategyCatalog, ...]:
    """Every catalog this game loads: the primary first, then feed satellites."""
    return (profile.primary, *profile.satellites.values())
