"""Shared join lifecycle constants. One definition for backtest and live paper."""

# Model edge decays with the clock: gated capture (realized/predicted move) falls
# from 1.3 in minutes 1-2 to 0.52 in minute 8. Entries at 480-540 added 13$ of
# 1708$ per seed on 13% of turnover and were negative on 7 of 12 seeds.
BUY_CUTOFF_SECOND = 480
MIN_ABS_DELTA = 0.02
EXIT_ABS_DELTA = 0.015
# Entry price floor: below it our fills lag the model signal by 3-4 cents.
MIN_ENTRY_PRICE = 0.45
# Entry price ceiling: at or above it, high-price BUYs lose.
MAX_ENTRY_PRICE = 0.85
# Drop a side before the max-edge pick when ask-bid is this many 0.01 ticks or more.
MAX_ENTRY_SPREAD_TICKS = 6
BASE_SIZE_USDC = 100.0
MIN_ORDER_SIZE = 5.0
# p50 CLOB round trip after the 2026-09-24 deploy: POST 173 ms, DELETE 62 ms
# (28 Dota sessions, joined on order_id). One constant per operation, no jitter.
ORDER_INSERT_LATENCY_MS = 175.0
ORDER_CANCEL_LATENCY_MS = 60.0
# ponytail: 10s covers CLOB token credit lag; we do not read spendable balance. Raise if SELL still races a BUY fill.
EXIT_SETTLE_SECONDS = 10.0
# A resting SELL is re-emitted, not repriced, for this long after a successful
# place. Quoting only: it never delays a cancel.
SELL_MIN_LIFE_SECONDS = 1.0
QUOTE_GRID = 0.01
BUY_LEVEL_COUNT = 3
BUY_LEVEL_STEP_TICKS = 1
# This map's cap, in rungs of that map's clip: held cost plus standing BUY
# notional on the card stay under the cap times level_usdc.
LIVE_DOTA_MAX_POSITION_LEVELS = 9
LIVE_LOL_MAX_POSITION_LEVELS = 10
BACKTEST_DOTA_MAX_POSITION_LEVELS = 9
BACKTEST_LOL_MAX_POSITION_LEVELS = 9
LIVE_MAX_POSITION_LEVELS = {
    "dota": LIVE_DOTA_MAX_POSITION_LEVELS,
    "lol": LIVE_LOL_MAX_POSITION_LEVELS,
}
BACKTEST_MAX_POSITION_LEVELS = {
    "dota": BACKTEST_DOTA_MAX_POSITION_LEVELS,
    "lol": BACKTEST_LOL_MAX_POSITION_LEVELS,
}
BUY_POLICY_VERSION = "follow300-v8"
# Between game-state ticks (GRID lands every 6-11 s) the held delta re-anchors
# to the live book at this cadence; 0.25 s beat 1 s in the 2026-09-28 cadence run.
LATCH_REANCHOR_SECONDS = 0.25
# Archive replay and extraction keep the recorded cadence. 0 would re-anchor on
# every quote, so off is a horizon that never elapses. Lonely-L0 uses 0 as off.
LATCH_REANCHOR_OFF_S = 1e9
# ponytail: live watchdog arms on unique-gold yields, not raw frames (Klim/Lynx unique-gold p50=3.6s, max=40s). 16s is a midway cut of 12s false STALE. Socket-silence fix still pending.
GRID_FEED_STALE_SECONDS = 16.0
# Pull a resting SELL only after this long with no yielded tick. Entry stays on
# the per-source stale_seconds above. 45s covers ~90% of live SELL-cancels from
# GRID holes; pause and missing_book still clear the cell on the tick.
EXIT_FEED_STALE_SECONDS = 45.0

# Cooloff when radiant mid drops this far below its recent peak (cancel BUYs only).
MID_SPIKE_LOOKBACK_S = 10.0
MID_SPIKE_THRESHOLD = 0.10
MID_SPIKE_COOLOFF_S = 30.0

# A scoreboard frame older than this does not open the kill gate: at that age
# the kill-bearing table arrives within a second or has already arrived.
KILL_GATE_MAX_BOARD_AGE_S = 9.0
# Gate ceiling after a fresh scoreboard kill frame: the table's death update
# lands ~8.3s after the board frame (p90 8.52s); ~11% of kills never reach it.
KILL_GATE_HOLD_S = 10.0

PLAYERS_PER_SIDE = 5
