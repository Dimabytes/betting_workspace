# Brief: featemp-devin — feature and prediction parity on real data

Your name: `featemp-devin`. Report: `$R/reports/featemp-devin.md`. Work dir: `$R/work/featemp-devin/`.

Goal: measure the train/serve skew numerically on the same maps, and its effect on the
model output.

1. Check existing tools first: `scripts/compare_lol_grid_livestats.py`,
   `scripts/reconstruct_lol_networth.py`, `data/lol_dual_feed/`.
2. Maps: LoL maps that have both a live GRID archive (`$E/data/trader/grid-*/`,
   `match.json` `game=lol`) and livestats windows + details
   (`$E/data/lol/raw/lolesports/`, synced to 2026-09-20). Link via the LoL link table,
   universe, or Gamma `gridSeriesId` + map number.
3. Per map, per game second 0..540: features from livestats with the production training
   code (`src/lol/05_prepare_dataset.py`, `livestats_frames.py`, `networth.py`; or read
   `data/lol/processed/datasets/game_features.parquet` if the map is there), and features
   from `grid_state.jsonl` with the live code path (`src/trader/grid_feed.py`,
   `grid_widgets.py`, `game_profile.py`). Do not re-implement parsers; call the project code.
4. Per feature: mean diff, median |diff|, correlation, and lead/lag by cross-correlation
   (does one source lead the other by N seconds?).
5. Model impact: run the LoL production model (`data/lol/models/production`, loader in
   `src/trader/model_server.py` or `src/shared/utils/gbm.py`) on (a) livestats features and
   (b) GRID features, with the same market inputs. Report the distribution of prediction
   differences in cents, and how often the sign differs or the `|delta| >= 0.02` gate
   (`min_abs_delta`) opens on one side only.
6. Sanity: reproduce the live `radiant_fair` / `yes_fair` in `session.jsonl` signal rows
   from the GRID archive (proves your live path is the real one). Then compare with the
   livestats-based prediction at the same game second.
