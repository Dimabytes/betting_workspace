# STEP-005 plan — E2a: short forecast in the strategy core and the backtest

Worktree `../esports-trader-exp`, branch `exp-part2`, head `3518f50d`. All paths are relative to the worktree unless they start with `$INV` or `betting_workspace`.

`$INV` = `betting_workspace/investigations/2026-10-13-killgate-short-roles`.

Inputs from STEP-004 (untracked, never `git add`):

| dir | `model_identity_sha256` |
| --- | --- |
| `data/new_model/experiments/short-h30-xp` | `c79e8da035ee7174129c5f5e06753a51d5389af6c71a394ed1ce6f3833a57c15` |
| `data/new_model/experiments/short-h30-noxp` | `ab7e2cb254f8aef02eb29b844be8eadce0eb04595ab41f9b85f497295169fc82` |
| `data/new_model/experiments/short-h60-xp` | `0e6c1df1129a26e86f2e40cf012fd7d6782f7f94b8b918596d275442fa0c9c56` |
| `data/new_model/experiments/short-h60-noxp` | `cfe24a745af49abee6d7755ab68ea7f649c38c6d08d58a5b8819a9d1e81e5b62` |

All four have the canonical 81-column XP or 70-column no-XP catalog and `source_lag_seconds` 10, the same as `data/new_model/research`.

## Goal

1. Knob `short_veto_delta` (default off). The BUY side closes with reason `short_horizon` when `sign(predicted_delta) · short_delta < −short_veto_delta`.
2. Knob `short_sell_hold_delta` (default off). The SELL of a token is blocked while `side · short_delta > short_sell_hold_delta` and `now < short_expiry_ns`. The block uses `KillExposure.sell_blocked`, so it also blocks a SELL at join ask. The wake boundary includes `short_expiry_ns` while the block is active.
3. The backtest predicts the short model on the same decision rows as the 300 s model. XP or no-XP is chosen by `catalog_has_board` of the decision's main model. The signal carries `short_delta` and `short_expiry_ns = signal_ts + h`.
4. Flags `--short-model-dir`, `--short-model-dir-noxp`, `--short-horizon-seconds`, `--short-veto-delta`, `--short-sell-hold-delta`. Five manifest keys, all in `EXPECTED_MANIFEST_DIFFS`.
5. With every knob off, decisions are byte-for-byte the same as before. This includes a run that loads a short model but sets no knob.

## Facts I verified in the code

- `RawDeltaSignal` (`src/strategy/types.py:62-71`) is built in `src/backtest/strategy.py:1386`, `src/trader/core_trace_codec.py:367`, `src/trader/match_worker.py:602`, and in tests. New fields with default `None` keep live code and tests unchanged.
- `Follow300Policy` (`src/strategy/policy.py:39-62`) is built only in `follow300_policy` (`:65`), `extraction_policy` (`:95`), and `decode_policy` (`core_trace_codec.py:478`). Its fields have no defaults. I keep that rule.
- `_buy_closed_reason` (`src/strategy/quoting.py:206-229`) reads `state.signal`. The BUY token is always the sign of `predicted_delta`: `pick_episode_token` picks the best edge `fair − join`, and fair moves with `predicted_delta`. When the delta flips sign during an episode, the episode token's fair falls below its bid, so it gets no BUY target. After the `min_delta` check, `|predicted_delta| ≥ exit_abs_delta > 0`.
- **Gap 1.** `requote` cancels kill-exposed orders only when `exposure.buy_blocked` is not empty (`quoting.py:956-958`). For the kill gate, `sell_blocked = {1 − v for v in victims}`, so both sets are empty together. A short hold gives a non-empty `sell_blocked` with an empty `buy_blocked`. Without a change, a resting SELL on the held token stays live when the plan has no BUY target: `_reconcile_choice` cancels only BUYs there (`:869-873`).
- **Gap 2.** When BUY is closed and inventory is held, `_reconcile_choice` cancels the BUYs with `held_reason = "fair"` (`:864-873`). Only `ownership_unresolved` and `position_open` keep their own label. So a `short_horizon` BUY cancel shows as `fair` in `quote_events.parquet` whenever a position is open. The backtest keeps `short_horizon` as is: it is not in `_HELD_NO_TARGET_REASONS` (`src/backtest/strategy.py:157-159`).
- `KillExposure` is built only in `kill_exposure` (`src/strategy/kill_gate.py:136-142`). It is read in `quoting.py:536, 922, 956`. `kill_gate_boundary_ns` (`kill_gate.py:145-165`) has no signal guard. The backtest and live fire `Wake(forced=True)` (`src/backtest/strategy.py:867`, `src/trader/session_core.py:360`), so a boundary equal to the expiry evaluates at that ns.
- `decide_sell` (`quoting.py:503-524`) excludes `sell_blocked` tokens in `held_sell` and `_select_exit_token`. That covers the fair SELL and the join-ask SELL (`fair is None`).
- Codec: `_SIGNAL_KEYS` (`core_trace_codec.py:87`), `_POLICY_KEYS` (`:109`), `_POLICY_DEFAULTS_FOR_OLD_TRACES` (`:135`, STEP-002 moved policy defaults here), `decode_signal` (`:355`), `decode_policy` (`:458`). `require_nullable_number` and `require_nullable_int` exist in `src/trader/strict_json.py:102-122`. `digest_state` does not hash the signal or the policy. `policy_sha` hashes the policy, so it changes. STEP-002 changed it already. `archive_policy_sha` is in `EXPECTED_MANIFEST_DIFFS`.
- `_assemble_match_signals` (`src/backtest/signals.py:575-632`) builds one row per `ModelDecision`, then predicts per `decision.model_dir` with the extra-feature join (`:594-605`). Board decisions are decisions too, so the short model sees them as well.
- The main model dir per plan: GRID (grid-v1 and GRID archives) uses XP, Oddin uses no-XP (`src/backtest/feed_schedules.py:203-205`). The decision history comes from the main model's policy (`history_policy_for_model_dir`). `history_policy_for_columns` depends only on the canonical prefix (`src/shared/utils/dota_features.py:166-173`). So an XP short model on an XP decision, or a no-XP short model on a no-XP decision, reads the right history columns.
- `_sync_signal` (`src/backtest/strategy.py:1373-1400`) sends a `SignalUpdate` only for a decision timestamp (`_model_index_at`), with `received_ns = signal_ts`. So `short_expiry_ns = signal_ts + h·10⁹`.
- `DotaMakerConfig` goes through msgspec JSON (`DotaMakerConfig.parse(config.json())`, `tests/test_live_archives.py:143`). Frozen dataclasses already ride inside it (`KillGateUpdate`, `ObservedClockTape`).
- `TwoSidedCoreConfig` inherits `DotaMakerConfig`. Its test fixture copies every struct field from `build_maker_config` (`tests/test_backtest_two_sided_core.py:115-122`), so it needs no edit. `TwoSidedCoreStrategy` overrides `_sync_signal` and ignores the short fields.
- `assert_manifest_matches` (`src/backtest/results.py:454-466`) and `describe_manifest_diffs` (`scripts/compare_backtests.py:859-873`) read missing keys as `None`. A key that is always written, and is `None` when off, equals a LIVE manifest that lacks it.
- Call sites that the new required parameters touch: `MatchSignals(` 8 (2 src, 6 tests), `DotaMakerConfig(` 3 (run payload, `tests/test_backtest_maker.py:228`, `tests/test_live_archives.py:116`), `build_run_manifest(` 12 tests plus `manifest_for_selection`, `resolve_run_policies(` 2 src plus 2 tests, `resolve_run_signals(` 2 src. The two public signal builders have 20 test call sites (14 in `tests/test_feed_schedules.py`).
- Test file sizes: `tests/test_core_trace.py` 986, `tests/test_backtest_signals.py` 941, `tests/test_kill_gate.py` 539, `tests/test_feed_schedules.py` 1217, `tests/test_backtest.py` 1191, `tests/test_backtest_maker.py` 3615.
- LIVE seed0 rows (`data/backtests/dota_maker/LIVE/seed0/results.parquet`) for the smoke maps. All three start after D:

| map | mode | `engine_pnl` | buy / sell fills |
| --- | --- | --- | --- |
| `9040578015` | schedule / grid | `454.7348856500001` | 47 / 23 |
| `9010695310` | schedule / oddin | `556.5801051300002` | 89 / 36 |
| `8987569122` | grid_v1 | `-270.760599` | 35 / 11 |

## Decisions

1. **One commit.** E2a is one hypothesis. The BUY knob and the SELL knob share the channel. If E2a wins, both parts go to `main`. If it loses, neither goes.
2. **The SELL hold lives in `kill_exposure`.** This follows `feature.json` and the Resolved Questions. `KillExposure` gets a third field `short_held`: the sell-blocked tokens that only the short forecast holds. The kill gate wins when both hold the same token.
3. **Fix gap 1.** `requote` cancels exposed orders when `buy_blocked or sell_blocked` is not empty. With the short knob off the two sets are empty together, so the old path is the same.
4. **Cancel labels.** A SELL that only the short forecast holds is canceled with reason `short_horizon`. A kill SELL keeps `kill`. Fix gap 2: `_reconcile_choice` keeps `short_horizon` as the held reason, like `ownership_unresolved` and `position_open`. Labels do not change decisions.
5. **No expiry check for the BUY veto.** BUY needs a signal younger than `entry_stale_s` (16 s on GRID) and `short_delta` rides the same signal. This follows PLAN.md §8.3.
6. **Two typed records in `src/backtest/signals.py`.**
   - `ShortModels(xp: Path, noxp: Path, horizon_s: int)` is the run input.
   - `ShortDeltas(values: tuple[float, ...], horizon_ns: int)` is the per-match output. `MatchSignals.short_deltas: ShortDeltas | None` and `DotaMakerConfig.short_deltas: ShortDeltas | None`.
   - One record instead of two parallel optional fields. `None` means "no short model". The horizon comes from the record, so `build_strategy_configs` needs no new parameter.
7. **Required parameters**, per the AGENTS rule, on `Follow300Policy`, `MatchSignals`, `DotaMakerConfig`, `_assemble_match_signals`, `resolve_run_signals`, `resolve_run_policies`, `build_run_manifest`, and `manifest_for_selection`. There are two exceptions:
   - `RawDeltaSignal.short_delta` and `short_expiry_ns` default to `None`. `feature.json` says so, and the default spares live code and many tests.
   - `build_match_signals` and `build_schedule_match_signals` take a keyword `short_models: ShortModels | None = None`. `feature.json` says `short_models=None`, and the default spares 20 test call sites.
8. **Manifest keys are always written**: `short_model_sha256`, `short_model_noxp_sha256`, `short_horizon_s`, `short_veto_delta`, `short_sell_hold_delta`. Each is `None` when off, so they equal LIVE.
9. **A short model without a knob is allowed.** It changes only the manifest. The smoke uses it to check that the channel alone does not move results.
10. **No new comments.** No docstrings on new private helpers. This follows the review rule in `.feature-json.config.json`.

## Implementation

### 1. `src/strategy/types.py`

- Add `"short_horizon"` as the last entry of `BlockReason` (`:10-39`). `_BLOCK_REASONS` in the codec reads `get_args(BlockReason)` and accepts it with no change. The live `_ENTRY_BLOCK` map (`src/trader/session_core.py:144`) maps unknown reasons to `NO_EDGE`. Live never emits this reason.
- `RawDeltaSignal` after `top2_deaths_dire`:

```python
    short_delta: float | None = None
    short_expiry_ns: int | None = None
```

### 2. `src/strategy/policy.py`

- Fields `short_veto_delta: float | None` and `short_sell_hold_delta: float | None` after `kill_gate_hold_top2_only`. No defaults.
- `follow300_policy` and `extraction_policy` set both fields to `None`.

### 3. `src/strategy/kill_gate.py`

```python
def short_held_tokens(
    *, state: StrategyState, policy: Follow300Policy, now_ns: int
) -> frozenset[int]:
    signal = state.signal
    hold = policy.short_sell_hold_delta
    if signal is None or hold is None:
        return frozenset()
    short_delta = signal.short_delta
    expiry_ns = signal.short_expiry_ns
    if short_delta is None or expiry_ns is None or now_ns >= expiry_ns:
        return frozenset()
    if abs(short_delta) <= hold:
        return frozenset()
    token = kill_victim_token(
        radiant=short_delta > 0, radiant_token_index=state.limits.radiant_token_index
    )
    return frozenset({token})
```

`|short_delta| > hold` with `short_delta > 0` is `side · short_delta > hold` for the Radiant token. With `short_delta < 0` it is the same rule for the Dire token. `hold ≥ 0`, so at most one token is held.

`KillExposure` and `kill_exposure`:

```python
@dataclass(frozen=True)
class KillExposure:
    """Victim tokens cannot be bought; killer tokens and short-held tokens cannot be sold."""

    buy_blocked: frozenset[int]
    sell_blocked: frozenset[int]
    short_held: frozenset[int]


def kill_exposure(*, state: StrategyState, policy: Follow300Policy, now_ns: int) -> KillExposure:
    victims = kill_victims(state=state, policy=policy, now_ns=now_ns)
    killer_tokens = frozenset(1 - token for token in victims)
    short_held = short_held_tokens(state=state, policy=policy, now_ns=now_ns) - killer_tokens
    return KillExposure(
        buy_blocked=victims,
        sell_blocked=killer_tokens | short_held,
        short_held=short_held,
    )
```

Keep the existing one-line docstring of `kill_exposure` and update its text to name the short hold.

`kill_gate_boundary_ns`: before `if not candidates`, add:

```python
    expiry_ns = None if state.signal is None else state.signal.short_expiry_ns
    if expiry_ns is not None and short_held_tokens(state=state, policy=policy, now_ns=now_ns):
        candidates.append(expiry_ns)
```

A held token implies `expiry_ns > now_ns`. No new wake happens when the knob is off or the hold is inactive. An extra wake would move `last_eval_ns` and the fallback cadence.

### 4. `src/strategy/quoting.py`

1. `_short_vetoes_buy`, next to `_buy_closed_reason`:

```python
def _short_vetoes_buy(*, signal: RawDeltaSignal, policy: Follow300Policy) -> bool:
    veto = policy.short_veto_delta
    short_delta = signal.short_delta
    if veto is None or short_delta is None:
        return False
    side = 1.0 if signal.predicted_delta > 0 else -1.0
    return side * short_delta < -veto
```

2. `_buy_closed_reason`: after the `min_delta` return, add `if _short_vetoes_buy(signal=signal, policy=policy): return "short_horizon"`.
3. `_cancel_kill_exposed` (`:108-121`): map each `(side, token)` to its reason. Use `"kill"` for buy-blocked tokens. For sell-blocked tokens use `"short_horizon"` if the token is in `exposure.short_held`, else `"kill"`. Keep the `already_canceling(order) and order.cancel_reason == reason` skip. Update the docstring text to name short-held SELLs.
4. `requote` (`:956-958`): `if exposure.buy_blocked or exposure.sell_blocked:`.
5. `_reconcile_choice` (`:864-868`): replace the two-branch `if/elif` with:

```python
    if choice.no_buy_reason in ("ownership_unresolved", "position_open", "short_horizon"):
        held_reason = choice.no_buy_reason
```

### 5. `src/trader/core_trace_codec.py`

- Import `require_nullable_number` from `trader.strict_json`.
- `_SIGNAL_KEYS`: add `"short_delta"`, `"short_expiry_ns"`.
- `decode_signal`: `merged.setdefault("short_delta", None)` and `merged.setdefault("short_expiry_ns", None)` next to the top-2 defaults, with no new comment. The constructor reads `require_nullable_number(merged, "short_delta", label)` and `require_nullable_int(merged, "short_expiry_ns", label)`.
- `_POLICY_KEYS`: add `"short_veto_delta"`, `"short_sell_hold_delta"`. Add both keys with value `None` to `_POLICY_DEFAULTS_FOR_OLD_TRACES`. Do not use `setdefault` for the policy: STEP-002's fix moved policy defaults into this dict. `decode_policy` reads both with `require_nullable_number`.

### 6. `src/backtest/signals.py`

1. Records after `MatchSignals`:

```python
@dataclass(frozen=True)
class ShortModels:
    xp: Path
    noxp: Path
    horizon_s: int


@dataclass(frozen=True)
class ShortDeltas:
    values: tuple[float, ...]
    horizon_ns: int
```

   `MatchSignals.short_deltas: ShortDeltas | None` as the last field. `attach_kill_top2` uses `replace` and keeps it.

2. Move the per-dir predict loop out of `_assemble_match_signals` (`:594-605`) into one helper. Main and short then share the extra-feature join:

```python
def _predict_by_dir(decision_rows: pd.DataFrame, dirs: Sequence[Path]) -> list[float]:
    deltas = [0.0] * len(dirs)
    for model_dir in sorted(set(dirs), key=str):
        positions = [i for i, dir_ in enumerate(dirs) if dir_ == model_dir]
        columns = load_model_feature_columns(model_dir)
        extra_columns = columns[len(strip_extra_feature_columns(columns)) :]
        model_rows = decision_rows.iloc[positions]
        if extra_columns:
            model_rows = attach_model_extra_features(model_rows, model_dir, extra_columns)
        predictions = predict_model_deltas(model_dir, columns, model_rows[list(columns)])
        for index, delta in zip(positions, predictions, strict=True):
            deltas[index] = delta
    return deltas


def _short_model_dir(short_models: ShortModels, model_dir: Path) -> Path:
    if catalog_has_board(load_model_feature_columns(model_dir)):
        return short_models.xp
    return short_models.noxp
```

3. `_assemble_match_signals(replays, gates, short_models: ShortModels | None)`:
   - inside `if decisions:`, `deltas = _predict_by_dir(decision_rows, [decision.model_dir for decision in decisions])`;
   - when `short_models` is set, `short_values = _predict_by_dir(decision_rows, [_short_model_dir(short_models, decision.model_dir) for decision in decisions])`. Else the list stays empty;
   - split the values per match the same way as `predicted_by_match`;
   - `short_deltas=None if short_models is None else ShortDeltas(values=tuple(short_by_match[match_id]), horizon_ns=short_models.horizon_s * NS_PER_SECOND)`.
   - A match with no decisions gets `ShortDeltas(values=(), horizon_ns=…)` when a short model is set.
4. `build_match_signals(…, mids, *, short_models: ShortModels | None = None)` and `build_schedule_match_signals(plans, feature_rows, mids, *, short_models: ShortModels | None = None)` pass it on to `_assemble_match_signals`.
5. Import `catalog_has_board` from `shared.utils.dota_features`.

### 7. `src/backtest/strategy.py`

- `DotaMakerConfig.short_deltas: ShortDeltas | None` after `top2_deaths_dire`. Import `ShortDeltas` at runtime next to `MatchSignals`. Do not import it under `TYPE_CHECKING`: msgspec resolves field types at runtime.
- `__post_init__`: if `short_deltas` is set and `len(short_deltas.values) != len(signal_timestamps_ns)`, raise `ValueError("timestamps and short_deltas must have the same length")`.
- `DotaMakerStrategy.__init__` passes `short_deltas=config.short_deltas` to `MatchSignals`.
- `_sync_signal`: `short = self._signals.short_deltas`, then in the `RawDeltaSignal`:

```python
                    short_delta=None if short is None else short.values[model_index],
                    short_expiry_ns=None if short is None else signal_ts + short.horizon_ns,
```

### 8. `src/backtest/run.py`

1. Import `ShortModels` from `backtest.signals`.
2. Parser, after `--kill-hold-top2` (`:1528-1534`):
   - `--short-model-dir` (`type=Path`, `default=None`): XP catalog of the short-horizon model. It requires `--short-model-dir-noxp` and `--short-horizon-seconds`.
   - `--short-model-dir-noxp` (`type=Path`): no-XP short catalog for Oddin archives.
   - `--short-horizon-seconds` (`type=int`): the forecast expires h seconds after its decision.
   - `--short-veto-delta` (`type=float`): close BUY while `side · short delta < −this`.
   - `--short-sell-hold-delta` (`type=float`): hold a token's SELL while `side · short delta > this`, until the forecast expires.
3. `_pin_short_model_flags(parser, args)`, called in `parse_args` after `_check_kill_hold_top2`:

```python
def _pin_short_model_flags(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    knobs = (args.short_veto_delta, args.short_sell_hold_delta, args.short_horizon_seconds)
    dirs = (args.short_model_dir, args.short_model_dir_noxp)
    if all(value is None for value in (*dirs, *knobs)):
        return
    if args.game != "dota":
        parser.error("--short-model-dir and the --short-* flags are dota-only")
    if args.short_model_dir is None or args.short_model_dir_noxp is None:
        parser.error("--short-model-dir and --short-model-dir-noxp must be passed together")
    if args.short_horizon_seconds is None or args.short_horizon_seconds < 1:
        parser.error("--short-model-dir requires --short-horizon-seconds >= 1")
    _reject_negative(parser, args.short_veto_delta, "--short-veto-delta")
    _reject_negative(parser, args.short_sell_hold_delta, "--short-sell-hold-delta")
    args.short_model_dir = _pin_catalog(parser, args.short_model_dir, "--short-model-dir")
    args.short_model_dir_noxp = _pin_catalog(
        parser, args.short_model_dir_noxp, "--short-model-dir-noxp"
    )
    for catalog_dir, columns, flag in (
        (args.short_model_dir, DOTA_XP_FEATURE_COLUMNS, "--short-model-dir"),
        (args.short_model_dir_noxp, DOTA_NOXP_FEATURE_COLUMNS, "--short-model-dir-noxp"),
    ):
        try:
            require_catalog_features(catalog_dir, columns, flag)
        except ValueError as exc:
            parser.error(str(exc))
```

   `args.*` is `Any`, so pyright accepts the pinned values. A knob without the model fails on "must be passed together".

4. `_short_models(args) -> ShortModels | None`: `None` when `args.short_model_dir is None`, else `ShortModels(xp=…, noxp=…, horizon_s=args.short_horizon_seconds)`. Call it once in `main`.
5. `resolve_run_policies` (`:447-473`): required `short_veto_delta: float | None` and `short_sell_hold_delta: float | None`. Always use `replace(policy, short_veto_delta=…, short_sell_hold_delta=…)`, as `kill_gate_hold_top2_only` does. `main` passes `args.short_veto_delta` and `args.short_sell_hold_delta`.
6. `build_strategy_configs` payload (`:582`): `"short_deltas": match_signals.short_deltas`. The two-sided payload spreads `payload` and carries it.
7. `build_run_manifest` (`:797`): required `short_models: ShortModels | None`. Add to `shared` after `kill_top2_sha256` (`:843`):

```python
        "short_model_sha256": None if short_models is None else model_identity_sha256(short_models.xp),
        "short_model_noxp_sha256": (
            None if short_models is None else model_identity_sha256(short_models.noxp)
        ),
        "short_horizon_s": None if short_models is None else short_models.horizon_s,
        "short_veto_delta": run_policy.short_veto_delta,
        "short_sell_hold_delta": run_policy.short_sell_hold_delta,
```

   `manifest_for_selection` (`:2056`) gets the same required parameter and passes it on. `build_manifest_for` in `main` passes `short_models`. The `_archive` manifest (`expected_manifest=lambda: …build_manifest_for(True)`) carries the keys too. So a changed short model or knob refuses an old `_archive` and `--resume`.
8. `resolve_run_signals` (`:2211`): required `short_models: ShortModels | None`, passed as `short_models=short_models` to both builders. `main` (`:2466`) passes it.

### 9. `src/backtest/seed0_replay.py`

`resolve_run_signals(…, short_models=None)` and `resolve_run_policies(…, short_veto_delta=None, short_sell_hold_delta=None)`.

### 10. `scripts/compare_backtests.py`

Add the five manifest keys to `EXPECTED_MANIFEST_DIFFS` (`:49-81`), after `kill_top2_sha256`.

## Tests

### Core — `tests/test_kill_gate.py` (539 → about 720 lines; the file already allows private use)

Helpers: `_signal(…)` gets `short_delta: float | None = None` and `short_expiry_ns: int | None = None`. Add `_short_policy(*, veto: float | None = None, hold: float | None = None)`, which returns `replace(_policy(), short_veto_delta=veto, short_sell_hold_delta=hold)`. Update the module docstring to name the short forecast.

BUY veto:

1. Flat state (`_idle`, books mid 0.60, signal delta 0.05, anchor 0.60), veto 0.005. With `short_delta = −0.006`: `Wake(0, forced=True)` gives no BUY place and `plan.block_reason == "short_horizon"`. With `short_delta = −0.005` (equality) and with `+0.01`: BUY places exist.
2. Dire side: delta −0.05 (anchor 0.40, mid 0.40) and `short_delta = +0.006` closes BUY. `short_delta = −0.006` leaves it open.
3. Knob off: veto `None` with `short_delta = −0.5` gives the same `EngineOutput.plan` and `state.orders` as the same state with `short_delta = None`. Veto 0.005 with `short_delta = None` gives the same.
4. A resting BUY is canceled. Place a BUY first (no `short_delta`), then send `SignalUpdate` with `short_delta = −0.01` and `Wake`. The BUY gets `cancel_reason == "short_horizon"` and is in `plan.cancels`. Repeat with inventory held on the episode token: the reason is still `short_horizon` (gap 2), and the resting SELL on that token stays live.

SELL hold (radiant token 0, hold 0.005, `short_expiry_ns = 30 s`, no kill wait):

5. `short_delta = +0.01`: `kill_exposure(now=0)` has `sell_blocked == short_held == {0}` and `buy_blocked == ∅`. `decide_sell` returns `None` with `fair=0.6` and with `fair=None` (join ask). At `now = 30 s` the exposure is empty, and `decide_sell` returns a SELL on token 0.
6. `short_delta = −0.01` holds token 1. With `radiant_token_index = 1`, `+0.01` holds token 1. `|short_delta| = 0.005` (equality) holds nothing.
7. Gap 1: inventory in token 0, a resting `sell-0`, clock `game_second = 500` (after cutoff, so no BUY target), `short_delta = +0.01`. `Wake(0)` cancels `sell-0` with reason `short_horizon`. Without the `requote` change this test fails, because the SELL stays live.
8. Kill and short on the same token: a Dire kill wait (killer token 0) and `short_delta = +0.01`. `short_held == ∅` and the `sell-0` reason is `kill`.
9. Boundary: `kill_gate_boundary_ns(now=0) == 30 s` while held. It is `None` with the knob off, with `short_delta = +0.004`, and at `now = 30 s`. With a kill wait until 10 s, it is `10 s`. `step(…, Wake(0)).next_wake_ns <= 30 s`.
10. Knob off: hold `None` with `short_delta = +0.5` gives the same `kill_exposure` sets as `short_delta = None`, and `short_held == ∅`.

### Codec — `tests/test_core_trace.py` (986 → about 990 lines)

- `_signal()` (`:130`): add `short_delta=0.004, short_expiry_ns=31`. `test_codec_round_trip` then covers non-`None` values.
- `test_decode_signal_defaults_missing_death_fields` (`:521`) already compares with a default `RawDeltaSignal`. It now proves that a trace without the two keys decodes to `None`.
- `test_decode_policy_defaults_a_missing_later_field`: add rows `("short_veto_delta", "short_veto_delta", None)` and `("short_sell_hold_delta", "short_sell_hold_delta", None)`. Change its first line to `policy = replace(pinned_policy(), short_veto_delta=0.005, short_sell_hold_delta=0.004)`, so the present-key check covers non-`None` values. The other rows keep their values.
- Do not add a new test to this file. It stays under 1000 lines.

### Backtest — new file `tests/test_short_forecast.py`

It imports only public names: `build_signal_frame` and `predict_signals` from `test_backtest_signals`, `build_maker_config` from `test_backtest_maker`, `write_test_catalog` from `test_backtest`, and fixtures from `feed_schedule_fixtures`. It needs no pyright suppression.

Preparation in other files:

- `tests/test_backtest_signals.py`: `predict_signals` gets `short_models: ShortModels | None = None` and passes it on.
- Move `_model_dir` and `_feature_frame` from `tests/test_feed_schedules.py:643-683` to `tests/feed_schedule_fixtures.py` as public `build_model_dir` and `build_feature_frame`. Rename their 29 uses. This shrinks the 1217-line file. The new test does not copy them.
- `tests/test_backtest_maker.py` `build_maker_config`: add parameter `short_deltas: ShortDeltas | None = None` and pass it on.

Cases:

1. Grid-v1: patch `backtest.signals.predict_model_deltas` with a fake that returns one value per `model_dir` (main 0.03, short XP −0.004, short no-XP 0.007). Use `predict_signals((1,), build_signal_frame(seconds=(58, 67)), main, short_models=ShortModels(xp, noxp, 30))`. Expect `predicted_deltas == (0.03,)` and `short_deltas == ShortDeltas(values=(-0.004,), horizon_ns=30 * NS_PER_SECOND)`. Without `short_models`, `short_deltas is None`.
2. Schedule dispatch: two plans in one `build_schedule_match_signals` call. Match 1 has an XP main dir and `feed_source="grid"`. Match 2 has a no-XP main dir and `feed_source="oddin"`. Use `build_schedule`, `build_tick`, `build_schedule_plan`, `build_feature_frame`, and `MidSeries` as in `test_schedule_signals_use_causal_anchor_and_tick_times`. Every match-1 short value is the XP value. Every match-2 short value is the no-XP value. `len(values) == len(timestamps_ns)` for both.
3. `DotaMakerConfig` length check: `build_maker_config(signal_timestamps_ns=(0,), short_deltas=ShortDeltas(values=(), horizon_ns=1))` raises `ValueError`.
4. Manifest: `build_run_manifest(…, short_models=ShortModels(…), run_policy=replace(policy, short_veto_delta=0.005, short_sell_hold_delta=0.004))` gives the two `model_identity_sha256` values, 30, 0.005, and 0.004. With `short_models=None` and the default policy, all five keys are `None`. Patch `read_framework_commit` and `backtest_run.sha256_file` as `test_run_manifest_pins_kill_top2_sha` does. Also assert that the five keys are a subset of `EXPECTED_MANIFEST_DIFFS`.
5. `resolve_run_policies(…, short_veto_delta=0.005, short_sell_hold_delta=0.004)` sets both fields. With `None` both stay `None`.
6. Parser, through `monkeypatch.setattr(sys, "argv", […])` and `parse_args()`. Each of these raises `SystemExit`:
   - only `--short-model-dir`;
   - both dirs without `--short-horizon-seconds`;
   - `--short-horizon-seconds 0`;
   - only `--short-veto-delta 0.005`;
   - `--game lol` with the short flags;
   - swapped catalogs (an XP catalog passed as `--short-model-dir-noxp`);
   - `--short-sell-hold-delta -1`.

   The happy path (both catalogs written with `write_test_catalog`, plus a no-XP variant, horizon 30, veto 0.005) returns resolved paths.

### Other test updates

- `tests/test_backtest_maker.py`: in `test_board_decision_between_feed_ticks_reaches_the_core` (`:604`), assert `signal.short_delta is None and signal.short_expiry_ns is None`. Add one test next to it. Use `signal_timestamps_ns=(0, 2 s)` and `short_deltas=ShortDeltas(values=(0.004, -0.006), horizon_ns=30 s)`. After `_evaluate(0)` the core signal has `short_delta == 0.004` and `short_expiry_ns == 30 s`. After `_evaluate(2 s)` it has `-0.006` and `32 s`.
- `tests/test_live_archives.py:116`: `short_deltas=ShortDeltas(values=(0.004,), horizon_ns=30 * NS)`, and assert `decoded.short_deltas == config.short_deltas` after `DotaMakerConfig.parse(config.json())`.
- Add `short_deltas=None` to every other `MatchSignals(` site: `tests/test_feed_schedules.py:819`, `tests/test_backtest_signals.py:310, 344, 884`, and `tests/test_backtest.py:1099, 1153`.
- `short_models=None` on the 12 `build_run_manifest(` and `manifest_for_selection(` test sites (`tests/test_backtest.py`, `tests/test_since_match.py`, `tests/test_lol_backtest.py:592`).
- `short_veto_delta=None, short_sell_hold_delta=None` on `tests/test_backtest.py:1072, 1085`.

## Smoke (one map at a time, from the worktree)

Before each run: `pgrep -fl "backtest.run|run_seeds|train_model|train_short"`. If another heavy job runs, wait. Do not stop processes you did not start. List `data/backtests/dota_maker/` and `data/backtests/dota_maker/_logs/` into your own scratch dir before and after. Delete only the new `match_<id>_<name>` dirs that these runs create.

Base command:

```bash
PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -m backtest.run \
  --game dota --match-id <MAP> --name <NAME> \
  --short-model-dir data/new_model/experiments/short-h30-xp \
  --short-model-dir-noxp data/new_model/experiments/short-h30-noxp \
  --short-horizon-seconds 30 <KNOBS>
```

| # | map | name | knobs | check |
| --- | --- | --- | --- | --- |
| 1 | `9040578015` (schedule / grid) | `short-smoke-off` | none | `results.parquet` row equals LIVE seed0 exactly: `engine_pnl` 454.7348856500001, buy 47, sell 23 |
| 2 | `9010695310` (schedule / oddin) | `short-smoke-off-oddin` | none | equals LIVE seed0: 556.5801051300002, 89 / 36 |
| 3 | `9040578015` | `short-smoke` | `--short-veto-delta 0.005 --short-sell-hold-delta 0.005` | exit 0, manifest keys, counts |
| 4 | `9010695310` | `short-smoke-oddin` | same | exit 0, manifest keys, counts |
| 5 | `8987569122` (grid_v1) | `short-smoke-v1` | same | exit 0, manifest keys, counts |

- Runs 1–2 prove that the channel alone does not change decisions on the XP and no-XP paths. If either differs, stop and report. Do not tune anything.
- Runs 3–5 manifest: `short_model_sha256` `c79e8da0…`, `short_model_noxp_sha256` `ab7e2cb2…`, `short_horizon_s` 30, `short_veto_delta` 0.005, `short_sell_hold_delta` 0.005.
- Runs 3–5 counts from `quote_events.parquet`, grouped by `kind`, `side`, `reason` where `reason == "short_horizon"`. Record `cancel_request`/BUY, `no_quote`, and `cancel_request`/SELL. Also record `engine_pnl`, buy and sell fills, and the delta against the LIVE row. If the file is missing for a `--match-id` run, record that and do not change code for it.
- A count of 0 on one map is a valid result. STEP-004 F3 showed the h30 XP veto closes 2.6% of eligible seconds.
- Delete the five smoke dirs. Check that the listing matches the "before" listing.

## Verification

1. `uv run --group backtest python -m basedpyright` in the worktree: 0 errors. Use `--group backtest`: a bare `uv run` can drop backtest packages. If it does, run `uv sync --group backtest --inexact` once.
2. `uv run --group backtest ruff check` and `ruff format --check` on every changed file.
3. Targeted tests:

```bash
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -n 8 \
  tests/test_kill_gate.py tests/test_core_trace.py tests/test_short_forecast.py \
  tests/test_backtest_signals.py tests/test_feed_schedules.py tests/test_backtest.py \
  tests/test_backtest_maker.py tests/test_live_archives.py tests/test_backtest_two_sided_core.py \
  tests/test_since_match.py tests/test_lol_backtest.py tests/test_compare_backtests.py \
  tests/test_strategy_core.py tests/test_extra_features.py tests/test_core_trace_digest.py \
  tests/test_follow300_replay.py tests/test_extraction_oracle.py tests/test_trader_session_core.py
```

4. `PYTEST_N=8 make test`. Expect only the 18 pre-existing failures from the STEP-002 entry: 13 host tests need `ODDIN_BRAND_TOKEN`, 2 LoL smokes lack `backtest_audit.parquet`, and 3 Dota seed0 goldens disagree with the October 12 LIVE tapes. Any other failure blocks the commit.
5. `wc -l` on `tests/test_core_trace.py` (≤ 1000), `tests/test_kill_gate.py`, `tests/test_short_forecast.py`, `src/strategy/quoting.py`, `src/backtest/signals.py`.
6. `git diff --stat`: no file outside the list above. Run `git status --short`: only the four untracked `data/new_model/experiments/short-*` dirs stay untracked.
7. Pre-commit passes on the commit. Stage named files only. Never use `git add -A`.

## Progress entry (`betting_workspace/tasks/killgate-short-roles/progress.txt`)

Write the commit SHA, the parity result of runs 1–2, the counts and PnL of runs 3–5, basedpyright, targeted pytest, the `make test` totals, and file sizes. Add learnings: gap 1 and gap 2, the `short_held` label rule, and the kill-wins rule. Leave `passes` false.

## Edge cases

- **Signal cleared** (`SignalUpdate(signal=None)`): no veto and no hold. A stale but present signal keeps the hold until `short_expiry_ns`. With h = 60 that can be 15 s past `exit_stale_s` (45 s). The SELL at ask then waits for the expiry. This is the "SELL waits at most h" rule.
- **A hold that renews.** Each new decision gets a new expiry. While the forecast keeps predicting a rise, the SELL stays held. This is the intended E2a-sell behavior. The SELL leaves when one decision drops below the hold or when no decision comes for h seconds.
- **After cutoff** (second ≥ 480) the hold still applies to the exit. The veto has no effect there because BUY is already closed with `cutoff`.
- **Recovery** (`sell_only`): `_recovery_exit` uses the same exposure, so the hold applies, as the kill gate does.
- **Halt, pause, game end** return before the exposure. No change.
- **Two-sided strategy**: `TwoSidedPolicy` never calls these paths. Short flags with `--strategy two-sided` are not rejected. This matches `--kill-hold-top2`.
- **Both catalogs at once**: on a run with grid and Oddin maps, each decision takes its own short catalog by `catalog_has_board`. Smoke runs 2 and 4 exercise the no-XP path.
- **E3 main models** (XP plus extras) still dispatch to the XP short model. `_predict_by_dir` joins extras for any dir that has them.
- **Board decisions** get a short prediction. Their expiry counts from the board decision time.

## Hard parts and risks

- **Gap 1 is easy to miss.** Without the `requote` change, the hold works only when the plan also has BUY targets. Test 7 pins it.
- **Wrong horizon flag.** `--short-horizon-seconds 30` with an h60 model runs without error. The model dir does not record h. STEP-006 `queue.sh` must take the dirs and the horizon from one `H` value.
- **No lag check for the short model.** All four have `source_lag_seconds` 10, like research. A model with another lag would get misaligned rows without an error. I did not add a guard: no such model exists.
- **Rewritten predict loop.** `_predict_by_dir` must keep the extra-feature path exactly. The E3 tests in `tests/test_extra_features.py` cover it. Run them.
- **msgspec.** `ShortDeltas` must import at runtime in `src/backtest/strategy.py`. The `test_live_archives` round trip catches a failure.
- **`policy_sha` changes again**, so new live trace headers differ from older ones. `archive_policy_sha` is already an expected diff. Replay of old traces decodes the new keys as `None`.
- **Run time and memory.** The smokes are about 15 s to 2 min and 2–3 GB each, based on the STEP-004 smoke. They must not overlap with training or a backtest.

## Out of scope

- Live port of the short model (`host_resources.py`, `game_profile.py`, `model_server.py`, `match_worker.py`).
- `queue.sh`, E3 training, and the pre-queue checks (STEP-006).
- A BUY expiry check, a lag guard, a horizon check from the model dir, and a separate SELL block reason in live session telemetry.

## Done when

- One commit on `exp-part2` with all the code and tests above.
- basedpyright reports 0 errors and ruff passes.
- Targeted tests pass. `make test` shows only the 18 known failures.
- Smoke runs 1–2 equal LIVE seed0. Runs 3–5 exit 0 and show the five manifest keys. The counts are in `progress.txt`. The smoke dirs are deleted.
