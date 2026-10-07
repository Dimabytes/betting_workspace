# poly-maker two-sided MM — architecture report (read-only)

**Verdict for Dota reuse:** poly-maker’s *infrastructure* (market/user WS, OrderBook, post-only `ExecutionGateway`, risk shell, CTF merge, reconcile loop) is what `esports-trader` already reuses. Its *quote math* (BUY-YES + BUY-NO around microprice FV, inventory skew, reward-band farming) is **not** what live Dota uses — `construct_quotes` / `reconcile` are replaced by Follow300. Merge (YES+NO→USDC) remains available and is useful for hedged inventory; there is **no** split (USDC→YES+NO) and **no** redeem path in poly-maker.

---

## 1. Overall architecture

### Engine + main loop

`Engine` (`/Users/dimabytes/work/polymarket/dota_2_bot/poly-maker/src/polymaker/engine.py`) owns I/O and state; strategy is pure.

| Piece | Role | Cite |
|--------|------|------|
| `Engine.__init__` | Builds journal, `StateStore`, `CatalogStore`, `ExecutionGateway`, `RiskManager`, `Merger`, `MarketDataService`, `UserEventProcessor` | `engine.py:47–66` |
| `start()` | Connect gateway → resolve markets → refresh Gamma meta → cancel-all/startup reconcile → subscribe market+user WS → spawn supervised tasks | `engine.py:91–130` |
| Quoter tasks | One `_quoter(cid)` per market, woken by book/fill dirty events, debounced | `engine.py:318–340`, `126–127` |
| Aux loops | heartbeat, REST reconcile, metadata refresh, book maintenance, supervisor | `engine.py:123–128`, `546–624`, `709–721` |

Dataflow (README + module docstring):  
`market WS → OrderBook → wake → Quoter → strategy → reconcile → ExecutionGateway`  
`user WS → StateStore`  
(`README.md:81–93`, `engine.py:1–8`)

### Market data (CLOB market websocket)

`MarketDataService` connects to  
`wss://ws-subscriptions-clob.polymarket.com/ws/market`  
(`marketdata/service.py:42–48`, `94–120`).

- Subscribes **both** YES and NO token IDs per condition (`engine.py:103`, `service.py:67–75`).
- Frames: book snapshot/deltas, last trade, tick_size_change (`service.py:154–193`, `parse.py`).
- `on_dirty` wakes the market quoter; `on_trade` feeds flow/sweep detection (`engine.py:258–305`).

### User channel

`UserStream` →  
`wss://ws-subscriptions-clob.polymarket.com/ws/user`  
with L2 API auth + condition_ids (`userstream/client.py:25–34`, `74–80`).

- Live only (skipped in paper): `engine.py:114–117`.
- Fills/orders → `UserEventProcessor` → `StateStore`; reconnect forces REST reconcile (`engine.py:272–276`).

### Orders (py-clob-client-v2)

`ExecutionGateway` wraps **`py_clob_client_v2.ClobClient`**, signs V2 EIP-712, offloads blocking I/O to a thread pool (`execution/gateway.py:1–9`, `98–132`, `150–177`).

- Place: `create_order` + `post_orders(..., post_only=...)`, GTC (`gateway.py:160–177`).
- Cancel / cancel-asset / cancel-all / open_orders / positions / heartbeat / optional FAK market_order also on this gateway.
- Heartbeat dead-man: chained `post_heartbeat` every `heartbeat_interval_s` (`gateway.py:411+`, `engine.py:546–568`).

### What “livecfg” is

`livecfg/` is a **separate conservative live-test config tree**, not a special runtime mode:

- Paths point at `livecfg/state.db`, `livecfg/journal`, `livecfg/logs` (`livecfg/config.toml:34–37`).
- Smaller risk caps ($40 total / $15 per market) and tiny `live-tiny` profile (`livecfg/config.toml:19–27`, `livecfg/strategy.toml`).
- Default operator config remains `config/` (`config.py:2–6`, `Config.load` at `config.py:218–243`).

esports-trader does **not** use poly-maker’s `livecfg/`; it materializes a temp config dir from `trading.toml` (`session_config.py:322–344`, `host_resources.py:158–163`).

---

## 2. Quoting logic

### Both YES and NO (not bid+ask of one token)

Canonical quote = **two BUY bids**, one on YES, one on NO:

- `BUY YES @ r − δ`
- `BUY NO @ (1 − r) − δ`  

so bid prices sum to `< 1` and a filled pair can merge to USDC at edge `1 − p − q` (`quoting.py:6–14`, `89–119`; `README.md:97–105`).

Also posts **SELL** exits on held inventory (maker, never cross) (`quoting.py:121–125`, `198–219`).

### Fair value / spread / size

| Step | Formula / behavior | Cite |
|------|--------------------|------|
| Microprice | Depth-weighted from YES book, `micro_levels` | `engine.py:378–383`, profile `micro_levels` |
| FV | `micro + weight·flow_z·tick` | `quoting.py:37–40`, `engine.py:383` |
| Inventory util | `net = YES − NO` shares; `u = net / (q_max_usdc/fv)` | `quoting.py:73–76` |
| Skew | `skew = gamma · vol_short · u`; `r = fv − skew` | `quoting.py:79–89` |
| Half-spread | `δ = delta_min_ticks·tick + c_vol·σ + c_tox·tox`; QUIET clamps into reward band | `quoting.py:81–87` |
| Placement | Bid: join touch, never above FV−min_edge, never cross ask | `quoting.py:137–153` |
| Size | `base_size_usdc / price · scale`; soft-cap pulls adding side; TRENDING ×0.5; tox shrink | `quoting.py:93–119`, `156–160` |
| Layers | Split across `layers` prices stepping `layer_step_ticks` down | `quoting.py:163–195` |

### Tick / min size

- Prices snapped with `round_to_tick` (`quoting.py:29–34`).
- Exchange floor: `meta.min_order_size`; reward scoring floor: `rewards_min_size × reward_size_mult` per order (`quoting.py:77`, `170–188`; Gamma `orderMinSize` / rewards in `catalog/gamma.py:150–152`).
- Tick updates from WS `tick_size_change` (`marketdata/service.py:154–193`).

### Churn / refresh

- Event-driven requote + debounce `debounce_ms` (`engine.py:319–334`).
- Baseline `quoter_tick_s` (default 60s); faster if holding inventory or EVENT cooloff ending (`engine.py:342–355`).
- Reconciler keeps live orders within `reprice_ticks` / `resize_frac` (`execution/reconciler.py:29–64`).

---

## 3. Inventory / position management

### Tracking

- Optimistic WS fills → `StateStore.apply_fill` (SQLite-deduped) (`state/store.py:1–11`, `74–100`).
- REST positions/orders periodically; in-flight trades block REST overwrite (`engine.py:570–608`, `store.py` docstring).
- On-chain balances used for merge truth + divergence correction (`engine.py:529–541`, `625–650`).

### Max position / skew

- Soft: `q_soft_frac` of `q_max_usdc` stops adding on the long side (`quoting.py:98–100`).
- Hard: `inventory_util ≥ 1` → `REDUCE_ONLY` (`regime.py:59–61`).
- Risk caps → reduce-only / size taper (`risk/manager.py:110–124`).

### Merge (YES+NO → USDC via CTF)

Implemented; **split is not**.

- Trigger: `min(yes, no) ≥ merge_min_size`, not paper, not already merging (`engine.py:521–527`).
- On-chain `mergePositions` on ConditionalTokens or NegRisk adapter (`merge.py:25–56`, `107–117`, `122–133`).
- Wallet paths: EOA / Gnosis Safe / DepositWallet+relayer (`merge.py:5–13`, `98–117`).
- README notes Safe/deposit merge historically incomplete for some wallets; deposit path now coded with builder creds (`README.md:140–143`, `merge.py:9–13`, `config.py:171–177`).

### Split / redeem

- **No** `splitPositions` / USDC→YES+NO in poly-maker src.
- **No** redeem implementation (resolution cash-out is out of scope here).

### neg_risk

- Flag on `MarketMeta`; passed into order options and merge adapter choice (`domain.py:76`, `gateway.py:168`, `merge.py:128–133`).
- Event-group worst-case cost for risk (`engine.py:757–766`, `risk/manager.py:113–114`).

---

## 4. Risk controls

| Control | Behavior | Cite |
|---------|----------|------|
| Daily loss kill | `daily_pnl ≤ −daily_loss_kill_usdc` → global halt | `risk/manager.py:84–91` |
| Max exposure | Per-market / total / neg-risk event-group; soft taper from 70% of cap | `risk/manager.py:110–124`, `146–153` |
| WS stale | Market WS disconnected > `ws_stale_halt_s` (connection-based, not quiet-book) | `engine.py:396–405`, `risk/manager.py:104–105` |
| User WS blind | User WS down > `user_ws_blind_halt_s` | `engine.py:406–411` |
| Heartbeat halt | ≥ `heartbeat_halt_failures` consecutive misses | `engine.py:412–416`, `546–567` |
| Order error rate | Rolling fraction ≥ `max_order_error_rate` | `risk/manager.py:74–91` |
| Volatility / EVENT | Jump ticks / sweep → pull quotes for `event_cooloff_s` | `regime.py:51–57`, `engine.py:278–305` |
| Trade-through | **Post-only** at exchange + bid never ≥ ask + `min_edge_ticks` vs FV | `gateway.py:176`, `config.toml:45`, `quoting.py:141–149` |
| Load shed | Skip new places if order bucket pressure > 0.85 in QUIET/TRENDING | `engine.py:475–485` |
| Refresh rate | Debounce + reconcile interval + churn tolerances | `config.toml:24–26`, `reconciler.py:44–55` |

There is no separate named “stop-loss price”; kill is **daily realized/equity PnL**.

---

## 5. Fees / rebates

| Concern | Handling | Cite |
|---------|----------|------|
| Maker orders | Always `post_only=true` → maker path | `execution` config, `gateway.py:176` |
| Fee metadata | Gamma `feeSchedule.rate` → `taker_fee_bps`; `rebateRate` → `rebate_rate` | `domain.py:82–90`, `engine.py:692–698`, `catalog/gamma.py:157–159` |
| Scanner | Scores reward density + estimated maker-rebate pool | `catalog/scoring.py:46–65`, `74–96` |
| Live quoting | Does **not** dynamically price rebates into δ; QUIET keeps quotes inside **liquidity-rewards band** | `quoting.py:84–86`, `README.md:115–117` |
| Operator notes | Taker fee ≈ `rate × p(1−p)`; rebate 20–25% of taker fees | `TIPS.md:66–91` |

No per-fill rebate accounting in the engine PnL path — rebates are selection/economics, not inventory math.

---

## 6. What esports-trader patches

Primary seam file:  
`/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/trader/engine_seams.py`  
Wired from `host_resources.py` + `wallet_host.py`.

### Module-level rebinds (before/around `Engine()`)

| Patch | What changes |
|-------|----------------|
| `patch_engine_classes()` | `StateStore`→`WalletStateStore`, `CatalogStore`→`WalletCatalogStore`, `UserEventProcessor`→`WalletFillProcessor`, `UserStream`→`WalletUserStream`, `Journal`→`SlimJournal`, trade normalize→`normalize_maker_trades` | `engine_seams.py:364–380` |
| `polymaker_engine.construct_quotes` | → Follow300 `quote_cycle` via `_bind_quotes_adapter` (poly-maker FV/skew unused for live quotes) | `host_resources.py:169–176`, `76–116` |
| `polymaker_engine.reconcile` | → `make_esports_reconcile`: plan comes from `LiveCore.take_plan()`, not tick tolerances | `host_resources.py:177`, `session_core.py:1078–1097` |
| Paper | `ExecutionGateway`→`PaperGateway` | `host_resources.py:156–157` |

### Instance patches (`WalletHost._install_runtime_seams`)

| Seam | Effect | Cite |
|------|--------|------|
| `_resolve_markets` / `refresh_market_metadata` | Empty / no-op (markets attach from collector sidecars) | `wallet_host.py:772–773` |
| `_on_fill` | Ledger cash only (`cash_only_on_fill`); host dispatches fills | `wallet_host.py:774–788`, `engine_seams.py:1160–1166` |
| `wrap_risk_from_ledger` | Cash/day from sqlite ledger; HALT on store error | `engine_seams.py:1075–1109` |
| `wrap_alert_transitions` | Dedup alerts; log `risk_halt:` clears | `engine_seams.py:709–742` |
| `wrap_position_divergence` | Chain-aware write-down / ledger restore / buy-block on excess | `engine_seams.py:795–901` |
| `install_rest_snapshot_stamps` | Stamp REST send time for safe write-downs | `engine_seams.py:430–445` |
| `install_rest_fill_recovery` | REST trade backfill + unsettled BUY reconcile | `engine_seams.py:474–505` |
| `install_collateral_snapshot` | Refresh USDC cache on positions read | `engine_seams.py:415–427` |
| `install_strict_rest` | Cancel/open-orders require proof | `engine_seams.py:393–412` |
| `install_heartbeat_boot_grace` | Ignore early Invalid Heartbeat ID | `engine_seams.py:508–529` |
| `ShutdownLatch.wrap_gateway` | Refuse places after close; drain in-flight | `engine_seams.py:252–361` |
| `wrap_inventory_place_guard` | Drop frozen/oversized SELLs; per-cid error breaker | `engine_seams.py:1028–1072` |
| Place/cancel journal wrap | Durable intent before venue | `wallet_host.py:898–912` |
| `wrap_recompute_retire` | Retire unsent prepared places | `wallet_host.py:878–896` |
| `install_book_readiness` | Ready only after book snapshot post-attach | `engine_seams.py:1322–1330` |
| `install_core_quoter_wake` | Wake on Follow300 sell/cadence deadlines | `engine_seams.py:1360–1383` |
| `attach_market` / `detach_market` / `stop_quoter` | Dynamic markets + `GatedRegimeMachine` (no model fair → REDUCE_ONLY) | `engine_seams.py:1188–1268`, `session_engine.py:63–86` |
| `pin_engine_identity` / `bind_user_fill_address` | Pin funder; user WS matches Safe funder not EOA | `engine_seams.py:1112–1134` |

**Implication:** for Dota, reuse poly-maker as **execution + books + risk envelope + merge**. Do **not** expect to reuse `strategy/quoting.py` as-is — that path is already replaced.

---

## 7. Config

### poly-maker `config/` + `livecfg/`

Three TOML files (`config.py:2–6`):

**`config.toml` / `livecfg/config.toml`**

- `[wallet]` chain, signature_type, hosts, RPC  
- `[engine]` debounce_ms, quoter_tick_s, reconcile_interval_s, catalog_refresh_s, heartbeat*, journal, loop  
- `[risk]` max_total_exposure_usdc, max_event_group_loss_usdc, max_market_notional_usdc, daily_loss_kill_usdc, ws_stale_halt_s, user_ws_blind_halt_s, heartbeat_halt_failures, max_order_error_rate  
- `[execution]` rate_budget_fraction, post_only, max_orders_per_batch  
- `[paths]` db, journal_dir, log_dir  

**`strategy.toml` — `StrategyProfile` knobs** (`config.py:71–119`)

Fair value: `micro_levels`, `flow_ewma_halflife_s`  
Spread/skew: `gamma`, `delta_min_ticks`, `c_vol`, `c_tox`  
Vol: `vol_short_halflife_s`, `vol_long_halflife_s`  
Size/inventory: `base_size_usdc`, `q_max_usdc`, `q_soft_frac`, `layers`, `layer_step_ticks`, `reward_size_mult`  
Churn: `reprice_ticks`, `resize_frac`, `min_edge_ticks`  
Regime: `event_*`, `trend_flow_z`, `trend_vol_ratio`  
Lifecycle: `end_date_taper_days`, `reduce_only_hours`, `halt_before_hours`  
Exits: `exit_urgency_s`, **`merge_min_size`**

**`markets.toml`** — trade list: slug/condition_id → profile (+ overrides).

### esports-trader config (what actually drives live)

`esports-trader/config/trading.toml` is materialized into a temp poly-maker-shaped dir (`session_config.py:322–344`): empty `markets.toml`, profiles like `dota-map` / `lol-map`, `merge_min_size` derived from clip, `q_max_usdc` from account-cap guard floor. Live **clip size** is in `[clips.*]`, not poly-maker’s quoting formula.

---

## Reuse judgment (Dota MM)

| Mechanic | Reusable as-is? | Notes |
|----------|-----------------|--------|
| Quote both sides as BUY-YES+BUY-NO | Concept yes; code no for live | Follow300 already owns quotes |
| Inventory skew / q_max | Fork MM yes; Dota uses own inventory/budget | Soft/hard caps still useful patterns |
| CTF merge | **Yes** | Still wired via `_maybe_merge` + `merge_min_size` |
| Split | **No** — not implemented | |
| Post-only / heartbeat / WS books | **Yes** — already used | |
| Reward-band farming / rebate scoring | Low for esports maps | Different economics than political reward pools |
| Regime EVENT/TRENDING | Partially | Gated; model absence forces REDUCE_ONLY |

**Bottom line:** treat poly-maker as a **CLOB maker runtime** (books, user stream, post-only gateway, risk/halt, merge). Its two-sided political MM *strategy* is the wrong brain for Dota; esports-trader already swapped that brain out while keeping the body.