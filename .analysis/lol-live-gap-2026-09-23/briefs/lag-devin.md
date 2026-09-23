# Brief: lag-devin — measure the real LoL live lag on data

Your name: `lag-devin`. Report: `$R/reports/lag-devin.md`. Work dir: `$R/work/lag-devin/`.

Goal: measure on real data the clock and delay offsets between the LoL training source
(lolesports livestats) and the LoL live source (GRID socket), and how fast the Polymarket
mid reacts. This decides which training/backtest lag mimics live.

1. Check existing work first: `scripts/measure_lol_source_lag.py`,
   `scripts/compare_lol_grid_livestats.py`, `scripts/record_lol_dual_feed.py`,
   `data/live_lag/`, `data/lol_dual_feed/`, `docs/experiments/lol-grid-widget.md`.
2. Find LoL maps with both a live GRID archive (`$E/data/trader/grid-*/grid_state.jsonl`,
   `match.json` `game=lol`) and livestats windows + details
   (`$E/data/lol/raw/lolesports/{windows,details}/<esports_game_id>.jsonl.gz`, synced to
   2026-09-20). Link through the LoL link table / universe / Gamma `gridSeriesId` + map
   number. Expect maps from about 2026-08-30 to 2026-09-20.
3. Align the two clocks with discrete events: per-team death/kill counter increments,
   dragons, level-ups. Per event record: livestats `rfc460Timestamp` of the first frame that
   shows it; GRID `occurredAt` (if present) and `received_at_utc` of the first GRID frame
   that shows it; GRID game clock (`currentSeconds`) and livestats spawn-relative game time
   (use the project functions, e.g. `assign_game_times`).
4. Report median / p10 / p90 of: GRID received − livestats stamp; GRID occurredAt −
   livestats stamp; GRID clock − livestats game time (clock offset). Separate the
   scoreboard service from the table service (net worth / XP), they may lag differently.
5. Market reaction: around each kill, the Polymarket mid path (live `session.jsonl`
   signal rows `yes_mid`, and Telonex/onchain data if local). Time from the livestats stamp
   to 50% of the 60 s mid move. Do the same for a few Dota maps if you have time.
6. Conclude with numbers: which training lag (current mid at `state_wall_us + lag`)
   reproduces what live sees: 0 s (current code after `332e1c17`), ~8–10 s (old code), or
   something else.
