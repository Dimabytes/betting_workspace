"""Filesystem roots and data artifacts shared between pipeline stages."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]  # repo root
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

NEW_PROCESSED_DIR = DATA_DIR / "new_processed"

RAW_POLYMARKET_DOTA_DIR = RAW_DIR / "polymarket_dota"
RAW_OPENDOTA_MATCHES_DIR = RAW_DIR / "opendota_matches"
RAW_STRATZ_MATCHES_DIR = RAW_DIR / "stratz_matches"

# Archived Gamma event pages: the only historical source of closedTime and the
# sports secondsDelay, both of which the live API rewrites over time.
POLYMARKET_UNIVERSE_EVENTS_DIR = RAW_POLYMARKET_DOTA_DIR / "universe" / "events"

RAW_TELONEX_POLYMARKET_DIR = RAW_DIR / "telonex" / "polymarket"

DOTA_UNIVERSE_PATH = NEW_PROCESSED_DIR / "universe" / "universe.parquet"
NEW_DATASET_DIR = NEW_PROCESSED_DIR / "dataset"
MATCH_CATALOG_DIR = NEW_PROCESSED_DIR / "match_catalog"
MATCH_CATALOG_PATH = MATCH_CATALOG_DIR / "match_catalog.parquet"
TRAINING_DATASET_PATH = NEW_DATASET_DIR / "training_dataset.parquet"
VALIDATION_DATASET_PATH = NEW_DATASET_DIR / "validation_dataset.parquet"
# Exact game-second feature rows for archive-schedule backtests (Dota).
GAME_FEATURES_DATASET_PATH = NEW_DATASET_DIR / "game_features.parquet"
# Minute-boundary game levels through map end; the trainer's history tape (Dota).
GAME_HISTORY_MINUTES_PATH = NEW_DATASET_DIR / "game_history_minutes.parquet"
# Exact STRATZ death counts at the model start and each death second (Dota).
GAME_DEATHS_PATH = NEW_DATASET_DIR / "game_deaths.parquet"
PRODUCTION_DATASET_DIR = NEW_DATASET_DIR / "production"
PRODUCTION_TRAINING_DATASET_PATH = PRODUCTION_DATASET_DIR / "training_dataset.parquet"
PRODUCTION_DATASET_SPLIT_PATH = PRODUCTION_DATASET_DIR / "split.parquet"

MARKET_SECONDS_DIR = NEW_PROCESSED_DIR / "market_seconds"
# Per-match sports fee terms captured 2026-10-02: 156 v2 (0.03/0.25) and 692 v3 (0.05/0.15).
MARKET_TERMS_PATH = DATA_DIR / "backtests" / "market_terms.json"

NEW_MODEL_DIR = DATA_DIR / "new_model"
# Live model dirs: research/ and production/ keep XP; *-noxp drop radiant_xp_adv.
# A catalog is the directory (model.json plus member_*.txt).
RESEARCH_MODEL_DIR = NEW_MODEL_DIR / "research"
RESEARCH_MODEL_SPLIT_PATH = RESEARCH_MODEL_DIR / "split.parquet"  # rows: SplitRow
PRODUCTION_MODEL_DIR = NEW_MODEL_DIR / "production"
PRODUCTION_MODEL_META_PATH = PRODUCTION_MODEL_DIR / "model.json"
RESEARCH_NOXP_MODEL_DIR = NEW_MODEL_DIR / "research-noxp"
PRODUCTION_NOXP_MODEL_DIR = NEW_MODEL_DIR / "production-noxp"
# Each publish moves the replaced live dir here, under its own branch.
MODEL_ARCHIVE_DIR = NEW_MODEL_DIR / "archive"
RESEARCH_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "research"
PRODUCTION_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "production"
RESEARCH_NOXP_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "research-noxp"
PRODUCTION_NOXP_ARCHIVE_DIR = MODEL_ARCHIVE_DIR / "production-noxp"

# Trader match archive: one directory per tracked match.
TRADER_DIR = DATA_DIR / "trader"
STATE_ARCHIVE_FILENAME = "state.jsonl"

# Archive index + extracted feed schedules (archive_index package output).
ARCHIVE_INDEX_DIR = DATA_DIR / "archive_index"
