# STEP-014 plan: Nautilus crash check, follow300 Dota and LoL validation, goldens, promotion (B1.3 + B9)

Repo `esports-trader` (C) on `main`, HEAD `056e868b`. All C line numbers are at that commit. Framework `../prediction-market-backtesting` is on `local-day-native` at `e2cfebb2…` with a clean tree. Its `.so` SHA-256 is `15233bfc…8e2c`, the same as after STEP-006. Scratch for this step is `R14 = betting_workspace/tasks/sim-parity-fixes/orchestrate/work/impl-014/` (gitignored).

## 0. Result

After this step:

- Every runnable map from the Nautilus skip set, plus `dota2-ty-pari-2026-07-18-game2`, has run through `--match-id` with seeds 0, 1 and 2 on the final strategy code. The outcome of each (map, seed) pair is recorded.
- One of two paths has been taken:
  - **A. Nothing crashed.** The skip set, its stop reason and the `nautilus_zero_fill` branches are deleted. That is commit C1.
  - **B. Something crashed.** The set stays. The crashing maps and seeds go into `progress.txt`. `ty-pari` is added to the set if it was one of the crashes (commit C1b). Nautilus is not patched.
- The 8 follow300 smoke goldens have been recaptured, with the per-fixture delta recorded (commit C2). Full `make test`, `basedpyright` and `pre_commit run --all-files` are green.
- Dota and LoL follow300 validations (seeds 0, 1 and 2) have run on the final code. They are compared against the current `LIVE`, and the shares, PnL before and after rebate, and fill counts are in `progress.txt`.
- `LIVE` for `dota_maker` and `lol_maker` points at the new runs. The run artifacts are committed the way earlier promotions committed them (commit C3).
- The `rawfix` and `tapefix` Dota runs and their logs are gone (commit C4).
- `progress.txt` has the STEP-014 entry (W commit). `passes` stays false.

## 1. Evidence

### 1.1 The skip set and every place that reads it

- Set and constants: `src/backtest/lol_inputs.py:37-71`.
  - `:37-41` is a `ponytail:` comment.
  - `:42-51` are the 10 named ids: 7 LoL and 3 Dota.
  - `:52-65` is `NAUTILUS_ZERO_FILL_SKIP_IDS`.
  - `:66` is `NAUTILUS_ZERO_FILL_STOP_REASON = "nautilus_zero_fill"`.
  - `:67-70` is the empty-tape comment and `EMPTY_SIGNAL_TAPE_STOP_REASON`.
  - `:71` is `NO_REPLAY_STOP_REASONS = frozenset({NAUTILUS_ZERO_FILL_STOP_REASON, EMPTY_SIGNAL_TAPE_STOP_REASON})`.
- `src/backtest/run.py`:
  - imports at `:76-77`;
  - `engine_skip_ids` at `:2406` (empty under `--match-id`, otherwise the set);
  - the zero-fill `fault_results` rows at `:2408-2418`;
  - the `match_id not in engine_skip_ids` filter inside `empty_signal_ids` at `:2438-2446`;
  - the empty-tape rows appended to `fault_results` at `:2447-2464`;
  - `NO_REPLAY_STOP_REASONS` at `:1136`, which stays.
- `src/backtest/seed0_replay.py`:
  - import at `:30`;
  - a stop-reason check at `:98-100`, which is dead because the framework never emits `nautilus_zero_fill` (`rg` in `../prediction-market-backtesting` finds no such string);
  - a `try/except AssertionError` around `run_batch` at `:156-178`, which only rewrites the message.
- `scripts/report_seeds.py`: imports `NO_REPLAY_STOP_REASONS` at `:14` and uses it at `:497`. The `validate_catalog` docstring at `:533-534` says zero-fill faults are allowed.
- `src/backtest/shared_archive.py:80` uses `NO_REPLAY_STOP_REASONS` and stays.
- `src/backtest/results.py` has no zero-fill branch. `engine_fault_match_result` (`:287-333`) also builds the empty-tape rows (`run.py:2455`), so it stays unchanged. The feature lists `results.py`, but there is nothing to delete there.
- Tests that pin the zero-fill path:
  - `tests/test_promote_backtest.py:97-110` (`test_promote_allows_known_nautilus_zero_fill_faults`);
  - `tests/test_backtest_validation.py:705-719` (`stop_reason="nautilus_zero_fill"` at `:712` and `:717`);
  - `tests/test_lol_backtest.py:642-653` (monkeypatches the set at `:647`).
- Docs:
  - `docs/as-is.md:94-96` describes the set. The user has an uncommitted edit in the same file at `:104`.
  - `docs/extraction-oracle.md:86` also describes it.
  - Experiment docs mention it as history.

### 1.2 How `--match-id` behaves (the crash-check tool)

- `--match-id` bypasses the set (`run.py:2406`) and the empty-tape guard (`:2440`). Its output goes to `data/backtests/<game>_maker/match_<id>[_<name>]` (`build_report_dir`, `run.py:946-948`), which carries no seed. Each seed therefore needs its own `--name`, or it overwrites the previous one, since `clear_run_artifacts` runs at `:2126`.
- The default log level is INFO for `--match-id` (`:2269`). `BACKTEST_LOG_LEVEL=WARNING` makes it quieter.
- `MAX_MATCHES_PER_BATCH = 1` (`run.py:237`), so in a validation run every map is its own engine batch. A `--match-id` replay with `--signal-cadence-seed s` is the same engine input as validation seed `s` for a grid_v1 map: same cadence seed, same `build_kernels(latency_seed=…)` at `:2425`.
- A crash surfaces as an uncaught bare `AssertionError` from `nautilus_trader/model/events/position.pyx:331` (`PositionOpened`), with a nonzero exit (`fw-crash.md`).
- An empty signal tape under `--match-id` raises `ValueError("signal timestamps must not be empty")` (`src/backtest/strategy.py:341`). Validation records the same map as `empty_signal_tape` before the engine runs, so this is not a Nautilus crash.
- For LoL, `--match-id` is filtered by the league whitelist first (`lol_inputs.py:321-332`). It raises `ValueError("LoL match … is not an eligible backtest card")` when the map is outside it.

### 1.3 The maps (read-only query on the current LIVE runs and LoL inputs, 2026-10-11)

| map | id | game | in current LIVE | LIVE stop reason |
|---|---|---|---|---|
| dota2-huliga-lynx-2026-08-29-game1 | 8972289868 | dota | yes, grid_v1 | nautilus_zero_fill |
| dota2-aur1-liquid-2026-07-11-game1 | 8891371442 | dota | yes, grid_v1 | nautilus_zero_fill |
| dota2-ty-lgd-2026-06-07-game3 | 8842722527 | dota | yes, grid_v1 | nautilus_zero_fill |
| dota2-ty-pari-2026-07-18-game2 | 8902431810 | dota | yes, grid_v1 | completed in seeds 0, 1 and 3 |
| lol-gen-dk-2026-07-18 | 116855104460702379 | lol | yes, grid_v1 | nautilus_zero_fill |
| lol-dnf-fox1-2026-07-30-game2 | 115548147900684659 | lol | yes | nautilus_zero_fill |
| lol-mkoi-vit-2026-07-26-game1 | 115548681803406192 | lol | yes | nautilus_zero_fill |
| lol-sk-fnc-2026-08-15-game1 | 115548681803406256 | lol | yes | nautilus_zero_fill |
| lol-sly-ucam1-2026-06-14-game2 | 116634566264113554 | lol | yes | nautilus_zero_fill |
| lol-ross-use1-2026-07-17 | 116713163972559031 | lol | yes | nautilus_zero_fill |
| lol-edg-tes-2026-08-05-game1 | 116566854547769589 | lol | **no** | not selected |

- The ty-pari id is `8902431810`. The slug sits on that row of `dota_maker/LIVE/seed0/results.parquet`, so the `fw-crash.md` guess is now confirmed.
- `lol-edg-tes` is eligible in the LoL audit (event `770325`). However, `select_backtest_event_ids(load_canonical_league_whitelist(), LOL_UNIVERSE_PATH)` does not contain `770325`, so validation never selects it and `--match-id` refuses it. It cannot be crash-checked, and deleting it from the set changes no run.
- In the current LIVE runs:
  - Dota `LIVE` (`strip-fix2`) has seeds 0, 1 and 3. Seed 2 died on ty-pari, per `.learnings/onchain-tape-lag-20261009.md`. Per seed, the terminations are 3 `nautilus_zero_fill` and 6–7 `empty_signal_tape`.
  - LoL `LIVE` (`shared-20261005`) has seeds 0, 1 and 2. Per seed, the terminations are 6 `nautilus_zero_fill` and 15–21 `empty_signal_tape`.

### 1.4 Validation, promotion and the git-tracked run artifacts

- The order is in `docs/rebuild-order.md:24-34`: `SEEDS=1 SHARDS=4 scripts/run_seeds.sh <game> <name>`, then `SEED_LIST="1 2" SHARDS=4 …` with the same name, then `make promote-backtest RUN=… ARGS="--seeds 3"`. Do not run 12 processes at once, and run the two games one after the other.
- `scripts/run_seeds.sh`:
  - It builds `_archive` first (`:94-118`).
  - With `SEED_LIST` set, it skips `--expected-seeds` (`:170-174`).
  - At the end it runs `compare_backtests.py LIVE <run>` without `--allow-drift` (`:176-183`). That call will raise, because the new manifest has keys LIVE lacks, such as `place_latency_ms` and `response_ms` (`run.py:811-826`), and LIVE has keys the new one lacks, such as `debounce_ms`. These are not in `EXPECTED_MANIFEST_DIFFS` (`scripts/compare_backtests.py:49-81`). The script then exits nonzero after the seeds finished. Expect this, and compare by hand with `--allow-drift`, which also skips `validate_catalog` on the old LIVE (`compare_backtests.py:913-921`).
- `scripts/promote_backtest.py:33-49` runs `validate_catalog(run, range(seeds))`, then replaces the `LIVE` symlink.
- `data/backtests/` is **not** gitignored. Only logs, shard dirs, quote events and some other paths are (`.gitignore:14-31`).
  - `data/backtests/dota_maker/LIVE` and `data/backtests/lol_maker/LIVE` are tracked symlinks.
  - Earlier promotions committed the symlink plus each run's `_archive/{DONE,fills,manifest,results,summary}`, `seed*/{fills,manifest,results,summary}` and `seeds.json` (`1a7e97e6`, `2549f4bd`).
  - `rawfix` has 18 tracked files and `tapefix` has 10. Their `_logs/rawfix` and `_logs/tapefix` dirs are ignored.
  - The 193 existing `match_*` files are tracked. The new crash-check `match_*_nzf-s*` dirs must therefore not stay in `data/backtests`, or they show up as untracked.
- The baselines differ from the new code in more than the sim-parity steps:
  - Dota `LIVE` ran on framework `df17b59`, which predates the `e2cfebb` raw-price fix.
  - LoL `LIVE` ran on `c76e77a`, which predates both framework patches and the print-time tape.
  - The models did not change: Dota is `20261009T100319Z` and LoL is `20261005T085427Z`. Both match the current `model.json`.
  - `tapefix` (seed 0 only, `e2cfebb` plus the maker-side rule) is the closest pre-sim-parity Dota baseline. Read it before C4 deletes it.

### 1.5 Goldens

- `scripts/capture_follow300_smoke.py` rewrites the 8 `tests/fixtures/follow300_changes/smoke/*_seed0.json` files plus `inputs.json`. Its docstring (`:1-7`) asks the commit message to say whether the change is a policy change or a dataset rebuild.
- Each capture runs `seed0_replay.replay_seed0_identity` in a child process (`src/backtest/seed0_replay.py:184-212`).
- `tests/test_follow300_replay.py:47-58` compares fills and place/cancel only. `checks` is reported, not gated (`extraction_identity.first_tape_mismatch`, `:337-350`).
- The last capture was `a6910be1`, and the files are under 620 KB, well below the 4096 KB `check-added-large-files` limit.
- STEP-009 and STEP-010 measured the golden file at 245–276 s with `-n0`. It is 8 red tests by design until this step.

### 1.6 Caches, disk and memory

- STEP-011 changed `strip_own_book.py`, and STEP-007 changed `onchain_retime.py`. Both are part of the tree-cache key (`telonex_local.py:295`, `:344-345`). The first Dota archive stage therefore re-strips every archive book day and evicts those markets' framework book cache.
  - `.learnings/backtest-memory-20261009.md` measured that cold case at 47 min for the archive stage plus seed 0 on 4 shards, with swap at 1.4 GB or less.
  - STEP-013 used private caches under `impl-013/cache`, so the default caches have not seen most validation maps on the new code.
- The tree cache is content-addressed and never evicts (`telonex_local.py:415-449`). `~/.cache/esports-trader/telonex-tree` is 32 GB today, and the disk has 68 GiB free (93% used). Do not delete either cache without approval (STEP-011 learning).
- A/B cache trap (`.learnings/onchain-tape-lag-20261009.md:98-102`): a tree-cache hit does not evict the framework's `trade-ticks-v1`. Do not run any other code version, such as the investigation scripts or an old commit, on the default caches during this step.
- Earlier wall times:
  - Dota `strip-fix2`: archive 32 min, seed 0 13 min, seed 1 7 min.
  - LoL `shared-20261005`: archive 23 min, seed 0 18 min, seeds 1 and 2 together 23 min.
  - The new quote loop (STEP-009) may be slower.
- One backtest process is about 3.3 GB. The machine has 32 GB and 12 CPUs.

### 1.7 pre-commit `--all-files`

- `.pre-commit-config.yaml` runs these hooks:
  - `ruff-check --fix` and `ruff-format`;
  - `basedpyright` on the whole project;
  - `trailing-whitespace` and `end-of-file-fixer`;
  - `check-yaml` and `check-toml`;
  - `check-added-large-files` (4096 KB).
- With `--all-files` the fixers can rewrite files this step did not touch. A read-only scan today found no trailing whitespace in tracked `py/md/toml/yaml/sh/json/txt` files. All 11 user-dirty files end with a newline. So the run is expected to change nothing, but the plan still checks `git status` before and after.

## 2. Changes

Run everything from `C/` on `main`. Keep this order. Validation must run on the final committed code, so that the promoted `LIVE` matches HEAD.

### 2.0 Preflight (no writes in C)

- `git status --short` should show only the 11 pre-existing user edits (`AGENTS.md`, `docs/…`). Record `git rev-parse HEAD`.
- In the framework, `git -C ../prediction-market-backtesting rev-parse HEAD` should equal the Makefile pin `e2cfebb2c393a772e9b336166dfc6909e02e14b2` (`Makefile:9`), the branch should be `local-day-native`, and the tree should be clean. The `.so` SHA should be `15233bfc…`.
- Check that nothing named `*sim-parity*` or `match_*_nzf-s*` exists under `data/backtests/*_maker/` or `_logs/`. If something does, pick another name; never overwrite.
- Disk should have at least 30 GiB free (`df -h .`). Note `sysctl vm.swapusage`.
- `mkdir -p R14/{crash/logs,crash/runs,golden-before,validation}`.

### 2.1 Crash check (current HEAD, no code edits)

- Dota ids: `8972289868 8891371442 8842722527 8902431810`. LoL ids: `116855104460702379 115548147900684659 115548681803406192 115548681803406256 116634566264113554 116713163972559031`.
- Each run is `backtest.run --game <g> --match-id <id> --signal-cadence-seed <s> --name nzf-s<s>`, with `BACKTEST_LOG_LEVEL=WARNING`, one log file per run, and the exit code and wall seconds appended to `R14/crash/exit.txt`.
- Seed 0 of the 10 maps runs sequentially, to warm the caches one market at a time. Then seeds 1 and 2 run as two concurrent sequential loops (2 processes).
- `lol-edg-tes` (`116566854547769589`) runs once with seed 0, only to record the refusal. Expect exit 1 with "not an eligible backtest card".
- Classify each (map, seed) pair:
  - **ok**: exit 0. From `results.parquet` read `terminated_early`, `stop_reason`, `buy_fills`, `sell_fills`, `buy_quantity` and `engine_pnl`.
  - **nautilus_assert**: nonzero exit, and the log matches `PositionOpened|position.pyx|AssertionError`.
  - **empty_tape**: the log has "signal timestamps must not be empty". Count it as no crash: validation never sends that pair to the engine.
  - **not_selectable**: only `lol-edg-tes`. Count it as no crash: it is out of the universe.
  - **other**: any other failure. Stop and report the traceback before any code change. The set cannot be judged then.
- Move each run dir, including partial ones, from `data/backtests/<g>_maker/match_<id>_nzf-s<s>` to `R14/crash/runs/`. Check that the target does not exist first. Leave nothing new in `data/backtests`.
- Write `R14/crash/summary.tsv` with the columns game, id, slug, seed, outcome, exit, wall_s, buy_fills, sell_fills, buy_qty and engine_pnl.

### 2.2a Path A: no `nautilus_assert` anywhere (commit C1)

- `src/backtest/lol_inputs.py`:
  - Delete `:37-66`: the ponytail comment, the 10 constants, the set and `NAUTILUS_ZERO_FILL_STOP_REASON`.
  - At `:71`, change `NO_REPLAY_STOP_REASONS` to hold only `EMPTY_SIGNAL_TAPE_STOP_REASON`. Keep it a frozenset, so the three readers (`run.py:1136`, `shared_archive.py:80`, `report_seeds.py:497`) stay untouched.
  - Keep `:67-70`.
- `src/backtest/run.py`:
  - Drop the two imports at `:76-77`.
  - Delete `engine_skip_ids` (`:2406`) and the zero-fill `fault_results` (`:2408-2418`).
  - In `empty_signal_ids` (`:2441-2445`), drop the `match_id not in engine_skip_ids and` clause.
  - Build `fault_results` once, as the tuple of `engine_fault_match_result(…, stop_reason=EMPTY_SIGNAL_TAPE_STOP_REASON, …)` over `empty_signal_ids`. This replaces the `(*fault_results, *(…))` extension at `:2452-2464`.
  - Keep the `logger.warning` under `if empty_signal_ids:`. `skip_keys`, `resumed` and `replay_matches` (`:2472-2496`) keep their current shape.
- `src/backtest/seed0_replay.py`:
  - Delete the import at `:30` and the dead loop at `:98-100`.
  - Replace the `try/except AssertionError` (`:156-178`) with a plain `run_batch(…)` call inside the `with`. A crash still fails the child: `replay_seed0_identity` raises on a nonzero exit with stdout and stderr (`:210-211`), so the traceback still names `PositionOpened`.
- `scripts/report_seeds.py:533-534`: the docstring sentence becomes "Maps with an empty signal tape are recorded as terminated_early and allowed." Change nothing else.
- `src/backtest/results.py`: no change (1.1).
- Tests:
  - `tests/test_promote_backtest.py:97-110`: rename the test to `test_promote_allows_empty_signal_tape_maps`. It asserts promotion succeeds when every seed's rows are terminated with `stop_reason="empty_signal_tape"`, with the docstring updated to match.
  - Add `test_promote_refuses_a_skipped_nautilus_zero_fill_map` next to it. It writes the same catalog with `stop_reason="nautilus_zero_fill"`, asserts `promote` raises `SystemExit` matching `terminated_early`, and asserts the `LIVE` link is unchanged. This pins the one behaviour change a user sees: an old catalog with skipped maps no longer validates.
  - `tests/test_backtest_validation.py:712,717`: use `"empty_signal_tape"`, the same literal the file already uses at `:1643`.
  - `tests/test_lol_backtest.py:642-653`: delete the test. Without the monkeypatch it only repeats `test_lol_selection_orders_maps_and_builds_signals` (`:656`) and the single-id case at `:803`. The `lol_inputs` import stays in use (`:306-311`).
- After the edit, `rg -n 'NAUTILUS_ZERO_FILL|nautilus_zero_fill' src scripts tests` should find only the new promote test's literal.
- Docs: leave `docs/as-is.md:94-96`. It carries the user's uncommitted edit at `:104`, and committing only our hunk is not possible with `git add <path>`. Leave `docs/extraction-oracle.md:86` and the experiment docs as history. List `as-is.md:94-96` in `progress.txt` for the owner.
- Run the targeted tests, basedpyright and `pre_commit run --files` on the 7 paths (§7). Commit only those paths.

### 2.2b Path B: at least one `nautilus_assert` (commit C1b only if ty-pari crashed)

- Leave the set, its comment and every branch unchanged. Do not touch `engine.pyx`, build a wheel or patch the framework.
- If `8902431810` crashed on any seed from 0 to 2:
  - add `DOTA_TY_PARI_2026_07_18_GAME2 = 8902431810` after `:51` and add it to the set at `:52-65`, as the comment at `:40-41` says;
  - otherwise seed 2 of the Dota validation dies the way `strip-fix2`'s did.
  - Commit only `src/backtest/lol_inputs.py`. No new test: it is one set member.
- Record every crashing (map, seed) pair in `progress.txt`, plus the last 20 lines of each crashing log. Then continue with 2.3: the set protects the validation.

### 2.3 Goldens (commit C2)

1. `cp` the 9 files in `tests/fixtures/follow300_changes/smoke/` into `R14/golden-before/`.
2. Run `scripts/capture_follow300_smoke.py` once (about 4–5 min). It prints 9 `wrote …` lines.
3. Delta per fixture: compare `R14/golden-before/<name>` with the new file through `load_identity_golden`, using the snippet in §7. For each of the 8 maps, record these as old → new:
   - fill count, BUY shares and SELL shares;
   - place/cancel event count;
   - `engine_pnl`, `buy_fills`, `sell_fills`, `orders_submitted` and `orders_canceled`.
   Also record `inputs_drift(new_inputs, old_inputs)`. The expected drift is `strategy_constants_sha256` only: STEP-010 deleted constants from `src/shared/constants/strategy.py`. If a model, dataset or schedule fingerprint moved, say so in the commit message and in progress.
4. Run full `make test` with `PYTEST_N` unset (the goldens spawn one replay child each; 8 xdist workers would run 8 replays at once). Then `uv run python -m basedpyright`. Then `uv run python -m pre_commit run --all-files`, with `git status --short` captured before and after. If pre-commit rewrites any path outside this step's files, stop and report. Do not commit or revert that path.
5. Commit only `tests/fixtures/follow300_changes/smoke/`. The message names the cause as a strategy and execution change: the sim-parity adapter, STEP-001..012 (plus C1 on path A), with no dataset rebuild.

### 2.4 Validation (Dota, then LoL; never at the same time)

- Dota:
  - `SEEDS=1 SHARDS=4 scripts/run_seeds.sh dota sim-parity`. This builds `_archive` cold (re-strip) and then seed 0.
  - Then `SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh dota sim-parity`.
  - The trailing compare against `LIVE` in each call exits nonzero on manifest drift (1.4). Run the second call regardless, and judge success by seed dirs and shard logs, not by the script's exit code.
- LoL: the same two commands with `lol`.
- Watch `sysctl vm.swapusage` during each archive stage. If swap passes 8 GB, stop your own `backtest.run` processes, report, and do not retry with more shards.
- After each game:
  - `report_seeds.py <run> --expected-seeds 3`. This prints the two-column terminal report per seed (workspace rule: paste it verbatim, never `summary.json`) and fails on unexpected terminations or a differing universe. Save the output to `R14/validation/<game>-report.txt`.
  - `compare_backtests.py <old LIVE run dir> <new run> --allow-drift`. Use the resolved directory names, not `LIVE`, so the order relative to promotion does not matter. Save to `R14/validation/<game>-vs-live.txt`.
  - Run the shared-set snippet (§7) for shares, fill counts and PnL before and after rebate per seed, on the maps both runs completed, plus full-catalog completed and terminated counts.
- Dota only, before C4: `compare_backtests.py <tapefix> <new> --allow-drift`, seed 0 only. It isolates STEP-001..012 from the framework raw fix. Save to `R14/validation/dota-vs-tapefix.txt`.
- Pairing:
  - Dota `LIVE` holds seeds 0, 1 and 3, so the Dota compare pairs seeds 0 and 1 only. Record that.
  - LoL pairs seeds 0, 1 and 2.
- If a shard dies on `PositionOpened`:
  1. Take the map from the last slug the shard log loaded and the seed from the dir.
  2. On path A, `git revert --no-edit <C1>`. A new commit is allowed; reset is not. This restores the set. Then add the new id in a follow-up commit, the same way as path B.
  3. On path B, add the id.
  4. Re-run only the dead shard with `--resume`, then `--merge-shards 4` for that seed.
  5. Record everything in progress.
  A non-Nautilus failure: stop and report.

### 2.5 Promotion (commit C3)

- Run `make promote-backtest RUN=<new dota run> ARGS="--seeds 3"` and the same for LoL. Each prints `…/LIVE -> validation_…_sim-parity`. Promotion is the baseline for the current code, not a trading switch: only `run_seeds.sh` and `run_12_seeds.sh` read `LIVE`. So it is not conditional on PnL; flag a large drop in the report instead.
- `git add` exactly these paths:
  - `data/backtests/dota_maker/LIVE` and `data/backtests/lol_maker/LIVE`;
  - the two new run dirs. `.gitignore` drops quote events, shard dirs and logs.
- `git diff --cached --stat` should list the 2 symlinks plus, per run, `_archive/{DONE,fills,manifest,results,summary}`, `seed{0,1,2}/{fills,manifest,results,summary}.*` and `seeds.json`. If `check-added-large-files` rejects a file over 4096 KB (LoL fills were 3.0 MB before), stop and report. Never use `--no-verify`.

### 2.6 Delete `rawfix` and `tapefix` (commit C4)

- Feature STEP-014 asks for this explicitly, so it is the one deletion of files this run did not create. Touch exactly these four paths:
  - `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_rawfix`
  - `data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_tapefix`
  - `data/backtests/dota_maker/_logs/rawfix`
  - `data/backtests/dota_maker/_logs/tapefix`
- No `lol_maker` copies exist (checked).
- First confirm that `readlink` of `dota_maker/LIVE` and `LIVE_NOXP` names neither run.
- `git rm -r -q` the two run dirs (28 tracked files). Then `rm -rf` the ignored leftovers in them (`quote_events.parquet`) and the two `_logs` dirs. Commit.

### 2.7 Progress (W)

Append the STEP-014 entry to `tasks/sim-parity-fixes/progress.txt` in the existing format. It contains:

- the C HEAD before the step, and the C1, C1b, C2, C3 and C4 SHAs;
- the crash table and which path was taken;
- the 8 golden deltas;
- the test, basedpyright and pre-commit results;
- the Dota and LoL validation against `LIVE` (and Dota against `tapefix`);
- the new `LIVE` targets;
- the deleted paths;
- the wall time of every phase;
- the `docs/as-is.md` follow-up;
- learnings.

Commit only that file. Leave `passes` false.

## 3. Edge cases

- **`--match-id` overwrites itself.** Without a per-seed `--name`, seed 1 clears seed 0's dir (`run.py:2126`). Use `nzf-s<seed>`.
- **`lol-edg-tes` cannot run.** It is outside the league whitelist. This counts as "not in the universe" and does not block path A. Record it.
- **A seed with an empty tape** is not a crash (1.2).
- **A crash that `--match-id` did not show** cannot come from batching, because batches hold 1 map. It can come from a map that is not on the list, since fills moved with STEP-001..012. Handle it as in §2.4.
- **Old catalogs after path A.** `validate_catalog` now rejects `nautilus_zero_fill` rows. So `report_seeds --expected-seeds` and a compare without `--allow-drift` refuse the old LIVE runs. The step always compares with `--allow-drift`, and the new runs have no such rows.
- **`run_seeds.sh` exits 1 at the end** on manifest drift. This is not a failed seed (1.4).
- **Archive maps** replay once at seed 0 and are grafted into seeds 1 and 2 (STEP-010 learning). Their seed spread is zero by design.
- **The promote check** needs seeds 0 through 2, every map completed or `empty_signal_tape`, and one manifest apart from the seed (`report_seeds.py:528-540`).
- **Disk.** If free space drops below 20 GiB during a run, stop your own processes and report. Do not prune caches.
- **The user edits C during the run.** Commit with explicit paths only. Re-check `git status` before every commit.

## 4. Tests

Path A:

- `test_promote_allows_empty_signal_tape_maps`: a 12-seed catalog whose rows are all terminated with `empty_signal_tape` is promoted, and `LIVE` points at it.
- `test_promote_refuses_a_skipped_nautilus_zero_fill_map`: the same catalog with `nautilus_zero_fill` gives `SystemExit` matching `terminated_early`, and `LIVE` is unchanged.
- `test_engine_fault_match_result_is_terminated_without_fills`: the same asserts, with `empty_signal_tape`.
- `test_lol_validation_keeps_nautilus_zero_fill_maps_in_the_universe` is deleted.

Path B with ty-pari: no new test. Path B without a code change: no tests.

Both paths: the 8 goldens in `tests/test_follow300_replay.py` turn green after the capture. Full `make test` includes them.

## 5. Out of scope

- Patching Nautilus `engine.pyx`, building a Nautilus wheel, or moving to Nautilus 2.0. No `poly-maker`, `py_clob_client*` or framework commits.
- A two-sided validation (non-goal). A 12-seed run. Re-training, dataset rebuilds, or tuning constants toward the old numbers.
- Editing `docs/as-is.md`, `docs/extraction-oracle.md`, `PLAN.md` or `.learnings/onchain-tape-lag-20261009.md` (it still says LIVE is `strip-fix2`). List these in progress for the owner.
- Deleting old LIVE runs (`strip-fix2`, LoL `shared-20261005`), the STEP-013 caches, or anything in `~/.cache`.
- Pushing esports-trader. Setting `passes`.

## 6. Risks and hard parts

- **The seed coverage of the removal.** The check covers seeds 0–2, which are the seeds this validation and promotion use. A later 12-seed run can still hit the assert on a map. The upgrade path is to re-add the id (it is in git history, `056e868b:src/backtest/lol_inputs.py`).
- **Attribution.**
  - Against `LIVE`, the Dota delta mixes STEP-001..012 with the framework raw fix (`df17b59` → `e2cfebb`).
  - The LoL delta also mixes both framework patches and the print-time tape.
  - The `tapefix` compare gives the clean Dota seed-0 number. There is no clean LoL baseline.
  - Report the numbers; do not explain them beyond what the compares show.
- **Cold caches and wall time.**
  - Expect the crash check to take 20–40 min.
  - Goldens plus checks take about 15 min.
  - The Dota validation takes about 1.25–1.75 h, since the archive stage re-strips every archive book.
  - The LoL validation takes about 1–1.5 h.
  - Promotion and cleanup take a few minutes.
  - **Total: about 3–4 h.** Record the real times.
- **Disk.** The tree cache only grows, and 68 GiB are free. The re-stripped archive days and the v3 onchain days add new entries.
- **pre-commit `--all-files`** can touch files outside the step. The preflight scan says it won't, and the before/after `git status` catches it if it does.
- **Promotion commits about 30 run files per game.** That matches the existing practice (`1a7e97e6`, `2549f4bd`). A file over 4 MB stops the commit.

## 7. Verification commands

Run in zsh from `esports-trader/` on `main`.

```bash
C=/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader
R14=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/tasks/sim-parity-fixes/orchestrate/work/impl-014
cd $C && git rev-parse HEAD && git status --short
git -C ../prediction-market-backtesting rev-parse HEAD; git -C ../prediction-market-backtesting branch --show-current
git -C ../prediction-market-backtesting status --short
shasum -a 256 ../prediction-market-backtesting/prediction_market_extensions/_native_ext.cpython-313-darwin.so
( setopt nullglob; print -l data/backtests/*_maker/*sim-parity* data/backtests/*_maker/_logs/*sim-parity* data/backtests/*_maker/match_*_nzf-s* )   # expect nothing
df -h . | tail -1; sysctl vm.swapusage
mkdir -p $R14/crash/logs $R14/crash/runs $R14/golden-before $R14/validation

# 2.1 crash check (~20-40 min)
DOTA=(8972289868 8891371442 8842722527 8902431810)
LOL=(116855104460702379 115548147900684659 115548681803406192 115548681803406256 116634566264113554 116713163972559031)
nzf() {  # game id seed
  local t0=$SECONDS
  BACKTEST_LOG_LEVEL=WARNING PYTHONPATH=src:../prediction-market-backtesting .venv/bin/python -m backtest.run \
    --game $1 --match-id $2 --signal-cadence-seed $3 --name nzf-s$3 > $R14/crash/logs/$1_$2_s$3.log 2>&1
  echo "$1 $2 $3 $? $((SECONDS - t0))" >> $R14/crash/exit.txt
}
for id in $DOTA; do nzf dota $id 0; done; for id in $LOL; do nzf lol $id 0; done
nzf lol 116566854547769589 0   # expect exit 1: not an eligible backtest card
for s in 1 2; do ( for id in $DOTA; do nzf dota $id $s; done; for id in $LOL; do nzf lol $id $s; done ) & done; wait
cat $R14/crash/exit.txt
rg -l 'PositionOpened|position\.pyx' $R14/crash/logs        # any hit = path B
rg -l 'signal timestamps must not be empty' $R14/crash/logs  # empty tape, not a crash
# move run dirs out of data/backtests; each target must not exist yet
for d in data/backtests/{dota,lol}_maker/match_*_nzf-s*(N); do [[ -e $R14/crash/runs/${d:t} ]] && echo "exists ${d:t}" || mv $d $R14/crash/runs/; done
.venv/bin/python - $R14 <<'EOF'
import sys, pandas as pd
from pathlib import Path
root = Path(sys.argv[1]) / "crash"
for line in (root / "exit.txt").read_text().splitlines():
    game, mid, seed, code, wall = line.split()
    res = root / "runs" / f"match_{mid}_nzf-s{seed}" / "results.parquet"
    row = pd.read_parquet(res).iloc[0] if res.is_file() else None
    cols = "" if row is None else f"{row.slug} {row.terminated_early} {row.stop_reason} {row.buy_fills} {row.sell_fills} {row.buy_quantity:.2f} {row.engine_pnl:.2f}"
    print(game, mid, seed, code, wall, cols, sep="\t")
EOF

# 2.2a path A targeted checks
rg -n 'NAUTILUS_ZERO_FILL|nautilus_zero_fill' src scripts tests   # only the new promote test literal
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_promote_backtest.py tests/test_backtest_validation.py tests/test_lol_backtest.py tests/test_report_seeds.py tests/test_compare_backtests.py
uv run python -m basedpyright
uv run python -m pre_commit run --files src/backtest/lol_inputs.py src/backtest/run.py src/backtest/seed0_replay.py \
  scripts/report_seeds.py tests/test_promote_backtest.py tests/test_backtest_validation.py tests/test_lol_backtest.py

# 2.3 goldens (~5 min capture, ~10 min make test)
cp tests/fixtures/follow300_changes/smoke/*.json $R14/golden-before/
time PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python scripts/capture_follow300_smoke.py
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python - $R14/golden-before <<'EOF'
import sys
from pathlib import Path
from backtest.extraction_identity import load_identity_golden
from backtest.replay_inputs import inputs_drift, load_smoke_inputs
before, now = Path(sys.argv[1]), Path("tests/fixtures/follow300_changes/smoke")
def row(i):
    buy = sum(f.quantity for f in i.fills if f.side == "BUY"); sell = sum(f.quantity for f in i.fills if f.side == "SELL")
    c = i.checks
    return (len(i.fills), round(buy, 2), round(sell, 2), len(i.place_cancel), c.engine_pnl, c.buy_fills, c.sell_fills, c.orders_submitted, c.orders_canceled)
print("fixture  fills buy_sh sell_sh place_cancel engine_pnl buy_fills sell_fills submitted canceled  (old -> new)")
for path in sorted(now.glob("*_seed0.json")):
    print(path.stem, row(load_identity_golden(before / path.name)), "->", row(load_identity_golden(path)))
print("inputs drift:", inputs_drift(load_smoke_inputs(now / "inputs.json"), load_smoke_inputs(before / "inputs.json")))
EOF
git status --short > $R14/status-before-precommit.txt
make test                                      # full, PYTEST_N unset; expect 0 failed incl. 8 golden maps
uv run python -m basedpyright                  # expect 0 errors
uv run python -m pre_commit run --all-files    # expect all Passed
git status --short | diff $R14/status-before-precommit.txt -   # expect only the smoke fixtures (and C1 paths if uncommitted)

# 2.4 validation, Dota then LoL (~1.25-1.75 h + ~1-1.5 h); judge by seed dirs, not the trailing compare's exit code
SEEDS=1 SHARDS=4 scripts/run_seeds.sh dota sim-parity; SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh dota sim-parity
SEEDS=1 SHARDS=4 scripts/run_seeds.sh lol sim-parity;  SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh lol sim-parity
NEW_DOTA=$(print -l data/backtests/dota_maker/validation_*_sim-parity); NEW_LOL=$(print -l data/backtests/lol_maker/validation_*_sim-parity)
OLD_DOTA=data/backtests/dota_maker/$(readlink data/backtests/dota_maker/LIVE); OLD_LOL=data/backtests/lol_maker/$(readlink data/backtests/lol_maker/LIVE)
TAPEFIX=data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_tapefix
RP="PYTHONPATH=src:../prediction-market-backtesting:scripts"
for g in dota lol; do n=NEW_${(U)g}; o=OLD_${(U)g}
  env ${=RP} uv run --group backtest python scripts/report_seeds.py ${(P)n} --expected-seeds 3 | tee $R14/validation/$g-report.txt
  env ${=RP} uv run --group backtest python scripts/compare_backtests.py ${(P)o} ${(P)n} --allow-drift | tee $R14/validation/$g-vs-live.txt
done
env ${=RP} uv run --group backtest python scripts/compare_backtests.py $TAPEFIX $NEW_DOTA --allow-drift | tee $R14/validation/dota-vs-tapefix.txt
# shared-set shares / fills / PnL per seed: run once per pair (OLD_DOTA NEW_DOTA, OLD_LOL NEW_LOL)
env ${=RP} uv run --group backtest python - $OLD_DOTA $NEW_DOTA <<'EOF'
import sys, pandas as pd
from pathlib import Path
runs = [Path(p) for p in sys.argv[1:3]]
seeds = sorted(set.intersection(*({int(p.name[4:]) for p in r.glob("seed*") if (p / "summary.json").is_file()} for r in runs)))
res = {(r, s): pd.read_parquet(r / f"seed{s}" / "results.parquet") for r in runs for s in seeds}
done = {k: v[~v.terminated_early.astype(bool)] for k, v in res.items()}
shared = set.intersection(*(set(v.match_id) for v in done.values()))
print("run seed all completed terminated | shared buy_sh sell_sh buy_fills sell_fills pnl_pre rebate pnl_post")
for (r, s), d in done.items():
    x = d[d.match_id.isin(shared)]
    f = pd.read_parquet(r / f"seed{s}" / "fills.parquet"); f = f[f.match_id.isin(shared)]
    pre, reb = x.engine_pnl.sum(), f.maker_rebate.sum()
    print(r.name[-20:], s, len(res[(r, s)]), len(d), len(res[(r, s)]) - len(d), "|", len(shared), round(x.buy_quantity.sum(), 1), round(x.sell_quantity.sum(), 1),
          int(x.buy_fills.sum()), int(x.sell_fills.sum()), round(pre, 2), round(reb, 2), round(pre + reb, 2))
EOF

# 2.5 promotion
make promote-backtest RUN=$NEW_DOTA ARGS="--seeds 3"
make promote-backtest RUN=$NEW_LOL ARGS="--seeds 3"
readlink data/backtests/dota_maker/LIVE data/backtests/lol_maker/LIVE   # both ..._sim-parity
git add data/backtests/dota_maker/LIVE data/backtests/lol_maker/LIVE $NEW_DOTA $NEW_LOL && git diff --cached --stat

# 2.6 cleanup (after C3)
readlink data/backtests/dota_maker/LIVE data/backtests/dota_maker/LIVE_NOXP   # neither rawfix nor tapefix
git rm -r -q data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_rawfix data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_tapefix
rm -rf data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_rawfix data/backtests/dota_maker/validation_join_delta02_x015_cut480_p45_tapefix \
  data/backtests/dota_maker/_logs/rawfix data/backtests/dota_maker/_logs/tapefix
( setopt nullglob; print -l data/backtests/*/*rawfix* data/backtests/*/*tapefix* data/backtests/*/_logs/*rawfix* data/backtests/*/_logs/*tapefix* )   # expect nothing

# end state
git status --short   # only the user's pre-existing edits
git log --oneline -6
```

If a dead shard needs a resume (§2.4), use `PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run --game <g> --validation --name sim-parity --shard <i>/4 --signal-cadence-seed <s> --resume`, then the same command with `--merge-shards 4` instead of `--shard`.

## Commits

Commit in C with explicit paths only. Never use `git add -A` or `-a`, and do not push.

| commit | when | paths | message |
|---|---|---|---|
| C1 | path A | the 7 files in 2.2a | `fix: STEP-014 - drop the Nautilus zero-fill skip set: no listed map crashes on the final code` |
| C1b | path B, ty-pari crashed | `src/backtest/lol_inputs.py` | `fix: STEP-014 - skip dota2-ty-pari-2026-07-18-game2 in bulk runs: it trips the Nautilus zero-fill assert` |
| C2 | always | `tests/fixtures/follow300_changes/smoke/` | `fix: STEP-014 - recapture the follow300 smoke goldens for the sim-parity adapter`; the body states a strategy/execution change and that no dataset was rebuilt |
| C3 | always | the 2 `LIVE` links + 2 run dirs | `fix: STEP-014 - point Dota and LoL LIVE at the sim-parity validation`; the body holds the shared-set numbers against the old LIVE |
| C4 | always | the `git rm` of `rawfix` and `tapefix` | `fix: STEP-014 - drop the rawfix and tapefix Dota runs` |

In W, commit `tasks/sim-parity-fixes/progress.txt` only: `Record STEP-014 progress: Nautilus crash check, follow300 validation, goldens and LIVE promotion`.
