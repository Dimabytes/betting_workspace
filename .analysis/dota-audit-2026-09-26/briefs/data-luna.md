# Brief: data-luna — data quality: catalog, market cache, datasets, provenance, distribution shift

Report: `$R/reports/data-luna.md`. Work dir: `$R/work/data-luna/`.

## Data

`$E/data/new_processed/{match_catalog, universe, match_links, opendota_links, grid_game_starts,
pregame_quotes, stratz_match_index, market_seconds/v*/, dataset/*}`, samples of `$E/data/raw/*`, and
`$E/data/raw/telonex/polymarket/*` (per-map reads only). Live feature values from `$E/data/trader/*`
(`session.jsonl` / `core_trace`). Readers: grep `src/shared/utils` and `src/market_data`.

## Tasks

1. **Catalog integrity.** Duplicates (same match id or same condition id twice), inconsistent times (horn vs
   market activity), `radiant_win` vs the final market mid (orientation check), prior vs the first in-game mid,
   the `start_time` that drives the train/validation split.
2. **Market cache quality.** Per-second mid on a sample of maps across months: gaps, stale books (the same
   snapshot repeated), crossed / one-sided books, YES+NO pair consistency, tick-size regime near extremes,
   spikes. Find the provenance boundary (paid Telonex → our collector): do semantics change (snapshot
   frequency, timestamps, depth)? A discontinuity there can bias train (early) vs validation (late).
3. **Labels.** Recompute `signal_market_p_radiant_300s` for a sample and check exactness. Count labels that
   land in bad seconds. Count train/validation rows whose label time (state + 10 + 300) is after the game end:
   Dota maps shorter than ~14 min exist, so the label can contain the post-game jump to 0/1. Measure their
   share and label size. A model that learns post-game jumps from features correlated with a short game is a
   subtle leak.
4. **Distribution shift.** Train (minute rows) vs validation (exact-second rows) vs live feature values, per
   feature: `radiant_xp_adv`, top1 ratios, deaths, NW, `second` (train has only minute marks), prior,
   market_p. Quantiles and KS.
5. **Drops.** Maps and rows lost at each stage (catalog → market cache → STRATZ usable → rows). Which subsets
   (league, tier, month) are over- or under-represented in train vs validation vs live?
6. **Old data.** Early months (2025-10..2025-12) vs recent: anomalies that suggest bad downloads (empty
   STRATZ fields, zero NW, truncated maps).

## Allowed

Data scripts with `nice`; per-map reads of raw books.
