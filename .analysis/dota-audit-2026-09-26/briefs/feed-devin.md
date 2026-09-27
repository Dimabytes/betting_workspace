# Brief: feed-devin — live feeds, live features, discovery, model server, live prior

Report: `$R/reports/feed-devin.md`. Work dir: `$R/work/feed-devin/`.

## Scope (read line by line)

`src/trader/{steam_feed.py, steam_client.py, steam_live_feed.py, steam_types.py, steam_archive.py,
grid_feed.py, grid_live_feed.py, grid_widgets.py, grid_widget_types.py, grid_archive.py, oddin_feed.py,
oddin_live_feed.py, oddin_client.py, oddin_crypto.py, oddin_types.py, oddin_archive.py, oddin_catalog.py,
oddin_discovery.py, live_feed.py, feed_selection.py, source_picker.py, match_worker.py, match_meta.py,
model_server.py, market_prior.py, game_profile.py, cadence.py, discovery.py}`,
`src/shared/utils/{grid_series_state.py, dota_levels.py, level_xp.py, top_players.py}`.

## Questions

1. **Live features vs training.** For each live source (Steam, GRID, Oddin), how each of the 12 features is
   computed, and whether it matches the STRATZ construction (`src/prepare_dataset/stratz_seconds.py`): NW
   fields, XP (levels → XP through `LEVEL_XP`? GRID `increaseLevel` undercount after gaps — a known LoL bug; is
   Dota hit?), deaths, top1 NW player set, `second` (clock, pauses, draft), `market_p_radiant` (which book,
   which moment), prior. List mismatches with expected size. Verify on local archives: compute features from
   `state.jsonl` / `grid_state.jsonl` / `oddin_state.jsonl` for 2–3 maps per source and compare with the values
   the live model got (`session.jsonl` signal rows / `core_trace`) and with STRATZ exact-second values at the
   same second (`data/new_processed/dataset/game_features.parquet`) where the map exists.
2. **Orientation per source.** Radiant ↔ market token.
3. **Freshness.** Stale detection, pause handling, source switching mid-map (Steam ↔ GRID ↔ Oddin), delay
   estimates (`steam_delay_s`, `grid_delay_s`) and whether they are applied correctly to `second` and to model
   inputs.
4. **Model server wiring.** Which model per source (production vs production-noxp for Oddin?), feature order,
   NaN handling, ensemble averaging, clipping. train-luna checks numerics; you check wiring.
5. **Live prior.** `market_prior.py` vs the training prior (`src/collect/s05a_fetch_prices_history.py`): window,
   bar, token orientation, fallback when missing. Measure the live-vs-training prior difference on local maps
   that exist in the catalog.
6. **Discovery.** The Disir catalog (new today, `dec98290`..`bbb28897`): time zones, id mapping, stale roster,
   blacklist. What happens when discovery misses a match? Estimate missed maps from local data if possible.
7. **Model window.** Live 0..599 and the fair after 540/600 vs training −60..540; prehorn use.

## Allowed

Local archive analysis. No SSH.
