# fill-grok-books — market data ingestion and provenance
Status: FINAL

## Summary

Collector-era **book** days on this Mac are not a pile of frozen partial files. From 2026-08-09 through 2026-09-25, every UTC day except one has at least one snapshot at 23:59 UTC. The exception is **2026-08-23**, whose latest snapshot is **13:48:54 UTC**. That day is not in `match_catalog`.

The sync rule that would freeze a short file is real: books are pulled with `rsync --ignore-existing`, a day counts as present if the parquet exists, and `build_market_data` does not rebuild a cache that is already on disk. It has not left a truncated book under a catalog map in this tree.

**2026-09-20 onchain fills** still start at **20:29:11 UTC** (2,204 rows). That hole is not what produced the LIVE prints. Seed 0 has 14 of the 18 maps; 10 of them have 28 BUY + 26 SELL fills, all `is_maker`, all before 20:29. Those fill timestamps are book-snapshot timestamps (same microsecond, price on the opposite touch). The run wrote `book-deltas-v1` for them and wrote no `trade-ticks-v1` file. `_backtest_cache/trade` does not contain these markets. The public-trades cache directory is absent, and `run.py` turns that fallback off before replay.

Paid Telonex history is a different source. It was bulk-downloaded on 2026-08-08 and stops at 2026-08-07. **2026-08-08** itself is missing on all three channels (four catalog maps, dropped by the presence check). Two earlier Telonex days, 2025-11-26 and 2026-01-18, end in the evening while catalog matches on those days run later.

Repos read: esports-trader `bbb28897`, polymarket-collector `a4fcb4c`. No SSH. Footers only, plus the timestamp column of the small 2026-09-20 onchain day and the slug column of the sixteen 2026-08-23 book files.

## Findings

| id | sev | claim | status |
| --- | --- | --- | --- |
| B1 | S2 | Book sync keeps the first local file forever; a present file counts as a full day; an existing market-seconds cache is not rebuilt. Collector book days 2026-08-09..2026-09-25 are complete through 23:59 except 2026-08-23. | verified |
| B2 | S3 | 2026-08-23 books stop at 13:48:54 UTC (16 files, 8 markets). No catalog map falls on that day. | verified |
| B3 | S3 | 2026-08-08 has no book, trade, or onchain files. Four catalog maps that day fail the presence check. | verified |
| B4 | S2 | 2026-09-20 onchain fills start at 20:29:11 UTC (2,204 rows). The LIVE maker fills before that are book-snapshot crosses, not onchain prints and not `_backtest_cache`. Every snapshot emits `BookAction.CLEAR`, which zeros queue ahead, so a resting order can fill from the book with no trade tick. | verified |
| B5 | S3 | Local `trades/` ends 2026-09-17. The book rsync excludes `trades/`. The loader tries onchain, then trades. Neither supplied the 2026-09-20 morning fills. | verified |
| B6 | S3 | Paid-history days 2025-11-26 and 2026-01-18 end by 17:57 and 19:52 UTC. 7 and 25 catalog maps on those days have no book through `ended_at`. | verified |
| B7 | S3 | `_backtest_cache/{book,trade}` is a July 2026 fold of the raw channels, one file per condition id. The LIVE loader does not read it. Inside the cached window, sampled raw row counts still match. The writer keeps an existing file, so a later raw rewrite would not refresh it. `trade-ticks-v1` is the cache the loader does read, and it is not evicted when books are rewritten. | verified |

## Provenance

Local root: `esports-trader/data/raw/telonex/polymarket/`. Layout is `book_snapshot_full|trades|onchain_fills/asset_id=<token>/YYYY-MM-DD.parquet`. Counts from a name-and-stat walk (`work/fill-grok/scan_days.py`, `day_coverage.json`).

| UTC days | books | trades | onchain fills | how to tell |
| --- | --- | --- | --- | --- |
| 2025-10-14 .. 2026-08-07 |  paid Telonex, one mtime cluster 2026-08-08 10:46 UTC | same cluster, 10:53 UTC | many of these days rewritten 2026-09-19 02:22 UTC | bulk mtime, not a next-morning compact |
| 2026-08-08 | absent | absent | absent | gap between the bulk pull and the first collector day |
| 2026-08-09 .. 2026-09-17 | collector; file mtime is the next UTC morning, about 03:30–05:20 | same next-morning mtime, through 2026-09-17 | dictionary-encoded strings mixed with plain strings on 09-15..09-17; older days share the 09-19 rewrite | compact hour is 03:00 UTC the next day |
| 2026-09-18 .. 2026-09-25 | collector, max snapshot 23:59:59 except where B2 says | no files | plain strings; daily mtimes. 2026-09-20 is the short tape in B4 | `trades/` excluded from the book rsync |

Book schema is full L2, decimal strings, on every era. `timestamp_us` is int64 everywhere. `local_timestamp_us` is **double on 3,512 files across 91 days, 2025-10-14..2026-01-18**, and int64 otherwise. The only day with both types is 2025-11-28 (`work/fill-grok/affected.json`). The market-data reader and the Nautilus trade parser both prefer `timestamp_us`, so the double column is the receive-time field, not the event clock (`telonex_book.py` loads `timestamp_us`; `telonex.py` `_onchain_fill_trade_ticks_from_frame` tries `timestamp_us` / `block_timestamp_us` before `local_timestamp_us`).

Empty sides show up as a null list element (209 int64 files and 92 double files). `parse_best_bid_ask` treats a null side as no level.

Collector capture, from `polymarket-collector`:

- A `book` message replaces both sides and emits one full snapshot once the token is ready (`reducer.ts` `applyBook`, around 215–259). A `price_change` batch is applied atomically, then one snapshot per ready token (262–327). Deltas before the first book are counted and not written (`preSnapshotDeltas`).
- Levels are stored as canonical decimal strings, bids descending, asks ascending (`sortedLevels`, 149–168).
- `timestamp_us` is the effective exchange time: source milliseconds if they fall in the sanity window, else local receive time, then raised to previous output + 1 (`timestamps.ts` `effectiveTimestamp`, 91–99). `local_timestamp_us` is receive time. The UTC partition is the effective timestamp.
- Reconnect gaps are not in the file. The SDK reconnects on its own; the next `book` replaces state. Contract text: `networkGapObservability: "not_exposed_by_sdk"` (`docs/polymarket_dota_archive_contracts.md` around 344–353 and 872).
- `tick_size_change` is journaled and is not a book column (contract 5.3, around 296–302). Prices in the parquet are absolute.
- Compaction replays the journal and publishes the day with an atomic rename after validation. Dota runs at 03:00 UTC on the next day, plus startup catch-up (contract 12 and 12.1, around 640–685). A public book file is not a growing intra-day object.
- Onchain rows use the block timestamp in microseconds (`onchain-decode.ts` around 278). A fill whose block timestamp cannot be loaded fails the decode (`missing block timestamps`, around 305–308) rather than skipping. A day is published only once a `finalized` head covers the end of the day (`onchain-days.ts` around 222–266). Unfinalized tips stay pending.

`parse_best_bid_ask` (`telonex_book.py` 89–103) takes the max usable bid and the min usable ask. It does not trust level order. Unusable means non-finite or size ≤ 0. Prices are `float()` of the decimal string. Cent and mil prices stay distinct in float64; this pass did not find a mis-ordered book.

## Truncated and missing days

Footer scan of every book file (`work/fill-grok/short_days.py`, 31,848 files, 343 days, 0 missing timestamp stats). A day is "short" here when the latest `timestamp_us` is before 22:00 UTC.

Collector era, judged against the 23:59 bar that the small days also clear (2026-08-17 has 24 files and still reaches 23:59:42):

| day | files | rows | latest snapshot | catalog maps |
| --- | --- | --- | --- | --- |
| 2026-08-09 .. 2026-09-25 except below | 16–184 | up to 24.1e6 | 23:59 UTC | — |
| 2026-08-23 | 16 | 3,322,689 | 13:48:54.158092 UTC | 0 |
| 2026-08-08 | 0 | 0 | absent | 4 |

2026-08-23 slugs (first row of each file; both tokens): `dota2-ty-ts8-2026-08-22` and its game1/game2, spilling past midnight and ending by 04:29; `dota2-vsn2-ts8-2026-08-23` game1–game4 and the series slug, last snapshot 13:48:54. File mtime max is 2026-08-24 03:39 UTC, which is the normal post-compact pull, so this is a finished parquet that never got the evening, not a torn rsync (`unread=0` on the collector-era footer pass). No catalog `horn_at` falls on 2026-08-22 or 2026-08-23 (`affected.json` horn counts). Affected catalog maps: none.

2026-08-08 catalog maps, both tokens missing that day's book file:

| match_id | slug | horn UTC | ended UTC |
| --- | --- | --- | --- |
| 8935608232 | dota2-jenz-ill-2026-08-08-game1 | 15:31:29 | 16:20:27 |
| 8935755968 | dota2-jenz-ill-2026-08-08-game2 | 16:47:05 | 17:40:39 |
| 8935913261 | dota2-amaru-jenz-2026-07-28-game1 | 18:26:03 | 18:56:58 |
| 8936009381 | dota2-amaru-jenz-2026-07-28-game2 | 19:26:22 | 19:51:50 |

`has_local_telonex_days` (`telonex_local.py` 54–67) requires the parquet to exist. These four are skipped, not replayed off a short file.

Paid-history short days with catalog damage (file max more than 10 minutes before `ended_at`):

- 2025-11-26, day max 17:57:53. Maps 8577833540, 8577907652, 8577926044, 8577982630, 8578012189, 8578065966, 8578108612.
- 2026-01-18, day max 19:52:40. Twenty-five maps, 8654095598 through 8655240937 (full list in `work/fill-grok/affected.json`). Several tokens have no file; the rest stop between about 09:00 and 19:52 while the match ends later.

Other short paid days (2025-10-17, 2025-10-18, 2025-12-07, 2025-12-31, 2026-01-03, 2026-01-12, 2026-03-30) did not leave a catalog map whose book ends before the match. Those mtimes sit in the 2026-08-08 bulk download, so this is the provider file, not `--ignore-existing`.

### 2026-09-20 onchain

`block_timestamp_us` has no parquet stats (dictionary columns). The column was read. 226 files, 2,204 rows, min 2026-09-20 20:29:11 UTC, max 2026-09-20 23:59:58 UTC. Neighbor onchain row counts from the same footer pass: 09-19 = 98,688, 09-21 = 176,268, 09-22 = 130,006.

Books that day: 174 files, 15,335,082 rows, max 23:59:59 (`day_footers.json`).

Catalog maps whose horn is before 20:29 and whose window touches 2026-09-20. All of them end before the first onchain row:

| match_id | slug | horn | ended |
| --- | --- | --- | --- |
| 9007618767 | dota2-playti-tc4-2026-09-20-game1 | 07:03 | 08:09 |
| 9007618656 | dota2-cs-xtreme-2026-09-20-game1 | 07:05 | 07:54 |
| 9007700576 | dota2-cs-xtreme-2026-09-20-game2 | 08:23 | 09:05 |
| 9007784381 | dota2-cs-xtreme-2026-09-20 | 09:49 | 11:00 |
| 9007918871 | dota2-gl-lgd-2026-09-20-game1 | 11:31 | 12:18 |
| 9007993994 | dota2-lynx-pckcp-2026-09-13-game1 | 12:17 | 13:05 |
| 9008125103 | dota2-pi-yb1-2026-09-20-game1 | 13:32 | 14:31 |
| 9008160103 | dota2-aur1-navi-2026-09-20-game1 | 13:53 | 14:37 |
| 9008150824 | dota2-lynx-pckcp-2026-09-13-game2 | 13:40 | 14:17 |
| 9008292090 | dota2-pi-yb1-2026-09-20-game2 | 15:01 | 15:50 |
| 9008297395 | dota2-aur1-navi-2026-09-20-game2 | 15:04 | 15:38 |
| 9008413867 | dota2-ty-nem-2026-09-20-game1 | 16:07 | 16:34 |
| 9008403049 | dota2-pckcp-balu-2026-09-20-game1 | 16:03 | 16:42 |
| 9008436237 | dota2-pi-yb1-2026-09-20 | 16:22 | 17:49 |
| 9008510292 | dota2-ty-nem-2026-09-20-game2 | 17:01 | 17:30 |
| 9008530635 | dota2-pckcp-balu-2026-09-20-game2 | 17:10 | 17:43 |
| 9008670336 | dota2-kalmy-tm6-2026-09-15-game1 | 18:36 | 19:18 |
| 9008779767 | dota2-kalmy-tm6-2026-09-15-game2 | 19:47 | 20:23 |

Dota's replay config does not set an execution channel. The loader tries `onchain_fills`, then `trades` (`telonex.py` `_TELONEX_TRADE_TICK_CHANNELS`, `load_telonex_onchain_fill_ticks` 3388–3404). An empty parse returns None (3383–3384). There is no `trades/` file after 2026-09-17. `run.py` `main` calls `load_dotenv()` then `setdefault("TELONEX_DISABLE_POLYMARKET_TRADE_FALLBACK", "1")` (1991–1994). That line has been there since `73206975` (2026-08-04). No `.env` in esports-trader sets the variable. `~/.cache/nautilus_trader/polymarket_trades` does not exist, so the public API fallback was not cached on this machine.

The raw onchain hole is real. It is not the tape behind the LIVE fills. See the next section.

Onchain replace rule (`sync_collector_parquet.py` 193–220): same sha256 is kept; a different local file is replaced only when `--onchain-start-date` is set and the day is on or after it; otherwise it is a conflict and the local bytes stay. The flag defaults to None (384–388). The docstring's example date is 2026-09-21, which would leave 2026-09-20 frozen. Whether that flag was passed on the pull that wrote these files was not checked (no SSH, no shell history).

## Where the 2026-09-20 LIVE fills came from

Seed 0 `fills.parquet` (mtime 2026-09-24 21:31:17 UTC) has 54 rows on these maps: 28 BUY and 26 SELL, every one `is_maker` and `fill_model=queue`, every one before 20:29. Ten maps have those fills. Four more are in `results.parquet` with no fills (9008160103, 9008150824, 9008292090, 9008403049). Four are not in the run (9007993994, 9008530635, 9008670336, 9008779767).

Two fills on 9007618767, token 0, against the raw book (`cache_vs_raw.py`):

| fill | time UTC | price | book at that microsecond |
| --- | --- | --- | --- |
| BUY | 07:23:21.137000 | 0.69 | bid 0.68, ask 0.69 |
| SELL | 07:25:31.000001 | 0.67 | bid 0.67, ask 0.68 |

The fill clock is the book snapshot clock. There is no onchain row in that window to carry those timestamps.

The same run wrote `~/.cache/nautilus_trader/telonex/book-deltas-v1` for these slugs at 21:29 UTC (playti game1, both outcomes). In the window 21:20–21:40 UTC that directory gained 24 book files and `trade-ticks-v1` gained zero. Those slugs are still absent under `trade-ticks-v1` (onchain_fills and trades). A successful Telonex trade load writes that cache before returning (`telonex.py` 3541–3554). Nothing was written, and there was no older file to hit.

What the engine does with a book instead: each snapshot starts with `BookAction.CLEAR` (`telonex.rs` `BOOK_ACTION_CLEAR = 4`, `append_snapshot_rows` around 603–608). Nautilus `BookAction.CLEAR` is 4. On CLEAR the matching engine sets every resting order's queue-ahead to 0, then iterates (`engine.pyx` 4188–4212). `determine_trade_fill_qty` (`engine.pyx` 6650–6692) blocks only while queue-ahead is still positive. With no trade tick, `_last_trade_size` is unset, so a zeroed order is allowed its full remaining size, and the book fill runs if the order is marketable. A snapshot that puts the ask on a resting bid, or the bid on a resting ask, fills it. That is the 07:23 BUY at the ask and the 07:25 SELL at the bid.

`queue_ahead` on the fill row is the book depth the strategy stored at submit (`strategy.py` 1168–1176), not size that traded through.

## `_backtest_cache` is a different layer

`data/raw/telonex/polymarket/_backtest_cache/{book,trade}` is 2,035 files each, named `{condition_id}-{16 hex}.parquet`. The writer was `scripts/model_pipeline/track_s/backtest_cache.py` (commit `e07bf30b`, removed in `43bbd712`). It folds the raw `book_snapshot_full` and `trades` channels for one market window. The current loader never opens this directory: no reference in esports-trader, and the path is not one of `local_consolidated_candidate_paths` (`telonex.rs` 1021–1054). File mtimes run from 2026-07-12 22:09 UTC to 2026-07-24 22:18 UTC. None of the 18 condition ids are in it.

The loader's real trade cache is `~/.cache/nautilus_trader/telonex/trade-ticks-v1`, checked before the raw files (`_load_trade_ticks_cache_day`, 2629–2683). A hit returns those ticks and does not re-read the day file. `clear_telonex_cache_for_markets` evicts `book-deltas-v1` only (`telonex_local.py` 157–158, 247). A rewritten book does not drop a stale trade cache. That cache can be mixed-source across runs: whatever channel first returned rows (onchain, then trades, and the public API only if the disable flag is off) is what got stored, under that channel's directory.

Against raw, the July fold is a window extract, not a second feed. Whole-day raw row counts are larger because the day file continues outside the match. Filtered to the cache's own `timestamp_us` span, four markets that looked furthest off match exactly (trade and book): 8548193692 (160 / 3834), 8598618322 (324 / 17926), 8642086969 (230 / 6186), 8684124584 (14 / 714). The writer skips a file that already exists (`status: cached` when the path fingerprint matches). The fingerprint is source paths plus the window, not the file bytes, so a later rewrite of those paths would leave the July parquet in place. This sample has not diverged yet. The Sep 20 maps were never in it.

## Why a short book would stick

`build_rsync_argv` (`sync_collector_parquet.py` 137–156) is `rsync -av --ignore-existing`, excluding today's UTC date, `trades/`, and `onchain_fills/`. `rsync_pull.rsync_argv` also adds `--partial` (26–38). A file that exists locally is never updated. A transfer killed mid-file can leave a partial at the final name, and the next run skips it. This scan found no unreadable book parquet in the collector window; the short 2026-08-23 files have valid footers.

`run_market_data_build` queues a match only when the cache file is absent (`build_market_data.py` 164–167). `CACHE_VERSION` hashes numeric constants (line 45), not book completeness. A cache built from a short day stays short after a later full file, and the later file would not arrive anyway.

Today's file is excluded by UTC date, not by compaction hour. Compaction of day D is 03:00 UTC on D+1. A pull after midnight and before that compact does not see D yet, so it does not create a truncated D. The freeze happens if a short-but-valid D is published once (early compact, a collector that stopped mid-afternoon, or a `--partial` leftover that still parses) and a later complete file appears on the VPS.

## Checked and OK

- Best bid/ask does not use level index 0. Null and empty sides become one-sided quotes and are skipped by `find_asof_quote`.
- Collector event time is exchange time when the source stamp is sane, else receive time, and strictly increasing per token.
- Book days 2026-08-09..2026-09-25 except 2026-08-23 reach 23:59 UTC, including low-volume days.
- Onchain publish waits for a finalized head and errors on a missing block timestamp.
- `timestamp_us` type does not change across the Telonex/collector boundary. The double column is `local_timestamp_us` only, and only through 2026-01-18.
- Public-trades fallback was off for the 2026-09-24 LIVE run, and `_backtest_cache` does not hold those markets.

## Open questions

- VPS copy of `book_snapshot_full/.../2026-08-23.parquet` and `onchain_fills/.../2026-09-20.parquet`: if those are already complete, local `--ignore-existing` / conflict-keep is what stranded the short copies. Needs the VPS (sun-devin). Not checked here.
- Whether the 2026-09-20 onchain day on the VPS was itself only the evening (collector process started at 20:29) or a full day that lost the replace. Local mtime max for that day is 2026-09-22 23:01 UTC. The LIVE fills do not depend on that file.
- How often a CLEAR-zeroed queue fills a resting order that never traded through. The two probed fills sit on the touch. Not counted across the other 52.
- 2026-01-18 and 2025-11-26: provider gaps versus matches whose Polymarket book really stopped. Out of the collector-sync lead; listed because the catalog windows run past the files.
