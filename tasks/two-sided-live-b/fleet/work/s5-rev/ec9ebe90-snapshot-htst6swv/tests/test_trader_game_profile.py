"""Tests for frozen Dota and LoL GameProfile constants."""

from dataclasses import FrozenInstanceError, fields

import pytest

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
    GRID_HISTORY_POLICY,
    ODDIN_HISTORY_POLICY,
)
from shared.utils.team_names import TEAM_ALIASES
from trader.game_profile import GAME_PROFILES, StrategyCatalog, strategy_catalog
from trader.grid_widgets import DIRE_SIDE, RADIANT_SIDE
from trader.live_feed import FeedSource

_MUTABLE_TYPES = (list, dict, set, bytearray)


def test_game_profiles_are_exactly_dota_and_lol() -> None:
    """GAME_PROFILES has two keys, each matching that profile's game identity."""
    assert set(GAME_PROFILES) == {"dota", "lol"}
    assert len(GAME_PROFILES) == 2
    for key, profile in GAME_PROFILES.items():
        assert profile.game == key


def test_dota_profile_fields() -> None:
    """Dota stores RADIANT/DIRE, LEVEL_XP, and Steam."""
    profile = GAME_PROFILES["dota"]
    assert profile.game == "dota"
    assert profile.archive_root_env == "DOTA_ARCHIVE_ROOT"
    assert profile.mode_env == "DOTA_TRADING_MODE"
    assert profile.level_xp is LEVEL_XP
    assert profile.side_0_text == RADIANT_SIDE
    assert profile.side_1_text == DIRE_SIDE
    assert profile.primary.profile_name == "dota-map"
    assert profile.primary.model_dir is PRODUCTION_MODEL_DIR
    assert profile.primary.features == tuple(DOTA_XP_FEATURE_COLUMNS)
    assert profile.primary.source_lag_seconds == TRAIN_LAG_SECONDS
    assert dict(profile.satellites) == {
        FeedSource.ODDIN: StrategyCatalog(
            "dota-oddin-map",
            PRODUCTION_NOXP_MODEL_DIR,
            tuple(DOTA_NOXP_FEATURE_COLUMNS),
            TRAIN_LAG_SECONDS,
        )
    }
    assert profile.uses_steam is True
    assert profile.aliases == TEAM_ALIASES


def test_lol_profile_fields() -> None:
    """LoL stores BLUE/RED, LOL_LEVEL_XP, and no Steam."""
    profile = GAME_PROFILES["lol"]
    assert profile.game == "lol"
    assert profile.archive_root_env == "LOL_ARCHIVE_ROOT"
    assert profile.mode_env == "LOL_TRADING_MODE"
    assert profile.level_xp is LOL_LEVEL_XP
    assert profile.side_0_text == "BLUE"
    assert profile.side_1_text == "RED"
    assert profile.primary.profile_name == "lol-map"
    assert profile.primary.model_dir is LOL_PRODUCTION_MODEL_DIR
    assert profile.primary.features == tuple(DOTA_XP_FEATURE_COLUMNS)
    assert profile.primary.source_lag_seconds == LOL_SOURCE_LAG_SECONDS
    assert profile.satellites == {}
    assert profile.uses_steam is False
    assert profile.aliases == LOL_TEAM_ALIASES


def test_strategy_catalog_picks_the_feed_history_policy() -> None:
    """GRID and LoL run the GRID tape policy; the Oddin satellite keeps its own."""
    assert strategy_catalog(game="dota", feed_source=FeedSource.GRID).history is GRID_HISTORY_POLICY
    assert strategy_catalog(game="lol", feed_source=FeedSource.GRID).history is GRID_HISTORY_POLICY
    assert (
        strategy_catalog(game="dota", feed_source=FeedSource.ODDIN).history is ODDIN_HISTORY_POLICY
    )


def test_game_profiles_contain_only_real_game_differences() -> None:
    """Live profiles do not carry identical or always-None policy fields."""
    for profile in GAME_PROFILES.values():
        assert not hasattr(profile, "max_abs_nw_delta_30")
        assert not hasattr(profile, "unwind_after_seconds")
        assert not hasattr(profile, "model_signal_end_second")


def test_profiles_are_frozen() -> None:
    """setattr cannot mutate a profile; level_xp stays a tuple."""
    for profile in GAME_PROFILES.values():
        with pytest.raises(FrozenInstanceError):
            profile.__setattr__("game", "mutated")
        assert type(profile.level_xp) is tuple


def test_profiles_do_not_share_mutable_collections() -> None:
    """Dota and LoL are distinct; field values are not mutable collections."""
    dota = GAME_PROFILES["dota"]
    lol = GAME_PROFILES["lol"]
    assert dota is not lol
    assert dota.level_xp is not lol.level_xp
    for profile in (dota, lol):
        for field in fields(profile):
            assert type(getattr(profile, field.name)) not in _MUTABLE_TYPES
    copied = dict(GAME_PROFILES)
    copied["x"] = dota
    assert set(GAME_PROFILES) == {"dota", "lol"}
