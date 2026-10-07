# Live Dota trading strategy (Follow300) — codebase report

**Verdict:** Live Dota is a **one-sided directional maker** (“Follow300”): LightGBM predicts radiant midpoint **Δ over 300s**, the bot joins the bid ladder on the **single higher-edge side**, and posts a continuous maker **SELL** at ask/fair while holding. It is **not** two-sided market-making. Poly-maker’s Engine is a wallet/CLOB shell; quote logic is replaced by `LiveCore` + `strategy/quoting.py`.

---

## 1. Live trader entry, Engine patches, WalletHost

### Entry
| Piece | Path |
|---|---|
| CLI | `python -m trader.orchestrator daemon --mode live` |
| Compose | `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/compose.yaml:8-19` |
| Orchestrator | `src/trader/orchestrator.py:39-47` → `run_wallet_daemon` |
| Host open | `src/trader/host_resources.py:216-236` → `open_wallet_host` → `WalletHost.run` |

### What WalletHost does
`WalletHost` (`src/trader/wallet_host.py:670+`): **one process, one poly-maker `Engine`, one sqlite wallet**, many in-process `MatchWorker`s.

Boot flow (`wallet_host.py:1225-1264`):
1. `engine.start()`
2. Fence leftover finals (`_boot_scan`)
3. Poll discovery (`cadence.poll_discoveries`)
4. Attach/detach matches; each match runs feed → model → Follow300 quotes through the Engine quoter

### How Engine is patched (before + after `Engine()`)

**Before `Engine()`** (`host_resources.py:152-178`, `engine_seams.py:364-380`):
- `patch_engine_classes()` swaps: `StateStore`→`WalletStateStore`, `CatalogStore`→`WalletCatalogStore`, `UserEventProcessor`→`WalletFillProcessor`, `UserStream`→`WalletUserStream`, `Journal`→`SlimJournal`, trade normalize
- `polymaker_engine.construct_quotes` → Follow300 `quotes_adapter` (`host_resources.py:69-116`)
- `polymaker_engine.reconcile` → `make_esports_reconcile`
- Optional `PaperGateway` in paper mode

**After `Engine()` / at attach** (`wallet_host.py:769-809`, `engine_seams.py`):
- `cash_only_on_fill`, `wrap_risk_from_ledger`, `wrap_alert_transitions`, `wrap_position_divergence`
- `install_strict_rest`, `install_collateral_snapshot`, `install_rest_fill_recovery`, `install_heartbeat_boot_grace`, `install_book_readiness`
- `wrap_inventory_place_guard`, `ShutdownLatch.wrap_gateway`
- `attach_market` installs `GatedRegimeMachine` so missing model fair → `REDUCE_ONLY` (`engine_seams.py:1188-1223`, `session_engine.py:63-86`)

Poly-maker’s native fair-value / two-sided quoter is **bypassed**; Follow300 drives places/cancels via replaced `construct_quotes`.

---

## 2. Strategy logic — Follow300

### What “Follow300” is
Policy object `Follow300Policy` (`src/strategy/policy.py:26-75`), version `follow300-v8` (`shared/constants/strategy.py:43`). Name = **follow the model’s 300s-ahead midpoint move** with a 3-rung join-bid ladder.

### Model signal
- Label: `future_mid_300s − current_mid` (`shared/utils/gbm.py:133-137`; horizon `MODEL_TARGET_HORIZON_SECONDS = 300` in `shared/constants/dataset.py:11`)
- Live inference: `ModelServer.predict_fair` → `raw_delta`, `fair = clip(market_p_radiant + delta)` (`trader/model_server.py:173-196`)
- Kernel signal: `RawDeltaSignal.predicted_delta` + `anchor_p` (book mid at signal time) (`match_worker.py:587-594`)
- Fair for quoting: `fair_radiant = clip(book_p + predicted_delta)`, re-anchored to live book every `latch_reanchor_s` (0.25s) (`strategy/signals.py:14-15`, `quoting.py:738-751`, constants `LATCH_REANCHOR_SECONDS`)

### Entry rules
| Gate | Value | Where |
|---|---|---|
| Min \|Δ\| to open / re-arm | **0.02** (`MIN_ABS_DELTA`) | `strategy.py:7`, Schmitt gate `signals.py:55-67` |
| Hysteresis to stay armed | **0.015** (`EXIT_ABS_DELTA`) | `strategy.py:8` — lowers re-buy floor while gate open; **does not alone trigger sells** |
| Price band | **[0.45, 0.85)** | `MIN/MAX_ENTRY_PRICE` `strategy.py:10-12`; reject in `signals.py:84-89` |
| Max spread | **6 ticks** (0.06) | `MAX_ENTRY_SPREAD_TICKS` |
| Time cutoff | **game_second ≥ 480** | `BUY_CUTOFF_SECOND`; cancels BUYs (`quoting.py:124-125`, `865-867`) |
| Join price | floor bid to 0.01 | `join_buy_price` |
| Fair filter | BUY price ≤ token fair | `signals.py:84-85` |
| Side pick | **one** token with max `fair − join_price` | `pick_episode_token` `quoting.py:173-203` |
| Ladder | **3 levels**, 1 tick apart below join | `BUY_LEVEL_COUNT=3`, `BUY_LEVEL_STEP_TICKS=1`; `ladder_price` |

### Position sizing (clips)
- Each BUY rung size = `level_usdc / price` shares (`quoting.py:147-148`, `buy_share_quantity`)
- Live `level_usdc` = **tournament clip** from `config/trading.toml` `[clips.*]`, **not** `BASE_SIZE_USDC=100` (`match_worker.py:418`, `clip_rules.py:32-47`)
- Map cap: held cost + resting BUY notional ≤ `LIVE_DOTA_MAX_POSITION_LEVELS (9) × level_usdc` (`strategy.py:31`, `budget.py:22-36`)
- Account cap: `account_cap_usdc` from toml (stripped before fork write)

**Current live clips in toml** (not a fixed $60/$100/$300/$400 ladder):
- `clips.dota`: default **$5**, EPL World Series **$60**, BLAST Slam **$400**
- `clips.dota-oddin`: PARI Universe **$140**
- `profiles.dota-map.base_size_usdc = 300` is for **poly-maker** merge/sweep sizing, **not** the Follow300 rung (`trading.toml:32-36`, `session_config.py:166-168`)

### Exit rules
- While inventory exists and sells allowed: always target a **SELL** (`decide_sell` `quoting.py:501-523`)
  - Price = `max(ceil(ask), ceil(fair))` when fair known; else join ask
  - Full inventory qty (floored to min order size)
- Sell holds: 10s settle after BUY credit (`EXIT_SETTLE_SECONDS`), 1s SELL min-life, MATCHED/unconfirmed hold (`policy.py:70`, `quoting.py:403-522`)
- **Not** “exit only when Δ reverses” — Δ hysteresis gates **new BUYs**; exit is continuous maker sell
- `game_ended` / halt / pause → cancel all (`quoting.py:932-937`) → leftover shares settle via redeem (outside kernel). **Not** intentional hold-to-settle as primary exit
- After cutoff with flat inventory → stop (`quoting.py:874-875`)

### Both sides? MERGE?
| Question | Answer |
|---|---|
| Buy both sides of one market? | **No in one episode.** Episode locks `episode_token_index` to one token (`lifecycle.py:84-91`, `quoting.py:321-329`). After flat, a new episode may pick the other side. |
| MERGE YES+NO → $1? | Follow300 **does not** merge. Poly-maker `Engine._maybe_merge` still exists (`poly-maker/.../engine.py:521-541`) if **both** YES and NO sizes ≥ `merge_min_size` (= profile `base_size_usdc`, e.g. $300). Rare under Follow300’s one-sided inventory. |
| Split? | No strategy split logic in esports-trader. |

### Kill gating / directional gates
1. **Kill gate (GRID)** — mandatory: on scoreboard kill, block BUY on victim token and SELL on killer token until table deaths catch up or `KILL_GATE_HOLD_S=10` (`kill_gate.py:1-56`, `strategy.py:62-67`, wired in `match_worker.py:535-555`, `grid_feed.py` + `KILL_GATE_*`)
2. **Δ Schmitt gate** — entry/hysteresis (`signals.py:55-67`)
3. **Mid-spike** — cancel BUYs if watched mid drops ≥0.10 in 10s lookback; 30s cooloff (`mid_spike.py`, constants `strategy.py:57-60`)
4. **Anchor** — signal’s `anchor_p` must stay within 0.01 of live book_p or BUYs blocked (`signals.py:70-71`, `quoting.py:701-718`)
5. **Freshness** — entry vs exit stale windows; GRID entry stale 16s, exit SELL pull 45s (`GRID_FEED_STALE_SECONDS`, `EXIT_FEED_STALE_SECONDS`)
6. **GatedRegimeMachine** — no published fair → REDUCE_ONLY (`session_engine.py:73-86`)

---

## 3. `config/trading.toml` — keys and meaning

Path: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/config/trading.toml`

### `[wallet]`
| Key | Meaning |
|---|---|
| `signature_type = 2` | Gnosis Safe (PK / BROWSER_ADDRESS stay in env) |

### `[engine]` → Follow300 cadence + poly-maker loops
| Key | Current | Meaning |
|---|---|---|
| `debounce_ms` | 100 | Follow300 quote debounce |
| `quoter_tick_s` | 2.0 | Fallback wake / `fallback_timer_s` |
| `catalog_refresh_s` | 3600 | Engine catalog refresh |
| `reconcile_interval_s` | 20 | REST reconcile period |

### `[risk]`
| Key | Current | Meaning |
|---|---|---|
| `ws_stale_halt_s` | 30 | Market WS stale halt |
| `user_ws_blind_halt_s` | 15 | User WS blind halt |
| `heartbeat_halt_failures` | 3 | Heartbeat dead-man |
| `max_order_error_rate` | 0.25 | Place error breaker |
| `account_cap_usdc` | 30000 | **Ours** — stripped before fork write; drives Follow300 account room + inflated poly-maker guards |
| `daily_loss_kill_usdc` | 1e8 | Stays in fork; effectively disabled |

Derived (not in file): `q_max_usdc` and risk exposure guards = `2 * account_cap / 0.45` (`session_config.py:189-191`); `merge_min_size = 1.0 × base_size_usdc` per profile.

### `[profiles.dota-map]` / `[profiles.lol-map]` / satellite
Most knobs are **poly-maker regime leftovers** (micro_levels, vol_*, event_*, trend_*, exit_urgency_s, …). Follow300 does **not** use them for fair value.

Material to live:
| Key | Dota | Meaning for live |
|---|---|---|
| `base_size_usdc` | 300 | Poly-maker sweep/merge threshold; **not** Follow300 clip |
| (derived) `merge_min_size` | = base | YES+NO merge floor in fork Engine |
| (derived) `q_max_usdc` | guard floor | Inflated so fork guards don’t bite before our caps |

`[profiles.dota-oddin-map]`: only `base_size_usdc = 5` (default Oddin clip; tiers override).

### `[clips.dota]` / `[clips.lol]` / `[clips.dota-oddin]`
Tournament title-suffix match → Follow300 `level_usdc` (smaller hit wins). See §2 sizing.

---

## 4. Markets and feeds

### Market types
- Sidecar kinds: `map_winner` | `series_winner` (`collector_sidecars.py:36`)
- **Primary:** Game-N **map winner** markets
- **Series winner** only as **decider substitute** when live map = best-of (1/3/5) **and** no Game-N map_winner sidecar for that event (`discovery.py:773-794`, `series_format.py:16-18`)
- Live Dota title blacklist: `"Streamers"`, `"Winline"` (`wallet_host.py:145-151`)

### Feeds (live Dota)
| Source | Role |
|---|---|
| **GRID** | Primary game-state + kill board; XP model (`DOTA_XP_FEATURE_COLUMNS`) |
| **Oddin** | Alternate Dota feed; no-XP model (`dota-oddin-map`); delay-probed vs GRID |
| **Steam** | Discovery / live-list / orientation (`uses_steam=True`); **not** the quote feed |
| **PandaScore** | **Not** used on the live trading path |
| Databet / PGL | Legacy archive schema / unused live quote path |

Feed pick (`feed_selection.py`, `source_picker.py`):
- Delay gate: ≤ **61s** (`MAX_FEED_DELAY_SECONDS`)
- Prefer lower delay; tie-break GRID then Oddin
- LoL: GRID only
- Model lag assumption: `TRAIN_LAG_SECONDS = 10` (`dataset.py:13`)
- GRID stale watchdog: **16s** unique-gold yield (`GRID_FEED_STALE_SECONDS`)

---

## 5. PnL / fees / rebates

`src/backtest/postprocess.py`:

```
net_pnl = engine_pnl + maker_rebate − taker_fee
```
(`postprocess.py:595-599`)

| Term | Definition |
|---|---|
| `engine_pnl` | Cashflow from fills + settlement mark (rebate **not** included) |
| `fee_base` | `qty × p × (1−p)` | `calculate_taker_fee_per_share(..., fee_rate=1.0)` |
| Maker rebate | `0.15 × 0.05 × fee_base` | `REBATE_RATE=0.15`, `FEE_RATE=0.05` (`postprocess.py:64-65`, `ASSUMPTIONS` L78-80) |
| Taker fee | `0.05 × fee_base` on non-maker fills |

Assumptions documented at `postprocess.py:1-4, 72-108`:
- Polymarket **sports/esports** taker fee curve `rate × p(1−p)` with rate **5%**
- Maker rebate = **15% of the taker fee that fill generated** (not diluted by other makers)
- Day payout only if UTC-day rebate sum ≥ $1
- Liquidity Rewards **not** modeled (conservative understatement)
- Same constants in `shared/utils/trading.py:7-8, 48-63`

Live dashboard estimate mirrors the same rebate formula (`dashboard/summarize.py`).

---

## 6. merge / split / neg risk / both sides / inventory / skew

| Term | In this codebase |
|---|---|
| **merge** | Poly-maker on-chain YES+NO→USDC when both sides ≥ `merge_min_size` (`poly-maker` Engine). Configured via profile; Follow300 rarely holds both. Also: discovery `merge_cycle_matches`, dict merges — unrelated. |
| **split** | No trading split. Dataset/train “split” only. |
| **neg_risk** | Market flag from sidecar → exchange address / order hash / merge path (`core_execution.py:35-44`, bindings). Not a strategy skew. |
| **both sides** | Episode = one token. Kill gate / mid-spike watch both books. Discovery orients Radiant/Dire. No simultaneous two-sided quoting. |
| **inventory** | Per-token qty in `StrategyState`; wallet ledger; place guards vs on-chain excess (`wrap_inventory_place_guard`, `wrap_position_divergence`). Cap = 9×clip (Dota). |
| **skew** | **No** inventory-skew / asymmetric bid-ask MM. Direction comes only from model Δ + max-edge side pick. |

---

## Mental model vs two-sided MM

```
Feed (GRID|Oddin) → features → Δ̂_300s
     → fair = mid + Δ̂
     → if |Δ̂|≥2¢, second<480, price∈[45¢,85¢), spread OK:
           BUY 3 rungs on ONE side (join bid ladder, size=clip/price)
     → while long: SELL full size at max(ask, fair)  [maker exit]
     → kill/mid-spike/stale/anchor gate BUYs (and kill can block SELLs)
```

**Contrast with two-sided MM:** no continuous YES+NO quotes, no inventory skew around fair, no intentional dual-side inventory / merge-as-edge; merge is only a rare fork cleanup if both legs exist.