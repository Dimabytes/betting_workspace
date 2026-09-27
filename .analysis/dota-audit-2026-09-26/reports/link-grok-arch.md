# link-grok-arch — cross-cutting architecture
Status: FINAL

HEAD `bbb28897`. Ranked by money and decision quality, then debug time, then iteration speed. No new S1. Item 1 is the architectural cause of orchestrator N4b (already verified there). Item 2 is fill-grok-F3, confirmed in the report code.

## Summary

- The pipeline has one strategy kernel and three clocks, three clip sizes, and three ways to decide that a derived file is fresh. The kernel itself (`strategy/policy.py`) is the part that did not drift.
- The number a person reads after a backtest (`report.py` hold block) is closed round-trips. About half of LIVE seed-0 profit is unsettled inventory and is not in that block (fill-grok-F3).
- A fix that lands in one feed or one game does not land in the sibling. Oddin horn pinning was fixed in `deebb730`; Steam/GRID still pin the first pre-horn tick (`live_feed.py:107`). Dota and LoL each have their own dataset writer.

## Ranked recommendations

1. **One horn rule for every feed.** `horn_is_pinnable` (`src/trader/live_feed.py:102-111`) returns true for the first Steam/GRID horn-clock tick and, for Oddin only, requires `second > 0`. `match_worker._maybe_pin_horn` then sets `_horn_pinned` and never writes again. N4b: GRID `grid-3006669-m1` pinned `16:19:40` and the real horn is ~`16:27:08` (449s pre-horn pause). Catalog prefers that stamp (`s06_publish_catalog.py:119-127`), and the live prior anchors on `event.horn_unix_seconds - 90` (`match_worker.py:553`). Labels, the market-seconds join, and the prior move together.
   Smallest change: require `second > 0` for every source, and do not latch `_horn_pinned` on a pre-horn tick.

2. **Print the settlement split the summary already has.** `format_terminal_report` (`src/backtest/report.py:94-102`, called at `:266`) prints hold min/p50/p90 for trips whose quantity returned to 0. `cash_flow` and `settlement_remainder` are on the arm (`postprocess.py:647-652`) and in `summary.json`. LIVE seed 0 is engine $2,849 = cash −$2,685 + settlement $5,534; the hold block is 357 closed trips, p50 147s, and the open tail (~$1,450) is absent (fill-grok-F3).
   Smallest change: add cash, settlement remainder, and open-position count to the pnl block.

3. **Key `market_seconds` on the catalog row, not on six numeric constants.** `CACHE_BUSTING_PARAMETERS` (`src/market_data/build_market_data.py:37-44`) hashes book-age, pair tolerance, spread, 30, horizon, and start second. `run_market_data_build` (`:167`) skips a match whose file exists. Horn, pauses, `radiant_token_index`, duration, and token ids are not in the key. N3: 34/34 rebuilt maps matched the stored file today, so this is latent. After item 1, every archive-horn map keeps the old join until someone deletes the directory.
   Smallest change: put a hash of `(horn, pauses, radiant_token_index, duration, token_ids)` in the cache path.

4. **Backtest size is not the live clip.** `BACKTEST_LEVEL_USDC` is `{"dota": 100.0, "lol": 100.0}` (`src/backtest/run.py:210`) and is what `follow300_policy` receives (`:413`). Live Steam/GRID is `base_size_usdc = 60` and Oddin is `200` (`config/trading.toml:35`, `:53`). A third name, `BASE_SIZE_USDC = 100` (`src/shared/constants/strategy.py:15`), is what `tests/test_backtest_maker.py` asserts. Queue position and pnl scale with size, so a $100 backtest is not a $60 live policy. The rest of the policy (delta, 480s cutoff, 3 rungs) does come from `strategy.py` via `follow300_policy` (`src/strategy/policy.py:50-76`), and cadence is read from the same toml (`run.py:409`).
   Smallest change: set `level_usdc` from the profile of the feed that map used (`dota-map` vs `dota-oddin-map`), and delete `BASE_SIZE_USDC`.

5. **Stop substituting the map outcome into markout.** `_reference_at_horizon` (`src/backtest/postprocess.py:135-151`) uses `settlement_value_for_token` when no mid exists, and that number is weighted into the markout the report prints (`:78-80`, `:412-451`). On this LIVE catalog every 30s/300s source was `mid` (fill-grok-F4), so the printed markout is clean today. The next thin book will mix the label into the metric.
   Smallest change: leave markout null when the source would be settlement, and drop that fill from the mean.

6. **Move the Nautilus monkeypatch out of `run.py`.** `install_settlement_compatibility` replaces `backtest._load_sims_async` (`src/backtest/run.py:351-377`) and `install_skip_market_artifacts` replaces `_build_market_artifacts` (`:380-387`). The file is 2,223 lines and also owns CLI, sharding, manifest, and summary (`def` list from `:216` through `:1987`). A framework bump that renames either private method fails inside the run orchestrator, not next to the patch.
   Smallest change: move those two installers into `src/backtest/nautilus_seams.py`. Do not split the CLI.

7. **Patch poly-maker by subclass, not by swapping class objects.** `patch_engine_classes` (`src/trader/engine_seams.py:350-366`) assigns `WalletStateStore`, `WalletCatalogStore`, `WalletFillProcessor`, `WalletUserStream`, `normalize_maker_trades`, and `SlimJournal` onto the frozen fork’s module globals. `poly-maker` cannot be edited. A fork rename breaks live order state with no type error at the call site.
   Smallest change: one function that builds `Engine` with those six objects as arguments, and delete the global assign if the fork’s constructor already accepts them. If it does not, keep the patch and add a startup assert that each name still exists.

8. **Dota and LoL dataset writers are copies.** `prepare_dataset.py:256-268` and `lol/05_prepare_dataset.py:798` both emit `game_features.parquet` with their own row loops. The horn-pin bug is the same class: a guard added on one path (`deebb730`, Oddin only). Linking already shares `team_names.py` and `series_format.py`; the dataset stage does not.
   Smallest change: one `write_game_features` used by both games. Leave feature computation where it is.

9. **`game_features.parquet` has no input stamp.** It is overwritten in place (`prepare_dataset.py:268`). The backtest manifest records `game_features_sha256` after the fact (`run.py:781`), so a finished run can be checked, but the file on disk does not say which catalog or which code produced it. A prepare that changes the join leaves old feature bytes looking current until the next full write.
   Smallest change: write `game_features.parquet` beside a stamp of catalog mtime and the prepare module’s hash, and refuse the backtest when the stamp is absent.

10. **The live “why did this map lose” page is four files and a wrong clock.** A map archive is `match.json` plus `session.jsonl` plus `core_trace.jsonl` plus `grid_state` or `oddin_state`. `src/viewer/live_tape.py:793` plots core_trace against `horn_at` from that `match.json`, which for GRID is the early pin (item 1). Nothing on that page shows cash versus settlement, or `yes_is_radiant` next to the last mid and the feed winner.
    Smallest change: one header on the existing tape: horn source, `yes_is_radiant`, last `market_p_radiant`, feed winner, realized cash. Do not build a new app.

11. **Lock the horn rule with one test.** `tests/test_trader_match_lifecycle.py` drives PRE_HORN ticks and checks quoting, not the persisted horn. `horn_is_pinnable`’s docstring says Steam/GRID keep the first tick (`live_feed.py:107`). That sentence is why `deebb730` did not spread.
    Smallest change: one test — GRID second −47 then second 0 — asserts `horn_at_utc` is the second call.

12. **`docs/as-is.md` is a snapshot from 2026-09-04.** Line 3 says so. It reports catalog 3040 maps and 172 trader archives (`:21-23`). The catalog on disk now is 3207 maps and `data/trader` has 657 `match.json` files. Counts in that file will be quoted as current.
    Smallest change: replace the count tables with “run `s07_link_readiness.py`”.

13. **Do not split `wallet_host.py` or `quoting.py` yet.** `wallet_host.py` (1,387 lines) mixes boot fencing, sidecar refresh, and discovery reconcile (`reconcile` at `:1010`, rebind at `:1089`). `quoting.py` (977) is the shared kernel the live and backtest adapters both call; splitting it creates the drift item 8 already has. `strategy.py` (1,339) and `match_worker.py` (1,233) are the adapters.
    Smallest change that pays: extract `reconcile` / `_handle_match` (`wallet_host.py:1010-1125`) into `discovery_reconcile.py` so the rebind rules sit next to `discovery.py`. Leave quoting alone.

14. **Experiment leftovers still import live constants.** `docs/experiments/pnl-action-v1/replay_pnl_action.py:30` imports `BASE_SIZE_USDC` and replays joins at $100. It is not on the `make` path, and it is the only production-looking use of that constant besides tests. `docs/as-is.md` is the other leftover (item 12).
    Smallest change: move `docs/experiments/pnl-action-v1/` to an archive note, or delete it if the conclusion is already in `docs/experiments`.

15. **Cheap speed: stop paying for a cache that will not notice a horn fix.** Rebuilding `market_seconds` scans Telonex per match (`build_market_data.py:103-106`) and skips whatever file is already there (`:167`). The expensive work is correct once. The waste is the next horn or token fix, which does not invalidate, so someone either trusts stale pnl or deletes the directory and rescans every map. Item 3 is the speed fix. The backtest’s own end (`write_finished_summary`, `run.py:1018-1036`) only compacts quote parts and writes `summary.json`; that is not the slow part.

## What is already one source of truth

`follow300_policy` is the join rule for live (`match_worker.py:350` and `:982`) and for the backtest (`run.py:412`). Delta gates, the 480s cutoff, rung count, and spread live in `src/shared/constants/strategy.py` and are not copied into `trading.toml`. `trading.toml` owns clip, debounce, and quoter tick, and the backtest reads the last two from that file. The leak is clip and the horn, not the gates.

## Not recommended

Splitting `run.py` into selection, signals, and IO before the Nautilus patch has a home. Splitting `quoting.py`. Editing `poly-maker`. A new observability service. The series-winner decider path (N2) is the design, not a boundary to remove.
