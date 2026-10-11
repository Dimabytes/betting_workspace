# STEP-013 plan: investigation tools on the new adapter, exec replay, free ensembles, SIMULATOR-PLAN cleanup (B7 + B8 + C)

Repo `esports-trader` (C) on `main`, HEAD `056e868b`. All C line numbers are at that commit. The investigation folder is `I = betting_workspace/investigations/2026-10-08-liveb-postmortem` (gitignored, not in git, no history). Scratch for this step is `R13 = betting_workspace/tasks/sim-parity-fixes/orchestrate/work/impl-013/` (gitignored).

## 0. Result

After this step:

- The investigation tools run on product `main` with no product change: `live_net.py`, `journal_replay.py`, `run_exec_fixed.py`, `replica_repo.py`, `run_replica_repo.sh` and `run_extra.sh` import and start.
- `live_net.py` wakes the quoter on each live fill, the same way a heard fill does in the product.
- Two exec replays of wallet B's real orders on the 5 replica maps have run on the product tape and strip:
  - the plain replay, expected 63 of 63 live-filled orders filled;
  - the fixed replay (lost-cancel ends and book-time cancel ends), expected 1048.4 shares with every live-filled order matched.
  Each result sits next to its 2026-10-10 baseline, re-scored by the same `compare_exec.py`.
- Two free ensembles have run, 8 seeds each, on product two-sided code at the default `level_usdc = 20`. The only overlay is the 9034957701 restart.
  - 6 segments, prefix `pc`, compared against `fn` (1128.0 ± 30.0 shares against live's 1048.4).
  - 7 extra maps, prefix `xp`, compared against `xf` (7560 shares against live's 7543).
- `SIMULATOR-PLAN.md`:
  - The three stale sections at the bottom are removed.
  - New sections record the product-code results and the VPS log findings.
  - A link points to `betting_workspace/tasks/sim-parity-fixes/PLAN.md`.
- `uv run python -m basedpyright` in C reports 0 errors.

No file in C changes, so this step has no C commit. The W commit is `tasks/sim-parity-fixes/progress.txt` only.

## 1. Evidence

### 1.1 The product cycle the tools must use

- `DotaMakerStrategy._arm_wake` (`src/backtest/strategy.py:593-594`) only records `_core_deadline_ns`. The product calls `_wake_quoter` (`strategy.py:596-601`) to apply the `QUOTER_DEBOUNCE` and arm `_on_quoter` (`:611`). `_hear_fill` (`:482`) and the signal and kill alerts call `_wake_quoter`.
- Outside a cycle, `_submit_place` (`strategy.py:915`) routes to `_send_place` (`:922`), so a timer-driven submit still places.
- `VENUE_INSERT_MS = PLACE_LATENCY_MS[0]` (45) is in `src/backtest/live_latency.py:20`. `two_sided_core.py` no longer defines or re-exports it: its imports at `:14` are `BUY_CONFIRM_MS, MERGE_CALL_MS` from `live_latency`, and `NS_PER_MS` from `strategy.scheduling` at `:21`.
- `MatchKernelConfig` has no `order_latency_ns`. STEP-010 deleted `ORDER_INSERT_LATENCY_MS` and `order_latency_ns` (`progress.txt`, STEP-010).
- `--match-ids-file` runs get a per-seed latency draw. `load_seed_archive` returns an empty archive unless the run uses `--validation` or `--limit` (`src/backtest/run.py:2063-2065`), and `build_kernels(latency_seed=args.signal_cadence_seed)` is at `run.py:2425`. So 8 seeds give 8 different outcomes. None of the 12 maps is in `NAUTILUS_ZERO_FILL_SKIP_IDS` (checked with `rg` on `src/backtest/lol_inputs.py`).
- With `--level-usdc` omitted, a two-sided run gets `layer_usdc = 20.0` (STEP-012, `resolve_level_usdc`, `run.py:250-266`). The manifest records `layer_usdc`, `max_position_levels`, `latency_seed`, `signal_cadence_seed`, `framework_commit`, `quoter_recompute_ms` and `merge_call_ms` (`_stamp_strategy`, `run.py:1646-1662`).
- Tape v3 is product code: `onchain_retime.load_tape_times` (`src/backtest/onchain_retime.py:178`), called from `telonex_local.py:200`. `telonex_local.load_trade_times` no longer exists. `_rewrite_code_stamp` still exists (`telonex_local.py:381`), so the old investigation patch only changes the cache key and sets an attribute nothing reads.

### 1.2 What is broken in the investigation tools today

- `I/sim/strategy_check/live_net.py:36-39`: `_on_live_fill` drives the core, then calls only `self._arm_wake(out.next_wake_ns)`. On the new adapter that records a deadline and never starts a cycle, so a live fill never re-quotes. The fixed form already exists in `I/sim/replica/replica_repo.py:301-305` (`LivePositionMixin._on_live_fill`: `_arm_wake`, then `self._wake_quoter(now_ns)`).
- `I/sim/exec_replay/journal_replay.py:43`: `submit_ns = order["placed_ns"] - self._config.order_latency_ns` raises AttributeError in `on_start`. The module docstring (`:7`) says "submit = placed - order latency".
- `I/sim/replica/replica_repo.py:112` is a module-level `from backtest.two_sided_core import NS_PER_MS, VENUE_INSERT_MS`. This is an ImportError, so every `STRATEGY_PATH=replica_repo:*` run fails at strategy load. The other module-level imports still resolve: `:19-21`, `:282`, `:612` (`BUY_CONFIRM_MS` is imported into `two_sided_core`) and `:613`.
- `I/sim/exec_replay/run_exec_fixed.py:17` does `import tape_align`, and `:23-25` patches `telonex_local.load_trade_times` and `_rewrite_code_stamp`. That is inert apart from forking the tree-cache key. The docstring at `:5` names the book-aligned tape.
- `I/sim/replica/run_replica_repo.sh:8` and `I/sim/extra/run_extra.sh:10` do `cd .../esports-trader-cadence`. The orchestrator removed that worktree in STEP-010 (`progress.txt`). `run_extra.sh:12` exports `TAPE_BOOK_ALIGN=1`, which makes `run_with_align.py` apply the inert patch from the previous bullet. `run_extra.py` always runs `run_with_align.py` (its last lines).
- `ensemble_free.sh` and `run_replica_repo.sh` take their default caches from another session's scratchpad (`/private/tmp/claude-501/.../163a6623.../scratchpad/qprobe/*`). They must get `TREE_CACHE` and `NT_CACHE` explicitly.

### 1.3 What "without mixins" means here

- STEP-012: `MergeDelayMixin` on product two-sided code would add a second 3 s delay and shadow `_on_merge_done`, so merges would never apply. FillNotice, KillWake, MergeDelay and the latency mixins are product behaviour now and must not be stacked.
- `LiveHoldsStrategy(LiveHoldsMixin, TwoSidedCoreStrategy)` (`replica_repo.py:52-101`) only blocks bids inside the windows in `HOLDS`, and applies `SWITCH_HALF_SPREAD`. With no windows and no switch for a map it is inert (`_held` is False, so every call falls through to `super`).
- The bug holds in `I/work/replica/holds.json` and `I/work/extra/holds.json` (`dust`, `lost_cancel`) model live bugs that part A fixed. Two windows are a WS-loss pattern, not restarts: 9035078952 and 9034882975 each have ~63 s windows on both tokens. The only operational event left is the 9034957701 restart. Live h3 stopped at 14:47:23.299 and h6 started at 14:48:38.632 with the same position. In `holds.json` this is the two entries with `start_ns` 1791470843299331328 (token 1) and 1791470843299355392 (token 0), both ending at 1791470918632889600. `compare_replica.py` excludes the fills in that gap from both sides, but the sim's position at the h6 start depends on whether it quoted through the gap.

### 1.4 Exec replay baselines (2026-10-10)

- `I/work/sim-runs/validation_join_delta02_x015_cut480_p45_liveb1008-execreplay-fixed4/seed0`, with `I/work/exec-replay/orders_fixed4.json`, `replay_fixed4.jsonl` and `fixed4.log`. It covers 5 maps, took ~3.5 min, moved 4788 cancel ends to book times and fixed 8 lost cancels. Result: 1048.4 shares, PnL −12.97, every live-filled order matched. Manifest: hs 6, seed 0, framework `e2cfebb`.
- `...-execreplay-core/seed0`, with `orders_core.json` and `replay_core.jsonl`: plain replay, 63 of 63.
- The 63 filled orders span all 6 segments (5 maps). One segment cannot show 63 of 63, so both replays run on the 5-map set (`sim/ids_h3.txt` + `sim/ids_h6.txt`).

### 1.5 Ensemble references (`SIMULATOR-PLAN.md`, 2026-10-10)

- `fn`, 6 segments, 8 seeds, `--level-usdc` default 300 at that time: 1128.0 ± 30.0 shares (+7.6%), PnL −6.59 ± 5.16, mk30 −9.96, mk300 −15.17. Live: 1048.4 shares, −12.97.
- `xf`, 7 maps, 4 seeds, `--level-usdc 20`, MergeDelay and holds: 7560 shares (+0.2%), settle −59.72, mk30 −17.03, mk300 −10.73. Live: 7543, −95.42, −65.47, −66.62. 9034789047's no-cash window 12:25:20–12:43:37 is excluded on both sides (`EXCLUDED` in `sim/analysis/markout_*.py`).
- `xf` ran 2 seeds in parallel into the same run root without trouble: seeds 1 and 2 of h3 ran 18:46:03 → ~19:00, seeds 3 and 4 ran 19:00:21 → 19:15.

### 1.6 VPS findings to record (`tasks/sim-parity-fixes/PLAN.md` part C)

These come from the 2026-10-10 analysis of `docker compose logs live_b`. They are copied, not re-measured; this step has no VPS access.

- The 72 s merge on 9034882975 (14:46:38) was a stale relayer nonce plus the 30 s pause, not `_chain_lock`. A8 / STEP-003 fixed it. `SIMULATOR-PLAN.md:437` still says "possible cause `engine._chain_lock` … no VPS logs, not checked".
- 15:06:35: live decided to cancel the 0.26 bid at 35.373. The HTTP cancel took 670 ms against a day's p50 76, p90 134 and p99 702 ms over 11,906 B cancels, and the order left the book at 35.853. This is a latency tail, not missing mechanics. In the other 7 "saves" the cancel took 60–90 ms.
- In `docker compose logs live_b`, the line `trader core buy settled … wait_ms` gives the cancel decision time (`ts − wait_ms`), and `buy gone` gives the HTTP response time. Logs rotate at 5 × 10 MB, which is why A7 (B writes its own core trace, STEP-005) was needed.

### 1.7 `SIMULATOR-PLAN.md` today (526 lines, not in git)

- `PLAN.md` says it was committed as `e0dd1cb`. That is false: no such commit exists, and `investigations/` is gitignored. A backup copy is the only undo.
- Stale sections:
  - `## Итог в трёх строках` (`:454-458`);
  - `## Правка в esports-trader (сделана, не закоммичена)` (`:497-511`);
  - `## Шаги до «симулятор корректно считает вчерашний день»` (`:513-520`).
- Stale references:
  - `:479` "Исправлено (ниже)." will dangle once the sections are removed.
  - `:452` says to run `sim/analysis/` from `esports-trader-cadence`.
  - `:437` carries the `_chain_lock` guess.
- Kept: `## Доказательство…` (`:460`), `## Что не так в трёх симуляторах` (`:475`) and `## Что ещё стоит знать` (`:522`).

## 2. Changes

Do these in order: back up, fix the tools, prove the imports, then run. All edits are in the gitignored investigation folder. Nothing in C or `poly-maker` changes. Match each file's style and add no comments.

### 2.0 Backup and scratch

Create `R13/{backup,cache/tree5,cache/nt5,cache/tree7,cache/nt7,exec,logs}`. Use `cp`, never `mv`, to copy these into `R13/backup/` before editing:

- `live_net.py`
- `journal_replay.py`
- `run_exec_fixed.py`
- `replica_repo.py`
- `run_replica_repo.sh`
- `run_extra.sh`
- `SIMULATOR-PLAN.md`

Write the following:

- `R13/ids_5maps.txt` = `sim/ids_h3.txt` followed by `sim/ids_h6.txt` (9034957701 and the 4 h6 maps).
- `R13/restart_holds.json` = `{"9034957701": [the two restart entries from 1.3, copied from work/replica/holds.json]}`. Select them by `start_ns` in {1791470843299331328, 1791470843299355392} and keep their fields unchanged.

### 2.1 `I/sim/strategy_check/live_net.py`

In `_on_live_fill` (`:36-39`), add `self._wake_quoter(now_ns)` after `self._arm_wake(out.next_wake_ns)`. Keep `_arm_wake`: it records the core deadline that the cycle reads. This is the same two-line ending as `replica_repo.py:304-305`. The module docstring still holds.

### 2.2 `I/sim/exec_replay/journal_replay.py`

Make two changes:

- At `:43`, the submit time becomes `order["placed_ns"] - VENUE_INSERT_MS * NS_PER_MS`. Import `VENUE_INSERT_MS` from `backtest.live_latency` and `NS_PER_MS` from `strategy.scheduling`. The engine adds the 45 ms insert floor (`ENGINE_INSERT_FLOOR`, `run.py:330-334`), so the order rests at the live placement time, as before.
- At `:7`, the docstring changes from "submit = placed - order latency" to "submit = placed - the 45 ms venue insert".

The rest is compatible and does not change: `_on_submit` → `_submit_place` outside a cycle, the `_ack_venue_cancel(*, now_ns, client_order_id)` override, unknown fills pending (`lifecycle.py:721`), and `_on_cancel` calling `cancel_order` directly at engine cancel latency 0.

### 2.3 `I/sim/exec_replay/run_exec_fixed.py`

- Delete `import tape_align` (`:17`) and the three patch lines (`:23-25`). If `onchain_retime` and `telonex_local` are then unused, drop them from the import at `:19`; `run` stays.
- Rewrite the docstring sentence at `:5` to say the tape is the product's print-time tape (tape v3, `onchain_retime.load_tape_times`).
- Keep the lost-cancel ends from `LIVE_DB` `unsettled_buys` (`CANCEL_FLIGHT_MS = 30`) and the `CANCEL_BOOK_TIMES` ends.

### 2.4 `I/sim/replica/replica_repo.py`

At `:112`, replace the import with `from backtest.live_latency import VENUE_INSERT_MS` plus `from backtest.two_sided_core import NS_PER_MS`. The latter is still a module attribute there. Using `from strategy.scheduling import NS_PER_MS` is also fine; pick one. Change nothing else. The exact-replay classes (`LiveSyncStrategy`, `LiveTimingMixin` and others) use cycle fields the new adapter renamed. They are not used here and stay broken; this goes in 5.

### 2.5 Shell scripts

- `I/sim/replica/run_replica_repo.sh:8`: change `esports-trader-cadence` to `esports-trader`, and update the header comment (`:2`) "(worktree esports-trader-cadence)" to "(esports-trader main)".
- `I/sim/extra/run_extra.sh:10`: the same `cd` fix. At `:12`, drop `TAPE_BOOK_ALIGN=1` from the export.
- Leave `ensemble_free.sh`, `ensemble_par.sh`, `run_with*.py` and `run_extra.py` unchanged. Their stale defaults (`RUN_WITH=run_with_align.py`, `TAPE_BOOK_ALIGN=1`, scratchpad caches) are overridden by env in §7.

### 2.6 Runs

The commands are in §7. In short:

1. Smoke run `run_live_net.py` on `sim/ids_220.txt`, hs 6, seed 0.
2. Plain exec replay (`run_exec_replay.py`) on the 5 maps.
3. Fixed exec replay (`run_exec_fixed.py`, with `LIVE_DB` and `CANCEL_BOOK_TIMES`) on the 5 maps.
4. Score both new replays and both baselines with `compare_exec.py`.
5. The 6-segment ensemble, `pc1`..`pc8`. Seed 1 runs alone to warm `cache/tree5` and `cache/nt5`, then seeds 2-8 run 4 at a time.
6. The 7-map ensemble, `xp-h3` and `xp-h6`, seeds 1..8. Seed 1's two halves run together to warm `cache/tree7` and `cache/nt7`, then the remaining 14 jobs run in a pool of 4.
7. Summaries: `ensemble_summary.py`, `markout_seeds.py` and `markout_extra.py`.

Each run started from C writes to `C/data/backtests/...`:

- `run_replica_repo.sh` moves its own output to `I/work/sim-runs`.
- For the live_net run and the two exec runs, `mv` the new seed dir to a new path `I/work/sim-runs/<same dir name>/seed0`. That path does not exist yet; check first.
- `run_extra.sh` writes to `I/work/extra/backtests/...` itself.

### 2.7 `I/SIMULATOR-PLAN.md` (Russian, same style as the file)

1. Delete the three sections listed in 1.7 (`:454-458`, `:497-511`, `:513-520`).
2. At `:479`, replace "Исправлено (ниже)." with the real fixes: `4773d0b1` (print time), tape v3 in the product (STEP-007), and the mirror strip (STEP-011).
3. At `:437`, replace the `_chain_lock` sentence with a one-line pointer: stale relayer nonce, fixed in STEP-003, see «Находки по логам VPS».
4. At `:452`, change "запуск из `esports-trader-cadence`" to "запуск из `esports-trader`".
5. Under the H1, add one line linking the fix plan: `betting_workspace/tasks/sim-parity-fixes/PLAN.md` (relative `../../tasks/sim-parity-fixes/PLAN.md`).
6. Before `## Доказательство…`, add `## Проверка на продуктовом коде (2026-10-11, STEP-013)`. It contains:
   - The code and setup:
     - the C HEAD SHA and framework `e2cfebb`;
     - that the scripts now run from `esports-trader` `main` with no mixins;
     - the restart-only overlay;
     - that the bug holds were dropped and why;
     - `level_usdc` 20 by default.
   - The exec replay: one table with rows for the old `core`, the new plain replay, the old `fixed4` and the new fixed replay. Columns: filled in both, live only, sim only, live shares, sim shares, sim PnL, mk30.
   - The 6-segment ensemble: the same per-segment table as the existing «Свободный бэктест» one, plus the total and live's percentile, with `fn` on a reference row. Note that `fn` ran at level 300 and `pc` at 20.
   - The 7-map ensemble: shares, settle, mk30 and mk300 as mean ± sd over 8 seeds, a live row, and `xf` on a reference row.
   - The commands and wall times, and one line on the exact-replay classes left broken.
   - A short conclusion: what moved against the references and by how much. No speculation beyond the numbers.
7. Add `## Находки по логам VPS (2026-10-10)` with the three bullets from 1.6 and their source line.

## 3. Edge cases

- **Name collisions destroy data.** `run_replica_repo.sh` does `rm -rf $I/work/sim-runs/<dir>` before moving. Before any run, confirm that none of these exist:
  - `I/work/sim-runs/*-pc[0-9]*`
  - `I/work/sim-runs/*-s13-*`
  - `I/work/extra/backtests/dota_maker/*_xp-h[36]`
  - `I/work/replica/compare_pc*`
  - `I/work/extra/logs/xp-*`
  - `C/data/backtests/dota_maker/*liveb1008-s13-*`
  If any exists, pick another prefix rather than delete.
- **Stale caches.** Never pass the scratchpad caches or delete `~/.cache/esports-trader/telonex-tree`. The 5-map and 7-map sets use different `--own-archive-root`, so they get separate tree/nt dirs.
- **Concurrent cache writes.** Warm each cache with one seed before parallel seeds. The two halves of one 7-map seed use disjoint maps, so they can warm together.
- **`HOLDS` env.** `HOLDS=` (empty) has to reach the 7-map runs so the bug holds are not loaded. `run_extra.sh` uses `${HOLDS-…}`, so an exported empty value wins. For the 6 segments, `HOLDS` is the restart file and `SWITCH_HALF_SPREAD` is left unset, so the default `9034957701:14:48:38.632:6` applies.
- **The 957full run** is h3 until the switch at 14:48:38.632, then h6. With the restart hold it has no bids from 14:47:23.299 to 14:48:38.632. The 4 h6 maps have no overlay at all.
- **Do not set `INSERT_MS`.** `run.ORDER_INSERT_LATENCY_MS` no longer exists, and `run_with.py` would only set a dead attribute.
- **The no-cash window on 9034789047** (12:25:20–12:43:37) is excluded by the markout scripts on both sides. The sim starts with ample cash, so including that window would inflate sim shares.
- **Seeds:** check every new manifest (§7). If seeds produce identical totals, stop: the latency seed is not reaching the kernels.
- **Exec replay drift.** The STEP-011 mirror strip and the product tape v3 can move fills against `fixed4`, which used the investigation's `tape_align` v3 and the pre-STEP-011 strip. If "live only" is above 0, or sim shares on matched orders differ from 1048.4:
  - list the differing orders from the output CSV;
  - classify each one as tape time, strip, or cancel end, by checking its time against the book;
  - record that in the report.
  Do not tune constants and do not patch C in this step.
- **If a run crashes in product code,** stop and report the traceback. A product fix is a separate reviewed change, not part of this step.

## 4. Checks (no new tests)

No product code changes, so no pytest tests are added. Each check below fails if the related logic breaks:

- **Import smoke.** From C, with `PYTHONPATH=src:scripts:../prediction-market-backtesting:I/sim/replica:I/sim/exec_replay:I/sim/strategy_check`, `python -c "import replica_repo, journal_replay, live_net"` exits 0. It fails today on `replica_repo.py:112`.
- **`py_compile`** passes on the 4 edited `.py` files. `zsh -n` passes on the 2 edited `.sh` files.
- **Live_net smoke** completes with a non-empty `fills.parquet` for 9035220432. Record fills and shares next to the same map's rows in the `...-livenetcore` run, for information only.
- **Exec replays.**
  - The plain replay shows 63 filled in both and 0 live-only.
  - The fixed replay prints `cancel ends from the book: 4788` and `lost-cancel ends fixed: 8`, matching `fixed4.log`. `compare_exec.py` shows 0 live-only, and live shares of 1048.4 on the matched orders.
  - Report the sim-only count and shares as they are.
- **Ensembles.** All 16 + 16 runs finish. Each manifest has:
  - `layer_usdc == 20.0`
  - `max_position_levels == 9`
  - `quoter_recompute_ms == 10`
  - `merge_call_ms == 3000`
  - `framework_commit` starting with `e2cfebb`
  - `latency_seed` and `signal_cadence_seed` equal to the seed
  `ensemble_summary.py pc` prints 8 seeds, and `markout_seeds.py xp:1..8` prints 8 per-seed totals.
- **Typecheck.** `uv run python -m basedpyright` in C reports 0 errors.
- **C is clean.** `git -C C status --short` shows only the user's pre-existing edits, and no path this step touched.

## 5. Out of scope

- Any change in `esports-trader` or `poly-maker`. Tuning sim constants.
- STEP-014: the Nautilus crash check, full validation, goldens, promotion.
- Fixing the exact-replay classes (`LiveSyncStrategy`, `LiveTimingMixin`, `draw_tail`'s `RESPONSE_MS`) and the other scripts that still `cd` into the cadence worktree (`run_replica.sh`, `run_one.sh`, older ensemble scripts) or say so in their docstrings (`sim/analysis/*`).
- Re-measuring the VPS numbers. No ssh, no `docker compose logs`.
- Editing `PLAN.md`'s false `e0dd1cb` claim or `port.md`'s `_chain_lock` guess. Mention the `e0dd1cb` claim in the report; the orchestrator decides.
- Setting `passes`.

## 6. Risks and hard parts

- **The numbers will move against `fn` and `xf`, by design.** The product now has:
  - fill-notice timing, the merge delay and level 20;
  - the 45 ms insert and the 10 ms quoter recompute;
  - the mirror strip and kill wakes.
  The overlay also no longer carries the bug holds. The step records the new numbers; it does not have to reproduce the old ones. The 6-segment cap drops from 9 × $300 to 9 × $180, so expect fewer shares than `fn`.
- **The exec replay may miss 1048.4.** The risk is the STEP-011 mirror strip, and product tape v3 against the investigation patch. That is a regression signal, so it must be explained order by order (3), not averaged away.
- **Wall time and memory.** One backtest process uses ~3.3 GB; the Mac has 32 GB and 12 CPUs. Never run the two ensembles at the same time: 8 processes would need ~26 GB.
  - The 5-map runs take ~15 min: 3 to 4 min each for the exec replays, plus the smoke run.
  - The 6-segment ensemble takes ~35 min: ~10 min for seed 1, then 2 rounds at 4 parallel.
  - The 7-map ensemble takes ~55 min: ~15 min for seed 1, then 14 jobs in a pool of 4.
  - Markouts take a few minutes.
  - Total: about 1.75–2 h. Report the measured times.
- **`SIMULATOR-PLAN.md` has no git history.** Edit it only after `R13/backup/` holds a copy, and diff against the copy before finishing.

## 7. Verification commands

Run in zsh (the arrays and `setopt` below are zsh) from `esports-trader/` on `main`. Do not run during a live map (AGENTS.md).

```bash
C=/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
I=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-08-liveb-postmortem
R13=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/sim-parity-fixes/orchestrate/work/impl-013
J=$I/work/data/trader_live_b/wallet/engine_journal/live.jsonl
OWN=$I/work/data/trader_live_b_core_trace
RUN=validation_join_delta02_x015_cut480_p45
cd $C && git rev-parse --short HEAD

# collision check: expect no output
( setopt nullglob; print -l $I/work/sim-runs/*-pc[0-9]* $I/work/sim-runs/*-s13-* $I/work/extra/backtests/dota_maker/*_xp-h[36] \
  $I/work/replica/compare_pc* $I/work/extra/logs/xp-* data/backtests/dota_maker/*liveb1008-s13-* )

# imports and syntax after the edits: expect exit 0
PYTHONPATH=src:scripts:../prediction-market-backtesting:$I/sim/replica:$I/sim/exec_replay:$I/sim/strategy_check \
  .venv/bin/python -c "import replica_repo, journal_replay, live_net"
.venv/bin/python -m py_compile $I/sim/strategy_check/live_net.py $I/sim/exec_replay/journal_replay.py \
  $I/sim/exec_replay/run_exec_fixed.py $I/sim/replica/replica_repo.py
zsh -n $I/sim/replica/run_replica_repo.sh && zsh -n $I/sim/extra/run_extra.sh

export TELONEX_TREE_CACHE=$R13/cache/tree5 TELONEX_CACHE_ROOT=$R13/cache/nt5
ARGS=(--signal-cadence-seed 0 --half-spread-ticks 6 --strategy two-sided --own-archive-root $OWN)

# live_net smoke on one map (~2 min)
PYTHONPATH=src:scripts:../prediction-market-backtesting:$I/sim/strategy_check LIVEB_JOURNAL=$J LIVE_NET=$R13/live_net_220.json \
  .venv/bin/python $I/sim/strategy_check/run_live_net.py --match-ids-file $I/sim/ids_220.txt --name liveb1008-s13-livenet $ARGS \
  > $R13/logs/livenet.log 2>&1

# plain exec replay, 5 maps (~4 min): expect 63 filled in both, 0 live only
PYTHONPATH=src:scripts:../prediction-market-backtesting:$I/sim/exec_replay LIVEB_JOURNAL=$J \
  REPLAY_ORDERS=$R13/exec/orders_core.json REPLAY_LOG=$R13/exec/replay_core.jsonl \
  .venv/bin/python $I/sim/exec_replay/run_exec_replay.py --match-ids-file $R13/ids_5maps.txt --name liveb1008-s13-execcore $ARGS \
  > $R13/logs/execcore.log 2>&1

# fixed exec replay, 5 maps (~4 min): expect "4788" book cancel ends and "8" lost cancels fixed in the log
PYTHONPATH=src:scripts:../prediction-market-backtesting:$I/sim/exec_replay LIVEB_JOURNAL=$J \
  LIVE_DB=$I/work/data/trader_live_b/wallet/live.db CANCEL_BOOK_TIMES=$I/work/replica/cancel_book_times.json \
  REPLAY_ORDERS=$R13/exec/orders_fixed.json REPLAY_LOG=$R13/exec/replay_fixed.jsonl \
  .venv/bin/python $I/sim/exec_replay/run_exec_fixed.py --match-ids-file $R13/ids_5maps.txt --name liveb1008-s13-execfixed $ARGS \
  > $R13/logs/execfixed.log 2>&1

# move the three new run dirs out of data/backtests (each target must not exist yet)
for n in livenet execcore execfixed; do d=${RUN}_liveb1008-s13-$n; mkdir -p $I/work/sim-runs/$d && mv data/backtests/dota_maker/$d/seed0 $I/work/sim-runs/$d/seed0; done

# score new replays and the 2026-10-10 baselines with the same script
.venv/bin/python $I/sim/exec_replay/compare_exec.py $I/work/sim-runs/${RUN}_liveb1008-s13-execcore/seed0 $R13/exec/orders_core.json $R13/exec/replay_core.jsonl $R13/exec/core.csv
.venv/bin/python $I/sim/exec_replay/compare_exec.py $I/work/sim-runs/${RUN}_liveb1008-s13-execfixed/seed0 $R13/exec/orders_fixed.json $R13/exec/replay_fixed.jsonl $R13/exec/fixed.csv
.venv/bin/python $I/sim/exec_replay/compare_exec.py $I/work/sim-runs/${RUN}_liveb1008-execreplay-core/seed0 $I/work/exec-replay/orders_core.json $I/work/exec-replay/replay_core.jsonl
.venv/bin/python $I/sim/exec_replay/compare_exec.py $I/work/sim-runs/${RUN}_liveb1008-execreplay-fixed4/seed0 $I/work/exec-replay/orders_fixed4.json $I/work/exec-replay/replay_fixed4.jsonl

# 6-segment free ensemble, product code, restart-only overlay (~35 min)
export RUN_WITH=run_with.py CLASS=LiveHoldsStrategy HOLDS=$R13/restart_holds.json TREE_CACHE=$R13/cache/tree5 NT_CACHE=$R13/cache/nt5
unset SWITCH_HALF_SPREAD INSERT_MS RUN_ARGS
$I/sim/replica/ensemble_free.sh pc 1 1
$I/sim/replica/ensemble_par.sh ensemble_free.sh pc 2 8 4
.venv/bin/python $I/sim/replica/ensemble_summary.py pc
.venv/bin/python $I/sim/replica/ensemble_summary.py fn
SIX=1 PYTHONPATH=src:scripts .venv/bin/python $I/sim/analysis/markout_extra.py fn:1,2,3,4,5,6,7,8 pc:1,2,3,4,5,6,7,8

# 7-map free ensemble, no overlay, default level 20 (~55 min)
X=$I/sim/extra
export CLASS=LiveHoldsStrategy HOLDS= TREE_CACHE=$R13/cache/tree7 NT_CACHE=$R13/cache/nt7
$X/run_extra.sh $X/ids_h3.txt 3 xp-h3 1 & $X/run_extra.sh $X/ids_h6.txt 6 xp-h6 1 & wait
for s in 2 3 4 5 6 7 8; do echo "$X/ids_h3.txt 3 xp-h3 $s"; echo "$X/ids_h6.txt 6 xp-h6 $s"; done | xargs -P 4 -L 1 $X/run_extra.sh
PYTHONPATH=src:scripts .venv/bin/python $I/sim/analysis/markout_extra.py xf:1,2,3,4 xp:1,2,3,4,5,6,7,8
PYTHONPATH=src:scripts .venv/bin/python $I/sim/analysis/markout_seeds.py xp:1,2,3,4,5,6,7,8
# sd over the 8 per-seed totals from the last table: population sd (np.std), same as ensemble_summary.py

# manifests: expect no output (every run at level 20, 9 levels, 10 ms recompute, 3 s merge, e2cfebb, seed = dir seed)
.venv/bin/python - <<'EOF'
import json, pathlib
I = pathlib.Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-08-liveb-postmortem/work")
runs = [(p, int(p.parent.parent.name.rsplit("pc", 1)[1])) for p in I.glob("sim-runs/*liveb1008-*-pc[0-9]*/seed0/manifest.json")]
runs += [(p, int(p.parent.name[4:])) for p in I.glob("extra/backtests/dota_maker/*_xp-h[36]/seed*/manifest.json")]
assert len(runs) == 32, len(runs)
for p, seed in runs:
    m = json.loads(p.read_text())
    bad = {k: m.get(k) for k, v in dict(layer_usdc=20.0, max_position_levels=9, quoter_recompute_ms=10, merge_call_ms=3000,
           latency_seed=seed, signal_cadence_seed=seed).items() if m.get(k) != v}
    if bad or not str(m.get("framework_commit")).startswith("e2cfebb"):
        print(p, bad, m.get("framework_commit"))
EOF

# typecheck: expect 0 errors
uv run python -m basedpyright

# C untouched by this step: expect only the user's pre-existing edits
git status --short
```

If a manifest key is nested rather than top-level, read it from where `_stamp_strategy` and `manifest_for_selection` put it. The `xf` manifest has `layer_usdc`, `latency_seed` and `signal_cadence_seed` at top level.

Do not paste `summary.json`. Report numbers from the compare and markout scripts.

## Commit and progress

There is no C commit, because no C path changes. The investigation folder is gitignored; do not `git add -f` it.

In W, append the STEP-013 entry to `tasks/sim-parity-fixes/progress.txt`. It should contain:

- the C HEAD used;
- the list of edited investigation files, and the backup dir;
- the exec replay numbers against the baselines;
- the `pc` and `xp` ensemble numbers against `fn` and `xf`;
- the run times;
- the `e0dd1cb` note;
- the ceiling: the restart is the only overlay, and the exact-replay classes are still broken.

Commit only that file with `git add tasks/sim-parity-fixes/progress.txt`. Use the message `Record STEP-013 progress: investigation tools on the product adapter, exec replay and free ensembles on main`. Leave `passes` false.
