# LoL final run report (US-008)

Date: 2026-08-27 / 2026-08-28 (local CEST)
Host: macbookDmitrii.local
Lag constant used (LOL_SOURCE_LAG_SECONDS): 25 (not measured; user skip)
Commands: (exact argv, in order, with log paths)

## 1. Commands and exit codes

Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/dota_2_model`. `PYTHONPATH=src`, `PYTHONUNBUFFERED=1`. No `--fetch` on 01/03.

| Step | Command | Log | Exit | Duration |
|---|---|---|---:|---|
| 01 cache replay | `uv run python src/lol/01_build_universe.py` | `current-task/logs/US-008-01.log` | 0 | ~9s |
| 02 resume | `set -a && source .env && set +a`; `uv run python src/lol/02_fetch_telonex_books.py` | `current-task/logs/US-008-02.log` | 1 (stable 404 / missing_interval) | ~4 min (20:50:36Z–20:54:33Z) |
| 03 cache replay | `uv run python src/lol/03_link_lolesports.py` | `current-task/logs/US-008-03.log` | 0 (second try) | ~10s |
| 04 resume | `uv run python src/lol/04_fetch_lolesports.py` | `current-task/logs/US-008-04.log` | 1 (stable empty_body) | ~23 min (20:54:11Z–21:17:27Z) |
| source-lag | `uv run python scripts/measure_lol_source_lag.py` | `current-task/logs/US-008-lag.log` | skipped | killed by user |
| 05 first | `uv run python src/lol/05_prepare_dataset.py` | (aborted, cap=5) | 1 | ~11 min |
| 05 publish | same, `MAX_LIVESTATS_INVARIANT_MAPS=100` | `current-task/logs/US-008-05.log` | 0 | ~12 min (22:04:49Z–22:17:01Z) |
| 06 research | `uv run python src/lol/06_train_model.py` | `current-task/logs/US-008-06-research.log` | 0 | ~9s |
| 06 production | `uv run python src/lol/06_train_model.py --production` | `current-task/logs/US-008-06-production.log` | skipped | user: backtest next |

02 was skip-heavy: `downloaded: 0`, `skipped_valid: 77378`. 04 was skip-HTTP on complete archives (~23 min to re-read 4992 gzip files, not a re-download). `LOL_SOURCE_LAG_SECONDS` was not edited.

03 first attempt raised `missing cached livestats window` on 117 empty-body skips that never wrote `anchors/*.json`. Cache replay now skips those files like `--fetch` (same 11859 games / 4992 links).

05 was parallelized (`ProcessPoolExecutor`, `LOL_PREPARE_WORKERS = os.cpu_count() or 8` → 12 on this host) with `prepared: N/4992` progress. Thread pool sat at ~110% CPU (GIL); processes used ~1000% CPU.

## 2. Source ranges

- Gamma tag **65**. Universe `scheduled_ts` min/max UTC: **2025-04-26T13:00:00Z** … **2026-09-02T20:00:00Z**.
- lolesports `games.start_ts` min/max UTC: **2025-04-23T09:00:00Z** … **2026-08-27T08:00:00Z**.
- Telonex catalog (non-empty): `book_snapshot_full_from` **2025-10-11** … **2026-08-25**; `book_snapshot_full_to` **2025-10-15** … **2026-08-27**. Catalog rows 7731 (19 markets have empty interval → `missing_interval`).
- Livestats audit `max_game_second` (4989 non-null): mean 1203, p50 1204, min 17, max 1210. Three `empty_body` maps have no usable max.
- Validation cutoff `2026-06-04T08:59:52Z` (`VALIDATION_START_TIME = 1780563592`).

## 3. Coverage funnel

```
Gamma markets 78327 (3403 events, tag 65)
  → included/resolved 7731
      game_winner 4783, match_winner_decider 2948
      excluded: unsupported_contract 69938, unresolved_market 402,
                unsupported_bo2 192, series_only 32, malformed_tokens 24,
                missing_best_of 8
  → accepted links 4992 (11859 games, 2949 pm_events, 5317 schedule_matches)
      Stage 03 event/map: accepted 7371, no_candidates_in_window 570,
      orientation_ambiguous 87, unresolved_market 10, unsupported_fallback 12
  → windows: complete 4971, wall_time_limit 18, empty_body 3
  → Telonex jobs: skipped_valid 77378, http_404 280, missing_interval 19
  → Stage 05 accepted maps 4290 vs drops (no silent remainder):
      missing_prior 329, no_spawn_frame 187, missing_books 70,
      livestats_invariant_violation 68, zero_usable_rows 45, no_livestats 3,
      unresolved_market 0
```

Invariant rule histogram (68 maps): rule 2 (gold non-decreasing) 66, rule 1 (spawn 500 gold) 1, rule 6 (deaths) 1. Cap raised 5 → 100 so 68 dirty maps drop instead of aborting a live feed (4290 accepted). First 05 run with cap=5 published nothing.

## 4. Dataset / map / row counts

| File | Maps (`match_id` nunique) | Rows | rows/map min–max |
|---|---:|---:|---|
| `training.parquet` | 2777 | 1 328 090 | 1–541 |
| `validation.parquet` | 1513 | 757 922 | 1–541 |
| `production_training.parquet` | 4290 | 2 086 012 | 1–541 |

`training + validation` row/map counts equal production. Schema = `DatasetRow`. `second` in 0..540, unique `(match_id, second)`, ≤ 541 rows/map.

`split.parquet`: 2149 PM events, 1410 train / 739 validation. Map-level split rows: 2777 train / 1513 validation. Cutoff is first map `event_start_time` of the PM event.

Prepare audit: 4992 rows = links. `accepted_dataset_rows` 2 086 012. `skipped_age_rows` 860. `skipped_market_rows` 258 385.

## 5. Source-lag result

**Not run.** User: LoL lag will be set from live observation, same as Dota `TRAIN_LAG_SECONDS=10`. Script killed. Constant stays **25**. `|argmax-25|` not measured. `model.json` `source_lag_seconds = 25`. Stage 05 was not re-run for lag.

## 6. Research holdout metrics (with CIs)

From `model.json` `metrics` (cents), event-bootstrap, 1513 validation maps, 757 922 rows:

- no-move MAE: **9.809¢**
- model MAE: **9.695¢**
- MAE gain: **0.115¢** (95% CI **0.066 … 0.163**)
- bias: **0.016¢**
- dir_300: **0.560¢**
- trees: **39** (early stop of 3000)
- train maps 2777 / valid 1513

CSV full-window `bucket=0-540`: same MAE/gain; `dir_300_ci_low_cents=0.185`, `dir_300_ci_high_cents=0.932`.

Per-minute: gain near zero in 0–59s (CI crosses 0); 0.09–0.17¢ later. Not a quality gate — numbers only.

No production fit (user: backtest next).

## 7. Model artifact paths

Research (four files):

- `/Users/dimabytes/work/polymarket/dota_2_bot/dota_2_model/data/lol/models/research/model.txt`
- `/Users/dimabytes/work/polymarket/dota_2_bot/dota_2_model/data/lol/models/research/model.json` (`name=20260827T221739Z`)
- `/Users/dimabytes/work/polymarket/dota_2_bot/dota_2_model/data/lol/models/research/split.parquet`
- `/Users/dimabytes/work/polymarket/dota_2_bot/dota_2_model/data/lol/models/research/validation_metrics.csv`

Dataset hashes in `model.json` match files:

- train `be77735d2504f0adea99b6dd3bba66d4fc518a456d6e543e331855d3cf592d5d`
- validation `e6d9e44efe01fefd3808865b1c3200918141ef9b570d40f9898259cddd876c55`

`production_training.parquet` sha256 `3aa4e5b42f2f33015de57de3aec743e2bc35dc42e1e789f2efc56a25ec280a02` (not in a production `model.json`).

Production dir has **no** `model.json` / `model.txt` / `validation_metrics.csv`. Research and production paths are different directories. Dota `data/new_model` was not written (`git status` clean there).

## 8. LPL / LDL

Accepted dataset maps: **LPL 482** (train 318, validation 164). **LDL 0**. Same absence as `pre_telonex_audit.md`: no PM LDL markets and no `ldl` schedule slug. Stage 05 did not change that.

## 9. Integrity / reproducibility

| Check | Result |
|---|---|
| 01 replay | pass — 7731 included; `markets.parquet` sha256 identical to pre-replay |
| 03 replay | pass — 11859 games, 4992 links; sha256 identical after empty-anchor skip |
| 02 skipped_valid | pass — 77378 skipped, 0 downloaded |
| 04 skip-HTTP | pass — 4971 complete + 18 wall_time_limit unchanged; 3 empty_body; ~23 min vs hours original |
| DatasetRow + 1 Hz | pass — all three parquets |
| Event split | pass — no PM event in both splits |
| Audit reasons | pass — only known strings; 4992 = links |
| Research hashes | pass |
| Production model | **skipped** (user) |
| Dota outputs | pass — not written |

## 10. Tests and lint

```
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest \
  tests/test_lol_build_universe.py tests/test_lol_link_lolesports.py \
  tests/test_lol_fetch_lolesports.py tests/test_lol_fetch_telonex_books.py \
  tests/test_lol_prepare_dataset.py tests/test_lol_train_model.py \
  tests/test_measure_lol_source_lag.py tests/test_level_xp.py \
  tests/test_dota_levels.py tests/test_prepare_dataset.py \
  tests/test_train_model.py tests/test_model_registry.py \
  tests/test_market_scenario_report.py tests/test_telonex_book.py -q
```

**205 passed.** `uv run python src/train_model/train_model.py --help` and `src/lol/06_train_model.py --help` work. `make lint-all` (ruff + basedpyright) passed.

## 11. Evidence for the reviewer

- Logs: `betting_workspace/current-task/logs/US-008-01.log` … `US-008-06-production.log` (production file is a skip note).
- Datasets: `dota_2_model/data/lol/processed/datasets/{training,validation,production_training,split,audit}.parquet`
- Research model: paths in §7.
- This file.

No backtest, no live-paper, no numeric “model is good” gate. Production fit deferred until after backtest.
