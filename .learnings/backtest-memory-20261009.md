# Backtest memory: why four shards swapped and what was changed (2026-10-09)

## Where the memory goes

- Telonex `book_snapshot_full` is one full snapshot per WS message: October
  days reach 1.87M rows per token with ~115 levels per snapshot (220 snapshots
  per second at peak). Both tokens of a market have the same row count.
- Framework cache miss on a book day (before the patch): `pd.read_parquet` of
  the whole day, ~11 KB of Python objects per row (21 GB for 1.87M rows), then a
  Python diff of the window's snapshots (22 GB for 632k rows). Per shard the
  loader ran 8 worker processes, so 4 shards were up to 32 such loads at once.
  That is the kernel_task swap pattern seen for weeks on new maps.
- Framework cache hit: the same map replays in 2.9 GB and 19 s. Hot-cache
  per-map time is 2.6 s median, 5 s p90 (`rebuild-1009` seed logs); a seed of
  877 maps is ~10 min on 4 shards, ~5 min on 8. Not a minute: the engine builds
  up to a million delta objects per map.
- `strip_own_book.strip_book_frame` on a whole day file: 9.8 GB for 480k rows.
  The CancelUnsettled strip fix (`643b1f73`) changed the strip module hash,
  which keys the stripped-book cache, so four shards re-stripped every archive
  book at once and the machine went down (2026-10-09 18:11).

## What changed

- esports-trader `5247d16b`: strip streams 20k-row batches (1.7 GB on the same
  file). Lives in `telonex_local.py` so the strip module hash, the cache key,
  stays put.
- esports-trader `dd3e004f`: rewritten archive book days keep only rows within
  60 s of the replay window (57% of rows over the 349 LIVE archive maps, 650k
  rows at worst instead of 1.87M); `REPLAY_LOAD_WORKERS` 8 -> 1 so a map's two
  tokens never materialize at once.
- prediction-market-backtesting branch `local-day-native`: daily files diffed
  in Rust over the window's row groups. 9.2 GB and 36 s for the worst map
  instead of 22.5 GB and 158 s.

## Running shards

- Any tree-cache miss on the book channel evicts that market's framework
  cache (`clear_telonex_cache_for_market`), by design. A strip logic change
  therefore means a full re-materialization of every archive map; plan it.
- Watch swap, not "free" memory: `sysctl vm.swapusage`. The 2026-10-09 run was
  killed by hand at 24.7 GB swap used, 60 MB pages free.
- `backtest.run --warm-cache` materializes without the engine; run it first
  after a mass eviction if memory is tight.
