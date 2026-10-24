# STEP-008 plan: CS2 live features from the shared offline functions

Step: `STEP-008` of `tasks/cs2-live/feature.json` (priority 8, `passes: false`).
Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, clean, HEAD `fc40850e`.
Evidence folder: `betting_workspace/investigations/2026-10-23-cs2-live-validation/` (`report.md` + `work/step-008/`).
`report.md` exists. Append a `## STEP-008` section. Never overwrite the file.
No Figma or design links exist for this step.

The step turns the STEP-007 reducer facts into one model input frame and one prediction.
It adds `src/trader/cs2_rounds.py` with `RoundCallInputs`, `build_round_frame`, `RoundCallPrediction`, and `predict_round_call`.
Every feature value comes from the offline functions in `src/cs2/s11_*.py`. The step adds no feature formula.
A synthetic parity test proves that the call row equals the offline `build_rounds` row on the same history.

No `RoundSignals`, no profile, no worker change, no book read, no prior read. Those are STEP-009 and STEP-010.
Dota/LoL code does not change. The offline backtest path changes only by four renames.

Do not deploy, do not `git push`, do not run `scripts/sync_trader.py`, do not touch STEP-009+.
Planning measured only the unchanged code and the recorded tape. Scratch output: `betting_workspace/tasks/cs2-live/orchestrate/work/plan-s8/`.

## 1. Facts this plan rests on

Line numbers are at `fc40850e`.

### 1.1 The offline round frame

`build_rounds` (`src/cs2/s11_round_frame.py:129-144`) runs this chain on the 1 Hz validation dataset:
`_round_clocks` → `_mark_decisions` → `_attach_market` (ends with `_fill_missing_pistol`, `:295`) → `_attach_streaks` → `attach_loss_levels` → `attach_survivors` → `attach_kill_features` → `attach_cash_estimate` → `attach_team_form`.

- The dataset row at second `s` holds board columns from `s - 1` and table columns from `s - 10` (`src/cs2/s10_prepare_dataset.py:47-48`, `assemble_frame` `:642-644`, `lookup_delayed` `:541-550`).
- `_mark_decisions` (`:265-277`): a row that follows its previous round in the same half decides at `end_real + 9`, with `end_real = post_sec - 1`. Any other row decides at `fs_sec + 1`.
  Rows with `decision_s >= live_sec - 1` are dropped (`:277`).
  With the board delay, `post_sec = ceil(won_s) + 1`, so `end_real = ceil(won_s)`.
- `_attach_market` (`:280-295`): `m0` is the mid at `decision_s`. `prev_load_*` and `prev_a_won` are a one-row shift. `a_won` is "next row's score rose", and the map winner for the last row.
- `attach_survivors` (`src/cs2/s11_round_features.py:69-81`) reads `team_a_alive`/`team_b_alive` at exactly `decision_s`. That value is the table state at `decision_s - 10`.
- `_match_kill_features` (`:209-258`) counts kills with `ceil_s <= decision_s - TABLE_DELAY_SECONDS` (`_write_kill_row` `:296`, `TABLE_DELAY_SECONDS = 10`).
  So a regular row sees the previous round's kills up to `end_real - 1`. The last kill (at `won_s`, `ceil = end_real`) is not seen.
  An opening row (`fs_sec + 1`) sees every kill of the previous round, because the half break is longer than 10 s.
- `_estimate_match` (`src/cs2/s11_cash_estimate.py:106-147`) resets purses on `opens_half`, else advances them with the previous round's facts and this row's `surv_*`.
  It subtracts `300 × (full kills − seen kills)` for late kills (`:133-136`, `_count_late_kills` `:271-274` clamps at 0).
  Its inputs come from `_read_round_facts` (`:86-103`): `is_planted`, `is_explode`, `is_defused`, and enemy `kills_ct`/`kills_t`.
- `attach_team_form` (`src/cs2/s11_team_form.py:78-87`) writes `FORM_COLUMNS` (`:21-28`) as NaN when `CS2_BO3_LINKS_PATH` is missing. No `form_*` column is in `FEATURES`.
- `predict_symmetric` (`s11_round_frame.py:379-397`) applies `add_derived` itself, to the rows and to `mirror_rows(rows)`.
  So `build_rounds` returns raw rows, and `predict_round_deltas`/`predict_round_rises` (`:182-210`) take raw rows.
  `mirror_rows` (`:348-360`) and `add_derived` (`:330-345`) read `carry_*`, `map_kills_*`, `loss_level_*`, `surv_*`, `form_*`, `*_won_map`, `first_kill_share_a`, `a_won`, `y`. Those columns must exist even though they are not in `FEATURES`.
- `predict_round_deltas` and `predict_round_rises` score only rows where `m0`, `cash_a`, `cash_b` are known (`known_cash_mask` `:363-364`). Other rows get 0.
- `predict_round_run` (`src/backtest/cs2_round.py:182-228`) is the backtest call: `predict_round_deltas`, `predict_round_rises`, then `apply_round_rules` (`src/cs2/round_rules.py:20-34`) on scores, `is_overtime`, `is_pistol_round`.
  `RoundCall` (`round_rules.py:9-17`) has no `model_delta` field.
- `attach_round_features` (`src/cs2/s10_state_frame.py:124-131`) derives `half`, `is_overtime`, `is_pistol_round` (rounds 1 and 13), and `team_a_ct` from `round_number` and `team_a_side`.

### 1.2 The STEP-007 facts

- `RoundFacts` (`src/trader/cs2_round_types.py:21-38`): `number`, `team_a_ct`, `a_won`, `win_type`, `loadout_a/b`, final `kills_a/b`, `seen_kills_a/b`, `seen_alive_a/b`, `planted`, `duration_s`, `live_ns`, `end_ns`.
  It has no final alive count.
- `RoundDecisionDue` (`:47-53`) carries `round_number`, `due_ns`, `team_a_ct` (board side for the called round), `header: MapHeader`, and the completed `rounds`.
- `Cs2FrameReducer._try_facts` (`src/trader/cs2_feed.py:430-476`) builds `RoundFacts` from the first `WinType` sample (`final`) and the last sample with `duration_s <= final - 1` (`_seen` `:487-496`).
  `final.alive_a`/`final.alive_b` exist on `_RoundSample` (`:61-66`) and are dropped today.
- A due for round N is emitted only when `len(rounds) == N - 1` (`_due_ns` `:521-536`, check at `:527`). So the call round is always `len(rounds) + 1`, and history always starts at round 1.
- Only `tests/test_cs2_feed.py:281-300` reads `RoundFacts` fields. Only `cs2_feed.py:457` constructs it, with keywords.

### 1.3 Measurements on unchanged code

All in `work/plan-s8/`.

- Offline gate (`round_preds_sha.py`, `round-preds-base.txt`): `predict_cs2_run` over the 636 maps of `data/backtests/cs2_maker/LIVE/seed0/round_preds.parquet` with the production models gives
  `maps=636 rows=13617 sha256=8d6f38cd74e7c5ed12432c8cbe1fa4f4080a497c99d64501977f382c6b4d8891`, 12.1 s.
  That equals `cs2_round_preds_sha256` in `LIVE/seed0/manifest.json`. The renames must keep this sha.
- Latency (`predict_timing.py`, `predict-timing.txt`): production models have 10 + 10 members. One call of `predict_round_deltas` + `predict_round_rises` takes 142 ms median, 180 ms max, on 1 row; 147 ms on 13 rows.
- Model map codes (`map-codes.txt`): both models know codes 1–9. `CS2_MAP_CODES['vertigo'] = 10` is not a model category.
- Tape (`tape_alive.py`, `tape-alive.txt`), map 3 of `data/cs2/live/cs2-damaju-hachae-2026-10-21/grid.jsonl`:
  21 rounds, 0 kill counts that fall inside a round. Seen alive differs from final alive in 11 of 21 rounds.
  The winner's own count differs once: R15, team a (winner, `bombDefused`) seen alive 2, final alive 1.
  R12 → R13: R12 END `21:39:58.377`, R13 freeze `21:40:19.124` (20.7 s break).
- Targeted baseline: `tests/test_cs2_cash_estimate.py tests/test_cs2_round_features.py tests/test_cs2_round_model.py tests/test_cs2_signals.py tests/test_cs2_feed.py tests/test_cs2_models.py tests/test_cs2_execution.py tests/test_backtest_inspect_cs2.py tests/test_cs2_round_sides.py tests/test_core_trace_round.py`
  → `112 passed, 16 warnings in 4.03s` with `-n 8`.

## 2. Design decisions

1. **Four renames, no new offline functions.** `_attach_streaks` → `attach_streaks`, `_fill_missing_pistol` → `fill_missing_pistol` (`s11_round_frame.py`),
   `_match_kill_features` → `build_match_kill_features` (`s11_round_features.py`), `_estimate_match` → `estimate_match_cash` (`s11_cash_estimate.py`).
   Bodies do not change. They already take frames, not paths. `attach_kill_features` and `attach_cash_estimate` stay the disk wrappers.
2. **`build_round_frame` returns raw rows, like `build_rounds`.** It does not call `add_derived`.
   `add_derived` needs the model's `map_codes`, which `build_round_frame(header, rounds, call)` does not have, and `predict_symmetric` already applies it to own and mirrored rows.
   The chain therefore ends in `FORM_COLUMNS` NaN, and `add_derived` runs inside the prediction, exactly as in the backtest. The parity test applies `add_derived` on both sides before it compares.
3. **One frame row per round.** Rows are the completed rounds `1..N-1` plus the call row `N = len(rounds) + 1`.
   Every row has the columns `build_rounds` produces that the chain or the model reads. Completed rows have `m0` NaN, so only the call row is scored.
4. **Facts become columns directly; formulas stay in the offline functions.**
   - `team_a_side` (`"CT"`/`"T"`) from `team_a_ct`, then `attach_round_features` gives `half`, `is_overtime`, `is_pistol_round`, `team_a_ct`.
   - `team_a_score`/`team_b_score` before a round: the count of earlier `a_won` true/false.
   - `a_won` (float, NaN on the call row), `team_a_loadout`/`team_b_loadout` (float, NaN on the call row).
   - `prev_a_won`, `prev_load_a`, `prev_load_b`: `shift(1)` of the three columns above, as `_attach_market` does (`:289-294`).
   - Header columns `map_code`, `game_number`, `team_a_series_score`, `team_b_series_score` on every row.
   - `market_radiant_prior` = `call.prior` on every row (offline it is one value per map). `m0` = `call.m0` on the call row, NaN elsewhere.
   - `fs_sec`, `post_sec` = NaN. They feed only `prev_duration_s`, which is not in `FEATURES`.
5. **A feature clock replaces the 1 Hz history.** The offline functions compare kill clocks and survivor seconds with `decision_s`. The live frame encodes the same cut points:
   - `end_s = ceil(end_ns / 1e9)`. This is the offline `end_real = ceil(won_s)`.
   - A seen kill of round r has clock `end_s - 1`. A late kill (`kills - seen_kills`) has clock `end_s + 1`.
   - Row k reads the state of round k−1 at a state clock: `end_s - 1` (seen state) for a regular row, `end_s + 1` (final state) for a row that `opens_half(k)`.
   - `decision_s(k) = state clock + TABLE_DELAY_SECONDS`. For a regular row this is `end_s + 9`, the offline formula. The kill cutoff `decision_s - 10` then keeps seen kills and drops late ones; an opening row keeps both.
   - Row 1 has no previous round: state clock 0, no survivor row, no kills.
   - `decision_s` is therefore a feature clock, not the due time. STEP-009 keeps `due_ns` for `call_ns`. The parity test does not compare `decision_s`.
6. **Opening rows use the final state of the previous round** (feature.json). `RoundFacts` gains final `alive_a`, `alive_b` from the reducer's `final` sample.
   Survivors of row k: `alive_a/b` of round k−1 when `opens_half(k)`, else `seen_alive_a/b`. This changes `prev_clutch` of rounds 13, 25, 28, … when the winner loses a player in the last second (R15 on the tape shows that this happens).
7. **Late kill count `max(0, kills - seen_kills)`.** Same clamp as `_count_late_kills`. The tape never lowers a kill count (0 of 21 rounds).
   CS2 subtracts one kill for a team kill, so a lower final count is a real input, and a negative count would make the arrays fail. State this reason in `report.md`, not in a code comment.
8. **Cash facts use the same merge as the wrapper.** A small per-round facts table (`round_number`, `is_planted`, `is_explode`, `is_defused`, `kills_ct`, `kills_t`) is left-merged on `round_number`, then `estimate_match_cash` runs. The call row gets NaN, as an offline row without replay facts.
   `kills_ct` is `kills_a` when team a was CT, else `kills_b`. `is_explode` is `win_type == "bombExploded"`, `is_defused` is `win_type == "bombDefused"`.
9. **`RoundCallInputs` carries only what the facts do not.** `team_a_ct` (board side for the called round; it is not derivable at an overtime half), `m0`, `prior`. Both prices are in team-a orientation and may be NaN.
   The round number is `len(rounds) + 1`. No check: the reducer guarantees it (`cs2_feed.py:527`).
10. **`RoundCallPrediction` is a new result type.** `RoundCall` has no `model_delta`, and the backtest reads it from parquet. Fields: `model_delta`, `pred_delta`, `m0`, `up_a`, `up_b`, all `float`.
    `m0` is the raw call price. NaN stays NaN, so STEP-009/010 can log the missing book.
11. **NaN `m0` needs no new branch.** `known_cash_mask` is false, so `model_delta`, `up_a`, `up_b` are 0. `apply_round_rules` turns 0 into 0 (`sign(0) × 1e-4 = 0`; overtime gives 0). `pred_delta` is 0 and below every entry threshold.
12. **No comments and no docstrings in new code** (`.feature-json.config.json` review rule). The renamed functions have none today.

## 3. File-by-file changes

### 3.1 `src/cs2/s11_round_frame.py`

- Rename `_fill_missing_pistol` (`:298`) to `fill_missing_pistol`. Update its call in `_attach_market` (`:295`).
- Rename `_attach_streaks` (`:316`) to `attach_streaks`. Update its call in `build_rounds` (`:138`).

### 3.2 `src/cs2/s11_round_features.py`

- Rename `_match_kill_features` (`:209`) to `build_match_kill_features`. Update its call in `attach_kill_features` (`:92`).

### 3.3 `src/cs2/s11_cash_estimate.py`

- Rename `_estimate_match` (`:106`) to `estimate_match_cash`. Update its call in `attach_cash_estimate` (`:81`).

### 3.4 `src/trader/cs2_round_types.py` and `src/trader/cs2_feed.py`

- `RoundFacts`: add `alive_a: int` and `alive_b: int` after `kills_b`.
- `Cs2FrameReducer._try_facts`: pass `alive_a=final.alive_a`, `alive_b=final.alive_b`. Nothing else in the reducer changes.

### 3.5 New `src/trader/cs2_rounds.py`

Imports only existing names: `apply_round_rules` (`cs2.round_rules`), `TABLE_DELAY_SECONDS` (`cs2.s10_prepare_dataset`), `attach_round_features` (`cs2.s10_state_frame`),
`estimate_match_cash` (`cs2.s11_cash_estimate`), `ROUND_KEY`, `MatchKills`, `attach_loss_levels`, `attach_survivors`, `build_match_kill_features`, `opens_half` (`cs2.s11_round_features`),
`attach_streaks`, `fill_missing_pistol`, `predict_round_deltas`, `predict_round_rises` (`cs2.s11_round_frame`), `FORM_COLUMNS` (`cs2.s11_team_form`),
`NS_PER_SECOND` (`shared.utils.match_time`), `Cs2RoundModels` (`trader.cs2_models`), `MapHeader`, `RoundFacts` (`trader.cs2_round_types`).
All these modules are already loaded by the trader through `trader/cs2_models.py:4`. No `backtest` import.

Module constants: `BOMB_EXPLODED = "bombExploded"`, `BOMB_DEFUSED = "bombDefused"`, `SEEN_BEFORE_END_S = 1`, `LATE_AFTER_END_S = 1`, a private match id `0`.

Types (frozen dataclasses):

- `RoundCallInputs(team_a_ct: bool, m0: float, prior: float)`.
- `RoundCallPrediction(model_delta: float, pred_delta: float, m0: float, up_a: float, up_b: float)`.

`build_round_frame(header: MapHeader, rounds: tuple[RoundFacts, ...], call: RoundCallInputs) -> pd.DataFrame`:

1. Build the base rows (decision 4 and 5) with explicit dtypes: `int64` for ids, numbers, scores, header values, and `decision_s`; `float64` for prices, loadouts, `a_won`; `str` for `team_a_side`.
2. `attach_round_features` → `fill_missing_pistol` → `attach_streaks` → `attach_loss_levels`.
3. `attach_survivors(frame, survivors)`. `survivors` has `match_id`, `sec`, `team_a_alive`, `team_b_alive`: one row per row k ≥ 2 at `decision_s(k)` (decision 6).
4. `frame.merge(build_match_kill_features(frame, kills, present, planted), on=list(ROUND_KEY), how="left")`.
   `kills` is a `MatchKills` from the facts (decision 5 and 7): per completed round, team a kills have `killer_ct = team_a_ct`, team b kills `not team_a_ct`. `tick` is a running counter (it feeds only `first_kill_share_a`).
   `present` is the set of completed round numbers. `planted` is the set of completed rounds with `planted`.
5. `frame.merge(estimate_match_cash(frame.merge(cash_facts, on="round_number", how="left")), on=list(ROUND_KEY), how="left")` (decision 8).
6. Set every `FORM_COLUMNS` column to NaN. Return the frame.

The round 1 call has no completed round. Build the kills arrays and the cash facts table with explicit dtypes (`int64`, `bool`, `float64`) so the empty case merges and casts like the others.

`predict_round_call(models: Cs2RoundModels, frame: pd.DataFrame) -> RoundCallPrediction`:

- `model_delta = predict_round_deltas(models.delta, frame)`, `rises = predict_round_rises(models.outcome, frame)`,
  `pred_delta = apply_round_rules(model_delta, team_a_score, team_b_score, is_overtime, is_pistol_round)` with the same dtypes as `predict_round_run` (`backtest/cs2_round.py:192-198`).
- Return the last element of each array and the last `m0`, as `float`.

Split the frame build into small private helpers so no function passes ruff `C901` (limit 10), for example: one for the end second, one for the state clock and survivors of a row, one for the base rows, one for the kills, one for the cash facts.
Expected size 150–220 lines. Use named intermediate variables (AGENTS.md rule).

### 3.6 Tests

`tests/test_cs2_round_model.py:303`: `round_frame._attach_streaks` → `round_frame.attach_streaks`. Keep the file pragma; the file still reads `_attach_market`.

`tests/test_cs2_feed.py`, `test_r1_facts_use_the_first_win_type_frame` (`:281`): assert `facts.alive_a == 1` and `facts.alive_b == 0` (tape R1 final sample, `work/plan-s8/tape-alive.txt`).

New `tests/test_cs2_rounds.py` (expected 350–500 lines). Use the pandas pyright pragmas that `tests/test_cs2_round_features.py:1-5` uses, only if basedpyright needs them.

**Synthetic history, built only by the test.** A test-local frozen `_RoundSpec` per round: `a_won`, `win_type`, `team_a_ct`, loadouts, final kills, seen kills, seen alive, final alive, `planted`.
A test-local timeline: map start `T0`; `freeze(r)`, `live(r) = freeze + 20`, `end(r) = live + 60` (integer seconds); END stamp `end(r) - 0.3 s`;
next freeze `end + 7`, or `end + 30` when the next round opens a half; plant at `live + 40` for planted rounds.
From these the test builds two inputs and never calls `build_round_frame` helpers:

1. Offline input. The dataset stays in memory and goes straight to `build_rounds`; only the replay and catalog files go to `tmp_path`:
   - A 1 Hz dataset with `STATE_COLUMNS`, one row per second from `freeze(1) - 15` to `end(last) + 2`. Board columns at second `s` use board time `s - 1`:
     before `freeze(1)` phase −1, round 1, score 0:0; else round r with `freeze(r) <= s - 1`, phase 0 before `live(r)`, 2 inside the plant window, 3 from `end(r)`, else 1.
     Scores count rounds with `end <= s - 1`. `bomb_planted = phase == 2`. `team_a_ct`, `is_pistol_round`, `is_overtime`, `half` from round r.
     Table columns use table time `s - 10`: before `freeze(1)` alive 5/5 and loadout NaN; freeze alive 5/5 and loadout NaN; live (until `end`) seen alive and the round loadout; from `end` to the next freeze final alive and the round loadout.
     `market_p_radiant` from a test mid function of the second, with a NaN window of 5 s that covers one round's decision second. `market_radiant_prior` constant 0.55, or NaN in one variant. `paused` and `round_dropped` false.
   - `round_state/<match_id>/kills.parquet` (`round_number`, `tick`, `at`, `killer_side`, `victim_side`): seen kills at `live + 10 + i`, late kills at the END stamp.
   - `round_state/<match_id>/seconds.parquet` (`round_number`, `is_planted`, `is_explode`, `is_defused`).
   - `catalog.parquet` (`match_id`, `team_a_win` = last round winner).
   - Monkeypatch `cs2.s11_round_frame.CS2_MATCH_CATALOG_PATH`, `cs2.s11_round_features.CS2_ROUND_STATE_DIR`, `cs2.s11_cash_estimate.CS2_ROUND_STATE_DIR`, and `cs2.s11_team_form.CS2_BO3_LINKS_PATH` (a missing file), as `tests/test_cs2_signals.py:411-412` does.
2. Live input: `RoundFacts` per round from the same specs (`end_ns = end * 1e9 - 300_000_000`, `live_ns = live * 1e9`, `duration_s` any), and a `MapHeader` equal to the dataset constants.

**Histories.**

- `BASE`: 14 rounds, team a on T in rounds 1–12. It must contain: every win type; a four-round losing streak (loss streak and loss level reach the cap); late kills in a regular round;
  a planted round that T loses (plant bonus) and one that T wins by explosion; R12 with a late kill and winner seen alive 2 → final alive 1; R13 side flip.
- `BASE` with team a on CT first (sides mirrored), and `BASE` with prior NaN.
- `OVERTIME`: 26 rounds, 12:12 after round 24, generated from a winner string with simple per-round values. Round 25 opens overtime.

**Tests.**

1. `test_call_row_matches_build_rounds` (parametrized over the four histories): run `build_rounds` once per history. Assert it keeps every round (`round_number == 1..n`) and that its `decision_s` equals the test's own expectation (`freeze + 2` for round 1 and openers, `end(prev) + 9` else).
   For each N in `1..n`: `frame = build_round_frame(header, facts[:N-1], RoundCallInputs(team_a_ct=spec N, m0=mid at the expected decision second, prior=prior))`.
   Assert `len(frame) == N`. Compare `add_derived(frame.tail(1), (map_code,))[FEATURES]` with `add_derived(offline row N, (map_code,))[FEATURES]` by `pd.testing.assert_frame_equal(..., check_dtype=True, check_exact=True)` after `reset_index(drop=True)`. NaN positions must match.
   Then set `a_won` to 1.0 on both copies and compare `OUTCOME_FEATURES`; repeat with 0.0. Assert the live call row `a_won` is NaN.
2. `test_parity_cases_are_not_vacuous`, on `BASE`: R1 call row has NaN `prev_a_won`, `prev_load_*`, `prev_kills_*`, `prev_planted`, `prev_clutch` and cash 4000/4000;
   R2 cash is finite and not 4000 (pistol result); the NaN-window round has NaN `m0` and `logit_m0`; R13 `prev_clutch == 1.0` and `prev_kills_*` equal R12 final kills (the regular rule would give 0.0 and seen kills);
   a regular row after a late-kill round has `prev_kills_*` equal to seen kills; OT history R25 cash 62500/62500, `is_overtime` true, `half` 3.
3. `test_predict_round_call_reads_the_last_row`: tiny models from `tests/test_cs2_round_model.py` (`_fit_tiny_member`, `_build_round_frame`; import them as `tests/test_cs2_signals.py:9` does), map codes `(0, 1)`, `ModelReference(name="tiny", trained_at="tiny")`.
   On a mid-half call: `model_delta == predict_round_deltas(models.delta, frame)[-1]`, `up_a`/`up_b` equal `predict_round_rises(models.outcome, frame)` last values, `pred_delta == model_delta`, `model_delta != 0`.
4. `test_closed_entry_rounds_keep_only_the_sign`: R13 call and a call at 12:5 → `pred_delta == np.sign(model_delta) * CLOSED_ENTRY_DELTA` with `model_delta != 0`.
5. `test_overtime_call_is_zero`: R25 call → `pred_delta == 0.0`.
6. `test_missing_book_gives_a_zero_call`: `m0 = NaN` → `model_delta`, `pred_delta`, `up_a`, `up_b` all 0.0 and `math.isnan(result.m0)`.
7. `test_missing_prior_still_predicts`: `prior = NaN`, known `m0` → finite `model_delta`, `up_a`, `up_b`.

## 4. Verification

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`. `INV=../betting_workspace/investigations/2026-10-23-cs2-live-validation`. `PLAN8=../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s8`. Write outputs to `$INV/work/step-008/`.

1. `.venv/bin/ruff check src tests scripts` and `.venv/bin/ruff format --check src tests scripts` → clean.
2. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`.
3. Targeted pytest:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 tests/test_cs2_rounds.py tests/test_cs2_cash_estimate.py tests/test_cs2_round_features.py tests/test_cs2_round_model.py tests/test_cs2_signals.py tests/test_cs2_feed.py tests/test_cs2_models.py tests/test_cs2_execution.py tests/test_backtest_inspect_cs2.py tests/test_cs2_round_sides.py tests/test_core_trace_round.py`
   Baseline without the new file: 112 passed. The result must be 112 plus the new tests, 0 failed.
4. `PYTEST_N=8 make test` → 0 failed. Known: `test_upcoming_rows_from_sidecars` fails only 23:00–00:00 Berlin; `test_follow300_replay` `dota_9007700576` flakes under xdist (rerun alone). Never run two `-n` suites at once.
5. Offline gate for the renames:
   `PYTHONPATH=src .venv/bin/python $PLAN8/round_preds_sha.py data/backtests/cs2_maker/LIVE/seed0/round_preds.parquet > $INV/work/step-008/round-preds-head.txt`
   → `maps=636 rows=13617 sha256=8d6f38cd74e7c5ed12432c8cbe1fa4f4080a497c99d64501977f382c6b4d8891` (equal to `$PLAN8/round-preds-base.txt` and the LIVE manifest). About 15 s.
6. Import smoke: `PYTHONPATH=src .venv/bin/python -c "import trader.cs2_rounds, trader.cs2_feed, trader.game_profile, trader.match_worker; print('import-ok')"`.
   `grep -rn --include='*.py' "from backtest\|import backtest" src/trader src/strategy src/cs2 src/shared` → only `src/strategy/signals.py:1`, as in STEP-007.
   `grep -rn --include='*.py' "_attach_streaks\|_fill_missing_pistol\|_match_kill_features\|_estimate_match" src tests scripts` → empty.
7. Tape feature smoke (a script in `$INV/work/step-008/`, not a repo test; STEP-010 owns the CLI and the books).
   Fold the tape as `$INV/work/step-007/tape_smoke.py` does. For each `RoundDecisionDue`, call `build_round_frame(due.header, due.rounds, RoundCallInputs(due.team_a_ct, m0, NaN))` and `predict_round_call` with `load_cs2_round_models(CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR)`.
   Run twice: `m0 = NaN` (expect every result 0.0 and `m0` NaN) and a constant `m0 = 0.5` labeled synthetic in the output (not a book price).
   Expected with 0.5: 21 frames, `len(frame) == round_number`, every value finite, R1 and R13 `|pred_delta| in {0, 1e-4}`, R13 call row `team_a_ct` true, no exception.
   Print per round: `FEATURES` of the call row after `add_derived(frame, models.delta.map_codes)`, `model_delta`, `pred_delta`, `up_a`, `up_b`, and the median call time. Save the ledger and the command in `report.md`.
8. `report.md`: append `## STEP-008` with revisions, commands, outputs, durations, the round_preds sha, the late-kill clamp reason, the opener final-state rule, and the gaps in §5. `investigations/` stays untracked.
9. `progress.txt` entry with learnings. Commit once in `esports-trader`.

No browser check: the step changes no page. No GRID or signal differential: no Dota/LoL file changes. `make test` covers the MOBA suite.

## 5. Edge cases, risks, hand-offs

- **Opening rows.** Offline sees the final state only when the half break is longer than about 10 s (`fs_sec - 8 >= end`). Live always takes the final state. On the tape the break is 20.7 s. A shorter overtime break would make offline drop the last kill and live keep it.
- **Offline drops late-decision rows** (`_mark_decisions` `:277`). Then offline `prev_*` and streaks of the next row skip a round. Live keeps every completed round. The synthetic histories keep every row, so parity holds; real maps with a dropped row differ in that one place.
- **Final kills.** Live final kills are the first `WinType` frame. Replay kills can include post-round kills (R4, R8, R15 on the tape). Known from STEP-007.
- **R6 late plant.** Live `planted` is false for tape R6 (STEP-007). That changes R7 `prev_planted` and the R7 cash plant bonus against the replay truth.
- **Team kills.** Late kills are clamped at 0 (decision 7). Seen kills can include a team kill as a kill; offline `prev_kills_*` has the same property, offline cash counts enemy kills only.
- **`decision_s` is a feature clock.** It is not the due time and not the trace `call_ns`. Do not print it as the due in STEP-010.
- **Map outside the model.** Vertigo (code 10) is not a model category. `add_derived` makes `map_code` NaN, and LightGBM treats it as missing. The call still runs. STEP-009/012 decide whether to trade such a map.
- **Latency.** One call takes about 140–180 ms with production models on this Mac. It blocks the event loop if called inline. STEP-009 decides between inline and an executor; the due window is about 10 s.
- **STEP-009 needs.** `RoundCallInputs(team_a_ct=due.team_a_ct, m0=team-a midpoint at due, prior=latched team-a prior or NaN)`.
  `RoundSignal(predicted_delta=pred_delta, anchor_p=m0, up_radiant=up_a, up_dire=up_b, call_ns=due_ns)`. A NaN `m0` result must not open BUY; log it as a missing book.
- **STEP-010 needs.** The CLI prints `RoundCallPrediction` fields and the call row `FEATURES` after `add_derived(frame, models.delta.map_codes)`.

## 6. Out of scope

- `RoundSignals`, `buys_open`, heartbeat, stale timeout, book and prior reads, `GAME_PROFILES['cs2']`, config, Compose (STEP-009).
- `replay_cs2_rounds` CLI, `test_cs2_tape_replay.py`, books from `books.jsonl` (STEP-010).
- Dashboard (STEP-011), VPS (STEP-012).
- Any change to offline formulas, model retraining, or `attach_team_form` (live `FORM_COLUMNS` stay NaN).
