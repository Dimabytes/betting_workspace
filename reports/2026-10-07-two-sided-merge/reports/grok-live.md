# Live path: two-sided bids + CTF merge on Dota, and who already makes these markets
Status: FINAL

## Summary

- Follow300 can place only one token's BUY ladder. `episode_token_index` locks the episode, and a held token blocks a new episode with `position_open` (`quoting.py:321-328`). A two-sided plan is a new quote function in esports-trader. poly-maker's `construct_quotes` is already replaced.
- Merge is already live in the frozen engine. `_maybe_merge` fires when `min(yes, no)` shares ≥ `merge_min_size` (`engine.py:521-527`). Our GRID profile sets that threshold to **300 shares** (`session_config.py:166-168` × `trading.toml` `base_size_usdc = 300`). The live $5 clip never reaches it. A $300 rung at 50¢ is 600 shares, so one paired fill would.
- Our wallet is Gnosis Safe `signature_type = 2`. `Merger._merge_safe` is implemented and pays gas from the owner EOA (`merge.py:102-116`, `176-217`). `README.md:141-143` still says Safe merge is unwired. That sentence is stale. No live Safe merge of our wallet is recorded in this repo. Collateral is hardcoded to USDC.e `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` with a "confirm" comment (`merge.py:25-28`). Changing that address is BLOCKED (poly-maker).
- Nothing calls `update_balance_allowance`. A merge returns USDC on-chain, then the CLOB cache and our cash ledger stay wrong until someone adds that call and a cash credit. Polygon cost at the 2026-10-07 gas reading is about **1–4 cents** per Safe merge.
- Five maps × two tokens × three $300 rungs locks **$9,000** of BUY notional. Account cap is $30,000. The per-map cap is 9 × clip (`budget.py:34`), so a BLAST $400 clip fills that cap once both sides fill.
- Dota map taker notional, mirrored=false, 2026-09-01..10-05: **$57.45M** on 502 markets, 713,529 fills. September sum **$47.24M**. Median day **$0.70M**, mean day **$1.64M**. Tournament days reach $6.0M (Sep 21).
- Largest maker is `antec` `0x893575…7daa`: $1.82M (3.16%), buy-both on 217/222 markets, zero maker sells, and Dota MERGE rows in the data-api. `0xed44…4f76` merged **$229k** of Dota in its latest 500 MERGE rows (2026-10-01..10-07). `0x6e2c…` has no Dota map fills in this window. Our Safe `0x941aa…24d9` is maker rank 67, **$207k, 0.36%**, and it sells about as much as it buys.
- On 30 non-decider maps (2026-09-21..10-03) the median best-bid/ask spread is **2¢** (30 of 60 token-windows at 1¢). Best-level depth median is about **$120 / $115**, p25 about **$29**. On-chain distinct makers in horn..+480s: median **44.5** across 258 maps, **71.5** on those 30 maps. The older "~30 makers, 3¢, ~$40 depth" picture is a thinner sample.

## Findings

### 1. Quote plan shape

One quoter task per market calls the replaced `construct_quotes`, then the replaced `reconcile`, then `gateway.place` (`engine.py:447-487`, `gateway.py:150-176`).

The adapter is `_bind_quotes_adapter` (`host_resources.py:69-116`), installed before `Engine()` returns (`host_resources.py:169-177`). It calls `quote_cycle` (`session_core.py:1019-1050`):

1. `clear_plan` / `drain_apply` fold queued fills and acks into `StrategyState`.
2. `live_sources` builds books, regime permissions, and budget.
3. `finish_cycle` runs `requote` (`quoting.py:928-949`) and `stash`es a `PlannedBatch`.
4. `target_quotes` (`session_core.py:885-888`) returns the places as `TargetQuotes`. The engine then calls `make_esports_reconcile` (`session_core.py:1078-1097`), which `take_plan`s (`session_core.py:457-465`) and returns venue cancel ids plus `Quote` places.

Places are post-only because `[execution]` is not in `trading.toml`, so `ExecutionConfig.post_only` stays `True` (`config.py:59-62`, `gateway.py:176`).

What a BUY-YES + BUY-NO ladder has to change:

| Piece | Today | Two-sided change |
|---|---|---|
| Side pick | `pick_episode_token` keeps the one token with the best `fair − join` (`quoting.py:173-203`) | Emit both tokens' ladders in one plan. Three rungs, one tick apart, is already `BUY_LEVEL_COUNT` / `BUY_LEVEL_STEP_TICKS` (`strategy.py:27-28`). |
| Episode lock | `begin_episode` stores one `episode_token_index` (`lifecycle.py:84-96`). `_open_buy_targets` refuses a new episode while either inventory is ≥ min size, reason `position_open` (`quoting.py:321-328`). | A two-sided mode must not call `begin_episode` for a single index. Leave Follow300's lock in place and branch before it. |
| Exit | `decide_sell` sells the selected token. `selected_position` keeps the larger of the two inventories (`types.py:249-255`, `308-309`). `held_position` does the same when both sides are above the minimum (`session_core.py:181-192`). | A merge strategy posts no SELLs. Selling either leg fights the merge. |
| Budget | Map room = `9 × level_usdc − held cost − reserved BUYs` (`budget.py:22-36`, `LIVE_DOTA_MAX_POSITION_LEVELS = 9` at `strategy.py:31`). Held cost sums both tokens (`session_budget.py:78-83`). | Resting buys on both tokens share that one cap. See §3. |
| Regime | `GatedRegimeMachine` returns `REDUCE_ONLY` when `StrategyCell.yes_fair` is `None` (`session_engine.py:26-29`, `63-86`). The worker publishes model fair or `None` (`match_worker.py:462`). `REDUCE_ONLY` blocks buys (`quoting.py:215-216`). | A pure book MM has to publish a number every cycle or it never bids. The book microprice is enough to open the gate. poly-maker's own `fv` is computed in `_recompute_locked` (`engine.py:378-384`) and then thrown away, because our adapter ignores `QuoteInputs` fair. Publishing stays in esports-trader (`StrategyCell.publish`). |
| Follow300 gates | `|Δ| ≥ 0.02`, price in [0.45, 0.85), spread ≤ 6 ticks, game second < 480, kill gate (`quoting.py:206-228`, `173-203`). | Those gates are the directional strategy. A continuous two-sided quoter does not use them. `EVENT` from the delegate regime machine does not block Follow300 buys (`permissions_from_quote`, `session_core.py:329-345` only maps `HALTED` and `REDUCE_ONLY`). A two-sided policy should pull bids on `EVENT` itself. |

`wrap_inventory_place_guard` drops frozen or oversized SELLs (`engine_seams.py:1028-1048`). It does not block two BUY tokens. BUY blocking is the per-token `is_buy_blocked` flag inside `allow_buy` (`session_core.py:342`).

### 2. Merge path

Trigger, already in the frozen engine, after every requote (`engine.py:459`, `502`):

```521:527:poly-maker/src/polymaker/engine.py
def _maybe_merge(self, cid: str, meta: MarketMeta, p: StrategyProfile,
                 yes_size: float, no_size: float) -> None:
    amount = min(yes_size, no_size)
    if amount < p.merge_min_size or cid in self._merging or self.paper:
        return
```

`yes_size` / `no_size` are share balances. Paper mode never merges.

`_merge_task` (`engine.py:529-541`) takes `_chain_lock`, reads on-chain `balanceOf` for both token ids (`gateway.token_balances`, `gateway.py:356-397`), clamps the amount, and calls `merger.merge(condition_id, raw_6dp, neg_risk)` on a worker thread. Map markets are `negRisk` false, so the call is CTF `mergePositions` with partition `[1, 2]`, not the NegRisk adapter (`merge.py:128-138`).

Wallet paths (`merge.py:6-13`, `98-117`):

| `signature_type` | Path | Who pays gas |
|---|---|---|
| 0 EOA | `_merge_eoa`, gas cap 300,000 | The EOA |
| 2 Gnosis Safe | `_merge_safe`: owner `eth_sign` of the Safe tx hash (`v += 4`), then `execTransaction` from the EOA. Gas fields inside the Safe tx are 0, so the EOA pays (`merge.py:190-207`). Gas cap 600,000. | Owner EOA (`PK`), not the Safe |
| 1 or 3 deposit wallet | `_merge_deposit_wallet` via the builder relayer | Relayer, and only if builder creds exist |

`trading.toml:4` sets `signature_type = 2`. `can_merge` is true for 0 and 2 without builder creds (`merge.py:102-104`). esports-trader does not stub `Merger` or `_maybe_merge`. The only reason it does not fire on GRID Dota is the threshold.

`merge_min_size` is not a key in the template schema. `DOLLAR_MULTIPLES["merge_min_size"] = 1.0` copies `base_size_usdc` into the materialized profile (`session_config.py:166-168`, `217-219`).

| Profile | `base_size_usdc` | `merge_min_size` (shares) |
|---|---:|---:|
| `dota-map` (GRID) | 300 | 300 |
| `dota-oddin-map` | 5 | 5 |
| `lol-map` | 5 | 5 |

The live rung is the clip table, not `base_size_usdc` (`trading.toml:32-36`, `87-92`): default **$5**, EPL **$60**, BLAST **$400**. Shares = clip / price. At 50¢ that is 10, 120, and 800 shares. GRID merge waits for 300 shares on **both** tokens, so the $5 and $60 clips never merge on one fill, and the $400 clip does. Oddin would merge at 5 shares, the exchange minimum.

The smallest seam for "merge at 20 shares, or at game end" is in esports-trader only: stop deriving `merge_min_size` as `1.0 × base_size_usdc`, write `20` (or a game-end override on the attached `StrategyProfile`). `_maybe_merge` already uses that field. Game-end is not a trigger today; add it beside the threshold check by wrapping `_maybe_merge` after `Engine()`, the same way `attach_market` replaces methods. That wrap is not a poly-maker edit.

On-chain truth for the merge amount is `gateway.token_balances` (CTF `balanceOf`). esports-trader's divergence loop is a different reader: `chain_balances.read_chain_snapshot` uses `balanceOfBatch` at an explicit block (`chain_balances.py:22-23`, `79-114`), installed over `_check_position_divergence` (`engine_seams.py:795-823`). The stock loop (`engine.py:625-650`) is replaced.

After a successful merge the engine does **not** shrink `StateStore` or credit cash. The next chain read sees fewer shares. `_apply_chain_balance` write-downs when chain < internal (`engine_seams.py:867-895`) into `write_down_chain_position` (`wallet_store.py:262-274`). `running_net_cash` is the fill ledger only (`wallet_store.py:487-489`). Inventory falls, cash does not rise, so equity marks a loss of the merged shares. The next quote cycle sees a core/store mismatch and enters `Recovery`, which sets `sell_only` (`session_core.py:673-688`, `lifecycle.py:381-389`). That path is built for a broken fill, not for an intentional merge.

CLOB collateral. `install_collateral_snapshot` (`engine_seams.py:415-427`) refreshes `CollateralCache` from `gateway.collateral_balance` on each REST positions read. That calls `get_balance_allowance(COLLATERAL)` (`gateway.py:399-409`, `500-510`). `ClobClient.update_balance_allowance` exists (`py_clob_client_v2/client.py:697-705`). A repo search finds **zero callers**. The exchange cache of the Safe's pUSD/USDC is what `get` reads. After `mergePositions` the on-chain USDC.e balance changes before that cache does, until something calls `update`.

Latency, as wired today:

| Step | Delay |
|---|---|
| `wait_for_transaction_receipt` | timeout 180s (`merge.py:210`); a mined Polygon tx is a few seconds |
| Chain write-down | every 4th reconcile × `reconcile_interval_s = 20` → about **80s** (`engine.py:617-619`, `trading.toml:10`), and the block must be ≤ 30s old (`chain_balances.py:18`) |
| Collateral cache | same 20s positions read, and it calls `get`, not `update` |
| Spendable on the CLOB | not bounded, until an `update_balance_allowance` seam exists |

Cost, 2026-10-07. `eth_gasPrice` on `https://polygon-bor-rpc.publicnode.com` returned `0x404158a1c4` = **275.97 gwei**. CoinGecko `polygon-ecosystem-token` was **$0.10188**. Safe path caps gas at 600,000 and sets `maxFeePerGas` to `2 × gas_price` (`merge.py:204-206`).

| Assumption | POL | USD |
|---|---:|---:|
| 600,000 gas at 2 × 275.97 gwei (the cap) | 0.331 | $0.034 |
| 300,000 gas at 275.97 gwei (half the cap, no 2×) | 0.083 | $0.008 |

No merge receipt was pulled, so this is the code ceiling times the public gas price, not a measured `gasUsed`. Likely band: **about 1–4 cents**.

Safe merge for our wallet: the code path matches `signature_type = 2`. It has not been executed against our Safe in anything this run could see. The deposit-wallet path has a "verified live 2026-07-09" note (`merge.py:12-13`); the Safe path says it was ported from `safe-helpers.js` and has no such note. First live use should be a small amount. If the tx reverts because the condition's collateral is no longer USDC.e, the fix is a constant in `merge.py`, which is BLOCKED.

### 3. Capital and balance plumbing

A resting BUY locks `price × remaining shares` of collateral at the exchange. Our budget treats the CLOB `balance` as gross and subtracts that notional locally:

- `cash_usdc = collateral cache − all reserved BUYs` (`session_budget.py:35`)
- `account_cap_room = account_cap_usdc − held cost − reserved` (`session_budget.py:40`)
- `account_cap_usdc = 30000` is stripped before the fork config is written (`trading.toml:20`, `session_config.py:98`)
- `unsettled_buy_notional` adds MATCHED BUYs whose cancel is not yet proven, so a just-filled buy keeps reserving cash (`session_budget.py:49`, `unsettled_buy_recovery.py`)
- `q_max_usdc` and the three poly-maker exposure caps are `2 × 30000 / 0.45 = 133,333.33` so the fork's inventory util and notional taper never bind before our caps (`session_config.py:170-191`)

Five maps, both tokens, three rungs, **$300** per rung:

| Item | Dollars |
|---|---:|
| Resting BUY notional if every rung is up | 5 × 6 × 300 = **9,000** |
| Account cap | 30,000 |
| One map's cap (9 × 300) | 2,700 |
| One map, all six rungs filled | 1,800 held, 900 left |
| BLAST clip $400, six rungs filled | 2,400 held vs 3,600 cap |

$9,000 fits under $30,000. What gets tight is the **per-map** 9× cap once both sides have inventory, and the CLOB's own lock of the same $9,000, which our cache also subtracts. I did not read a live `balance-allowance` payload, so whether `balance` is already net of open orders is unverified. If it is net, `session_budget` subtracts twice and quotes shrink.

`q_max` will not stop this. The map cap and the account cap will. A paired position should count the unhedged residual (`|yes_cost − no_cost|`, or cost minus the mergeable shares) toward the map cap, or the cap treats a hedged pair like two directional bets.

### 4. Fills and reconciliation

`WalletFillProcessor.on_trade` (`wallet_store.py:849-877`) books whatever `token_id` the user WS names. It does not assume one token per market. It does force `is_maker=True` on every normalized trade (`wallet_store.py:872`). REST backfill uses the same maker-address filter (`engine_seams.py:448-472`, `install_rest_fill_recovery` at `474-505`).

The single-token assumptions sit in the strategy, not the ledger:

- `TokenInventory` is a pair (`types.py:242-246`) and fills update the indexed side.
- `state.position` / `selected_position` expose only the larger side (`types.py:249-255`).
- Sell freeze and `held_token` follow that larger side (`session_core.py:181-192`, `338`).
- A store/core size mismatch starts recovery and `sell_only` (`session_core.py:673-688`). A merge write-down is that mismatch.

User WS is pinned to the Safe funder, not the signer EOA (`engine_seams.py:1123-1134`). That part already works for two tokens.

### 5. Rate limits and order count

`[execution]` is not materialized (`session_config.py:347-362`), so the gateway uses defaults (`gateway.py:56-58`):

| Knob | Value |
|---|---|
| `rate_budget_fraction` | 0.25 |
| Order bucket | 50 posts/s sustained, burst 125 |
| Cancel bucket | 50/s, and `cancel()` acquires **1** token for the whole id list (`gateway.py:208`) |
| `post_only` | true |
| `max_orders_per_batch` | 15, **defined and never read** |

Load shed skips new places when `order_pressure > 0.85` and the regime is `QUIET` or `TRENDING` (`engine.py:475-485`). `REDUCE_ONLY` and `EVENT` still place. Once a book MM publishes a fair, calm markets become `QUIET` and can be shed.

`ORDER_INSERT_LATENCY_MS = 175` and cancel 60 ms are measured POST/DELETE p50s for the backtest (`strategy.py:17-20`). The live gateway does not sleep them.

`LATCH_REANCHOR_SECONDS = 0.25` (`strategy.py:46`) re-anchors Follow300's fair. BUY prices are the book bid, not that fair, so a quiet book does not cancel/replace every 250 ms. `install_core_quoter_wake` (`engine_seams.py:1360-1383`) can still wake the task on that deadline. Debounce is 100 ms, fallback tick 2 s (`trading.toml:7-8`).

Five maps × 6 rungs = 30 live orders. Replacing all of them every 250 ms is 120 posts/s, above the 50/s budget, and shed would drop the new bids. Replacing a market when its touch moves, a few maps at a time, fits. Six orders per market is under a single `post_orders` call.

### 6. Minimal live plan

All of this is esports-trader. poly-maker stays frozen. About **450 lines**.

1. New quote function, called from `quote_cycle` behind a flag. Two post-only BUY ladders. No `begin_episode`, no `decide_sell`. ~200 lines in a new module plus ~40 in `session_core.py`. Follow300 stays on the other branch.
2. Publish a book microprice through `StrategyCell.publish` when the two-sided flag is on (`match_worker.py:462`). ~30 lines. Without this, `GatedRegimeMachine` stays `REDUCE_ONLY` and buys never leave.
3. Budget: reserve both tokens' resting BUYs (already true globally) and count only the unhedged residual toward the 9× map cap (`session_budget.py`, `budget.py`). ~40 lines.
4. Set `merge_min_size` to a share count, starting at 20, instead of `1.0 × base_size_usdc` (`session_config.py:166-168`). ~20 lines. `_maybe_merge` then works for the $5 clip as soon as both sides have 20 shares.
5. After `Engine()`, wrap `_merge_task` / `merger.merge`: on a successful tx, call `update_balance_allowance(COLLATERAL)`, credit `running_net_cash` by the merged shares, and sync both core inventories without `Recovery`/`sell_only`. ~100 lines in `engine_seams.py`.
6. One manual Safe merge of ~20 shares on a single map before the flag is left on. If it reverts, stop. A collateral-address change is BLOCKED.
7. Leave redeem manual. There is no `redeemPositions` in poly-maker or esports-trader. The dashboard only displays data-api `redeemable` (`dashboard/home_lists.py`). Residual one-sided shares after the map resolves sit until someone redeems in the UI.

Risks:

| Risk | What happens |
|---|---|
| Ledger cash | Write-down drops shares and does not add the $1. Equity and the daily-loss figure lie until step 5. `daily_loss_kill_usdc` is 1e8, so it will not halt, but the books will. |
| Recovery | The mismatch path cancels buys and sets `sell_only` (`lifecycle.py:381-389`). A merge would pull the quotes. |
| Safe collateral | USDC.e is hardcoded. A revert means BLOCKED. |
| Dual inventory | `selected_position` and any leftover SELL logic act on one leg. |
| Settlement | The unpaired winner is not redeemed by the bot. |
| Rate shed | A 250 ms full replace of 30 orders gets shed in `QUIET`. |
| Oddin | `merge_min_size` 5 would merge at the first paired minimum order if that profile is used. |

BLOCKED (needs a poly-maker edit): the USDC.e constant, any change to `_merge_safe` itself, adding `splitPositions`, adding `redeemPositions`. The threshold, the cash credit, the allowance refresh, and the quote brain are not blocked.

### 7. Who makes Dota map markets

Script: `work/grok-live/dota_competition.py`. Universe `contract_kind = map_winner` (5,070 markets, 10,140 tokens). On-chain fills with `mirrored = false`, deduped on `(tx_hash, log_index, order_hash)`, days 2026-09-01..2026-10-05. Notional = `price × amount` in USDC, the same definition as `.analysis/series-market-2026-09-25/work/micro-devin/addresses.py:51`. 2,718 parquet files, 1,054 token directories, **713,529** fills, **$57,447,725**, **502** markets, **3,068** makers, **5,941** takers.

"Both-buy markets" means the address has `maker_side = buy` (or `taker_side = buy`) on two distinct `asset_id`s in that `market_id`. `fills/min span` divides all fills by the wallet's first-to-last timestamp. `fills/min mkt` is the median, over markets, of that market's fills divided by that market's first-to-last span.

Our wallet is `0x941aa5589961e33c54365a27a3223c916e6a24d9`, the address in the 2026-09-25 micro report. Checked here: 25 live `session.jsonl` maker fills in the window, 9 joined to an on-chain row within 5s on price and size, and all 9 have that maker. The misses sit on days the fill archive is thin (Sep 20 has 25 on-chain fills all day) or on fractional sell sizes.

`0x6e2c0e9474af7d720e5aba2a5b0bb6f723b7787c` is absent from this maker table.

Top 20 makers. Share is of the $57.45M. Top 20 together are **33.1%**.

| # | Wallet | USD | Share | Markets | Both-buy | Buy USD | Sell USD | Med shares | Med USD | Fills/min span | Fills/min mkt |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `0x893575c7d99542163c6b6e8a0fe5af0b6d217daa` | 1,816,336 | 3.16% | 222 | 217 | 1,816,336 | 0 | 100.0 | 38.33 | 0.44 | 0.34 |
| 2 | `0xcd3675803ac7c8242a83fd6ecdfe3d2239ae0f01` | 1,535,397 | 2.67% | 69 | 1 | 1,533,152 | 2,244 | 154.3 | 154.09 | 0.05 | 1.90 |
| 3 | `0x59229282121256ed2d629ce5adf0e04cc7fb3388` | 1,463,587 | 2.55% | 57 | 0 | 1,463,587 | 0 | 131.7 | 131.54 | 0.04 | 1.54 |
| 4 | `0x7c37b52eb226bcb9411375ec48b0169663fb6aeb` | 1,351,850 | 2.35% | 32 | 0 | 1,351,850 | 0 | 85.7 | 46.70 | 0.18 | 17.74 |
| 5 | `0x9909dfa7a317343039193b349909e2094b59311c` | 1,081,751 | 1.88% | 57 | 0 | 1,081,751 | 0 | 147.4 | 147.24 | 0.04 | 1.80 |
| 6 | `0x3413c803c3a6efc8d963afbce2dcee48d3738ff2` | 1,019,775 | 1.78% | 112 | 0 | 1,006,397 | 13,378 | 100.0 | 40.96 | 0.09 | 8.96 |
| 7 | `0xd3b034d7bfb2473fb252d0414646d9786bac329e` | 950,531 | 1.65% | 159 | 5 | 907,450 | 43,081 | 84.8 | 46.14 | 0.06 | 8.11 |
| 8 | `0x73b18f50526851ac8e52f07ae0f3cc665bfdbd8b` | 908,432 | 1.58% | 19 | 0 | 908,432 | 0 | 150.1 | 149.95 | 0.02 | 1.47 |
| 9 | `0xe59f2ab1b26b403f95b9df6e18ea66b395010d08` | 826,845 | 1.44% | 92 | 43 | 243,427 | 583,418 | 124.8 | 59.84 | 0.08 | 1.28 |
| 10 | `0x758dac51ba3cc9a246a79787da9df11c5f425d9e` | 821,402 | 1.43% | 378 | 352 | 817,540 | 3,863 | 22.0 | 9.00 | 0.68 | 0.82 |
| 11 | `0x731a241767938bb23d1b2fac4c9cd2f3cea9033f` | 788,627 | 1.37% | 19 | 0 | 788,627 | 0 | 140.9 | 140.71 | 0.02 | 1.71 |
| 12 | `0x4aec70021891ea712aaf3e2dd76c30f6b09a4ce9` | 761,456 | 1.33% | 115 | 65 | 761,456 | 0 | 100.2 | 53.87 | 0.11 | 1.94 |
| 13 | `0xb35f674af4603c9602dfbf39564087e94897cf4c` | 761,047 | 1.32% | 60 | 49 | 424,018 | 337,029 | 283.2 | 98.99 | 0.41 | 1.35 |
| 14 | `0x15508cacc0af5bdb52874a315b156a9d7a645481` | 756,230 | 1.32% | 152 | 34 | 756,230 | 0 | 98.0 | 37.38 | 0.09 | 2.88 |
| 15 | `0xcc77f32616a96f696a3850ad48c0e378eddcc035` | 741,345 | 1.29% | 22 | 0 | 741,345 | 0 | 143.8 | 143.65 | 0.03 | 2.15 |
| 16 | `0xed446954680979932092e970b443e6ae61e34f76` | 724,120 | 1.26% | 75 | 75 | 724,120 | 0 | 113.2 | 41.79 | 0.23 | 1.22 |
| 17 | `0x103721386ec40df5ab93df0bd0964e8fe70b5b09` | 706,297 | 1.23% | 92 | 5 | 624,928 | 81,369 | 204.3 | 92.43 | 0.05 | 4.06 |
| 18 | `0x2e3c40fa47b27c676ddd573064162f57d51508ba` | 698,555 | 1.22% | 406 | 376 | 698,555 | 0 | 46.7 | 17.24 | 0.29 | 0.57 |
| 19 | `0xbed3644bcdaab7d8bfa582bbf9ffb7ef9598fbe2` | 662,391 | 1.15% | 96 | 24 | 268,821 | 393,570 | 123.7 | 65.85 | 0.21 | 1.25 |
| 20 | `0xd06c49e1c86f970bc5c48f91169a607e9b03cdaf` | 633,362 | 1.10% | 188 | 28 | 595,450 | 37,912 | 96.4 | 35.47 | 0.09 | 2.11 |

Ours, maker rank **67**: $207,059 (0.360%), 233 markets, both-buy on 17, buy $106,334 / sell $100,725, 1,421 buys / 876 sells, median fill 57.7 shares / $35.41, 1.67 fills/min inside a market. Taker rank **1305**: $2,575, 66 fills, 30 markets, both-buy 0. We are a one-sided maker that sells the position out. The 17 both-buy markets are episodes that flipped token, not a merge book.

Top 20 takers (same notional; each fill has one maker and one taker, so the totals match):

| # | Wallet | USD | Share | Markets | Both-buy | Fills | Med shares | Med USD | Fills/min span |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `0xe16d3f2a5807999b358affd9445c3a09e45e5e30` | 1,983,094 | 3.45% | 135 | 112 | 7,784 | 75.0 | 31.57 | 0.190 |
| 2 | `0xb26fa000b3b281ba582bebc3457e12f0b8219536` | 1,210,094 | 2.11% | 172 | 126 | 12,481 | 16.5 | 7.25 | 0.252 |
| 3 | `0xe59f2ab1b26b403f95b9df6e18ea66b395010d08` | 1,084,704 | 1.89% | 95 | 76 | 6,525 | 26.9 | 11.70 | 0.179 |
| 4 | `0xf201a19b43471261a3c1ba9247335d55270e527e` | 1,024,617 | 1.78% | 89 | 69 | 3,075 | 100.0 | 57.00 | 0.076 |
| 5 | `0x7c37b52eb226bcb9411375ec48b0169663fb6aeb` | 967,817 | 1.68% | 28 | 21 | 1,316 | 107.7 | 54.65 | 0.076 |
| 6 | `0x86df6ce94c9263d09a6052b43d652adb7b51d902` | 965,948 | 1.68% | 248 | 237 | 32,131 | 15.3 | 7.30 | 0.677 |
| 7 | `0x51bef5ceb733fc728d3fb55e64600d4896cc12c2` | 818,668 | 1.43% | 227 | 169 | 2,814 | 41.0 | 18.51 | 0.057 |
| 8 | `0x15508cacc0af5bdb52874a315b156a9d7a645481` | 802,808 | 1.40% | 132 | 80 | 1,985 | 74.0 | 40.36 | 0.046 |
| 9 | `0xd3b034d7bfb2473fb252d0414646d9786bac329e` | 732,807 | 1.28% | 159 | 81 | 2,401 | 88.8 | 40.28 | 0.049 |
| 10 | `0xdc3fcad6dd903e59c211c24e54520143f50b5e0d` | 714,533 | 1.24% | 158 | 124 | 6,725 | 20.0 | 6.90 | 0.136 |
| 11 | `0x28ab8e33192e7dc9cf855cee6cd4046378dcd5bf` | 652,173 | 1.14% | 100 | 93 | 8,212 | 89.7 | 36.75 | 0.457 |
| 12 | `0xf3ce7f04dde4f8c5aed19a20e5ecd8520e5ca57a` | 639,182 | 1.11% | 146 | 58 | 1,927 | 129.9 | 50.00 | 0.039 |
| 13 | `0x5b13949f155cb6e1749a8f18e69849ef54540263` | 623,581 | 1.09% | 166 | 109 | 5,832 | 20.0 | 6.00 | 0.126 |
| 14 | `0xbfab152afa43741b41be0f355f862be2630cf067` | 574,011 | 1.00% | 182 | 129 | 7,282 | 20.0 | 7.00 | 0.156 |
| 15 | `0x5a56fe0468618bbd44e0f278c080db59b30610f5` | 572,766 | 1.00% | 93 | 77 | 4,859 | 20.0 | 8.90 | 0.223 |
| 16 | `0x29295021c598f1ec3efb894e69820f9691521dd0` | 540,599 | 0.94% | 61 | 54 | 3,744 | 20.0 | 8.90 | 0.122 |
| 17 | `0x9e3ed7b661a903fc97afcf49e0f014ebe869f882` | 533,274 | 0.93% | 226 | 148 | 5,263 | 21.2 | 8.70 | 0.107 |
| 18 | `0xe9076a87c5ed90ef16e6fe6529c943baeca0cff6` | 525,943 | 0.92% | 217 | 139 | 4,983 | 18.0 | 7.60 | 0.101 |
| 19 | `0xec981ed70ae69c5cbcac08c1ba063e734f6bafcd` | 502,318 | 0.87% | 101 | 84 | 2,686 | 41.0 | 20.74 | 0.129 |
| 20 | `0x4aec70021891ea712aaf3e2dd76c30f6b09a4ce9` | 501,119 | 0.87% | 115 | 105 | 3,654 | 50.0 | 14.59 | 0.108 |

Fifty maker wallets have both-buy on at least 10 markets, both-buy on at least 80% of their markets, and zero maker-sell notional. That is the on-chain shape of buy-both. Activity (below) shows which of the ones checked actually MERGE.

data-api `GET /v2/activity?user=&limit=500`, fetched 2026-10-07. Latest 500 rows, any type. Title prefix is the text before the first colon.

| Wallet | Name | TRADE buy | TRADE sell | MERGE | REDEEM | Other | Title prefixes in those 500 |
|---|---|---:|---:|---:|---:|---|---|
| `0x893575…` | antec | 476 | 0 | 20 | 4 | — | Valorant 272, Counter-Strike 191, LoL 4. This slice is a ~25 min burst (ts 1791378164–1791379703), so Dota is absent from it. |
| `0xcd3675…` | Birdof | 462 | 0 | 8 | 19 | SPLIT 5, YIELD 4, MAKER_REBATE 2 | Dota 2 326, Counter-Strike 155 |
| `0x592292…` | LuckyWin | 481 | 0 | 0 | 18 | TAKER_REBATE 1 | Dota 2 391, Counter-Strike 88, LoL 19 |
| `0x7c37b5…` | Diabolical-Prize | 494 | 0 | 0 | 3 | rebate/yield | Counter-Strike 246, LoL 123, Valorant 34. No Dota in this slice. |
| `0x9909df…` | LhordGryffin | 448 | 6 | 4 | 32 | SPLIT 4, YIELD 4 | Dota 2 128, Counter-Strike 64, plus tennis and other sports |

`type=MERGE&limit=500` on the same day. `usdc_size` summed. The endpoint caps at 500, so a full 500 is a lower bound on recent merges, not a history.

| Wallet | MERGE rows | Sum `usdc_size` | Dota 2 rows | Dota 2 USDC | Window (UTC) |
|---|---:|---:|---:|---:|---|
| antec | 500 | 197,811 | 40 | 5,427 | 2026-10-06 22:13 → 2026-10-07 13:24 |
| Birdof | 302 | 6,756,000 | 20 | 12,557 | 2026-01-02 → 2026-10-07. The size is almost all 2028 nomination markets. |
| LuckyWin | 27 | 140,769 | 0 | 0 | last row 2026-06-21. NBA and one CS map total. |
| Diabolical-Prize | 0 | 0 | 0 | 0 | — |
| LhordGryffin | 54 | 1,897,261 | 0 | 0 | politics, 2026-07-30 → 2026-10-07 |
| `0x758dac…` (maker #10) | 500 | 24,274 | 44 | 3,644 | 2026-10-07 12:42 → 13:30 |
| `0x2e3c40…` (maker #18) | 500 | 19,137 | 79 | 9,170 | 2026-10-06 15:11 → 2026-10-07 13:28 |
| `0xed4469…` (maker #16) | 500 | 627,768 | 246 | 228,857 | 2026-10-01 19:29 → 2026-10-07 13:29 |

Buy-both + merge is already on Dota maps. The clearest book is `0xed4469…4f76`: every one of its 75 Dota map markets is both-buy, maker sells are $0, $724k maker notional, and about $229k of its latest 500 merges are titled Dota 2. `antec` is the largest Dota maker and does merge Dota, but in the latest 500 merges Dota is $5.4k of $198k; the rest is LoL, CS, Valorant. `0x758d` and `0x2e3c` are the wide both-buy books (352 and 376 markets) with smaller recent Dota merge tickets. Birdof, LuckyWin, and Diabolical-Prize buy one Dota outcome and do not run this pattern there. Birdof's merges are politics.

Daily taker notional (capacity). September sum **$47,244,618** over 30 calendar days. Median day **$696,606**. Mean day across the 35-day file **$1,641,364**. Three September days are under $100k and look like archive holes: Sep 11 ($49k), Sep 20 ($7.4k, 25 fills), Sep 28 ($37k). Busy days are the cap a maker can actually hit: Sep 21 **$6.02M**, Sep 26 **$4.92M**, Sep 25 **$4.25M**, Sep 24 **$3.82M**, Sep 30 **$3.77M**. Early September shows 8–15 markets a day; Sep 19 onward shows 20–36. A two-sided bot's fill ceiling on a normal day is on the order of **$0.7M** of taker notional across all Dota map markets, shared with the makers above, and several million on a tier-1 slate.

### 8. Do the Sep 25 microstructure numbers still hold?

Source: `.analysis/series-market-2026-09-25/reports/micro-devin.md:16-20, 52-76`. Model window = horn .. horn+480s. Their map figures, median over maps, WS `trades` plus books, last 8 weeks ending ~2026-09-23, non-decider: spread p50 **3¢** (p90 6¢), best bid/ask depth **$37 / $44**, **~30 makers / ~38 takers**, **~4.4 prints/min**, **~$306/min**.

This run, script `work/grok-live/spread_sample.py`:

- On-chain distinct makers in horn..+480s, 258 catalog maps with a horn in 2026-09-01..10-05 and at least one fill: median **44.5** makers, **57.5** takers (p25 23, p75 78). Every one of those 258 is already `game_number < best_of` on a BO3+, which matches "Dota has no decider map market."
- Same window on the 30-map book sample below: median **71.5** makers, **84** takers, median notional **$22,188**.
- Books, 30 non-decider maps evenly picked from those with both token files, horns 2026-09-21..10-03. Per token, median of two-sided snapshots of `ask − bid`, then the median across the 60 token-windows: **2¢**. Counts: 30 tokens at 1¢, 12 at 2¢, 4 at 3¢, 8 at 4¢, 4 at 6¢, 2 at 12¢ (`dota2-ivo-yg-2026-09-30-game1`). Best-level USDC depth, median of per-token medians: bid **$120**, ask **$115**. p25 bid depth **$29**, p75 **$258**.

The 3¢ / ~$40 / ~30 makers summary does not describe this later window. Tier-1 slugs in the sample (Liquid, NAVI, MOUZ, OG, PARI) sit on a **1¢** touch with six-figure depth at the best level. Thinner maps are still 3–6¢ with ~$30 at the touch, which is the old picture. Maker counts here are on-chain, and the WS `trades` tree stops at **2026-09-17**, so the old "~30 makers" WS count was not recomputed on these dates. On-chain participation in the same 480s window is higher: mid-40s over the whole month, about 70 on the busy late-September sample.

## Open questions / what I could not verify

- No live Safe merge of `0x941aa…24d9` was sent or found. `_merge_safe` matches the config. Whether USDC.e is still the collateral for current Dota conditions was not read from a contract. A reverting merge would be a poly-maker constant change (BLOCKED).
- Whether `get_balance_allowance` `balance` is gross or already net of open BUY locks. Our budget assumes gross (`session_budget.py:35`). A live payload was not fetched.
- How long the CLOB cache lags `mergePositions` when nobody calls `update_balance_allowance`. The missing call is verified. The lag itself was not timed.
- WS-trades maker counts and print-rate ($/min, prints/min) after 2026-09-17. That feed is not in the archive. Spread and depth were recomputed from books; flow rate was not.
- Sep 11, Sep 20, and Sep 28 taker totals look truncated (25 fills on Sep 20). The $47.2M September sum is a lower bound if those days dropped data.
- Activity MERGE pages are capped at 500 rows. antec's $5.4k Dota figure is the Dota slice of the latest 500 merges, not its September Dota merge total.
- `max_orders_per_batch = 15` is unused. The real batch limit is whatever `post_orders` accepts; that HTTP limit was not measured.
- Gas is a public `eth_gasPrice` times the code cap, not `gasUsed` from a receipt.

## Files

| Path | What it produced |
|---|---|
| `work/grok-live/dota_competition.py` | Wallet, daily, and model-window tables. Outputs: `makers.parquet`, `takers.parquet`, `daily.parquet`, `window_makers.parquet`, `summary.json`, `fill_files.txt`. |
| `work/grok-live/spread_sample.py` | 30-map spreads and the non-decider maker median. Output: `spread_sample.json`. |
| `work/grok-live/activity_0x893575.json`, `activity_0xcd3675.json`, `activity_0x592292.json`, `activity_0x7c37b5.json`, `activity_0x9909df.json` | Latest 500 activity rows for the top 5 makers, 2026-10-07. |
| `work/grok-live/merge_activity_summary.json` | `type=MERGE` counts for those five. |
| Inline GET, same day | MERGE counts for `0x758dac…`, `0x2e3c40…`, `0xed4469…` (quoted in §7). |
| Inline join | 9 session fills → maker `0x941aa5589961e33c54365a27a3223c916e6a24d9`. |

Code cited read-only from `poly-maker/src/polymaker/{engine.py,merge.py,config.py,execution/gateway.py}` and `esports-trader/src/{trader,strategy}` plus `config/trading.toml`. No repo files were edited.
