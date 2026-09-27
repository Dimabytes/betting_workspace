# history-devin — bug archaeology, sibling hunt, leftovers
Status: FINAL

Scope: 3 months of `esports-trader` history (1,194 commits on `main`), ~190–300
fix-like commits depending on filter breadth, `docs/experiments`, `docs/analysis`,
`docs/as-is.md`, `docs/next_steps.md`, `docs/paper_review.md`, workspace
`.learnings`, prior audits under `betting_workspace/.analysis/*/`, and the
`codex-session` archive (grepped, not read whole). Product repo:
`/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` @ HEAD `main`.

## Summary

- **S2 — Dota market-second cache version hashes only 6 scalar parameters, not
  code.** `src/market_data/build_market_data.py:37-51`: `CACHE_VERSION` is a hash
  of `CACHE_BUSTING_PARAMETERS`; the cache contract is file existence
  (`db23b945`). A logic fix in `telonex_book.py`, `stratz_seconds.py`, horn/pause
  projection, or the builder itself that touches none of the 6 constants silently
  reuses stale `v<hash>/match_id=*.parquet` for both train and validation joins.
  LoL fixed exactly this failure mode 2026-09-19 (`00344982`, module-hash cache
  version); the Dota builder never got the sibling fix. Direct train-label risk.
- **S3 — `gate_*_seconds` in summary.json counts evaluated events, not seconds.**
  `src/backtest/results.py:159-169` increments once per `no_quote` quote event;
  evals are debounce/coalescing-driven (`scheduling.py`), not 1 Hz. Under archive
  feed schedules (irregular cadence) and 100 ms debounce the number is an event
  count wearing a duration label — it misleads "seconds blocked by X" analysis.
- **S3 — `delta_gate_open` is live strategy state that survives neither restart
  nor trace digest.** In `StrategyState` (`src/strategy/types.py:302`), absent
  from `StructuralCheckpoint` (`src/trader/core_persistence.py:143-164`) and from
  `digest_state` (`src/trader/core_trace_codec.py:221-247`). A mid-map restart
  silently rearms the entry hysteresis at 0.02 instead of the open-state 0.015
  floor; the parity harness cannot detect the divergence. Recorded 2026-09-20 in
  `.learnings/hysteresis-nw-readiness-20260920.md` (finding 3) — still unfixed.
- **S3 — `daily_loss_kill` is derived as 4× the clip sum, not the account.**
  `src/trader/session_config.py:150-158`: `daily_loss_kill_usdc=4.0`,
  `max_total_exposure_usdc=8.0` × Σ largest per-game clips (Dota $200 Oddin +
  LoL $5 ≈ $820 kill). `docs/next_steps.md` records "this stop will never fire;
  set it manually" — the code still derives it automatically. Risk control is
  weaker than the operator believes.
- **S3 — Live predicts past the trained signal window; backtest cannot.**
  `in_model_window` (`src/trader/session_quoting.py:94-100`) accepts any
  `second >= 0` while IN_PROGRESS; dataset signal rows stop at
  `VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE = 900`. Exits past 899 s consume model
  predictions far outside the train window (features at `second` > 600 are pure
  extrapolation) — live/backtest diverge in the exit tail.
- **S3 — Oddin live serve lag (~15 s) exceeds the trained lag contract (10 s).**
  `src/trader/game_profile.py:61-66` stamps the noxp satellite with
  `source_lag_seconds = TRAIN_LAG_SECONDS`; `model_server.py:266-272` only checks
  the meta contract — no delay is applied. Oddin's real feed delay ≈15 s
  (per `docs/experiments/oddin-no-xp.md` "exec 15"), so serve features are ~5 s
  staler relative to the market anchor than in training. The bundled experiment
  (noxp + exec-15 vs XP + exec-25) had a CI covering 0 — magnitude unproven.
- **S4 — Orphaned modules:** `src/trader/databet_client.py` /
  `databet_types.py` have zero importers after the Thunderpick/DATA.BET feed was
  superseded by Oddin/Disir. `watch_thunderpick_live.py` is self-contained.
- **S4 — `docs/as-is.md` is a stale snapshot** (dated 2026-09-04): names model
  `20260904T222047Z` / 1 464 production train matches and 1010/454 split counts;
  actual `data/new_model/production/model.json` is `20260924T183900Z` (2 225
  matches, spread-6 retrain `1e894b24`) and the local split is 1 563 / 717.
- **Already fixed at HEAD (do not re-report):** live self-join / own-book
  contamination (fixed `60e40a2c` + `283b43c0` + `86ae7cf8`, 2026-09-24);
  unconditional `exit_abs_delta=min_abs_delta` override (fixed, `run.py:400-423`
  now applies only explicit overrides); look-ahead tape filter (`1e894b24`);
  GRID-vs-Steam orientation (`cae35662`, `bc561b16`); dust/post-episode wedges
  (`8ae05d3b`, `7832466a`, `0017b8d1`).

## Findings table

| ID | Sev | Layer | Title | Confidence | Estimated impact |
|---|---|---|---|---|---|
| history-devin-F1 | S2 | market_data | Market-second cache version ignores code changes | verified | Stale train/validation labels after any logic fix without a constant change; silent, affects both dataset and all downstream numbers |
| history-devin-F2 | S3 | backtest | `gate_*_seconds` counts no_quote events, not seconds | verified | Gate-time attribution in every summary.json is an event count; archive-schedule runs misreport blocked time |
| history-devin-F3 | S3 | trader/strategy | `delta_gate_open` not checkpointed, not digested | verified | Restart rearms entry hysteresis at 0.02; trace parity blind to the bit |
| history-devin-F4 | S4 | strategy | Delta Schmitt gate samples on evaluated events only | verified | Close→reopen inside one 100 ms debounce window is invisible; needs an explicit semantics decision |
| history-devin-F5 | S4 | trader | Live predicts at `second ≥ 900`, outside the trained window | verified | Live-only exit tail uses untrained-domain predictions; backtest settles instead — tail divergence |
| history-devin-F6 | S3 | trader/config | Risk caps derive from clip sum, not account equity | verified | `daily_loss_kill` ≈ $820 on a ~$1 000 bankroll; documented as never-firing, still auto-derived |
| history-devin-F7 | S4 | trader | Dead modules `databet_client.py` / `databet_types.py` | verified | ~700 lines orphaned; confusing surface for future feed work |
| history-devin-F8 | S4 | docs | `docs/as-is.md` snapshot is 3 weeks stale | verified | Wrong model names/counts for anyone reading "as-is" state |
| history-devin-F9 | S3 | trader/train | Oddin serve lag ≈15 s vs trained lag 10 | likely | ~5 s extra state staleness on every Oddin-fed decision; bundled experiment CI covered 0 |

## Findings detail

### history-devin-F1 — S2 — market-second cache version ignores code changes

**Location:** `src/market_data/build_market_data.py:37-51`

```python
CACHE_BUSTING_PARAMETERS = (
    MAX_BOOK_AGE_SECONDS, PAIR_SUM_TOLERANCE, MAX_ENTRY_SPREAD_TICKS,
    30, MODEL_TARGET_HORIZON_SECONDS, MODEL_START_SECOND,
)
CACHE_VERSION = hashlib.sha256(repr(CACHE_BUSTING_PARAMETERS).encode()).hexdigest()[:8]

def market_seconds_cache_path(match_id: int) -> Path:
    return MARKET_SECONDS_DIR / f"v{CACHE_VERSION}" / f"match_id={match_id}.parquet"
```

**What is wrong.** The cache key is a hash of six scalar *parameter values*. The
cache contract is file existence ("Trust our own pipeline files: existence is
the cache contract", `db23b945`, 2026-09-04). Any change to the *logic* —
`find_asof_quote`, `resolve_market_pair`, `lookup_market_p_after`
(`src/shared/utils/telonex_book.py`), horn/OpenDota pause projection
(`get_state_available_ts`), or `build_market_data.py` itself — that leaves those
six values untouched produces the same `v<hash>` directory and silently reuses
parquets built by the old code.

**Mechanism / history.** This is a known bug class in this repo: the LoL
pipeline hit it and was fixed on 2026-09-19 — `00344982` "Derive the LoL
map-build cache version from imported src modules so missed files cannot stale
the dataset" hashes every imported `src/` module into the version. The Dota
builder (the sibling path in the same pipeline) still uses the parameters-only
scheme. Concrete precedent inside Dota: `1e894b24` (2026-09-24) added the
`wide_spread` gate to `resolve_market_pair` — it happened to bust the cache only
because the same commit changed `MAX_ENTRY_SPREAD_TICKS` 8→6 *and* added it to
the tuple. A logic-only change (e.g. fixing pair-sum tolerance handling, pause
projection, or the +300 s target lookup) would not have.

**Evidence.** `src/market_data/build_market_data.py:37-51` at HEAD; LoL sibling
fix `git show 00344982` (`src/lol/map_build_cache.py` `cache_version()` hashes
`sys.modules` src files); contract commit `db23b945`.

**Impact.** After the next market-cache logic fix, `make prepare` and every
validation/backtest will silently mix stale and fresh cache rows — train labels
and backtest market values keep the old semantics under a new model run. The
failure mode is invisible: identical paths, no version print unless logged.

**Confirmation / fix.** Port `cache_version()` from `src/lol/map_build_cache.py`
(hash imported src modules, or at minimum `build_market_data.py`,
`telonex_book.py`, `stratz_seconds.py`, the pause/horn helpers) into
`market_seconds_cache_path`. Cheapest interim: a `make` step that refuses to run
prepare when `v<hash>` is older than the newest mtime of those files.

---

### history-devin-F2 — S3 — `gate_*_seconds` counts events, not seconds

**Location:** `src/backtest/results.py:159-169`, `:144-146`; emitters
`src/backtest/strategy.py:923-928`, `:1198`.

**What is wrong.** `_count_gate_seconds` counts `no_quote` `QuoteEvent`s and
`gate_seconds_json` exports them as `gate_<reason>_seconds` in summary.json.
A `no_quote` event is recorded once per *evaluated* engine step with an empty
plan — evaluations are driven by `should_evaluate` (`src/strategy/scheduling.py:
121-134`): forced wakes, debounce deadlines, acks — not a 1 Hz ticker. Under the
seeded grid-v1 cadence the count approximates seconds; under archive feed
schedules (`src/backtest/feed_schedules.py`) ticks replay at the real feed
cadence and the count tracks ticks, not time.

**Evidence.** The docstring itself says "Count 1 Hz no_quote events" while the
emitter fires per eval; `.learnings/esports-trader-backtest-performance-2026-09-07.md`
flagged the same: "counters count no-quote events, not actual elapsed seconds,
despite being named `gate_seconds`". Verified unchanged at HEAD.

**Impact.** Every gate-attribution number in every summary ("N seconds blocked
by stale_signal / kill_gate / anchor") is wrong wherever feed cadence ≠ 1 Hz —
i.e. all archive-replay validation runs, which are the ones used for live
comparison. Decisions about gate tightening read these numbers.

**Confirmation / fix.** Either stamp `no_quote` events with the interval since
the previous eval for the same match and sum real seconds, or rename the field
`gate_<reason>_events`. One-line repro: count `no_quote` events vs
`sum(gap_seconds)` in one archive-scheduled match's quote-events file.

---

### history-devin-F3 — S3 — `delta_gate_open` lost on restart, invisible to digests

**Location:** `src/strategy/types.py:302` (state field, default `False`);
`src/trader/core_persistence.py:143-164` (`StructuralCheckpoint` — no field);
`src/trader/core_trace_codec.py:221-247` (`digest_state` — no field).

**What is wrong.** `delta_gate_open` is the persistent Schmitt bit of the entry
hysteresis: once |Δ| ≥ `min_abs_delta` (0.02) it opens and stays open while
|Δ| ≥ `exit_abs_delta` (0.015), letting later entries pass on the lower floor
(`src/strategy/signals.py:46-58`). The checkpoint restores rungs, orders,
inventory, episode flags — everything except this bit; after a restart the gate
reads closed even if it was open before the crash, so re-entries demand a fresh
0.02 crossing. `digest_state` also omits it, so the live/backtest parity harness
cannot detect the divergence it causes.

**Evidence.** Verified field lists at HEAD (both files). Reproduced in
`.learnings/hysteresis-nw-readiness-20260920.md` finding 3: "true restores as
false; states differing only in this bit have identical trace digests." Still
unfixed two weeks later.

**Impact.** Bounded but real: only matters when a restart lands mid-map while
the gate is open and a new episode would have entered on the 0.015 floor — the
window where the strategy is most likely to re-enter. And any future divergence
in this bit is undetectable by the trace tools.

**Confirmation / fix.** Decide the contract (rearm at 0.02 vs preserve), then
add `delta_gate_open` to `StructuralCheckpoint` (schema bump) and to the
`digest_state` episode digest.

---

### history-devin-F4 — S4 — Schmitt gate advances only on evaluated events

**Location:** `src/strategy/quoting.py:936-951` (`_advance_delta_gate` called
only from `requote`); `src/strategy/engine.py:121-128` (`requote` only under
`should_evaluate`); `src/strategy/scheduling.py:121-134`.

**What is wrong.** A `SignalUpdate` only opens the dirty window; the gate bit
advances on the next *evaluated* event (wake/ack/deadline). Two signals inside
one 100 ms debounce window are sampled once — a close-below-exit then reopen
inside the window never registers the close. The learning documents the exact
repro: an open gate seeing 1.4 then 1.8 cents in one window stays open, where a
per-signal Schmitt would close at 1.4.

**Impact.** Sparse production feeds make this rare; live and backtest share
`step()` so parity is preserved — this is a semantics gap, not a divergence.
It becomes wrong if feed cadence rises (Oddin pushes ~1 Hz + book-update wakes).

**Confirmation / fix.** Deliberate decision, then either move
`_advance_delta_gate` into the signal-store path (`_apply` on `SignalUpdate`) or
document the sampled semantics. From the same learning, finding 2 — unfixed.

---

### history-devin-F5 — S4 — live predicts past the trained signal window

**Location:** `src/trader/session_quoting.py:94-100`
(`in_model_window`: any `second >= 0` while IN_PROGRESS, no upper bound);
`src/shared/constants/dataset.py:19` (`VALIDATION_SIGNAL_END_SECOND_EXCLUSIVE =
TRAIN_END_SECOND_EXCLUSIVE + MODEL_TARGET_HORIZON_SECONDS = 900`).

**What is wrong.** Live predicts and drives exit deltas at any positive game
second while a map is in progress; the model trained on features at seconds
[-60, 600) and the dataset emits signals only to 899. Backtests therefore never
replay an exit decision beyond 899 — held positions settle at the terminal mark
— while live keeps extrapolating `predict_fair` at `second` values the model has
never seen (`second` is itself a feature).

**Impact.** Tail-only: entries stop at `BUY_CUTOFF_SECOND = 480`, so this affects
exits of positions still open at 900 s — rare (map usually decided), small money.
But it is a genuine live-vs-backtest divergence: live can sell on a
model-computed delta where the backtest would settle.

**Confirmation / fix.** Either cap `in_model_window` at `VALIDATION_SIGNAL_END`
(symmetric, dataset is the contract) or extend dataset signal rows so backtest
covers the live exit window; whichever is intended, make them equal.

---

### history-devin-F6 — S3 — risk caps derive from clip sum, not account

**Location:** `src/trader/session_config.py:150-158` (`DOLLAR_MULTIPLES`),
`:103` (`risk | _dollar_limits("risk", total_base_size_usdc)`);
`config/trading.toml:32-35`, `:51-53` (clips $60 / $200 / $5).

**What is wrong.** `daily_loss_kill_usdc = 4.0 ×` and
`max_total_exposure_usdc = 8.0 ×` the sum of each assigned game's *largest* clip
(Dota contributes $200 from the Oddin satellite, LoL $5 → kill ≈ $820,
exposure ≈ $1 640). The values are materialized into the frozen engine's
`[risk]` table — they are live enforcement, not decoration. `docs/next_steps.md`
records the operator's own note: "daily_loss_kill = 4 × sum of clips … this stop
will never fire; set it manually" — the code still derives it automatically and
there is no account-size input anywhere in the chain.

**Impact.** On a ~$1 000-class bankroll the kill switch permits losing most of
the account before tripping. Adding a bigger clip (e.g. a deeper-market
experiment) silently loosens every risk cap without any review of the bankroll.

**Confirmation / fix.** Add an explicit absolute cap (or account-equity input)
in `[risk]`; at minimum print the derived caps at startup so the looseness is
visible. Needs the actual account size from the VPS to pick the number.

---

### history-devin-F7 — S4 — dead DATA.BET modules

**Location:** `src/trader/databet_client.py` (508 lines),
`src/trader/databet_types.py`.

**What is wrong.** Nothing under `src/`, `scripts/`, `tests/`, `Makefile`, or
`config/` imports them after the Thunderpick/DATA.BET path was replaced by the
Oddin feed and the Disir discovery work (`dec98290..bbb28897` cluster).
`scripts/watch_thunderpick_live.py` is self-contained (its own websocket code,
no databet import). Verified: `grep -rln "databet" src/ --include='*.py'`
returns only the two files themselves.

**Impact.** Dead weight on the trader package; a future reader can mistake it
for the live feed path.

**Fix.** Delete both, or move under `docs/experiments/` if the DATA.BET probe is
a kept artifact like the teacher scripts.

---

### history-devin-F8 — S4 — `docs/as-is.md` is materially stale

**Location:** `docs/as-is.md` (whole file, snapshot 2026-09-04).

**What is wrong.** It names production `20260904T222047Z` (1 464 train matches)
and the 1010/454 research split. Actual at HEAD:
`data/new_model/production/model.json` → `name=20260924T183900Z`,
`train_matches=2225`, `source_lag_seconds=10` (the spread-6 retrain from
`1e894b24`); local split train 1 563 / validation 717. It also claims VPS `sun`
runs `20260825T193532Z` — unverifiable locally, flagged as an open question.
The doc *does* label itself a snapshot, so this is drift rather than a lie, but
it is the canonical "current state" doc a reviewer reaches first.

**Fix.** Either regenerate on each refresh commit or retitle with the date in
the filename. Cheap guard: the doc's counts vs `match_catalog` row counts drift
by >10 % — a script could flag it.

---

### history-devin-F9 — S3 — Oddin serve lag vs trained lag contract

**Location:** `src/trader/game_profile.py:61-66` (satellite
`source_lag_seconds=TRAIN_LAG_SECONDS`); `src/trader/model_server.py:258-272`
(the check is meta-only — no delay is applied to snapshots);
`docs/experiments/oddin-no-xp.md:5,14` (Oddin real delay treated as exec 15).

**What is wrong.** Training joins market second `m` to state `m−10`
(`join_validation_rows`). Live, a feed snapshot arrives carrying game state at
its publish time while the market is now — the effective lag equals the feed's
real delay. GRID ≈ the trained 10 s; Oddin ≈ 15 s (the reason the experiment
used "exec 15"). Every Oddin-fed decision therefore sees state ~5 s older
relative to the market anchor than the model was trained for — systematic
serve skew on the source that carries the $200 clip.

**Impact.** Unmeasured in isolation. The only data point is the bundled
experiment (noxp + exec-15 vs XP + exec-25): +$150 pre-rebate on 556 maps with
cluster-bootstrap 95 % CI [−$1.12, +$1.67] per map — noise. Direction suggests
slightly pessimistic deltas (staler state under-reads the current move), bounded
small by the 300 s horizon.

**Confirmation / fix.** Measure real Oddin arrival-vs-game-second lag from the
archived schedules (`schedule.ticks` carry `received_ns` and `game_second` — one
afternoon), then either retrain noxp at lag 15 or accept and document.

## Historical taxonomy

Fix-like commits: 303 match the broad filter (`fix|bug|hotfix|regress|correct|
broke|wrong|crash|miss|stale|wedge|leak|skip|drop|guard|race|mismatch|align|
clamp|snap|rescue|recover|restart|flake`); ~190 classify into the classes below
(~16 % of all commits are fixes — high but normal for a live-trading repo).

### Class table

| Class | Count | Root-cause pattern | Sibling hunt @ HEAD |
|---|---|---|---|
| train_data | ~32 | Feature/label join computed differently for train vs live (net worth source, window boundary, missing-cache caps) | Join code now shared/`is_ok` status-gated; **F1** leaves stale-label risk on cache rebuild |
| crash_robustness | ~29 | Watchers/readers assumed fields/daemons exist; restarts counted as feed failures | Feed-wait vs corruption split (`ee2917ef`), draft feed wait (`65e1b8dc`), fd counting (`e1d129ff`) — patterns hold, no sibling found |
| silent_drop | ~27 | Rows/matches skipped without a reason surfaced | `exclude_missing_market_caches` logs+aborts over caps; archive exclusions log reasons; `join_validation_rows` raises. OK |
| feed_parse_gaps | ~23 | Feed frames arrive partial, out of order, or spliced | `oddin_feed` rejects OOO/dupes; `recovery_stale_mask` mirrors live stale budget; OK |
| cache_stale | ~19 | Cache identity didn't cover what actually produced the bytes | **F1** — LoL got `00344982` module-hashing, Dota builder still parameters-only |
| lookahead_selection | ~13 | Match/row admission peeked past the decision time | Tape filter dropped (`1e894b24`), both catalogs retrained; `select_matches` admits all catalog entries; OK |
| discovery_binding | ~12 | Match bound to wrong market/side or bound too early/late | Aliases both directions (`team_names.py:108-111`), `archive_unlinked`/`schedule_identity_mismatch`/`assert_archive_binding` fail loud; OK |
| time_lag_join | ~11 | "Now" for market ≠ "now" for state | As-of quotes everywhere (5 s cap), lag joins symmetric in train/validation; residual **F5**, **F9** |
| order_state/wedge | ~10 | Fills/cancels arrived for states the machine thought impossible | Dust-FAK bind, post-episode unwedge, closed-episode block all in; residual **F3** (unpersisted gate bit), **F4** (gate sampling) |
| side_orientation | ~9 | Radiant/Dire ↔ YES/NO mapping taken from the wrong source or order | `outcome_0_is_radiant`/`yes_is_radiant` carried, fail-closed orientation; OK |
| config_metadata | ~6 | Docs/config no longer describe the code | **F6** (risk caps vs next_steps), **F8** (as-is.md) |
| accounting_pnl | ~5 | Ledger counted fills it shouldn't or missed fills it should | `fde4e5e2` covers the Aug-20–28 unjournaled-sell cause; reconcile recommendation still unimplemented (see Open questions) |

### Representative commits per class

**train_data (32).**
- `e6be219c` 2026-07-25 — train/live net-worth mismatch: use STRATZ `networthPerMinute`.
- `9d71f6d3` 2026-09-03 — fix Dota training at the 300-second window.
- `d85d09f6` 2026-09-10 — cap missing train market caches same as validation.
- Pattern: training joins silently diverged from live semantics until measured.

**time_lag_join (11).**
- `216e9c9c` 2026-08-25 — look back up to 10 s for live 30 s gold velocity.
- `c9fb5cd2` 2026-09-19 — retrain LoL on contemporaneous mid after the T+10 fix.
- Pattern: assumed-same timestamps across market and game-state clocks.

**side_orientation (9).**
- `cae35662` 2026-08-25 — pin GRID orientation to market names, not Steam labels.
- `bc561b16` 2026-08-25 — take live map number from GRID, not Steam 0-0.
- `da188f66` 2026-08-27 — entry gate picks yes/no, not a venue token id.
- Pattern: two enumeration orders (venue token order vs faction) used interchangeably.

**lookahead_selection (13).**
- `1e894b24` 2026-09-24 — drop look-ahead tape filter, retrain on ≤6-tick books.
- Pattern: admission predicates computed over data not available at decision time.

**cache_stale (19).**
- `00344982` 2026-09-19 — LoL cache version from imported src modules.
- `f0f47128` 2026-09-20 — archive-index stale-file sweep + universe fingerprint.
- `f282290f` 2026-08-09 — close cache-staleness and horizon-duplication review findings.
- Pattern: "file exists" treated as "file is current". **Sibling live: F1.**

**order_state/wedge (10).**
- `8ae05d3b` 2026-09-21 — bind dust FAK venue id so its fill does not wedge buys.
- `7832466a` 2026-09-21 — unwedge buys after post-episode dust sell.
- `0017b8d1` 2026-09-23 — block a new buy episode while a closed one holds shares.
- `b27556be` 2026-08-26 — latch unknown cancel, occupy only while resting.
- Pattern: venue events outside the modeled transition set wedge the episode.

**feed_parse_gaps (23).**
- `14a899e3` 2026-09-19 — resync broken PGL watcher splice; reject out-of-range deletes.
- `4b0de465` 2026-08-25 — read `series_table` without `increaseLevel` yet.
- `0e40b87c` 2026-09-21 — parse string `steam_match_id` from archive index.
- `4037fc38` 2026-08-25 — probe GRID delay from scoreboard so socket opens in draft.
- Pattern: feeds deliver partial/late/reordered frames; parsers assumed complete.

**discovery_binding (12).**
- `f434e36b` 2026-08-25 — mint GRID ids without Steam, bump match.json to v3.
- `180b92d7` 2026-08-25 — score team aliases in both directions.
- `f29f9f42` 2026-08-25 — wait for GRID sides on the next map; one task per condition.
- Pattern: binding raced ahead of the identity evidence.

**crash_robustness (29).**
- `65e1b8dc` 2026-08-25 — wait for feed during draft instead of exhausting crash restarts.
- `ee2917ef` 2026-08-25 — split feed-wait from feed corruption in picker skip.
- `bc7788a9` 2026-08-27 — smoke fails in one place so the fence cannot be skipped.
- `e12e5e32` 2026-08-28 — LoL Stage 02 pool leak and Stage 07 replay input edges.

**silent_drop (27).**
- `f93f19de` 2026-09-03 — keep `resume_exit` only on restart-mismatch fixtures.
- `d85d09f6` 2026-09-10 — (also) missing-cache caps, loud abort over cap.
- Pattern: absence looked like "nothing to do"; fixes add reasons and counters.

**accounting_pnl (5).**
- `fde4e5e2` 2026-09-05 — REST fill backfill accounting; drop in-memory archive cache
  (FAILED/RETRYING trades no longer booked as confirmed; late fills find journals).
- `c65c5ee2`/`5b6eb3d6` 2026-08-25 — half-tick float residue / 2-dp SELL snap.
- Ghost-fill audit (2026-09): 12 unjournaled SELL legs ~$115 Aug 20–28 — pre-fix era;
  root cause covered by `fde4e5e2`. Periodic journal↔Data API reconcile is a recorded
  recommendation, still unimplemented.

**config_metadata (6).**
- `4037fc38` 2026-08-25 — probe GRID delay from scoreboard.
- Pattern: constants/docs drifted from measured reality. Siblings: F6, F8.

**Reverts and residue.**
- `74d96e2a` 2026-09-25 — revert teacher catalog integration (Dota teacher = noise);
  scripts remain under `docs/experiments/dota-teacher/` (1 062 lines incl. `code.patch`
  that restores the integration) — labeled "drop, code dropped". Acceptable residue.
- `59550f2b` 2026-09-11 — revert E2–E4 gate experiments.
- `6c26543d` 2026-09-11 — revert map-first-entry/depth-cap knobs.
- `7b0d05d6`/`54cf56cd` 2026-09-02 — anti-flicker fixes later implicated in the
  self-join finding; final fix landed `60e40a2c`/`283b43c0`/`86ae7cf8` (2026-09-24).
- `6ba30b63` 2026-09-19 — drop Telonex `trades` channel → `onchain_fills` (note:
  local `trades` data ends 2026-09-17; onchain publishes closed days only — see
  Open questions).

## Hotspots

Fix-commit density per file (broad filter, 193 commits touching files —
artifact: `work/history-devin/hotspots.txt`):

```
  27 PROJECT_LOG.md
  23 src/backtest/run.py
  21 docs/live-paper.md
  13 src/trader/session_core.py
  13 src/live_paper/wallet_host.py        (deleted subsystem)
  13 tests/test_live_paper_wallet_host.py (deleted subsystem)
  12 src/backtest/strategy.py
  12 tests/test_backtest_validation.py
  10 src/prepare_dataset/prepare_dataset.py
  10 scripts/model_pipeline/shared.py     (deleted subsystem)
   9 tests/test_backtest.py
   9 tests/test_trader_session_core.py
   9 src/backtest/signals.py
   9 src/train_model/train_model.py
   9 tests/test_prepare_dataset.py
   9 docs/domain.md
   9 src/live_paper/discovery.py          (deleted subsystem)
   9 scripts/model_pipeline/README.md     (deleted subsystem)
   8 src/backtest/results.py
   8 tests/test_live_paper_session.py     (deleted subsystem)
```

Reading: the live defect mass sits in `src/backtest/run.py` (2 223 lines at
HEAD — top code hotspot by both fix count and size) and `src/trader/session_core.py`
(order/fill state machine — same class as the wedge fixes). Three of the top-15
are the deleted `live_paper`/`model_pipeline` first-generation stack — that
churn already aged out. Docs hotspots (`PROJECT_LOG`, `live-paper`,
`docs/domain`) are chronic lying-doc risk (see F8).

## Architecture / performance / debuggability notes (ranked)

1. **`src/backtest/run.py` is a 2 223-line monolith and the #1 fix hotspot.**
   Selection, feed plans, kernel configs, Nautilus compatibility shims,
   manifests, sharding and reporting all live in one file. Every fix commit
   there has maximum blast radius; splitting plan-selection, kernel wiring and
   reporting into modules is the highest-leverage refactor for debuggability.
2. **Two cache-version schemes coexist.** LoL hashes imported modules; Dota
   hashes six scalars (F1). One utility should serve both.
3. **Strategy-state parity is opt-in per field.** `delta_gate_open` shows the
   pattern: new state fields silently miss the checkpoint and the digest (F3).
   A unit test asserting every `StrategyState` field is either checkpointed or
   explicitly transient would kill the class.
4. **Metrics that say "seconds" but count events** (F2) — and in general,
   gate/block telemetry is keyed by event count while operators reason in time.
   Duration attribution needs timestamps, not counts.
5. **Window asymmetry between live and dataset** (F5): the model contract says
   [-60, 900) but live admits `second ≥ 0` unbounded — the dataset is the more
   restrictive contract and should be the bound.
6. **Deleted subsystems still dominate hotspot history** — `src/live_paper/`,
   `scripts/model_pipeline/` are gone; the churn they absorbed is why
   `wallet_host.py`/`session_core.py` got hardened. Current `src/trader/` is the
   second generation of the same bugs.
7. **`MAX_TRAIN_HARD_MISSES = 161` is pinned exactly at the known hole size**
   (`src/shared/constants/dataset.py`). Loud-by-design, but each new collector
   gap requires editing the constant — consider counting *distinct missing days*
   instead of maps.
8. **In-flight work visible in the tree:** `src/backtest/series_run.py` and
   `tests/test_series_run.py` are dirty, `src/strategy/series_c.json` landed
   today (`3a804f01`), two untracked `*_series-line` result dirs under
   `data/backtests/` — series-signal work in progress, not residue. Left
   untouched.

## Checked and OK

- **Side orientation:** `grid_feed` resolves board sides via `read_board_sides`,
  fails closed when names cannot orient, and carries `outcome_0_is_radiant`;
  `discovery` prefers GRID orientation over Steam names; `oddin_feed` projects
  teams by explicit faction and preserves `yes_is_radiant`. `team_names.py:
  108-111` scores probes both directions (`180b92d7` fix intact).
- **Lag joins:** train `build_minute_rows` uses `state.second + lag`; validation
  maps `market_second - lag` and *raises* on a missing lagged state; market cache
  resolves as-of quotes with `MAX_BOOK_AGE_SECONDS = 5.0`, pair-sum and spread
  gates; `lookup_market_p_after` never interpolates future data.
- **Look-ahead admission:** `select_matches` admits all catalog entries; the
  only exclusion is `backtest_book_gap_excluded` recorded per-match in split
  rows and enforced in `src/backtest/selection.py` — explicit, not silent.
- **Live/backtest exit-stale parity:** `build_kernels` wires per-source
  `entry_stale_s` (`entry_stale_seconds(binding)`: 15 s Oddin / 16 s GRID) and
  the shared 45 s `exit_stale_s`; archive-schedule staleness uses the same
  constants.
- **Self-join fix verified:** `books_from_md` → `_stripped_token_book`
  subtracts own resting sizes per price level (`session_core.py:227-275`,
  `host_resources.py:92`); `lonely_l0_s = 3.0` is on for production policies
  (`policy.py:74`, `86ae7cf8`).
- **`exit_abs_delta` override bug fixed:** `run.py` applies overrides only when
  supplied; factory policy exits 0.015 reach the kernel (learning finding 1 —
  closed).
- **Order wedges:** dust-FAK venue binding, post-episode dust-sell unwedge,
  closed-episode-with-shares block all present; `entry_delta_blocked`,
  kill-gate, mid-spike, lonely-L0 all operate on stripped books.
- **Archive binding fails loud:** `archive_unlinked`, `schedule_stale`,
  `schedule_identity_mismatch`, `schedule_fingerprint_mismatch` are explicit
  exclusions; `assert_archive_binding` hard-fails a lost link — no silent
  grid-v1 fallback.
- **Model contract pinning:** `model_server.py:258-272` rejects a model whose
  `source_lag_seconds`/features don't match the catalog; journal pins model
  name+trained_at and goes archive-only on mismatch (`match_worker.py:295-298`).
- **Nautilus zero-fill:** recorded `terminated_early` + `nautilus_zero_fill`,
  excluded from aggregates (`lol_inputs.py:50`, `postprocess.py:457`,
  `report_capital.py:94`) — documented upstream defect, handled.
- **Teacher revert residue is labeled:** `docs/experiments/dota-teacher/README.md`
  says "drop, code dropped", names `74d96e2a`, and `code.patch` restores it —
  intentional archive, not junk.
- **`VALIDATION_START_TIME` fixed split still appropriate:** chronological
  boundary at 2026-06-04 keeps research train frozen; validation has grown to
  717 maps (≈4 months). No re-leak risk introduced by the time-based rule itself.
- **Rounding:** `share_floor`/`floor_to_grid`/`ceil_to_grid` carry the 1e-6 /
  1e-12 epsilons; `drop_share_residue` zeroes sub-tick dust — the `5b6eb3d6`/
  `c65c5ee2` class is closed.

## Open questions / Needs from VPS

1. **Which model does `sun` actually run?** `as-is.md` claims
   `20260825T193532Z` (2 510 matches); local production is `20260924T183900Z`
   (spread-6 retrain). If VPS still runs the 08-25 pair, live trades on a model
   trained before the look-ahead-filter and spread-gate fixes — that would
   upgrade F-adjacent risk materially. Check `data/new_model/production/model.json`
   on `sun`.
2. **Post-fix live-vs-replay gap.** The 2026-09-24 audit measured Dota live
   +$42 vs replay +$299 pre-`60e40a2c`. Re-running the replay comparison on
   archives after 2026-09-24 shows whether own-book strip + lonely-L0 closed the
   gap or a residual (queue model on thin books) remains.
3. **Journal↔Data API reconcile** (ghost-fill audit recommendation) is still
   unimplemented — no periodic check that journaled fills match on-chain
   activity. Cheap to confirm whether drift has recurred post-`fde4e5e2`.
4. **Telonex `trades` channel ends 2026-09-17** (`6ba30b63` moved backtests to
   `onchain_fills`; collector publishes closed days only). Any pre-horn gate
   consuming `last_trade_price` needs a live source — collector journal on the
   trader host is the noted path; confirm the mount exists on `sun`.
5. **Account equity for F6** — the derived kill ≈ $820 needs the real bankroll
   to say whether "never fires" is accurate today.
6. **Oddin lag measurement for F9** — quantify arrival-vs-game-second lag from
   archived `schedule.ticks` to size the 5 s skew claim.
