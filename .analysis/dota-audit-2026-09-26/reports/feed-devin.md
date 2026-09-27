# feed-devin — live feeds, live features, discovery, model server, live prior
Status: FINAL

Scope: Steam / GRID / Oddin live feeds, live feature construction and parity with STRATZ
training features, feed orientation, freshness/pauses/delays, source selection, model-server
wiring, live prior vs training prior, Disir discovery, model timing window and pre-horn
behavior, plus the N4/N4b archived-horn issue. Empirical checks replay archived feeds through
the live reducers and compare per-second features against `game_features.parquet` (STRATZ
exact-second). Scratch: `work/feed-devin/`.

Severity scale: S1 = wrong training data / labels / backtest numbers or live money flow;
S2 = material but bounded/intermittent bias or loss; S3 = real but smaller bug or operational
weakness; S4 = minor / latent / debuggability.

## Executive summary

The live feature pipeline is well built: features are shared verbatim with the training side
(`build_top_player_features`, `xp_advantage`, `normalize_pair_mids`,
`last_aligned_pre_anchor_pair`), orientation is pinned once and consistently, the model server
validates its contract strictly, and GRID's published clock freezes correctly through pauses.
Archive replay against STRATZ on 153 GRID + 13 Oddin maps shows tight agreement (death MAE
~0.05, NW-adv MAE ~33 GRID / ~11.5 Oddin, all consistent with a uniform ±1 s clock convention
offset).

The headline problem is N4/N4b, fully confirmed at HEAD in my scope: the archived
`horn_at_utc` for GRID (and latently Steam) is the *first* pinnable tick's naive estimate
(`occurred_at − clock_seconds` at clock ≈ −90), taken before any pre-horn stall completes, and
never re-pinned. The catalog prefers that archive horn, so 58 maps carry a horn that is early
by the pre-horn pause duration D (median ~70 s, max 549 s), which shifts every wall-keyed
market feature *and* the +300 s labels on those maps; 57 affected maps are in the validation
dataset and 50 are in the live backtest (all schedule mode). Live `second` itself is correct
during pauses — the corruption is confined to the persisted horn and everything keyed off it.

Other findings, in impact order: a successful-but-empty `fetch_market_prior` permanently
disables the prior for a map (verified code path); GRID tables can carry a 6th roster row
(ghost player) that inflates per-side sums live (verified on one map, ~0.7 % prevalence);
8 archive pairs share one `steam_match_id` across two maps of a series, corrupting the
STRATZ join for one of each pair; the model is evaluated on unbounded `second ≥ 0` while
training stops at 600, so exits past the cutoff price off out-of-distribution deltas
(live and backtest do the same thing, so it is consistent but unvalidated); and the live
prior anchor (`first event horn − 90`) diverges from the training anchor (`spawn_at` /
archive horn) exactly on the maps with pre-horn stalls.

## Empirical base

Archive inventory (`data/trader`): 581 GRID-fed, 76 Oddin-fed, **0 Steam-fed** Dota maps
(`stream_delay_s=900` everywhere → Steam never passes the 61 s delay gate; the Steam feed is
a latent path, see F9). 170 archives overlap `game_features.parquet`; 153 GRID and 13 Oddin
had enough in-window rows to compare. `joined_at_second` distribution (Dota): 311 maps attach
pre-horn, 15 at second 0–60, 7 later.

GRID replay-compare (shift scan −8..+8, join on `game_second` ∈ [0,600]): best-shift
histogram {−1: 147, −4: 1, +4: 1}; means — `nw_adv_mae` 32.8, `xp_mismatch` 10.8 % of rows,
`xp_mae` 64.2, `deaths_mae` 0.055, `top1_mae` 8.1, `paused_pct` 0.45 %.
Oddin replay-compare: all 13 maps best-align at +1; `nw_adv_mae` 11.5, `deaths_mae` 0.017,
`top1_mae` 3.6.

Pause scan (553 GRID archives, raw scoreboard frames): the published clock freezes during
every real pause — e.g. a 53 s wall pause at clock 1584 leaves the next board at 1584;
`grid-3006669-m1` froze at −90 for 449 s (N4b); `grid-3006421-m2` froze at −90 for 5078 s.
Conclusion: live `second` (= frozen-aware clock − table delay) is not pause-inflated; the
horn estimate *is*, because it is `occurred_at − clock_seconds` evaluated once, early.

## Findings

### F1 — S1, verified: archived `horn_at_utc` is early by the remaining pre-horn stall (GRID + Steam; Oddin already fixed)

Mechanism chain at HEAD:

- `grid_feed.py:113-115` `_horn_unix_seconds = clock_stamp_unix_seconds(board.occurred_at) −
  board.clock_seconds`. During a pre-horn stall the clock is frozen (e.g. −90) while
  `occurred_at` advances, so the estimate drifts later every tick; the *first* tick gives the
  earliest (most wrong) value — `attach_time + 90` — early vs. the true horn by the stall that
  is still ahead.
- `live_feed.py:102-111` `horn_is_pinnable` accepts the first PRE_HORN/IN_PROGRESS/FINISHED
  tick for Steam and GRID; only Oddin requires `second > 0` (the partial fix, commit
  `deebb730`, never extended).
- `match_worker.py:303-309` `_maybe_pin_horn` calls `pin_horn_from_event` once and sets
  `self._horn_pinned = True` on the first pinnable tick → no later tick can correct it.
- `match_meta.py:392` writes the first event's horn into `match.json` at `write_match_start`;
  `grid_archive.py:64` `trusted_horn=first_event_horn_iso(events)` replays to the *same first
  pinnable event*, so `finalize_match` "correction" re-pins the same wrong value
  (`match_meta.py:372-373`).
- `steam_feed.py:33-35` `start_timestamp + timestamp − game_time` and
  `steam_archive.py:109-122` `_first_trusted_horn_iso` — identical pattern, latent because
  Steam never wins the delay gate today.

Downstream (all consume the persisted horn, not the live clock):

- `s06_publish_catalog.py:119-127`: archive_id present → `horn_source="archive"`,
  `horn = archive_horn_at_utc` wins over `grid_derived` (`spawn_at + 90 + pre_horn_pauses`,
  verified correct: kill-reaction event study peaks at lag 0 for grid_derived, +62.5 s for
  archive-horn maps).
- `market_data/build_market_data.py:92-100`: every `state_us = horn + second +
  post_horn_pauses` — `market_p_radiant` rows *and* `signal_market_p_radiant_300s` labels are
  read ~D seconds too early on the wall clock for every second of the affected map.
- `ended_at`, catalog `anchor_at`, `s05a` fallback anchor (`load_anchors`: archive horn when
  no `spawn_at`), and `backtest/feed_schedules` horn-keyed lookups inherit it.
- Live: the pre-horn prior anchor uses the first event's horn (F8); `second`, the −60 window,
  and the 480 cutoff are unaffected because they come from the feed clock, which is correct.

Scale (N4): 58 catalog maps affected (56 GRID, 2 Oddin — pre-fix archives), D median ~70 s,
max 549 s; 57 enter validation rows (146 373 rows), 50 enter the live backtest, all
`schedule` mode (~$331 of ~$714 schedule-mode PnL). Roughly a third of future maps have a
pre-horn pause and will get the same corruption.

Impact: wrong market features and wrong labels on a structurally selected subset (maps with
pauses — not random), wrong backtest numbers on 50 maps, and a permanently wrong horn in
every archive that stalls pre-horn. Money/decision impact: high — this is exactly the "bad
data → bad model → bad decisions" class the owner asked about.

Remediation: (a) only pin the horn once `snapshot.second ≥ 0` (extend the Oddin rule to all
sources) or keep re-pinning while `phase == PRE_HORN` and latch on the first IN_PROGRESS tick
— at a post-horn tick the estimate is exact; (b) `s06` should prefer `grid_derived` (or
recompute the horn from the archived clock trajectory: `occurred_at − clock_seconds` at the
first tick with `clock_seconds ≥ 0`) over the stored archive horn; (c) rebuild market caches
and re-derive labels for the 58 maps, then retrain.

### F2 — S2, verified: a successful-but-empty prior fetch is never retried → the map is permanently `missing_prior`

`match_worker.py:544-586`: `_maybe_start_prior` refuses to start when `self._prior is not None
or self._prior_task is not None`. `_load_prior` clears `_prior_task` only on an exception
(line 584). When `fetch_market_prior` returns `None` — `missing_quote` or `pair_broken`
(`market_prior.py:29-40`), both normal "no tape in the trailing 6 h window" outcomes — the
task completes, `_prior` stays `None`, `_prior_task` stays set, and no retry ever happens.
`SignalReason.MISSING_PRIOR` then blocks every model decision for the rest of the map.

Observed: only 4 Dota maps all-time ended `missing_prior` (the Sep-26 cluster I initially
suspected is LoL, which uses the book-tape latch instead — different path). But the bug is
binary and silent: one transiently empty prices-history response blinds the map forever, and
a map attached before the market had any prints never gets a second chance.

Fix: on `prior is None` set `self._prior_task = None` and retry on a bounded cadence while
`second < 0`/early IN_PROGRESS; log once.

### F3 — S2, verified: GRID tables can list >5 players per side; live features sum every row

`_live_snapshot` (`grid_feed.py:160-194`) filters `table.players` by `team_id` and sums NW,
levels→XP, and deaths over **all** matching rows — no 5-per-side completeness or cap. Oddin
has exactly this guard: `_complete_side` requires 5 distinct nicknames per side
(`oddin_feed.py:193-195`, enforced at 432-433), so the bug is GRID/Steam-shaped.

Verified instance: `grid-3007267-m3` (steam 9003856182) — the table carries **11 rows**, 6 on
team 53864: `JANTER` is a roster ghost (NW 600, level 1, 0/0/0 — never changed all game) and
`ESCALATOR` is the real standin (13 kills, 18 864 NW). Effects live: `radiant_nw` +
a constant 600 → `radiant_nw_adv` off by ~600 the whole map (replay-vs-STRATZ MAE 583, vs.
~33 typical); XP advantage sums the ghost's level; `deaths_*` would corrupt too if the ghost
had any (here 0 → deaths reconciled). `build_top_player_features` picks `max`, so a high-NW
ghost would also corrupt `top1_nw_adv` / ratios.

Impact: biased model inputs on affected maps (~1/153 comparable ≈ 0.7 % in this sample) —
moderate but real, and invisible unless you diff against STRATZ. Backtest schedule-mode
replays inherit the same corrupted archive.

Fix: apply an Oddin-style completeness check to GRID (reject or trim to the 5 rows per side
that actually play — e.g. by kills+deaths+assists+level activity or by excluding rows that
never change from spawn values), and log a warning when a side has ≠5 rows so this is visible.

### F4 — S2, verified: same `steam_match_id` bound to two different maps of one series

8 archive pairs share one steam match id across adjacent maps: 8986344478
(`grid-2996008-m1/m2`), 8984433236, 8980211577 (`-m2/m3`), 8986955347, 9008150824,
9009540902, 9010104415, 9011063317 (`-m1/m2`). (A ninth pair, `9007118998` vs
`9007118998.pgl-detached`, is a benign archive retry.)

A Dota match id is unique per map, so one archive of each pair carries a wrong link. The
`+4`-shift outlier in the parity run was exactly this: `8986344478`/`grid-2996008` —
deaths MAE 1.8, NW MAE 916 — features from the sibling map joined to this map's market. The
binding happens in discovery (`_SideSource.steam_match_id = steam live-game match_id`,
`discovery.py:595-610`, archive id from `grid series+map` at `discovery.py:878-884`) — the
Steam live-list row can linger across the map transition and get bound to the next map's
archive; nothing checks the Steam game's map number against `current_map`.

Impact: for one map per dup pair the catalog/STRATZ join pairs market data of map A with game
features of map B (same teams — plausible-looking, hard to spot), and
`feed_schedules`/`live_archives` bind by `steam_match_id` so backtest pairing can hit the
wrong archive. 8 known pairs; prevalence in the full catalog needs a duplicate-id audit on
`match_links` (catalog-devin's side).

Fix: at bind time require the Steam game's map/`series_type` progress to match the archive
map number (or the steam match id to be unused by earlier maps of the series); on the catalog
side, flag any `steam_match_id` appearing under two different `archive_id`s and drop both.

### F5 — S3, verified: model evaluated for exit pricing on `second ≥ 0` unbounded — out-of-training-domain deltas after 600 s

`session_quoting.py:94-100` `in_model_window`: PRE_HORN admits −60 ≤ second < 0; IN_PROGRESS
admits **any** second ≥ 0. Training rows are strictly `second < 600`
(`train_model.py:107,119`; `TRAIN_END_SECOND_EXCLUSIVE = 600`; labels end at 900 =
600 + horizon). Buys are cut at `BUY_CUTOFF_SECOND = 480` by the strategy layer
(`strategy/quoting.py:206`, `877`).

Past 480 the model keeps running: `_enqueue_core_signal` (`match_worker.py:473-493`) pushes
`RawDeltaSignal` on every fresh non-paused tick, `_sync_latch`/`_rebuild_latch`
(`strategy/quoting.py:741-784`) keep `latch.fair_radiant` updating, and exits price off that
fair (`_recovery_fair`, `decide_sell`). At second 600+ the feature row (second, NW, XP,
deaths) is far outside every training leaf; LightGBM extrapolates the highest-`second` leaf —
bounded, but never validated.

Backtest parity: `backtest/signals.py:368` — "cutoff or beyond opens no gate, matching the
live reducer" — so live and backtest agree; both are extrapolating. Historical journals with
`outside_window` at second 900–2947 came from the retired Aug-15 `run_session` runtime
(added `60cc4f52`, removed `c2f54582`/`509b6484`) — not a current gate that regressed.

Impact: exits (and the fair used for cooloff/dust decisions) rest on deltas whose quality was
never measured beyond the train window. Not obviously wrong — just unmeasured. Fix options:
cap model evaluation at `TRAIN_END_SECOND_EXCLUSIVE + MODEL_TARGET_HORIZON_SECONDS` for the
signal (keep last-known fair or switch exits to book-only past it), or measure delta quality
on 600–900 validation rows before trusting it.

### F6 — S3, verified: systematic ~1 s clock convention skew vs STRATZ (GRID +1, Oddin −1)

147/153 GRID maps best-align at shift −1 (`live.second = stratz.second + 1` — GRID's clock
runs ~1 s ahead), 13/13 Oddin maps at +1 (Oddin ~1 s behind). Residual agreement is otherwise
tight (Section "Empirical base"). GRID `second = live_clock_seconds(board, age) −
table.feed_delay` (`grid_feed.py:181`); Oddin `second = game_time` embedded in the payload.

Impact: the `second` feature carries ±1 vs the training convention, and `nw_adv` at a labeled
second carries ~1 s of extra/less growth (~20–40 gold early-game) — small, inside the 10 s
`TRAIN_LAG_SECONDS` contract noise, and identical across live/backtest archives. The 10.8 %
`xp_mismatch` rows are level-up boundary placement, not a level-semantics bug (STRATZ
`stats.level` includes a level-1 entry at ≈ −89, so `level_at_second` returns current level
and `LEVEL_XP[level−1]` matches GRID's `LEVEL_XP[level]` indexing — verified on a raw STRATZ
record). Acceptable; document the convention so nobody "fixes" it later.

### F7 — S3, verified: GRID feed-gone terminal path only fires past the buy cutoff

`grid_live_feed.py:125-144`: on `InvalidStatus` (unpublished series) the feed-gone finished
tick is emitted only `if past_cutoff` (second ≥ 480, set at line 91-93); same at the
`MAX_CONSECUTIVE_FAILURES` exit (140-144). A GRID unpublish or persistent socket failure
**before** 480 ends `ticks()` with just an error log — no terminal snapshot, so
`finalize_match` raises "does not end in a terminal snapshot", no final block, archive
non-final (self-correcting for the catalog via the `ended_at` mask, but the live position is
orphaned and only `wallet_host` restart/`select_feed` re-pick — possibly Oddin — saves it).

Impact: rare; when it fires mid-position the market may still be open with no feed. Note the
asymmetry: before 480 the code *chooses* not to declare the map finished (correct instinct —
a socket drop ≠ map end), but it also leaves no explicit "feed lost" state in the archive.

Fix: when a pre-480 feed dies for good, write a distinct non-terminal `feed_lost` marker to
the archive/journal and let the host mark the map unmonitored rather than silently ending the
iterator.

### F8 — S3, likely: live prior anchor diverges from the training anchor precisely on stalled maps

Live: `match_worker.py:553` — `anchor_ts = first_event.horn_unix_seconds − 90`
(`HORN_OFFSET_SECONDS=90`), fetched once at the first PRE_HORN/IN_PROGRESS event.
Training (`s05a_fetch_prices_history.py:56-74`): `anchor = spawn_at` (GRID `startedAt` =
the wall instant the clock reads −90) else `archive_horn_at_utc` — no −90 offset on the
fallback, which is a *different* anchor convention than live's `horn − 90` (the fallback
anchors at "the estimated horn", live anchors at "estimated horn − 90" — a systematic ~90 s
offset on archive-horn-fallback maps even before the stall error).

Regimes:

- Early attach, no stall (the common case, ~93 % of Dota maps): live anchor ≈ attach ≈
  spawn_at → consistent with training. Verified structurally.
- Attach mid-stall at clock −90: live anchor = attach time (since horn_est = now+90), i.e.
  `spawn + stall_so_far`; training anchor = spawn → prior differs by market drift over the
  elapsed stall (up to ~500 s of price movement in observed archives).
- Post-horn attach (22 maps): live anchor = true horn − 90 = `spawn + D`; training anchor =
  `spawn` → off by D.
- Archive-horn fallback in training: anchors at the *early* archive horn — shifted yet again,
  on the same 58 maps as F1.

Impact: `market_radiant_prior` is a train feature read at a slightly different wall moment
live vs. training on exactly the pause-affected subset — systematic skew of the prior feature
(rarely more than a cent or two of mid drift, but not zero), compounding F1. Fix: same
corrected horn as F1; for the fallback path use `horn − 90` (or better, `spawn_at`) so the
convention matches.

### F9 — S4, verified: the Steam feed path is latent dead code

`steam_delay_s` = `stream_delay_s` from `GetLiveLeagueGames` (`discovery.py:363-372, 607`);
every archived discovery shows 900 → `pick_source` (`source_picker.py:52-60`,
`MAX_FEED_DELAY_SECONDS = 61`) never admits Steam; zero Steam-fed archives exist. The whole
`steam_feed`/`steam_archive` path — including its identical first-tick horn bug (F1) — is
unexercised in production. If tournament delays ever drop under 61 s, the latent horn bug
activates at the same time as the first real Steam coverage. Also note `_is_paused`
(`steam_feed.py:95-102`) is a wall-vs-game-clock gap heuristic that marks the first tick
paused — conservative and fine.

### F10 — S4, verified: watchdog/freshness asymmetries worth documenting

- Freshness: `FreshnessWatchdog` (`session_engine.py:98-163`) arms per snapshot; `consume()`
  marks the *previous* tick's expiry — i.e. freshness is "the last tick arrived on time",
  not "this tick is young". Combined with per-source `stale_seconds` (GRID 16 s on
  unique-gold cadence — comment at `strategy.py:28` cites p50 3.6 s/max 40 s — Oddin 15 s,
  Steam 3 s) and `EXIT_FEED_STALE_SECONDS = 45 s` for exits. Sound design; note the
  semantics so future readers don't read `fresh` as tick-local.
- During a pause, `paused` snapshots still arrive (GRID publishes frozen-clock frames) →
  `window_reason` → PAUSED → no new signal; the last signal stays fresh for 45 s of exits;
  a pause longer than 45 s freezes exits entirely until resume. Bounded and probably
  intended, but a 449 s pre-horn stall with an open position (buyable from second −60) can't
  exit for most of the stall.
- `window_reason` order: FINISHED > window > PAUSED > STALE — a paused *and* stale tick
  reports PAUSED; harmless.

## Verified-correct areas (worth keeping)

- **Feature parity is structural.** `build_top_player_features` is the same function in
  `grid_feed.py:179` (Dota), `oddin_feed.py:403`, and `stratz_seconds.py:196` (training);
  `xp_advantage`/`LEVEL_XP` shared; `normalize_pair_mids` + `PAIR_SUM_TOLERANCE` shared
  between live `_gate_pair` (`match_worker.py:422`), the training prior (`s05a:204`), and the
  market cache (`build_market_data.py`). `FEATURE_COLUMNS` == parquet columns minus the
  join-time `market_p_radiant`; `NO_XP_FEATURE_COLUMNS` drops exactly `radiant_xp_adv`.
- **Model contract is strict.** `model_server.py`: `model.json` features must equal the
  profile's expected list and `source_lag_seconds` must equal `TRAIN_LAG_SECONDS`; ensemble
  members are plain filenames, deduped, tree-count-checked, per-member feature-checked;
  all inputs finite (booleans rejected); ensemble = member mean; fair clipped to [0,1];
  `radiant_fair → yes_fair` conversion validates polarity type.
- **Satellite wiring is right.** `strategy_profile_name(game, feed_source)`
  (`game_profile.py:92-98`) picks `dota-oddin-map` (no-XP) only for `FeedSource.ODDIN`;
  `wallet_host.py:1316-1324` passes the per-source model into `MatchWorker`; all catalogs
  load at startup (fail-fast) in `host_resources.py`.
- **Orientation is pinned once and applied consistently.** First event's `yes_is_radiant`
  is written onto the worker, `DiscoveredMatch`, and `MatchStart`
  (`match_worker.py:254-267`); `_gate_pair` maps YES/NO mids into radiant/dire with the same
  flag every tick; `yes_fair_from_model` inverse-maps. GRID self-orients from board side
  names vs market outcome names + aliases and *raises* `GridOrientationError` rather than
  trading un-oriented; Oddin carries orientation from discovery binding. No silent
  side-flip path found.
- **GRID pause semantics verified empirically**, not just by code reading (Section
  "Empirical base"): `second` is correct through pauses; `paused = not clock_ticking`;
  paused events gate PAUSED; mid-game kill-vs-table lag (~8.3 s) is mitigated by the
  `KillTick` wait gate (`grid_feed.py:286-323`, `KILL_GATE_HOLD_S=10`,
  `KILL_GATE_MAX_BOARD_AGE_S=9`).
- **Source selection is sane**: per-map probe of the *table* delay (the model-relevant lag,
  `source_picker.py:124-133`), ≤61 s gate, tie-break Steam>GRID>Oddin, failed probes drop
  only that source, pinned archives rebuild their source, `no_usable_feed` →
  `waiting_for_feed` retry (`feed_selection.py`, `wallet_host.py:1300-1308`).
- **Disir catalog/discovery is bounded and defensive** (`oddin_catalog.py`): seed 14900,
  frontier scan LOOKBACK 200 / LOOKAHEAD 100, stop after 100 consecutive `Not found`, hard
  cap 2000 ids, refresh only in the active window, invalid ids marked permanent;
  `oddin_discovery.py` resolves ambiguous team-name hits by probing Disir snapshots and
  accepting only a uniquely-playing candidate.
- **Oddin completeness guards**: exactly-5-distinct-players per side, `lastUpdatedAt`
  monotonic dedupe across reconnects (reset clears clock tracking but not horn), horn frozen
  at first `game_time > 0` — the model that GRID/Steam horn pinning should follow.
- **Archive-first ordering**: every raw frame is written to `state.jsonl` before any trading
  decision touches it; `finalize_match` re-derives outcome/summary from a replay, and double
  finalization with a different block raises.

## Follow-ups / open questions

1. Recompute horns for the 58 affected archives (clock trajectory: `occurred_at − clock` at
   first tick with `clock_seconds ≥ 0`), rebuild their market caches and labels, retrain —
   then re-run the N4 event study as the regression test.
2. Decide the canonical fix for horn pinning (second ≥ 0 latch for all sources) — one-line
   change in `horn_is_pinnable` covers GRID, Steam, and future feeds.
3. Instrument `missing_prior` retry + alert; report how many of the 4 historical maps had
   later-arriving tape (would have been rescued by a retry).
4. Duplicate-`steam_match_id` audit on `match_links`/catalog (hand to catalog-devin); add the
   map-number check at steam↔grid bind time.
5. GRID 5-per-side completeness check mirroring Oddin's `_complete_side`, plus a warning
   when a side has ≠5 rows — this is the kind of corruption that is invisible without a
   STRATZ diff.
6. Decide whether model evaluation should stop at `TRAIN_END_SECOND_EXCLUSIVE +
   MODEL_TARGET_HORIZON_SECONDS` for signal purposes; if exits keep using fair past it,
   measure delta calibration on the 600–900 validation band.
7. Add a `feed_lost` archive marker for pre-cutoff GRID death so finalize/catalog can
   distinguish "map not finished" from "archive truncated".
8. If Steam delays ever drop under 61 s, fix the Steam horn pin before enabling it.

## Evidence index

- Scratch scripts: `work/feed-devin/compare_grid.py`, `compare_oddin.py`, `check_overlap.py`,
  `grid_pause_probe*.py`, `grid_pause_scan_all.py`; outputs `compare_grid_full.txt`.
- Code citations (all `src/`): `trader/live_feed.py:102-119`, `trader/grid_feed.py:113-115,
  160-194, 286-356`, `trader/steam_feed.py:33-35,95-129`, `trader/oddin_feed.py:62-64,193-195,
  359-418`, `trader/match_worker.py:218-241,254-267,303-309,398-449,473-493,544-586`,
  `trader/match_meta.py:329-347,350-374,377-399`, `trader/grid_archive.py:28-65`,
  `trader/session_quoting.py:94-113`, `trader/session_engine.py:98-163`,
  `trader/model_server.py:160-309`, `trader/market_prior.py:15-60`,
  `trader/game_profile.py:48-103`, `trader/wallet_host.py:1300-1324`,
  `trader/feed_selection.py`, `trader/source_picker.py:32-60,124-133,173-197`,
  `trader/grid_live_feed.py:91-147`, `trader/oddin_live_feed.py:72-138`,
  `trader/discovery.py:363-372,595-610,860-884`, `trader/oddin_catalog.py:26-31,214-221,
  265-359`, `collect/s05a_fetch_prices_history.py:56-74,192-221`,
  `collect/s06_publish_catalog.py:103-144`, `market_data/build_market_data.py:92-136`,
  `shared/utils/match_time.py:34-55`, `shared/utils/top_players.py:40-77`,
  `prepare_dataset/stratz_seconds.py:196`, `backtest/signals.py:368`,
  `shared/constants/strategy.py:6,28-33`, `shared/constants/dataset.py:15-19`.
