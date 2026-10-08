"""Shared dataset chronology constants."""

VALIDATION_START_TIME = 1_780_563_592
# Collector/Telonex day holes (e.g. 2026-08-08) skip this many empty-book
# misses; more than this still aborts so a broken capture cannot silently
# empty the split. Train is larger, so its cap sits on the known hole
# (161 maps with no in-window book as of the spread-6 rebuild).
MAX_VALIDATION_HARD_MISSES = 20
MAX_TRAIN_HARD_MISSES = 161
MODEL_START_SECOND = -60
MODEL_TARGET_HORIZON_SECONDS = 300
# STRATZ snapshot age baked into training rows: state at S, market at S+lag.
TRAIN_LAG_SECONDS = 10
# STRATZ snapshot age at execution: validation rows and backtest predict.
BACKTEST_LAG_SECONDS = 10
PREHORN_LEAD_SECONDS = -(MODEL_START_SECOND + BACKTEST_LAG_SECONDS)
