# STEP-002 plan — E1: grid-v1 parity (A), post-table hold (B), STRATZ top-2 hold (C)

Worktree `../esports-trader-exp`, branch `exp-part2`, base `c05201b1`. All paths below are relative to the worktree unless they start with `$INV` or `betting_workspace`.

`$INV` = `betting_workspace/investigations/2026-10-13-killgate-short-roles` (exists, empty, gitignored in the workspace repo).

## Goal

1. grid-v1 kill waits move like the live reducer: a kill on one side keeps the other side's active wait.
2. Knob `kill_gate_post_table_hold_s` (default 0): after the table shows the awaited death, the side stays gated N more seconds. Flag `--kill-post-table-hold-s`.
3. Knob `kill_gate_hold_top2_only` + flag `--kill-hold-top2 PATH`: the hold stamp is set only when a top-2 networth hero of that side died in that transition (STRATZ, optimistic).
4. With every knob off, archive maps replay exactly as before. grid-v1 maps change only through fix A.

## Facts I verified in the code

- Live reducer: `src/trader/grid_feed.py:343-357` opens a wait only on the victim side with growth. `_side_wait` (`:317-325`) keeps the other side's wait while `now < until`. An active wait grows from `max(awaited, table deaths)`.
- Backtest bug: `_grid_v1_kill_gates` (`src/backtest/signals.py:400-423`) builds each `KillGateUpdate` from one `BoardKill` only. The side without growth gets `until_ns = board_ns` (expired). The engine replaces the gate wholesale (`src/strategy/engine.py:62-63`), so a Dire kill 3 s after a Radiant kill cancels the Radiant wait.
- Archive maps take `KillGateUpdate` from recorded live `KillTick`s (`signals.py:600-609`). Fix A does not touch them.
- `BoardHistory.record_board` (`src/backtest/signal_replay.py:123-126`) reads `awaited_deaths` of both sides. Fix A changes model inputs on grid-v1 maps in the rare case where the other side's kept wait has an older `awaited`. That matches live.
- `kill_victims` returns `frozenset()` when `state.signal is None` (`src/strategy/kill_gate.py:29-31`). The backtest sends `SignalUpdate(signal=None)` from `_clear_signal` (`src/backtest/strategy.py:1393-1396`), live from `match_worker.py:1080`.
- `_store` in `engine.py:54-73` has no policy today. `Policy = Follow300Policy | TwoSidedPolicy`. The two-sided core never reads the kill gate.
- `StrategyState` is built in one place only: `src/strategy/lifecycle.py:45-76`.
- `Follow300Policy` is built in three places: `policy.py:66`, `policy.py:92`, `core_trace_codec.py:474`.
- `RawDeltaSignal` is built in `core_trace_codec.py:356`, `match_worker.py:602`, `backtest/strategy.py:1381`, plus 21 test sites. Fields with default `0` keep those untouched.
- `MatchSignals(` is built at `signals.py:466`, `backtest/strategy.py:258`, `tests/test_feed_schedules.py:819`, `tests/test_backtest_signals.py:291, 323`, `tests/test_backtest.py:1039, 1091`.
- `DotaMakerConfig(` is built in `src/backtest/run.py:560-580` (payload, also reused by two-sided at `:595-606`), `tests/test_live_archives.py:116`, `tests/test_backtest_maker.py:220`, `tests/test_backtest_two_sided_core.py:115` (`TwoSidedCoreConfig` inherits the fields).
- `build_match_signals` / `build_schedule_match_signals` / `_assemble_match_signals` have 20 test call sites (14 in `tests/test_feed_schedules.py`).
- Validation has 903 maps. Every map has a STRATZ cache file in `data/raw/stratz_matches/` (checked). Players carry `playerSlot`, `playbackData`, `stats.deathEvents`, `stats.networthPerMinute`.
- The validation dataset deaths are `bisect_right(death_times(match, side), second)` (`src/prepare_dataset/stratz_seconds.py:194-195`). So on grid-v1 maps the signal death count k and the STRATZ death index are the same sequence.
- `data/*` is gitignored. `data/experiments/kill-top2/top2_deaths.parquet` stays out of git.
- LIVE seed0 (`data/backtests/dota_maker/LIVE/seed0/results.parquet`, 886 maps): 528 grid_v1, 261 schedule/grid, 97 schedule/oddin.

## Decisions

1. **Stamp only with a prior signal.** The transition needs `state.signal` (prior) not None. With prior None the side was not gated (`kill_victims` returns empty), so nothing "catches up". Rare, and it keeps the rule exact.
2. **The stamp needs `hold > 0`.** `next_kill_catch_up` returns the stored stamps unchanged when the policy is `TwoSidedPolicy` or `kill_gate_post_table_hold_s <= 0`. With N = 0 the stamps stay `None`, so the knob-off path is identical by construction. That one guard also covers two-sided.
3. **Fix A uses `max` on an active wait with growth**, as live does: `awaited = max(prior.awaited, kill.deaths_side)`. It differs from today only when drawn board delays reorder two close kills. Expired prior on a grown side: `awaited = kill.deaths_side` (as today).
4. **Top-2 counters attach after assembly, not inside `_assemble_match_signals`.** This deviates from the wording in `feature.json`. `MatchSignals.deaths_*` already holds the decision snapshot deaths, so the result is the same. Threading the table through `build_*` would touch 20 test call sites. `_assemble_match_signals` fills zeros; `resolve_run_signals` replaces them when `--kill-hold-top2` is set.
5. **Manifest keys are always written**: `kill_gate_post_table_hold_s`, `kill_gate_hold_top2_only`, `kill_top2_sha256` (`null` without a table). LIVE lacks them: `kill_top2_sha256` reads equal (`None == None`), the two policy keys show as expected diffs.
6. **Required arguments** (AGENTS rule): new params on `resolve_run_policies`, `resolve_run_signals`, `manifest_for_selection`, `build_run_manifest`, `MatchSignals`, `DotaMakerConfig`, `Follow300Policy`, `StrategyState` are required. Only `RawDeltaSignal.top2_deaths_*` default to `0` (`feature.json` says so; it spares live code and 21 tests).
7. **No new block reason.** Hold cancels use reason `"kill"`. Quote events cannot tell wait from hold. Add a reason later only if the analysis needs it.
8. **Three commits** in this step, in order A, B, C. Then `main` can take the winner without the rest (`Resolved Questions`: only the winner goes to main). If the split costs much, one commit is acceptable; say so in progress.txt.

## Implementation

### Commit A — grid-v1 parity

`src/backtest/signals.py`:

```python
def _next_wait(*, prior: KillWait, deaths: int, grew: bool, board_ns: int, hold_ns: int) -> KillWait:
    """One side's wait after a board kill marker, as the live reducer moves it."""
    active = board_ns < prior.until_ns
    if grew:
        awaited = max(prior.awaited_deaths, deaths) if active else deaths
        return KillWait(awaited_deaths=awaited, until_ns=board_ns + hold_ns)
    if active:
        return prior
    return KillWait(awaited_deaths=deaths, until_ns=board_ns)
```

`_grid_v1_kill_gates` becomes a loop per match. It carries `gate` from `empty_kill_gate()` (import from `strategy.kill_gate`) and emits one `KillGateUpdate` per `BoardKill`, as today. Use named variables per AGENTS (`radiant = _next_wait(...)`, `dire = ...`, `gate = KillGate(...)`).

Expired and no-growth sides keep today's exact value, so maps without overlapping kills produce byte-identical updates. Test `test_grid_v1_kill_gate_board_to_decision_gap_dota` (`tests/test_backtest_signals.py:633`) still holds.

Test `test_grid_v1_kill_gates_keep_other_side_wait` next to `:633` (call `_grid_v1_kill_gates` directly; the file already disables `reportPrivateUsage`):

- input: `BoardKill(board_ns=0, deaths_radiant=1, deaths_dire=0, grew_radiant=True, grew_dire=False)`, then `BoardKill(board_ns=3 s, deaths_radiant=1, deaths_dire=1, grew_radiant=False, grew_dire=True)`;
- second update: radiant `KillWait(1, 10 s)`, dire `KillWait(1, 13 s)`;
- drive `strategy.engine.step` with both updates and a prior signal `(0, 0)`. `SignalUpdate` at 5 s with deaths `(1, 0)`: `kill_victims` has only the Dire token. `SignalUpdate` at 9 s with `(1, 1)`: empty. Default policy (N = 0).
- second test: reordered kills (`deaths_radiant` 2 then 1, both grew, 2 s apart): `awaited` stays 2.

### Commit B — post-table hold

1. `src/shared/constants/strategy.py` after `KILL_GATE_HOLD_S` (`:63`): `KILL_GATE_POST_TABLE_HOLD_S = 0.0` with a one-line meaning comment in the file's style.
2. `src/strategy/policy.py`: field `kill_gate_post_table_hold_s: float` in `Follow300Policy`. `follow300_policy` sets `KILL_GATE_POST_TABLE_HOLD_S`, `extraction_policy` sets `0.0`.
3. `src/strategy/types.py`: `KillCatchUp(radiant_ns: int | None, dire_ns: int | None)` frozen dataclass next to `KillGate`. `StrategyState.kill_caught_up: KillCatchUp` right after `kill_gate` (no default).
4. `src/strategy/lifecycle.py:72`: `kill_caught_up=KillCatchUp(radiant_ns=None, dire_ns=None)`.
5. `src/strategy/kill_gate.py` (imports `Follow300Policy, Policy` from `strategy.policy`; no cycle, `policy.py` imports only constants and `two_sided`):

```python
def _hold_ns(policy: Follow300Policy) -> int:
    return round(policy.kill_gate_post_table_hold_s * NS_PER_S)


def _side_gated(*, wait: KillWait, signal_deaths: int, caught_up_ns: int | None, hold_ns: int, now_ns: int) -> bool:
    waiting = now_ns < wait.until_ns and signal_deaths < wait.awaited_deaths
    holding = caught_up_ns is not None and now_ns < caught_up_ns + hold_ns
    return waiting or holding


def _side_caught_up(*, wait: KillWait, prior_deaths: int, deaths: int, top2_grew: bool, top2_only: bool, now_ns: int) -> bool:
    if not (now_ns < wait.until_ns and prior_deaths < wait.awaited_deaths <= deaths):
        return False
    return top2_grew or not top2_only


def next_kill_catch_up(*, state: StrategyState, signal: RawDeltaSignal | None, policy: Policy, now_ns: int) -> KillCatchUp:
    """Stamp a side when this signal's table first shows its awaited death inside the wait."""
    stamps = state.kill_caught_up
    prior = state.signal
    if not isinstance(policy, Follow300Policy) or policy.kill_gate_post_table_hold_s <= 0:
        return stamps
    if prior is None or signal is None:
        return stamps
    ...  # radiant / dire: now_ns if _side_caught_up(...) else the old stamp
```

   Commit B has no top-2 yet: pass `top2_only=False` and drop the `top2_grew` argument until commit C, or add both in C. Keep the commit compiling.

   - `kill_victims(*, state, policy: Follow300Policy, now_ns)`, `kill_exposure(*, state, policy, now_ns)`.
   - `kill_gate_boundary_ns(*, state, policy, now_ns)`: candidates are both `until_ns` and `stamp + hold_ns` for each non-None stamp; return the smallest one `> now_ns`, else None.
6. `src/strategy/engine.py`: `_apply` and `_store` take `policy`. `case SignalUpdate():` returns `replace(state, signal=event.signal, kill_caught_up=next_kill_catch_up(state=state, signal=event.signal, policy=policy, now_ns=event.now_ns))`. `KillGateUpdate` stays as is: it never touches the stamps.
7. Callers get `policy=policy`: `src/strategy/quoting.py:536, 922, 956`, `src/strategy/scheduling.py:120` (policy is `Follow300Policy` there after the `TwoSidedPolicy` return at `:115`).
8. `src/trader/core_trace_codec.py`: `"kill_gate_post_table_hold_s"` in `_POLICY_KEYS` (`:107-130`); `merged.setdefault("kill_gate_post_table_hold_s", 0.0)` with the file's usual "traces from before … default it OFF" comment; `require_number` in the constructor. `digest_state` (`:257-285`) stays unchanged: stamps are not hashed, so old live traces still replay.
9. `src/backtest/run.py`:
   - parser after `--exit-abs-delta` (`:1499-1505`): `--kill-post-table-hold-s`, `type=float`, `default=None`, help names the default `KILL_GATE_POST_TABLE_HOLD_S`;
   - `_check_gate_flag_values` (`:1692`): `_reject_negative(parser, args.kill_post_table_hold_s, "--kill-post-table-hold-s")`;
   - `resolve_run_policies` (`:443-464`): required `kill_gate_post_table_hold_s: float | None`; `replace(...)` when not None, like `min_abs_delta`;
   - `main` (`:2314`) passes `args.kill_post_table_hold_s`;
   - `build_run_manifest` `shared` dict (`:806-854`): `"kill_gate_post_table_hold_s": run_policy.kill_gate_post_table_hold_s`.
10. `src/backtest/seed0_replay.py:143`: `kill_gate_post_table_hold_s=None`.
11. `scripts/compare_backtests.py:49-81`: add `"kill_gate_post_table_hold_s"`.

Tests (commit B):

- `tests/test_kill_gate.py`: pass `policy` at `:243, :252, :255, :361`. New helper `_hold_policy(hold_s)` = `replace(_policy(), kill_gate_post_table_hold_s=hold_s)`. Use `step(SignalUpdate(...))` for the catch-up; `_quoted` already sets a prior signal `(5, 3)`.
  - release at `stamp + N`: N = 10, gate radiant `(6, until 10 s)`, `SignalUpdate` at 2 s with radiant 6. Stamp = 2 s. At 11.999 s `kill_exposure` still buy-blocks token 0 and sell-blocks token 1; at 12 s both empty. `kill_gate_boundary_ns` is 10 s at `now = 3 s` and 12 s at `now = 10 s`. `next_wake_ns <= 12 s` after the 10 s wake.
  - no stamp when the table catches up at `now >= until` (`SignalUpdate` at 10 s).
  - repeat signal (`SignalUpdate` at 4 s, same deaths) keeps stamp 2 s.
  - stamp survives a Dire `KillGateUpdate` at 3 s; radiant still gated at 11 s.
  - a new Radiant kill (awaited 7, until 14 s) does not clear the stamp; table 7 at 12 s moves it to 12 s.
  - N = 0: same sequence, `kill_caught_up == KillCatchUp(None, None)` and `kill_victims` empty right after the catch-up, as before the change.
- `tests/test_core_trace.py`: `test_decode_policy_defaults_missing_kill_post_table_hold` (pattern of `:479-486`); `digest_state(replace(state, kill_caught_up=KillCatchUp(1, None))) == digest_state(state)`.
- `tests/test_backtest.py:1025`: add the new argument; one more assert that `kill_gate_post_table_hold_s=10.0` reaches the policy.
- Every `build_run_manifest(` / `manifest_for_selection(` call site gets the new required arguments only in commit C (commit B adds no new manifest param; the policy key comes from `run_policy`).

### Commit C — STRATZ top-2

1. Script `$INV/work/build_kill_top2.py` (outside the repo; run from the worktree with `make run F=<abs path>`):
   - match ids: `pd.read_parquet(VALIDATION_DATASET_PATH, columns=["match_id"])`, unique, sorted;
   - per map: `read_gzip_json(stratz_match_cache_path(id))["data"]["match"]`; missing file or `match is None` → count as "no STRATZ";
   - per side, deaths = `(time, playerSlot, player)` for each `player_death_times(player)` entry, sorted by `(time, playerSlot)`; `death_index` = 1, 2, …;
   - networth at `t − 1` for each teammate: playback → last `playerUpdateGoldEvents` networth with `time <= t − 1`, else 0; no playback → `networthPerMinute[clip((t − 1) // 60, 0, len − 1)]`;
   - `is_top2`: victim is in the first two of the side sorted by `(-networth, playerSlot)`;
   - output `DATA_DIR / "experiments" / "kill-top2" / "top2_deaths.parquet"`, columns `match_id: int64, is_radiant: bool, death_index: int64, is_top2: bool`, sorted by `(match_id, is_radiant, death_index)`; `mkdir(parents=True, exist_ok=True)`;
   - print: maps, maps without STRATZ, maps with at least one player on the `networthPerMinute` fallback, total deaths, `is_top2` share, maps where the validation `max(deaths_<side>)` exceeds the STRATZ death count (must be 0 — it proves the index link on grid-v1 maps);
   - the script uses `Path`, plain loops, no `argparse`. No comments that narrate the code.
2. `src/strategy/types.py:62-69`: `top2_deaths_radiant: int = 0`, `top2_deaths_dire: int = 0` on `RawDeltaSignal`.
3. Codec: both keys in `_SIGNAL_KEYS` (`:87-96`), `setdefault(…, 0)` in `decode_signal` (`:342-363`), `require_int` in the constructor.
4. Policy: `kill_gate_hold_top2_only: bool` (`False` in both factories); codec `_POLICY_KEYS`, `setdefault(…, False)`, `require_bool`.
5. `kill_gate.py`: `_side_caught_up` takes `top2_grew = signal.top2_deaths_<side> > prior.top2_deaths_<side>` and `top2_only = policy.kill_gate_hold_top2_only`.
6. `src/backtest/signals.py`:

```python
@dataclass(frozen=True)
class KillTop2:
    """Per match: item k-1 counts the top-2 networth deaths among the side's first k deaths."""

    radiant: dict[int, tuple[int, ...]]
    dire: dict[int, tuple[int, ...]]


def load_kill_top2(path: Path) -> KillTop2: ...
def count_top2_deaths(cumulative: tuple[int, ...], deaths: int) -> int: ...
def attach_kill_top2(signals: Mapping[int, MatchSignals], top2: KillTop2) -> dict[int, MatchSignals]: ...
```

   - `load_kill_top2`: require exactly the four columns; require `death_index == 1..n` per `(match_id, is_radiant)` (raise `ValueError` naming the match); cumulative sums of `is_top2`.
   - `count_top2_deaths`: `0` when `deaths <= 0` or no entry; else `cumulative[min(deaths, len(cumulative)) - 1]` (k past the table → last value).
   - `attach_kill_top2`: `replace(match_signals, top2_deaths_radiant=tuple(count(...) for deaths in match_signals.deaths_radiant), ...)`; a match missing from the table gets zeros.
   - `MatchSignals` (`:177-189`): required `top2_deaths_radiant: tuple[int, ...]`, `top2_deaths_dire: tuple[int, ...]`. `_assemble_match_signals` fills `(0,) * len(replay.decisions)`.
7. `src/backtest/run.py`:
   - parser: `--kill-hold-top2`, `type=Path`, `metavar="PATH"`, `default=None`;
   - `_check_kill_hold_top2(parser, args)` called from `parse_args`: dota only; requires `--kill-post-table-hold-s > 0`; file must exist; store `.resolve()`;
   - `resolve_run_policies`: required `kill_gate_hold_top2_only: bool`;
   - `resolve_run_signals`: required `kill_top2: KillTop2 | None`; after both builders, `if kill_top2 is not None: signals = attach_kill_top2(signals, kill_top2)`;
   - `main`: `kill_gate_hold_top2_only=args.kill_hold_top2 is not None`; `kill_top2=None if args.kill_hold_top2 is None else load_kill_top2(args.kill_hold_top2)`;
   - manifest: `manifest_for_selection` and `build_run_manifest` get required `kill_top2_path: Path | None`; `shared["kill_gate_hold_top2_only"] = run_policy.kill_gate_hold_top2_only`; `shared["kill_top2_sha256"] = None if kill_top2_path is None else sha256_file(kill_top2_path)`. The `_archive` manifest (`expected_manifest` lambda at `:2379`) goes through the same builder, so a changed table rejects an old `_archive` and `--resume` (`assert_manifest_matches` compares all keys);
   - `build_strategy_configs` payload (`:560-580`): `"top2_deaths_radiant"`, `"top2_deaths_dire"` from `match_signals`.
8. `src/backtest/strategy.py`: `DotaMakerConfig` fields `top2_deaths_radiant`, `top2_deaths_dire` with the same length checks as `deaths_*` (`:236-241`); `MatchSignals(...)` in `__init__` (`:258-268`); `_sync_signal` (`:1381-1388`) passes both counters into `RawDeltaSignal`.
9. `src/backtest/seed0_replay.py`: `kill_top2=None`, `kill_gate_hold_top2_only=False`.
10. `scripts/compare_backtests.py`: add `"kill_gate_hold_top2_only"`, `"kill_top2_sha256"`.
11. Test call sites to update mechanically: `MatchSignals(` (5 test sites), `DotaMakerConfig(` builders (3), `build_run_manifest(` (`tests/test_backtest.py:392, 434, 473, 529, 597`, `tests/test_lol_backtest.py:592`, `tests/test_since_match.py:439, 477, 517, 551, 568`), `manifest_for_selection(` (`tests/test_since_match.py:604`), `resolve_run_policies(` (`tests/test_backtest.py:1026`).

Tests (commit C):

- `tests/test_kill_gate.py` with `_hold_policy(10)` and `kill_gate_hold_top2_only=True`:
  - catch-up with `top2_deaths_radiant` 0 → 1 sets the stamp; 0 → 0 sets none (side ungated right after the table);
  - top-2 field off: 0 → 0 still sets the stamp;
  - two deaths in one signal (prior deaths 5, top2 0; new deaths 7, top2 1; awaited 6) sets the stamp.
- `tests/test_backtest_signals.py`: synthetic parquet in `tmp_path` (radiant `death_index` 1..3, `is_top2` F, T, F). `MatchSignals` with `deaths_radiant (0, 1, 2, 5)` → `attach_kill_top2` gives `(0, 0, 1, 1)`; unknown match → zeros; a gap in `death_index` raises.
- `tests/test_core_trace.py`: `_signal()` (`:131-139`) carries non-zero top-2 counters so `test_codec_round_trip` covers them; `test_decode_signal_defaults_missing_death_fields` (`:520-530`) still passes on defaults; policy default test for `kill_gate_hold_top2_only`.
- `tests/test_backtest.py`: one manifest assert that `kill_top2_sha256` is `None` without a table and equals `sha256_file(path)` with one.

## Verification

Run from `../esports-trader-exp`. `BT=src:../prediction-market-backtesting`.

1. Baseline smoke **before any code change** (commit `c05201b1`), one map each, a few minutes:
   - `PYTHONPATH=$BT uv run --group backtest python -m backtest.run --game dota --match-id 9026301227 --name kg-base` (archive/grid map, LIVE seed0 `engine_pnl` −101.039052, 85 BUY fills);
   - same for `--match-id 8940266495 --name kg-base` (grid-v1 map, LIVE seed0 `engine_pnl` 2239.43118).
   - If one map runs longer than 10 min, stop and report.
2. Typecheck after each commit: `uv run python -m basedpyright` → 0 errors. The pre-commit hook runs it too.
3. Lint: `uv run python -m pre_commit run` on staged files (ruff check + format).
4. Tests. PLAN rule 9: ask the orchestrator before any pytest run.
   - targeted: `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest tests/test_kill_gate.py tests/test_backtest_signals.py tests/test_core_trace.py tests/test_backtest.py tests/test_backtest_maker.py tests/test_live_archives.py tests/test_backtest_two_sided_core.py tests/test_feed_schedules.py tests/test_since_match.py tests/test_lol_backtest.py tests/test_strategy_late_fills.py tests/test_strategy_anchor_age.py tests/test_trader_book_guard.py -q`;
   - full: `PYTEST_N=8 make test` (local Mac only).
5. Table: `make run F=$INV/work/build_kill_top2.py`. The validation-max-deaths-over-STRATZ count must be 0. Record the printed stats in progress.txt.
6. Smoke after the code change:
   - knobs off, map 9026301227 (`--name kg-off`): `engine_pnl`, `buy_fills`, `sell_fills` equal the `kg-base` run and LIVE seed0. A mismatch is a bug in B/C: stop and report.
   - knobs off, map 8940266495 (`--name kg-off`): it may differ from `kg-base` through fix A only. Record both numbers.
   - knobs on, map 9026301227: `--kill-post-table-hold-s 20 --kill-hold-top2 data/experiments/kill-top2/top2_deaths.parquet --name kg-top2`. It runs; the manifest holds `kill_gate_post_table_hold_s: 20.0`, `kill_gate_hold_top2_only: true`, a 64-hex `kill_top2_sha256`.
   - flag errors: `--kill-hold-top2 <path>` without the hold, `--kill-post-table-hold-s -1`, `--game lol --kill-hold-top2 …` each exit with a parser error.
   - delete the smoke dirs this run created: `data/backtests/dota_maker/match_9026301227_kg-*`, `match_8940266495_kg-*` (and their `_logs` if any). Delete nothing else.
7. `git status` clean except intended files; `git add` file by file (never `-A`).

## Progress entry (`betting_workspace/tasks/killgate-short-roles/progress.txt`)

Commits (A, B, C hashes), table stats, the smoke numbers from step 6, check results. Keep `passes` false; the orchestrator sets it after review.

## Edge cases and risks

- **Signal cleared before the table catches up.** Prior `None` → no stamp → no hold for that kill. The side was ungated anyway (`stale_signal` pulls everything).
- **Stamp time** is the `SignalUpdate.now_ns` that carries the table, not the table's own time. In the backtest that is the decision `received_ns`.
- **SELL block grows too.** During the hold the killer token stays sell-blocked (`kill_exposure`), so exits wait up to N seconds longer. This is part of the arm, not a bug; watch SELL markout in the comparison.
- **Archive maps and top-2.** Archive decisions count deaths from GRID tables; STRATZ death indexes can drift from them (counting rules, missing frames). This step does not measure the drift. The arm is labelled optimistic.
- **Same-second deaths.** The tie order `(time, playerSlot)` matters only when a table count k lands inside a same-second group. On grid-v1 maps k always covers whole seconds.
- **Pre-horn deaths** (negative `time`) are indexed like any other death, as the dataset counts them. Their networth rank is near-meaningless (start gold); accepted.
- **Live code.** Live constructs the policy with N = 0 and top-2 off, so live behavior does not change. Its trace header gains three policy keys. Traces from this branch would not decode on `main`; the branch is not deployed.
- **Fix A changes grid-v1 results.** `e1-base` against LIVE: the `--signal-mode schedule` slice must match exactly, the `grid_v1` slice shows fix A.
- **NaN/inf hold.** `_reject_negative` does not reject `nan` or `inf`. Not worth a check for a research flag.

## Out of scope

- Live top-2 detector from GRID tables, new `ScheduleTick` field, archive index rebuild.
- Sharing `_next_wait` with `grid_feed.py`: live works on datetimes and table deaths; live code stays unchanged.
- Queue runs and full-cohort backtests (STEP-006/007).
