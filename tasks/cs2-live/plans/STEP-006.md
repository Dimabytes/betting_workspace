# STEP-006 plan: CS2 shared round rules, trace contract, model loading

Step: `STEP-006` of `tasks/cs2-live/feature.json` (priority 6, `passes: false`).
Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, clean, HEAD `235ab50e`.
Evidence folder for regressions: `betting_workspace/investigations/2026-10-23-cs2-live-validation/` (`report.md` + `work/step-006/`).
`report.md` exists. Append a `## STEP-006` section. Never overwrite the file.
No Figma or design links exist for this step.

The step moves the CS2 round strategy pieces out of `backtest` into shared modules, adds a typed loader for the two CS2 models, and teaches the core trace to carry a `RoundSignal`.
No CS2 profile, strategy, feed, or signal source lands here. `GAME_PROFILES` stays `{dota, lol}`.
Dota/LoL behavior must not change. Shared files change only by renames, wider types, and one new decode branch.

Do not deploy, do not `git push`, do not run `scripts/sync_trader.py`, do not touch STEP-007+.

Planning measured only the unchanged code. Scratch output: `betting_workspace/tasks/cs2-live/orchestrate/work/plan-s6/`.

## 1. Facts this plan rests on

Line numbers are at `235ab50e`.

- `RoundSignal(RawDeltaSignal)` with `up_radiant`, `up_dire`, `call_ns` lives in `src/backtest/cs2_round_sides.py:23-27`.
  The same file holds `ROUND_SELL_RULE` (`:20`), `round_exit_fair`, `earlier_round_sells`, `current_round_view`, `choose_round_sell`, `delta_side_token`, `choose_round_buy`, and `ROUND_SIDES` (`:161`).
  It imports only `strategy.*`. `RawDeltaSignal` is `src/strategy/types.py:62-69`.
- Importers of `backtest.cs2_round_sides`: `src/backtest/strategy.py:46` (`ROUND_SIDES, RoundSignal`), `src/backtest/cs2_run.py:12` (`ROUND_SELL_RULE`),
  `tests/test_cs2_reports.py:14` (`ROUND_SELL_RULE`), `tests/test_cs2_round_sides.py:21` (`ROUND_SIDES, RoundSignal`), `tests/test_trader_session_core.py:26` (`ROUND_SIDES`).
- `src/backtest/cs2_round.py`: `CLOSED_ENTRY_LEADER_SCORE`/`CLOSED_ENTRY_DELTA` `:40-41`, `RoundCall` `:44-52`, `apply_round_rules` `:76-95`.
  The module itself uses `RoundCall` (`_calls_for_match` `:148-175`) and `apply_round_rules` (`predict_round_run` `:226-232`). `CLOSED_ENTRY_*` are used only inside `apply_round_rules`.
- Other users of those names through `backtest.cs2_round`:
  - `src/backtest/run.py:46` imports the module; `:1677` and `:1679` read `cs2_round.CLOSED_ENTRY_DELTA`.
  - `src/backtest/inspect/cs2_view.py:20-27` (`RoundCall`), `tests/test_backtest_inspect_cs2.py:17` (`RoundCall`), `tests/test_cs2_signals.py:13-23` (`RoundCall`, `apply_round_rules`).
- The rule test exists: `tests/test_cs2_signals.py:310-322` covers regulation pass-through (11), leader 12 → ±1e-4, delta 0 at 12 → 0, leader 13 → +1e-4, overtime → 0, pistol → −1e-4.
- Round-lots and SELL tests exist in `tests/test_cs2_round_sides.py`: radiant hold `:138`, dire hold `:223`, top-tick cap `:243`, round-lots `:417`, `:459`, `:491`, `:533`.
  All of them run with `radiant_token_index=0` (`tests/test_strategy_core.py:75-78`). No test covers a market where the YES token is not radiant.
- `src/trader/model_server.py`:
  - `_SilencedStderr` `:58-114`, used at `:238` and `:359`. `tests/test_model_server.py` uses `model_server._SilencedStderr` 14 times; its header comment `:9-10` explains the private name.
  - `load_model` `:199-208` logs `STARTUP_BLOCKED_PREFIX` + reason and calls `notify_in_background` on `ModelLoadError`.
  - `_load_model_from` `:229-246` calls `_require_meta_file` (`:249`), `_read_meta` (`:267`), `_validate_meta` (`:276`).
  - `ModelContractError` is a subclass of `ModelLoadError` (`:141`). No caller in `src/` tells them apart (grep of both names outside `model_server.py`: tests only).
- `src/cs2/s11_round_frame.py`: `FEATURES` `:46-77`, `OUTCOME_FEATURES` `:78`, `RoundModel` `:108-112`, `load_round_model` `:166-177`.
  `load_round_model` reads `model.json` again and raises plain `ValueError` for a feature mismatch, missing map codes, or members that disagree on map codes.
  Its members load through `shared.utils.gbm.load_booster`, which wraps LightGBM and `OSError` failures into `ModelCatalogError` (`gbm.py:429-436`). `ModelCatalogError` is a `ValueError` (`gbm.py:56`).
- `cs2.s11_round_frame` imports no `backtest` and no `trader` module. Measured with `python -X importtime`: `work/plan-s6/s11_round_frame-imports.txt` (it pulls `cs2.*`, `collect.*`, `lol.constants`, `shared.*`).
  Import cost after `trader.game_profile`: `+0.40 s` (game_profile alone `0.87 s`).
- The two production CS2 models are tracked in git: `data/cs2/models/round/` and `data/cs2/models/round_outcome/` (`git ls-files`, clean).
  - `round/model.json`: name `20261022T142007Z`, trained_at `2026-10-22T14:20:07Z`, `source_lag_seconds: 0`, 30 features equal to `FEATURES`, 10 members.
  - `round_outcome/model.json`: name `20261022T142332Z`, trained_at `2026-10-22T14:23:32Z`, `source_lag_seconds: 0`, 31 features equal to `OUTCOME_FEATURES`, 10 members.
  - `model_identity_sha256` (`shared/utils/gbm.py:484-502`) today: round `7eba748bcdf6162e668f9a47b1c97e63c73d3b12275fcbf33cc01a0188c5f607`, outcome `a1fc1a12d5c12baf83dfbca61fea8bf248275abc4c8af5621ec5563ef8aca2c7`.
    Both equal the Resolved Questions and the LIVE manifest keys `model_sha256` / `cs2_outcome_model_sha256` (`work/plan-s6/model-identity-baseline.txt`).
    The backtest computes `cs2_outcome_model_sha256` with the same function (`src/backtest/run.py:2609-2610`).
  - Both members' map codes are `(1..9)`; loading takes under 0.1 s each (`work/plan-s6/load-timing-baseline.txt`).
- Model typing today:
  - `StrategySpec.load_models -> dict[str, ModelServer]` and `build_signals(*, model: ModelServer, ...)` (`src/trader/strategy_spec.py:74-85`). Same in `Follow300Strategy` (`follow300_strategy.py:55-79`).
  - `host_resources._load_strategy_models -> dict[str, ModelServer]` (`:207-210`), `WalletHost.__init__(models: dict[str, ModelServer])` (`wallet_host.py:687`), `MatchWorker.__init__(model: ModelServer)` (`match_worker.py:173`).
  - The worker reads only `model.model_reference` (`match_worker.py:232`, `:1128-1129`) and passes the model to `build_signals` (`:192-197`).
- **Test trap.** `FakeModelServer` (`tests/trader_session_fixtures.py:355`) is not a `ModelServer` subclass. Tests pass it as `cast(Any, FakeModelServer())`
  into `MatchWorker` (`trader_session_fixtures.py:657`) and into `strategy.build_signals` (`tests/test_follow300_signals.py:79-80`).
  A positive `isinstance(model, ModelServer)` check in `Follow300Strategy.build_signals` would fail every worker test.
- Trace identity: `core_trace.model_file_sha(game)` hashes `GAME_PROFILES[game].strategy.trace_model_dir` (`core_trace.py:37-38`), called once from `header_for_market` (`:225`).
  `trace_model_dir` is a `StrategySpec` property (`strategy_spec.py:67-70`); `Follow300Strategy` returns `primary.model_dir` (`:45-47`).
  `tests/test_trader_game_profile.py:84` and `:109` assert it.
- The pinned model identity is also the resume guard: `MatchWorker` compares the journal's `model_name`/`model_trained_at` with `start.model` and goes archive-only on a mismatch (`match_worker.py:322-328`).
  `start.model` is `self._model.model_reference` (`:232`). Name and trained_at are opaque strings everywhere (`match_meta.py:563-565`, `session_journal.py:249-250`); nothing parses them.
- Codec: `_SIGNAL_KEYS` `core_trace_codec.py:91-100`; `decode_signal` `:340-361` builds a `RawDeltaSignal` and rejects any other key set.
  `jsonable` (`:194-212`) encodes every dataclass field, so a `RoundSignal` already encodes with its three extra keys; it maps NaN/inf to `null`.
  `decode_signal` is called only from `_decode_signal_update` (`:513-519`). Checkpoints do not hold the signal (`core_persistence.py:158-180`).
- Replay: `replay_core_trace._on_header` takes sides from `GAME_PROFILES[header.game].strategy.quote_sides` (`replay_core_trace.py:351`).
  A `"cs2"` header raises `KeyError` until STEP-009 registers the profile.
- Live modules import no `backtest` today: `grep -rn "from backtest\|import backtest" src/trader src/strategy src/cs2 src/shared` prints one docstring line, `src/strategy/signals.py:1` (`work/plan-s6/backtest-imports-baseline.txt`).
- Baseline checks at `235ab50e` (`work/plan-s6/lint-baseline.txt`, `targeted-baseline.txt`):
  - `uv run ruff check src tests scripts` → `All checks passed!`; `ruff format --check` → `533 files already formatted`; `uv run basedpyright` → `0 errors, 0 warnings, 0 notes` (12.7 s).
  - The 12 test files of §6.3 without the new ones: `355 passed` in 4.4 s.
- The 13 local core traces of the STEP-005 gate 2 are present under `data/trader/` (checked). STEP-005 head outputs are in `investigations/.../work/step-005/core-head/` and `model-smoke-head.txt`.

## 2. Design decisions

1. **`RoundSignal` moves to `src/strategy/types.py`**, directly after `RawDeltaSignal`. Body unchanged. The codec and the round sides import it from there.
2. **`git mv src/backtest/cs2_round_sides.py src/strategy/round_sides.py`.** Delete the `RoundSignal` class from it and import it from `strategy.types`.
   Everything else stays byte-equal, so git keeps the rename. Do not rename `tests/test_cs2_round_sides.py`; only its imports change.
3. **`src/cs2/round_rules.py` (new)** holds `CLOSED_ENTRY_LEADER_SCORE`, `CLOSED_ENTRY_DELTA`, `RoundCall`, `apply_round_rules`, moved verbatim except the docstring.
   - Drop the `apply_round_rules` docstring on the move. The rule rationale is already in `docs/experiments/cs2-pistol-ban.md`, and the comments review deletes moved docstrings.
   - `backtest/cs2_round.py` imports `RoundCall` and `apply_round_rules` from `cs2.round_rules` and deletes its own copies. It uses both names, so they stay importable from `backtest.cs2_round`.
     That is the re-export for `cs2_view.py`, `test_backtest_inspect_cs2.py`, and `test_cs2_signals.py`. None of them change.
   - `backtest/run.py` imports `CLOSED_ENTRY_DELTA` from `cs2.round_rules` and uses it at `:1677`/`:1679`.
     Reason: `cs2_round` no longer uses the constant. A bare re-export of an unused name fails ruff F401 and basedpyright `reportUnusedImport`.
     The `X as X` idiom would pass but appears nowhere in this repo. One consumer changes instead.
4. **`model_server.py` gets a small public surface.** Behavior for Dota/LoL stays the same.
   - Rename `_SilencedStderr` → `SilencedStderr` (class and both uses). Update the 14 test references and delete the stale comment `tests/test_model_server.py:9-10`.
     Keep `# pyright: reportPrivateUsage=false` there: the file still reads `_STDERR_SILENCE_LOCK` and `_build_feature_values`.
   - `read_meta(model_dir: Path) -> ModelMeta` replaces `_require_meta_file` + `_read_meta`: it runs the regular-file check, then the read, with the same two errors and reasons.
     `_load_model_from` calls `meta = read_meta(model_dir)`. The `meta_path` local goes away.
   - Rename `_validate_meta` → `validate_meta`. Signature and errors unchanged. Callers outside the module read only `.reference` of the returned `_ValidatedMeta`.
   - `report_startup_block(reason: str) -> None` holds the two lines from `load_model`'s `except`: the `STARTUP_BLOCKED_PREFIX` warning and `notify_in_background`.
     `load_model` calls it and re-raises. The CS2 loader calls it too, so the alert text has one home and tests patch one name (`trader.model_server.notify_in_background`).
5. **`src/trader/cs2_models.py` (new).**
   - `@dataclass(frozen=True) class Cs2RoundModels: delta: RoundModel; outcome: RoundModel; model_reference: ModelReference`.
   - `load_cs2_round_models(round_dir: Path, outcome_dir: Path) -> Cs2RoundModels`:
     1. Read and validate both metas first: `validate_meta(read_meta(dir), FEATURES or OUTCOME_FEATURES, 0).reference`. A contract error stops before any booster loads.
     2. Load both member sets with `load_round_model(dir, features)` inside `with SilencedStderr():`.
     3. Map errors from step 2, in this order: `ModelCatalogError` → `ModelLoadError(exc.reason)`; other `ValueError` → `ModelContractError(str(exc))`;
        `OSError` → `ModelLoadError("model artifacts are unreadable")`. The order matters: `ModelCatalogError` is a `ValueError`.
        The `ValueError` messages from `s11_round_frame` carry no path. `OSError` text can carry one, so it gets the fixed reason (the same text `_require_meta_file` uses).
     4. Any `ModelLoadError` from steps 1–3 → `report_startup_block(exc.reason)` and re-raise, like `load_model`.
     5. `model_reference = ModelReference(name=f"{delta.name}+{outcome.name}", trained_at=f"{delta.trained_at}+{outcome.trained_at}")`.
   - Pass the lag as `expected_lag_seconds=0` (keyword). Training writes the literal 0 (`s11_train_round_model.py:139`); no new constant.
   - Use small private helpers if they read better (for example `_read_round_reference(model_dir, features) -> ModelReference` and `_load_round_members(model_dir, features) -> RoundModel`).
     Do not return an anonymous tuple (AGENTS.md).
   - No docstrings or comments in the new file.
6. **One combined identity covers both CS2 models.** This is how the step's "verifiable identity of round/outcome models" lands without a trace schema change.
   - The name/trained_at pair above flows into `MatchStart`, `match.json`, the session journal, and the trace header unchanged. The resume guard (`match_worker.py:322-328`) then catches a swap of either model.
   - `StrategySpec.trace_model_dir: Path` → `trace_model_dirs: tuple[Path, ...]`. `Follow300Strategy` returns `(self.primary.model_dir,)`.
   - `core_trace.model_file_sha(model_dirs: tuple[Path, ...]) -> str` returns `"+".join(model_identity_sha256(path) for path in model_dirs)`.
     `header_for_market` passes `GAME_PROFILES[game].strategy.trace_model_dirs`. For one dir the string is byte-equal to today's, so Dota/LoL headers do not change.
   - STEP-009's `RoundStrategy` returns `(CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR)`. Its header sha256 is then
     `7eba748b…c5f607+a1fc1a12…8aca2c7`, directly comparable with the manifest's `model_sha256` and `cs2_outcome_model_sha256`.
   - Rejected: a second hash key in the trace header. `decode_header_row` requires exact keys (`core_trace_codec.py:851-853`), so it needs a legacy default and a schema decision. Not worth it here.
   - Rejected: hashing on the loaded object. It would split identity between "files at header time" (MOBA) and "files at load time" (CS2).
7. **`LoadedModel = ModelServer | Cs2RoundModels`** in `src/trader/strategy_spec.py` (implicit alias, the style of `InboundEvent` in `strategy/types.py`).
   - `StrategySpec.load_models -> dict[str, LoadedModel]`, `build_signals(*, model: LoadedModel, ...)`.
   - `Follow300Strategy`: same signatures. `build_signals` starts with `assert not isinstance(model, Cs2RoundModels)`. Pyright narrows the union to `ModelServer`.
     Do **not** write `assert isinstance(model, ModelServer)`: it breaks every test that passes `FakeModelServer` (§1, test trap).
   - `host_resources._load_strategy_models -> dict[str, LoadedModel]`, `WalletHost(models: dict[str, LoadedModel])`, `MatchWorker(model: LoadedModel)`.
     Drop the `ModelServer` imports that become unused (`host_resources.py:35`, `wallet_host.py:125`, `match_worker.py:70`; check each with ruff).
   - Import chain check: `strategy_spec` → `cs2_models` → `model_server`, `bindings`, `cs2.s11_round_frame`, `shared.utils.gbm`. None of them imports `game_profile` or `strategy_spec`. No cycle.
8. **Codec.** `_ROUND_SIGNAL_KEYS = _SIGNAL_KEYS | {"up_radiant", "up_dire", "call_ns"}`.
   In `decode_signal`, after the existing legacy defaults and the `nw_delta_30` drop: if the keys equal `_ROUND_SIGNAL_KEYS`, return a `RoundSignal`; if they equal `_SIGNAL_KEYS`, return a `RawDeltaSignal`; else raise as today.
   Read the six common fields once into named locals and build either type from them. `up_*` with `require_number`, `call_ns` with `require_int`. The return type stays `RawDeltaSignal | None`.
   Encoding needs no change.
9. **Docs.** Point three experiment notes at the new paths: `docs/experiments/cs2-round-lots.md:39`, `docs/experiments/cs2-up-sell.md:12` → `src/strategy/round_sides.py`;
   `docs/experiments/cs2-pistol-ban.md:7` → `src/cs2/round_rules.py::apply_round_rules`. Leave `docs/experiments/cs2-ud-size/code.patch` alone (a historical diff).

## 3. File-by-file changes

`src/strategy/types.py`
- Add `RoundSignal(RawDeltaSignal)` with `up_radiant: float`, `up_dire: float`, `call_ns: int` after `RawDeltaSignal`.

`src/strategy/round_sides.py` (git mv from `src/backtest/cs2_round_sides.py`)
- Remove the `RoundSignal` class. Add `RoundSignal` to the `strategy.types` import. Drop `dataclass` from the `dataclasses` import if unused; `replace` stays.

`src/cs2/round_rules.py` (new)
- `import numpy as np`, `from dataclasses import dataclass`.
- `CLOSED_ENTRY_LEADER_SCORE = 12`, `CLOSED_ENTRY_DELTA = 1e-4`, `RoundCall`, `apply_round_rules` (body verbatim, no docstring).

`src/backtest/cs2_round.py`
- Delete `CLOSED_ENTRY_LEADER_SCORE`, `CLOSED_ENTRY_DELTA`, `RoundCall`, `apply_round_rules`. Add `from cs2.round_rules import RoundCall, apply_round_rules`.

`src/backtest/run.py`
- `from cs2.round_rules import CLOSED_ENTRY_DELTA`; `:1677` and `:1679` use `CLOSED_ENTRY_DELTA`. `cs2_round` stays imported (other uses at `:2269`, `:2277`).

`src/backtest/strategy.py:46`, `src/backtest/cs2_run.py:12`
- Import `ROUND_SIDES` / `ROUND_SELL_RULE` from `strategy.round_sides`. `backtest/strategy.py` adds `RoundSignal` to its existing `strategy.types` import (`:108`).

`src/trader/model_server.py`
- `SilencedStderr`, `read_meta`, `validate_meta`, `report_startup_block` as in §2.4. `_load_model_from`, `_predict_delta` use the new names.

`src/trader/cs2_models.py` (new)
- `Cs2RoundModels`, `load_cs2_round_models` as in §2.5. Imports: `cs2.s11_round_frame` (`FEATURES`, `OUTCOME_FEATURES`, `RoundModel`, `load_round_model`),
  `shared.utils.gbm` (`ModelCatalogError`), `trader.bindings` (`ModelReference`), `trader.model_server` (errors, `SilencedStderr`, `read_meta`, `validate_meta`, `report_startup_block`).

`src/trader/strategy_spec.py`
- `LoadedModel` alias; `load_models`/`build_signals` types; `trace_model_dir` → `trace_model_dirs: tuple[Path, ...]`.
  Delete the `trace_model_dir` docstring with the rename; the plural name says it. Keep the other protocol docstrings, as the STEP-003/005 reviews accepted.

`src/trader/follow300_strategy.py`
- `trace_model_dirs` returns `(self.primary.model_dir,)`. `load_models -> dict[str, LoadedModel]`. `build_signals(model: LoadedModel)` with the negative assert of §2.7.

`src/trader/core_trace.py`
- `model_file_sha(model_dirs: tuple[Path, ...]) -> str` with the join. `header_for_market` passes `GAME_PROFILES[game].strategy.trace_model_dirs`. The `except OSError` stays.

`src/trader/core_trace_codec.py`
- `_ROUND_SIGNAL_KEYS`, `RoundSignal` import, the `decode_signal` branch of §2.8.

`src/trader/host_resources.py`, `src/trader/wallet_host.py`, `src/trader/match_worker.py`
- Types only (§2.7).

Docs: the three lines of §2.9.

Untouched: `GAME_PROFILES`, `session_core.py`, `replay_core_trace.py`, `shared/constants/strategy.py` (the CS2 thresholds and cutoff belong to STEP-009), `cs2/s11_*` (STEP-008).

## 4. Tests

### 4.1 Changed imports only

- `tests/test_cs2_round_sides.py:21` → `from strategy.round_sides import ROUND_SIDES` and `from strategy.types import RoundSignal` (merge into the existing `strategy.types` import).
- `tests/test_trader_session_core.py:26` → `from strategy.round_sides import ROUND_SIDES`.
- `tests/test_cs2_reports.py:14` → `from strategy.round_sides import ROUND_SELL_RULE`.
- `tests/test_cs2_signals.py`: import `apply_round_rules` from `cs2.round_rules`. Keep `RoundCall` and the rest from `backtest.cs2_round`; that keeps the re-export exercised.
  The existing `test_apply_round_rules_passes_regulation_and_closes_match_point_and_pistol` is the pistol / leader-on-12 / overtime check of this step.
- `tests/test_model_server.py`: `_SilencedStderr` → `SilencedStderr` (14 places); delete lines 9-10.
- `tests/test_trader_game_profile.py:84`, `:109`: `assert strategy.trace_model_dirs == (PRODUCTION_MODEL_DIR,)` and `== (LOL_PRODUCTION_MODEL_DIR,)`.

### 4.2 New `tests/test_cs2_models.py`

Use the tracked production dirs `CS2_ROUND_MODEL_DIR`, `CS2_OUTCOME_MODEL_DIR` from `cs2.constants`. Use `tmp_path` copies (`shutil.copytree`) for the broken cases. Patch `trader.model_server.notify_in_background` with a list's `append`.

1. `test_load_cs2_round_models_pins_both_production_models`:
   `model_reference == ModelReference("20261022T142007Z+20261022T142332Z", "2026-10-22T14:20:07Z+2026-10-22T14:23:32Z")`;
   `delta.features == FEATURES`, `outcome.features == OUTCOME_FEATURES`, 10 members each; no alert.
2. `test_model_file_sha_joins_the_round_and_outcome_identities`:
   `model_file_sha((CS2_ROUND_MODEL_DIR, CS2_OUTCOME_MODEL_DIR)) == "7eba748bcdf6162e668f9a47b1c97e63c73d3b12275fcbf33cc01a0188c5f607+a1fc1a12d5c12baf83dfbca61fea8bf248275abc4c8af5621ec5563ef8aca2c7"`.
   Also `model_file_sha((PRODUCTION_MODEL_DIR,)) == model_identity_sha256(PRODUCTION_MODEL_DIR)` (the Dota header string is unchanged).
   This test may live in `tests/test_core_trace.py` instead; pick one place.
3. `test_swapped_dirs_block_startup_with_a_contract_error`: `load_cs2_round_models(CS2_OUTCOME_MODEL_DIR, CS2_ROUND_MODEL_DIR)` → `ModelContractError`,
   reason `model.json features do not match the live feature columns`, exactly one alert `STARTUP_BLOCKED_PREFIX + reason`, one WARNING with the same text.
4. `test_nonzero_source_lag_is_a_contract_error`: copy the round dir, set `source_lag_seconds` to 11 → `ModelContractError`, reason `model.json source_lag_seconds does not match the live contract`.
5. `test_missing_dir_is_a_load_error`: `tmp_path / "absent"` as the outcome dir → `type(exc) is ModelLoadError`, reason `model.json is missing or not a regular file`.
6. `test_unparseable_member_is_a_load_error`: copy, overwrite `member_03.txt` with junk bytes → `type(exc) is ModelLoadError` (not the contract subclass), reason from `ModelCatalogError`.
7. `test_read_failure_is_a_load_error_without_the_path`: patch `trader.cs2_models.load_round_model` to raise `PermissionError(f"denied {tmp_path}")` →
   `type(exc) is ModelLoadError`, reason `model artifacts are unreadable`, and `str(tmp_path)` appears in neither the alert nor `caplog.text`.
8. `test_members_load_under_the_stderr_silencer`: patch `trader.cs2_models.SilencedStderr` with a recording context manager and
   `trader.cs2_models.load_round_model` with a stub that asserts the recorder is entered. Assert two loads ran inside it.

### 4.3 `tests/test_core_trace.py`

1. Add one `SignalUpdate(now_ns=…, signal=RoundSignal(...))` to `_sample_events()`. `test_codec_round_trip` then proves the type survives: dataclass equality compares the class.
2. `test_decode_signal_rejects_a_partial_round_signal`: the six base keys plus only `up_radiant` → `StrictJsonError`.
3. `test_round_sides_trace_replays_the_round_lots` (the step's direct core replay with `ROUND_SIDES`):
   - Policy `follow300_policy(game="cs2", level_usdc=300.0, debounce_ms=0, fallback_timer_s=1.0)`. Header `replace(_header(policy), game="cs2")`.
   - `LiveCore(..., trace=CoreTrace.open(path), max_position_levels=6, sides=ROUND_SIDES)`. Drive it with `core.apply(...)`, the same tape as `test_cs2_round_sides._held_round_sell` and `test_new_decision_for_us_keeps_the_old_sell`:
     clock (second 100), budget, books radiant 0.64/0.66 and dire 0.34/0.36, `RoundSignal(delta 0.05, anchor 0.65, up_radiant 0.06, up_dire 0.04, call_ns=t0)`, forced `Wake`;
     accept the BUYs, fill the top BUY; at +10 s books + same signal + `Wake` → a SELL place at 0.71; accept it;
     at +12 s a new call (`up_radiant 0.11`, `call_ns` = that time) + `Wake` → no cancel of the 0.71 SELL. `core.detach_trace()`.
   - Assert the recorded plans contain the 0.71 SELL place.
   - `monkeypatch.setattr(replay_core_trace, "GAME_PROFILES", {"cs2": SimpleNamespace(strategy=SimpleNamespace(quote_sides=ROUND_SIDES))})`,
     then `replay_trace(path, use_current_policy=False)` → `events > 0`, plan, wake and digest mismatches all 0.
   - Patch again with `FOLLOW300_SIDES` → `plan_mismatches > 0`. This proves the replay steps with the given sides.
   - `setattr` takes an `object` value, so the stubs type-check. STEP-009 replaces the stub with the real `GAME_PROFILES["cs2"]` replay.

### 4.4 `tests/test_cs2_round_sides.py`: market polarity

`test_round_sell_follows_the_radiant_token`, parametrized over `radiant_token_index` ∈ {0, 1} and delta sign:
- State `replace(_idle(), limits=replace(_idle().limits, radiant_token_index=index))`; books mirrored so the radiant token always shows 0.50/0.52 and the dire token 0.48/0.50.
- Prime with a round signal (anchor 0.51, `up_radiant` 0.06, `up_dire` 0.05), accept, fill the top BUY, feed the signal at +10 s, wake.
- Expected: delta + buys the radiant token and its SELL rests at `0.51 + 0.06 = 0.57`; delta − buys the dire token and its SELL rests at `1 − (0.51 − 0.05) = 0.54`, on the token the index names.
- I did not run these numbers (no prototype). If a price lands one tick off because of rounding, fix the expectation from the formula, not the code.

## 5. Verification

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`. Save outputs under `investigations/2026-10-23-cs2-live-validation/work/step-006/`.

1. Lint and types:
   - `uv run ruff check src tests scripts` → `All checks passed!`
   - `uv run ruff format --check src tests scripts` → all files formatted
   - `uv run basedpyright` → `0 errors, 0 warnings, 0 notes`
2. Targeted pytest (§4 files plus the shared-path neighbors):
   `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q -n 8 tests/test_cs2_models.py tests/test_cs2_round_sides.py tests/test_core_trace.py tests/test_model_server.py tests/test_cs2_signals.py tests/test_cs2_reports.py tests/test_trader_session_core.py tests/test_backtest_inspect_cs2.py tests/test_cs2_execution.py tests/test_follow300_signals.py tests/test_trader_host_resources.py tests/test_trader_game_profile.py tests/test_trader_durable_place.py`
   Baseline without the new tests: `355 passed`.
3. Full suite: `PYTEST_N=8 make test`. Known noise: `test_upcoming_rows_from_sidecars` fails between 23:00 and 00:00 Berlin; `test_follow300_replay` flaked once under xdist. Rerun those alone before calling a failure.
4. Dota/LoL regression on the shared paths (the codec and `model_server` changed):
   - Core replay with the STEP-005 script: `sh ../betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-005/run_core_replay.sh ../betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-005/core_sessions.txt ../betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-006/core-head src`.
     Expect 13 sessions, 1 102 495 events, `plan=0 wake=0 digest=0`, and per-session `.txt` files byte-equal to `work/step-005/core-head/` (`cmp`; the `real` time lines live in `.err`).
   - Model smoke: `PYTHONPATH=src .venv/bin/python ../betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-005/model_smoke.py > …/work/step-006/model-smoke-head.txt`; `diff` against `work/step-005/model-smoke-head.txt` is empty.
5. Live modules stay free of `backtest`:
   - `grep -rn "from backtest\|import backtest" src/trader src/strategy src/cs2 src/shared` → only `src/strategy/signals.py:1` (a docstring).
   - `PYTHONPATH=src uv run python -c "import sys, trader.cs2_models, trader.core_trace_codec, trader.replay_core_trace, strategy.round_sides, cs2.round_rules; bad = sorted(m for m in sys.modules if m.split('.')[0] == 'backtest'); print(bad); assert not bad"` → `[]`.
6. The old path is gone: `test ! -e src/backtest/cs2_round_sides.py` and `grep -rn "cs2_round_sides" src tests scripts` prints nothing.
7. Identity, printed for the report:
   `PYTHONPATH=src uv run python -c "from cs2.constants import CS2_ROUND_MODEL_DIR as r, CS2_OUTCOME_MODEL_DIR as o; from trader.cs2_models import load_cs2_round_models; from trader.core_trace import model_file_sha; print(load_cs2_round_models(r, o).model_reference); print(model_file_sha((r, o)))"`
   → the combined reference and `7eba748b…c5f607+a1fc1a12…8aca2c7`.
8. Import smoke for the dashboard (it imports `game_profile`, which now pulls `cs2.s11_round_frame`):
   `PYTHONPATH=src uv run python -c "import trader.game_profile, dashboard.live_hub, dashboard.game_grid, dashboard.game_state, dashboard.match_state; print('import-ok')"`.
9. Append `## STEP-006` to `report.md`: revisions, what landed, items 1–8 with their outputs, and the package wall clock.

No data is missing for this step. The models are in git, the 13 core traces are local, and no command needs the VPS or the NAS.

## 6. Edge cases, risks, hand-offs

1. **`FakeModelServer` is not a `ModelServer`.** Only the negative assert keeps the worker tests green (§2.7).
2. **`ModelCatalogError` is a `ValueError`.** Catch it first, or a corrupt member reports as a contract error.
3. **Non-finite round fields break the trace.** `jsonable` writes NaN as `null` and `decode_signal` then fails `require_number`. That is already true for `RawDeltaSignal.anchor_p`.
   STEP-009 must never publish a `RoundSignal` with a non-finite `anchor_p`/`up_*` (feature: NaN `m0` → no usable signal).
4. **The CS2 identity strings change STEP-012's check.** PLAN 2D.7 says the header sha256 equals `7eba748b…`.
   After this step it is `7eba748b…+a1fc1a12…`, and `model.name` is `20261022T142007Z+20261022T142332Z`. STEP-012 compares both halves with the LIVE manifest.
5. **Import cost.** `strategy_spec` now imports `cs2_models` → `cs2.s11_round_frame`: +0.40 s for the trader and the dashboard, plus `collect.*`, `lol.constants`, and `typer` modules.
   No `backtest`. STEP-009 pays this anyway when `RoundStrategy` joins `GAME_PROFILES`.
6. **Map codes.** Both production models know map codes 1–9. `CS2_MAP_CODES` also has `vertigo: 10`. Not this step; STEP-008/009 must decide what a Vertigo map does (`add_derived` turns an unknown category into NaN).
7. **Hash pins in a unit test.** Test 4.2.2 fails if someone retrains a CS2 model. That is intended: model changes are out of scope, and the LIVE manifest pins these hashes.
8. **Comments review.** New files carry no docstrings. Moved functions lose theirs (`apply_round_rules`, `trace_model_dir`). `round_sides.py` keeps its untouched docstrings to stay a clean rename.
9. **`replay_core_trace` and a `"cs2"` header** raise `KeyError` until STEP-009. The new test patches the module global. Do not add a fallback in production code.
10. **Hand-offs:**
    - STEP-008: `predict_round_call` uses `cs2.round_rules.apply_round_rules` and returns `cs2.round_rules.RoundCall`.
    - STEP-009: `RoundStrategy.trace_model_dirs = (round_dir, outcome_dir)`, `load_models() → {"cs2-map": load_cs2_round_models(...)}`, `quote_sides = ROUND_SIDES`.
      Its `build_signals` narrows with `assert not isinstance(model, ModelServer)` if its tests use a fake, or `assert isinstance(model, Cs2RoundModels)` if they load the real pair.

## 7. Out of scope

`GAME_PROFILES["cs2"]`, `RoundStrategy`, `RoundSignals`, buy windows, `CS2_*` constants and cutoff, GRID CS2 parsing, feature building, the replay CLI, the dashboard, compose and env, the whitelist.
