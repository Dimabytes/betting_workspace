# Notes from the orchestrator (verified numbers; re-check what you rely on)

## N1. Settlement tail in the Dota LIVE backtest (script: `work/orchestrator/tail_check.py`)

Per seed, `results.parquet`: engine PnL = cash_flow + settlement part.

| seed | maps | engine PnL | cash_flow | settlement part | held maps (incl. dust) |
|---|---:|---:|---:|---:|---:|
| 0 | 613 | 2849 | −2685 | 5534 | 41 |
| 1 | 613 | 3339 | −1994 | 5332 | 39 |
| 2 | 613 | 3321 | −2796 | 6117 | 39 |

By signal mode (seed 0; held = non-dust terminal position):

| mode | maps | traded | held | engine PnL | settlement part | PnL of non-held maps |
|---|---:|---:|---:|---:|---:|---:|
| grid_v1 (synthetic) | 464 | 323 | 31 | 2134.51 | 5220.70 | 546.48 |
| schedule/grid (archive) | 136 | 68 | 4 | 707.29 | 313.15 | 626.05 |
| schedule/oddin (archive) | 13 | 10 | 0 | 6.79 | 0.00 | 6.79 |

- Every held position has a positive settlement part (all winners). Typical held map: one $100 rung bought
  (cash_flow ≈ −100), never sold, settled at $1.
- Held maps are short games: horn→end median 1890 s (traded maps: 2561 s).
- SELL fills do occur after second 645 in both modes (grid_v1 343 fills, schedule 181), so the fair keeps
  being computed late in the backtest.
- Schedule-mode results are identical across seeds (cadence seed only moves grid_v1).
- Working hypothesis (NOT proven): policy effect — in stomps the winner's price runs to ~0.99 above the
  resting SELL at fair; losers' SELL joins the ask and fills on the way down. Open questions: does live keep a
  fresh fair after 600 s like the backtest? Does the grid_v1 path hold more because it never goes stale (no feed
  gaps), while live and schedule mode go stale (45 s) and dump at the ask?

## N2. REFUTED: "series markets used as map markets" (script: `work/orchestrator/series_in_catalog.py`)

93 of 613 LIVE-backtest maps have a slug without `-game` = `series_winner` BO3 markets. Catalog: 423 BO3/BO5
maps link to a series market. This is the intended decider rule (`src/shared/utils/series_format.py`
`series_winner_covers_map`: the Match Winner market is the map market of the last map, BO3 map 3 / BO5 map 5,
when no Game-N market exists). Data check: 376 of 410 BO3 series-linked catalog maps have games 1 and 2 of the
same event earlier in the catalog; all 13 BO5 have 3–4 earlier maps. Do not report this as a bug unless you
find a map that is NOT the decider (e.g. the 34 BO3 rows with 0–1 earlier maps: check them if in scope).

## N3. market_seconds cache is consistent today (script: `work/orchestrator/cache_staleness.py`)

`run_market_data_build` only builds caches whose file is missing, and `CACHE_VERSION` hashes only numeric
constants (not catalog horn/pauses/token orientation/duration, not raw-book completeness, not
`telonex_book.py` code). Rebuilt 40 random catalog maps in memory: 34 had a cache, 34/34 identical to the
stored file (rows, status, state_ts, market_p, labels); 6 had no cache (no books). So staleness is a latent
design risk, not an active data error in this sample.

## N4. Archive horn is early by the pre-horn pause (scripts: `work/orchestrator/horn_check.py`, `horn_event_study.py`, `horn_event_study_archive.py`)

- Catalog `horn_source`: 3041 `grid_derived` (GRID startedAt + 90 s + pre-horn pauses, `get_horn_datetime`),
  166 `archive` (live `match.json` `horn_at_utc`; `s06_publish_catalog.py:119-127` prefers it).
- On 99 maps with both: archive − grid_derived = −(pre-horn pause D) (residual median −0.6 s, IQR −0.95..−0.30).
- Event study (market reaction to STRATZ kills, cache keyed by catalog horn): grid_derived maps with D in
  60..500 s peak at lag 0 (same as control) → grid_derived horn is right. Archive-horn maps with D ≥ 20 s peak
  at lag +62.5 s median (mean curve +65 s); archive control peaks at 0 → **the archive horn is early by D**.
- Affected: 58 archive-horn maps with a pre-horn pause (56 GRID, 2 Oddin), D median 70 s, max 549 s; 57 in the
  validation dataset (146,373 rows), 50 in the LIVE backtest (all `schedule` mode, engine PnL $331 of the
  schedule total ~$714). Every future map with a pre-horn pause gets the same error (~1/3 of maps).
- Open (time-grok / feed-devin / signal-devin / parity-luna): WHY is the live horn early — GRID
  `clock_seconds` counting the pre-horn pause (`grid_feed.py:113` `occurred_at − clock_seconds`) or
  `finalize_match` `trusted_horn`? If GRID's clock itself is inflated by D, the **live `second`** (feature, 480
  cutoff, model window) is off by D on those maps. Which backtest/dataset paths use the catalog horn for
  schedule-mode maps (market cache, labels, `dataset_market_p`, gates)?

## N4b. Mechanism verified (script: `work/orchestrator/grid_clock_dump.py`)

`grid-3006669-m1` (steam 8992034384, pre-horn pause 449 s): GRID clock −90 live at 16:18:10 (horn estimate
16:19:40) → stops at −47 at 16:18:52 → ticks again at 16:26:20 → clock 60 at 16:28:08 (horn 16:27:08, equals
the GRID-derived formula 16:27:09). `match.json` `horn_at_utc` = 16:19:40.
Cause: `src/trader/live_feed.py:102-111` `horn_is_pinnable` accepts the first PRE_HORN tick for Steam/GRID
("Steam/GRID keep the first horn-clock tick"); `match_worker._maybe_pin_horn` then never re-pins. The same
problem was fixed for Oddin only in `deebb730` (2026-09-20, "Pin Oddin horn only after the game clock goes
positive"): a sibling fix that did not reach GRID/Steam. The live GRID `second` itself comes from the clock
and is correct after the pause. Wrong: the archived `horn_at_utc` → catalog (`s06` prefers archive horn) →
market cache / validation rows / labels / anything keyed by catalog horn; also the live prior anchor
(`match_worker.py:553` uses the first event's horn − 90 s).
