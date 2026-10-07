# Two-sided + merge Dota backtest: in-repo design

Status: FINAL

## Summary

- Add a new Nautilus strategy and a new kernel behind a CLI flag. Leave `DotaMakerStrategy` and `strategy.engine.step` on the Follow300 path.
- The venue already runs one engine, one cash account, NETTING per instrument, with both books and both on-chain print tapes. Maker BUY orders on both tokens are a strategy change, not a data or fill-model change.
- Terminal PnL of a merged pair equals hold-to-settlement. `compute_binary_settlement_pnl` pays each token its own 0/1 outcome; a pair pays exactly $1. Verified on synthetic sizes and both winners. Do not inject a synthetic venue fill.
- Peak capital is a different number from engine PnL. `report_capital.py` and `wallet_path.py` keep one share lot per match. A 100 @ 0.42 and 100 @ 0.55 pair that should settle at +$3 is marked −$97 if the last fill is the loser. That ledger has to learn two tokens and a merge credit.
- Mirrored on-chain rows are not double-counted. Each print is in both token files; `side` is the aggressor on that file. 300/300 unique-tx sibling pairs on one Dota map had prices summing to 1. A YES bid fills only on an aggressor sell of YES.
- The two books are not complementary inside Nautilus. Post-only checks one instrument. The quote itself must keep `bid_yes + bid_no <= 0.99` by flooring in integer ticks. No prediction-market-backtesting change, and no poly-maker change.
- Follow300 seed0 has fills on both tokens in 33 of 601 matches, and a buy of at least 5 shares on both tokens in 32. Peak simultaneous pair inventory is under 1 share on every one of them: episodes are sequential, then sold. There is no held pair in that tape to use as a merge example.
- Quote the whole map by pointing the BUY cutoff at game end. The replay window is already horn − 2 minutes through game end. Feature rows already run from second −60 to 6648 (1,780,919 of 2,248,559 rows are at or after 480).
- First runnable check is an assert on the quote sum and the merge ledger, with no engine. Smoke is one map via `--limit 1`. A 20-map run is one process (`SEEDS=1 SHARDS=1`); the tape load is the same full-map load Follow300 already pays.
- Rough size: ~1,050 new or edited lines, mostly a thinner copy of the venue adapter. Order: kernel, adapter, capital ledger, report fields.

## Findings

### 1. Where the strategy plugs in

`run.py:231-232` hardcodes

```text
STRATEGY_PATH = "backtest.strategy:DotaMakerStrategy"
STRATEGY_CONFIG_PATH = "backtest.strategy:DotaMakerConfig"
```

`build_strategy_configs` (`run.py:515-612`) emits one config per match. The config's `instrument_ids` is a real pair, not a framework placeholder. Because that field is set, `_is_batch_strategy_config` (`_prediction_market_backtest.py:489-496`) treats the config as batch-level and constructs one strategy for the batch (`_prediction_market_backtest.py:474-475`). `MAX_MATCHES_PER_BATCH = 1` (`run.py:223`), so that one strategy is one match and both instruments. Both legs are added to the same engine (`_prediction_market_backtest.py:250-252`).

`DotaMakerStrategy` (`strategy.py:213`) is the wrong base. Its `_drive` calls `strategy.engine.step` (`engine.py:131-145`), and `step` always calls `quoting.requote`. That quoter locks `episode_token_index` and refuses a new episode while any token is held (`quoting.py:321-328`). Inventory helpers collapse the pair to the larger leg (`types.py:249-256`). Fill accounting, the cutoff clock, and `_mark_trading_done` (`strategy.py:680-689`) assume one episode and a SELL exit. A subclass would override `_drive`, `on_order_filled`, the cutoff, and the clock, and would still inherit the episode fields.

Add:

- `backtest.two_sided_strategy:TwoSidedMakerStrategy` and `TwoSidedConfig`
- a pure kernel in `src/backtest/two_sided.py` (no Nautilus imports)

Select with `--strategy {follow300,two-sided}`, default `follow300`. Only the two-sided branch reads the new path. Follow300's `STRATEGY_PATH` stays the default.

Reuse as-is:

| piece | use |
|---|---|
| `TokenBook`, `BookPair`, `TokenInventory`, `KillGate`, `RawDeltaSignal`, `PlaceOrder`, `CancelOrder`, `Plan`, `ExecutionMode` | kernel I/O. `ExecutionMode` is already `MAKER` / `IOC` (`types.py:8-10`) |
| `kill_victims` (`kill_gate.py:27-39`) | victim token for the 10 s gate. It wants a `StrategyState`; build one with `empty_state` and `replace` the gate, signal, and limits. The rule itself stays |
| `MatchSignals`, `calculate_fair_radiant`, `calculate_book_p_radiant` (`signals.py:673-688`) | fair input |
| `build_order_latency`, `install_settlement_compatibility`, `create_telonex_source_tree`, `enrich_fills` | runner, unchanged |

Leave unused: `StrategyState` as the kernel state (episode, rungs, sell, ownership), `strategy.engine.step`, `quoting.requote`, `selected_position`, `Follow300Policy`.

`observe_mid_spike` (`mid_spike.py:70-97`) watches both tokens only while `episode_token_index` and `position` are empty (`mid_spike.py:46-53`). Calling it requires a fake flat `StrategyState` on every tick. Copy the 30-line sample/drop math into the new kernel instead of pretending to be Follow300.

### 2. Dual inventory and merge accounting

Venue, verified in the library profile `L2_BOOK_ENGINE_PROFILE` (`replay_adapters.py:564-573`) and `add_venue` (`_prediction_market_backtest.py:450-463`):

| setting | value |
|---|---|
| OMS | `OmsType.NETTING` per instrument |
| account | one `AccountType.CASH` for the venue |
| starting cash | `ENGINE_STARTING_BALANCE = 1_000_000` (`run.py:230`). Comment at `run.py:225-229`: this balance must not bind; report capital is rebuilt from the fill tape |
| engine fee | `taker_fee = "0"` on every loaded leg (`run.py:328-342`) |

YES and NO are different instruments, so NETTING does not net them against each other. The cash account sees BUY notionals and, at expiration, the settlement adjustment.

Settlement is per instrument in `compute_binary_settlement_pnl` (`backtest_utils.py:356-398`):

```text
pnl = cash_from_fills + settlement_value * open_qty - commissions
settlement_value = realized_outcome    if fill side == "yes"
                 = 1 - realized_outcome if fill side == "no"
```

Fill events force `side = "yes"` unless the market id contains `NO` (`research.py:314-356`). Our instrument id is `{condition_id}-{token_id}.POLYMARKET` (`context.py:51-57`). Token ids are decimal strings, so both legs stay `side=yes` and `settlement_value = realized_outcome` of that leg. Gamma sets `winner` on the token whose outcome name won (`loaders.py:805-822`, `gamma_markets.py:217-246`). One token's realized outcome is 1 and the other's is 0. `results.py:211-217` sums the two legs into `engine_pnl`.

Identity, for paired quantity `q` and any winner:

```text
hold  = -q*(p_y + p_n) + q*1
merge = -q*(p_y + p_n) + q*1
```

Leftover shares settle on their own leg either way. Commissions are 0 because the engine fee was zeroed. Taker fees and maker rebates are applied later, once, in `enrich_fills` (`postprocess.py:187-196`), and `net_pnl = engine_pnl + maker_rebate - taker_fee` (`postprocess.py:595-599`). A fee does not get paid twice if the merge stays out of the venue.

Verified by calling `compute_binary_settlement_pnl` (`work/grok-bt/check_identity.py`):

| yes qty | no qty | yes wins | hold PnL | merge-then-settle | gap |
|---:|---:|---|---:|---:|---:|
| 100 | 100 | yes | 3 | 3 | ~0 |
| 100 | 100 | no | 3 | 3 | ~0 |
| 100 | 40 | yes | 36 | 36 | ~0 |
| 100 | 40 | no | −24 | −24 | ~0 |
| 17 | 80 | yes | −34.14 | −34.14 | 0 |
| 17 | 80 | no | 28.86 | 28.86 | 0 |

Same identity on the 33 seed0 maps that have a fill on both tokens, using catalog `radiant_win` (`work/grok-bt/check_real_map.py`). Max |gap| was `5.7e-14`. Those maps are not a strategy example: see the sanity section below. The largest paired residual net was 0.008 shares.

Recommendation: accounting merge only.

- Kernel ledger: when `min(yes_qty, no_qty) >= M`, record a merge of that many shares, subtract it from both qtys, and quote off the reduced inventory. On-chain gas stays 0.
- Nautilus positions stay at the gross buys. Expiration still pays $1 per pair. `engine_pnl` is the merge PnL. Do not add `merge_usdc` into `net_pnl`.
- There is no strategy API that force-fills a SELL at 1.0 and 0.0 without walking the book. A real SELL would hit the bid, and `bid_yes + bid_no < 1`, so it would not be a merge. Do not add that path.

Capital timing lives in the report, which is already how Follow300 works (`report_capital.py:54-65` settles at `game_end`, not from the engine account). Extend that tape:

```text
BUY token i:  cash -= p*q,  qty[i] += q,  reserve -= p*q
SELL token i: cash += p*q,  qty[i] -= q          (Follow300 only)
merge:        cash += M,    qty[0] -= M, qty[1] -= M
settle:        for each token, cash += qty * (1 if that token won else 0)
peak unreturned = max over events of (sum of resting BUY reserves − cash)
```

Today `apply_capital_fill` (`report_capital.py:32-51`) and `_apply_reserve_fill` (`wallet_path.py:346-368`) store one lot per `match_id` and overwrite `token_index` with the latest fill. A replica of that update on 100 YES @ 0.42 then 100 NO @ 0.55, YES winning, returns −97 instead of +3 (`check_identity.py`, `single_lot`). The replica follows those lines; it does not import `wallet_path`.

Fix the shared lot once, in both functions, as two qtys keyed by `(match_id, token_index)`. On a pure one-token Follow300 map the cash path matches. On the 33 maps that did trade both tokens, Follow300 required-cash can move, because the old lot could settle the residual on the wrong token. `engine_pnl` does not move.

Merge timestamps come from a new telemetry row (`kind="merge"`, quantity M, price 1). The kernel emits it when it merges. The report applies it. The venue never sees it.

### 3. Fill model for two-sided quoting

Queue fills are on. `run_batch` passes `ExecutionModelConfig(queue_position=True, latency_model=...)` (`run.py:669`). The book profile sets `book_type=L2_MBP`, `liquidity_consumption=True`, `trade_execution=True`, `fill_model_mode="passive_book"` (`replay_adapters.py:564-573`, `_prediction_market_backtest.py:446-463`). `passive_book` leaves `fill_model=None`, so maker limits rest on the real L2 book and fill when trade ticks trade through the size ahead at accept. Resting orders on the two instruments are two queues. A trade tick is stamped with one instrument id and only consumes that book.

Latency (`shared/constants/strategy.py:17-20`, `run.py:306-325`):

| hop | value | who applies it |
|---|---:|---|
| insert / update | 175 ms | venue `StaticLatencyConfig` |
| cancel | 60 ms | strategy alert, then `cancel_order`. Venue `cancel_latency_ms` is 0 on purpose (`run.py:309-316`) |

`strip_own_book.py` removes our historical live resting size from archive books so a replay of Follow300 does not queue behind itself (`telonex_local.py:9-11`). A new two-sided strategy was not in that tape. Leave the strip as it is.

Mirrored prints. Telonex writes each match into both token day files. `price` is in the file's token; `taker_side` stays the taker's side on the token they actually traded (`onchain_side.py:1-11`). `write_aggressor_side` adds `side`: the taker's side when `taker_asset_id == asset_id`, otherwise the flipped side (`onchain_side.py:21-33`). `telonex_local.py:192-217` rewrites every on-chain day. The loader prefers column `side` over `taker_side` (`telonex.py:3253-3255`) and emits every row with `0 < price < 1` (`telonex.rs:833-834`). It does not drop `mirrored`.

That is one economic fill, not two bid fills:

| tape | aggressor | which of our BUY bids it can fill |
|---|---|---|
| token the taker bought | buy | the ask side, not our bid |
| sibling file | sell, at `1 − p` | our bid on the sibling, at the mirrored price |

Verified on `dota2-flc-liquid-2026-06-04-game2` (match `8838170936`, day `2026-06-04`, 3,016 rows in the token-0 file, 1,445 with `mirrored=true`). Of sibling rows whose `tx_hash` appears once on each side, 300/300 had prices summing to exactly 1 (`check_real_map.py`). A naive dict of all txs missed 24 of the first 200 because one hash can hold several fills (2,034 unique hashes in 3,016 rows).

Post-only. `_submit_place` sets `post_only=True` and `TimeInForce.GTC` for `ExecutionMode.MAKER`, and `post_only=False` with `TimeInForce.IOC` for IOC (`strategy.py:744-752`). `on_order_rejected` drops the order and keeps quoting (`strategy.py:466-473`). That rejection is against the instrument's own ask. Our NO bid is not visible as a YES ask, so the venue will not stop `bid_yes + bid_no >= 1`.

The quote formula cancels skew: `bid_yes + bid_no = 1 − 2h` before rounding. With `h >= 1` tick the raw sum is at most 0.98. Flooring each side in float can still step the sum the wrong way, and a negative price truncates toward zero (`int(-0.20/0.01)` is −19). The sketch in `work/grok-bt/quote_sketch.py` floors in integer ticks, clamps at 0, then walks the larger bid down until the sum is at most 99 ticks. Asserted for fair in {0.03, 0.50, 0.97}, half-spread in {1, 2, 3} ticks, skew in {−0.20, −0.04, 0, 0.04, 0.20}. At fair 0.50, h = 1 tick: skew 0 → 0.49/0.49; skew +0.02 → 0.47/0.51.

Drop a bid outside [0.03, 0.97] instead of clamping it into the band. Deeper rungs are one tick under the touch, so they are further from a cross. The kernel assert is `bid0 + bid1 <= 99` ticks on every place.

### 4. Taker flatten

IOC already exists on the Follow300 adapter and is gated off. `_defer_ioc` raises unless the research ideal-exit scenario is on (`strategy.py:819-823`). The two-sided adapter submits the IOC with `submit_order` on the same 175 ms venue insert latency. Flatten is an IOC BUY of the short token at that token's ask (it opens the short leg so a merge can pair it). `reduce_only` would be the wrong flag: the short token's position is flat or smaller, and a reduce-only BUY would be rejected.

Fee. `on_order_filled` stores `is_maker` from `liquidity_side == MAKER` (`strategy.py:442`). `enrich_fills` (`postprocess.py:191-196`):

| fill | maker_rebate | taker_fee |
|---|---|---|
| maker | `0.15 * 0.05 * qty * p * (1-p)` | 0 |
| taker | 0 | `0.05 * qty * p * (1-p)` |

An IOC that crosses is taker. Example at p = 0.60, qty = 10: fee = 0.12 (`check_identity.py`).

Slippage. `PredictionMarketTakerFillModel` (`fill_model.py:97`) is built only when `fill_model_mode == "taker"` (`_prediction_market_backtest.py:446-447`). This replay is `passive_book`, so the IOC walks the L2 ask with `liquidity_consumption=True`. The fill price is the book, not a one-tick synthetic slip. Partial fills stop at displayed size. Min size stays 5 shares (`strategy.py` constants, `MIN_ORDER_SIZE = 5`).

### 5. Signals

`MatchSignals` is already per match: feed timestamps, predicted deltas, anchor mids, deaths, kill gates (`signals.py:466-484`). The adapter arms a time alert per feed tick and per kill (`strategy.py:307-328`) and pushes `SignalUpdate` from `_sync_signal` (`strategy.py:1199-1224`). Book deltas already wake the quoter (`strategy.py:350-365`). A whole-map quoter keeps those alerts. It does not need a denser signal clock.

Fair:

```text
book_p = calculate_book_p_radiant(radiant mid, dire mid)   # None if the pair breaks PAIR_SUM_TOLERANCE (0.05)
micro  = (ask * bid_size + bid * ask_size) / (bid_size + ask_size) on the radiant token, optional
fair   = clip(book_p + k * predicted_delta, 0, 1)
```

`k = 0` is pure book mid (or microprice) with the same signal tape still loaded, so the kill gate still sees deaths. `k = 1` is the current model delta. Do not copy Follow300's `|delta| >= 0.02` gate or the [0.45, 0.85) band. Price band for this strategy is [0.03, 0.97]. Re-anchor fair to the live book on every quote. The 0.25 s Follow300 latch is a SELL-exit device.

Stale feed: if the latest signal is older than `entry_stale_s` (schedule binding, else `GRID_FEED_STALE_SECONDS = 16`), pull both bids and keep the last deaths for the kill gate until the hold expires. Stale book: either token's top of book older than `MAX_BOOK_AGE_SECONDS = 5` (`shared/utils/telonex_book.py:18`) pulls both bids. Mid-spike: radiant or dire mid down 0.10 within 10 s cancels bids for 30 s (`shared/constants/strategy.py:57-60`). Kill gate: no bid on the victim token for 10 s (`KILL_GATE_HOLD_S = 10`, `kill_gate.py:27-39`).

`MODEL_WINDOWS["dota"]` is `range(-60, 480)` (`archive_index/schedule.py:87-89`). Schedule admission uses only `.start` (`signals.py:584-593`): a tick with `game_second >= -60` and a feature row at that second gets a delta. Feature parquet `data/new_processed/dataset/game_features.parquet`: 2,248,559 rows, `game_second` from −60 to 6648, 1,780,919 rows at or after 480 (`check_identity.py`). The model can be evaluated past the Follow300 cutoff when the schedule tick's second exists in that table. This run did not call the model, so it did not count how many schedule ticks after 480 actually have a row.

### 6. Time range

Replay window is `horn − 2 minutes` through `game_end + 1 minute` (`context.py:12-32`). Books after the 480 s cutoff are already in the tape. Follow300 stops buying because `buy_cutoff_ns` is the feed tick where `game_second >= policy.buy_cutoff_second` (480) (`run.py:538-547`, constant `strategy.py:6`). `_on_buy_cutoff` cancels live BUYs (`strategy.py:556-559`). Accepts that land after the cutoff are cancelled too (`strategy.py:386-389`).

For two-sided, set `buy_cutoff_ns = game_end_ns` and do not arm a separate cutoff. Quote while `observed_clock.game_second >= -60` and the tick is not terminal. The schedule path fills `ObservedClockTape` (`run.py:552-556`). The non-schedule clock in `DotaMakerStrategy._clock_at` is 0 until the cutoff and then jumps to 480 (`strategy.py:1113-1122`). The new strategy should require the observed tape on Dota validation runs, which already have it.

`_on_game_end` cancels every resting order (`strategy.py:561-571`). Do the same, and set a finished flag so later book deltas do not place. Instrument expiration is `market_closed_at`, not game end (`run.py:328-337`, `install_settlement_compatibility` at `run.py:387-413`). Settlement marks the open position; it does not trade the book. A cancel released 60 ms after the decision can still fill in that window. That is the current race, and it should stay.

Leftover inventory after the last merge settles at 0 or 1 on its own leg. The kernel's post-merge qty is what the report calls leftover. The venue's position is the pre-merge gross, and the $1 per pair is inside `engine_pnl`.

### 7. Reporting

Already present and still correct if the venue is left alone: `total_engine_pnl`, `cash_flow`, `settlement_remainder` (`postprocess.py:671-673`), `maker_rebate`, `taker_fee`, `net_pnl` (`postprocess.py:677-681`), wallet `required_cash` and `required_cash_with_reserves`. The two-column report already prints rebate and taker fee (`report.py:203-205`).

`terminal_position` is the last fill's `position_after` (`results.py:197-200`). That field is one number (`telemetry.py:29`). It cannot describe two leftovers. Stop using it for this strategy.

Add, summed in `summarize_arm` and printed under PNL:

| field | meaning |
|---|---|
| `merge_count` | merges with `M >= threshold` |
| `merge_shares` | sum of paired shares |
| `merge_usdc` | same number, in dollars (1 per share). Not added to `net_pnl` |
| `leftover_shares_0`, `leftover_shares_1` | kernel qty after the last merge |
| `leftover_settlement_usdc` | those shares marked with the catalog winner |
| `peak_unreturned_usdc` | `required_cash_with_reserves` on the two-lot + merge tape |
| `taker_flatten_fills`, `taker_fee` | taker fee is already there; add the fill count so a flatten is visible |

`engine_pnl` stays the settlement sum so a two-sided run is comparable to a hold of the same fills. The report line should say the pairs inside that PnL are paid at settlement, and `peak_unreturned_usdc` is the number that moves when merge returns cash early.

`MakerMatchResult` (`results.py:90-125`) needs the per-match merge and leftover fields with defaults, so old Follow300 checkpoints still load. Follow300 rows stay at zero merges.

### 8. Runtime plan

`scripts/run_seeds.sh` already forwards extra args to `python -m backtest.run` (`run_seeds.sh:44-46`, `68-89`). No script edit. Archive `DONE` is per `--name` and stores fills (`shared_archive.py:32-38`, `run_seeds.sh:92-98`), so a two-sided name must not reuse a Follow300 name.

Smoke:

```text
SEEDS=1 SHARDS=1 scripts/run_seeds.sh dota twosided-smoke \
  --limit 1 --strategy two-sided --half-spread-ticks 2 --model-k 0 \
  --level-usdc 50 --merge-min-shares 5
```

Twenty maps, one process. `MAX_MATCHES_PER_BATCH` is already 1 (`run.py:223`). The orchestrator explore put a Follow300 seed near 3–5 GiB; this run did not remeasure RSS, so keep `SEEDS=1 SHARDS=1` and do not stack shards:

```text
SEEDS=1 SHARDS=1 scripts/run_seeds.sh dota twosided-h2-k0 \
  --limit 20 --strategy two-sided --half-spread-ticks 2 --model-k 0
```

The book window is already the full map, so tape decode does not grow versus Follow300. Extra cost is order churn from second 480 to the terminal tick. Expect the same order of magnitude as 20 Follow300 maps: a few minutes of engine time for one process, plus a small postprocess. Seed0's whole validation quote tape is 53 MB for 838 maps; twenty maps of denser quotes are still small. Do not start three shards for a 20-map run.

Grid, each cell its own `--name`:

| wave | axes | runs |
|---|---|---|
| 1 | `h ∈ {1,2,3}` ticks × `k ∈ {0,1}` | 6 × 20 maps |
| 2 | on the best `h`: `g ∈ {0, 0.0001, 0.001}` per share, `N_max` in shares, `M ∈ {5, 50}` | a handful |

`--level-usdc` already exists (`run.py:1468-1472`) and is the clip in `shares = clip / price`. New flags read only when `--strategy two-sided`: `--half-spread-ticks`, `--skew-per-share`, `--net-max-shares`, `--net-taker-shares`, `--merge-min-shares`, `--model-k`, `--rung-count` (1..3). Default rung step is 1 tick, same as `BUY_LEVEL_STEP_TICKS`.

Telonex's materialized cache is outside the run directory and is reused across names. The signal archive inside the run directory is not: different names rebuild it. That rebuild is schedule and model work, not a second copy of the books.

### 9. Effort

| order | file | what | lines |
|---:|---|---|---:|
| 1 | `src/backtest/two_sided.py` | new kernel: integer-tick quotes, skew, pull at `N_max`, IOC intent, merge ledger, kill/mid-spike/stale gates. `python -m backtest.two_sided` asserts quote sum and merge identity | ~280 |
| 2 | `src/backtest/two_sided_strategy.py` | new `Strategy`: both books, submit/cancel with the 175/60 ms delays, fill telemetry, game-end cancel. Copy the cancel-release slice from `strategy.py`; do not subclass | ~420 |
| 3 | `src/backtest/run.py` | `--strategy` and the two-sided flags; branch in `build_strategy_configs` and `build_kernels` | ~90 |
| 4 | `src/backtest/telemetry.py` | `kind="merge"` on `QuoteEvent`, or a small `MergeRecord` if a new kind would break the quote store | ~20 |
| 5 | `src/backtest/wallet_path.py` | two lots per match; apply merge cash | ~70 |
| 6 | `src/backtest/report_capital.py` | same lot model as the wallet path | ~40 |
| 7 | `src/backtest/results.py` | per-match merge and leftover fields with defaults | ~50 |
| 8 | `src/backtest/postprocess.py` | sum those fields into the arm | ~35 |
| 9 | `src/backtest/report.py`, `report_types.py` | PNL lines and typed keys | ~45 |

`scripts/run_seeds.sh`, `seed0_replay.py`, `telonex_local.py`, `onchain_side.py`, `strip_own_book.py`: no change. Smoke goes through `python -m backtest.run --limit 1`, not `seed0_replay.py` (that module constructs Follow300 kernels directly, `seed0_replay.py:33-41`).

The one check before any engine run: `python -m backtest.two_sided` asserts (1) bid sum ≤ 99 ticks on the grid in `quote_sketch.py`, (2) merge-then-settle equals `compute_binary_settlement_pnl` for both winners and for unequal qty, (3) `|net| > N_max` places no order on the adding side. The sketch and the settlement call already pass in `work/grok-bt/`.

poly-maker: nothing. The backtest does not import it.

prediction-market-backtesting: nothing required. A complementary matcher (our YES bid becomes a NO ask, so the venue rejects a self-cross) would be a library change and is the expensive way to enforce a rule the quote already enforces in integer ticks. `strategies/binary_pair_arbitrage.py` buys both asks and holds to resolution (`binary_pair_arbitrage.py:76-87`, `411`). `strategies/private/passive_pair_accumulation.py` rests post-only buys and also holds. Neither is a merge, and neither should be wired in.

### Sanity check: merge PnL vs settlement PnL on a Follow300 map

LIVE seed0 fills: `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_shared-20261005/seed0/fills.parquet` (12,404 fills, 601 matches).

| check | result |
|---|---|
| matches with a fill on both token indexes | 33 |
| of those, buy qty ≥ 5 on both tokens | 32 |
| matches whose running inventory had `min(yes, no) ≥ 1` share at any fill | 0 |

Example of the largest gross both-sides buy, match `9026301227`: buy 2,551 and 4,221 shares, sell 2,551 and 4,221, so the net pair is dust. Follow300 bought the other token only after it had sold the first. The residual identity on those 33 maps is true and uninformative.

The informative check is the direct call to `compute_binary_settlement_pnl` in section 2. Held pairs pay the same PnL at settlement as they would have paid if merged. The Follow300 tape cannot show the capital-timing difference, because it never held the pair.

## Open questions / what I could not verify

- No Nautilus replay in this run. Queue behavior on two live orders is from the engine setup (one book and one trade tape per instrument, `queue_position=True`), not from a two-sided smoke.
- The −97 vs +3 capital figure is a replica of `wallet_path.py:346-368`, not a call into that module.
- Schedule ticks after second 480 were not passed through the model. Feature rows exist out to second 6648; admission allows them; a real `MatchSignals` count is still undone.
- `liquidity_side=TAKER` on an IOC that walks the book is the Nautilus matching rule the adapter already reads (`strategy.py:442`). This run did not execute an IOC.
- Unresolved markets (`is_50_50_outcome` or no winner) leave `realized_outcome` empty and skip settlement (`backtest_utils.py:320-321`, `_result_policies.py:433-434`). Same as Follow300. A two-sided map in that set would show no engine PnL for either leg.
- Historical books do not contain the strategy's own size. The sim can fill as if those bids were extra liquidity. That is the usual backtest assumption; capacity is the other agent's question.
- On-chain merge gas and the CTF call are out of this design. The ledger uses a zero cost.

## Files

Scripts that produced numbers:

| path | what it checked |
|---|---|
| `work/grok-bt/check_identity.py` | `compute_binary_settlement_pnl` identity, seed0 both-token count, feature `game_second` range, one-lot replica |
| `work/grok-bt/check_identity.json` | their output |
| `work/grok-bt/check_real_map.py` | 33-map residual identity, simultaneous-pair peak, mirrored price sums on match 8838170936 |
| `work/grok-bt/check_real_map.json` | their output |
| `work/grok-bt/quote_sketch.py` | integer-tick bid sum and merge threshold |

Commands, cwd `esports-trader`, interpreter `.venv/bin/python`. The first two need `PYTHONPATH=src:../prediction-market-backtesting`. Paths below are absolute.

```text
PYTHONPATH=src:../prediction-market-backtesting \
  .venv/bin/python /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge/work/grok-bt/check_identity.py
PYTHONPATH=src:../prediction-market-backtesting \
  .venv/bin/python /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge/work/grok-bt/check_real_map.py
.venv/bin/python /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge/work/grok-bt/quote_sketch.py
```

Code cited, not modified: `esports-trader/src/backtest/{run,strategy,postprocess,signals,context,telonex_local,onchain_side,results,report,report_capital,wallet_path,seed0_replay,telemetry,feed_schedules}.py`, `esports-trader/src/strategy/{engine,quoting,types,policy,kill_gate,mid_spike}.py`, `esports-trader/scripts/run_seeds.sh`, `prediction-market-backtesting` files named in the sections above.
