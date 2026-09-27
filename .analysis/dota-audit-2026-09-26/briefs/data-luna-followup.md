# Follow-up brief: data-luna — crossed and locked books, project-wide, incl. backtest replay windows

Report: `$R/reports/data-luna-followup.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.

Your F2 found one strict cross passing the `ok` gates in 4 sampled maps. Size it properly.

1. **Rate.** For a stratified sample of catalog maps (at least 150: ~10 per month, both eras — paid Telonex
   before 2026-08-08 and collector after), read the raw books per map window (per-map reads only) and count
   snapshots with bid > ask (strict cross) and bid == ask (locked) per token, and pair-level cases where both
   tokens are crossed so the pair-sum gate passes. Rate by month and by source era.
2. **Where it lands.** Share of `ok` market seconds, training rows (current mid and label), validation rows,
   and entry-window rows (market second ≤ 489) whose as-of quote is crossed or locked. Size of the mid error vs
   the nearest uncrossed snapshot.
3. **Backtest replay.** The Nautilus replay consumes the same raw books (`src/backtest/telonex_local.py`, the
   framework L2 loader). In the LIVE backtest windows (`$E/data/backtests/dota_maker/LIVE/seed0`), are there
   crossed snapshots while our orders rest? Do any fills happen within ±2 s of a crossed snapshot on that token
   (fills.parquet `ts_ns`, `queue_ahead`)? Would a crossed ask below our resting bid fill us instantly in the
   framework's matching logic (read the framework code path)? Count and dollar-size such fills.
4. Verdict: is this a data-cleaning fix (reject bid ≥ ask at load) with measurable effect, or noise?
