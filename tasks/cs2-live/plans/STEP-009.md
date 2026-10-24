# STEP-009 plan: CS2 RoundSignals and the live profile

Repo: `../esports-trader` at `6dd5df0f` (worktree clean). Planner: plan-s9.
Scratch (measurement scripts on unchanged code only): `tasks/cs2-live/orchestrate/work/plan-s9/`.

The step adds `RoundSignals` and `RoundStrategy`, adds `GAME_PROFILES["cs2"]`, and adds a BUY window to `LiveCore` through one `buys_open` flag. The feed gets a per-game stale timeout (CS2 600 s). The step pins the live constants, `config/trading.toml` tables, compose env and mounts, and the league whitelist. Dota and LoL behavior stays equal: the STEP-005 GRID diff, signal diff and core replay gates must match the baseline. No test or check sends an order to the venue.

## 1. Facts this plan rests on

### 1.1 Seams that exist today

- `SignalSource` and `StrategySpec` protocols: `src/trader/strategy_spec.py:25-86`. `LoadedModel = ModelServer | Cs2RoundModels` at :13.
- The worker builds signals once: `src/trader/match_worker.py:189-195` (`build_signals(model=..., feed_source=feed.source, enqueue_core=self._enqueue_core, requote=self._quote_board)`). Every tick goes to `self._signals.on_tick(event, core_now_ns())` before the worker looks at it (:237). The `finally` block of `run()` calls `_stop_feed_timers()` → `watchdog.disarm()` + `signals.stop()` (:263-274).
- `Follow300Signals` (`src/trader/follow300_signals.py`): a gate reason gives `signal=None` (:56-59). The timer pattern is `loop.call_later(BOARD_REACTION_SECONDS, self._requote)` in `_board_handles` (:98-109). `stop()` cancels the handles (:81-84).
- `GameProfile.feed: MobaGridFeed` (`src/trader/game_profile.py:40`). Profiles: dota (:45) and lol (:79). `LIVE_MAX_POSITION_LEVELS` has only dota and lol (`src/shared/constants/strategy.py:52-55`).
- `WidgetReducer` has only `reduce_frame` and `note_feed_gap` (`src/trader/grid_feed_spec.py:29-38`). `GridFeedSpec` has `services`, `read_board_sides`, `probe_delay`, `build_reducer` (:41-71).
- `Cs2GridFeed` is at `src/trader/cs2_feed.py:92`, `Cs2FrameReducer` at :127. The reducer has `board` but no `read_pinned_horn_iso`. `_event` (:583-614) builds a zero-MOBA `GameSnapshot`: `phase=IN_PROGRESS` (FINISHED when terminal), `second = now - map_live_unix`, `paused = not board.clock_ticking`, prior anchor `map_live_unix + DOTA_PRIOR_ANCHOR_SECOND`, or `PriorAnchor(None)` on a late join. Team a is `sides.radiant_id`, so `market_p_radiant` is the team-a mid.
- Feed gone gives a finished FeedEvent (`cs2_feed.py:172-177`). `note_feed_gap` gives one `RoundHistoryLost(FEED_GAP)` (:167-170). `_lose` returns no tick for `LATE_JOIN` (:569-581).
- `GridLiveFeed.stale_seconds` is a class constant `GRID_FEED_STALE_SECONDS` = 16.0 (`src/trader/grid_live_feed.py:46`, `strategy.py:78`). `wallet_host.py:1717` passes `feed.stale_seconds` to `MatchWorker` as the entry timeout. The exit timeout is `EXIT_FEED_STALE_SECONDS` = 45.0 when the caller passes none (`match_worker.py:183-188`, `strategy.py:82`).
- `BUY_CUTOFF_SECOND_BY_GAME["cs2"]` is 480 (`strategy.py:7-11`). Live readers: the core buy cutoff (`src/strategy/policy.py:70`, `src/strategy/quoting.py:153`) and the feed `past_cutoff` flag (`feed_selection.py:109`, `grid_live_feed.py:97`). The CS2 backtest uses per-map cutoffs (`src/backtest/cs2_inputs.py:154`, `:407`), not this table.

### 1.2 Core paths the step uses

- `permissions_from_quote` sets `allow_buy = sidecar_usable and not store.is_buy_blocked(...)` (`src/trader/session_core.py:208-224`). `LiveCore.__init__` sets `self.sidecar_usable = True` (:281). `run_core_cycle` passes `sidecar_usable=core.sidecar_usable` (:970). `run_core_cycle` is the only live caller of `live_sources`.
- `sync_inputs` emits a `PermissionsUpdate` on every cycle (:527-540). `apply` writes every event to the core trace (:588-606). A replay therefore sees `allow_buy` change with no new inbound event type.
- `PermissionsUpdate` is not a dirtying event (`src/strategy/scheduling.py:51-67`). It forces evaluation only with `halt` (:142). So a closed window removes a resting BUY only on the next fallback wake. The worker already has a direct BUY cancel: `_schedule_entry_cancel()` (`match_worker.py:1010-1017`), used by `_on_entry_feed_timeout` (:984-986).
- `buy_closed_reason` returns `"fair"` when `not permissions.allow_buy` (`quoting.py:248-249`). `"fair"` is not a key of `_ENTRY_BLOCK` (`session_core.py:145`), so `entry_block_from_reason("fair")` gives `EntryBlock.NO_EDGE` (:173). The journal takes `entry_block_from_reason(core.preview_block_reason())` (`match_worker.py:559`). This is the "fair/no_edge" contract: core block `"fair"`, journal `entry_block = "no_edge"`.
- Each `on_feed_event` result goes through `feed_core` → `SignalUpdate(signal)` (`session_core.py:1010-1020`), also when the signal is None. A None clears the core signal.
- `_quote_board` (`match_worker.py:508-515`) re-runs `_quote_pm` on the last fresh board event while its age is at most `entry_timeout_seconds`. `_quote_pm` (:431-452) gates with `window_reason(snapshot, fresh)` (`src/trader/session_quoting.py:103-113`). That returns `PAUSED` for a paused snapshot.
- `ROUND_SIDES` (`src/strategy/round_sides.py`) has `quote_through_pause=True`. `earlier_round_sells` keeps SELLs with `order.placed_ns < signal.call_ns`. `placed_ns` is core clock (`core_now_ns`), so `call_ns` must be core clock too.
- Backtest reference: `assign_round_signals` (`src/backtest/cs2_round.py:245-298`). The active call stays until the next call. A NaN-m0 call has delta 0 and the book anchor at the call. Before the first call, delta is 0.
- Prior: `_maybe_start_prior` (`match_worker.py:589-603`). LoL reads the book tape. Every other game calls `read_book_prior` on `archive_for(game)`. CS2 takes that branch with no change.
- `assigned_games` (`src/trader/trading_mode.py:22-34`) raises `TradingDisabled` when any profile's mode env is missing. A cs2 profile makes `CS2_TRADING_MODE` mandatory wherever the trader starts.
- `read_template(games)` (`src/trader/session_config.py:52`) needs `profiles.<primary>` and `clips.<game>` with exactly `{default, tiers, events}` for each assigned game.
- `grid_archive.summarize` (`src/trader/grid_archive.py:33-72`) uses `reducer.board` and `reducer.read_pinned_horn_iso()`. `_finish_terminal` calls it at map end (`match_worker.py:1263`). For a CS2 map it fails today. `_winner_from_grid_board` (:95) reads RADIANT/DIRE side text, so the CS2 winner is None.
- `archive_index`: `audit_identity` tests `game not in GAME_PROFILES` (`src/archive_index/index.py:164`). `build_index` calls `load_universe(game)` for each game in GAME_PROFILES (:653). `load_universe("cs2")` raises `ValueError` (`src/archive_index/universe.py:87-93`). The default index root is the trader archive (`src/archive_index/__main__.py:55`). After this step a CS2 trader archive would crash the index without a fix. Schedule extraction runs only for identity `ok` (`index.py:665`).
- Tests that use "cs2" as an unknown game: `tests/test_trader_wallet_host.py:1626`. `tests/test_core_trace_round.py:53-59` builds a cs2 trace with a monkeypatched profile.

### 1.3 Measurements on unchanged code

All files are in `work/plan-s9/`.

- `tape-timing.txt` (`tape_timing.py`, tape map 3): 461 ticks, 419 FeedEvents, 21 dues, 21 live starts, 0 losses. Longest in-map FeedEvent gap 129.6 s. Gaps over 16 s: 68. Over 45 s: 7. Over 120 s: 1. Over 600 s: 0. 80 FeedEvents are paused. Every due tick arrives 0.7–1.7 s before its `due_ns`. No due falls in a pause. Due to live start is about 17 s; outliers R2 251.6 s, R10 55.9 s, R17 59.5 s.
- `call-timing.txt` (`call_timing.py`): one CS2 call (frame + both models) median 143.0 ms, max 145.5 ms; the frame is 5.4 ms. Dota, oddin and lol predicts are about 0.16 ms.
- `loop-stall.txt` (`loop_stall.py`): an inline call stalls the event loop up to 436.8 ms. A single-worker executor gives max 9.8 ms, p99 8.0 ms.
- `model-identity-base.txt`: `model_file_sha((CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR))` = `7eba748bcdf6162e668f9a47b1c97e63c73d3b12275fcbf33cc01a0188c5f607+a1fc1a12d5c12baf83dfbca61fea8bf248275abc4c8af5621ec5563ef8aca2c7`. Both halves equal the LIVE manifest `model_sha256` and `cs2_outcome_model_sha256` (`data/backtests/cs2_maker/LIVE/seed0/manifest.json`). Reference name `20261022T142007Z+20261022T142332Z`.
- `tape-markers.txt`: 78 connect/disconnect pairs. 73 closes follow exactly 120.0 s of silence. Two closes fall inside map 3: 21:16:46.620Z (120.0 s idle, R2 pause) and 21:39:54.985Z (31.2 s idle).
- `gamma-pgl.txt` (public Gamma GET at 2026-10-24T04:07Z): 8 open "PGL Masters Bucharest Group Stage" BO3 events, ids 1199656–1199661, 1199663, 1199665. `eventMetadata.league = "PGL Masters"`, serie "Bucharest", tournament "Group Stage". `gridSeriesId` is None on all 8. 112 of 437 open CS2 events have one.
- The CS2 universe (`data/cs2/universe/events.parquet`, latest start 2026-10-17) has no title league "PGL Masters…". New whitelist names change no backtest selection. They change the whitelist sha.

## 2. Design decisions

D1. **Per-game feed stale timeout on the feed spec.** Add `CS2_FEED_STALE_SECONDS = 600.0` to `src/shared/constants/strategy.py`. `GridFeedSpec` gets a read-only `stale_seconds: float`. `MobaGridFeed` returns `GRID_FEED_STALE_SECONDS`; `Cs2GridFeed` returns `CS2_FEED_STALE_SECONDS`. `GridLiveFeed.__init__` takes a required `stale_seconds: float` and stores it on the instance; the class attribute goes. `_build_feed` passes `profile.feed.stale_seconds`. Reason: the CS2 board is event-driven; the tape has 68 gaps over 16 s, the longest 129.6 s, and a 251.6 s R2 pause. 600 s is above all of them. Socket health stays with the socket (close ends the feed; a fault gives `FEED_GAP`).

D2. **Exit timer follows the entry timer when the caller gives none.** `match_worker.py:187` becomes `max(EXIT_FEED_STALE_SECONDS, feed_timeout_seconds)` for the `None` case. Dota/LoL (16 s) and the test default (3 s) still get 45 s. CS2 gets 600 s. Without it the exit timer sends `SignalUpdate(None)` after 45 s of board silence and the heartbeat puts the signal back 1 s later. `_core_freshness` (:1158-1163) does not change.

D3. **`GameProfile.feed: GridFeedSpec`.** `WidgetReducer` gets a read-only `board: Scoreboard | None` and `read_pinned_horn_iso() -> str | None`. `Cs2FrameReducer.read_pinned_horn_iso` returns `unix_seconds_to_iso_z(self._map_live_unix)` (`src/trader/live_feed.py:174`) when sides are locked and the map is not a late join, else None. The three dashboard MOBA reads (`src/dashboard/game_state.py:425-426`, `src/dashboard/match_state.py:192-194`, `src/dashboard/game_grid.py:224-225, 339-346`) narrow with `assert isinstance(feed, MobaGridFeed)`. STEP-011 replaces them with CS2 labels ("Подключить подписи сторон CS2 через контракт feed"). No CS2 trader archive exists before STEP-012, because `CS2_TRADING_MODE=off` until then.

D4. **`window_reason(snapshot, fresh, quote_through_pause)`, all required.** It returns `PAUSED` only when `quote_through_pause` is False. The worker passes `self._strategy.quote_sides.quote_through_pause`. Dota/LoL pass False through `FOLLOW300_SIDES`, so their output does not change. This is what lets the round signal serve a pause.

D5. **BUY window in `LiveCore`.** `LiveCore.__init__` sets `self.buys_open = True` next to `sidecar_usable` (:281). `run_core_cycle` passes `sidecar_usable=core.sidecar_usable and core.buys_open` (:970). `SignalSource` gets a read-only `buys_open: bool`; `Follow300Signals` returns True. `_adopt_core` (`match_worker.py:1165`) sets `core.buys_open = self._signals.buys_open`. New worker methods:
  - `open_round_buys()`: if no core, return. Set `core.buys_open = True`, wake the cid.
  - `close_round_buys()`: if no core, return. Set `core.buys_open = False`, call `_schedule_entry_cancel()`, wake the cid.
  `build_signals` gets two more required callables, `open_buys` and `close_buys`. `Follow300Strategy.build_signals` ignores them. `WalletStateStore.block_buy` stays out (it means on-chain excess).

D6. **`RoundSignals` in a new file `src/trader/round_signals.py`.** Constructor `RoundSignals(models: Cs2RoundModels, requote, open_buys, close_buys)`. State: pending due, a ripe flag, the due timer handle, the call future, the finished call result, the current `RoundSignal`, the live round number, a map-closed flag, a stopped flag, the heartbeat handle, `buys_open = False`.
  - `on_tick(RoundDecisionDue)`: keep the due. Arm `loop.call_later(max(0.0, (due.due_ns - time.time_ns()) / NS_PER_SECOND), self._on_due_timer)`. `_on_due_timer` marks the due ripe and calls `requote()`. `due_ns` is wall clock from the board `occurredAt`, so the delay uses `time.time_ns()`.
  - `on_tick(RoundLiveTick)`: set the live round. Drop a due whose round is at most that number and cancel its timer. Close buys.
  - `on_tick(RoundHistoryLost)`: close the map (drop the due, close buys, stop the heartbeat). The current signal stays and is re-issued on each FeedEvent; only the 1 Hz heartbeat stops, so a socket fault is not hidden.
  - `on_tick(FeedEvent)` with `snapshot.finished`: close the map.
  - `check_history` returns True. The reducer owns history completeness.
  - `on_feed_event`, step 1 (start a call): when a due is ripe and no call runs:
    - reason `STALE` or `FINISHED`: drop the due.
    - `market_p_radiant is None` (book or sidecar reasons): this is the NaN-m0 case. Drop the due, set the current signal to None, keep buys closed, log.
    - otherwise: read `m0 = market_p_radiant` and `call_ns = now_ns` (core clock), take `prior` or NaN, and submit `build_round_frame` + `predict_round_call` to a module-level `ThreadPoolExecutor(max_workers=1)` through `loop.run_in_executor`. The done callback stores the result and calls `requote()`. It does nothing when stopped. An exception logs and drops the call like NaN m0.
  - `on_feed_event`, step 2 (publish a finished call): publish only if the map is not closed and the live round is below the call round. Else drop it ("устаревший callback ... не открывает покупки"). Publish = new `RoundSignal(predicted_delta=pred_delta, received_ns=now_ns, source_received_ns=now_ns, anchor_p=m0, deaths_radiant=0, deaths_dire=0, up_radiant=up_a, up_dire=up_b, call_ns=call_ns)`, then open buys, then start the heartbeat (`call_later(1.0)` → `requote()` and re-arm).
  - `on_feed_event`, step 3 (return): no current signal, or reason `STALE`/`FINISHED` → `SignalOutcome(reason or SignalReason.ROUND_PENDING, None, None)`. Else re-issue `replace(signal, received_ns=now_ns, source_received_ns=now_ns)` with `ModelFair(radiant, yes_fair_from_model(radiant, yes_is_radiant), pred_delta)`, where `radiant` is `anchor_p + predicted_delta` clipped to [0, 1], and reason `reason or SignalReason.MODEL`. Other gate reasons (MISSING_BOOK, OWN_LIQUIDITY_ONLY, …) still re-issue: the anchor is pinned and the backtest re-emits on every stamp regardless of the book.
  - `stop()`: set stopped; cancel the due timer, the heartbeat and the call future.
  - One `logger.info` per call: round, due_ns, m0, prior, model_delta, pred_delta, up_a, up_b. One per dropped call with the cause.

D7. **`RoundStrategy` in a new file `src/trader/round_strategy.py`.** Frozen dataclass `RoundStrategy(profile_name, round_dir, outcome_dir, max_position_levels)`. `quote_sides` → `ROUND_SIDES`. `primary_profile_name` → `profile_name`. `satellite_profile_names` → empty. `trace_model_dirs` → `(round_dir, outcome_dir)`. `profile_name_for(feed_source)` → `profile_name`. `load_models()` → `{profile_name: load_cs2_round_models(round_dir, outcome_dir)}`. `build_signals(...)` asserts `isinstance(model, Cs2RoundModels)` and returns `RoundSignals`. No `game == "cs2"` branch in the shared worker.

D8. **Live constants.** `CS2_MIN_ABS_DELTA = CS2_EXIT_ABS_DELTA = 0.01` (LIVE manifest). Delete the stale "0.75c" comment at `strategy.py:14`. `LIVE_CS2_MAX_POSITION_LEVELS = 6` and `LIVE_MAX_POSITION_LEVELS["cs2"]`. `CS2_BUY_CUTOFF_SECOND = 10**9` and `BUY_CUTOFF_SECOND_BY_GAME["cs2"] = CS2_BUY_CUTOFF_SECOND`. The map end stops entries through `game_end`. The CS2 backtest does not read the table (per-map cutoffs, D1 facts).

D9. **`GAME_PROFILES["cs2"]`** = `GameProfile(game="cs2", archive_root_env="CS2_ARCHIVE_ROOT", mode_env="CS2_TRADING_MODE", uses_steam=False, aliases=MappingProxyType(CS2_TEAM_ALIASES), league_whitelist_path=CS2_LEAGUE_WHITELIST_PATH, feed=Cs2GridFeed(), strategy=RoundStrategy("cs2-map", CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR, LIVE_MAX_POSITION_LEVELS["cs2"]))`. Update the module docstring (`game_profile.py:1`).

D10. **`SignalReason.ROUND_PENDING = "round_pending"`** (`src/trader/session_types.py:21-40`). The journal label for "no round call yet". `dashboard/diagnostics.py` falls back to the raw string (:247); a label is STEP-011 work.

D11. **Config.** `config/trading.toml`: `[profiles.cs2-map]` with the same 15 keys and values as `[profiles.lol-map]` and `base_size_usdc = 5.0`; `[clips.cs2]` with `default = 5.0`, `tiers = []`, `events = []`. `compose.yaml` live: `CS2_ARCHIVE_ROOT: /archive/cs2`, `CS2_TRADING_MODE: ${CS2_TRADING_MODE}`, volumes `/var/lib/polymarket-cs2-archive:/archive/cs2:ro` and `./data/cs2/models:/app/data/cs2/models:ro`. Dashboard: `CS2_ARCHIVE_ROOT: /archive/cs2` and the archive mount (`live_hub._sidecar_roots` reads every profile's root, `src/dashboard/live_hub.py:139-147`). Local `.env`: append `CS2_TRADING_MODE=off` without printing the file.

D12. **Whitelist.** `config/cs2_league_whitelist.json`: add league `"PGL Masters Bucharest"` and aliases `"PGL Masters"`, `"PGL Masters Bucharest Group Stage"`, `"PGL Masters Bucharest Playoffs"` → `"PGL Masters Bucharest"`. `read_event_league` tries `metadata.league`, then `eventMetadata.league`, then the title suffix (`src/trader/league_filter.py`). Gamma gives `"PGL Masters"`, so the alias is the one that matches. No series lookup by team names when `gridSeriesId` is None.

D13. **`archive_index` keeps to games with a universe.** `universe.py` gets `UNIVERSE_GAMES = frozenset({"dota", "lol"})`. `index.py:164` and `:653` use it. A CS2 archive gets identity `unsupported_game` and no schedule.

D14. **Deviation from PLAN.md:217.** PLAN.md says `call_ns = due·1e9`. This plan uses the core clock at the moment m0 is read, because `earlier_round_sells` compares `call_ns` with core-clock `placed_ns`. A wall-clock `call_ns` would mark every SELL as "earlier".

## 3. File-by-file changes

### 3.1 `src/shared/constants/strategy.py`
- `CS2_BUY_CUTOFF_SECOND = 10**9`; `BUY_CUTOFF_SECOND_BY_GAME["cs2"]` uses it.
- `CS2_MIN_ABS_DELTA = 0.01`, `CS2_EXIT_ABS_DELTA = 0.01`. Delete the comment at :14.
- `LIVE_CS2_MAX_POSITION_LEVELS = 6` and the `"cs2"` key in `LIVE_MAX_POSITION_LEVELS`.
- `CS2_FEED_STALE_SECONDS = 600.0` next to `GRID_FEED_STALE_SECONDS`. No new comment.

### 3.2 `src/trader/grid_feed_spec.py`, `src/trader/grid_feed.py`, `src/trader/cs2_feed.py`
- `WidgetReducer`: add `board` (read-only property) and `read_pinned_horn_iso`.
- `GridFeedSpec`: add read-only `stale_seconds`.
- `MobaGridFeed` (`grid_feed.py:373`): `stale_seconds` property → `GRID_FEED_STALE_SECONDS`.
- `Cs2GridFeed`: `stale_seconds` property → `CS2_FEED_STALE_SECONDS`. `Cs2FrameReducer.read_pinned_horn_iso` as D3.

### 3.3 `src/trader/grid_live_feed.py`, `src/trader/feed_selection.py`
- `GridLiveFeed.__init__(series_id, services, match_id, reducer, buy_cutoff_second, stale_seconds)`; delete the class attribute at :46 and the unused import.
- `_build_feed` (`feed_selection.py:100-110`) passes `profile.feed.stale_seconds`.

### 3.4 `src/trader/session_quoting.py`, `src/trader/session_types.py`
- `window_reason(snapshot: GameSnapshot, fresh: bool, quote_through_pause: bool)`: drop the `fresh` default; `PAUSED` only when not `quote_through_pause`.
- `SignalReason.ROUND_PENDING`.

### 3.5 `src/trader/session_core.py`
- `LiveCore.__init__`: `self.buys_open = True`.
- `run_core_cycle`: `sidecar_usable=core.sidecar_usable and core.buys_open`.

### 3.6 `src/trader/strategy_spec.py`, `src/trader/follow300_strategy.py`, `src/trader/follow300_signals.py`
- `SignalSource`: read-only `buys_open: bool`.
- `StrategySpec.build_signals(*, model, feed_source, enqueue_core, requote, open_buys, close_buys)`; both new args are `Callable[[], None]`.
- `Follow300Strategy.build_signals`: accept and drop (`del open_buys, close_buys`).
- `Follow300Signals.buys_open` → True.

### 3.7 New `src/trader/round_signals.py` and `src/trader/round_strategy.py`
As D6 and D7. Function names use action verbs (`_on_due_timer`, `_start_call`, `_publish_call`, `_close_map`, `_beat`). The call result is a frozen dataclass (round number, `call_ns`, `RoundCallPrediction`), not a tuple. The executor is one module-level `ThreadPoolExecutor(max_workers=1, thread_name_prefix="cs2-call")`. No comments in new code.

### 3.8 `src/trader/match_worker.py`
- :187 exit-timer default as D2.
- :190-195 pass `open_buys=self.open_round_buys, close_buys=self.close_round_buys`.
- :432 `window_reason(event.snapshot, fresh, self._strategy.quote_sides.quote_through_pause)`.
- `_adopt_core`: `core.buys_open = self._signals.buys_open`.
- New `open_round_buys` and `close_round_buys` next to `_on_entry_feed_timeout` (D5).

### 3.9 `src/trader/game_profile.py`
- `feed: GridFeedSpec`. Add the cs2 profile (D9). Imports: `Cs2GridFeed`, `RoundStrategy`, `CS2_TEAM_ALIASES`, `CS2_LEAGUE_WHITELIST_PATH`, `CS2_ROUND_MODEL_DIR`, `CS2_OUTCOME_MODEL_DIR`. Check for an import cycle (`round_signals` → `cs2_rounds` → `cs2.*`; `game_profile` must not be imported by any of them).

### 3.10 Dashboard narrowing (D3)
`src/dashboard/game_state.py`, `src/dashboard/match_state.py`, `src/dashboard/game_grid.py`: `assert isinstance(profile.feed, MobaGridFeed)` before the MOBA field reads. Same in any test that reads `GAME_PROFILES[...].feed.side_0_text` or `level_xp` (`tests/test_trader_game_profile.py:63-66, 95-98, 145-155`).

### 3.11 `src/archive_index/universe.py`, `src/archive_index/index.py`
`UNIVERSE_GAMES` and its two uses (D13).

### 3.12 Config files
- `config/trading.toml`: `[profiles.cs2-map]` after `[profiles.lol-map]`; `[clips.cs2]` after `[clips.lol]`.
- `config/cs2_league_whitelist.json` (D12).
- `compose.yaml` (D11).
- Local `.env` (gitignored, `.gitignore:28`): `grep -q '^CS2_TRADING_MODE=' .env || printf '\nCS2_TRADING_MODE=off\n' >> .env`. Do not print `.env`.

## 4. Tests

### 4.1 New `tests/test_trader_round_signals.py`
Unit cases drive `RoundSignals` under `asyncio.run` with recorder callables for `requote`, `open_buys`, `close_buys`. Most cases monkeypatch `trader.round_signals.build_round_frame` and `predict_round_call` with a fake that returns a fixed `RoundCallPrediction`. One case uses real tiny models (`_fit_tiny_member` from `tests/test_cs2_round_model.py`, the import pattern of `tests/test_cs2_rounds.py:11`).
1. Due then FeedEvent: the timer fires at `due_ns`, `requote` runs, the call result publishes a `RoundSignal` with `anchor_p = m0`, `call_ns` = the core clock at the call, `up_radiant/up_dire = up_a/up_b`; `open_buys` runs once; `buys_open` is True.
2. Due/live-start race: `RoundLiveTick(N)` before the timer → no call, `close_buys` runs, no `open_buys`.
3. Call-thread race: `RoundLiveTick(N)` while the call runs → the result is dropped; the previous signal stays; no `open_buys`.
4. History lost (`FEED_GAP`, `SCORE_JUMP`): pending due dropped, buys closed, heartbeat stopped, later dues ignored; the old signal is still re-issued on a FeedEvent.
5. NaN m0 (`market_p_radiant=None`, reason `MISSING_BOOK` at the ripe due): no call submitted, signal None, buys stay closed.
6. STALE at the ripe due: due dropped, outcome has no fair.
7. Pause: a paused FeedEvent with reason None re-issues the signal with the same `anchor_p` and `call_ns` and a new `source_received_ns`.
8. Heartbeat: after a publish, `requote` runs about once per second with no FeedEvent.
9. Missing prior: `prior=None` still calls (prior NaN), reason is not `MISSING_PRIOR`.
10. `stop()` and terminal: after a finished FeedEvent or `stop()`, no timer, heartbeat or call callback runs (advance the loop past the due and 2 s).
11. A model exception in the call: logged, dropped, signal None.
12. One real tiny-model call through the executor gives a finite `pred_delta`.

### 4.2 Worker and core integration (new tests in `tests/test_trader_round_signals.py`, reusing `tests/trader_session_fixtures.py`)
`build_attached_worker(..., game="cs2", model=<tiny Cs2RoundModels>)` with the real `GAME_PROFILES["cs2"]`, `FakeEngine`, `FakeWalletHost`, a `WalletStateStore`, and the `match_worker.core_now_ns` patch from `tests/test_board_signal_contract.py`. No venue calls.
1. Before any call the core has `buys_open` False: `run_core_cycle` → `preview_block_reason() == "fair"` and the journal row `entry_block == "no_edge"`, reason `round_pending`.
2. After a publish: `buys_open` True, the trace has a `PermissionsUpdate` with `allow_buy=True` and a `SignalUpdate` with the `RoundSignal`.
3. `close_round_buys` with a resting BUY: `cancel_entry_buys` targets the BUY, SELLs stay, the next cycle records `allow_buy=False`, and no new BUY is planned.
4. With a held position and buys closed, the core still plans the round-lot SELL.
5. Trace round trip: write the cycle trace and replay it with `replay_trace(path, use_current_policy=False)` (`src/trader/replay_core_trace.py:504`) through the real `GAME_PROFILES["cs2"].strategy.quote_sides`; the replay matches open and close with no new inbound event.

### 4.3 Updated tests
- `tests/test_trader_game_profile.py`: profile set {dota, lol, cs2} (:52, :161); cs2 fields (env names, `uses_steam` False, aliases, whitelist path, `Cs2GridFeed`, `RoundStrategy` with `ROUND_SIDES`, 6 levels, `trace_model_dirs`); `follow300_policy(game="cs2", ...)` gives 3 levels, min entry 0.45, max entry 0.85, abs delta 0.01 (the LIVE manifest numbers pinned in the test); MOBA reads narrowed.
- `tests/test_backtest.py:569-570`: CS2 delta 0.01.
- `tests/test_trader_trading_mode.py`: add `CS2_TRADING_MODE` to every env dict; one case for missing `CS2_TRADING_MODE` → `TradingDisabled`; one for `live` assigning cs2.
- `tests/test_trader_session_config.py`: cs2 in the template tests (`read_template` for cs2 gives `{"cs2-map"}` with base 5.0 and clip default 5.0, no tiers, no events).
- `tests/test_trader_compose.py`: `SHARED_ENV_KEYS` gets `CS2_TRADING_MODE` and `CS2_ARCHIVE_ROOT`; mount asserts for `/archive/cs2:ro` and `./data/cs2/models:/app/data/cs2/models:ro` (read_only in the config test); dashboard has `CS2_ARCHIVE_ROOT`.
- `tests/test_trader_host_resources.py`: one cs2 host case with `trader.round_strategy.load_cs2_round_models` patched, as the file patches `trader.follow300_strategy.load_model`.
- `tests/test_trader_wallet_host.py:1626`: use a game name that is not a profile (for example `"valorant"`).
- `tests/test_core_trace_round.py`: drop the cs2 monkeypatch; use the real profile.
- `tests/test_trader_session_quoting.py:119, 126, 157, 173, 250`: pass `fresh` and `quote_through_pause`; add a case where a paused snapshot gives None with `quote_through_pause=True`.
- `tests/test_cs2_feed.py:685`, `tests/test_grid_feed.py:71, 545`, `tests/test_trader_match_lifecycle.py:281`: pass `stale_seconds` to `GridLiveFeed`. Add a `read_pinned_horn_iso` case (locked map → ISO of the lock stamp; late join → None).
- New case: `grid_archive.summarize` on a synthetic CS2 archive (frames from `tests/cs2_grid_widget_fixtures.py`) gives a summary with winner None and the pinned horn.
- New case in `tests/test_archive_index.py`: a CS2 meta gets identity `unsupported_game` and `build_index` does not call `load_universe("cs2")`.
- New case in `tests/test_trader_lol_discovery.py` style (or a new `tests/test_trader_cs2_discovery.py`): a CS2 sidecar with `gridSeriesId=None` gives no match; one with a series id and a CS2 board gives `game == "cs2"`.

Do not grow `tests/test_trader_session_core.py` (2597 lines).

## 5. Verification

Run from `../esports-trader`. Never run two `-n` suites at once. Do not run the suite during a live map.

1. `.venv/bin/ruff check src tests scripts`
2. `.venv/bin/ruff format --check src tests scripts`
3. `.venv/bin/basedpyright`
4. Targeted: `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 tests/test_trader_round_signals.py tests/test_trader_game_profile.py tests/test_trader_trading_mode.py tests/test_trader_session_config.py tests/test_trader_compose.py tests/test_trader_host_resources.py tests/test_trader_wallet_host.py tests/test_core_trace_round.py tests/test_trader_session_quoting.py tests/test_cs2_feed.py tests/test_grid_feed.py tests/test_trader_match_lifecycle.py tests/test_archive_index.py tests/test_trader_lol_discovery.py tests/test_backtest.py tests/test_board_signal_contract.py`
5. Full: `PYTEST_N=8 make test`. Known flakes: `test_upcoming_rows_from_sidecars` fails 23:00–00:00 Berlin; `dota_9007700576` flakes under xdist.
6. Dota/LoL gates from STEP-005 (betting_workspace `tasks/cs2-live/orchestrate/work/step-005/`): GRID differential `grid_reducer_diff.py` (24427 lines, sha `df2fb0a8…784c5e`), signal differential `signals_diff.py` (27514 lines, sha `7b70dde6…c3445e`), core replay `run_core_replay.sh` on `core_sessions.txt` (13 sessions, 1102495 events). All three must equal the baseline. The model smoke `model_smoke.py` reads `server._features` and breaks on cs2; copy it to `work/step-009/` and skip `Cs2RoundModels`.
7. CS2 identity: `GAME_PROFILES["cs2"].strategy.load_models()` loads, and `model_file_sha(GAME_PROFILES["cs2"].strategy.trace_model_dirs)` equals the value in `work/plan-s9/model-identity-base.txt`.
8. Offline round predictions sha (`work/plan-s8/round_preds_sha.py`) stays `8d6f38cd…4d8891`.
9. Whitelist: old and new whitelist select the same count on the universe titles (`parse_event_league(None, title)` + `is_allowed_league`).
10. Synthetic host check without execution: build a paper wallet host for cs2 with the real models and one synthetic market (the `test_trader_host_resources.py` harness), check `RoundSignals` is built and both model shas match. No order leaves the process.
11. `CS2_TRADING_MODE` in `.env`: `grep -c '^CS2_TRADING_MODE=off$' .env` gives 1. Do not print the file.
12. Import smoke: `PYTHONPATH=src .venv/bin/python -c "import trader.game_profile, trader.round_signals, trader.round_strategy, archive_index.index, dashboard.live_hub"`. Backtest import grep: `grep -rn "trader.round_signals\|trader.round_strategy" src/backtest` is empty.
13. Append `## STEP-009` to `betting_workspace/investigations/2026-10-23-cs2-live-validation/report.md` (append only). Add a `progress.txt` entry. One commit in `../esports-trader`.

All data for these checks is on this machine (`data/cs2/models/round`, `data/cs2/models/round_outcome`, `data/cs2/universe/events.parquet`, the tape, the STEP-005 inputs). No VPS fetch is needed.

## 6. Edge cases, risks, hand-offs

- **GRID idle close (top risk, STEP-012 owns the fix).** 73 of 78 tape closes follow exactly 120 s of silence, one inside map 3 during the R2 pause. A clean close ends `GridLiveFeed.ticks` (`grid_live_feed.py:129`), the worker quiesces, and the restart sees a late join → no buys for the rest of the map. An abnormal close gives `FEED_GAP` → no buys for the rest of the map. STEP-009 cannot fix this without a feed change.
- **PGL events have no `gridSeriesId`** (8 of 8 at 04:07Z). Discovery gives no source and no trading until Gamma fills it.
- **Alias breadth.** `"PGL Masters"` admits any PGL Masters serie, not only Bucharest.
- **Team names.** `normalize_team_name` (`src/shared/utils/team_names.py:69`) splits "The MongolZ" into two tokens and keeps "TheMongolz" as one. `CS2_TEAM_ALIASES` coverage for the 16 PGL teams is not checked. Orientation fails loud (`GridOrientationError`), so the map is skipped, not mis-traded. STEP-012 checks against real titles.
- **Whitelist sha.** Older CS2 report dirs fail resume (`src/backtest/run.py:1938`, `:2217`). New runs are fine.
- **Heartbeat after a history loss.** The heartbeat stops; the signal is re-issued only on FeedEvents. After 45 s of board silence the core exit fair is stale and the SELL moves to the ask.
- **Prior on R1.** `read_book_prior` runs in a thread from the first event. The R1 due is 2 s after the first freeze (`FREEZE_DUE_SECONDS`, `cs2_feed.py:51`). I did not measure the read time. A read that ends after the R1 call gives NaN prior on R1; the model accepts NaN.
- **LightGBM concurrency.** One executor worker serializes calls across maps. Two maps with dues in the same second wait about 143 ms each.
- **`past_cutoff` for CS2.** With `10**9`, a 4xx after map end is never "feed gone"; the feed retries up to `MAX_CONSECUTIVE_FAILURES` and stops. The map end still comes from the board (`FINISHED` or feed gone).
- **Dashboard asserts.** Unreachable until a CS2 trader archive exists (STEP-012). STEP-011 replaces them.
- **Journal volume.** The heartbeat writes one decision row per second through `_quote_pm`, the same rate as a Dota feed.

## 7. Out of scope

- The replay CLI and tape acceptance (STEP-010).
- Dashboard CS2 labels, slate and CLI filter (STEP-011).
- VPS rollout, live mode, the idle-close fix and real-title checks (STEP-012).
- No change to `../poly-maker`, the backtest, or model training.
