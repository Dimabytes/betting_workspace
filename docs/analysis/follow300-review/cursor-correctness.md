# Follow300 correctness audit: `ladder-experiment` vs `main`

Independent code audit of whether Follow300 (3 BUY rungs × $100, 1 tick, parallel, follow, continue, no sell-drop-block, no depth sizing) can become the sole backtest default. No product changes. Statistics are the parent's to recompute; this document is code correctness and what would invalidate promotion.

**Verdict: do not make Follow300 the sole backtest default yet.** Keep the ladder-v2 bookkeeping fixes. Leave the default as legacy 1×$100 until the size-matched 12-seed pair and the open races are settled. Follow300 is a pinned catalog on this branch, not a drop-in replacement for `main`.

Branch: `ladder-experiment` @ `0d3eafd`. Base: `main` @ `eac5690`. Working tree: esports-trader on `ladder-experiment`.

---

## Keep from ladder v2 (do not revert)

These are real bugs vs pre-fix / `main`. Promoting F300 is a separate decision from keeping them.

| Fix | Where | What breaks if reverted |
|---|---|---|
| Fill credits **current** rung, not submit rung | `src/backtest/strategy.py:375-376`, `440-473`, `1479-1488`; `src/backtest/maker_orders.py:95-137` | Reattach after L2 fill marks the wrong level done |
| Failed anchor blocks BUYs with a position open; no fair=0/1 | `src/backtest/strategy.py:784-800`, `928-946` | Dire BUY at TOB with no fair (legacy too; this is why B100 ≠ old B) |
| `fixed` does not re-snap after first fill | `src/backtest/strategy.py:981-985`, `1004-1015` | `fixed` behaves as `follow` on every model tick |
| Episode id + resume on late fill while idle | `src/backtest/strategy.py:514-518`, `1508-1532` | Late fill silently opens a new episode |

`DEFAULT_MAKER_QUOTE_POLICY` is still legacy (`src/backtest/run.py:227-239`): `layers=1`, `layer_step_ticks=2`, `base_size_usdc=100`, `buy_lifecycle="legacy"`, `buy_after_first_fill="continue"`, `buy_depth_cap_multiple=None`, sell-drop delta `0`. F300 is CLI-only today.

---

## Actionable findings

### 1. High — `except AssertionError` is not PositionOpened-specific

`src/backtest/run.py:752-771`. `MAX_MATCHES_PER_BATCH = 1` (`run.py:135`), so every map is a 1-match batch. Any `AssertionError` in `run_batch` is stubbed as `nautilus_position_opened` with zero PnL and no telemetry (exception before `records` are kept).

Observed LoL stubs, 0 leaked fills (matches the crash path):

- F300 seed0 and B200 seed0: `116855104460702379` (`lol-gen-dk-2026-07-18`)
- F300 seed3 only: `116566854547769589` (`lol-edg-tes-2026-08-05-game1`)

Does **not** currently misstate stored PnL/reserves for completed maps: those rows contribute 0 fills and 0 engine. Absolute net is the same as counting them as zero. `summarize_arm` drops them from `completed` (`src/backtest/postprocess.py:536-548`), so per-match rates are slightly high (1/1292). F300 vs B200 paired maps: seed0 shared skip is fine; seed3 F300 skip is F300-only — drop that map in paired tests.

**Blocks default:** a Follow300 default will skip more fill-path asserts (3 in-flight BUYs). Match the exception text, or this will hide non-PositionOpened failures as empty maps.

### 2. High — F300 is not a $300 spend cap (intentional, but default-incompatible as labeled)

Replacement after partial posts the full rung again (`docs/experiments/buy-ladder-v2/progress.md`; `src/backtest/postprocess.py:653`). Three $100 rungs can buy well over $300 in one episode. Engine cash is `$1_000_000` (`src/backtest/run.py:142`); `_can_afford` (`src/backtest/strategy.py:1604-1622`) never binds. Wallet `required_cash*` is post-hoc.

Plan §8 net-at-W (skip BUY if free cash < order) was not implemented. A default named Follow300 will not simulate a $300 wallet.

### 3. Medium — default knobs ≠ Follow300 even if you flip lifecycle

`DotaMakerConfig.layer_step_ticks` default is **2** (`src/backtest/strategy.py:140`); F300 needs **1**. Setting `layers=3` without `--layer-step-ticks 1` is a different ladder. Promotion must pin all of: `parallel`, `layers=3`, `layer_step_ticks=1`, `base_size_usdc=300`, `follow`, `continue`, `buy_depth_cap_multiple=None`, `sell_drop_block_delta=0`.

### 4. Medium — late BUY fill after a new episode already started (unverified residual)

Documented ceiling (`docs/experiments/buy-ladder-v2/progress.md` §3.4). Code:

- `on_order_canceled` → `_evaluate` (`src/backtest/strategy.py:555-556`) can `_begin_episode` in the same visit.
- `_apply_buy_fill` restores the old episode only if `_episode_token_index is None` (`src/backtest/strategy.py:514-518`).
- Otherwise it adds size to the current episode and sets `_position_token_index` from the fill’s token.

No test covers “new episode already started.” Parallel 3-rung F300 has more leftover BUYs at flatten than legacy, so this window is larger.

Parent checked every saved fill of Dota F300 seeds 0–2 and LoL F300/B200 seeds 0–11: zero negative per-token inventory, zero overlapping opposite-token inventory, zero discrepancy between cumulative inventory and `position_after`, zero overfilled order quantities (tolerance 1e-5). Mixed-token map counts in these catalogs are sequential episodes, not overlapping two-token inventory.

Preserve this race as an **unverified residual**, not demonstrated historical PnL corruption.

### 5. Low / latent — unfiltered quote events in reserve path

`build_summary_payload` cleans fills, not quote events (`src/backtest/postprocess.py:672-673`). `calculate_reserve_path` then sees all events (`src/backtest/postprocess.py:571`). Crash path records no events, so this is **not a financial error** on current F300/B200/B300 artifacts. Independent ledger match on Dota F300/B200/B300 seed0 and LoL F300 0,1,3 and B200 0,1 confirms stored reserves for those seeds. Mention unfiltered quote events only as a latent bug: pass `_clean_quote_events_for_results` into the reserve tape so a future non-crash `terminated_early` cannot leak opens.

### 6. Low — audit CLI exits 0 despite PROBLEMS

Rejecting terminated runs in `SeedAudit.ok` is **desired** (`scripts/audit_buy_ladder_v2.py:42-48`, `:164-165`). The actual bug is `main()` prints PROBLEMS and always returns (`:180-198`): no `sys.exit(1)` when `RunAudit.ok` is false. A driver that checks only the process exit code will treat a LoL F300 12-seed catalog with intentional stubs as success.

---

## Are historical F300 / B200 / B300 comparisons valid?

| Contrast | Valid for what | Not valid for |
|---|---|---|
| **LoL F300 vs LoL B200**, 12 seeds | Mixed **size + lifecycle** on the same universe/model/gates; crash stubs have no leaked fills; seed0 skip is shared | Isolating “ladder vs one order” or “$300 vs $200”. Overnight report already says this. Seed3 F300-only skip: drop that map in paired tests (n=1290) |
| **Dota F300 vs Dota B200** | Qualitative “ladder $300 vs live clip $200” on seeds 0–2 (B200’s extra 9 seeds are not in the F300 3-seed mean) | Protocol size pair. At audit time Dota F300 seeds 3–11 were launching (seed3 had no `summary.json`); parent reports no code change during those runs |
| **Dota F300 vs Dota B300** | The protocol pair (3×$100 follow vs 1×$300 legacy), **seeds 0–2 only** | 12-seed claim. B300 is still 3 seeds. Seed noise on B600 0–2 vs 12-seed mean was ~$188 (`progress.md` §19) |
| **New B100 vs old B** | Same 454 maps; delta is fix 3.3 (anchor while long) | Treating old C5 as F300 |
| **Old C5 vs LFOLLOW / F300** | Invalid | Different code (reattach/anchor/episode) and C5 is 3×~$33 not 3×$100 |
| **LoL F300 vs LoL LIVE $100** | Invalid as a ladder test | Size and lifecycle both change; overnight report says so |

Reserves vs fills-only cash: LoL F300 seed0 `required_cash` $341 vs `with_reserves` $868 — use the reserve column for deposit talk; `roi_with_rebate` still divides by fills-only cash (`src/backtest/postprocess.py:511-522`).

Depth-cap and sell-drop-block are **off** on these F300/B200/B300 manifests. Do not mix those catalogs into a Follow300-default decision.

Observed catalog completeness used for this audit:

- LoL F300: 12/12 seeds, 1292 unique maps each; term on seed0 and seed3 only; mean net $3306.14
- LoL B200: 12/12 seeds, 1292 unique; term on seed0 only (same GEN vs DK map as F300 seed0); mean net $1773.80
- Dota F300: seeds 0–2 complete (454 maps, 0 terminated); seeds 3–11 were in flight
- Dota B200: 12-seed catalog exists (from experiment docs / prior audit)
- Dota B300: 3 seeds (0–2)

---

## Unverified (not used as blockers)

- Whether any swallowed `AssertionError` was *not* Nautilus `PositionOpened` (logs say PositionOpened; the `except` does not check).
- Whether replay L2 `volume_at_price` includes own orders. Irrelevant to F300 **sizing** (`buy_depth_cap_multiple` is null); only telemetry `queue_ahead`.
- Dota F300 seeds 3–11 and a 12-seed F300 vs B300 contrast — in flight at audit time.
- Cross-episode late fill after a new episode already started: possible in code, not shown in saved fills of Dota F300 0–2 or LoL F300/B200 0–11.
- Protocol leftovers that are not F300-default blockers but still open: P100, LSTOP, LFOLLOW at 12 seeds, B900/LFOLLOW900, 250 ms latency, OOS after 2026-09-03, `analyze_buy_ladder_v2.py`.

---

## If you still change the backtest default later

1. Keep the v2 assignment/anchor/episode/reserve code.
2. Pin the full F300 tuple (including `layer_step_ticks=1`).
3. Narrow the crash `except` to the PositionOpened assert.
4. Do not describe F300 as a $300 wallet or spend cap unless net-at-W is implemented in the strategy, not only in `wallet_path`.
5. Treat LoL F300 vs B200 as a mixed contrast; wait for Dota 12-seed F300 vs B300 before calling the ladder the default on Dota.
6. Make `audit_buy_ladder_v2.py` exit non-zero when `RunAudit.ok` is false; keep treating `terminated_early` as a problem.
