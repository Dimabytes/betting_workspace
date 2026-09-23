# feat-devin — LoL feature-parity audit: train vs backtest vs live

Audit of the 12 model features across the LoL training/dataset path, the archive
backtest, and the live GRID trader. Scope: definition, units, Radiant/Dire
orientation, missing-value behavior, and timing. Read-only research; no code
changed. Evidence scripts and outputs live in `../work/feat-devin/`.

## Verdict

**Feature values are in parity; the feature/market pairing is not.** All
state-derived features (net worth, XP, deaths, top-player) are computed
consistently between the livestats training path and the live GRID path —
verified both by code inspection and by replaying 9 recent live tapes through
the production `GridFrameReducer` and diffing against the livestats
reconstruction of the same maps (median |Δ| on NW ≈ 20–105 gold ≈ ~0.5–1.5%,
deaths exact, ratios exact within rounding).

The real defect is **temporal**: the training pipeline pairs each state row
with a market mid sampled at the *same wall instant* (lag 0), while live pairs
a market mid sampled at decision time with GRID table content that is
~10.5–15 s old. Backtest sits between the two (gap ≈ 8 s). Commit `332e1c17`
("Join LoL prepare current mid at as-of 0, not source lag") is the change that
introduced the train↔live mismatch; its premise — "the live quote already sits
on the GRID clock" — is false: the live quote sits on the *decision wall*,
~10+ s ahead of the table content the features describe. Dota still trains at
state+10 s, which approximately matches its live gap. This is the strongest
identified asymmetry between the games.

Secondary, smaller mismatches: (a) live admits out-of-domain `second` values
that training never sees (negative seconds via the shared PRE_HORN −60 floor,
and unbounded seconds past the 540 training window); (b) `market_radiant_prior`
comes from different sources train (Telonex book mids) vs live (Polymarket
minute bars), measured |Δ| up to ~5c and occasionally absent in training;
(c) GRID `increaseLevel` permanently undercounts levels after feed holes,
corrupting `radiant_xp_adv` for the rest of the map (observed ~1.4
levels/player deficit → +580 median xp_adv error on grid-3000372-m4);
(d) `source_lag_seconds` metadata says 10 everywhere while the LoL join uses 0 —
the enforced contract is stale.

## The timing model

Every model input is a pair: a **game state** (features 1–10) and a **market
quote** (features 11–12). The three pipelines pair them at different relative
times. Notation: a game-time is seconds since spawn/horn-anchor; a mid "at
game-time T" is the book at the wall moment when the game clock read T.

### Training (`src/lol/05_prepare_dataset.py`, `src/lol/livestats_frames.py`)

- Frames: lolesports window/details payloads; spawn anchored via
  `loading_anchor_ts`; spawn-relative `game_time` with pauses subtracted.
- `select_grid_rows` picks the latest frame with `game_time <= second`, rejects
  it if `second - game_time > 2` (2 s age gate), and records the frame's wall
  stamp as `state_wall_us`.
- **Current mid**: `lookup_market_p_after(radiant_book, dire_book,
  slot.state_wall_us, 0)` — `05_prepare_dataset.py:310`. Lag **0**: the mid is
  at the state's own wall time. Telonex book series.
- **Label**: `lookup_market_p_after(..., state_wall_us,
  LOL_TARGET_HORIZON_SECONDS)` at `:316–322` — mid at state+300 s.
- Prior: strict Telonex prior at spawn anchor.
- ⇒ Pairing: **(state@S, mid@S)** — gap 0.

### Live (`src/trader/grid_feed.py`, `grid_widgets.py`, `match_worker.py`, `model_server.py`)

Two GRID widget services: `series_scoreboard_v2` (zero-delay clock, kills,
sides) and `series_table` (per-player NW/KDA/level, declared `feed_delay = 8`).

- At decision wall T: `second = live_clock_seconds(board, age) -
  table.feed_delay` — `grid_feed.py:172`. The label S is the scoreboard clock
  at receipt minus the declared 8 s.
- Table content age is the *actual* end-to-end delay δ, not the declared 8.
  Measured via death-step alignment between GRID labels and livestats
  game_time: `grid_label − livestats_second = +2.4…+3.1` on 9 recent tapes and
  +6.7 on the 2026-08-29 dual-feed capture ⇒ **δ ≈ 10.4–11.1 s recent, ~14.7 s
  in August** (δ = 8 + offset). Live feature content at label S describes
  game-time ≈ S − (δ−8) ≈ S − 2.4…6.7.
- **Current mid**: engine YES/NO best bid/ask at decision time, pair-normalized
  (`normalize_pair_mids`, tol 0.05), oriented by `yes_is_radiant` —
  `match_worker.py`. Mid at wall T ↔ game-time ≈ S+8.
- **Prior**: Polymarket `prices-history` minute bars strictly before
  `horn_unix_seconds − 90` (`HORN_OFFSET_SECONDS`), YES/NO bars aligned ≤30 s,
  oriented, normalized — `market_prior.py`.
- ⇒ Pairing: **(state content ≈ S+8−δ, mid ≈ S+8)** — gap ≈ δ ≈ 10.4–14.7 s.

### Backtest (`src/backtest/signals.py`, `lol_inputs.py`, `marks.py`)

- Archive schedule ticks are recorded GRID feed arrivals: `tick.game_second`
  (same label rule as live) + `tick.received_ns` (wall time of arrival,
  ≈ spawn+S+8).
- Feature values: `game_features.parquet` row at exactly `game_second = S` —
  i.e. the **fresh livestats state at S**, not the delayed GRID content live
  saw (`signals.py:336`).
- `market_p_radiant` is **overwritten** with
  `lookup_reference_mid(mid_series, tick.received_ns)` (`signals.py:339,397`);
  the mid series is indexed by `state_ts_us` = wall-of-game-second
  (`lol_inputs.py:147–172`), so the anchor mid is at game-second ≈ S+8 — the
  same "mid at decision wall" as live.
- Prior: the parquet's Telonex prior (same as training, not the live source).
- ⇒ Pairing: **(state@S fresh, mid@≈S+8)** — gap ≈ 8 s by construction.

### Summary of the pairing

| pipeline | state game-time | mid game-time | state→mid gap |
|---|---|---|---|
| train  | S (±≤2 s frame age) | S | **0 s** |
| backtest | S (fresh) | ≈ S+8 | **≈ 8 s** |
| live | ≈ S+8−δ (δ−8 ≈ 2.4–6.7 s stale vs label) | ≈ S+8 | **≈ δ ≈ 10.4–14.7 s** |

Three different input distributions. The model learned P(mid_{t+300} − mid_t |
state_t, mid_t) on **contemporaneous** pairs; live hands it a mid that already
moved ~10–15 s past the state. Market moves in the gap window are inside
`market_p_radiant` but absent from the state features, so the model evaluates
(state, mid) combinations that never co-occurred in training — e.g., "state
shows pre-kill values, mid already jumped". The sign of the resulting delta
bias is not provable by inspection; it is a systematic off-manifold input, and
the fix direction (train the mid at state+δ, like Dota's +10) is clear.

Backtest is not identical to live either: it pairs a **fresh** state@S with the
same receipt-time mid, so its gap (8 s) understates live's (δ) and it gives the
model a *fresher* state than live ever sees. Backtest is closer to live than
training is — but it measures a third regime, not live.

## Feature-by-feature audit

Contract: 12 features in fixed order (`FEATURE_COLUMNS`,
`src/shared/utils/gbm.py:37`), enforced by model.json feature validation
(`model_server.py:264`). Production model `20260921T095813Z`:
`source_lag_seconds=10`, `state_source=lolesports_window_details_grid_networth`,
`xp_source=level`, `train_matches=3825`.

### 1. `second`

- Definition: integer game second, spawn/horn-anchored. Train: lolesports
  `game_time` after pause subtraction. Live: `scoreboard_clock − feed_delay(8)`
  truncated to int (`grid_feed.py:172`). Backtest: recorded tick's
  `game_second`.
- Units/orientation: seconds; n/a.
- Missing: training drops slots with frame age >2 s (holes in the feature
  parquet — backtest skips ticks with no row, `signals.py:336–338`). Live: no
  gate on value quality beyond feed freshness.
- **Mismatch (domain)**: live `in_model_window` admits `PRE_HORN` seconds in
  `MODEL_START_SECOND..−1` = **−60..−1** (`session_quoting.py:96–97`) — the
  shared Dota constant. LoL training starts at 0 (`LOL_GRID_START_SECOND=0`),
  so negative-second inputs are out-of-domain. Observed live `reason='model'`
  decisions at second −43…−5 on 6+ maps (e.g. grid-3007261-m2 emitted deltas of
  +0.9…+2.8c at seconds −43…−9). No fills at negative seconds in sampled tapes
  — deltas stayed under the edge gate — but the model *did* quote-space
  evaluate OOD inputs. Backtest does **not** admit negatives:
  `MODEL_WINDOWS["lol"] = range(0, 540)` (`schedule.py:63`).
- **Mismatch (domain, upper bound)**: `IN_PROGRESS` admits any `second >= 0`
  with no cap (`session_quoting.py:98–99`). Live predicts past the 540
  training window (signal rows observed at second ~1500+); entries stop at
  `BUY_CUTOFF_SECOND=480` but fair/exit logic still consumes OOD deltas.
  Backtest caps at `last_feature_second` (`signals.py:334`).

### 2–4. `radiant_nw_adv`, `radiant_nw`, `dire_nw`

- Definition: train reconstructs consumed-item net worth from livestats
  details frames (≈ totalGold − consumables); live reads GRID `series_table`
  NetWorth cells. Empirically the **same quantity**: GRID nw − raw totalGold
  median −230…−475, but GRID nw − reconstructed nw median −61…+50 across 9
  maps (`work/feat-devin/compare_tape_livestats.out.txt`). nw_adv = radiant−dire
  in both.
- Units: integer gold. Orientation: Radiant = Blue in both paths (LoL profile
  `side_0_text="BLUE"`, `side_1_text="RED"`; training parses blue side as
  radiant; market orientation via `radiant_token_index`/`yes_is_radiant`
  flips correctly map-to-map — verified on linked maps).
- Missing: live cells use `value or 0` — a null cell would silently read 0.
  31-tape scan: 0 null NW/death/kill cells, all tables 5v5
  (`tape_health_scan.out.txt`). Benign today, fragile by construction.
- Timing: state-side — same δ story as above.
- Verdict: **parity within noise** (~±50–105 median |Δ|, i.e. sub-1% of
  typical NW).

### 5. `radiant_xp_adv`

- Definition: cumulative XP via `LOL_LEVEL_XP` thresholds from player level;
  adv = radiant−dire (`src/shared/utils/level_xp.py`). Train level = livestats
  `level`. Live level = `increaseLevel + 1` (`grid_widgets.py` table parse);
  absent column ⇒ level 1, which is correct pre-first-level-up.
- **Failure mode**: `increaseLevel` counts *observed* level-up events, not true
  level. A table/socket hole during a level-up permanently undercounts. On
  grid-3000372-m4 a 173 s feed hole (S=147→250) left GRID ~1.4 levels/player
  low → `xp_adv` median error **+580** for the rest of the map
  (`compare_tape_livestats.out.txt`, `drill_levels_m4.py`,
  `stall_analysis.py`). grid-3000375-m2 shows a similar persistent +380 median.
  Other maps: median 0, but p90 |Δ| 480–1360 — heavy tails whenever holes hit
  level-ups. Shared risk with Dota (same column).
- Verdict: **parity in mechanism; live-only corruption mode** (training
  livestats data has no such desync — level is absolute there).

### 6–7. `deaths_radiant`, `deaths_dire`

- Cumulative side deaths. Train: livestats; live: GRID table cells (`or 0`).
  Dual-feed diff: exact after alignment (median 0, p90 0 on all 9 maps).
- Verdict: **exact parity** modulo state timing.

### 8–10. `top1_nw_adv`, `radiant_top1_nw_ratio`, `dire_top1_nw_ratio`

- LoL uses `build_top_player_features_over_total`: ratio = top-1 NW / **team
  total** NW (`src/shared/utils/top_players.py`), selected by the LoL game
  profile (`game_profile.py`) on both train and live paths — consistent.
- `top1_nw_adv` = max radiant player NW − max dire player NW. Median |Δ| 0–27
  on the tape diff; ratios differ ~0 (p90 ≤ 0.01).
- Note: Dota uses the other variant (top-1 / sum of other four). Intentional
  cross-game difference, **not** a train↔live mismatch inside LoL.
- Verdict: **parity** (intentionally different from Dota).

### 11. `market_radiant_prior`

- Definition: normalized Radiant win probability before the map. Anchor:
  `horn − 90 s` (`HORN_OFFSET_SECONDS`) on both sides.
- **Intentional source difference**: train/backtest use Telonex book mids
  (strict prior, `lookup_strict_prior`); live uses Polymarket `prices-history`
  minute bars (`market_prior.py`), YES/NO aligned ≤30 s, oriented by
  `yes_is_radiant`.
- Measured on 9 linked maps (`prior_compare.out.txt`): |Δ| typically 1–3c;
  worst **grid-3000372-m4: live 0.385 vs train 0.335 (5c)**; grid-3000372-m1
  had **no training prior** (None) but live traded with prior 0.5 — i.e. live
  covers maps the dataset dropped.
- Verdict: **intentional difference with real measured deviation**; not a
  name/orientation bug.

### 12. `market_p_radiant`

- Definition: normalized Radiant-side pair mid. Train: Telonex book at
  `state_wall_us + 0`. Live: engine books at decision receipt. Backtest:
  receipt-time mid from the Telonex-derived `market_seconds` series (indexed
  by wall-of-second).
- Missing/quality: live pair tolerance 0.05 via `normalize_pair_mids`; the
  session also re-checks `|book_p − signal market_p|` freshness before quoting.
- **Mismatch — the core finding**: the three pipelines sample this feature at
  0 / ~+8 / ~+δ seconds after the paired state (see timing model). The
  `source_lag_seconds` contract that is supposed to encode this is stale: the
  join uses literal `0` (`05_prepare_dataset.py:310`) while the audit column,
  model.json, `LOL_SOURCE_LAG_SECONDS`, and both validators
  (`model_server.py:266`, `lol_inputs.py:76`) all say **10**. The enforced
  number describes what the pipeline used to do, not what it does.
- Verdict: **mismatch** (train↔live and train↔backtest; backtest↔live differ
  only residually).

## Findings (ranked)

| # | Finding | Class | Evidence |
|---|---|---|---|
| F1 | `market_p_radiant` pairing: train state+0 / backtest ~state+8 / live state+δ(≈10.4–14.7). Commit `332e1c17` premise false — the live quote sits on the decision wall, not the GRID content clock. | real mismatch | `05_prepare_dataset.py:310`, `grid_feed.py:172`, `signals.py:339,397`, death-offset measurements |
| F2 | `second` domain: live admits PRE_HORN −60..−1 and unbounded IN_PROGRESS seconds; LoL trained on 0..540 only. OOD predictions observed live at −43…−5 and >540. | real mismatch (low observed harm) | `session_quoting.py:94–99`, session.jsonl `reason='model'` at negative seconds; `MODEL_WINDOWS["lol"]=range(0,540)` shields backtest |
| F3 | `market_radiant_prior` source split (Telonex vs PM minute bars), |Δ| up to 5c; live priors exist where training had none. | intentional difference, nontrivial | `market_prior.py`, `prior_compare.out.txt` |
| F4 | `increaseLevel` undercounts after feed holes → persistent `radiant_xp_adv` corruption (m4 +580 median). | live-only failure mode | `grid_widgets.py` parse, `drill_levels_m4.py`, `compare_tape_livestats.out.txt` |
| F5 | `source_lag_seconds=10` enforced by loaders but the join is 0 — stale contract, actively misleading. | contract bug | `05_prepare_dataset.py:310,446`, `lol_inputs.py:76`, `model_server.py:266` |
| F6 | Live numeric cells `value or 0` would silently zero a null NW/death cell; zero occurrences in 31 tapes. | latent fragility | `tape_health_scan.out.txt` |
| F7 | Early model window is nearly empty live (0–4 ticks < s70; first signal ~69–93 s) — but that's ~15–20% of the 480 s buy window and a bigger fraction of the cheap-price edge zone. | observation, not a bug | `early_window.out.txt` |

## Why LoL differs from Dota

1. **Market-mid lag convention.** Dota's prepare joins the mid at
   `state.second + TRAIN_LAG_SECONDS` (10 s) and validates at
   `market_second − 10` (`prepare_dataset.py`) — deliberately placing the mid
   ~10 s *after* the state, approximately matching the live GRID pipeline
   where content is ~δ old when the mid is sampled. LoL was changed to lag 0 by
   `332e1c17`. Same live architecture, opposite training convention: Dota's
   trained (state, mid) gap ≈ live's; LoL's trained gap (0) ≈ nothing live or
   backtest ever produces. This alone can make a strongly-positive backtest
   (which evaluates at gap 8 — closer to live) coexist with flat live trading,
   though the residual backtest↔live gap (~2.4–6.7 s of extra staleness plus
   the prior source) means even Dota-style lag would not make backtest equal
   live.
2. **State source.** Dota trains on dense STRATZ-derived second rows; LoL
   trains on sparse livestats frames with a 2 s age gate. Both run GRID live.
   LoL's coverage inside 0..540 is thin early (F7), so the mis-timed
   `market_p_radiant` dominates a larger share of tradeable signals.
3. **Top-player feature variant.** LoL uses top-1/team-total; Dota uses
   top-1/sum-of-others. Consistent inside each game; irrelevant to the gap.
4. **Shared weaknesses** (not LoL-specific but worth noting): `increaseLevel`
   desync (F4), no upper `second` bound (F2), PRE_HORN floor (F2 — harmless for
   Dota since −60 is in-domain there).

## Recommended next steps (no code changes made)

1. **Lag-alignment experiment (highest value).** Rebuild the LoL dataset's
   `market_p_radiant` at `state_wall_us + L` for L ∈ {8, 10, 12}, retrain, and
   compare OOS delta accuracy + schedule-backtest PnL against the lag-0
   baseline. If the flat-live hypothesis is right, L≈10 should dominate.
2. **Measure δ directly per capture** by clock-aligning table content against
   scoreboard kill events (not labels) on more dual-feed tapes; check whether
   δ drifted (Aug ~14.7 vs Sep ~10.5) — if unstable, the right fix is a
   measured per-tick delay, not a constant.
3. **Fix the contract**: either restore `source_lag_seconds` semantics or add
   an explicit `market_mid_offset_seconds` metadata field that the join
   actually reads (F5).
4. **Cap the live window for LoL** at [0, 540] (per-game `in_model_window`),
   closing F2.
5. **Prior unification**: quantify Telonex-vs-PM-minute prior delta over the
   training set (F3); consider training the prior from the same source live
   uses.
6. **XP robustness**: consider clamping live `level` to never decrease and/or
   falling back to scoreboard data after feed holes (F4).

## Evidence index

All under `../work/feat-devin/`:

- `compare_tape_livestats.py` / `.out.txt` — replays 9 live tapes through
  `GridFrameReducer`, aligns to livestats via death steps, diffs every
  feature; also tests GRID NW vs raw totalGold vs consumed-corrected.
- `tape_health_scan.py` / `.out.txt` — 31 tapes: null-cell counts, team sizes,
  `increaseLevel` presence.
- `early_window.py` / `.out.txt` — ticks below s70/s480 vs first model signal,
  32 maps.
- `prior_compare.py` / `.out.txt` — training (Telonex) vs live (PM minute-bar)
  priors on the 9 linked maps.
- `drill_levels_m4.py`, `drill_levels_early.py`, `stall_analysis.py` — the
  increaseLevel desync investigation on grid-3000372-m4.

Key code references: `src/lol/05_prepare_dataset.py:310,316,446`,
`src/lol/livestats_frames.py` (`select_grid_rows`, 2 s age gate),
`src/trader/grid_feed.py:172` (second = clock − feed_delay),
`src/trader/grid_widgets.py:14–15` (scoreboard 0-delay, table 8 s),
`src/trader/session_quoting.py:94–99` (in_model_window),
`src/backtest/signals.py:339,397` (mid overwrite at received_ns),
`src/backtest/lol_inputs.py:147–172` (mid series keyed by wall-of-second),
`src/backtest/marks.py:42–49` (as-of bisect),
`src/archive_index/schedule.py:61–64` (MODEL_WINDOWS),
`src/shared/utils/gbm.py:37` (FEATURE_COLUMNS),
`src/shared/constants/lol.py:50` (LOL_SOURCE_LAG_SECONDS=10),
`src/trader/model_server.py:264–267` (live contract validation),
`src/backtest/lol_inputs.py:66–78` (backtest contract validation).
