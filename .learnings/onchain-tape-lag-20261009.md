# Backtest trade tape lags the match by 2–6 s (2026-10-09)

## Finding

- The Nautilus backtest fills maker orders from `onchain_fills`. Its only time is
  `block_timestamp_us`, the Polygon block. On 2026-10-08 that block came 1.7–3.9 s
  (p10–p99, p50 2.5 s, max 6.0 s) after the WS print of the same tx; 108,538 Dota
  fills joined by tx hash, 2.8% of rows have no print.
- The book deltas are on time. So the sim cancels a quote on the book move (cancel
  latency 60 ms) and only then sees the print that would have filled it. It keeps the
  benign fills and skips the toxic ones.
- Proof at the order level (`investigations/2026-10-08-liveb-postmortem/calib.py`):
  the 10,629 real wallet-B orders of 2026-10-08 replayed under the framework's queue
  rule (queue ahead at placement, prints at our price eat it, level delete zeroes it).
  Real: 503 filled orders, 8,282 shares, markout30 −0.84 c/share.
  Tape on block time: 218 filled, 2,801 shares, markout30 −0.06.
  Tape on block time − 4 s: 283 filled, markout30 +0.56.
  Tape on WS print time (tx hash join): 488 filled, 8,035 shares, 468 of them the real
  orders, markout30 −0.67.
- Every backtest built on the block-time tape is suspect, including the 307-map
  two-sided sweeps of 2026-10-07/08.

## Fix (esports-trader, uncommitted on 2026-10-09)

- `scripts/sync_collector_parquet.py` now pulls the `trades` channel too (it was
  excluded since the onchain switch; local `trades` stop at 2026-09-17).
- `src/backtest/onchain_retime.py`: the linked onchain day file gains `timestamp_us`
  = the `trades` print time of the same tx hash. A row whose tx has no print is
  dropped and counted in a warning; the block time is never used as a stand-in. The
  framework reads `timestamp_us` before `block_timestamp_us`. On 2026-10-08 against the
  investigation's journal dump: 108,538 rows matched, 3,078 dropped (2.8%), 6 of wallet
  B's 523 fills among them. That dump ends at 17:59:59 UTC (hour file boundary); the 6
  fills are 18:00–18:15 UTC and all 6 tx hashes are in the VPS `trades` day file of the
  sibling token (593 rows, 17:20–18:59). The drops are the dump's cut, not the collector's.
- `src/backtest/telonex_local.py`: selection requires, for every window day whose
  onchain day file holds a fill, the trades day file of that token (the capture writes
  no trades file for a day without a print, so an empty onchain day needs none). A
  traded day without prints has no clock, so the map is not eligible and `link` raises.
  The trades files are read for the join only and are not linked into the tree, so the
  framework still executes on the per-OrderFilled onchain rows. On the local tree up to
  2026-09-17, 878 of 21,620 non-empty onchain token-days (4%) have no trades file. 874
  are before 2026-08, the Telonex vendor era: per `_control/availability`, 314 tokens
  have no trades channel at Telonex, 224 days fall outside Telonex's trades range, 330
  tokens have no record, 6 are our download gaps. In our collector's era (Aug–Sep) the
  only 4 cases are single-row midnight spill-overs. Telonex-era maps without prints
  drop out; nothing on our side can recover them.
- VPS archive on 2026-10-09: `trades` 2,532 asset dirs, 200 MB, day files through
  2026-10-08 (80–108 per day in late September, 48 on 10-08); books 22 GB; onchain 1.5 GB.
- `scripts/sync_collector_parquet.py` is the one sync: `docs/rebuild-order.md` step 1
  and the live inspector button (`src/viewer/live_sync.py`) both run it, so both pull
  `trades` now. Run it for `--game dota` and `--game lol` before any backtest, or every
  map after 2026-09-17 drops out for a missing trades day.

## History (why trades were dropped on 2026-09-19)

- Until `6ba30b63` (2026-09-19) the backtest executed on the collector's `trades`
  channel (WS `last_trade_price`): one print per tx, one price, total size. To let
  queue_position fill deeper levels, `explode_trades.py` spread each print across the
  pre-sweep book ladder, a guess. `onchain_fills` was optional and preferred when
  non-empty (`choose_execution_channel`).
- `6ba30b63` dropped trades and explode: "Maker backtests need per-log OrderFilled
  grain". The sync then excluded `trades/`. `005ef59c`/`ecd2a570` moved on-chain
  collection into the TypeScript collector (`onchain` service, Alchemy RPC).
  `69499bd6` (2026-09-28) fixed the aggressor side of mirrored rows.
- The third source, Polymarket's public `data-api.polymarket.com/trades`, is the
  framework's own fallback. `backtest/run.py` sets
  `TELONEX_DISABLE_POLYMARKET_TRADE_FALLBACK=1`; it is never used.
- The grain argument was right and stays: onchain rows remain the tape. What the
  switch lost was the clock. The fix borrows only the clock from `trades`.

## WS prints are not a tape

- One `last_trade_price` per tx: one price and the total size, even when the taker
  swept several levels. Of our 107 same-token fills, the print price was below our
  bid 33 times and above it 20 times.
- 412 of our 519 joined fills show no print on our token at all: the taker bought the
  sibling token (mint match). The onchain rows carry the maker token and price exactly.
  Use onchain rows for what traded, WS prints for when.
