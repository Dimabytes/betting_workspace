# diff-grok — LoL vs Dota pipeline differences, and the LoL live path

Date: 2026-09-23. Read-only. Code and backtests under `esports-trader`. Numbers below are from seed 0 of `data/backtests/{lol,dota}_maker/LIVE` → `validation_join_delta02_x015_cut480_p35_s06-playback-gf`, unless noted.

## TL;DR

1. **LoL training and the grid-v1 backtest pair game state with the book at the same wall time. Dota pairs state at second S with the book at S+10. Live LoL does not see them together:** local tapes declare `grid_delay_s = 8` on 302/305 LoL maps, and the Polymarket book is the current CLOB. The 2026-09-19 change that dropped the LoL +10 s join (`332e1c17`) made the backtest more contemporaneous than live. **verified, high.**
2. **That contemporaneous path is 782 of 945 LoL maps and $4684 of $5171 pre-rebate.** Buy markout at 30 s is **+0.619¢** there and **+0.086¢** on the 163 archive-schedule maps. Dota grid-v1 (lag 10) is **−0.454¢**. The red-flag positive short markout is the grid-v1 join, not the schedule replay. **verified, high.**
3. **`source_lag_seconds` is still 10** in LoL `model.json`, the prepare audit, and the LoL backtest manifest, while the join and `RunSelection.lag_seconds` are 0. The manifest prints the contract, not the join. **verified, medium.**
4. **LoL trains every second (0..540); Dota trains minute rows.** Kept on purpose after an offline minute-train loss. A 1 Hz model can fit the short move that a contemporaneous fill then collects. **likely, medium.**
5. **Live features are GRID `NetWorth` and a level→XP staircase. Training net worth is `totalGold` minus consumed items.** Top-1 ratios match within each game. The residual between reconstructed gold and GRID `NetWorth` was not measured here. **speculative, medium.**
6. **Same Follow300 knobs on both games** (`min_abs_delta` 0.02, `exit_abs_delta` 0.015, cutoff 480, entry 0.35..0.85). Clips differ (live LoL $20, Dota $60, Oddin $100; backtest both 3×$100). Clip scales dollars. It does not flip the 30 s markout sign. **verified, low for the sign.**
7. **LoL live path (frame → sides → features → fair → YES) is internally consistent** with the LoL training formulas for side, XP, and top-1 ratio. The mismatch is the clock the book is read on, not a swapped side in the current reducer.

## Findings

### DG-1 — LoL join is lag 0; Dota and live GRID are a delayed state vs a current book

- **Stage:** prepare, backtest, live
- **Claim:** After `332e1c17` (2026-09-19), LoL labels use the book at the livestats frame wall time. Dota still uses the book 10 s later. Live LoL features come from a table delayed about 8 s, while `market_p_radiant` is the book at decision time.
- **Evidence:**
  - LoL current mid and label, lag argument `0` and horizon `300`: `src/lol/05_prepare_dataset.py:310` and `:314-322`. `state_wall_us` is the chosen frame's wall time, not the theoretical second boundary (`src/lol/livestats_frames.py:649-654`).
  - The parent commit replaced `LOL_SOURCE_LAG_SECONDS` (10) on both the current mid and the label (`git show 332e1c17`). Commit text: "The live quote already sits on the GRID clock; lagging the training mid by 10s made second 0 read T+10 instead of the spawn print." Retrain: `c9fb5cd2` the same day. Production `data/lol/models/production/model.json` name `20260921T095813Z` still records `"source_lag_seconds": 10`.
  - Dota minute rows: `market_by_second.get(state.second + lag_seconds)` with `TRAIN_LAG_SECONDS = 10` (`src/prepare_dataset/prepare_dataset.py:154`, `src/shared/constants/dataset.py:19-22`). Validation join uses `market_second - lag_seconds` (`prepare_dataset.py:208`). Lag-grid experiment kept 10/10 (`docs/experiments/lag-grid.md`).
  - Backtest selection hardcodes LoL `lag_seconds=0` and Dota `BACKTEST_LAG_SECONDS` (`src/backtest/run.py:1405` and `:1425`). Grid-v1 then does `second = second - lag_seconds` (`src/backtest/signals.py:271`), so LoL's `second` feature is unshifted and Dota's is the state second, 10 s behind the market row. Signal time is that row's `state_ts_us` (`signals.py:260`), which for LoL is the frame wall (`05_prepare_dataset.py:281`).
  - Live snapshot clock: `second = live_clock_seconds(board, age) - table.feed_delay` (`src/trader/grid_feed.py:171-172`). The book is read later, from the CLOB, in `MatchWorker._gate_pair` (`src/trader/match_worker.py:388-404`). Local tapes: `grid_delay_s` is `8.0` on 302 of 305 `data/trader/grid-*/match.json` with `game=lol` (3 missing). Command: a `Path("data/trader").glob("grid-*/match.json")` count on 2026-09-23.
  - GRID measurement (`docs/experiments/lol-grid-widget.md`): scoreboard ~7 s behind wall, table declared 8 s, livestats availability ~55 s. The frame timestamp used as `state_wall_us` is the event stamp inside the archive, not the fetch time. Joining the book at that stamp is "as if the trader saw gold at the event." Live sees it about one table-delay later.
- **Mechanism:** Grid-v1 places the maker order on the book at the same instant as the feature frame. Gold and deaths in the frame are not in that book yet, so the next 30 s of mid move with the buy (+0.62¢). Live, the table is ~8 s old and the book has already moved. Dota's +10 s join was built for that shape (STRATZ/Steam snapshot age). The commit treated the GRID game clock and the CLOB as one clock. The reducer delays `second`; it does not delay the book.
- **Deliberate?** The lag-0 edit was deliberate. The premise does not match `grid_feed.py` plus the CLOB read. Dota 10/10 is deliberate (`docs/experiments/lag-grid.md`).
- **Severity:** high. **Confidence:** verified.
- **Next check:** Retrain and replay with the current mid taken 8 s (declared delay) or 10 s after `state_wall_us`, label 300 s after that mid. Compare buy markout 30 s on the same 945 maps. No code was changed here.

### DG-2 — Positive 30 s markout and most of the LoL dollars sit on grid-v1, not on archive schedules

- **Stage:** backtest
- **Claim:** The LIVE catalog's +0.51¢ buy markout at 30 s is the 782-map grid-v1 arm. The 163 maps that replay an admitted GRID schedule (market anchored at tick receipt, features at `tick.game_second`) are near flat at 30 s and are about 9% of pre-rebate PnL.
- **Evidence:** `work/diff-grok/split_markout.py` on seed 0 `results.parquet` + `fills.parquet`. Quantity-weighted buy markout.

  | game | arm | maps | engine PnL | buy fills | buy qty | markout 30 s | markout 300 s |
  |---|---|---:|---:|---:|---:|---:|---:|
  | LoL | grid_v1 | 782 | $4684.19 | 4424 | 294474 | **+0.619¢** | +2.979¢ |
  | LoL | schedule:grid | 163 | $486.88 | 1335 | 73088 | **+0.086¢** | +2.205¢ |
  | LoL | all | 945 | $5171.07 | | | +0.513¢ | +2.825¢ |
  | Dota | grid_v1 | 456 | $1606.58 | 1551 | 124604 | **−0.454¢** | +1.047¢ |
  | Dota | schedule:grid | 126 | $460.43 | 277 | 20289 | +0.059¢ | +1.967¢ |
  | Dota | schedule:oddin | 1 | $53.44 | 8 | 741 | −0.526¢ | +15.453¢ |

  Summary groups match: LoL `signal_groups` grid_v1 782, schedule:grid 163 (`seed0/summary.json`). Manifest `signal_source=auto`, `source_lag_seconds=10` (the printed contract; selection lag for LoL is 0). Schedule decisions set `second = game_second` and `market_p_radiant` from the book at `tick.received_ns` (`src/backtest/signals.py:339-350` and `:396-397`). They do not subtract lag. Tick `game_second` is `snapshot.second` (`src/archive_index/schedule.py:176`), which is the delay-adjusted GRID clock.
- **Mechanism:** Schedule replay is the closer live analogue: stale game second, book at arrival. Its 30 s buy markout collapses toward zero (still +0.09¢, not Dota's −0.45¢). Grid-v1 is the optimistic analogue and is 83% of LoL maps. Dota grid-v1 is the lag-10 join, so the same sampler is adversely selected at 30 s, which is the usual maker shape. Both games still show positive 300 s buy markout; the live-vs-backtest argument is about the short horizon the queue fill can harvest before the book moves.
- **Deliberate?** Archive schedules are deliberate (`feed_schedule_rules_version=feed-schedule-v3`). Leaving maps with no admitted archive on grid-v1 is deliberate (`src/backtest/feed_schedules.py` module doc). The lag-0 grid-v1 join on those maps is the accidental optimism.
- **Severity:** high. **Confidence:** verified.
- **Next check:** Report markout and PnL by `signal_mode` on seeds 1–2 before treating seed 0 as the whole catalog. A live-faithful number for "what the model does when the book is 8 s newer than the state" is the schedule arm, not the headline $6403.

### DG-3 — `source_lag_seconds = 10` is no longer the LoL join

- **Stage:** prepare, train, backtest, live
- **Claim:** The field still says 10. The join and the LoL backtest shift are 0. Live only checks that the field equals `TRAIN_LAG_SECONDS` (10). It does not shift the book.
- **Evidence:**
  - Constant `LOL_SOURCE_LAG_SECONDS = 10` (`src/shared/constants/lol.py:50`). Written onto every audit row (`src/lol/05_prepare_dataset.py:446`) and into `model.json` (`src/lol/06_train_model.py:104`).
  - Backtest refuses a model whose lag differs from that constant (`src/backtest/lol_inputs.py:65-78`). So the audit and the model agree with each other and disagree with `join_market_rows`.
  - LoL seed 0 manifest key is `source_lag_seconds: 10` only. Dota seed 0 has `train_lag_seconds: 10` and `backtest_lag_seconds: 10` (`run.py:752-764` vs the LoL branch that stores the model field).
  - Live loader: `if source_lag != TRAIN_LAG_SECONDS: raise` (`src/trader/model_server.py:266-267`). `TRAIN_LAG_SECONDS` is 10. A LoL model trained at lag 0 still loads.
- **Mechanism:** Anyone comparing manifests sees "both games lag 10." Only Dota applies it. That hides DG-1.
- **Deliberate?** The constant was left in place when the join argument changed. The commit did not update the field. Accidental.
- **Severity:** medium (it misleads the next run; it does not by itself move a quote). **Confidence:** verified.
- **Next check:** If the join stays at 0, write 0 into `model.json` and stop equating it with `TRAIN_LAG_SECONDS`. If live is supposed to match an 8–10 s table delay, put that delay back into the join and the field together.

### DG-4 — Training cadence: LoL 1 Hz, Dota one row a minute

- **Stage:** prepare, train
- **Claim:** LoL keeps a labeled row at every game second from 0 through 540. Dota training rows are `range(-60, 600, 60)`.
- **Evidence:** `slice_model_rows` (`src/lol/06_train_model.py:75-81`), `LOL_TRAIN_GRID_SECONDS = 1` (`src/lol/constants.py:148`), `LOL_GRID_END_SECOND = 540`. Dota `build_minute_states` steps 60 (`src/prepare_dataset/stratz_seconds.py:208`). Exact-second Dota states exist for playback features (`build_exact_second_states`) but are not the training rows. `docs/experiments/lol-minute-train.md` (2026-08-28): minute filter lost on offline MAE/dir (1 Hz mae_gain_300 0.115¢ vs 0.108¢); no backtest; 1 Hz kept. Train size in that note: 1,328,090 rows vs 24,593.
- **Mechanism:** Adjacent seconds are near-duplicates, so leaf counts overstate sample size. The booster can fit a move that shows up inside 30 s. Grid-v1 then trades that move at the feature timestamp (DG-1). Minute rows cannot resolve it. The minute experiment did not rerun the maker backtest, so the PnL link is inference.
- **Deliberate?** Yes. Verdict in the experiment doc was to drop the minute filter.
- **Severity:** medium. **Confidence:** likely.
- **Next check:** Only after a lag-matched retrain. A 1 Hz model on a lag-0 join and a minute model on a lag-0 join are different questions.

### DG-5 — Feature source: livestats reconstruction vs GRID table, and LoL has no second feed

- **Stage:** prepare, live
- **Claim:** The 12 feature names match (`FEATURE_COLUMNS`). The values do not come from the same instrument. LoL live is GRID only. Dota live is Steam, GRID, or Oddin, and Oddin uses a no-XP catalog.
- **Evidence:**
  - Train net worth: per-player `gold - consumed` (`src/lol/livestats_frames.py:436-448`), `src/lol/networth.py` header. Live net worth: GRID `NetWorth` summed by team (`src/trader/grid_widgets.py:238` and `:301-303`). `state_source` in model.json is `lolesports_window_details_grid_networth`, i.e. the reconstruction is meant to imitate GRID. `scripts/compare_lol_grid_livestats.py` compares raw `totalGold`, not consumed-item net worth. This pass did not run it.
  - XP: both LoL train and LoL live use `xp_advantage(LOL_LEVEL_XP, levels)` (`livestats_frames.py:455-458`, `grid_feed.py:180`). Live level is `increaseLevel + 1` (`grid_widgets.py:236-237`). GRID also sends raw `ExperiencePoints` (`docs/experiments/lol-grid-widget.md`); the trader ignores it. `xp_source=level` matches that. Dota train and Dota Steam also use the level staircase (`src/shared/utils/dota_levels.py:26-36`, `src/trader/steam_feed.py:124`), not a separate XP feed.
  - Top-1 ratio: LoL train is `max / team_total` (`livestats_frames.py:463-464`). LoL live calls `build_top_player_features_over_total` (`grid_feed.py:167-168`, `src/shared/utils/top_players.py:63-72`). Dota train and Dota GRID/Steam use `build_top_player_features`, which is `max / (sum - max)` (`stratz_seconds.py:196`, `grid_feed.py:170`, `steam_feed.py:114`). Consistent inside each game. Deliberate split.
  - Profiles: LoL `uses_steam=False`, satellites empty. Dota `uses_steam=True` plus an Oddin no-XP catalog (`src/trader/game_profile.py:41-71`).
- **Mechanism:** If reconstructed net worth drifts from GRID `NetWorth`, every live vector is off the training distribution even when the clock is right. That would hit live and the schedule arm (schedule still looks up livestats `game_features`, not the archived table numbers) and would not by itself create a positive 30 s markout on grid-v1, because grid-v1 trains and fills on the same reconstruction.
- **Deliberate?** Level XP and the LoL ratio are deliberate. Whether the reconstruction matches GRID is an unclosed measurement (`lol-grid-widget.md` is a measurement note, not a parity test).
- **Severity:** medium. **Confidence:** speculative on magnitude; the code split is verified.
- **Next check:** On `data/lol_dual_feed/`, compare reconstructed team net worth to GRID `NetWorth` at the delay-adjusted second. A median gap of a few hundred gold is noise. A systematic thousands-gold gap is a live feature bug.

### DG-6 — Data admission: LoL whitelist gates trade and backtest, not training

- **Stage:** collect, link, backtest, live
- **Claim:** The production model is fit on the full LoL split (3825 train matches). Live and the LIVE backtest drop leagues with no GRID feed, and only trade a 17-league list.
- **Evidence:** `config/lol_league_whitelist.json`: 17 leagues, `no_live_feed` = LPL and LCK Challengers League. Backtest loads it only for `game == "lol"` (`src/backtest/run.py:1740-1744`). Live discovery filters sidecars (`src/trader/discovery.py:202`, `src/trader/lol_league_filter.py`). `docs/experiments/lol-league-whitelist.md` (2026-09-08): filtered model rejected; full-split training unchanged; the gate is mandatory. Dota has no league whitelist. Collect stacks differ on purpose: Dota `src/collect/` (universe, OpenDota link, STRATZ, GRID horn); LoL `src/lol/01`–`04` (Gamma tag 65, lolesports link, window/details, Telonex).
- **Mechanism:** The booster sees LPL gold pace and then never trades LPL. That can move calibration. It does not timestamp the book in the future, so it is a weak explanation of +0.62¢ at 30 s. The whitelist is the same for the LIVE backtest and for live, so it also does not explain backtest-vs-live by itself.
- **Deliberate?** Yes.
- **Severity:** low for this gap. **Confidence:** verified that the gate exists; speculative that it costs live PnL.
- **Next check:** None until DG-1 is settled. A league-sliced markout would be the follow-up.

### DG-7 — Risk and size: one policy, three clips

- **Stage:** risk, sizing
- **Claim:** Follow300 thresholds, cutoff, entry band, staleness, debounce, and backtest layer size are shared. Live clip is not.
- **Evidence:** `src/shared/constants/strategy.py`: `MIN_ABS_DELTA=0.02`, `EXIT_ABS_DELTA=0.015`, `MIN_ENTRY_PRICE=0.35`, `MAX_ENTRY_PRICE=0.85`, `BUY_CUTOFF_SECOND=480`, `BUY_LEVEL_COUNT=3`, `GRID_FEED_STALE_SECONDS=16`, `EXIT_FEED_STALE_SECONDS=45`. `follow300_policy` takes only `level_usdc` and cadence (`src/strategy/policy.py:47-70`). `config/trading.toml`: `dota-map` 60, `dota-oddin-map` 100, `lol-map` 20. Backtest `BACKTEST_LEVEL_USDC = {dota: 100, lol: 100}` (`src/backtest/run.py:199`), so both manifests show `base_size_usdc=300`, `layer_usdc=100`. Both seed-0 manifests: `max_signal_age_seconds=16`, `max_exit_age_seconds=45`, `fill_model=queue`, `network_latency_ms=85`, `debounce_ms=100`, `quoter_tick_s=2`. Grid-v1 bands differ on purpose: Dota 11/8/7/6 s, LoL 8/6/5/5 s (`src/backtest/signals.py:69-79`, `docs/experiments/grid-v1-cadence.md`).
- **Mechanism:** A $20 clip on a thin edge looks flat next to a $60–$100 clip. It does not create a positive backtest markout. Per-map backtest dollars are not comparable to live dollars until divided by clip or by buy notional.
- **Deliberate?** Yes. Clip history is operational, not a strategy fork.
- **Severity:** low for the sign of the gap; required for any dollar comparison. **Confidence:** verified.
- **Next check:** Compare live and schedule-arm PnL per buy-notional dollar, not per map.

### DG-8 — Priors use different windows

- **Stage:** prepare, live
- **Claim:** LoL training prior is the last two-sided Telonex book in `[spawn-61s, spawn)`. Live prior, for both games, is a Polymarket minute bar ending `horn - 90s`, searched over 6 hours. Dota training prior is the catalog prior at the GRID horn.
- **Evidence:** `lookup_strict_prior` (`src/lol/05_prepare_dataset.py:180-183`), `LOL_PRIOR_WINDOW_SECONDS = 61`. Live: `anchor_ts = horn_unix_seconds - HORN_OFFSET_SECONDS` with `HORN_OFFSET_SECONDS = 90` (`src/trader/match_worker.py:508`, `src/shared/utils/match_time.py:9`), then `fetch_market_prior` (`src/trader/market_prior.py:15-27`) and `QUOTE_TRAILING_SECONDS = 6 * 3600` (`src/shared/constants/api.py:14`). Dota catalog prior is the last aligned pair before the horn (`src/collect/s05a_fetch_prices_history.py:192-214` and `:70-73`).
- **Mechanism:** `market_radiant_prior` is one of 12 inputs. A prior from 90 s earlier than the training prior shifts LoL live relative to LoL train by more than Dota, because Dota train and Dota live at least share the prices-history series. Unmeasured size.
- **Deliberate?** The 61 s book window and the 90 s live offset were each chosen. They were not lined up.
- **Severity:** low. **Confidence:** verified as a code difference; speculative as PnL.
- **Next check:** On a handful of live LoL `session.jsonl` rows, compare `market_radiant_prior` to the training-style book prior at spawn.

### DG-9 — LoL live will score pre-horn seconds the model never trained

- **Stage:** live
- **Claim:** `in_model_window` allows `second` in `[-60, 0)` during `PRE_HORN` for every game. LoL rows start at second 0. Dota minute rows start at −60.
- **Evidence:** `src/trader/session_quoting.py:94-100`. `MODEL_START_SECOND = -60` (`dataset.py:12`). LoL grid start is 0 (`lol/constants.py:123`). A live LoL second is `extrapolated scoreboard − feed_delay`, so the first ~8 s after the horn still have a negative `second` while the table may already show post-spawn gold (`grid_feed.py:109-117` and `:171`).
- **Mechanism:** LightGBM will still emit a delta off the bottom of the training support. Limited to the opening seconds. Not the 30 s markout on 0..480.
- **Deliberate?** The window is the Dota window, reused. Accidental for LoL.
- **Severity:** low. **Confidence:** verified.
- **Next check:** Count live LoL `signal` rows with `second < 0` and a model reason. Tapes are local under `data/trader/`.

## LoL live path (frame → quote)

Read as one pipeline. Shared with Dota GRID except the profile branch noted.

1. **Socket.** `GridLiveFeed` reads `wss://api.grid.gg/widgets-v2/live/<id>?delay=zero` (`src/trader/grid_live_feed.py`, URL builder `grid_widgets.build_socket_url`). Same socket as Dota GRID.
2. **Parse.** `parse_frame` → scoreboard service or table service (`grid_widgets.py`). Table rows become players: team from `teamColor`, level = `increaseLevel + 1`, gold = `NetWorth` (`grid_widgets.py:221-241`). Empty nicks drop.
3. **Reduce.** `GridFrameReducer` locks BLUE/RED to the market names once (`grid_feed.py:219-246`). LoL profile `side_0_text="BLUE"`, `side_1_text="RED"` (`game_profile.py:65-66`). `read_board_sides` treats side 0 as radiant (`grid_feed.py:87-94`). A failed orient raises `GridOrientationError`. Upcoming boards stay pending. This is the post-`bef88cb9` / `30ebe5b7` behavior: GRID sides, not a Steam leftover. LoL `uses_steam` is false, so discovery cannot pin Steam sides first.
4. **Features.** `_live_snapshot` (`grid_feed.py:151-185`): team net worth, deaths summed from players, XP from the LoL level table, top-1 via the LoL ratio. `paused = not clock_ticking`. Finished scoreboard emits a zero-feature terminal snapshot (`_terminal_snapshot`) and does not predict: `window_reason` returns `FINISHED` first (`session_quoting.py:105-106`).
5. **Model vector.** `MatchWorker._latch_fair` → `ModelServer.predict_fair` (`match_worker.py:424-427`, `model_server.py:168-178`). Columns are the shared 12. Fair = clipped(`market_p_radiant + delta`).
6. **YES/NO.** `yes_fair_from_model`: YES fair is radiant fair if `yes_is_radiant`, else `1 - fair` (`model_server.py:191-198`). `yes_is_radiant` is `outcome_0_is_radiant` from the locked board (`grid_feed.py:129`). `_gate_pair` swaps the two mids the same way before building `market_p_radiant` (`match_worker.py:399-404`). The two flips match.
7. **Quote.** A model decision becomes a `RawDeltaSignal` only when `reason` is `MODEL` (`match_worker.py:437-441`). `feed_core` sets the clock (`paused`, `finished`). `window_reason` blocks finished, out-of-window, paused, and a stale watchdog tick (`session_quoting.py:103-113`). Entry age is the watchdog (`GRID_FEED_STALE_SECONDS` 16, comment cites GRID unique-gold holes). Exit pull is 45 s (`strategy.py:32-33`). Cutoff 480 cancels buys in `src/strategy/quoting.py` (`_cutoff`). Same policy object as Dota; only `level_usdc` changes (`match_worker.py:336`).

Nothing in steps 3–7 swaps sides or drops the LoL ratio. The step that disagrees with training is step 5's book: it is "now", and the vector's game fields are the delayed table.

Pause: training subtracts pause gaps from the livestats clock (`assign_game_times` in `livestats_frames.py`). Live freezes `second` while `clock_ticking` is false and refuses a new prediction with `PAUSED`. Those agree if GRID `currentSeconds` is a game clock. Not re-measured on a tape in this pass.

Finish: a table tick is dropped when `active_game_number` is not the pinned map (`grid_feed.py:283-284`). The finished scoreboard is what ends the map. A missing finish relies on the stale watchdog and, if the socket dies, the feed-gone sentinel (`grid_live_feed.py`). Same GRID path as Dota.

## Checked and OK

- **Strategy identity.** One `Follow300Policy`. Thresholds, cutoff 480, entry 0.35–0.85, 3 layers, 16 s / 45 s ages, queue fill, 85 ms latency match across the two LIVE manifests. Differences that are real are clip, grid-v1 band, league gate, and lag.
- **Side orientation in the current reducer.** BLUE → radiant id, RED → dire id, YES flipped with `yes_is_radiant` on both the feature mid and the fair. Training features use the same blue=radiant assignment (`features_from_sides`).
- **Top-1 formula.** LoL live matches LoL train (`max/total`). Dota live GRID matches Dota train (`max/rest`).
- **XP formula.** LoL train and LoL live both use `LOL_LEVEL_XP`. Raw GRID XP is unused on both sides of live vs train.
- **Schedule arm is not the bug.** 163 LoL maps with a real GRID receipt time have +0.086¢ buy markout at 30 s. The anomaly is the other 782.
- **Dota GRID live being profitable does not contradict a LoL feed-delay story.** Dota's model was trained and backtested at lag 10. LoL's was not.

## Open questions for the owner

1. The 2026-09-19 commit says lagging the mid by 10 s was wrong because the live quote sits on the GRID clock. The book is not delayed with the table. Should the join go back to `state_wall + grid_delay` (8 s) or `+ 10` s, with the label 300 s after that mid?
2. Is the number you call "the LoL backtest" allowed to stay dominated by grid-v1 maps that have no admitted archive? The schedule subset is the part that resembles live.
3. Do you want the printed `source_lag_seconds` to mean the join, or to stay a historical constant so old catalogs still load?

## Needs from VPS

None. `grid_delay_s` was read from local `data/trader/*/match.json`. A live-vs-book lag measured from `session.jsonl` `signal` rows would be a local script, not a VPS read.

## Scripts and outputs

- `work/diff-grok/split_markout.py` — quantity-weighted buy markout and engine PnL by `signal_mode` / `feed_source` for both LIVE seed 0 catalogs. Run: `cd esports-trader && PYTHONPATH=src uv run python <that path>`. Output is the table in DG-2 (no separate log file).
- Local delay count (inline, not saved): 305 LoL `match.json`, `grid_delay_s` `{8.0: 302, None: 3}`.
