"""LoL artifacts and game rules consumed by multiple pipeline stages."""

from datetime import timedelta
from pathlib import Path

LOL_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "lol"
LOL_RAW_GAMMA_DIR = LOL_DATA_DIR / "raw" / "polymarket" / "gamma"
LOL_UNIVERSE_DIR = LOL_DATA_DIR / "processed" / "universe"
LOL_UNIVERSE_PATH = LOL_UNIVERSE_DIR / "markets.parquet"

LOL_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "skt": ("t1", "sk telecom t1"),
    "t1": ("skt", "sk telecom t1"),
    "damwon": ("dplus kia", "dk", "dwg"),
    "dplus kia": ("damwon", "dk", "dwg"),
    "dk": ("dplus kia", "damwon", "dwg"),
    "ruddy sack": ("ruddy corporation",),
    "ruddy corporation": ("ruddy sack",),
}

LOL_RAW_TELONEX_DIR = LOL_DATA_DIR / "raw" / "telonex" / "polymarket"

# Cumulative XP at each champion level, 1..20. One table for every patch:
# item prices change with the shop catalog, these thresholds do not.
# 1..18 is the V3.14 table (verified on Leaguepedia V5). 19 and 20 are the
# Season 2026 Top Lane Role Quest rungs (wiki 20340 / 22420, patch 26.01 /
# ddragon 16.1); same +100-per-level step as 17→18.
LOL_LEVEL_XP: tuple[int, ...] = (
    0,
    280,
    660,
    1140,
    1720,
    2400,
    3180,
    4060,
    5040,
    6120,
    7300,
    8580,
    9960,
    11440,
    13020,
    14700,
    16480,
    18360,
    20340,
    22420,
)
# GRID series_table arrives this long after the livestats frame (measured 10.98s).
# grid-v1, the training join, and model.json all use this offset.
LOL_SOURCE_LAG_SECONDS = 11
LOL_DATASETS_DIR = LOL_DATA_DIR / "processed" / "datasets"
LOL_VALIDATION_PATH = LOL_DATASETS_DIR / "validation.parquet"
LOL_SPLIT_PATH = LOL_DATASETS_DIR / "split.parquet"
# Exact game-second feature rows for archive-schedule backtests (LoL).
LOL_GAME_FEATURES_PATH = LOL_DATASETS_DIR / "game_features.parquet"

LOL_MODELS_DIR = LOL_DATA_DIR / "models"
LOL_RESEARCH_MODEL_DIR = LOL_MODELS_DIR / "research"
LOL_PRODUCTION_MODEL_DIR = LOL_MODELS_DIR / "production"
LOL_BACKTEST_MARKET_SECONDS_PATH = LOL_DATASETS_DIR / "market_seconds.parquet"
LOL_BACKTEST_AUDIT_PATH = LOL_DATASETS_DIR / "backtest_audit.parquet"
# Must stay equal to backtest.context.REPLAY_LEAD.
LOL_REPLAY_LEAD = timedelta(minutes=2)
REASON_MISSING_REQUIRED_CHANNEL = "missing_required_channel"
REASON_ZERO_SIGNAL_ROWS = "zero_signal_rows"
