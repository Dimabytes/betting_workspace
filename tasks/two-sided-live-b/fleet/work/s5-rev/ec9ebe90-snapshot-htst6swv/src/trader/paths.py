"""Trader wallet and archive file locations."""

from shared.constants.paths import TRADER_DIR

TRADER_WALLET_DIR = TRADER_DIR / "wallet"
GRID_STATE_ARCHIVE_FILENAME = "grid_state.jsonl"
ODDIN_STATE_ARCHIVE_FILENAME = "oddin_state.jsonl"
MATCH_META_FILENAME = "match.json"
SESSION_JOURNAL_FILENAME = "session.jsonl"
CORE_TRACE_FILENAME = "core_trace.jsonl"
EXECUTION_CLEANUP_FILENAME = "execution_cleanup.json"
