# Brief: diff-grok — LoL vs Dota pipeline differences, and a full read of the LoL live path

Your name: `diff-grok`. Report: `$R/reports/diff-grok.md`. Work dir: `$R/work/diff-grok/`.

Goal: list every design difference between the LoL and Dota pipelines, stage by stage, and
rank which could explain "Dota profitable live, LoL flat live, LoL backtest very profitable".

1. Stages: collect, link, prepare, train, backtest, live, risk/sizing. `file:line` on both sides.
2. Look at: training row cadence (LoL per second vs Dota per minute), source-lag handling,
   as-of joins, feature source (livestats vs Steam/STRATZ), live source (GRID only vs
   Steam/GRID/Oddin), data admission (tape filter, whitelist), strategy parameters
   (`min_abs_delta` 0.02, `exit_abs_delta` 0.015, cut 480, min entry price 0.35), clips and
   profiles (`config/trading.toml`, `src/trader/game_profile.py`), staleness gates
   (`max_signal_age_seconds`), pause handling, finish detection.
3. For each difference: deliberate (cite `docs/experiments/*.md`) or accidental?
4. Read the LoL live path end to end and report anything wrong: GRID frame → parse →
   features → model input vector → prediction → orientation to YES/NO → quote
   (`src/trader/grid_feed.py`, `grid_widgets.py`, `grid_widget_types.py`, `grid_live_feed.py`,
   `match_worker.py`, `model_server.py`, `session_core.py`, `src/strategy/*`).
