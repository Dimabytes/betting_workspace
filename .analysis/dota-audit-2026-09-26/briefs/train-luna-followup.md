# Follow-up brief: train-luna — row selection on future book quality; money side of F2

Report: `$R/reports/train-luna-followup.md` (line 2 `Status: WIP` → `Status: FINAL`). Same rules as before.
Read `$R/work/shared/notes-from-orchestrator.md` first (N1 settlement tail, N4/N4b horn bug).

## Task 1 — future-information selection at row level (main task)

A training or validation row exists only if the label quote at +300 s passes the book gates
(`signal_market_p_radiant_300s` is None when the future pair is stale / missing / wide_spread ≥ 6 ticks /
inconsistent; `prepare_dataset.py` `build_minute_rows` drops those rows). That conditions the dataset on
future book quality, the same class as the removed whole-map tape filter.

1. Share of rows dropped only because the +300 s label failed (current second OK), per split (research train,
   validation, production), per status, and by regime: second, |radiant_nw_adv|, market_p extremes, map
   outcome vs current favourite.
2. Are dropped rows different in the future move? Use a relaxed label for the dropped rows (e.g. the as-of
   quote without the spread/age gate, or the nearest OK second within ±30 s after +300) and compare the move
   distribution of dropped vs kept rows. Does the gate preferentially drop big moves (events, map ends)?
3. Estimate the effect on the model: small offline fits (allowed, work dir only) with relaxed labels on the
   dropped rows vs the current recipe, scored on the latest validation slice. Report Δ in MAE gain and in
   calibration of large |Δ̂|.
4. The same selection in the backtest/validation metrics: which rows are scored.

## Task 2 — money side of F2 (fair overstated when Radiant p ≥ 0.85)

In the Dota LIVE backtest (`$E/data/backtests/dota_maker/LIVE/seed{0,1,2}`: `quote_events.parquet`,
`fills.parquet`, `results.parquet`) find SELL quotes whose price was set by the fair bound above the ask
(price == ceil(token fair) > ceil(best ask)), split by held-token price ≥ 0.85 vs below. What happened to those
positions: later SELL fill (price), held to settlement (won/lost), PnL vs a counterfactual "SELL at the ask at
that moment" using the book. Connect to note N1 (41 held maps, all winners). Is the fair bias making money
(holding winners) or losing it (delaying exits of losers)?

## Task 3 (quick) — horn bug impact on metrics

Recompute the research metrics excluding the 57 validation maps from note N4 (archive horn + pre-horn pause).
Does the MAE gain / directional markout change beyond noise?
