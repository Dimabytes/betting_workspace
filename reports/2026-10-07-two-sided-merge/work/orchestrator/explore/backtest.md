# Feasibility report: two-sided YES+NO maker + merge in the Dota backtest

**Verdict:** Market data and the Nautilus L2 queue fill stack already support posting maker BUYs on **both** tokens. The hard part is the **strategy/portfolio layer**: Follow300 is one-episode / one-token / exit-by-SELL, and there is **no CTF merge/split** in the sim. A new strategy is doable without changing the frozen poly-maker fork, but expect a **new core + merge cash accounting**, not a config tweak.

---

## 1. Backtest architecture

### Entry scripts
- `scripts/run_seeds.sh` — default launcher: archive stage → parallel seeds → report. Defaults `SEEDS=3`, `SHARDS=4` (12 cores). Invokes `python -m backtest.run` with `PYTHONPATH=src:../prediction-market-backtesting`.
- Stages in that script:
  - **Archive** (`RUN_SEEDS_STAGE=archive`): `--archives-only` for schedule-bound archive maps, shared across seeds via `_archive/DONE` (`run_seeds.sh:95–118`, `src/backtest/shared_archive.py:27–134`).
  - **Seed** (`RUN_SEEDS_STAGE=seed`): `--validation --name … --signal-cadence-seed N`, optional `--shard i/N` then `--merge-shards` (`run_seeds.sh:64–90`).
- Module entry: `src/backtest/run.py` (`python -m backtest.run`). Smoke/single-map: `src/backtest/seed0_replay.py`.

### Plugging a strategy
- Hardcoded paths: `STRATEGY_PATH = "backtest.strategy:DotaMakerStrategy"` / `DotaMakerConfig` (`run.py:231–232`).
- One Nautilus strategy **per match**, bound to **both** instrument IDs (`run.py:526–527`, `582`).
- Configs built in `build_strategy_configs` (`run.py:515–600`); batch run wires them into the framework experiment (`run.py:647–673`).
- Adapter: `DotaMakerStrategy(Strategy)` (`strategy.py:213+`) drives shared kernel `strategy.engine.step` (`engine.py:131–145`) with `Follow300Policy`.

### Time stepping (event loop)
Per match, Nautilus replays **L2 book deltas + onchain trade ticks** (merged book-before-trade). Strategy reacts to:

| Event | Handler | Effect |
| --- | --- | --- |
| Book deltas | `on_order_book_deltas` `strategy.py:350–365` | Update L2 books → `BookUpdate` → coalesce wake |
| Signal / feed alerts | `_on_signal_alert` `506–513`, `_sync_signal` `1199–1225` | `SignalUpdate` with `predicted_delta` |
| Kill gates | `_on_kill_gate_alert` `515–522` | `KillGateUpdate` |
| Core wake | `_on_core_wake` / `_evaluate` | `requote` via `step` |
| Venue fills/accepts/cancels | `on_order_*` `371+` | Inventory + telemetry |
| Cutoff / game end | `_on_buy_cutoff` / `_on_game_end` | Cancel BUYs / stop |

Feed ticks + board ticks arm time alerts at `on_start` (`strategy.py:307–317`).

### Framework batch
`run_batch` builds a Telonex book experiment with `ExecutionModelConfig(queue_position=True, …)` (`run.py:631–685`). Library profile: `L2_BOOK_ENGINE_PROFILE` = L2_MBP, `fill_model_mode="passive_book"`, `liquidity_consumption=True` (`prediction-market-backtesting/.../replay_adapters.py:564–573`).

---

## 2. Market data input (“tape”)

### What is stored
Local Telonex capture under `data/raw/telonex/polymarket/`:

| Channel | Role in maker BT |
| --- | --- |
| `book_snapshot_full` | Full L2 snapshots → diffs → OrderBook deltas |
| `onchain_fills` | Trade prints that drive queue fills (aggressor side rewritten) |
| `trades` | Present on disk; maker path requires book + **onchain_fills**, not `trades` (`telonex_local.py:42–43`, `60–70`) |

Layout: `…/<channel>/asset_id=<token_id>/<YYYY-MM-DD>.parquet` (`telonex_local.py:1–12`). Bridged into framework slug/`outcome_id=` tree for replay.

Book schema: `timestamp_us`, nested `bids`/`asks` of `{price,size}` — **full depth, not top-of-book only** (`src/shared/types/telonex.py:10–18`).

### Frequency / depth (measured samples)
- Recent map day file ~1.2M rows/token; median Δt ≈ **2 ms**, p90 ≈ **17 ms**; mid-row depth **~26–55 levels** (both tokens present for same day).
- Quieter days: median tens–hundreds of ms; depth still multi-level (10–27+).

### Both tokens?
**Yes.** Catalog always has `token_id_0` / `token_id_1`; eligibility requires **both** tokens’ book + onchain day files (`telonex_local.py:57–70`). `MarketContext.as_book_replays()` emits one `BookReplay` per token (`context.py:114–125`). Strategy snapshots a `BookPair` of both TOBs for quoting (`strategy.py:1139–1187`) while the venue still holds full L2 for matching.

### Volumes / dates
| Dataset | Count | Range (UTC) |
| --- | --- | --- |
| Research split train | 1745 | 2025-10-15 → 2026-05-30 |
| Research split validation | 866 | 2026-06-04 → 2026-10-04 |
| Match catalog | 3356 | horn ≈ 2025-10-04 → 2026-09-30 (3037 with horn) |
| Current LIVE run | eligible **862**, completed **838**, archive_excluded **14**, without_signal_rows **4** | `data/backtests/dota_maker/LIVE` → `…_shared-20261005` |

`data/duck.db` is analytics/views (catalog, training, old backtest tables) — **not** the live replay tape. Framework may use Telonex duckdb caches internally (`telonex.duckdb` in library); product tape is parquet under `data/raw/telonex/…`.

---

## 3. Fill model

### Maker (what you use)
- Label: `FILL_MODEL = "queue"` (`run.py:218`).
- Mechanism: Nautilus `queue_position=True` on a real L2 book; LIMIT fills only after same-price size ahead at accept trades through (`postprocess.py:72–77`).
- Accept latency: insert **175 ms**; cancel release **60 ms** (`shared/constants/strategy.py:19–20`, `run.py:306–325`). Gamma `secondsDelay` **not** applied to post-only.
- Own resting size stripped from archive books so the sim does not queue behind itself (`telonex_local.py:9–11`).
- Mode in library: `passive_book` (not the synthetic taker model) when queue_position is set (`_prediction_market_backtest.py:446–461`, `run.py:669`).

### Taker
- Library default `PredictionMarketTakerFillModel` builds a slipped synthetic L2 for **market**/aggressive fills (`fill_model.py:97–226`). Dota maker path sets fees on instruments to **0** in-engine and does **not** rely on that for LIMIT makers (`run.py:328–334`).
- Taker IOC/exit would use book liquidity + postprocess taker fee when `is_maker=False`.

### Fees / rebates per fill
**Outside the engine** in `enrich_fills` (`postprocess.py:172–196`):
- `fee_base = qty * p * (1-p)`
- Maker: `maker_rebate = 0.15 * 0.05 * fee_base`; `taker_fee = 0`
- Taker: `taker_fee = 0.05 * fee_base`; `maker_rebate = 0`
- Constants also in `shared/utils/trading.py:7–8,48–63`. Documented sports terms (`postprocess.py:1–4`).

---

## 4. Position / portfolio model & merge

### What exists
- Core inventory: `tuple[TokenInventory, TokenInventory]` for indices 0 and 1 (`strategy/types.py:242–246`, `299`).
- **Logical position** is single-sided: `selected_position` picks the larger qty side (`types.py:249–255`).
- Quoting: one `episode_token_index`; if any inventory ≥ min size, new episode blocked with `position_open` (`quoting.py:324–328`). `pick_episode_token` chooses **one** token by edge (`quoting.py:173–203`).
- Exits: maker/taker **SELL** of the held token, not merge (`quoting.py` SELL paths).
- Nautilus venue: cash account, **NETTING OMS per instrument**; YES and NO are **distinct** instruments, so the engine can hold both legs as separate positions. Settlement marks winners via binary settlement helpers in the library (`_backtest_runtime.py:375`, `_result_policies.py`).

### Merge / split
- **No** Polymarket CTF merge/split action in esports-trader backtest or as a first-class sim op.
- Closest library example: `strategies/binary_pair_arbitrage.py` — **taker** buys both asks when ask0+ask1 ≪ 1, then **holds to resolution** (`hold_to_resolution`), does **not** merge mid-map (`binary_pair_arbitrage.py:76–87`, `247–259`, `411`).
- `replay_merge_plan` in the library merges **book+trade event streams**, not YES+NO inventory (`_native.py:647+`).

### Could it support YES+NO + merge?
| Layer | Support today | Gap |
| --- | --- | --- |
| Data (both books + trades) | Yes | None |
| Place BUY on both tokens | Venue yes; Follow300 no | New quoting / episode model |
| Track both inventories | Struct yes; policy ignores dual | Skew / caps / selected_position |
| Merge → USDC | No | Synthetic: `q=min(yes,no)`; cash `+= q`; both qty `-= q` (or settlement-equivalent). Not on-chain CTF |
| Close risk by buying opposite | Possible as BUY other token | Must not use SELL-only episode rules; fees if taker |

**Difficulty:** medium–hard. Infra is ready; **product logic is a new strategy**, not a Follow300 flag.

---

## 5. Postprocess & reporting

- `src/backtest/postprocess.py`: markouts, fees, drawdowns, arm summary.
- `net_pnl = total_engine_pnl + maker_rebate - taker_fee` (`postprocess.py:595–599`).
- `pnl_before_rebate` = `engine_pnl` (raw settlement/trading PnL with engine fees zeroed).
- LIVE seed0 example: engine ~37.5k implied, rebate ~2867, taker 0, **net ≈ 40375**.
- Artifacts: `results.parquet`, `fills.parquet`, `quote_events` parts, `summary.json`, `manifest.json`.
- Two-column terminal: `format_terminal_report` (`report.py:230+`), printed at finalize (`run.py:1145`). Shows rebate, taker fee, “pnl with rebate”, wallet/ROI (`report.py:193–217`).

---

## 6. Model signal (midpoint +300s)

- Precomputed per map into `MatchSignals.predicted_deltas` (+ anchors, deaths, kill gates) (`signals.py:178–189`, loaded in `run.py` / `signals.py:437+`).
- At runtime: fair = `book_p_radiant + predicted_delta` (`signals.py:686–688`); latched into quoting.
- Injected via time alerts → `_sync_signal` → `SignalUpdate` (`strategy.py:1199–1224`).
- **Ignore:** new strategy can omit signal alerts / pass empty deltas / never call fair gates.
- **Skew input:** natural — e.g. size YES vs NO from `predicted_delta` sign/magnitude; Follow300 already maps delta → which token to buy (`pick_episode_token`).

---

## 7. Performance & shards

From `.learnings/esports-trader-backtest-performance-2026-09-07.md` (then ~454 maps, 12 seeds):
- ~**5 s/map** framework; ~**26 s/map** post-save (dominated by rewriting growing quote_events) → ~**31 s** wall between maps under load.
- 12 parallel seed processes; heavy RAM (~3–5 GiB footprint each observed).

Current launcher:
- Archive once (often `ARCHIVE_SHARDS = SEEDS * SHARDS`), then each seed sharded (`run_seeds.sh`).
- Shard assignment by book-row weights (`selection.py:110–119`, `run.py` shard helpers).
- Scale today: **~838–862 maps × N seeds**. At ~5 s engine/map alone, one unsharded seed is tens of minutes of engine time; full multi-seed validation is hours unless shards/archive graft cut work. Smoke: `--limit` / `seed0_replay` / small name.

---

## Difficulty scorecard for your two-sided + merge strategy

| Piece | Hardness | Notes |
| --- | --- | --- |
| Replay both YES/NO L2 + queue fills | **Already there** | No data pipeline change |
| Maker BUY both sides all map | **Medium** | New strategy/config path; don’t reuse single-episode Follow300 |
| Inventory skew | **Medium** | Dual inventory exists; need new state + sizing |
| Merge pairs → $1 | **Hard (new)** | No CTF op; implement cash+inventory math + report identity |
| Buy opposite to flatten | **Medium** | Works as second-token BUY; watch fees / queue |
| Ignore or use 300s signal as skew | **Easy** | Signal plumbing is optional input |
| Fees/rebates/report | **Easy** | Keep postprocess; makers still get rebate |
| Full validation runtime | **Ops cost** | Same shard/archive harness; expect similar wall time |

**Practical path:** add a new `*Strategy` + kernel (leave Follow300 alone), register via `STRATEGY_PATH` / CLI, keep Telonex queue fill + postprocess fees, implement **merge as accounting** (and optionally settlement residual). Do **not** expect merge from `prediction-market-backtesting` or Nautilus out of the box.

No files were modified.