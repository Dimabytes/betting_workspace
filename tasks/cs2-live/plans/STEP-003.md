# STEP-003 plan: Trader 2A — split out the GRID feed contract

Step: `STEP-003` of `tasks/cs2-live/feature.json` (priority 3, `passes: false`).
Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, clean, HEAD `6b1080653026b4b0c58986a2cf03ba2e74918add`.
Evidence folder: `betting_workspace/investigations/2026-10-23-cs2-live-validation/` (`report.md` + `work/step-003/`).
The folder does not exist yet. Create it with `mkdir -p`. If `report.md` exists, append a section. Never overwrite it.
No Figma or design links exist for this step.

The step is a pure refactor. Dota/LoL behavior must not change. No CS2 code lands here.
The MOBA GRID fields move from `GameProfile` into `MobaGridFeed`. Discovery, feed selection,
`GridLiveFeed`, replay, offline readers and the dashboard read them through `profile.feed`.

Do not deploy, do not `git push`, do not run `scripts/sync_trader.py`, do not touch STEP-004+.

## 1. Facts this plan rests on

- `GameProfile` carries the MOBA feed fields `level_xp`, `side_0_text`, `side_1_text`, `grid_services`:
  `src/trader/game_profile.py:43-57`. Values: Dota `LEVEL_XP`, `"RADIANT"`, `"DIRE"`,
  `(*REDUCED_SERVICES, MAP_SCHEMATIC_SERVICE)` (`:65-67, :86`); LoL `LOL_LEVEL_XP`, `"BLUE"`, `"RED"`,
  `REDUCED_SERVICES` (`:92-94, :104`).
- These four fields are the only moved fields read outside `game_profile.py`. A grep of `profile\.[a-z_]*` over `src scripts`
  shows `side_*` ×14, `level_xp` ×1, `grid_services` ×1. Every other access (`game`, `uses_steam`, `satellites`,
  `aliases`, `primary`, `archive_root_env`, `mode_env`) stays on `GameProfile` in this step.
- Readers of the moved fields:
  - `src/trader/grid_feed.py:194-197` picks the top builder with `if profile.game == "lol"`; `:207` reads `profile.level_xp`;
    `:265-272` reads `side_0_text`/`side_1_text`/`aliases`.
  - `src/trader/grid_live_feed.py:59` reads `profile.grid_services`; `:61` builds `GridFrameReducer(..., profile)`.
  - `src/trader/discovery.py:513-520` calls `read_board_sides(board, o0, o1, profile.side_0_text, profile.side_1_text, profile.aliases)`.
  - `src/dashboard/game_grid.py:228-229, 247-248, 346, 352`; `src/dashboard/game_state.py:425-426`; `src/dashboard/match_state.py:194`.
- `GridFrameReducer(map_number, o0, o1, profile)` is built in 4 source places: `src/trader/grid_live_feed.py:61`,
  `src/trader/grid_archive.py:39-44`, `src/archive_index/schedule.py:321-326`, `src/viewer/game_state_replay.py:192`.
  `grid_archive.summarize` and `schedule._extract_grid` also read `reducer.board` / `reducer.read_pinned_horn_iso()`.
- `probe_grid_delay(series_id, map_number)` is async: `src/trader/source_picker.py:125-134`. Its only caller is
  `src/trader/feed_selection.py:116-119`. Tests patch it as `"trader.feed_selection.probe_grid_delay"` in 8 places of
  `tests/test_trader_wallet_host.py` (lines 1482, 1507, 1556, 1858, 1945, 1970, 2482, 2720).
- `grid_feed.py:17` imports `GameProfile`. `game_profile.py` must import `MobaGridFeed` from `grid_feed.py`.
  So the import of `GameProfile` in `grid_feed.py` must go, or the modules form a cycle.
- `source_picker.py` imports only `grid_widgets`, `live_feed`, `oddin_*`. None of them import `game_profile` or `grid_feed`.
  So `grid_feed.py` can import `probe_grid_delay` without a cycle. `httpx` and `websockets` are main dependencies (`pyproject.toml:9, 22`).
- Python is 3.13 (`.venv/bin/python --version`), ruff bans `from __future__ import annotations` (`pyproject.toml` TID251).
  A forward reference needs quotes. The repo already uses them: `src/trader/bindings.py:67`, `src/trader/core_trace.py:74`.
- Tick types `FeedEvent` and `KillTick` and the `LiveFeed` protocol live in `src/trader/live_feed.py:59, :146, :154-161`.
  That module imports no reducer. The requirement "tick types do not depend on a concrete reducer" holds already.
- basedpyright runs `strict` over `src`, `tests` and `scripts` (`pyrightconfig.json`). Baseline: `0 errors, 0 warnings, 0 notes`.
- Baseline ruff: `ruff check src tests scripts` → `All checks passed!`; `ruff format --check` → `528 files already formatted`.
- Baseline targeted pytest (list in §6, without the 3 extra files): `512 passed in 5.73s`.
- Pre-existing quirk, not in scope: `src/trader/grid_archive.py:96-108` finds the winner only on `RADIANT`/`DIRE` sides.
  A LoL summary (`BLUE`/`RED`) always has `winner=None`. The refactor must keep this unchanged.

## 2. Design decisions

1. **`GameProfile.feed` is typed `MobaGridFeed` in this step, not `GridFeedSpec`.**
   Reason: MOBA is the only implementation. Four consumers need MOBA-only members:
   the dashboard `GridFold` and side labels (`side_0_text`, `side_1_text`), `grid_archive.summarize` and
   `schedule._extract_grid` (`reducer.board`, `reducer.read_pinned_horn_iso()`).
   A `GridFeedSpec`-typed field would force `isinstance` branches for inputs that cannot occur today (AGENTS.md rule),
   or CS2 members guessed before STEP-007. STEP-009 widens the field to `GridFeedSpec` when `GAME_PROFILES["cs2"]` lands.
   basedpyright then lists exactly the MOBA-only consumers above.
2. **Generic consumers depend only on the contract.** `GridLiveFeed` and `replay_grid_records` take a `WidgetReducer`.
   `discovery._grid_source` takes `feed: GridFeedSpec`. That call site makes basedpyright check that
   `MobaGridFeed` satisfies `GridFeedSpec`, and that `GridFrameReducer` satisfies `WidgetReducer`.
   `feed_selection` uses only contract members (`services`, `probe_delay`, `build_reducer`).
3. **`BoardSides` and `BoardSideRead` move to `grid_feed_spec.py`.** `GridFeedSpec.read_board_sides` returns them.
   `grid_feed.py` must import `WidgetReducer` for `replay_grid_records`. If the contract imported these types from `grid_feed.py`,
   the two modules would form a cycle.
4. **`GridOrientationError`, `FEED_GONE_SERVICE`, `FEED_GONE_FRAME`, `select_board_players` stay in `grid_feed.py`.**
   This step does not need them moved. STEP-007 decides if CS2 needs them elsewhere.
5. **The free function `read_board_sides` is deleted.** Its body becomes `MobaGridFeed.read_board_sides`.
   It has no test callers (grep of `tests/`). Its 3 callers switch to the method.
6. **No new tick alias.** `FeedEvent | KillTick` stays spelled out. STEP-007 adds the round ticks and decides the union.
7. **`services` is a read-only `@property` in the protocol.** A plain protocol attribute is writable. A frozen dataclass field
   does not satisfy a writable protocol attribute in pyright strict.
8. **The reducer takes `aliases` separately.** The feature contract is `build_reducer(map_number, o0, o1, aliases)`.
   Aliases stay on `GameProfile` because discovery also uses them for Oddin binding (`discovery.py:543-559, :593`).
9. **The old-vs-new comparison is an investigation script, not a repo test.** After the refactor the old reducer
   is not in the repo. The script runs on the baseline checkout and on the new checkout and diffs per-frame hashes.
   The existing reducer tests stay as the automated guard (`tests/test_grid_feed.py`, `tests/test_lol_grid_feed.py`).

## 3. Code changes (in this order)

### 3.1 New `src/trader/grid_feed_spec.py`

Module docstring: one line about the GRID feed contract.

- `BoardSides` and `BoardSideRead`: move verbatim from `src/trader/grid_feed.py:63-79` (same fields, same docstrings).
- `class WidgetReducer(Protocol)`:
  - `def reduce_frame(self, frame: Frame, now: datetime) -> FeedEvent | KillTick | None: ...`
  - `def note_feed_gap(self) -> None: ...`
- `class GridFeedSpec(Protocol)`:
  - `@property def services(self) -> tuple[str, ...]: ...`
  - `def read_board_sides(self, board: Scoreboard, outcome_0_name: str, outcome_1_name: str, aliases: Mapping[str, tuple[str, ...]]) -> BoardSideRead: ...`
  - `async def probe_delay(self, series_id: str, map_number: int) -> int | None: ...`
  - `def build_reducer(self, map_number: int, outcome_0_name: str, outcome_1_name: str, aliases: Mapping[str, tuple[str, ...]]) -> WidgetReducer: ...`
- Imports only `collections.abc`, `dataclasses`, `datetime`, `typing.Protocol`, `trader.grid_widgets` (`Frame`, `Scoreboard`),
  `trader.live_feed` (`FeedEvent`, `KillTick`). No import of `grid_feed` or `game_profile`.
- Follow the `LiveFeed` protocol style (`live_feed.py:154-161`): short docstrings, `...` bodies.

### 3.2 `src/trader/grid_feed.py`

- Delete `from trader.game_profile import GameProfile` (`:17`).
- Import `BoardSides`, `BoardSideRead`, `WidgetReducer` from `trader.grid_feed_spec`; delete the two class definitions (`:63-79`).
- Import `probe_grid_delay` from `trader.source_picker`. Import `Callable`, `Sequence` from `collections.abc`, and `TopPlayerFeatures` from `shared.utils.top_players`.
  The `build_top_player_features*` imports (`:12-16`) move to `game_profile.py`. Keep `ZERO_TOP`.
- Delete the free function `read_board_sides` (`:99-130`).
- `_live_snapshot(board, players, feed_delay, now, feed: "MobaGridFeed")` (`:180-212`):
  - `top = feed.top_builder(radiant_nws, dire_nws)`. This replaces the `if profile.game == "lol"` branch at `:194-197`.
  - `radiant_xp_adv=xp_advantage(feed.level_xp, radiant_levels, dire_levels)` (`:207`).
- `GridFrameReducer.__init__(self, map_number: int, outcome_0_name: str, outcome_1_name: str, aliases: Mapping[str, tuple[str, ...]], feed: "MobaGridFeed")`:
  store `self._aliases`, `self._feed`; drop `self._profile`. Update the docstring (it names "the profile").
- `_lock_sides` (`:265-272`): `read = self._feed.read_board_sides(board, self._outcome_0_name, self._outcome_1_name, self._aliases)`.
- `_on_table` (`:406`): pass `self._feed` to `_live_snapshot`.
- `replay_grid_records(records, reducer: WidgetReducer)` (`:414-417`). The body does not change.
- New frozen dataclass `MobaGridFeed`, defined after `GridFrameReducer`, fields in the feature order:
  - `level_xp: tuple[int, ...]`
  - `side_0_text: str`
  - `side_1_text: str`
  - `top_builder: Callable[[Sequence[int], Sequence[int]], TopPlayerFeatures]`
  - `services: tuple[str, ...]`
  - `read_board_sides(self, board, outcome_0_name, outcome_1_name, aliases) -> BoardSideRead`: the old free-function body,
    with `self.side_0_text` / `self.side_1_text`. Keep its docstring content (pending vs unresolved).
  - `async def probe_delay(self, series_id: str, map_number: int) -> int | None`: `return await probe_grid_delay(series_id, map_number)`.
  - `build_reducer(self, map_number, outcome_0_name, outcome_1_name, aliases) -> GridFrameReducer`:
    `return GridFrameReducer(map_number, outcome_0_name, outcome_1_name, aliases, self)`.
  The concrete return type `GridFrameReducer` lets offline readers keep `reducer.board` and `read_pinned_horn_iso()`.

### 3.3 `src/trader/game_profile.py`

- `GameProfile`: delete `level_xp`, `side_0_text`, `side_1_text`, `grid_services`; add `feed: MobaGridFeed` as the last field.
  Update the module docstring only if it names the moved fields (it does not today).
- `GAME_PROFILES["dota"]`: `feed=MobaGridFeed(level_xp=LEVEL_XP, side_0_text="RADIANT", side_1_text="DIRE", top_builder=build_top_player_features, services=(*REDUCED_SERVICES, MAP_SCHEMATIC_SERVICE))`.
- `GAME_PROFILES["lol"]`: `feed=MobaGridFeed(level_xp=LOL_LEVEL_XP, side_0_text="BLUE", side_1_text="RED", top_builder=build_top_player_features_over_total, services=REDUCED_SERVICES)`.
- Values are copied from today's profile lines. Do not change any value.
- `StrategyCatalog`, `primary`, `satellites`, `strategy_catalog*` stay as they are (STEP-005 moves them).

### 3.4 `src/trader/grid_live_feed.py`

- `__init__(self, series_id: str, services: tuple[str, ...], match_id: str, reducer: WidgetReducer)`:
  `self._url = build_socket_url(series_id, services)`, `self._match_id = match_id`, `self._reducer = reducer`.
  Update the docstring: it talks about `profile`, XP and the top-player rules.
- Imports: drop `GameProfile` and `GridFrameReducer`; keep `FEED_GONE_FRAME` from `trader.grid_feed`; add `WidgetReducer` from `trader.grid_feed_spec`.
- `BUY_CUTOFF_SECOND` stays (STEP-005 owns the per-game cutoff).

### 3.5 `src/trader/feed_selection.py`

- `_build_feed` (`:96-107`):
  ```python
  profile = GAME_PROFILES[handoff.game]
  market = handoff.market
  reducer = profile.feed.build_reducer(
      handoff.map_number, market.outcome_0_name, market.outcome_1_name, profile.aliases
  )
  return GridLiveFeed(series_id, profile.feed.services, handoff.match_id, reducer)
  ```
- `_collect_candidates.grid_probe` (`:119`): `return await profile.feed.probe_delay(grid_series_id, handoff.map_number)`.
- Import line `:14`: drop `probe_grid_delay`.

### 3.6 `src/trader/discovery.py`

- Delete `from trader.grid_feed import read_board_sides` (`:27`); import `GridFeedSpec` from `trader.grid_feed_spec`.
- `_grid_source(sidecar, boards, series_id, feed: GridFeedSpec, aliases: Mapping[str, tuple[str, ...]])` (`:500-505`).
  Body: `read = feed.read_board_sides(board, sidecar.outcome_0_name, sidecar.outcome_1_name, aliases)` (`:513-520`).
- Caller `:587`: `_grid_source(sidecar, scan.boards, series_id, profile.feed, profile.aliases)`.
- `LolLeagueFilter` and `profile.game == "lol"` at `:192` stay (STEP-005).

### 3.7 Offline readers (constructor change only)

- `src/trader/grid_archive.py:39-44`: `profile = GAME_PROFILES[match_game(document)]`; `reducer = profile.feed.build_reducer(document["map_number"], market["outcome_0_name"], market["outcome_1_name"], profile.aliases)`.
- `src/archive_index/schedule.py:321-326`: the same with `profile = GAME_PROFILES[game]`; drop `GridFrameReducer` from the import at `:42-46`.
  Do not bump the schedule contract versions: the output does not change. The differential in §5 proves it.
- `src/viewer/game_state_replay.py:192`: `reducer = profile.feed.build_reducer(map_number, outcome_0, outcome_1, profile.aliases)`; drop `GridFrameReducer` from the import at `:21`.

### 3.8 Dashboard (`profile.feed` for side texts)

- `src/dashboard/game_grid.py`:
  - Import `BoardSides` from `trader.grid_feed_spec`. Drop `BoardSides` and `read_board_sides` from the `trader.grid_feed` import (`:17-22`).
  - `_lock_sides` (`:224-257`): `side_team(board, self._profile.feed.side_0_text)` / `side_1_text`;
    `read = self._profile.feed.read_board_sides(board, self._outcome_0_name, self._outcome_1_name, self._profile.aliases)`.
  - Labels `:346, :352`: `self._profile.feed.side_0_text.capitalize()` / `side_1_text`.
- `src/dashboard/game_state.py:425-426`: `inputs.profile.feed.side_0_text.capitalize()` / `side_1_text`.
- `src/dashboard/match_state.py:192-194`: read `profile.feed.side_0_text` / `side_1_text`.
- `GridFold` keeps its `profile: GameProfile` argument. No other dashboard change.

## 4. Tests

Update (constructor/field moves only, no assertion changes):

- `tests/test_grid_feed.py`: `:59-61` `_feed()`, `:205`, `:213`, `:243` → one local helper,
  for example `_reducer(outcome_0, outcome_1)` = `DOTA.feed.build_reducer(1, outcome_0, outcome_1, DOTA.aliases)` with `DOTA = GAME_PROFILES["dota"]`.
  `:66` and `:542` → `GridLiveFeed("2995964", DOTA.feed.services, MATCH_ID, <reducer>)`.
- `tests/test_lol_grid_feed.py:30-32` (`_lol_feed`) and `:138` (Dota feed on a LoL board) → `build_reducer`.
- `tests/test_match_meta.py:866` → `build_reducer`.
- `tests/test_trader_match_lifecycle.py:295` → `GridLiveFeed(LEC_SERIES_ID, LOL.feed.services, match_id, LOL.feed.build_reducer(2, "GIANTX", "Natus Vincere", LOL.aliases))`.
- `tests/test_trader_wallet_host.py`: the 8 strings `"trader.feed_selection.probe_grid_delay"` → `"trader.grid_feed.probe_grid_delay"`.
  `MobaGridFeed.probe_delay` looks the name up in `trader.grid_feed` at call time, so the patch takes effect.
- `tests/test_trader_game_profile.py`:
  - `:50-52, :67` (Dota) and `:76-78, :86` (LoL): assert on `profile.feed.level_xp`, `side_0_text`, `side_1_text`, `services`.
  - Add: `GAME_PROFILES["dota"].feed.top_builder is build_top_player_features` and
    `GAME_PROFILES["lol"].feed.top_builder is build_top_player_features_over_total`.
  - `:106-111`: also check that `profile.feed.__setattr__("side_0_text", "x")` raises `FrozenInstanceError`, and `type(profile.feed.level_xp) is tuple`.
  - `:119`: `dota.feed.level_xp is not lol.feed.level_xp`.

Add (small, one each):

- `tests/test_lol_grid_feed.py`: `test_feed_read_board_sides_uses_the_game_side_texts`.
  The CBLOL scoreboard (`read_map_scoreboard(json.dumps(CBLOL_SCOREBOARD), 1)`; the fixture is a `ScoreboardPayload` dict, `tests/lol_grid_widget_fixtures.py:158`)
  read by `GAME_PROFILES["lol"].feed.read_board_sides(...)` gives ready sides with Fluxo as radiant.
  The same board read by the Dota feed gives `pending=True` and `sides=None`. This pins the discovery path,
  which no longer goes through a free function.

No test for `probe_delay` alone: the 8 `select_feed` tests in `test_trader_wallet_host.py` call it through `profile.feed`.

## 5. Baseline and old-vs-new comparison (feature bullets 1 and 7)

Scratch tools from planning (tested on the baseline checkout):
`betting_workspace/tasks/cs2-live/orchestrate/work/plan-s3/grid_reducer_diff.py` and `.../work/plan-s3/sessions.txt`.
Copy both into `investigations/2026-10-23-cs2-live-validation/work/step-003/`.

The script hashes, for each frame: the reducer tick (`asdict` + class name), the reducer's locked `_sides`,
and the discovery side read on scoreboard frames. It also prints the per-game socket services.
It builds the reducer with `profile.feed.build_reducer` when `profile.feed` exists, else the old constructor.
The method names in §3 must match the script: `feed.build_reducer`, `feed.read_board_sides`, `feed.services`, `reducer._sides`.

Baseline set (finished sessions with `grid_state`, `core_trace`, `session_end terminal_reason=finished`,
newest first, at most 2 maps per series), from local `data/trader`:

| game | sessions | frames |
|---|---|---|
| dota | grid-3015051-m2, grid-3015043-m1, grid-3015051-m1, grid-3015042-m2, grid-3015050-m2, grid-3015050-m1 | 7 175 |
| lol | grid-3008335-m5, grid-3008335-m4, grid-3012947-m3, grid-3012947-m2, grid-3012946-m4, grid-3012946-m3 | 7 050 |

Plus 10 200 synthetic frames (the 2026-10-22 audit method: 50 seeds × 102 frames × 2 games on the committed fixtures).
Total 24 425 frames. Non-empty ticks at baseline: 3 324 Dota and 2 742 LoL on real sessions, 2 195 + 2 196 synthetic.
The first synthetic hash `74234e98…` matches `investigations/2026-10-22-vps-rollout-audit/work/orchestrator/grid-base.txt`.
Data is local up to 2026-10-22. No sync is needed. STEP-005 gates 2 and 3 can reuse this set.

Procedure:

1. Before any edit: `git -C <trader> rev-parse HEAD` and `git status --short`. Expect `6b108065…` and a clean tree.
   If HEAD moved, use the new HEAD as the baseline and re-run `pick_sessions.py` only if `data/trader` changed.
2. Base run (from the trader repo root):
   `PYTHONPATH=src:tests .venv/bin/python $W/grid_reducer_diff.py data/trader $W/sessions.txt > $W/grid-base.txt`
   with `W=../betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-003`. Takes about 11 s.
   If the edits already started: `git archive 6b108065 src tests | tar -x -C $W/base_snapshot` and run with
   `PYTHONPATH=$W/base_snapshot/src:$W/base_snapshot/tests`. Do not create a worktree.
3. After the change: the same command into `$W/grid-head.txt`.
4. Write `$W/grid-reducer-diff.json` with `base_rev`, `head_rev`, line counts per prefix (`services`, `synthetic dota/lol`,
   `session dota/lol`), `different_lines`, and the first 5 differing pairs. Expected: `different_lines == 0`, 24 427 lines each.
   Any difference is a failure of the step, not a note.

## 6. Verification

From the trader repo root:

1. `.venv/bin/ruff check src tests scripts` and `.venv/bin/ruff format --check src tests scripts` → clean.
2. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`.
3. Targeted pytest (baseline 512 passed for the first 13 files):
   ```
   PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 \
     tests/test_grid_feed.py tests/test_lol_grid_feed.py tests/test_trader_game_profile.py \
     tests/test_trader_wallet_host.py tests/test_match_meta.py tests/test_trader_match_lifecycle.py \
     tests/test_source_picker.py tests/test_archive_index.py tests/test_dashboard_game_state.py \
     tests/test_discovery_cadence.py tests/test_trader_discovery.py tests/test_trader_lol_discovery.py \
     tests/test_dashboard_match.py tests/test_live_inspect.py tests/test_backtest.py tests/test_trader_session_journal.py
   ```
4. Full suite: `make test` (or `PYTEST_N=8 make test` on this Mac only). Record pass/fail counts.
5. GRID differential: §5 → `different_lines == 0`.
6. Import smoke: `PYTHONPATH=src .venv/bin/python -c "import trader.game_profile, trader.grid_feed, trader.grid_feed_spec, trader.discovery, trader.feed_selection, dashboard.game_state"`.
7. Browser check with the `agent-browser` skill (feature bullet "Verify in browser"). Do not write into the trader repo:
   - Base page: `git archive 6b108065 src config compose.yaml | tar -x -C $W/dash-base`. Head page: after the commit,
     `git archive HEAD src config compose.yaml | tar -x -C $W/dash-head`. Run streamlit from the trader repo root, so it reads the repo `.streamlit/` config.
   - In both: `mkdir -p <snap>/data/trader_live` and `cp -R data/trader/grid-3015051-m2 data/trader/grid-3008335-m5 <snap>/data/trader_live/`.
     The dashboard root is `Path(__file__).parents[2]` (`src/dashboard/summarize.py:32`), so each snapshot reads its own copy.
   - Run from the trader repo root:
     `PYTHONPATH=$W/dash-base/src .venv/bin/python -m streamlit run $W/dash-base/src/dashboard/app.py --server.port 8601 --server.headless true`
     and the same for `dash-head` on port 8602. Keep the PIDs; stop only those two processes at the end.
   - Open `http://localhost:860{1,2}/?match=grid-3015051-m2` (Dota) and `?match=grid-3008335-m5` (LoL), and the home page.
     Expect the same text on base and head: side labels `Radiant`/`Dire` and `Blue`/`Red`, team names, the GRID player table.
     Save screenshots to `$W/browser/`.
   - Without `data/trader_live/wallet/live.db` or Docker, a panel can show an error. If base and head show the same error,
     record it as an environment limit. If the match card does not render on base either, record that and the exact message.
8. `git diff --stat` touches only the files in §3 and §4.

Write the results (revisions, the session list, frame counts, differential result, pytest counts, basedpyright and ruff output,
browser findings, total time) into `report.md` of the evidence folder under a `STEP-003` heading.

## 7. Commit

One commit in `esports-trader` on `main`, for example:
`refactor(trader): move MOBA GRID feed fields into MobaGridFeed behind GridFeedSpec`.
No push. `progress.txt` gets the usual STEP-003 entry from the implement skill.

## 8. Out of scope (later steps own these)

- `StrategySpec`, catalogs, `LIVE_MAX_POSITION_LEVELS`, `QuoteSides` in `LiveCore`, `BUY_CUTOFF_SECOND_BY_GAME`,
  `LeagueFilter` rename (STEP-005). `Follow300Signals` (STEP-004). Any CS2 type, reducer or tick (STEP-007).
- Widening `GameProfile.feed` to `GridFeedSpec` (STEP-009, see §2.1).
- The LoL `winner=None` quirk in `grid_archive._winner_from_grid_board`. Fixing it changes archive output.
- New comments. The review config forbids narrating comments in new code. Docstrings follow the file's existing style.

## 9. Risks and hard parts

- **Import cycle.** If `grid_feed.py` keeps `from trader.game_profile import GameProfile`, the import fails at load.
  The import smoke in §6.6 catches it.
- **Monkeypatch target.** `feed_selection` no longer has `probe_grid_delay`. The 8 old patch strings then raise
  `AttributeError` (pytest `raising=True` default). They must point at `trader.grid_feed.probe_grid_delay`.
- **Protocol attribute writability.** If `GridFeedSpec.services` is a plain attribute, basedpyright rejects the frozen
  `MobaGridFeed` at the `discovery._grid_source` call site. Use `@property`.
- **Callable field binding.** `top_builder` is an instance field of a dataclass, so Python does not bind it as a method.
  `self.top_builder(r, d)` calls the plain function. Do not turn it into a class attribute.
- **Feed build time.** `GridLiveFeed` used to build the reducer inside `__init__`. `feed_selection` now builds it just before.
  Both happen in the same call, and `GridFrameReducer.__init__` does not raise, so the timing does not change.
- **STEP-009 cost.** Widening `feed` later touches `GridFold`, the label helpers, `grid_archive.summarize` and `schedule._extract_grid`.
  CS2 `summarize` needs `board` and a horn anyway. STEP-009's plan must cover this list.
- **Browser check data.** Local `data/trader_live` does not exist; the snapshot copies are the only safe local source.
  The home page tries Docker logs and the Polymarket websocket. Those panels can fail locally on base and head alike.
