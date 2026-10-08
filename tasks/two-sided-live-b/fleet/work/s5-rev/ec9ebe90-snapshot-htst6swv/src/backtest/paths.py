"""Backtest report directories and artifact filenames."""

from shared.constants.paths import DATA_DIR

BACKTESTS_DIR = DATA_DIR / "backtests"
DOTA_MAKER_BACKTESTS_DIR = BACKTESTS_DIR / "dota_maker"
LOL_MAKER_BACKTESTS_DIR = BACKTESTS_DIR / "lol_maker"

# Artifact names inside one backtest run directory. They live here so a reader can
# open a run without importing the backtest package and its framework dependency.
RESULTS_FILENAME = "results.parquet"
FILLS_FILENAME = "fills.parquet"
QUOTE_EVENTS_FILENAME = "quote_events.parquet"
SUMMARY_FILENAME = "summary.json"
MANIFEST_FILENAME = "manifest.json"
