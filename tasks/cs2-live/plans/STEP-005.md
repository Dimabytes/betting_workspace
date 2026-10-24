# STEP-005 plan: Trader 2A — strategy seam, QuoteSides injection, LeagueFilter, four Dota/LoL gates

Step: `STEP-005` of `tasks/cs2-live/feature.json` (priority 5, `passes: false`).
Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, clean, HEAD `582e3621d80310aa7b2b902affd0dc1318e18da1`.
Evidence folder: `betting_workspace/investigations/2026-10-23-cs2-live-validation/` (`report.md` + `work/step-005/`).
`report.md` exists. Append a `## STEP-005` section. Never overwrite the file.
No Figma or design links exist for this step.

The step is a pure refactor. Dota/LoL behavior must not change. No CS2 code lands here.
`GameProfile` keeps the game identity, the feed and a new `strategy: StrategySpec`.
`Follow300Strategy` owns the model catalogs, the map cap, the quote sides, model loading and signal building.
`LiveCore` gets its `QuoteSides` injected. The buy cutoff becomes a per-game table. `LolLeagueFilter` becomes `LeagueFilter`.
The step closes the four Dota/LoL gates before any CS2 code.

Do not deploy, do not `git push`, do not run `scripts/sync_trader.py`, do not touch STEP-006+.

## 0. Validated prototype

I applied this plan to a scratch snapshot of HEAD and checked it. Everything is in
`betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/` (below: `$P5`).

- `$P5/proto.patch`: the full src + test change (44 files, 511 insertions, 266 deletions, one rename).
  `git apply --check` passes on HEAD `582e3621`. Use it as a reference. The plan text below is the contract.
- `$P5/proto/` is the snapshot (`git archive 582e3621 src tests config scripts <3 production model dirs> pyproject.toml .basedpyright Makefile`)
  with its own scratch git repo. It is not a worktree of `esports-trader`.
- Prototype checks:
  - `ruff check src tests scripts` → `All checks passed!`; `ruff format --check src tests scripts` → `533 files already formatted`.
  - `basedpyright` (strict, whole project) → `0 errors, 0 warnings, 0 notes`.
  - Full suite in the snapshot, `ODDIN_BRAND_TOKEN=dummy PYTEST_N=8`: `29 failed, 3577 passed`. The same 29 tests fail on an
    untouched `git archive 582e3621` snapshot (`$P5/base-suite.txt` vs `$P5/proto-suite2.txt`, `diff` empty).
    They need files a snapshot lacks (`compose.yaml`, `data/` parquet, smoke fixtures' inputs) or are the known Berlin-midnight
    `test_upcoming_rows_from_sidecars`. In the real repo they pass (STEP-004: 3600 passed).
  - Targeted pytest (§6.3 list) in the snapshot: `718 passed`.
  - Gate 2, core trace replay `--policy current`, prototype `src` against the real `data/trader`: 13 sessions, 1 102 495 events,
    `plan=0 wake=0 digest=0` everywhere; every per-session output file is byte-equal to the baseline (`$P5/core-base/` vs `$P5/core-proto/`). Wall clock 2 min 6 s.
  - Gate 3, GRID reducer differential: base and prototype 24 427 lines each, `cmp` equal, and equal to STEP-003 `grid-fix.txt`.
  - Gate 4, model smoke: `$P5/model-smoke-base.txt` and `$P5/model-smoke-proto.txt` are equal (3 catalogs, §5).
  - Signal differential (STEP-004 harness, compat copy): 27 514 lines, sha256 `7b70dde6…c3445e`, `cmp` equal to STEP-004 `signals-fix.txt`.
    Mutant check: `build_signals` with `self.primary.history` instead of `catalog_for(feed_source).history` gives 4 174 different lines. The harness sees the wiring.

## 1. Facts this plan rests on

Line numbers are at `582e3621`.

- `GameProfile` (`src/trader/game_profile.py:48-59`) carries `primary: StrategyCatalog`, `satellites: Mapping[FeedSource, StrategyCatalog]`
  and has three helpers: `strategy_catalog` (`:117`), `strategy_profile_name` (`:123`), `strategy_catalogs` (`:128`).
- Their callers in `src/`:
  - `match_worker.py:194` (history policy for `Follow300Signals`), `:337` and `:368` (clip profile name), `:1153` (`LIVE_MAX_POSITION_LEVELS[game]`).
  - `wallet_host.py:1673` (`GAME_PROFILES[game].primary.profile_name not in self._models`), `:1714` (`strategy_profile_name` → `self._models[...]`).
  - `host_resources.py:207-216` `_load_strategy_models` (`load_model` per catalog).
  - `session_config.py:87, 104, 107, 236, 243, 246` (expected profile names, satellite resolution, clip tables keyed `clips.<game>-<feed>`).
  - `core_trace.py:38` `model_file_sha(game)` hashes `GAME_PROFILES[game].primary.model_dir` (also for Oddin sessions; unchanged here).
- `LiveCore` (`src/trader/session_core.py:230-281`) calls `strategy.engine.step` at `:323`, `:326` (`preview_block_reason`) and `:592` (`apply`).
  `step` is `step_with_sides(..., sides=FOLLOW300_SIDES)` (`src/strategy/engine.py:141-142`). `match_worker.open_core` (`:1144-1154`) is the only `src/` constructor.
  Tests construct `LiveCore(` in 17 places (9 files, list in §4.1).
- `replay_core_trace._on_event` calls `step` (`:403`). `core_state_report.next_plan` passes `FOLLOW300_SIDES` (`:100`).
  The header already records `game` (`core_trace.py:264`); `TraceHeader.game` is decoded by `decode_header_row`.
- `follow300_policy` sets `buy_cutoff_second=BUY_CUTOFF_SECOND` (`src/strategy/policy.py:70`). `extraction_policy` also does (`:97`).
  `GridLiveFeed._apply_widget_frame` compares against `BUY_CUTOFF_SECOND` (`src/trader/grid_live_feed.py:84`).
  `follow300_policy(game="cs2", ...)` is called by the CS2 backtest (`src/backtest/run.py:451`, `tests/test_cs2_execution.py:91`,
  `tests/test_cs2_round_sides.py:336`, `tests/test_cs2_reports.py:93`). A cutoff table without `"cs2"` raises `KeyError` there.
  The backtest replaces the cutoff per map afterwards (`run.py:2395-2403`, `_with_map_cutoffs`).
- `LolLeagueFilter(archive_root)` (`src/trader/lol_league_filter.py:76-82`) loads `load_canonical_league_whitelist()`
  (`src/shared/utils/lol_leagues.py:28-32`), which reads the module global `LOL_LEAGUE_WHITELIST_PATH` at call time and logs
  `LoL league whitelist path=… sha256=…`. Built in `discovery.py:192` (`if profile.game == "lol"`) and `wallet_host.py:866` (LoL book watch).
  `backtest/run.py:2529` also calls `load_canonical_league_whitelist`.
- Two wallet-host tests monkeypatch `shared.utils.lol_leagues.LOL_LEAGUE_WHITELIST_PATH` (`tests/test_trader_wallet_host.py:2825`, `:2862`)
  and one reads the log under logger `shared.utils.lol_leagues` (`:2863`). Once the path comes from the profile, that patch does nothing.
- **Import cycles.** `game_profile` cannot import `Follow300Signals`/`model_server` today:
  1. `model_server.py:34` → `trader.bindings` → `bindings.py:5` → `trader.collector_sidecars` → `collector_sidecars.py:16` → `trader.game_profile` (`GameProfile`, only for `load_archive_root(profile)` at `:92`).
  2. `model_server.py:36` → `trader.notify` → `notify.py:13` → `trader.trading_mode` (`ExecutionMode` only) → `trading_mode.py:9` → `trader.game_profile` (`GAME_PROFILES`).
  The prototype hit both as `ImportError: cannot import name ... partially initialized module 'trader.game_profile'`.
- `tests/test_trader_host_resources.py:121` patches `trader.host_resources.load_model`. After the move, loading runs in `trader.follow300_strategy`.
- `tests/test_trader_match_lifecycle.py:477` sets `worker._discovered = replace(..., game=game)` on a Dota worker after construction.
  In production `_discovered` is replaced only in `_pin_feed_orientation` (`match_worker.py:290`, sides and market, never the game).
- The dashboard imports `trader.game_profile` (`src/dashboard/game_state.py:29`, `match_state.py:28`, `live_hub.py:66`, `game_grid.py:16`).
  Today that does not import `lightgbm`. After this step it does (through `follow300_signals` → `model_server`).
  `lightgbm` is a main dependency (`pyproject.toml:10`), so the dashboard image has it. Import cost measured: +0.66 s.
- Baseline gate values on this Mac at `582e3621` (planning runs, details in §5): core replay 13 sessions / 1 102 495 events / 0 mismatches;
  model smoke `dota-map` 81 `20261022T191205Z`, `dota-oddin-map` 74 `20261022T194409Z`, `lol-map` 81 `20261022T232724Z`;
  `tests/test_follow300_replay.py tests/test_extraction_oracle.py` → `21 passed` in 82 s.

## 2. Design decisions

1. **`GameProfile.strategy` is typed `StrategySpec`, not `Follow300Strategy`.** Every production caller goes through the protocol.
   This proves the seam before CS2. (`feed` stays `MobaGridFeed` until STEP-009, as `progress.txt` records.)
   Tests that inspect catalogs narrow with `assert isinstance(strategy, Follow300Strategy)`.
2. **`StrategySpec` members** (`src/trader/strategy_spec.py`, read-only properties so frozen dataclass fields satisfy them —
   the STEP-003 learning about `GridFeedSpec.services`):
   - `quote_sides: QuoteSides`, `max_position_levels: int`.
   - `primary_profile_name: str` and `satellite_profile_names: Mapping[FeedSource, str]`. Together they are the feature's `profile_names`.
     A flat tuple is not enough: `session_config` needs the feed of each satellite for the `clips.<game>-<feed>` key and the parent table.
   - `profile_name_for(feed_source) -> str` (replaces `strategy_profile_name`).
   - `trace_model_dir: Path` (replaces `.primary.model_dir` in `model_file_sha`). STEP-006 owns the round/outcome identity; it extends this.
   - `load_models() -> dict[str, ModelServer]` keyed by clip profile name (replaces `_load_strategy_models` + `strategy_catalogs`).
   - `build_signals(*, model, feed_source, enqueue_core, requote) -> SignalSource` (replaces the worker's `Follow300Signals(...)` construction).
   Model selection by feed stays `self._models[strategy.profile_name_for(feed.source)]` in `wallet_host`; no `model_for` member is needed.
3. **`Follow300Strategy` lives in a new `src/trader/follow300_strategy.py`**, together with `StrategyCatalog` (moved from `game_profile.py`).
   Frozen dataclass fields: `primary`, `satellites`, `max_position_levels`. `quote_sides` is a property returning `FOLLOW300_SIDES`.
   Extra public method `catalog_for(feed_source)` (the old `strategy_catalog` body). Tests and the signal-diff harness use it; the protocol does not.
4. **`LIVE_MAX_POSITION_LEVELS` stays a table** (`shared/constants/strategy.py:47`). `GAME_PROFILES` passes `LIVE_MAX_POSITION_LEVELS["dota"]` / `["lol"]`.
   STEP-009 writes `LIVE_MAX_POSITION_LEVELS["cs2"] = 6` into the same table.
5. **Break both import cycles at their root**, not with `TYPE_CHECKING`:
   - `collector_sidecars.load_archive_root(archive_root_env: str)`. It only reads an env var. Callers pass `profile.archive_root_env`.
   - `ExecutionMode = Literal["paper", "live"]` moves to `src/trader/session_types.py`. `trading_mode.py` and `notify.py` import it from there.
     The other 11 modules keep `from trader.trading_mode import ExecutionMode`: `trading_mode` uses the name in its own signatures, so it stays importable there.
6. **The worker reads the strategy once.** `self._strategy = GAME_PROFILES[discovered.game].strategy` in `MatchWorker.__init__`.
   The game of a worker never changes (§1). The lifecycle test that switched the game after construction builds a worker of that game instead.
7. **`LiveCore(..., sides: QuoteSides)` is required.** `preview_block_reason` and `apply` call `step_with_sides(..., sides=self._sides)`.
   `strategy.engine.step` stays: tests and the backtest harness use it.
8. **Replay sides by header game.** `replay_core_trace` resolves `GAME_PROFILES[header.game].strategy.quote_sides` on the first header.
   `_need` returns a frozen `_Ready(policy, sides, state)` instead of the anonymous `tuple[Follow300Policy, StrategyState]`
   (it now carries three values; AGENTS.md bans anonymous tuples). `ReplayedState` gets `sides`, and `core_state_report.next_plan` takes `sides`.
9. **`BUY_CUTOFF_SECOND_BY_GAME = {"dota": 480, "lol": 480, "cs2": 480}`**, all three as `BUY_CUTOFF_SECOND`.
   The `"cs2"` key keeps `follow300_policy(game="cs2")` working for the CS2 backtest (§1). Its value is today's value, so nothing changes.
   STEP-009 sets `"cs2"` to `CS2_BUY_CUTOFF_SECOND = 10**9`. `BUY_CUTOFF_SECOND` stays for offline code (train, backtest, dashboard, `extraction_policy`).
10. **`GridLiveFeed(..., buy_cutoff_second: int)`**: `feed_selection._build_feed` passes `BUY_CUTOFF_SECOND_BY_GAME[handoff.game]`. An int, not the game.
11. **`LeagueFilter(game, archive_root, whitelist_path)`** in `src/trader/league_filter.py` (`git mv` of `lol_league_filter.py`).
    - Loading and the identity log move to a new `shared.utils.league_whitelist.load_league_whitelist(game, path)`.
      `lol_leagues.load_canonical_league_whitelist()` becomes `return load_league_whitelist("LoL", LOL_LEAGUE_WHITELIST_PATH)`, so the backtest log text stays.
      Reason: if `LeagueFilter` logged the whitelist line itself, that line lands on the `trader.league_filter` logger, and 9 discovery tests that count
      exactly one `trader.league_filter` message fail (prototype run). The whitelist line belongs to the whitelist module.
    - Event log line: `"%s event %s event_id=%s league=%r reason=%s"` with `self._game`. Live LoL text changes from `LoL event …` to `lol event …`.
      No doc, skill or learning greps it (grep of `esports-trader/docs`, `AGENTS.md`, `betting_workspace/.shared-skills`, `.learnings`).
    - `GameProfile.league_whitelist_path: Path | None`: Dota `None`, LoL `LOL_LEAGUE_WHITELIST_PATH`.
      `MarketDiscovery` builds the filter when the path is not `None`. `wallet_host._sync_lol_book_watch` passes `LeagueFilter("lol", root, LOL_LEAGUE_WHITELIST_PATH)` explicitly.
12. **No new comments.** Docstrings only where the old code had them or where a protocol member's contract is not obvious from its name
    (`satellite_profile_names`, `trace_model_dir`, `load_models`). The review config bans narrating comments.

## 3. Code changes (in this order)

Every snippet below is in `$P5/proto.patch`. Run `ruff check --fix` and `ruff format` after the edits (import order).

### 3.1 Break the import cycles first

- `src/trader/collector_sidecars.py`: delete `from trader.game_profile import GameProfile` (`:16`).
  `def load_archive_root(archive_root_env: str) -> Path:` — docstring `Read \`archive_root_env\` and return it as a Path; blank or missing raises.`;
  body uses `archive_root_env` in `env_value(...)` and in the `RuntimeError` text (text unchanged: `f"{archive_root_env} is not set (env or .env)"`).
- `src/trader/host_resources.py:335`: `load_archive_root(profile.archive_root_env)`. `src/dashboard/live_hub.py:145`: same.
- `src/trader/session_types.py`: add `from typing import Literal` and `ExecutionMode = Literal["paper", "live"]` above the `PHASE_*` constants.
- `src/trader/trading_mode.py`: delete the local `ExecutionMode` and `from typing import Literal`; `from trader.session_types import ExecutionMode, TradingDisabled`.
- `src/trader/notify.py:13`: `from trader.session_types import ExecutionMode`.

Check: `PYTHONPATH=src .venv/bin/python -c "import trader.game_profile, trader.wallet_host, trader.trading_mode, trader.notify, dashboard.live_hub"` after §3.3.

### 3.2 `src/trader/strategy_spec.py`: add `StrategySpec`

Keep `SignalOutcome` and `SignalSource` as they are. New imports: `collections.abc.Callable, Mapping`, `pathlib.Path`,
`strategy.quoting.QuoteSides`, `strategy.types.InboundEvent`, `trader.live_feed.FeedSource`, `trader.model_server.ModelServer`.

```python
class StrategySpec(Protocol):
    """One game's trading strategy: quote sides, cap, clip profiles, models and signals."""

    @property
    def quote_sides(self) -> QuoteSides: ...

    @property
    def max_position_levels(self) -> int: ...

    @property
    def primary_profile_name(self) -> str: ...

    @property
    def satellite_profile_names(self) -> Mapping[FeedSource, str]:
        """Clip profiles of feeds that quote their own table, keyed by feed."""
        ...

    @property
    def trace_model_dir(self) -> Path:
        """The model directory whose identity the core trace header records."""
        ...

    def profile_name_for(self, feed_source: FeedSource) -> str: ...

    def load_models(self) -> dict[str, ModelServer]:
        """Every model this strategy quotes, keyed by clip profile name."""
        ...

    def build_signals(
        self,
        *,
        model: ModelServer,
        feed_source: FeedSource,
        enqueue_core: Callable[[InboundEvent], bool],
        requote: Callable[[], None],
    ) -> SignalSource: ...
```

### 3.3 New `src/trader/follow300_strategy.py`

No module docstring (the new STEP-004 files have none). Contents:

- `StrategyCatalog`: moved verbatim from `game_profile.py:33-45` (fields `profile_name`, `model_dir`, `features`, `source_lag_seconds`, property `history`).
- `@dataclass(frozen=True) class Follow300Strategy` — docstring `MOBA Follow300: the primary catalog, per-feed satellite catalogs, and the map cap.`
  - fields `primary: StrategyCatalog`, `satellites: Mapping[FeedSource, StrategyCatalog]`, `max_position_levels: int`.
  - `quote_sides` → `FOLLOW300_SIDES`; `primary_profile_name` → `self.primary.profile_name`;
    `satellite_profile_names` → `{source: catalog.profile_name for source, catalog in self.satellites.items()}`;
    `trace_model_dir` → `self.primary.model_dir`.
  - `catalog_for(feed_source)` → `self.satellites.get(feed_source) or self.primary` (docstring from the old `strategy_catalog`).
  - `profile_name_for(feed_source)` → `self.catalog_for(feed_source).profile_name`.
  - `load_models()` → `{c.profile_name: load_model(c.model_dir, c.features, c.source_lag_seconds) for c in (self.primary, *self.satellites.values())}`.
  - `build_signals(...)` → `Follow300Signals(model=model, history=SnapshotHistory(self.catalog_for(feed_source).history), board=BoardHistory(), enqueue_core=enqueue_core, requote=requote)`.

Imports: `BoardHistory` (`shared.utils.board_features`), `HistoryPolicy, SnapshotHistory, history_policy_for_columns` (`shared.utils.dota_features`),
`FOLLOW300_SIDES, QuoteSides`, `InboundEvent`, `Follow300Signals`, `FeedSource`, `ModelServer, load_model`, `SignalSource`.

### 3.4 `src/trader/game_profile.py`

- `GameProfile` fields, in this order: `game`, `archive_root_env`, `mode_env`, `uses_steam`, `aliases`, `league_whitelist_path: Path | None`, `feed: MobaGridFeed`, `strategy: StrategySpec`.
- Delete `StrategyCatalog`, `strategy_catalog`, `strategy_profile_name`, `strategy_catalogs`. Delete the now unused imports (`HistoryPolicy`, `history_policy_for_columns`).
- New imports: `LIVE_MAX_POSITION_LEVELS`, `LOL_LEAGUE_WHITELIST_PATH` (`shared.utils.lol_leagues`), `Follow300Strategy, StrategyCatalog`, `StrategySpec`.
- Dota: `league_whitelist_path=None`, `strategy=Follow300Strategy(primary=StrategyCatalog("dota-map", PRODUCTION_MODEL_DIR, tuple(DOTA_XP_FEATURE_COLUMNS), TRAIN_LAG_SECONDS), satellites=MappingProxyType({FeedSource.ODDIN: StrategyCatalog("dota-oddin-map", PRODUCTION_NOXP_MODEL_DIR, tuple(DOTA_NOXP_FEATURE_COLUMNS), TRAIN_LAG_SECONDS)}), max_position_levels=LIVE_MAX_POSITION_LEVELS["dota"])`.
- LoL: `league_whitelist_path=LOL_LEAGUE_WHITELIST_PATH`, `strategy=Follow300Strategy(primary=StrategyCatalog("lol-map", LOL_PRODUCTION_MODEL_DIR, tuple(DOTA_XP_FEATURE_COLUMNS), LOL_SOURCE_LAG_SECONDS), satellites=MappingProxyType({}), max_position_levels=LIVE_MAX_POSITION_LEVELS["lol"])`.

### 3.5 `src/trader/match_worker.py`

- Imports: drop `LIVE_MAX_POSITION_LEVELS`, `BoardHistory`, `SnapshotHistory`, `Follow300Signals`, `strategy_catalog, strategy_profile_name`, `SignalSource`.
  Add `from trader.game_profile import GAME_PROFILES`.
- `__init__` (`:194-201`):
  ```python
  self._strategy = GAME_PROFILES[discovered.game].strategy
  self._signals = self._strategy.build_signals(
      model=model,
      feed_source=feed.source,
      enqueue_core=self._enqueue_core,
      requote=self._quote_board,
  )
  ```
- `_clip_choice` (`:337-339`) and `_try_attach` (`:368-370`): `self._strategy.profile_name_for(self._feed.source)`. The `_try_attach` call stays inside its `try`.
- `open_core` (`:1153`): `max_position_levels=self._strategy.max_position_levels,` and `sides=self._strategy.quote_sides,`.

### 3.6 `src/trader/session_core.py`

- `from strategy.engine import step_with_sides`; `from strategy.quoting import QuoteSides`.
- `LiveCore.__init__`: required keyword `sides: QuoteSides` after `max_position_levels`; `self._sides = sides`.
- `preview_block_reason`: `step_with_sides(state=state, policy=self._policy, event=event, sides=self._sides)` in the loop;
  then `wake = Wake(now_ns=now_ns, forced=True)` and the same call with `event=wake`.
- `apply`: `out = step_with_sides(state=self._state, policy=self._policy, event=event, sides=self._sides)`.

### 3.7 `src/trader/replay_core_trace.py` and `src/trader/core_state_report.py`

- `replay_core_trace.py`: `from strategy.engine import step_with_sides`, `from strategy.quoting import QuoteSides`, `from trader.game_profile import GAME_PROFILES`.
  - `ReplayedState`: new field `sides: QuoteSides` after `policy`. `_Replay`: `sides: QuoteSides | None = None` after `policy`.
  - New `@dataclass(frozen=True) class _Ready: policy: Follow300Policy; sides: QuoteSides; state: StrategyState`.
    `_need(replay) -> _Ready` raises the same `StrictJsonError("trace is missing a header")` when `first`, `policy`, `sides` or `state` is `None`.
  - `_on_header` first-header branch: `replay.sides = GAME_PROFILES[header.game].strategy.quote_sides` after the policy line.
  - `_on_reset` (`ready.state`, `ready.policy.sell_min_life_s`), `_on_last_buy` (`_need(replay).state`), `_on_event`
    (`step_with_sides(state=ready.state, policy=ready.policy, event=event, sides=ready.sides)`), `replay_state` (`sides=ready.sides` into `ReplayedState`).
- `core_state_report.py`: `from strategy.quoting import QuoteSides, requote`;
  `next_plan(*, state, policy, sides: QuoteSides, now_ns)` passes `sides`; `build_report` passes `sides=replayed.sides`.

### 3.8 Cutoff table

- `src/shared/constants/strategy.py`, right after `BUY_CUTOFF_SECOND = 480`:
  `BUY_CUTOFF_SECOND_BY_GAME = {"dota": BUY_CUTOFF_SECOND, "lol": BUY_CUTOFF_SECOND, "cs2": BUY_CUTOFF_SECOND}`. No comment.
- `src/strategy/policy.py:70`: `buy_cutoff_second=BUY_CUTOFF_SECOND_BY_GAME[game]`. `extraction_policy` (`:97`) keeps `BUY_CUTOFF_SECOND`.
- `src/trader/grid_live_feed.py`: drop `BUY_CUTOFF_SECOND` from the import; `__init__(..., reducer, buy_cutoff_second: int)` stores `self._buy_cutoff_second`;
  `:84` compares with `self._buy_cutoff_second`.
- `src/trader/feed_selection.py:103`: `GridLiveFeed(series_id, profile.feed.services, handoff.match_id, reducer, BUY_CUTOFF_SECOND_BY_GAME[handoff.game])`.

### 3.9 Profile names and models through the strategy

- `src/trader/host_resources.py`: imports become `from trader.game_profile import GameProfile` and `from trader.model_server import ModelServer`.
  `_load_strategy_models` body: `return {name: model for profile in games for name, model in profile.strategy.load_models().items()}`.
- `src/trader/session_config.py`: import only `GameProfile`.
  - `read_template` expected set: names from `(strategy.primary_profile_name, *strategy.satellite_profile_names.values())`.
  - Resolution loop: `name = game_profile.strategy.primary_profile_name`; `for satellite in game_profile.strategy.satellite_profile_names.values(): resolved[satellite] = _resolved_satellite(profiles_table, satellite, parent)`.
  - `_clip_tables`: `satellites = game_profile.strategy.satellite_profile_names`; `needed.update(f"{game}-{source.value}" for source in satellites)`;
    `tables[strategy.primary_profile_name] = _clip_table(...)`; `for source, name in strategy.satellite_profile_names.items(): ... default = profiles[name]["base_size_usdc"]; tables[name] = _satellite_clip_table(key, default, clips_table[key])`.
- `src/trader/wallet_host.py`: `:1673` → `GAME_PROFILES[game].strategy.primary_profile_name`; `:1714` → `GAME_PROFILES[game].strategy.profile_name_for(feed.source)`;
  drop `strategy_profile_name` from the import.
- `src/trader/core_trace.py:38`: `model_identity_sha256(GAME_PROFILES[game].strategy.trace_model_dir)`.

### 3.10 `LeagueFilter`

- `git mv src/trader/lol_league_filter.py src/trader/league_filter.py`.
- Module docstring: `"""Admit collector events using their current archived Polymarket league."""`.
- `class LeagueFilter:` docstring `Snapshot one game's whitelist at startup, retry event files every cycle.`
  `__init__(self, game: str, archive_root: Path, whitelist_path: Path)`: `self._game = game`, `self._archive_root = archive_root`,
  `self._whitelist = load_league_whitelist(game, whitelist_path)`, `self._skips = {}`. Import `load_league_whitelist` instead of `load_canonical_league_whitelist`.
- `select_sidecars` log: `"%s event %s event_id=%s league=%r reason=%s"` with `self._game` first. Nothing else changes.
- `src/shared/utils/league_whitelist.py`: add `from shared.utils.log import get_logger`, `logger = get_logger(__name__)`, and
  ```python
  def load_league_whitelist(game: str, path: Path) -> LeagueWhitelist:
      """Read one game's mandatory admission policy and announce its exact identity."""
      whitelist = read_league_whitelist(path)
      logger.info("%s league whitelist path=%s sha256=%s", game, whitelist.path, whitelist.sha256)
      return whitelist
  ```
- `src/shared/utils/lol_leagues.py`: `load_canonical_league_whitelist` body → `return load_league_whitelist("LoL", LOL_LEAGUE_WHITELIST_PATH)`.
  Import `load_league_whitelist` instead of `read_league_whitelist`; delete the now unused `logger` and `get_logger` import.
- `src/trader/discovery.py:29, :192`:
  ```python
  whitelist_path = profile.league_whitelist_path
  self._league_filter = (
      None
      if whitelist_path is None
      else LeagueFilter(profile.game, archive_root, whitelist_path)
  )
  ```
- `src/trader/wallet_host.py:113, :707, :866`: `LeagueFilter`; `LeagueFilter("lol", root, LOL_LEAGUE_WHITELIST_PATH)` with
  `from shared.utils.lol_leagues import LOL_LEAGUE_WHITELIST_PATH`.

## 4. Tests

### 4.1 Update (seams only; keep every existing assertion unless named)

- `sides=FOLLOW300_SIDES,` after `max_position_levels=...` in all 17 `LiveCore(` calls, plus the `FOLLOW300_SIDES` import:
  `tests/adapter_contract_fixtures.py` (1), `test_core_trace.py` (5), `test_trader_book_guard.py` (1), `test_trader_core_persistence.py` (1),
  `test_trader_core_recovery.py` (4), `test_trader_live_execution.py` (1), `test_trader_session_core.py` (2), `test_trader_shared_budget.py` (1),
  `test_trader_unsettled_buy_activation.py` (1).
- `tests/test_trader_session_core.py`: `_core(...)` gets `sides: QuoteSides = FOLLOW300_SIDES` and passes it (the helper already has defaults).
- `tests/test_grid_feed.py` (2) and `tests/test_trader_match_lifecycle.py:280` (1): pass `BUY_CUTOFF_SECOND_BY_GAME["dota"]` / `["lol"]` to `GridLiveFeed`.
- `tests/test_trader_match_lifecycle.py:471-477`: `build_attached_worker(tmp_path, request, game=game)` and delete the `replace(worker._discovered, game=game)` line.
- `tests/test_collector_sidecars.py:42, :51, :64`: `load_archive_root(DOTA.archive_root_env)` / `LOL.archive_root_env` / `profile.archive_root_env`.
- `tests/test_trader_host_resources.py`: `fake_archive(archive_root_env: str)` appends `archive_root_env`; patch `"trader.follow300_strategy.load_model"`.
- `tests/test_model_server.py:180-191, :212, :394-397` and every `harness.profile` / `profile_with_dir`: helpers become
  `catalog_with_dir(game, model_dir) -> StrategyCatalog` (narrow `GAME_PROFILES[game].strategy` to `Follow300Strategy`, `replace(strategy.primary, model_dir=...)`)
  and `load_production(catalog: StrategyCatalog)`; the harness attribute becomes `catalog`; the reorder test does `replace(harness.catalog, features=tuple(reordered))`.
- `tests/test_follow300_signals.py`: `_history` narrows to `Follow300Strategy` and uses `catalog_for(source)`;
  `test_check_history_follows_the_catalog_lag_policy` builds its source with `GAME_PROFILES[game].strategy.build_signals(model=cast(Any, FakeModelServer()), feed_source=source, enqueue_core=worker.enqueue_core, requote=worker.requote)`.
  That makes the existing dota/GRID False, dota/ODDIN True, lol/GRID False matrix the regression for `build_signals`.
- `tests/test_dota_map_config.py:340-342`: `GAME_PROFILES[g].strategy.profile_name_for(FeedSource.X)`.
- `tests/test_trader_lol_discovery.py`, `tests/test_lol_build_universe.py`: `trader.lol_league_filter` → `trader.league_filter` (imports, `patch(...)` targets, logger names).
- `tests/test_trader_wallet_host.py`:
  - `:450` and `:1578`: fake model dicts keyed by `strategy.primary_profile_name` (+ `satellite_profile_names.values()` for Dota). Drop `strategy_catalogs`.
  - `test_invalid_lol_whitelist_prevents_engine_start` (`:2813`): `host._games = (replace(GAME_PROFILES["lol"], league_whitelist_path=whitelist),)`; delete the monkeypatch and the `monkeypatch` parameter.
  - `test_lol_start_logs_whitelist_before_engine_start` (`:2848`): same `replace`; delete the path monkeypatch; `caplog.set_level(logging.INFO, logger="shared.utils.league_whitelist")`.

### 4.2 Add or rewrite

- `tests/test_trader_game_profile.py` (helper `_follow300(game)` with the `isinstance` narrow):
  - Dota/LoL field tests: `league_whitelist_path` (`None` / `LOL_LEAGUE_WHITELIST_PATH`), `strategy.primary == StrategyCatalog(...)`, `satellites`,
    `max_position_levels == 9` / `10`, `quote_sides is FOLLOW300_SIDES`, `primary_profile_name`, `satellite_profile_names`
    (`{FeedSource.ODDIN: "dota-oddin-map"}` / `{}`), `trace_model_dir is PRODUCTION_MODEL_DIR` / `LOL_PRODUCTION_MODEL_DIR`.
  - `test_catalog_for_picks_the_feed_history_policy` (rename of the `strategy_catalog` test).
  - New `test_follow300_policy_keeps_the_dota_and_lol_baseline`, parametrized dota/lol, literal numbers:
    `buy_cutoff_second == 480`, `min_abs_delta == 0.02`, `exit_abs_delta == 0.015`, `min_entry_price == 0.45`, `reanchor_drift_k == 0.3`, `mid_spike_cooloff_s == 30.0`.
  - `test_profiles_are_frozen`: also `profile.strategy.__setattr__("max_position_levels", 1)` raises `FrozenInstanceError`.
- `tests/test_trader_session_core.py`, new `test_core_steps_with_the_sides_it_was_given`, parametrized `(FOLLOW300_SIDES, True), (ROUND_SIDES, False)`:
  `core = _core(sides=sides)`, `core.set_clock(paused clock)`, `(core.preview_block_reason() == "paused") is blocked`,
  `(core.apply(ClockUpdate(now_ns=1, clock=clock)).block_reason == "paused") is blocked`.
  `ROUND_SIDES` from `backtest.cs2_round_sides` (STEP-006 moves it and updates this import). It fails if `LiveCore` ignores `sides`.

Prototype: `--collect-only` gives 3606 items; the base snapshot gives 3602. The difference is the four new parametrized cases.

## 5. Gates (evidence for `report.md`)

Write everything to `investigations/2026-10-23-cs2-live-validation/work/step-005/` (`$W`). Copy the scratch tools from `$P5`:
`run_core_replay.sh`, `core_sessions.txt`, `model_smoke.py`, `signals_diff.py`, `sessions.txt`. Copy the GRID tool from `work/step-003/grid_reducer_diff.py`.
Record the start and end time (UTC) of the whole package; the budget is one hour. Measured planning cost: about 7 minutes of runs.

Before any edit: `git rev-parse HEAD` (expect `582e3621…`) and `git status --short` (expect empty).
Take the baseline runs on the clean tree first (in-tree `PYTHONPATH=src`). If edits already started:
`mkdir -p $W/base_snapshot && git archive 582e3621 src tests config | tar -x -C $W/base_snapshot` and use `PYTHONPATH=$W/base_snapshot/src[:$W/base_snapshot/tests]`.
Do not create a worktree.

### Gate 1 — tests, typecheck, lint, goldens

- `.venv/bin/ruff check src tests scripts`, `.venv/bin/ruff format --check src tests scripts`, `.venv/bin/basedpyright` → clean / `0 errors`.
- `PYTEST_N=8 make test` → record counts. Expected: all pass, or only the known `test_upcoming_rows_from_sidecars` failure between 23:00 and 00:00 Berlin
  (STEP-004 progress note). Any other failure fails the gate.
- Goldens, also named in the report: `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 tests/test_follow300_replay.py tests/test_extraction_oracle.py`
  → `21 passed` (baseline 82 s).

### Gate 2 — core trace replay, `--policy current`

`$W/run_core_replay.sh $W/core_sessions.txt $W/core-base src` on the clean tree, then `$W/run_core_replay.sh $W/core_sessions.txt $W/core-head src` after the commit.
It runs `python -m trader.replay_core_trace --archive data/trader/<id> --policy current` for 13 finished sessions, 7 in parallel.
Same sessions as STEP-003/004 plus the Oddin trace the 2026-10-22 audit used. Data is local; `sync_trader.py` is not needed.

Baseline at `582e3621` (planning run, wall clock 2 min 15 s):

| game | session | events | plan / wake / digest |
|---|---|---|---|
| dota | grid-3015051-m2 | 76 104 | 0 / 0 / 0 |
| dota | grid-3015043-m1 | 116 858 | 0 / 0 / 0 |
| dota | grid-3015051-m1 | 133 540 | 0 / 0 / 0 |
| dota | grid-3015042-m2 | 100 021 | 0 / 0 / 0 |
| dota | grid-3015050-m2 | 81 347 | 0 / 0 / 0 |
| dota | grid-3015050-m1 | 110 264 | 0 / 0 / 0 |
| dota (Oddin) | 9056907498 | 13 821 | 0 / 0 / 0 |
| lol | grid-3008335-m5 | 75 090 | 0 / 0 / 0 |
| lol | grid-3008335-m4 | 89 346 | 0 / 0 / 0 |
| lol | grid-3012947-m3 | 66 391 | 0 / 0 / 0 |
| lol | grid-3012947-m2 | 52 114 | 0 / 0 / 0 |
| lol | grid-3012946-m4 | 84 339 | 0 / 0 / 0 |
| lol | grid-3012946-m3 | 103 260 | 0 / 0 / 0 |

Total 1 102 495 events (Dota 631 955, LoL 470 540). Every run exits 0.
Pass: head prints the same event counts and `plan=0 wake=0 digest=0` for all 13. Write `$W/core-differential.json`
(`base_rev`, `head_rev`, per-session events and mismatches, totals, wall clock).

### Gate 3 — GRID reducer differential (≥ 10 000 frames)

`PYTHONPATH=src:tests .venv/bin/python $W/grid_reducer_diff.py data/trader $W/sessions.txt > $W/grid-base.txt` on the clean tree, the same into `$W/grid-head.txt` after.
The script reads `profile.feed`, which this step does not change. Expected: 24 427 lines each (2 service + 10 200 synthetic + 7 175 Dota + 7 050 LoL frames),
`cmp` equal. Write `$W/grid-reducer-diff.json` like STEP-003 (`base_rev`, `head_rev`, line counts per prefix, `different_lines`, `first_diffs`).

### Gate 4 — production model load

`PYTHONPATH=src .venv/bin/python $W/model_smoke.py > $W/model-smoke-base.txt` on the clean tree, `> $W/model-smoke-head.txt` after.
Base loads through `strategy_catalogs` + `load_model`; head through `profile.strategy.load_models()`, the `open_wallet_host` path. Baseline:

| game | profile | features | ModelReference |
|---|---|---|---|
| dota | dota-map | 81 | `20261022T191205Z` / `2026-10-22T19:12:05Z` |
| dota | dota-oddin-map | 74 | `20261022T194409Z` / `2026-10-22T19:44:09Z` |
| lol | lol-map | 81 | `20261022T232724Z` / `2026-10-22T23:27:24Z` |

These differ from the 2026-10-22 audit log (`model-smoke.log`: `20261012T…`). The local production models are newer; compare against this baseline.
Pass: `diff` of the two files is empty (names, counts, feature-order sha256, references).

### Extra — signal differential (worker construction moved)

`build_signals` now creates the history and board. Rerun the STEP-004 harness with the compat copy:
`PYTHONPATH=src:tests .venv/bin/python $W/signals_diff.py data/trader $W/sessions.txt > $W/signals-head.txt 2> $W/signals-head.err`.
Expected: 27 514 lines, sha256 `7b70dde69205f7823f4e2509ce33806dfe46ff162f14921873b2d4e16d3c445e` (the STEP-004 base and fix value),
`cmp` with `work/step-004/signals-fix.txt` equal. Rerun the base too if the head differs.

Any mismatch in gates 2–4 or the extra check fails the step. Missing data is not a pass.

## 6. Verification

From the trader repo root:

1. `.venv/bin/ruff check src tests scripts` and `.venv/bin/ruff format --check src tests scripts` → clean.
2. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`.
3. Targeted pytest:
   ```
   PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 \
     tests/test_trader_game_profile.py tests/test_trader_session_core.py tests/test_core_trace.py tests/test_trader_core_state_report.py \
     tests/test_follow300_signals.py tests/test_model_server.py tests/test_trader_host_resources.py tests/test_trader_wallet_host.py \
     tests/test_trader_session_config.py tests/test_dota_map_config.py tests/test_collector_sidecars.py tests/test_trader_lol_discovery.py \
     tests/test_lol_build_universe.py tests/test_grid_feed.py tests/test_trader_match_lifecycle.py tests/test_trader_trading_mode.py \
     tests/test_strategy_core.py tests/test_cs2_round_sides.py tests/test_cs2_execution.py
   ```
   Expect `718 passed` (prototype).
4. `PYTEST_N=8 make test` (gate 1).
5. Import smoke: `PYTHONPATH=src .venv/bin/python -c "import trader.game_profile, trader.follow300_strategy, trader.wallet_host, trader.trading_mode, trader.notify, trader.replay_core_trace, trader.core_state_report, dashboard.live_hub"`.
6. CLI smoke: `PYTHONPATH=src .venv/bin/python -m trader.core_state_report --help` and `-m trader.replay_core_trace --archive data/trader/9056907498 --policy current` → `OK`.
7. `grep -rn "strategy_catalog\b\|strategy_catalogs\|strategy_profile_name\|lol_league_filter\|LolLeagueFilter\|\.primary\.profile_name\|LIVE_MAX_POSITION_LEVELS\[self" src tests scripts` → no output.
8. `git diff --stat` touches only the 44 files of `$P5/proto.patch` (24 under `src/` incl. the rename and the new file, 20 under `tests/`).
9. Gates 2–4 and the extra check (§5).

No browser check: the feature item for this step has none. The dashboard change is one call (`load_archive_root(profile.archive_root_env)`); the import smoke covers it.

Write into `report.md` under `## STEP-005`: revisions, wall clock of the package, the four gates with numbers and file names, the extra check,
pytest counts, ruff and basedpyright output.

## 7. Commit

One commit in `esports-trader` on `main`, for example:
`refactor(trader): move Follow300 catalogs and sides behind StrategySpec, inject QuoteSides, generalize LeagueFilter`.
No push. `progress.txt` gets the usual STEP-005 entry from the implement skill.

## 8. Out of scope (later steps own these)

- `RoundStrategy`, `ROUND_SIDES` move, `RoundSignal` trace codec, `Cs2RoundModels`, round/outcome identity in `model_file_sha` (STEP-006).
- Widening `GameProfile.feed` to `GridFeedSpec`, `GAME_PROFILES["cs2"]`, `CS2_BUY_CUTOFF_SECOND`, `LIVE_MAX_POSITION_LEVELS["cs2"]`, `buys_open` (STEP-009).
- The game branch in `match_worker._maybe_start_prior` (`:607`, LoL book prior) and `wallet_host.select_title_blacklist` (`:170`). Not strategy members today.
- Moving the remaining 11 `from trader.trading_mode import ExecutionMode` imports.
- `model_file_sha` hashing the primary catalog for Oddin sessions. It is today's behavior; STEP-006 redefines it.

## 9. Risks and hard parts

- **Import cycles (§1).** Any new `trader.game_profile` import below `model_server`, `bindings`, `notify` or `collector_sidecars` brings the cycle back.
  Check with the import smoke (§6.5). `$P5/import_paths.py trader.follow300_strategy trader.game_profile` prints the path when one exists (run from `$P5/proto`, `.venv/bin/python`).
- **Monkeypatch targets that stop working silently.** `trader.host_resources.load_model` → `trader.follow300_strategy.load_model`;
  `shared.utils.lol_leagues.LOL_LEAGUE_WHITELIST_PATH` no longer reaches live code (the profile binds the path at import). Both tests in §4.1 cover it.
- **Logger counts.** Do not log the whitelist identity from `trader.league_filter`; 9 `test_trader_lol_discovery` cases count its messages (§2.11).
- **Live log text.** `LoL league whitelist …` and `LoL event …` become `lol league whitelist …` and `lol event …` on the trader. Backtest text stays `LoL`.
- **`follow300_policy(game="cs2")`.** Without the `"cs2"` key the CS2 backtest tests raise `KeyError`. Keep the key at 480 in this step.
- **Dashboard imports lightgbm** through `game_profile` (+0.66 s import, same image). Accepted: the dashboard image installs main dependencies.
- **Replay speed.** `replay_core_trace` now imports `trader.game_profile` (and `lightgbm`). One-time import cost; gate 2 wall clock shows it.
- **Snapshot tests.** In a `git archive` snapshot 29 tests fail on missing files on base and head alike. Run gate 1 in the repo, not in a snapshot.
- **Known flake.** `test_upcoming_rows_from_sidecars` fails between 23:00 and 00:00 Berlin; `test_seed0_map_replay` / `test_follow300_replay` flaked once under xdist (progress.txt).
  Rerun a flaky test alone before calling it a regression, and write both runs into the report.
