# Follow300 sole BACKTEST policy: cleanup inventory and plan

Status: plan only. No product edits, no git mutation, no live/VPS, no poly-maker.
Branch: `esports-trader` `ladder-experiment` (6 commits ahead of `main`: `454fe4e` … `0d3eafd`).
Date: 2026-09-06.

Numerical analysis is the user's job. Correctness of reattach / anchor / episode / latch-then-follow is another reviewer's job. This document is deletion/simplification inventory plus a bounded implementation plan.

## Corrections vs the first draft

1. **Dota F300 seeds 3..11 are running, not proposed.** Parent started them, 1 shard each, current CLI flags. Do not schedule them. Do not `--resume` them after a fingerprint bump. Wait for `summary.json` on every seed before landing resume-fingerprint changes.
2. **No 1-rung strategy retained for tests.** Production policy is 3 rungs × $100, 1 tick, parallel BUY+SELL, follow after first fill, continue, fixed size, no sell-drop-block. `DotaMakerConfig` must not accept `layers=1` / `buy_lifecycle=legacy` / other matrix knobs. Gate tests adapt to 3 rungs, or they call already-pure helpers (`nw_velocity_block_reason`, `buy_price_legal`, `ladder_price`, `passes_anchor_gate`). Do not keep a parameterized 1-rung fixture path.

---

## 1. What Follow300 is

Sole remaining BACKTEST quote policy:

| Knob | Value |
|---|---|
| rungs | 3 |
| size | $100 per rung ($300 total clip) |
| step | 1 tick (0.01) |
| lifecycle | parallel: BUY rungs and SELL rest together |
| reprice | GRID-latched until first BUY fill, then follow the book |
| after first BUY fill | continue remaining rungs |
| sizing | fixed clip (not depth-at-submit) |
| sell-drop-block | absent |

Current `DEFAULT_MAKER_QUOTE_POLICY` on this branch is still the old B path (`layers=1`, `layer_step_ticks=2`, `base_size_usdc=100`, `buy_lifecycle=legacy`). Follow300 is only reachable via experimental CLI flags.

`shared.constants.strategy.BASE_SIZE_USDC = 100` stays **one $100 clip**. Do not change it to 300. Follow300's $300 total is backtest-only (`100 * 3`). Live paper still reads `config/trading.toml`.

---

## 2. Branch vs main (code/docs/scripts only)

`git diff --stat main..HEAD -- ':!data/'` — 48 files, +19898 / −602.

### Commits `main..HEAD`

- `454fe4e` BUY ladder v2: experiment code, runs and docs
- `27547e1` trailing newlines (pre-commit)
- `f3c030a` depth-at-submit BUY sizing
- `dfac5ab` depth-cap seed runs, audits, result
- `a7c8875` B300 legacy baseline, 3 seeds
- `0d3eafd` LoL F300/B200 12-seed catalogs; sell-drop-block (dropped)

### Sep 1–4 simplification intent (already on `main`, copy the pattern)

- After a drop verdict: **delete the knob from the canonical runner**, leave the report (`docs/experiments/README.md` “code dropped”).
- `shared/` only for code more than one pipeline uses (`1667ad0`). `maker_orders.py` stays in `backtest/`.
- C901 = 10: split only after deletion still trips the rule. Do not pre-split.
- Do not promote without 12 seeds and an explicit ask.
- Canonical runner no longer accepts dropped knobs (no-unwind, pred ramp, etc.).

---

## 3. LIVE / trader / promotion coupling — do not touch

Live quoting `layers` is **not** the backtest BUY ladder.

| Surface | Current | Cleanup |
|---|---|---|
| `data/backtests/dota_maker/LIVE` | symlink → `validation_join_delta01_cut540_nw350_p35_l1-val454` (1-layer $100, 12 seeds) | leave |
| `data/backtests/lol_maker/LIVE` | symlink → `validation_join_delta01_cut540_nw350_p35_tape10` | leave |
| `scripts/promote_backtest.py` | retargets LIVE if given a 12-seed root | keep code; **do not run** |
| `scripts/run_seeds.sh` | always prints `compare_backtests.py LIVE vs new` | keep; after cleanup that print is Follow300 vs old B |
| `config/trading.toml` `profiles.dota-map` | `base_size_usdc=200`, `layers=1`, `layer_step_ticks=2` | **poly-maker Avellaneda**. Do not change |
| `src/trader/*` | does not import `backtest.*` | do not import, do not share types |
| `BASE_SIZE_USDC` | 100, shared backtest+live meaning of one clip | leave at 100 |

Resume of LIVE is already dead on this branch (new fill/quote columns + `buy_ladder_policy`). `compare_backtests.py` still works (reads `summary.json`).

Dota F300 cannot be promoted until 12 summaries exist. Even then, this cleanup does not promote.

---

## 4. Dota F300 catalog — running

Root:

`data/backtests/dota_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow300`

Observed 2026-09-06:

| Seeds | State |
|---|---|
| 0, 1, 2 | complete (`summary.json` + manifest + results/fills/quote_events) |
| 3..11 | **running now**, 1 shard each. Checkpoints present (`manifest.json`, `results.parquet`, `fills.parquet`, `quote_events.parquet`). **No `summary.json` yet.** |
| `seeds.json` | still 3-seed (`keys: seeds, mean, sd`) |

Live processes (one unsharded `run.py` per seed) were started with the experimental flags:

```
--game dota --validation --name ladder-v2-lfollow300
--layers 3 --layer-step-ticks 1 --base-size-usdc 300
--buy-lifecycle parallel --ladder-reprice-mode follow
--buy-after-first-fill continue --sell-drop-block-delta 0
--model-dir data/backtests/dota_maker/ladder-v2-inputs/model
--signal-cadence-seed {3..11}
```

LoL F300 12-seed catalog is already complete:

`data/backtests/lol_maker/validation_join_delta01_cut540_nw350_p35_ladder-v2-lfollow300`

### Sequencing constraint

Fingerprint / CLI / `MakerQuotePolicy` changes **must wait** until seeds 3..11 write `summary.json`.

- In-flight processes already loaded old code; they can finish.
- If they crash and someone `--resume`s after `quote_policy=follow300-v1` (or after dropping `--layers` from the argv they used), `assert_manifest_matches` refuses.
- Cleanup that only deletes unused functions, without changing the written manifest keys those processes emit, can land in parallel. Safer default: **wait**.
- Do not kill, retarget, or merge those runs as part of cleanup.

---

## 5. Experiment verdicts (already decided)

| Variant | Verdict | Action |
|---|---|---|
| legacy 1-order B (B100/B200/B300/…) | drop as default | delete lifecycle |
| `fixed` reprice | drop | delete mode |
| LSTOP (`buy_after_first_fill=cancel`) | drop | delete mode |
| depth-at-submit (K1/K2) | drop; F300 better on profit and capital | delete sizing |
| sell-drop-block | drop; tails worse | delete gate |
| Follow300 | keep as backtest engine | hardcode |

Old C5 v1 report said “drop for live”. That still holds. This cleanup does not ship a ladder to live.

---

## 6. Real fixes to keep vs variants to delete

### Keep (Follow300 correctness, not knobs)

From `docs/experiments/buy-ladder-v2/progress.md`:

- **3.1** Fill credits the rung the order holds **now**. `_reattach_level` + `_detached` so a fill after cancel/reject/expiry still hits the last assignment. Tests: `test_full_fill_after_reattach_completes_current_level`, `test_partial_fill_before_reattach_does_not_complete_a_level`, `test_canceling_order_still_reserves_after_a_reattach`, `test_fill_after_cancel_ack_completes_the_level_it_last_held`.
- **3.3** Failed anchor blocks new BUYs while a position is open; do not invent fair 0/1. Tests: `test_anchor_failure_blocks_new_buys_while_long_on_the_other_token`, `test_anchor_failure_cancels_existing_buy_with_anchor_reason`, `test_anchor_failure_does_not_sell_on_a_fabricated_fair`. This already moved LIVE vs recomputed B100 by about −$3.91 net. Do not revert.
- **3.4** `episode_id`; a late BUY fill does not open a new episode. Test: `test_episode_survives_a_late_buy_fill_and_then_starts_a_new_one`.
- 1 ns unique cancel-send (Nautilus inflight heap). Comment already names the ceiling.
- `ENGINE_STARTING_BALANCE = 1_000_000` so maps do not share a sim pot across a batch.

Telemetry / capital (useful, keep):

- Fill columns: `episode_id`, `level_index`, `submit_level_index`, `level_moves`, `fair_at_fill`, `signal_age_seconds`, `gate_reason_at_fill`, `episode_buy_notional`, `episode_sell_proceeds`, `episode_buy_fill_index`, `position_cost_basis`, `reserved_buy_notional`.
- Quote kinds: `cancel_request`, `cancel_ack` (existing `canceled` still means “cancel left for the venue”).
- `src/backtest/wallet_path.py` `calculate_reserve_path`.
- summary wallet: `required_cash_with_reserves`, `required_cash_with_reserves_at_close`, `peak_reserved`. Keep `required_cash` (fills-only) beside them.
- `gate_no_cash_seconds` / `no_cash` reason.
- `volume_at_price` for **SELL `queue_ahead`** (not for BUY sizing).

Follow300 behavior that looks like a flag but is not:

- GRID latch until first fill, then follow (`_episode_prices_frozen`). Deleting `ladder_reprice_mode` is **not** “always `snap_to_book=True`”. `_choose_targets` currently snaps to the live book only after freeze. Correctness reviewer owns this.

### Delete with the variants

- **3.2** “fixed was not frozen” — only exists to make `fixed` real. Delete with the mode.
- `buy_lifecycle=legacy` and every `if not self._is_parallel()` branch.
- `buy_after_first_fill=cancel` → `_close_episode_buys` / `_episode_buys_closed`.
- Depth cap: `buy_sizing.py`, `buy_depth_cap_multiple`, `_keep_rung_target`, `_buy_rests_at`, `DEPTH_CAP_REASON`, `_depth_cap_blocked`.
- Sell-drop-block: `SellDropBlock`, `MidSample`, `_token_mids`, `_record_token_mids`, `_lookback_mid`, `_sell_drop_blocks_sell`, four config fields, `GateSeconds.sell_drop_block`.

---

## 7. File map

### 7.1 Delete files

| Path | Why |
|---|---|
| `src/backtest/buy_sizing.py` | depth-cap only |
| `tests/test_backtest_buy_sizing.py` | same |
| `scripts/analyze_buy_depth_cap.py` | four-arm experiment |
| `scripts/audit_buy_ladder_v2.py` | experiment completeness; operational tool is `report_seeds.py` |
| `docs/experiments/sell-drop-block/overnight.sh` | re-runs obsolete arms |
| `docs/experiments/sell-drop-block/resume-missing-seeds.sh` | same |
| `docs/experiments/sell-drop-block/lol-b200-after.sh` | same |
| `docs/experiments/sell-drop-block/summarize.py` | experiment-only consumer |

### 7.2 Simplify (do not rewrite)

**`src/backtest/strategy.py`** (~1840 lines)

Hardcode Follow300 as module constants. Remove from `DotaMakerConfig`:

- `buy_lifecycle`, `ladder_reprice_mode`, `buy_after_first_fill`
- `buy_depth_cap_multiple`
- `sell_drop_block_*`
- `layers`, `layer_step_ticks`, `base_size_usdc` as **settable** fields

Rung count, step, and $100 rung budget become constants used by `_ensure_buy_levels` / `_layer_budget` / `_rung_price`. Tests cannot pass `layers=1`.

Delete methods/state: `_is_parallel`, `_validate_lifecycle`, `_buy_quantity` depth-cap branch (replace with `buy_share_quantity(100, price)`), `_keep_rung_target`, `_buy_rests_at`, `_close_episode_buys`, `_episode_buys_closed`, `_depth_cap_blocked`, sell-drop + mid lookback.

Keep: `_evaluate` always takes the parallel path (BUY+SELL together; do not cancel SELL when BUY is gated). `_choose_targets` keeps GRID-latch until first fill, then follow. `_reattach_level`, `_detached`, episode begin/end, unique cancel-send.

`_evaluate` / `_choose_buy_targets` / `_reconcile_targets` currently `noqa: C901`. Delete first. Split only if still complexity 10.

**`src/backtest/run.py`**

- `MakerQuotePolicy` collapses to nothing user-settable, or a frozen record of the constants for the manifest.
- Delete CLI: `--layers`, `--layer-step-ticks`, `--base-size-usdc`, `--buy-lifecycle`, `--ladder-reprice-mode`, `--buy-after-first-fill`, `--buy-depth-cap-multiple`, `--sell-drop-block-*`.
- Delete `buy_size_policy()`, `BUY_SIZE_POLICY_FIXED`, `BUY_SIZE_POLICY_DEPTH`, `BUY_LADDER_POLICY = "buy-ladder-v2"`.
- Manifest fingerprint: **one** key `quote_policy = "follow300-v1"` plus the unchanged model/gates/latency/fill-model keys. Optionally also write the constants as documentation (`layers: 3`, …) but resume must refuse any catalog whose `quote_policy` is missing or not `follow300-v1`.
- Do **not** keep the six old knob keys in `expected` for resume: extra keys in old manifests are ignored; missing version would let a B200 checkpoint resume into Follow300 code.

**`src/backtest/maker_orders.py`**

Keep. Drop `UNAVAILABLE_DEPTH` if only depth-cap used it. Keep `volume_at_price`, `ladder_price`, `BuyLevel`, `DetachedOrder`, `LatchedDecision.fair_valid` / `fair_ts_ns`.

**`src/backtest/results.py`**

Drop `GateSeconds.sell_drop_block`. `_parse_gate_seconds` uses `.get(name, 0)` so old nested dicts with that key still load. Keep `no_cash`.

**`src/shared/types/backtest.py`**

Drop `gate_sell_drop_block_seconds`. Keep reserve wallet fields and `gate_no_cash_seconds`.

**`src/backtest/postprocess.py`**

`buy_ladder_assumption_lines`: one Follow300 sentence, or drop the mode-aware branch and put one line in `ASSUMPTIONS`. Keep reserve-cash assumption paragraph.

**`src/backtest/report.py`**

Identity rows: show Follow300 (`layers 3`, step 1, size $300) from manifest constants. Keep `base_size_usdc` row.

**`src/backtest/telemetry.py`**, **`src/backtest/wallet_path.py`**

Keep as-is.

**`Makefile`**

Revert the `backtest:` help string to operational flags. Do not list deleted ladder CLI.

**`pyrightconfig.json`**

`ignore` `"data"` instead of `"data/experiments"` exists because this branch tracked `model.txt` under `data/backtests`. Keep if those files stay tracked. Do not revert without untracking.

**`docs/experiments/README.md`**

One “code dropped” line on the three ladder/sell-drop/depth-cap rows. Do not rewrite reports.

### 7.3 Retain as historical (no code, no command refresh)

- `docs/experiments/buy-ladder-v2/` (protocol, progress, report, commands, baseline hashes)
- `docs/experiments/buy-depth-cap/` (protocol, report, summary.csv)
- `docs/experiments/sell-drop-block/report.md`, `STATUS.md`, `results.txt`, `overnight.log`

Same pattern as no-unwind / pred-ramp: report stays, runner dies. Do not “fix” `commands.md` to a CLI that no longer exists.

### 7.4 Do not touch (product / live / data purge)

- `config/trading.toml`, `src/trader/**`
- `../poly-maker`
- `scripts/promote_backtest.py` (code stays; no invocation)
- `scripts/run_seeds.sh`, `scripts/compare_backtests.py`, `scripts/report_seeds.py`
- `data/backtests/**` catalogs (including in-flight F300 seeds 3..11)
- LIVE symlinks
- `shared.constants.strategy.BASE_SIZE_USDC`

---

## 8. Tests

### 8.1 Policy: one fixture, 3×$100

`build_maker_config()` in `tests/test_backtest_maker.py` must construct Follow300 only. No `layers=` / `buy_lifecycle=` parameters.

Implications:

- `test_join_buy_is_limit_gtc_post_only_dollar_clip` / `test_buy_quantity_uses_fixed_backtest_clip`: expect **three** BUYs at join / join−1 tick / join−2 ticks, each sized at $100, not one $100 (and not three $33).
- `test_at_most_one_live_order_and_no_mirror_while_long`: rewrite. Allow 3 BUY rungs + at most one SELL. Keep “no opposite-token BUY while long”.
- Gate tests that currently assert “no submit” or “one cancel”: assert all three rungs are blocked/canceled. One `no_quote` per tick is still the existing recorder behavior — do not invent three `no_quote`s unless the strategy already does that.
- Tests that inspect a single live order id: pick by price/level, or assert the set of three prices.

Prefer adapting the existing strategy fixture. Extract a new helper module only if a test is already about a pure function (`nw_velocity_block_reason`, `buy_price_legal`, `ladder_price`, `round_buy_price`). Do not add a `GateProbe` abstraction.

### 8.2 Delete these tests

`tests/test_backtest_maker.py`:

- `test_legacy_rejects_layers_above_one`
- `test_legacy_rejects_cancel_after_first_fill`
- `test_parallel_legacy_still_allows_only_one_live_order`
- `test_fixed_partial_fill_freezes_prices_and_full_fill_completes_level` (if it is proving `fixed`)
- `test_after_top_fill_fixed_keeps_046_044_follow_aims_044_042` — drop the `fixed` arm; keep a **follow-only** assertion if it documents Follow300 prices after a top fill
- parametrized `ladder_reprice_mode` in `test_cutoff_and_lost_buy_gate_still_service_sell` and `test_dust_remainder_does_not_block_other_buys_or_game_end_cancel` — run follow only
- `test_fixed_keeps_prices_across_a_new_model_tick`
- `test_fixed_returns_to_the_saved_price_after_a_fair_block`
- `test_cancel_mode_pulls_every_remaining_buy_on_a_full_fill`
- `test_cancel_mode_pulls_the_partial_remainder_too`
- `test_cancel_mode_posts_no_new_buy_after_the_book_moves`
- `test_cancel_mode_keeps_a_fill_that_races_the_cancel`
- `test_cancel_mode_reopens_buying_only_in_the_next_episode`
- all `test_depth_cap_*`
- `test_config_rejects_a_non_positive_depth_cap`
- `test_fixed_mode_ignores_book_depth`
- all `test_sell_drop_block_*`

Whole file: `tests/test_backtest_buy_sizing.py`.

`tests/test_backtest.py`:

- `test_parse_args_records_quote_policy` (records deleted flags)
- `test_parse_args_records_the_depth_cap`
- `test_depth_cap_reaches_strategy_config_and_manifest`
- `test_resume_refuses_a_checkpoint_with_a_different_depth_cap`
- bad-flag rows for `--layers`, `--base-size-usdc`, `--ladder-reprice-mode`, `--buy-depth-cap-multiple`

Rewrite:

- `test_backtest_cli_exposes_only_operational_options` — those flags gone
- `test_run_manifest_records_model_name` — default is Follow300, `quote_policy=follow300-v1`
- `test_parse_args_game_defaults_to_dota` — no `args.layers == 1`
- `test_make_backtest_recipe_has_no_game_flag`
- `test_resume_rejects_old_manifest_missing_buy_ladder_keys` → reject missing/`buy-ladder-v2` / any non-`follow300-v1`
- `test_summary_assumptions_describe_buy_ladder_mode`

### 8.3 Keep (Follow300 engine)

- `test_parallel_posts_three_initial_ladder_buys` — change expected step from 2 ticks to **1 tick**, sizes from base/3 to **$100 each**
- follow-after-fill tests (not the `fixed` counterparts)
- reattach 3.1, anchor 3.3, episode 3.4
- `test_cancel_ack_precedes_replacement`, `test_shared_balance_does_not_double_spend_reserve`
- `test_queue_ahead_is_per_level_not_copied_from_tob`
- `test_partial_cancel_replace_uses_full_level_size_and_can_exceed_100` (full rung $100, not $100 total spend cap)
- `test_signal_flip_does_not_buy_opposite_token`
- `test_extra_buy_while_sell_live_updates_settle_and_inventory`
- `test_sell_flatten_waits_for_buy_cancels_and_late_fill_keeps_token`
- reserve-path tests in `tests/test_backtest_postprocess.py`
- `tests/test_promote_backtest.py` unchanged
- `tests/test_lol_backtest.py` — only if it asserts default policy; then Follow300

---

## 9. Old artifact / fingerprint consumers

| Consumer | After cleanup |
|---|---|
| `scripts/report_seeds.py` | keep; `summary.json` |
| `scripts/compare_backtests.py` | keep; LIVE vs Follow300 is a mixed size+lifecycle number (already stated in sell-drop report) |
| `scripts/promote_backtest.py` | keep; do not invoke; still requires 12 `summary.json` |
| `read_quote_events_checkpoint` | missing new columns still refuse. Old LIVE cannot resume. Good. |
| `_parse_gate_seconds` | extra `sell_drop_block` in old results ignored after field removal |
| `assert_manifest_matches` | **must** key on `quote_policy=follow300-v1`. Do not resume B200/LIVE/lfollow300-v2 into new code |
| in-flight Dota F300 seeds 3..11 | finish under **current** fingerprint; then they are historical catalogs. Later Follow300 runs use a new `--name` or accept that v2 manifests will not resume |
| `docs/experiments/sell-drop-block/summarize.py` | delete with scripts |

Completed v2 catalogs on disk (LoL F300 12-seed, Dota F300 seeds 0–2, B200, depth-cap, …) stay as evidence. After `follow300-v1`, they are not resumable. That is intended.

Do not glob or delete `data/`. Untracking experiment dumps is a later git decision, not this cleanup.

---

## 10. Bounded implementation plan

One cleanup PR after Dota F300 seeds 3..11 have `summary.json`. No live, no VPS, no `poly-maker`, no promote, no data glob, no new policy types.

### Phase 0 — wait (external, already started)

Dota F300 seeds 3..11, 1 shard each, already running. Cleanup does not start, stop, or resume them. When all twelve seeds have `summary.json`, parent can refresh `seeds.json` / `report_seeds.py`. Not this plan's job.

### Phase 1 — hardcode Follow300, delete CLI

- Constants: `LAYERS=3`, `LAYER_STEP_TICKS=1`, `RUNG_BUDGET_USDC=100`.
- `DEFAULT` / manifest `quote_policy=follow300-v1`.
- Delete experimental argparse flags and `MakerQuotePolicy` variant fields.
- `Makefile` help: operational flags only.

### Phase 2 — delete variant code in `strategy.py`

Always parallel + follow-after-fill + continue + fixed $100/rung. Remove depth-cap and sell-drop. Do not always-snap; keep GRID latch until first fill.

### Phase 3 — delete files in §7.1

### Phase 4 — tests

`build_maker_config` is Follow300-only. Adapt gate fixtures to 3 rungs. Delete variant tests. Rewrite CLI/manifest/resume tests.

### Phase 5 — docs index

Three README rows: code dropped. Leave reports.

### Phase 6 — green

```
PYTHONPATH=src:scripts:../prediction-market-backtesting \
  uv run --group backtest python -m pytest \
  tests/test_backtest_maker.py tests/test_backtest.py \
  tests/test_backtest_postprocess.py tests/test_backtest_validation.py \
  tests/test_backtest_telemetry.py tests/test_lol_backtest.py
```

Then `ruff check` on touched files. Split C901 only if still 10.

### Phase 7 — stop

Do not promote LIVE. Do not change `trading.toml`. Do not start another catalog in this PR.

Skipped unless asked: untracking `data/backtests` dumps; renaming `maker_orders.py`; sharing types with trader; 12-seed LoL already exists; Dota seeds 3..11 are already running.

---

## 11. Non-obvious hazards

1. **`trading.toml` `layers` ≠ backtest rungs.** Changing live `layers` to 3 is a product change. Forbidden.
2. **Do not set `BASE_SIZE_USDC=300`.** Shared constant is one $100 clip. Follow300 is three of them.
3. **GRID-latch until first fill.** Removing `ladder_reprice_mode` must not become `snap_to_book=True` on every tick. `_choose_targets` + `_episode_prices_frozen` is a correctness surface.
4. **Fingerprint extra keys are ignored.** Without `quote_policy=follow300-v1`, `--resume` on a B200 dir would mix old fills with Follow300 new maps.
5. **In-flight Dota seeds 3..11.** Fingerprint/CLI change before they write `summary.json` bricks a crash-resume. Wait.
6. **`run_seeds.sh` vs LIVE** will look like a huge win (size + lifecycle mixed). Do not treat that printout as promotion.
7. **Dota F300 promotion** still needs 12 summaries. This cleanup still does not promote.
8. **`pyrightconfig` ignore of all `data/`** exists because `model.txt` is tracked. Reverting it without untracking dumps pyright errors.
9. **C901 after deletion.** Delete first. Split only if still over 10 (Sep 4 pattern).
10. **Fix 3.3 already moved LIVE vs B100 ~−$3.91 net.** Follow300 catalogs include that fix. Do not revert 3.3 to make LIVE comparisons prettier.
11. **Three $100 rungs can exceed a $100 “spend cap” mental model.** Cancel+replace posts the full rung again. `test_partial_cancel_replace_uses_full_level_size_and_can_exceed_100` stays. Assumptions text already says this.
12. **`_cash_blocked_a_buy` currently calls `_buy_quantity(..., external_depth=...)`.** After depth-cap deletion it must use the fixed $100 clip only. Same for `_choose_buy_targets` (stop calling `volume_at_price` for BUY size; keep it for SELL queue).
13. **Nautilus `DotaMakerConfig` defaults.** If tests construct `DotaMakerConfig(...)` without the removed fields, they get Follow300. Any remaining `layers=1` kwargs fail typecheck — that is the point.
14. **`quote_events.parquet` is gitignored.** Reserve telemetry lives on disk only. Cleanup must not assume those files are in git.

---

## 12. Out of scope

- User's numerical read of F300 vs B200/LIVE (Dota 12-seed still filling).
- Correctness audit of 3.1 / 3.3 / 3.4 / latch-then-follow.
- Live / VPS / `poly-maker` / `promote_backtest`.
- Killing or restyling the running Dota seed 3..11 processes.
- Implementing this plan.
