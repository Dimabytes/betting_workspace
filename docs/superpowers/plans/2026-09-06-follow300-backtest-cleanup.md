# Follow300 Backtest Cleanup Implementation Plan

> **For agentic workers:** implement task by task, with a review after each independently testable change. The user explicitly requires Herdr and Cursor `cursor-grok-4.6-high` for delegated work. Read the workspace and sibling project briefings before executing commands. This document is a plan, not authorization to deploy or merge.

**Goal:** Make Follow300 the single Dota and LoL backtest policy, delete discarded experiment code and switches, preserve the execution fixes, and make reports describe capital and incomplete runs correctly.

**Architecture:** Keep the existing replay, signal, strategy, order-state, telemetry, and postprocessing boundaries. Specialize the current implementation to three $100 BUY rungs one tick apart, with simultaneous BUY/SELL and the existing latch-then-follow behavior. Delete the policy-selection object and its argument plumbing; do not create a strategy registry or keep alternative production behavior for tests.

**Tech Stack:** Python 3.13+, uv, NautilusTrader 1.226.0, the existing read-only `prediction-market-backtesting` checkout, pandas/Parquet, pytest, ruff, basedpyright.

**Spec:** `betting_workspace/docs/analysis/2026-09-06-follow300-review.md`, especially the final decision, accounting definitions, and validity limits. The numerical decision must use the completed twelve-seed Dota Follow300 catalog, not its three-seed preview.

**Decision basis:** All nine requested additional seeds finished successfully on 2026-09-06 at 09:35:31 UTC; all twelve Dota Follow300 catalogs contain 454 completed maps. Against B200, Follow300 improves mean net PnL from $625.40 to $956.33 for Dota and from $1773.80 to $3306.14 for LoL, with higher reserve-inclusive deposit requirements and worse absolute tail losses. Choose this as a practical backtest default, not as a claim of universal or statistically established superiority. Dota B100 retains the better net/deposit ratio. No live rollout is part of this decision.

## Global Constraints

- Product repo: `../esports-trader`; execute all product commands and git operations there.
- Current user branch: `ladder-experiment`, starting source commit `0d3eafd561706d819c15f3867431d6257c5c125f`.
- The explicitly requested Dota Follow300 seeds 3–11 are complete, one unsharded process each. Preserve all original seed artifacts; the root `seeds.json` now summarizes twelve seeds.
- Scope of this conversation: analysis, completion of those seeds, and a reviewable cleanup plan. Implementation is a subsequent step.
- Do not change `src/trader`, `config/trading.toml`, live/paper daemons, either backtest `LIVE` symlink, models in `production`, or `poly-maker`.
- `poly-maker` and `prediction-market-backtesting` are read-only.
- Keep the gates, model timing, 85 ms network latency, venue delay, queue model, SELL pricing, 10-second exit settling, dust handling, and settlement assumptions unchanged by cleanup.
- Do not change shared `BASE_SIZE_USDC` to 300. The new total is a backtest-only three-rung policy.
- Run Python through `uv run python`; tests needing the engine use `--group backtest` and the existing PYTHONPATH.
- Follow the project rules: named values, action-verb functions, required arguments, typed data structures, no new `__init__.py`, no dead branches.
- Historical catalogs remain readable for analysis. Resuming a catalog from a different policy must fail.
- No broad sweep of new strategy variants is part of cleanup. Replays after editing are behavioral regression checks.

## Contract to preserve

At a valid model tick, build up to three BUY levels on the selected token: best bid, one cent lower, two cents lower. Each new order uses the existing rounding rule for a $100 notional. All existing price/fair/depth availability gates still apply.

Before the first BUY execution of the episode, preserve the prices latched on the model tick. After that execution, eligible uncompleted rungs follow the current book. A partially executed order may be canceled and replaced with a fresh full $100 rung; $300 is not a cap on cumulative spending or inventory. A completed logical rung stays completed until the episode ends.

Maintain one current token per episode. A matching resting order can move to another logical rung without losing queue priority. Credit a fill to its current rung assignment, including late fills through `DetachedOrder`. Retain simultaneous remaining BUY rungs and at most one SELL. Retain cancel-in-flight inventory and reserves, reduce-only SELL, and the unique cancel-send timestamp workaround.

An anchor failure blocks BUY regardless of inventory. SELL may use only a valid, sufficiently recent carried fair; no fabricated 0/1 fair. Preserve the existing episode identity fix. Treat the documented cross-episode late-fill limitation as an audit issue, not an invitation to silently change the strategy during deletion.

## File map

| Files | Responsibility and action |
|---|---|
| `src/backtest/strategy.py` | Specialize configuration and runtime branches to Follow300; retain execution fixes. |
| `src/backtest/run.py` | Remove policy object, experimental CLI, forwarding arguments; version the fixed policy and stop swallowing arbitrary assertions. |
| `src/backtest/maker_orders.py` | Retain order/rung types, remaining-notional accounting and rung reassignment. Specialize rung spacing if its only production caller needs it. |
| `src/backtest/buy_sizing.py` | Delete the rejected depth-cap implementation. |
| `src/backtest/results.py`, `src/shared/types/backtest.py` | Remove the sell-drop-only gate fields; retain all fill/reserve accounting. |
| `src/backtest/postprocess.py` | Fix assumptions serialization; make capital fields and filtered telemetry consistent. |
| `src/backtest/report.py`, `scripts/report_seeds.py` | Display initial deposit including working orders, cash-only estimate separately, and explicit execution-failure counts. |
| `scripts/run_seeds.sh` | Wait for each child and propagate failures; preserve seed/shard operation. |
| `scripts/promote_backtest.py` | Validate the finished catalog before changing a symlink; do not invoke promotion in this task. |
| `scripts/audit_buy_ladder_v2.py` | Remove only after required completeness checks have a generic operational home. |
| `scripts/analyze_buy_depth_cap.py`, `tests/test_backtest_buy_sizing.py` | Delete discarded experiment code and its tests. |
| `docs/experiments/sell-drop-block/{overnight.sh,resume-missing-seeds.sh,lol-b200-after.sh,summarize.py}` | Delete runners and report generator for the rejected variants. |
| `Makefile`, `docs/as-is.md`, `docs/experiments/README.md` | Describe the one new backtest default and operational CLI. |
| `docs/experiments/{buy-ladder-v2,buy-depth-cap,sell-drop-block}` | Keep historical reports/results; mark old commands as historical at the source commit. Replace stale current-status statements. |

## Task 1: Make completion and accounting reports truthful

**Files:** `src/backtest/run.py`, `src/backtest/postprocess.py`, `src/backtest/report.py`, `src/shared/types/backtest.py`, `scripts/run_seeds.sh`, `scripts/report_seeds.py`, `scripts/promote_backtest.py`; related tests in `tests/test_backtest_validation.py`, `tests/test_backtest_postprocess.py`, `tests/test_report_seeds.py`, `tests/test_promote_backtest.py`.

**Interfaces:** Keep `format_terminal_report(payload)` and the current reserve-path API. Add `validate_catalog(run_root: Path, expected_seeds: Sequence[int]) -> None` to `scripts/report_seeds.py`, callable by the multi-seed report/promotion path. It returns normally only for a complete catalog and must not silently reduce the requested set to discovered summaries.

- [ ] Preserve the completed pre-cleanup catalog hashes and comparison report. Confirm no replay process is still using the source tree.
- [ ] Fix `(*ASSUMPTIONS, ...)`: `ASSUMPTIONS` is a string, so the current code creates hundreds of one-character entries. The replacement is:

```python
"assumptions": (ASSUMPTIONS, *buy_ladder_assumption_lines(manifest)),
```

  Add a regression assertion to the existing summary test that `payload["assumptions"][0] == ASSUMPTIONS`, not only that the extra ladder lines exist.
- [ ] Filter both fills and quote events to completed map ids before building reserve paths. Reuse `_clean_quote_events_for_results`; do not add another reader. Extend the existing synthetic terminated-map fixture with an outstanding BUY and assert the failed map contributes neither PnL nor reserve. The current LoL crash path already discards its telemetry, so this correction must not change the catalog numbers reviewed here.
- [ ] Show `required_cash_with_reserves`, `required_cash_with_reserves_at_close`, and `peak_reserved` in the terminal and cross-seed reports. Keep `required_cash` explicitly labeled as the executed-fills-only estimate. Add `net_per_required_deposit = net_pnl / required_cash_with_reserves`; never present this ratio as a constant-budget replay or annualized ROI. Preserve old artifact reading without inventing reserves for old summaries that lack them.
- [ ] Make the catalog validator reject missing seed directories, missing required artifacts, duplicate map ids, differing map universes, summary/result count mismatches, incompatible manifests, and any `terminated_early`. Compare full manifests except the seed field; the new fixed policy has one uniform schema. Historical mixed-schema catalogs are analysis inputs, not candidates to resume or promote.
- [ ] Let the execution wrapper pass its requested count to `report_seeds.py <run_root> --expected-seeds "$SEEDS"`; this is an operational completeness assertion, not a strategy switch. Standalone historical reporting can keep discovery but must label the actual count and incomplete maps. `promote()` always supplies `range(LIVE_SEEDS)` to the validator.
- [ ] Replace `run_seeds.sh` bare background `wait` with explicit waits for every child both at seed and shard levels. Preserve all exit codes and finish waiting for siblings before returning failure. A shell-level regression should run fake child processes with one failure and verify that the driver returns nonzero even when a later child succeeds.
- [ ] Stop labeling any caught `AssertionError` as a known `PositionOpened` failure. The simplest policy is to let the run fail and preserve its checkpoint. If a narrowly recognized engine exception is retained for diagnostics, the final catalog still returns a failure status and cannot be promoted. Add a regression where `run_batch` raises an unrelated assertion and assert that it propagates.
- [ ] Reproduce LoL `116855104460702379` with seed 0 and `116566854547769589` with seed 3 in separate diagnostic output directories before changing execution. Capture the real exception and order/fill sequence with the broad assertion handler disabled. Use the original Follow300 parameters and the recorded model hash for the pre-cleanup replay. Determine whether the retained order/episode logic causes the failure or the read-only framework does. Do not hide these maps with a filter, label the old catalog clean, or modify the frozen framework. If a project-side fix is needed, isolate it from deletion, add the concrete failing order sequence as a regression, and replay the affected map/seeds afterward. If it requires an upstream framework correction, document that unresolved dependency and withhold a claim of an error-free LoL baseline; the default-selection evidence remains explicitly limited to the 1290 jointly completed maps.
- [ ] Make `promote()` invoke the same completeness check before touching `LIVE`. Extend its existing fake-catalog tests with a twelve-summary directory containing one failed result and a directory missing fills. Both must reject with the old symlink untouched. Do not run real promotion.
- [ ] Once the generic check covers the retained invariants, delete `scripts/audit_buy_ladder_v2.py`. Its existing `main()` only prints `PROBLEMS` and still returns exit 0; do not carry that behavior into the replacement.
- [ ] Run the affected tests. The tests should distinguish malformed/incomplete catalogs from valid ones and check accounting with concrete dollars; avoid tests that only echo implementation constants.

## Task 2: Specialize the engine, CLI and resume boundary as one change

**Files:** `src/backtest/strategy.py`, `src/backtest/run.py`, `src/backtest/maker_orders.py`, `src/backtest/results.py`, `src/shared/types/backtest.py`; delete `src/backtest/buy_sizing.py`.

**Interfaces:** `DotaMakerConfig` continues to carry market, signal and timing data. It no longer exposes `layers`, `layer_step_ticks`, `base_size_usdc`, lifecycle, reprice, after-fill, depth-cap or sell-block options. Remove the `quote_policy` object parameter from `build_strategy_configs`, `run_batch`, `replay_matches` and `build_run_manifest`, then update their callers. The fixed manifest version key described below remains mandatory. Engine/config/CLI removal is one atomic, testable change; do not commit a half-removed configuration.

- [ ] Put the fixed backtest constants next to the strategy that consumes them; use those same constants to describe the manifest:

```python
BUY_LEVEL_COUNT = 3
BUY_LEVEL_STEP_TICKS = 1
BUY_LEVEL_USDC = 100.0
BUY_POLICY_VERSION = "follow300-v1"
```

- [ ] Delete `MakerQuotePolicy`, `DEFAULT_MAKER_QUOTE_POLICY`, `buy_size_policy()`, their constructors, and all forwarding arguments. Remove obsolete literal unions and validation sets. Keep the separate engine starting balance.
- [ ] Reduce BUY quantity calculation to the existing size-rounding utility:

```python
def _buy_quantity(self, *, price: float) -> Decimal:
    return Decimal(str(buy_share_quantity(base_size_usdc=BUY_LEVEL_USDC, price=price)))
```

  Remove `_keep_rung_target`, `_buy_rests_at`, depth-cap branches/reasons and the rejected sizing import. Do not delete `volume_at_price` or queue-at-level telemetry: execution still uses them.
- [ ] Delete `_is_parallel()` and retain its current true branch at every caller. Retain at most one SELL and rung occupancy checks; delete the legacy global one-order barrier.
- [ ] Delete the `fixed` reprice branch and `_snap_latched_prices()` indirection, but retain `snap_to_book=False` before the first fill when consuming a latch. In the post-fill path, `snap_to_book` follows the existing first-fill state. Rename `_episode_prices_frozen` to `_episode_has_buy_fill` only if done consistently; its state is still required.
- [ ] Delete `buy_after_first_fill=cancel`, `_episode_buys_closed`, `_close_episode_buys()` and all related branches. Keep episode-end cancellation after a SELL flattens inventory; it is a different behavior.
- [ ] Delete `SellDropBlock`, mid-history buffers, `_record_token_mids`, `_lookback_mid`, `_sell_drop_blocks_sell`, their calls, and the sell-drop gate counter/type. Keep general position cost basis and fill telemetry.
- [ ] Keep current-rung attribution, `_detached`, fair validity/timestamp, episode identity, the 1 ns cancel workaround, post-only orders and reduce-only SELL unchanged.
- [ ] Keep archived result parsing compatible: `_parse_gate_seconds` may ignore an extra old sell-drop field. This is a reader concern, not an alternative strategy branch.
- [ ] First delete branches, then run ruff complexity checks. Extract a focused helper only if the remaining function is still over the limit; do not replace deleted switches with a generic policy framework.

### Continuation of Task 2: Freeze the CLI, manifest and resume boundary

**Files:** `src/backtest/run.py`, `src/backtest/postprocess.py`, `src/backtest/report.py`, `Makefile`, `tests/test_backtest.py`, `tests/test_lol_backtest.py`, `tests/test_backtest_postprocess.py`.

**Interfaces:** Both game entrypoints still use `src/backtest/run.py`. Operational options remain: map/validation selection, game, name, limit, seed, resume, shard/merge, warm-cache. The research model path comes from the existing game-specific defaults.

- [ ] Remove these CLI arguments entirely: `--layers`, `--layer-step-ticks`, `--base-size-usdc`, `--buy-lifecycle`, `--ladder-reprice-mode`, `--buy-after-first-fill`, `--buy-depth-cap-multiple`, all four `--sell-drop-block-*`, and `--model-dir`. Remove their namespace reads and experimental validation. This follows the Sep 3 cleanup commit `952f160`.
- [ ] Before removing `--model-dir`, verify the canonical Dota and LoL research model bytes match the pinned pre-cleanup models used in regression replays. Compare SHA256, not path strings. If they differ, preserve both model artifacts and run before/after diagnostics against one identical snapshot in an isolated temporary replay harness; do not change research/production pointers to force a match.
- [ ] Version the manifest with `buy_ladder_policy="follow300-v1"`. Retain descriptive scalars `base_size_usdc=300.0`, `layers=3`, `layer_step_ticks=1` so reports can identify the policy; these are fixed metadata, not controls. Remove discarded-mode metadata from new runs. Preserve model and framework fingerprints and game-specific dataset hashes.
- [ ] Add regression tests asserting a fresh canonical Dota/LoL manifest has the fixed policy identity, and `assert_manifest_matches` refuses both an old B200 manifest and a `buy-ladder-v2` Follow300 manifest. Existing comparison readers can still read them.
- [ ] Parameterize a CLI rejection test over every removed flag. Exercise ordinary `--validation --name cleanup-check --game dota` and the LoL equivalent and assert they parse without policy options.
- [ ] Replace the mode-dependent assumption helper with fixed-policy text explaining latch-then-follow, three $100 rungs, no replenishment of completed rungs, and possible cumulative spending above $300. Historical reports must obtain old policy identity from their own manifests rather than be relabeled Follow300.
- [ ] Update Makefile help and report identity. Keep `backtest`, `lol-backtest`, seed/shard controls and normal report tools.

## Task 3: Remove experiment tests/runners and verify behavior is unchanged

**Files:** `tests/test_backtest_maker.py`, `tests/test_backtest.py`, `tests/test_backtest_validation.py`, `tests/test_backtest_postprocess.py`, `tests/test_backtest_telemetry.py`, `tests/test_lol_backtest.py`; remove depth-cap tests/script and rejected sweep runners listed in the file map.

**Interfaces:** Test helpers construct the production three-rung strategy. Do not retain a configurable one-rung runtime solely to keep old tests easy.

- [ ] Delete legacy-only, fixed-only, stop-after-fill, depth-cap and sell-block tests. Rewrite mixed tests to retain only Follow300 coverage. In particular, the useful partial-fill behavior in `test_fixed_partial_fill_freezes_prices_and_full_fill_completes_level` must remain covered under the Follow300 contract even though its fixed-mode assertion disappears.
- [ ] Update gate fixtures to expect three legal BUY targets or inspect the relevant selected order. Replace the old one-live-order invariant with no opposite-token BUY, at most one SELL, and no duplicate active order for a logical rung.
- [ ] Preserve and adapt these named regressions: `test_full_fill_after_reattach_completes_current_level`, `test_partial_fill_before_reattach_does_not_complete_a_level`, `test_canceling_order_still_reserves_after_a_reattach`, `test_fill_after_cancel_ack_completes_the_level_it_last_held`, the three `test_anchor_failure_*` tests, `test_episode_survives_a_late_buy_fill_and_then_starts_a_new_one`, `test_cancel_release_defers_venue_cancel_to_unique_ns`, `test_shared_balance_does_not_double_spend_reserve`, the cutoff/cancel-race tests, and reserve-ledger tests.
- [ ] Add a direct regression that a book move before first fill leaves the latched rung prices unchanged, while the same move after the first fill reprices only eligible uncompleted rungs. Use the existing fake books/events; assert actual order prices and ids, not private boolean flags.
- [ ] Separately test a fresh model tick before and after first fill. `_rebuild_latch` must still re-anchor/re-snap at a new model tick under Follow300; this is distinct from a book-only tick before first fill. Cover both callers when inlining `_snap_latched_prices`.
- [ ] Save a pre-cleanup diagnostic replay outside the canonical catalogs for the deterministic map set already named by the v2 tests: Dota `8837869969`, `8911784562`, and worst F300 map in the initial three-seed mean `8933879286`; use seed 0. Include completed LoL map `115564793879469302`, whose seed-0 fills include partial execution after rung reassignment. Run the same maps after cleanup with the canonical CLI.
- [ ] Compare sorted fills on `(match_id, ts_ns, order_id, side, token_index, price, quantity)` plus episode/rung attribution, and compare map PnL, rebate and reserve outputs. Differences in policy metadata and corrected assumptions text are expected; execution or PnL differences fail the cleanup check. If a real execution fix changes a path, separate and document that fix before treating results as a deletion-only regression.
- [ ] Run:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_backtest.py tests/test_backtest_maker.py tests/test_backtest_postprocess.py tests/test_backtest_telemetry.py tests/test_backtest_validation.py tests/test_lol_backtest.py tests/test_report_seeds.py tests/test_promote_backtest.py tests/test_compare_backtests.py
uv run python -m ruff check src/backtest src/shared/types/backtest.py scripts/report_seeds.py scripts/promote_backtest.py tests/test_backtest.py tests/test_backtest_maker.py tests/test_backtest_postprocess.py tests/test_backtest_telemetry.py tests/test_backtest_validation.py tests/test_lol_backtest.py tests/test_report_seeds.py tests/test_promote_backtest.py
uv run python -m basedpyright
```

  Record any pre-existing type errors separately; do not claim the entire project passed if only changed-code diagnostics are clean. Run the full suite once before handoff because config and shared report types changed. Do not repeat passing suites without new changes.

## Task 4: Record the decision and prepare a small reviewable diff

**Files:** `docs/as-is.md`, `docs/experiments/README.md`, the three experiment report/status directories, and this review/plan.

- [ ] Record the final twelve-seed comparison, capital definitions, residual risks, source commit, model hashes and exact catalog paths in the decision report. Keep LoL's two omitted map identities visible; paired analysis is evidence, not a repair of failed executions.
- [ ] Mark obsolete command lists as historical and pinned to their original commit. Do not change old commands to imply that they ran under the new implementation. Replace stale current-status headings in the ladder report/progress with a link to the new decision.
- [ ] Delete obsolete runnable scripts listed above. Retain compact historical reports and raw results needed to audit the decision. Do not broadly delete data/model archives as part of a code cleanup.
- [ ] Inspect the final diff for every discarded switch and helper:

```bash
rg -n 'buy_lifecycle|ladder_reprice_mode|buy_after_first_fill|buy_depth_cap_multiple|sell_drop_block|SellDropBlock|MakerQuotePolicy' src/backtest src/shared/types/backtest.py scripts tests
git diff --stat
git diff --check
```

  Hits must be limited to intentional historical-reader/rejection tests, if any. No alternative executable strategy remains.
- [ ] Confirm the live/trader/config paths and both `LIVE` symlinks have no diff. The ordinary backtest CLI now runs Follow300; preserved historical `LIVE` references do not select its runtime behavior. Keep historical/deployed references distinct from the new research default until the separately requested live design is done.
- [ ] Commit in reviewable units only when implementing: reporting/completeness corrections, strategy specialization with tests, and historical documentation cleanup. Do not merge or push as part of this plan.

## Acceptance criteria

1. One normal CLI command runs Follow300 for Dota or LoL without strategy switches.
2. No legacy/fixed/stop/depth/sell-block executable alternative remains, including config-only test escape hatches.
3. The retained Follow300 regression replays have identical execution and PnL paths unless a separately documented defect fix explains a change.
4. Working and canceling BUY reserves are visible in capital reporting; the old fills-only denominator is not presented as the required deposit.
5. Incomplete runs and unrelated assertions cannot become successful promotion candidates.
6. The old experiment evidence remains interpretable; mixed-policy resume is impossible.
7. Live trading, production models, frozen sibling projects and baseline symlinks are untouched.
8. Both known LoL failure scenarios have an explicit disposition and captured evidence. Any unresolved execution failure remains visible in the decision and blocks labeling its catalog complete; dropping the affected rows is not a fix.

## Self-review

The requested simplification includes size/layer/model override flags, not just sell-block and depth-cap. Real order/anchor/episode fixes and reserve telemetry remain. Removing the fixed-only correction is intentional because its entire mode is deleted. Diagnostic replays validate deletion equivalence; they are not another strategy search. A separate live-port design is still required to reproduce this behavior in the trader.
