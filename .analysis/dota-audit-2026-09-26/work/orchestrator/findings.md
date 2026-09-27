# Orchestrator findings ledger

Status per finding: `verified-by-me` (I reproduced), `agent-verified` (agent reproduced; I read the evidence),
`agent-likely`, `refuted`.

## Orchestrator (own checks)

- **O1 (S2, verified-by-me). Live-archive horn pinned before a pre-horn pause.** `src/trader/live_feed.py:102-111`
  `horn_is_pinnable` accepts the first PRE_HORN tick for Steam/GRID; Oddin got the guard in `deebb730`
  (2026-09-20). `s06_publish_catalog.py:119-127` prefers the archive horn. 58 catalog maps (56 GRID, 2 Oddin),
  D median 70 s, max 549 s. Event study: archive-horn paused maps' market reacts to kills at +62.5 s (cache lag),
  grid_derived paused maps at 0. Rows affected: validation 57 maps / 146,373 rows; LIVE backtest 50 maps
  (schedule mode; engine PnL $331); production train 55 maps / 475 rows (2.81%). State is (D − 10) s AHEAD of the
  market on those rows (look-ahead). Grows with every data refresh (~1/3 of maps have a pre-horn pause).
  Also live prior anchor (`match_worker.py:553`) uses the first event's horn − 90 s.
- **O2 (refuted).** 39/39 held-to-end winners = look-ahead. Held maps are short stomps; policy effect likely.
  (Awaiting fill-grok / fill-devin / parity-luna for the exact mechanism.)
- **O3 (refuted).** Series markets used as map markets: decider-map rule by design (376/410 BO3 verified).
- **O4 (checked OK).** market_seconds cache identical to in-memory rebuild on 34/34 sampled maps; staleness is
  a latent design risk (hash excludes catalog/book/code).
- **O5 (checked OK).** STRATZ exact-second reconstruction is causal (events with time ≤ S); XP is level-based
  in train, validation and live (consistent); as-of book lookups never read the future.
- **O6 (checked).** Live keeps running the model after 600 s (grid-3006669-m1: 371 `model` rows at 600+); the
  `outside_window` row in log-map.md is stale documentation.

## train-luna (FINAL)

- F1 (S2, agent-verified mechanism): early stopping on the validation maps = backtest maps. Inner-split refit
  showed no forecast-metric optimism on the latest 40% of validation (−0.0044¢, CI −0.028..+0.020).
- F2 (S3, agent-verified): fair overstated by +1.916¢ (CI +0.877..+3.085) when Radiant p ≥ 0.85
  (11,027 rows / 80 maps). Affects SELL price floor (holds winners longer?).
- OK: labels/lag joins, hashes, no duplicate maps, inference parity (1e-17), map-grouped bootstrap, CIs by
  event series. Late window 600–900: bias small, edge weak (+0.093¢, CI −0.045..+0.238).

## fill-grok (FINAL)

- F1 (agent S2 → my S3, agent-verified): SELL dropped when ceil(fair) = 1.00; no fallback to ceil(ask)=0.99
  (`quoting.py:519-532`). Explains the tail: loser tokens sold fraction 1.0000, winners 0.92. Tail profit vs
  cost $1,450–1,586/seed (~half of engine PnL) but vs last fresh mid only +$45–56/seed. Not look-ahead.
  Settlement identity exact (engine = cash + catalog payout, 0 mismatches). No fills after game end.
- F2 (S3): wallet/drawdown keep one lot per match (both tokens merged) — required_cash/MTM only.
- F3 (S3, debuggability/decisions): terminal report omits settlement split; closed-hold stats hide open holds.
- F4 (S3 latent): markout falls back to settlement (outcome) when no mid; not hit on LIVE.
- F5 (S4): terminal_position = last fill's token only.
- Confirms O1: match 8982107035 entry at cache-second 631 / SELL 32 s after catalog game end = early horn.

## link-grok (FINAL) — linking and sides are clean

- 0 hard flips on 2,276 grid_derived maps; 0 live vs catalog disagreements (174 shared); YES = team_a on all
  5,228 candidates; decider rule verified (254 BO1/410 BO3/13 BO5).
- F1 (S3): s03 attaches an archive with a different Steam id when winners agree (2 maps; same map, 2 ids).
- F4 (S3): GRID archive with no Steam id and no OpenDota row never links (2 played maps dropped).
- F2/F3 (S4): outcome names not stored in Dota universe index; short-map series drop has no audit reason.

## time-grok (FINAL)

- F10 (agent S1): = O1, code path confirmed incl. `finalize_match` trusted_horn = same first pin; replay window
  `[horn−2min, ended_at+1min]` cuts the book D−60 s before the true end. Schedule decisions themselves use
  receipt time (not shifted). Live `second` is NOT shifted.
- F1 (S2 → S3 after time-grok-lag: +6 s costs ~0.016c MAE gain, entry markout kept 99.7–99.98%, 11.7% of entries drop below 2c; verified-by-me: received − lastUpdatedAt per-map median 15.09 s, p10 15.04, p90 15.10, 74 maps; script work/orchestrator/oddin_lag.py): **live Oddin state age ~16 s** (p50 16.04 s, 25 maps; transport 15.6 s stable) vs
  10 s training join and model.json; $200 clip. Model counts ~6 s of already-priced move → optimistic entries.
- F2 (S2): `find_longest_book_gap` over −60..900 drops 92/717 validation maps from the backtest (future info).
- F3 (S2): grid-v1 (464/613 maps) never sets `paused`; quotes into pauses until 16/45 s stale timers.
- F4 (S2): early stopping on backtest maps (= train-luna F1).
- F5 (S3): live GRID feature age 8.4 s vs 10 s join (cutoff ~10 s early in grid-v1 vs live).
- F6 (S3): fair used past last trained second 540 (live and backtest alike).
- F7 (S3, series only): series c-bucket uses un-lagged second on grid-v1.
- F8: prior anchor errors cancel (not a finding). F9 (S4): Steam stale constant mismatch, latent (Steam unused).
- Arch: three clocks, one constant "lag"; validation `second` = market second vs train `second` = state second.

## link-grok-arch (FINAL) — architecture, ranked

1. One horn rule for every feed (= O1 root cause: guard fixed for Oddin only, docstring says Steam/GRID keep first tick).
2. Print settlement split in the terminal report (= fill-grok-F3).
3. Key market_seconds on catalog row (horn/pauses/token idx/duration/token ids), not 6 constants (after the horn
   fix, affected maps keep the old join until someone deletes the dir).
4. **Backtest size ≠ live clip**: `BACKTEST_LEVEL_USDC` $100/rung (`run.py:210`) vs live $60 Steam/GRID, $200
   Oddin (`trading.toml:35,53`); third name `BASE_SIZE_USDC=100` asserted in tests. Queue position & PnL scale.
5. Markout settlement fallback (= fill-grok-F4).
6. Nautilus private-method monkeypatches live inside the 2,223-line `run.py`.
7. poly-maker patched by assigning six module globals (`engine_seams.py:350-366`) — silent break on fork rename.
8. Dota and LoL dataset writers are copies (same class as the Oddin-only horn guard).
9. `game_features.parquet` has no input stamp (overwritten in place; manifest hash only after the fact).
10. Live tape viewer plots against the (early) archive horn; no cash vs settlement / yes_is_radiant header.
11. No test pins the persisted horn.
12. `docs/as-is.md` stale (2026-09-04 counts).
13. Extract `wallet_host.reconcile` (1010-1125) only; do not split quoting.
14. Leftover `docs/experiments/pnl-action-v1/replay_pnl_action.py` uses `BASE_SIZE_USDC`.
15. Speed: market_seconds rebuild cost is paid again on any horn/token fix (item 3 fixes).

## fill-grok-books (FINAL, B4 under re-check)

- B1 (S2 design, verified): books synced with `rsync --ignore-existing` (+`--partial`), presence = full day,
  market cache never rebuilt → a short day freezes forever. Today: collector days 08-09..09-25 reach 23:59 except
  08-23 (ends 13:48; no catalog maps).
- B3 (S3): 2026-08-08 has no book/trade/onchain files (4 catalog maps skipped).
- B4 (S2 → re-check): raw onchain_fills 2026-09-20 only 20:29–23:59 (2,204 rows vs 100–176k); sync keeps the
  conflicting local file unless `--onchain-start-date` is set. BUT orchestrator: 14 of the 18 maps are in LIVE
  with 28 BUY/26 SELL fills before 20:29 → the backtest took trades from another source (`_backtest_cache/trade`,
  3.3 GB?). Sent back to fill-grok to trace that cache layer.
- B5 (S3): `trades/` channel stops 2026-09-17 (excluded from rsync).
- B6 (S3): paid Telonex short days 2025-11-26 (7 maps) and 2026-01-18 (25 maps) end before match end.
- OK: best bid/ask = max bid / min ask (order not trusted); collector timestamp = exchange time when sane, else
  receive, strictly increasing; onchain days publish only on finalized head.

## data-luna (FINAL)

- F1 (S2) = time-grok F2: book-gap exclusion; ≥19/717 maps excluded only by post-entry gaps (generous cutoff).
- F2 (S3): crossed books (bid ≥ ask) pass `ok` gates (spread_ticks negative, pair sum still ~1). 1 strict cross in 4
  sampled maps (current mid and a +300 label both 0.33). → follow-up sent (rate, backtest replay fills).
- F3 (S3): 1/11,070 training labels land 4 s after game end (post-game jump 0.395 → 0.01).
- F4 (S3, debuggability): live signal archives omit the full 12-feature input → live feature drift unmeasurable.
- Arch: attrition — train keeps 1,563 of 2,485 pre-cutoff catalog maps (market data quality); Oct-2025 6/58.
  Validation seconds: 74.8% ok, 18.5% wide_spread, 5.5% stale, 1.1% missing. Train vs validation feature
  shift modest (KS ≤ 0.086). 12 monthly cache rebuilds matched exactly.

## history-devin (FINAL)

- Taxonomy: ~190 classified fixes; classes train_data 32, crash 29, silent_drop 27, feed_parse 23, cache_stale 19,
  lookahead_selection 13, discovery 12, time_lag 11, order_state 10, side 9, config 6, accounting 5.
  Hotspots: run.py 23 fix commits, session_core 13, backtest/strategy 12, prepare_dataset 10.
- F1 (S2 with O1): Dota market cache version = 6 scalars, not code; LoL sibling fixed in `00344982` (module hash).
  After the horn fix the 58 maps' caches will NOT rebuild unless deleted by hand.
- F2 (S3): `gate_*_seconds` counts no_quote events, not seconds (misleading gate attribution).
- F3 (S3): `delta_gate_open` not in StructuralCheckpoint nor digest (restart rearms at 0.02; parity blind).
- F4 (S4): Schmitt gate advances only on evaluated events.
- F5 REFUTED: "backtest stops at 899" — validation rows run to map duration (time-grok: 8837869969 to 3694 s).
- F6 (S3): risk caps = multiples of clip sum (daily_loss_kill ≈ $820), docs say "never fires".
- F7/F8 (S4): dead databet modules; stale as-is.md. F9 = Oddin lag (quantified small by time-grok-lag).
- VPS check (me): sun runs production 20260924T183900Z / noxp 20260924T183921Z = local; git clean at 00c3dd19.

## fill-grok-books B4 revised + orchestrator verification → CANDIDATE S1

- Revised B4 (verified by fill-grok): 2026-09-20 LIVE fills before 20:29 are book-snapshot crosses (fill ts =
  snapshot ts, price on the opposite touch), not onchain prints, not `_backtest_cache` (July fold, unused).
- **Verified-by-me in installed Nautilus `backtest/engine.pyx`:** `process_order_book_delta` →
  `_clear_all_queue_positions()` (line 6903: every resting order's queue-ahead := 0) on snapshot flag or CLEAR;
  `_seed_tob_baseline()` (6943) only stores TOB, never re-seeds queue-ahead. Telonex loader emits CLEAR per
  snapshot; collector writes a full snapshot per book change. ⇒ queue model effectively off: after the first
  snapshot our order is first in line; next trade at our price fills us; a snapshot touching our price fills us.
  Direction: more maker fills, less adverse selection than live → backtest PnL optimistic. Size: fill-grok-queue.
- B7 (S3): `_backtest_cache/{book,trade}` = July 2026 fold written by a deleted script; unused by the loader;
  `trade-ticks-v1` (~/.cache/nautilus_trader/telonex) IS read and is not evicted when raw books are rewritten.

## signal-devin (FINAL)

- S1 = O1 (independent intercept fit confirms feed clock is right, archived horn is wrong). Adds: s05a prior
  falls back to archive horn when no GRID window → prior sampled D early on those maps.
- **O1 scope expansion (verified-by-me from signal-devin `horn_shift.csv`, 149 schedule maps in LIVE):** after
  subtracting feed delay (GRID 7.6 s, Oddin 16.2 s): GRID 66/136 maps |offset|>5 s, 29 >60 s, 2 >300 s; Oddin
  5/13 >5 s, 4 >300 s. The 4 Oddin maps (9007208887, 9007618767, 9007618656, 9007700576; pre-`deebb730` pins at
  the −90 tick, no GRID spawn) have archive horn 814–1010 s early → 44 production-train rows + 10,937 validation
  rows with market ~14 min away from the state.
- S2: grid-v1 cadence cannot express real outages: 45% of schedule maps (67/149) have ≥1 in-window gap >45 s
  (92 gaps; p99 interval 31.6 s, max 4,622 s); grid-v1 gaps are geometric, mean ≤11 s → stale gates almost never
  fire on 76% of maps → optimistic opportunity set (same class as LoL A6, time-grok F3).
- S2 (dup): cache identity; also 3 version dirs (v59b14c69 2,277 files, va75c29ad 3,081, current v9c88adc2 3,041),
  no GC; manifest does not hash per-match market-second files.
- S3: one `auto` cohort mixes two timing regimes (24% schedule / 76% grid-v1) under one PnL; manifest hides it.
- S3 (dup): selection waterfall 717 → −92 book-gap → 625 → −12 archive exclusions → 613.
- S3: `join_validation_rows` raises on one bad row and kills the whole prepare (no per-map isolation).
- S4: pair legs aged independently (≤5 s each); `usable` rows run to ~5,000 s; tick horn jitter; 456 MB dead caches.

## feed-devin (FINAL)

- F1 (S1) = O1; also Steam horn has the identical first-tick pattern (latent: Steam never passes the 61 s delay
  gate; 0 Steam-fed archives; all discoveries show stream_delay 900/300). GRID clock freezes correctly in
  pauses (553 archives scanned); live `second` correct.
- F2 (S2 → S3 by frequency): `_load_prior` clears `_prior_task` only on exception; an empty prices-history answer
  (missing_quote/pair_broken) leaves the map `missing_prior` forever (4 Dota maps all-time).
- F3 (S3): GRID table can list 6 rows per side (roster ghost); live sums all rows (grid-3007267-m3: +600 NW all
  map, MAE 583 vs STRATZ). ~1/153 maps. Oddin has a 5-per-side guard, GRID does not. Schedule-mode backtest
  replays the same archive features, so it inherits it.
- F4 (S2 → S4 after my check): 8 archive pairs share a steam_match_id across adjacent maps. Catalog is correct:
  8986344478 links only grid-2996008-m1 (duration 1729 matches); m2's market has its own id 8986439206 with no
  archive (LIVE manifest excludes it); other pairs are not in the catalog. Impact = wrong steam id in match.json.
  (feed-devin's own +4-shift outlier came from its script joining by steam id.)
- F5 (S3): model evaluated past 600 s for exits (dup with time-grok F6 / train-luna late-window numbers).
- F6 (S4): ±1 s clock convention (GRID +1, Oddin −1) vs STRATZ; parity otherwise tight (NW-adv MAE 33 GRID,
  11.5 Oddin; deaths MAE 0.05).
- F7 (S3): GRID feed death before 480 ends ticks() with only a log; no terminal/feed_lost marker; open position
  relies on host re-pick.
- F8 (S3): prior anchor conventions differ on fallback maps (s05a uses archive horn without −90 when no spawn;
  live uses first-event horn − 90). time-grok F8: for spawn maps the errors cancel. Small (1–2c drift at most).
- F9/F10 (S4): Steam path is latent dead code; watchdog semantics notes.
- Verified OK: shared feature functions (top1, xp, pair mids), strict model contract, satellite wiring
  (dota-oddin-map → noxp only for Oddin), orientation pinned once, Disir catalog bounded, source picker sane.

## sun-devin (FINAL) — live ops

- F1 (S1 live-money risk, verified-by-me in live.log lines 541-543, 1141-1154): discovery emits a pinned Oddin
  source with `oddin_match_id=None` (Disir `_open` empty ~40 s after boot / card flicker; 93 "oddin bind
  skipped" lines) → `feed_selection._build_feed` raises CorruptFeedPin → relaunch → `refusing to rebind`
  (mutable market fields in the identity check) → session orphaned while discovery keeps emitting.
  Today: 9016905513 (113 s gap, recovered), 9017026154 (orphaned; was flat). 10/14 all-time orphans in the
  Oddin era. Mid-window this abandons a $200-clip map, possibly with inventory.
- F2 (S2): `docker compose restart` deploys kill live sessions (6 boots today), no graceful drain; triggers F1.
- F3 (S2): collector keeps crossed books in 1102/3782 Dota partitions (29%); validator warns, test asserts
  acceptance. Downstream: data-luna F2 (crossed passes `ok`) → follow-up running.
- F4 (S2, frozen poly-maker): heartbeat stale-id 400 loop → `heartbeat_down_halting` 10–15 s ×2 today.
- F5 (S2): 11/333 Dota sessions joined >60 s after horn (worst +2502 s).
- F6 (S3): 14/331 orphan tapes (no session_end/cleanup); `placed[]` has no order ids; orphans drop out of day sums.
- F7 (S3): unexplained match.json rewrite (9017026154 at 14:35). F8/F9 (S4): 429 reconcile skips; emit log spam.
- OK: core_state --all: 648 archives VERDICT OK (0 wedges); no stranded inventory; models identical VPS/local;
  collector continuity OK; onchain healthy. Live day PnL 2026-09-26: +$188.60 (rebate +18.15).

## parity-luna (FINAL)

- F3 (S2, key number): 49 common complete GRID maps (Sep 1–19, all backtest schedule mode): live session-end
  PnL +$53.05 on $10,352 BUY (+0.51%/BUY $) vs backtest 3-seed mean +$647.18 on $6,153 (+10.52%). Live BUY fills
  202 vs backtest 161; markout 30 s 0.127c vs 0.365c; 300 s 2.35c vs 3.41c. Tail maps < 8% of the gap. Mixed
  model versions (5 historical live builds): current research model flips the 2c gate on 20.2% of same-input
  rows. Replay of live model on archived frames reproduces logged deltas (p95 err 0.00106).
- F1 (S2) = O1: 31/33 locally joinable paused GRID maps show the offset; live `second` fine; prior anchor fine
  for spawn maps (keep prior anchor separate when fixing horn).
- F2 (S2) = book-gap selection: 20/92 excluded maps fail only because of post-480 gaps.
- F4 (S2 exposure) = settlement tail; 35 non-dust holds settle $5,522 (engine +$1,669); all winners.
- F5 (S3) = no timestamped full feature vector in session/core_trace.
- Population: 330 live conditions → 147 in LIVE backtest; 24 in validation but excluded (12 gap, 12 archive
  exclusions); 159 outside the validation id set (not explained). 18 backtest maps with no local live session.
- Follow-up sent: order-lifecycle parity (is the backtest over-filling?).

## train-luna-followup (FINAL)

- Row selection on future book (S3): +300 label gate drops 17.75% of current-OK train minute rows, 10.53%
  validation, 15.35% production (mostly wide spread). Dropped rows have heavier move tails (train |move|≥10c
  +4.86 pp, CI +2.50..+7.39). 5-member paired sensitivity with relaxed labels: MAE gain change −0.0009c (CI
  −0.018..+0.016); large |Δ̂| slightly smaller. → real selection, negligible model effect.
- F2 money side: fair-bound SELLs (ceil(fair) > ceil(ask)) held only winners in all seeds; later value beat
  immediate-ask counterfactual (+$1,449–1,867 <0.85 bucket; +$327–549 ≥0.85). Not a loss driver (in-sample,
  under the optimistic fill model).
- Horn-bug metric impact (supports O1 severity): excluding the 57 maps changes research MAE gain 0.243 → 0.218c
  (−0.025, CI −0.057..+0.004) and directional markout 1.618 → 1.511c (−0.107, CI −0.225..−0.001). The misaligned
  rows make the headline metric look ~10% better (look-ahead direction). Small today, grows with every refresh.

## CORRECTION (orchestrator error) — queue reset per snapshot is REFUTED

- `crates/core/src/telonex.rs:355-420`: `append_snapshot_rows` (the only CLEAR emitter) runs once, for the first
  in-window snapshot (`emitted_snapshot` gate); later snapshots → UPDATE/DELETE diffs (flag LAST=128, no
  F_SNAPSHOT=32). So `_clear_all_queue_positions` fires once per loaded window, not per snapshot. My earlier
  "queue model effectively off" was wrong: I verified the engine side, not the CLEAR frequency. fill-grok's B4
  "every snapshot emits CLEAR" was a misread. Corrections sent to fill-grok and parity-luna.
- The 2026-09-20 pre-20:29 fills without trade ticks = book-side marketable fills (ask moved onto our resting
  bid). Plausible as maker fills when no other bid sat at our price.

## fill-devin (FINAL) + orchestrator checks

- S1 claim → **S2 verified-by-me (mechanism), direction re-assessed** (`work/orchestrator/aggressor_check.py`, match
  8982107035, 2026-09-04): every onchain fill row is filed in BOTH token files (1005/1005 shared, price_0+price_1=1,
  identical taker_side). On rows where taker_asset ≠ file asset (59% in file0) the file token's mid moves the
  OPPOSITE way to taker_side (buy→down 267 vs up 69; sell→up 76 vs down 3); where taker_asset = file it agrees
  (buy→up 239 vs down 28). Converter (`telonex.py:3253`, `telonex.rs:844-893`) uses taker_side verbatim →
  ~50% of trade ticks have the wrong aggressor on that book. Introduced in framework `04bc79fb` (2026-05-01).
  My reading of the effect: the sell-flow that should advance our resting BUY queue (sibling-token buyers) arrives
  as Buyer ticks → our BUY queue is NOT decremented (missed/late fills); the phantom-fill path fill-devin describes
  needs our order on the wrong side of the touch, which the policy does not do. Net PnL sign unknown.
- S1/S2 (likely): onchain trade ticks stamped at block time (whole second), p50 ~+2 s late vs the trades channel
  (range −8.5..+14.5 s); book diffs show the level shrink first (cap_queue_ahead lowers queue), then the late trade
  decrements again → double count → premature fills (optimistic). `secondsDelay=1` for new orders not modeled
  (queue position read ~1 s early → optimistic).
- S2: own-order strip removes only the same-token copy; the mirrored copy (token1 asks at 1−p) stays as phantom
  depth (schedule maps only).
- S3: queue bookkeeping approximations (cap only lowers; delete→0 is actually correct: everyone ahead left).
- S3: gate_seconds are event counts (dup). S3/S4: cancel models one leg (85 ms), ack ~85 ms early.
- S4: TICK_SIZE hardcoded 0.01; engine does not enforce min qty; Gamma metadata fetched at replay (non-hermetic).
- Settlement tail: mechanically sound; all-winners is policy (= fill-grok F1). Mode-dependent: schedule maps go
  stale at 45 s and dump winners at the ask; grid_v1 never stales and parks winners to $1.
- Fees: sports rebate 15% applied in postprocess; all fills maker; liquidity rewards not modeled (conservative).
- Performance: quote-event O(N²) fixed; ~3 s/match; MAX_MATCHES_PER_BATCH=1; effective load fan-out 2.

## collect-devin (FINAL)

- F1 (S2 → S3 by prevalence, verified): GRID roster substitute 6th row (+600 NW constant) on 17/553 archives
  (~3%; feed-devin found 1/153 in its sample). Plus 1–2 degenerate all-zero table ticks on ~13% of sampled archives
  (~8/60): `_live_snapshot` emits nw_adv=0/xp=0/deaths=0 → a spurious model tick (can fire a wrong-side entry for
  up to one signal lifetime). Fix: require 5 rows per side (Oddin already does).
- F2 (S2 coverage): `series_state/*.json` write-once; 7 unfinished series (61 games) cached forever → those maps
  can never match.
- F3 (S2 coverage — biggest data-quantity lever): exact `clock == duration` GRID matching + ±8 h/±2 d prefilters lose
  1,763/4,950 clocked links (1,351 no GRID game in window, 412 clock ≠ duration) → never reach the catalog unless
  archive-attached. Largest single drop stage (1,746 of 5,046 links).
- F4 REFUTED as stated (my check): prepare does not crash — `exclude_missing_market_caches` drops them first.
  Train-era missing caches = 161 = `MAX_TRAIN_HARD_MISSES` exactly (validation 5/20): one more hole aborts the
  next prepare (S4 fragile constant; = history-devin note).
- F5 (S3): STRATZ unusable drops biased to long games (24 maps; p50 3512 s vs 2328 s), league-clustered.
- F6 (S3): prior anchors mixed (spawn vs archive horn); 4 degenerate priors (0.9995/0.0005) and 3 priors stale
  >600 s pass the gates; prices-history bucket semantics could leak ≤59 s post-anchor on horn-anchored rows.
- F7 (S3): STRATZ cache never refreshed; 13,734 dead v2 files (71% of cache); stale v2 on a new id would crash
  scan_cache; "unusable" verdicts permanent.
- F8 (S3): second −60 train row silently missing for 2,437/2,490 non-validation maps (no player playbackData);
  validation always has it → train/validation coverage differs at the prehorn slot.
- F9 (S4): ±1 s STRATZ vs GRID clock (dup). F10–F17 (S4): pause-diverge noise; 3 cache versions (455 MB);
  dead raw dirs (events 295 MB, markets 14 MB, stratz_matching 9 MB); GRID windows keep no series/game id;
  match_game raises on ambiguity; OpenDota 404s retried forever; STRATZ RICH_QUERY ~6× wider than used
  (1.4 GB, ~13.5 h fetch).
- OK: exact-second NW causal (367-point independent recompute); clock model consistent; NW definitions equal
  across STRATZ/GRID/Steam (±4 gold); deaths match; winner_conflict gate caught 1 real conflict.

## data-luna-followup (FINAL) — crossed/locked books → S3

- 156-map stratified sample: strict cross 0.046% of two-sided snapshots (paid 0.052%, collector 0.032%), locked
  0.066%. ok seconds using a crossed/locked quote: 0.18% (38 strict, 351 locked); training rows 0/434; validation
  current 0.23%, labels 0.37%; entry rows (≤489) 0.065% (15, all Sep collector).
- LIVE seed0 replay: 41 unstripped grid_v1 BUY fills ($1,599.61 notional of $143,351 total) came 0–161 ms after a
  strictly crossed ask below the resting limit (Nautilus fills marketable crossed L2 levels when queue allows).
- Recommendation: reject strict crosses (bid > ask) at raw ingest for BOTH market-seconds and the L2 replay; do
  not reject locks (bid == ask) — that filter moves 117 ok seconds to non-ok and shifts prices up to 95c.

## kernel-devin (FINAL) — kernel clean on the normal path; live-only trigger-gated wedges

- S2-1 → S3 (mechanism verified; 0 "unknown venue fill" lines in today's live log; core_state 0 wedges): unmapped
  venue fills park in `pending_ownership`; only dust-sweep resolves it → `ownership_unresolved` blocks all BUYs for
  the session; the orphan venue order is invisible to the core's cancel path. Trigger: poly-maker `place` swallows
  exceptions and returns [] even if the venue got the order.
- S2-2 (mechanism verified, frequency low): venue-side order termination (no own cancel in flight) never reaches
  LiveCore → dead BUY pins its rung; dead SELL holds the only exit slot (`sell_occupied`) → stranded position.
- S2-3 (mechanism verified; `block_reason:"anchor"` seen live: 204 evals grid-3008569-m1, 152 on 9013743409, …;
  share caused by own orders NOT measured): `anchor_p` = raw book mid incl. own orders (`match_worker.py:407-485`)
  vs kernel `book_p` = own-stripped mid → own top-of-book quote can trip the 1c anchor check → cancel all BUYs →
  re-place → oscillation on thin books. Backtest anchor = dataset mid (no asymmetry) → live-only.
- S2-4 = config drift: backtest $100/rung vs live $60/$200 (LoL $5 → 20×); `sell_after_game_end` true only in
  series backtests, live never sells post-game on series.
- S3-1: backtest adapter does not re-arm wake on OrderAccepted/OrderRejected → retries up to 2 s late vs ~100 ms live
  (conservative). S3-2 = cash never binds in backtest. S3-3 = Steam staleness 3 s vs 16 s (latent). S3-4: durable
  BUY reservations leak if a crash/sqlite fault hits between dispatch_started and results (clears on restart).
- S4: double reservation for one RTT; `_executes_plan` re-implements `should_evaluate` (2 arms missing, unreachable);
  dead RELEASE alerts; unused `durable_buy_reservation`; `SignalUpdate` without monotonic check.
- OK: pure step() kernel, cadence parity, fill dedup (2 layers), cancel self-healing, recovery, budget model,
  strip own orders, kill gate, mid-spike, SELL single-slot, adapter contract tests (14 scenarios) + replay goldens.

## live-devin (FINAL) + my VPS read-only check

- F1 claimed S1 → **S3 latent (verified mechanism, 0 occurrences)**: `consume_command` has no callers (grep);
  `_RESERVED_BUY_SQL` sums BUY commands with empty outcome across ALL sessions; `proof_blocks` returns
  `unknown_command` for dispatch_started+''/unknown. An exception between `await original_place` and
  `record_dispatch_results` (GatewayClosed at latch close, CancelledError on shutdown) would leak budget forever
  and wedge recovery. VPS `data/trader_live/wallet/live.db` (mode=ro): place BUY dispatched/accepted 11,988;
  SELL 9,068; BUY dispatched/unknown 72; SELL unknown 10; cancel canceled 20,401 / timeout 96; **no
  dispatch_started rows; reserved_buy_notional = 0**. Side effect that IS real: every command stays consumed=0 →
  `unresolved_commands` non-empty for any session that ever placed → every restart classifies the session as
  recovery (sell_only until proofs incl. on-chain balance RPC pass) (F12, S3).
- 82 place commands with outcome `unknown` (0.39%) = the trigger class for unbound venue orders (F3/F4).
- F2 (S2→S3): orphaned place/cancel results logged once; stuck pending/canceling SELL blocks the exit slot.
- F3 (S2→S3): unbound venue orders never cancelled intra-session (cleaned only at quiesce/quarantine).
- F4 (= kernel S2-1, S3): pre-binding fills → pending_ownership forever → buys closed for the session.
- F5 (S3, compound): user-WS exception consumes the event (no replay) → FAILED reversal lost → MATCHED wedge.
- F6 (S2 calibration): live latency: signal→place p50 166 ms / p90 479 ms / p99 7.2 s; place→accept p50 174 ms;
  total ~340 ms median vs backtest 85 ms. Contributors: debounce 100 ms, 2 s tick, per-record fsync, sqlite commits,
  model inference, REST backfill on the reconcile tick — all on one event loop.
- F7 (S2 decisions): paper gateway fills full size at own limit on any strict cross (no queue, no partials) →
  paper PnL optimistic by construction.
- S3: collateral_balance REST failure → budget 0 for ~20 s; positions() {} on error looks flat; REST open-order
  merge means REST can never prove an order gone; boot order verification suppressed; every halt pulls exit SELLs
  (daily_loss halt strands inventory to settlement); MINED treated terminal by backfill (later FAILED dropped);
  oversized backfilled SELL credits cash at full size; apply_and_persist rollback leaves ledger ahead of core;
  restart loses settle-window protection.
- S4: telegram daemon thread drops alerts on crash; quoter errors only logged; fills missing from tape when no
  outbox row; stale-cancel warns once then resends forever; resubscribe clears other markets' book cells.

## parity-luna-orders (FINAL) — no backtest over-fill

- 499 matched GRID BUY placements (29 maps / 25 series; ±30 s; same map/token/rung/queue bucket, price ±1c):
  backtest 21 filled vs live 50; first-fill rate 0.259 vs 0.648 /order-min; ratio 0.400 (CI 0.237–0.613); robust to
  ±15/±60 s; L0 0.365 (CI 0.203–0.560). SELL: 244 paired, live 24 vs backtest 7 filled (sparse, CI crosses parity).
- Queue at placement reconstructable: own-stripped raw depth within 1 share of backtest queue_ahead on 234/241.
- Broad traced BUY: live 128/1,357 orders filled (9.4%), backtest 75/1,818 (4.1%).
- No stable front-of-queue toxicity signature. Backtest BUY executions ~1c above as-of mid; live ~0.5c below.
- Verdict: the +0.51% vs +10.52% gap is NOT fill-model over-filling; drivers = model version (20.2% gate flips),
  PnL definition (live session-end mark vs settlement), exits, market path — not apportioned. Needs a same-model,
  corrected-aggressor, no-double-decrement replay.

## fill-grok-queue (first FINAL; variant requested) — queue sensitivity, with caveats

- Strict honest queue (ahead = displayed size at submit; only right-side prints and prior-snapshot size drops reduce
  it; fill-snapshot drop NOT counted): keep valid_empty + valid_cleared → PnL $119.75 / $39.79 / −$515.12 (engine
  $2,849 / $3,339 / $3,321). grid_v1 → −$401 / −$481 / −$1,036; schedule keeps $521 of $714 every seed. Trades-only
  variant: −$111 / −$101 / −$361.
- Caveats (mine): (1) its Q2 mechanism ("CLEAR per snapshot zeros the queue") is REFUTED (telonex.rs emits CLEAR
  once per window). (2) It drops 1,330 `touch_only` fills (~$22k buy notional/seed, 300 s markout +2.5c): an ask at
  our bid price in data without us implies the bid level was consumed in that snapshot → our resting order would
  likely have filled; so the strict model is too strict. (3) parity-luna-orders: backtest BUY fills 0.40× live at
  matched placements — stricter queue moves the backtest further from live. Variant with touch_only requested.
- Q3 (verified): verbatim taker_side vs the taker_asset rule disagrees on 1,637 fills: 1,196 missed decrements
  (560,771 shares) vs 461 phantom (296,080 shares) → mostly under-fill, matches my read.
- Useful: the backtest PnL is concentrated in marginal fills whose realism depends on queue assumptions; grid_v1
  part is the fragile part; schedule part is robust (~73% survives strict).
- fill-grok-queue FINAL (variant): keeping touch_only (1,298/1,330 have zero own-side size on the fill snapshot):
  $2,293.74 / $3,294.24 / $2,729.61 (+improve $2,327 / $3,331 / $2,736; fill-snapshot-as-cancel $2,411 / $3,460 /
  $2,792) vs engine $2,849 / $3,339 / $3,321 → 80–99% survives; grid_v1 stays positive; schedule $727–806 vs $714.
  Q2 restated (CLEAR per snapshot refuted). ⇒ fill model is NOT the main source of backtest optimism.
