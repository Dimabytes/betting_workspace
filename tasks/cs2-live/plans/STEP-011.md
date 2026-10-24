# STEP-011 plan: dashboard CS2 label, slate, side labels and `summarize.py --game cs2`

Repo: `../esports-trader` at `f2164b98` (worktree clean). Planner: plan-s11.
Scratch (measurements on unchanged code only): `tasks/cs2-live/orchestrate/work/plan-s11/`.

The step makes the existing dashboard show CS2 without a crash and without Dota words. It adds the `CS2` label, the `cs2` choice of `summarize.py --game`, side labels from the feed contract, and a plain note in place of the MOBA player table. The CS2 slate and CS2 PnL need no new code; tests pin them. No deploy, no push, no change to Dota/LoL numbers.

## 1. Facts this plan rests on

### 1.1 Code

- `_GAME_LABEL = {"dota": "Dota", "lol": "LoL"}` and `_game_label(game)` (`src/dashboard/home_view.py:401-407`). Two users: `closed_records` (:333) and `upcoming_records` (:415). The "Trading now" table has no game column (:284-297).
- `summarize.py --game` has `choices=("dota", "lol")` (`src/dashboard/summarize.py:1712`). `main()` reads `sys.argv` (:1701-1749, `parse_args()` at :1728). `cmd_list(today, live_only, game)` filters with `game_from_meta(meta) != game` (:532-574) and calls `print_polymarket_today(day)` when `today` (:573-574). `summarize.py` imports only stdlib and light project modules (:1-30). The VPS runs it with host `python3`, so it must not import `trader.game_profile` (that pulls lightgbm, progress STEP-005).
- Four dashboard sites assert the MOBA feed type and crash on CS2:
  - `GridFold._lock_sides` (`src/dashboard/game_grid.py:221-253`): `assert isinstance(feed, MobaGridFeed)` (:225), then `side_team(board, feed.side_0_text)` (:226-227).
  - `GridFold._on_table` (:299-358): `assert isinstance(feed, MobaGridFeed)` (:333-334); table side labels from `feed.side_0_text.capitalize()` (:344, :350).
  - `game_state._identity` (`src/dashboard/game_state.py:411-435`): assert (:419-420); `side_0_label=feed.side_0_text.capitalize()` (:428-429).
  - `match_state._side_labels` (`src/dashboard/match_state.py:193-197`): assert (:196); its only caller is `resolve_sides` (:205-214) when there is no `GameIdentity`.
- `GridFeedSpec` (`src/trader/grid_feed_spec.py:46-79`) has `services`, `stale_seconds`, `read_board_sides`, `probe_delay`, `build_reducer`. Two implementations only: `MobaGridFeed` (`src/trader/grid_feed.py:376-413`) and `Cs2GridFeed` (`src/trader/cs2_feed.py:94-130`). `grep` finds no other implementer in `src` or `tests`.
- How each feed pairs the board teams:
  - `MobaGridFeed.read_board_sides` (:388-399): `side_team(board, self.side_0_text)` and `side_1_text`; a missing side is `pending`; then `orient_side_pair` (:354-373).
  - `Cs2GridFeed.read_board_sides` (:103-118): `pending` unless `len(board.teams) == 2`; then `orient_side_pair(board.teams[0], board.teams[1], …)`. CT/T swaps at the half, so CS2 has no fixed side text.
  - Callers of `read_board_sides`: `GridFrameReducer._lock_sides` (`grid_feed.py:205`), `Cs2FrameReducer._lock` (`cs2_feed.py:207`), `discovery._grid_source` (`src/trader/discovery.py:518`), `GridFold._lock_sides` (`game_grid.py:241`).
- `GameSummary` (`src/dashboard/game_types.py:183-191`) is built by `build_game_summary` (`game_state.py:438-466`, one call at :779) and directly in two tests (`tests/test_dashboard_match.py:949`, `:1044`).
- `render_players(summary)` prints `players: no data` when the table is `None` (`src/dashboard/match_view.py:469-479`).
- Slate: `live_hub._sidecar_roots` loops over `GAME_PROFILES` (`src/dashboard/live_hub.py:140-148`). `GAME_PROFILES["cs2"]` exists since STEP-009 (`src/trader/game_profile.py:111-125`), so `CS2_ARCHIVE_ROOT` is already read. `logs.list_nontrading` keeps open `map_winner` sidecars per game root (`src/dashboard/logs.py:239-286`). `home_lists._upcoming_rows` groups them per series (`src/dashboard/home_lists.py:484-494`). `compose.yaml:95,101` already gives the dashboard `CS2_ARCHIVE_ROOT` and the read-only mount.
- PnL: `home_lists._place` puts any game other than dota/lol into `other` (:523-530), and `_history_row.pnl` sums all three buckets (:533-546). `pnl_chart._game_buckets` maps unknown games to `Other` (`src/dashboard/pnl_chart.py:295-309`). A history map needs `fill_count >= 1` (`home_lists.py:561`).
- Closed rows exist only after a known rebate payout (`home_lists.py:590-591`, `:815`). Without a wallet and activity, the home shows `payout not found`.
- Test fixtures that exist: `dashboard_fixture.build/enable_balance/write_match/write_journal/wait_for` (`tests/dashboard_fixture.py:145-424`); `write_match` writes `"game": "dota"` with no parameter (:145-181). `dashboard_game_fixtures.write_meta(game=…)`, `make_entry(game=…)`, `grid_record`, `signal_record`, `write_lines` (`tests/dashboard_game_fixtures.py`). CS2 frames: `cs2_scoreboard`, `cs2_table`, `MARKET_A/B`, `GRID_A/B`, `DAMAJUANA`, `HACHA`, stamps (`tests/cs2_grid_widget_fixtures.py`). `wrap(service, delay, payload)` (`tests/grid_widget_fixtures.py:173`). AppTest pattern: `tests/test_dashboard_app.py:19-56`.
- File sizes: `test_dashboard_home.py` 1153, `test_dashboard_match.py` 1162, `test_dashboard_game_state.py` 1219, `test_trader_game_profile.py` 212, `test_cs2_feed.py` 763 lines.

### 1.2 Measurements on unchanged code (`work/plan-s11/`)

- `baseline.txt` (`baseline.py`, run with `PYTHONPATH=src:tests`):
  - `home_view._game_label('cs2')` → `'cs2'`.
  - `match_state._side_labels('cs2')` → `AssertionError`. Dota `('Radiant', 'Dire')`, LoL `('Blue', 'Red')`.
  - `GameStateReader.read(<cs2 archive>)` → `AssertionError`. The CS2 map card crashes today.
  - CS2 board through `read_map_scoreboard`: teams `('56753', 'Damajuana', 'T', 0, 1)` and `('59048', 'HACHA', 'CT', 0, 1)`. `kills` is the round score.
  - `read_net_worth(<cs2 table>)` → `None`. Once the asserts are gone, every CS2 table frame would be a reject with the note `table without players`.
  - `summarize.py --today --game cs2` → exit 2, `invalid choice: 'cs2' (choose from dota, lol)`.
- `tape-frame-sizes.txt` (tape `data/cs2/live/cs2-damaju-hachae-2026-10-21/grid.jsonl`): scoreboard lines median 11464 B, table lines median 132784 B. The dashboard tail is 256 KB (`src/dashboard/tails.py:12`), so it holds about one table and one board. The longest gap between two scoreboards is 1882225 B.
- `complexity.txt` (ruff C901 at 5): `GridFold._lock_sides` 6, `GridFold._on_table` 8, `GameStateReader._read` 6, `resolve_sides` 6. The repo limit is the ruff default 10.
- `baseline-targeted.txt`: dashboard, profile and feed tests, 205 passed in 4.22 s.
- Gate baselines at `f2164b98`: `work/impl-s10/grid-head.txt` 24427 lines, sha256 `df2fb0a83bf12b0b629fcbd582f7c0162aacffd134003975383506c315784c5e`; `work/impl-s10/signals-head.txt` 27514 lines, sha256 `7b70dde69205f7823f4e2509ce33806dfe46ff162f14921873b2d4e16d3c445e`.
- Ports: 8601 (pid 4035) and 8501 (pid 14763) are taken by other apps. Use 8611 and 8612. The shell exports `PYTEST_N=10`.

## 2. Design decisions

D1. **The feed contract gets side labels and the board pair.** Two read-only properties `side_0_label` and `side_1_label`, and one method `read_board_pair(board) -> BoardPair | None`. `BoardPair` is a frozen dataclass with `side_0: TeamSide` and `side_1: TeamSide`, next to `BoardSides` in `grid_feed_spec.py`. Each feed already pairs the teams inside `read_board_sides`; the method names that rule once. Both `read_board_sides` methods then call it. The dashboard asks the feed and holds no CS2 rule of its own. The alternative, an `isinstance` branch in `GridFold` with `board.teams[0]`/`[1]`, copies the CS2 pairing rule into the dashboard.

D2. **CS2 labels are `Team A` and `Team B`.** Side 0 is `board.teams[0]`, the trader's team a (`cs2_feed.py:103-118`). CT/T cannot be a label because it swaps at the half. Book panels read `YES · Damajuana · Team A`. Dota stays `Radiant`/`Dire`, LoL `Blue`/`Red` (`side_N_text.capitalize()`).

D3. **The MOBA table stays MOBA-only.** `GridFold._on_table` returns early when the feed is not `MobaGridFeed`. CS2 table frames are then not rejects and add no control note. The board still folds, so the header shows the round score and the series score.

D4. **The note is data, not a view rule.** `GameSummary` gets `players_note: str | None`. `game_state._resolve_inputs` sets it from the profile: `None` for `MobaGridFeed`, else `f"players: {profile.game} player table not supported"` → `players: cs2 player table not supported`. `render_players` prints the note and returns. The view has no game branch.

D5. **No code for the slate and PnL.** Both already work through `GAME_PROFILES["cs2"]` and the `other` bucket. Tests pin them. The label table gets one key.

D6. **`summarize.py` keeps a literal tuple** `("dota", "lol", "cs2")`. Reading `GAME_PROFILES` there would import lightgbm on the host.

D7. **New tests go into a new file** `tests/test_dashboard_cs2.py`. The three dashboard test files are already over 1000 lines. They get only the edits the new field forces, plus one or two asserts.

## 3. Code changes (`../esports-trader`)

No new comments or docstrings. Follow the file style: the new protocol members carry no docstring, like `stale_seconds` (`grid_feed_spec.py:54-55`).

### 3.1 `src/trader/grid_feed_spec.py`

- Import `TeamSide` from `trader.grid_widgets` (the module already imports `Frame`, `Scoreboard` from there).
- Add `@dataclass(frozen=True) class BoardPair` with `side_0: TeamSide`, `side_1: TeamSide`.
- In `GridFeedSpec` add `@property side_0_label -> str`, `@property side_1_label -> str`, and `read_board_pair(self, board: Scoreboard) -> BoardPair | None`. Use `@property` in the protocol: a plain attribute rejects the frozen `MobaGridFeed` field (progress STEP-003).

### 3.2 `src/trader/grid_feed.py` (`MobaGridFeed`, :376-413)

- `side_0_label` / `side_1_label` properties return `self.side_0_text.capitalize()` / `self.side_1_text.capitalize()`.
- `read_board_pair(board)`: the two `side_team` calls that `read_board_sides` makes now; `None` when either is `None`; else `BoardPair(side_0, side_1)`.
- `read_board_sides`: `pair = self.read_board_pair(board)`; `None` → `BoardSideRead(sides=None, pending=True)`; else `orient_side_pair(pair.side_0, pair.side_1, …)`. Same result as today. This is the Dota/LoL live path, so the GRID and signal differentials must stay byte-equal (§5).

### 3.3 `src/trader/cs2_feed.py` (`Cs2GridFeed`, :94-130)

- `side_0_label` returns `"Team A"`, `side_1_label` returns `"Team B"`.
- `read_board_pair(board)`: `None` unless `len(board.teams) == 2`; else `BoardPair(board.teams[0], board.teams[1])`.
- `read_board_sides` uses `read_board_pair` the same way as §3.2.

### 3.4 `src/dashboard/game_grid.py`

- `_lock_sides` (:221-253): replace the assert and the two `side_team` calls with `pair = self._profile.feed.read_board_pair(board)`; `None` → return. Build the pre-lock `BoardSides` and `team_0_name`/`team_1_name` from `pair.side_0` / `pair.side_1`. The rest (outcome names, `read_board_sides`, `outcome orientation unresolved` note) stays.
- `_on_table` (:299-358): first lines `feed = self._profile.feed`; `if not isinstance(feed, MobaGridFeed): return`. Delete the assert at :333-334. The table side labels use `feed.side_0_label` / `feed.side_1_label`. Complexity goes from 8 to 9 (limit 10).
- Drop `side_team` from the `trader.grid_widgets` import. `MobaGridFeed` stays imported.

### 3.5 `src/dashboard/game_types.py`

- `GameSummary` (:183-191): add `players_note: str | None` after `table`.

### 3.6 `src/dashboard/game_state.py`

- `_Inputs` (:77-84): add `players_note: str | None`.
- `_resolve_inputs` (:348-368): `profile = _profile(game)`; `players_note = None`; if `profile.feed` is not a `MobaGridFeed`, set the text from D4. Pass `profile=profile` and `players_note=players_note`.
- `_identity` (:411-435): delete the assert; `side_0_label=feed.side_0_label`, `side_1_label=feed.side_1_label`.
- `build_game_summary` (:438-466): new required keyword `players_note: str | None`, passed to `GameSummary`. The call at :779 passes `players_note=inputs.players_note`.
- The `MobaGridFeed` import stays (used by `_resolve_inputs`).

### 3.7 `src/dashboard/match_state.py`

- Delete `_side_labels` (:193-197) and the `MobaGridFeed` import (:29).
- In `resolve_sides`, no-identity branch (:212-215, today `_side_labels(entry.game)` at :213): `profile = GAME_PROFILES.get(entry.game or "dota", GAME_PROFILES["dota"])`, then `side_0_label = profile.feed.side_0_label`, `side_1_label = profile.feed.side_1_label`.

### 3.8 `src/dashboard/match_view.py`

- `render_players` (:469-479): if `summary` is not `None` and `summary.players_note` is not `None`, `st.caption(summary.players_note)` and return. The rest stays.

### 3.9 `src/dashboard/home_view.py`

- `_GAME_LABEL` (:401): add `"cs2": "CS2"`.

### 3.10 `src/dashboard/summarize.py`

- :1712 `choices=("dota", "lol", "cs2")`. No other change.

### 3.11 Expected diff check

`grep -rn "side_0_text\|side_1_text\|assert isinstance(feed, MobaGridFeed)" src/dashboard` is empty after the change. `side_N_text` stays in `grid_feed.py`, `game_profile.py` and the profile tests.

## 4. Tests

Run style: `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q …`. Set `TG_BOT_API_TOKEN=disabled TG_CHAT_ID=disabled` as the STEP-010 package did.

### 4.1 Edits the new contract and field force

- `tests/test_dashboard_match.py:949` and `:1044`: add `players_note=None` to both `GameSummary(...)` calls.
- `tests/test_trader_game_profile.py`: in the Dota, LoL and CS2 profile tests (:71, :105, :132) assert `side_0_label`/`side_1_label` = `Radiant`/`Dire`, `Blue`/`Red`, `Team A`/`Team B`.
- `tests/test_cs2_feed.py::test_cs2_grid_feed_satisfies_the_spec` (:746): `feed.read_board_pair(<upcoming board>)` is `None`; on a live board (`cs2_scoreboard("live", 115, "white", True, R1_LIVE, 0, 0, "T", "CT", "nuke")`) the pair ids are `DAMAJUANA`, `HACHA`; the labels are `Team A`/`Team B`.
- `tests/test_dashboard_game_state.py`: in `test_grid_dota_aligned_decision_board_table` (:216) and `test_lol_grid_map2_blue_red_uuid_heroes` (:496) assert `summary.players_note is None` and the identity labels (`Radiant`/`Dire`, `Blue`/`Red`).
- `tests/dashboard_fixture.py::write_match` (:145): add `game: str = "dota"` (the function already uses defaulted keywords). Write `"game": game` and `"market_slug": f"{game}-map-{match_id}"`. Existing callers keep Dota.

### 4.2 New `tests/test_dashboard_cs2.py`

Constants: `CS2_MATCH = "grid-3009765-m3"`. Frames: `wrap(SCOREBOARD_SERVICE, 0, cs2_scoreboard(...))` and `wrap(TABLE_SERVICE, 0, cs2_table(3, ...))`.

1. `test_cs2_game_summary_uses_feed_labels_and_a_players_note(tmp_path)`. Archive by `dashboard_game_fixtures.write_meta(archive, match_id=CS2_MATCH, game="cs2", map_number=3, radiant=MARKET_A, dire=MARKET_B)`. `grid_state.jsonl` holds: a live board at 0:0 (`R1_LIVE`), one map-3 table, a board at 1:0 after `R1_END`. Journal: one `signal_record` with zero MOBA fields. `GameStateReader(TailCache()).read(make_entry(..., game="cs2", map_number=3, radiant=MARKET_A, dire=MARKET_B), now)`:
   - `identity.side_0_label == "Team A"`, `side_1_label == "Team B"`.
   - `board.kills_0 == 1`, `board.kills_1 == 0`; `board.series.name_0 == GRID_A`.
   - `table is None`, `source.rejected == 0`, `source.control == ()`.
   - `players_note == "players: cs2 player table not supported"`.
   - No evidence line `board orientation conflicts saved yes_is_radiant`.
   On `f2164b98` this test raises `AssertionError` (baseline).
2. `test_cs2_sides_without_a_summary_use_feed_labels(tmp_path)`: `resolve_sides(make_entry(..., game="cs2"), None, None)` → `yes.side_label == "Team A"`, `no.side_label == "Team B"`, `yes.team == MARKET_A`.
3. Fixture `cs2_hub(tmp_path) -> Generator[LiveHub]`:
   - `fixture = fx.enable_balance(fx.build(tmp_path))`.
   - CS2 sidecar root `tmp_path / "sidecars" / "cs2"`: two open `map_winner` sidecars of one event (`eventId` `ev-cs2`, `eventSlug` `cs2-g2-navi-2026-10-24`, `mapNumber` 1 and 2, outcomes `G2`/`NaVi`, file name `<conditionId>.json`, the field set of `tests/test_dashboard.py:906-923`). `metadata/events/ev-cs2.json` gives both `sports.gameStartTime` at a time later today in Berlin (copy the clamp of `tests/test_dashboard_home.py:847-861` into a local helper).
   - CS2 closed map: `fx.write_match(fixture.live_root / CS2_MATCH, CS2_MATCH, cid=…, yes=…, no=…, teams=(MARKET_A, MARKET_B), map_number=3, final={"winner": "radiant"}, game="cs2")`. Journal: `session_start`, one signal with `feed_source` `grid`, one `fill` (`price` 0.5, `size` 10, `is_maker` false), `session_end` with `net_cash` -2.5, `inventory_value` 0, `positions` {}. `grid_state.jsonl` as in test 1.
   - `config = replace(fixture.config, sidecar_roots={**fixture.config.sidecar_roots, "cs2": cs2_root})`; `hub = LiveHub(config).start()`.
   - `fx.wait_for` the CS2 map in `snap.maps` and a `cs2` market in `snap.nontrading.markets`. Teardown: `hub.close()`, `live_hub_module.close_live_hub()`.
4. `test_home_lists_cs2_slate_and_total_pnl(cs2_hub)`: `lists = build_lists(hub.get_snapshot(), time.time())`.
   - `[(row.game, row.maps) for row in lists.upcoming] == [("cs2", 2)]` (the fixture's Dota sidecar is closed).
   - `upcoming_records(lists.upcoming)[0]["game"] == "CS2"`. This is the public path of `_game_label("cs2") == "CS2"` and needs no `reportPrivateUsage` exemption.
   - Today's history row: `pnl == -2.5`, `pnl_dota == 0.0`, `pnl_lol == 0.0`, `maps == 1`. The Dota fixture maps have no fill and stay out.
   - `build_map_stats(lists.map_results).net == -2.5`; `collect_pnl_days(lists.history, today=berlin_day(now))[-1].pnl == -2.5`.
5. `test_app_renders_cs2_home_and_map_card(monkeypatch, cs2_hub)`: patch as `tests/test_dashboard_app.py:37-40` (`get_live_hub`, `render_fresh`, `st.cache_resource.clear()`).
   - Home run: no `at.exception`; the HTML tables contain `>CS2<`.
   - `at.query_params["match"] = CS2_MATCH`; run: no `at.exception`; a caption equals `players: cs2 player table not supported`; a markdown equals `**book · YES · Damajuana · Team A**`; the joined markdown, caption and HTML text of the page has no `Radiant` and no `Dire`.
6. `test_summarize_today_lists_only_cs2(tmp_path, monkeypatch, capsys)`: one Dota and one CS2 archive by `fx.write_match` (`joined_at_utc` is now) with `session_start` and `session_end`. Patch `summarize.match_dirs` to return both as `("live", path)`, patch `summarize.print_polymarket_today` to a no-op, and set `sys.argv` to `["summarize.py", "--today", "--game", "cs2"]`. `summarize.main()` prints one row with `game=cs2` and no `game=dota`, then a `berlin_day=` line with `finished=1`.

## 5. Verification

Run from `../esports-trader`. Logs to `tasks/cs2-live/orchestrate/work/impl-s11/`. Evidence to `investigations/2026-10-23-cs2-live-validation/work/step-011/`. Do not run two `-n` suites at once. `PYTEST_N=10` is exported in the shell; pass `-n` explicitly or unset it.

1. `.venv/bin/ruff check src tests scripts`
2. `.venv/bin/ruff format --check src tests scripts`
3. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`
4. Targeted: `pytest -n 8` on `tests/test_dashboard_cs2.py tests/test_dashboard_game_state.py tests/test_dashboard_match.py tests/test_dashboard_home.py tests/test_dashboard_app.py tests/test_dashboard.py tests/test_dashboard_live_hub.py tests/test_trader_game_profile.py tests/test_cs2_feed.py tests/test_grid_feed.py tests/test_lol_grid_feed.py tests/test_trader_discovery.py tests/test_trader_lol_discovery.py tests/test_trader_cs2_discovery.py tests/test_source_picker.py tests/test_live_inspect.py`. Test 1 of §4.2 fails on `f2164b98` and passes on the head.
5. Full: `PYTEST_N=8 make test`. Last count 3731 passed (`f2164b98`). Known flakes: `test_upcoming_rows_from_sidecars` fails 23:00–00:00 Berlin; `test_follow300_replay` `dota_9007700576` under xdist (rerun alone, report both).
6. Dota/LoL gates (§3.2 changes the live `read_board_sides`):
   - GRID: `PYTHONPATH=src:tests .venv/bin/python ../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/grid_reducer_diff.py data/trader ../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s5/sessions.txt > <impl-s11>/grid-head.txt`; `cmp` with `work/impl-s10/grid-head.txt`; 24427 lines, sha256 `df2fb0a8…784c5e`.
   - Signals: the same with `work/plan-s5/signals_diff.py`; `cmp` with `work/impl-s10/signals-head.txt`; 27514 lines, sha256 `7b70dde6…c3445e`.
   - Core replay and model smoke: not needed. The step touches no `session_core`, strategy or model file. Say so in the report.
7. Imports: `PYTHONPATH=src .venv/bin/python -c "import dashboard.game_state, dashboard.game_grid, dashboard.match_state, dashboard.match_view, dashboard.home_view, trader.game_profile"`.
8. CLI: `PYTHONPATH=src .venv/bin/python src/dashboard/summarize.py --self-check` → `self-check ok`; `--help` lists `{dota,lol,cs2}`.
9. `grep` from §3.11 is empty.
10. Browser check with the `agent-browser` skill (feature bullet "Verify in browser"). `W = investigations/2026-10-23-cs2-live-validation/work/step-011`. Do not write into the trader repo.
    - Snapshots: `git archive f2164b98 src config compose.yaml | tar -x -C $W/dash-base`; after the commit `git archive HEAD src config compose.yaml | tar -x -C $W/dash-head`.
    - Data in both snapshots, `data/trader_live/`:
      - `cp -R data/trader/grid-3015051-m2 data/trader/grid-3008335-m5` (Dota and LoL, as STEP-003).
      - A live CS2 map `grid-3009765-m3`, built by a script `$W/make_cs2_fixture.py` that imports the test fixture modules: `match.json` (game `cs2`, map 3, teams Damajuana / Hacha Esports, `feed_source` `grid`, tokens, `yes_is_radiant` true, `joined_at_utc` now); `session.jsonl` with `session_start` and CS2-shaped signals (zero MOBA fields); `grid_state.jsonl` from the tape frame lines (no `marker` lines) received 21:40:00Z–21:42:30Z (R13 freeze and bomb); `core_trace.jsonl` header with `buy_cutoff_second` 1000000000, as live CS2 writes it.
      - A closed CS2 map `grid-3009765-m2` with one fill and `session_end`, for the daily history total.
      - Sidecar roots `$W/archive/{dota,lol,cs2}/metadata/markets` (Dota and LoL empty). CS2 gets two open `map_winner` sidecars of one event and its `metadata/events/<id>.json` with a start later today.
    - Run from the trader repo root, env `DOTA_ARCHIVE_ROOT=$W/archive/dota LOL_ARCHIVE_ROOT=$W/archive/lol CS2_ARCHIVE_ROOT=$W/archive/cs2`: `PYTHONPATH=$W/dash-base/src .venv/bin/python -m streamlit run $W/dash-base/src/dashboard/app.py --server.port 8611 --server.headless true`, and `dash-head` on 8612. Keep the two PIDs. Stop only those at the end. Rewrite the CS2 fixture just before the check: a map with writes older than 900 s leaves "Trading now" (`STALE_WRITE_SECONDS`, `src/dashboard/catalog.py:22`).
    - Pages: home, `?match=grid-3015051-m2`, `?match=grid-3008335-m5`, `?match=grid-3009765-m3`. Reload once after the catalog builds (the match page shows `catalog still loading…` first, progress STEP-003).
    - Expect on head: "Upcoming today" row with game `CS2` and 2 maps (base: `cs2`); "Trading now" row `Damajuana vs Hacha Esports · map 3`; daily history total includes the closed CS2 map; the CS2 card renders with `Team A`/`Team B`, the round and series score, `players: cs2 player table not supported`, and no `Radiant`/`Dire`. Base: the CS2 card shows the `AssertionError`.
    - Dota and LoL cards: inner text byte-identical between base and head (Radiant/Dire, Blue/Red, team names, player tables).
    - `cd $W/dash-head && /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python src/dashboard/summarize.py --today --game cs2` lists only the two CS2 maps (the script puts its own `src` on `sys.path`, `summarize.py:25`). Without a wallet it prints `polymarket_today n/a  no funder in live.db`.
    - Save screenshots and text to `$W/browser/`. Record environment limits the same on both trees (wallet read error, `payout not found` for closed rows).
11. Append `## STEP-011` to `investigations/2026-10-23-cs2-live-validation/report.md` (append only): revisions, the baseline crash, every command with its result, the browser table, the known items from §6. Then `progress.txt` and one commit.

Data: all local. Tape `data/cs2/live/cs2-damaju-hachae-2026-10-21/grid.jsonl`, `data/trader/grid-3015051-m2`, `data/trader/grid-3008335-m5`, the 13 sessions in `work/plan-s5/sessions.txt`, the gate scripts and baselines under `tasks/cs2-live/orchestrate/work/`. No VPS, no `sync_trader.py`.

## 6. Edge cases, risks, hand-offs

- **CS2 header shows MOBA zeros.** The CS2 journal snapshot has `radiant_nw_adv = 0` and `radiant_xp_adv = 0` (`cs2_feed.py:598-628`). `_summary_html` prints `NW +0 · XP +0` (`match_view.py:447-466`). These are not side labels and the step does not list them. Record them in the report. Hiding them is a small follow-up if the user wants it.
- **CS2 live row shows `entry until 1e+09s`.** `_row_window` formats the trace `buy_cutoff_second` with `:g` (`home_lists.py:346-353`). CS2 writes `10**9` (`src/shared/constants/strategy.py:7`). Out of scope. Record it.
- **`summarize.py --restart-check` is wrong for CS2.** `restart_block` uses `BUY_CUTOFF_SECOND` (480) for every game (`summarize.py:442-447`). An open CS2 map without a position at second ≥ 480 reads SAFE. A restart then rejoins late, and late join bans BUY for the rest of the map (`cs2_feed.py:236-238`). STEP-012 runs `--restart-check` before the rollout. Hand this to the orchestrator for STEP-012.
- **CS2 board in a cold tail.** A 256 KB tail holds about one CS2 table and one board. A cold read inside a long board gap (up to 1.88 MB on the tape) shows no score until the next board. `_deepen` (`game_state.py:667-702`) looks for a table, so for CS2 it runs once per map key (≤ 8 MB) and never adopts. Bounded cost; no wrong data.
- **Closed CS2 rows need a payout.** Locally the home shows `payout not found`. Tests cover the label through `upcoming_records`; `closed_records` uses the same `_game_label`.
- **Label names.** `Team A`/`Team B` is a planner choice (D2). A rename is two strings in `cs2_feed.py` plus the tests.
- **Berlin midnight.** The slate test needs a start later today; it inherits the last-minute edge of the existing `_later_today` helper.
- **Shared trader file.** `read_board_sides` in `grid_feed.py` is on the Dota/LoL live path. §5 item 6 must show both differentials byte-equal before the commit.
- Optional, outside this repo: `.shared-skills/vps-trader/SKILL.md:52-54` lists `--game dota` and `--game lol`. A `--game cs2` line fits STEP-012.

## 7. Out of scope

- A CS2 PnL column, a new PnL bucket, a round screen (Resolved Questions).
- Restart-check, the cutoff window text, NW/XP in the CS2 header (§6).
- VPS rollout and `docker compose restart dashboard` (STEP-012).
- No change to `../poly-maker`, models, backtest, or Dota/LoL constants.

## 8. Commit

One commit in `../esports-trader` on `main`, for example `feat(dashboard): show CS2 label, slate and summarize --game cs2`. No push.
