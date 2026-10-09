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

## Fix (esports-trader `4773d0b1`, 2026-10-09)

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

## Aggressor side: take it from the maker (fixed esports-trader `b73bbd8a`, 2026-10-09)

- The onchain day file holds each OrderFilled in the files of both tokens. A row
  without `mirrored` has `maker_asset_id == asset_id`; a mirrored row has
  `maker_asset_id == sibling_asset_id`. Checked on all 33,860 local day files
  (Dota and LoL): no exception, no nulls, `maker_side` only buy or sell.
- `taker_asset_id` changed meaning on 2026-09-18, when the source changed.
  Days up to 2026-09-17 are Telonex downloads (the collector's legacy import):
  there it is the maker's token on a direct trade and the other token on a mint
  or merge. From 2026-09-18 (first collector-owned day by the data; the VPS
  `ONCHAIN_START_DATE` not checked) our `polymarket-collector`
  decodes the logs itself and writes `taker_asset_id: mirrored ? tokenId :
  siblingTokenId` (`src/onchain-decode.ts`), the token opposite the maker on
  every row. That is a collector bug: the taker's token is in `OrdersMatched`
  (`matched.token_id`), which the collector already reads for `taker_side`.
  `maker_asset_id` and `maker_side` come straight from OrderFilled and are right.
- The rule of `69499bd6` read `taker_asset_id`, so from 2026-09-18 it flipped
  the aggressor of every direct trade: a sale into our bid reached Nautilus as
  a buy and never filled a BUY. 463,358 of 1,944,524 rows over 21 days to
  2026-10-08. On every earlier row the old and the new rule agree.
- The rule now: maker on the file token -> aggressor = opposite `maker_side`;
  mirrored row -> aggressor = `maker_side`. Each trade gives one buy and one
  sell over the two books (6,363,495 each over all files).
- Proof: replaying wallet B's 4,862 real orders of 2026-10-08 fills all 63
  orders live filled, 1,048.4 shares on both sides (58 of 63 before). The 7
  extra sim orders are in
  `investigations/2026-10-08-liveb-postmortem/SIMULATOR-PLAN.md`.
- Cache trap for A/B runs of onchain code: a tree-cache miss evicts the
  market's framework `trade-ticks-v1`, a hit does not. Switch the code back to
  a version whose tree files are cached and the framework replays the ticks of
  the other version. Delete `~/.cache/nautilus_trader/telonex/trade-ticks-v1`
  (287 MB, rebuilt on use) before each run of an A/B pair.
- Effect on Dota follow300 seed 0 (`tapefix`, framework `e2cfebb`, not
  promoted): against `rawfix` (same framework, old side rule) the 653 maps that
  ended before 2026-09-18 match to the cent; 135 of the 224 later maps moved,
  PnL before rebate +$11,573 -> +$13,621, BUY fills 3,861 -> 4,266. Against
  `LIVE` (`strip-fix2`) on 868 maps: +$33,269 -> +$34,230, Wilcoxon p 0.83;
  the integer book prices give -$1,087 and the side fix +$2,048.

## WS prints are not a tape

- One `last_trade_price` per tx: one price and the total size, even when the taker
  swept several levels. Of our 107 same-token fills, the print price was below our
  bid 33 times and above it 20 times.
- 412 of our 519 joined fills show no print on our token at all: the taker bought the
  sibling token (mint match). The onchain rows carry the maker token and price exactly.
  Use onchain rows for what traded, WS prints for when.

## Effect on the Dota follow300 validation (2026-10-09 rebuild)

- Same models on both tapes, seeds 0 and 1, 822 shared maps: block-time LIVE
  `book-prior-85` +$35,913 per seed before rebate, print-time
  `book-prior-85-retime` +$32,194: -$3,719 per seed (-10%), -$4.53 per map,
  net per 100 shares $4.16 -> $3.42, buy volume +$67k (+11%), worst map
  -$989 -> -$1,341. Pooled Wilcoxon p 0.045, t p 0.26. The run also moved to
  `feed-schedule-v9`, which shifts the horn of 3 maps only.
- The new 2026-10-09 models against the old ones on the print tape, seeds 0, 1
  and 3, 865 maps: -$305 per seed, Wilcoxon p 0.45; 742 maps identical. The drop
  is the tape, not the model.
- LIVE pointed at `rebuild-1009` (esports-trader `c718aeef`) until the evening of
  2026-10-09; it now points at `strip-fix2` (own-book strip fix, native book
  loader), seeds 0, 1 and 3, +$2,638 per seed over `rebuild-1009` on the 82
  strip-affected archive maps and identical elsewhere. Seed 2 dies on
  the Nautilus `PositionOpened` assert on `dota2-ty-pari-2026-07-18-game2` with
  both models on the print tape; seed 3 replaces it. 0 of 892 validation maps
  dropped for a missing `trades` day.
- The first run on the new tape builds every onchain tree from scratch: the
  archive stage took 50 min on 4 shards instead of 7. Later seeds ran in ~10 min.

## STRATZ cache froze unparsed matches (fixed `8b0b51d8`)

- `make stratz` skipped every cached match, so a match cached before STRATZ
  parsed its replay (`missing_leads`) stayed unusable forever. On 2026-10-09
  11 of 51 such matches were already parsed upstream. The fetch now re-asks
  `missing_leads` matches each run (~2 min).
- 16 maps of 2026-10-07/08 (7 of wallet B's 12) and ~25 of late September are
  still unparsed at STRATZ, so they are not in the catalog or the backtest.

## Own-book strip: CancelUnsettled (fixed 2026-10-09, esports-trader)

- `strip_own_book.iter_resting_events` removed a resting order on `CancelAck` and
  `OrderRejected` only. Since `54b7ce50` (2026-09-29) a BUY cancel acks as
  `CancelUnsettled` (`session_core.note_cancel`), so every canceled wallet A BUY
  stayed in the reconstructed resting set and was subtracted from the book forever.
  Levels went to zero, Nautilus reset the queue, the sim filled on the next print.
  On 9034957701 at 14:26:11 UTC the strip held 4,957 shares at 0.45 and 2,130 at
  0.46 for A; the raw book had 0 and 23. 200 of 735 archives (every one after
  2026-09-29) carry `CancelUnsettled`, 16,109 events. Measured on `LIVE` seed 0
  (`strip-fix2`, 2026-10-09 evening): the bug cut trading, it did not inflate it.
  The over-strip removed other makers' size with ours, the best bid vanished,
  the spread gate blocked entries. 82 affected archive maps: PnL −$1,262 ->
  +$1,376, buy fills 1,228 -> 1,656; the other 267 archive maps and all 519
  grid maps identical to the cent, fills row for row. Seed 0 total $30,631 ->
  $33,269 before rebate.
- Two-sided replays never strip wallet A: it is another maker for B. B's own
  resting comes from `<root>/<archive>/core_trace.jsonl` via
  `backtest.run --own-archive-root`; `scripts/journal_to_core_trace.py` writes
  that file from the wallet engine journal because `TwoSidedWorker` writes no trace.
- On the 5 catalog maps of 2026-10-08 the two-sided sim still over-fills live:
  105 fills / +$10.43 / markout −0.93 c against 66 / −$12.97 / −1.30. 66 fills
  match live one to one. 17 fills have no seller print at or below our price:
  Nautilus fills a post-only bid when the mirrored ask crosses it. 17 fills had
  more queue ahead than every print at our price: Nautilus seems to count the
  whole sweep against the queue. The rest is position drift. Details in
  `investigations/2026-10-08-liveb-postmortem/SIMULATOR-PLAN.md`.
