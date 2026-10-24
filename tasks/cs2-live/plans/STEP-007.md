# STEP-007 plan: CS2 GRID parser and round reducer

Step: `STEP-007` of `tasks/cs2-live/feature.json` (priority 7, `passes: false`).
Repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`, branch `main`, clean, HEAD `a2616d45`.
Evidence folder: `betting_workspace/investigations/2026-10-23-cs2-live-validation/` (`report.md` + `work/step-007/`).
`report.md` exists. Append a `## STEP-007` section. Never overwrite the file.
No Figma or design links exist for this step.

The step adds a typed CS2 `series_table` parser, `Cs2GridFeed`, and `Cs2FrameReducer`.
The reducer turns widget frames into `FeedEvent`s plus three round ticks: live start, decision due, and history lost.
No CS2 profile, no feature builder, no `RoundSignals`. `GAME_PROFILES` stays `{dota, lol}`. STEP-008 and STEP-009 consume what this step defines.
Dota/LoL behavior must not change. Shared files change only by wider tick types, two new `Scoreboard` fields, one extracted helper, one new probe function, and one worker filter.

Do not deploy, do not `git push`, do not run `scripts/sync_trader.py`, do not touch STEP-008+.
Planning measured only the unchanged code and the recorded tape. Scratch output: `betting_workspace/tasks/cs2-live/orchestrate/work/plan-s7/`.

## 1. Facts this plan rests on

Line numbers are at `a2616d45`.

### 1.1 The recorded tape

`data/cs2/live/cs2-damaju-hachae-2026-10-21/grid.jsonl` is local: 1795 lines, series `3009765`, map 3 is Nuke.
Each line is `{"received_at_utc", "frame"}` or a recorder marker `{"received_at_utc", "marker", "stream"}`.
Services: scoreboard 784 frames (delay 0), table 777 frames (delay 8), map schematic 78 frames with no data.

Scoreboard (`work/plan-s7/board-timeline.txt`, `board-rounds-map3.txt`):

- `games` has 3 slots from the first frame. Slot 3 is `upcoming` with `mapName ""`, `teams []`, `occurredAt ""` until 21:13:00.
- Map 3 flickers once: live at 21:13:00.378, `upcoming` at 21:13:01.152, live again at 21:13:02.057.
- The first live map-3 board is clock 0, white, not ticking, `occurredAt 21:12:59.416`. The next is 20 white ticking (R1 freeze).
- `game.teams` order is `[56753, 59048]` on every frame of every map. `series.teams` order is the same. Names: `Damajuana`, `HACHA`.
- `infoText.text` is `T`/`CT`. Team 56753 is T in R1–R12. It shows CT from the R13 freeze frame (`occurredAt 21:40:19.124`) on.
- Live start: every round has two frames with `currentSeconds 115` (duplicates 50–100 ms apart). R1: `21:13:21.315`.
- END: the score sum rises by exactly 1 in one frame on all 21 rounds. R1 `21:14:18.67Z`, R12 `21:39:58.377`, R13 `21:41:49.778`, R21 `21:58:37.295`.
- Freeze: white `currentSeconds 20` after END. A `20 error.0` frame exists inside R13 (`21:41:49.778`) and in the R13 END frame. It is a bomb timer, not a freeze.
- Pause: `isTicking false` at clock 8 from `21:14:37.835` to the R2 live start `21:18:39.793`.
- The finished board arrives at `21:58:38.807` with the same `occurredAt 21:58:37.295` as the R21 END frame (`21:58:37.733` received). It repeats every ~2 min.
- `occurredAt` uses 1, 2, or 3 fraction digits (`21:30:08.7Z`, `21:14:18.67Z`). Python 3.13 `datetime.fromisoformat` parses all three (checked).
- `publishDelay` is 0 on 2 348 game slots and 1 on 4.

Table (`work/plan-s7/tape-round-values.txt`, `table-r6-r7.txt`, `table-r12-r13.txt`):

- Groups: `Series`, `Game`, `Round`, `Freezetime`, and `Timeout` on 561 frames. 4 frames hold only `Game` + `Series`. Each group holds one state: the latest.
- `Game.sequenceNumber` is the map. Map 2 and map 3 frames interleave at 21:13:08–21:13:10 (`2→3→2→3`).
- `Round.sequenceNumber` is the round. `Freezetime.sequenceNumber` is always 1. Its `General.Finished` is the current freeze.
- The first table frame of every round has `Freezetime.Finished false` (all 21 rounds, `work/plan-s7/freeze-first-frame.txt`). It turns true ~9 s after the board live start (R1 `21:13:30.146`).
- Loadout at that first true frame, `Game` Team Summary `LoadoutValue`: R1 3900/4000, R2 19200/1900, R3 23600/24250, R12 26200/30850, R13 4000/4250, R21 26800/27750. All equal PLAN.md 2C.
- `WinType` appears on the winner's `Round` Team Summary row before `Round.General.Finished` turns true. R1 first `WinType` frame: `21:14:27.438`, `Duration PT1M17.294S`.
- Kills can still rise after the first `WinType` frame: R4 4/3 → 5/3, R8 4/4 → 5/4, R15 4/4 → 4/5. PLAN.md 2C final kills equal the first `WinType` frame on all 21 rounds.
- Winners, win types, and first-`WinType` kills equal PLAN.md 2C on all 21 rounds.
- `plantBomb`, `defuseBomb`, `explodeBomb` are absent on some rows and `null` on others (Round and Game Team Summary and Player). `CurrentHealth` is always an int on Round Player rows.
- R6: `plantBomb 1` first appears at `21:28:04.622`, 5 s after the R6 first `WinType` frame (`21:27:59.358`).
- R1 samples: `PT1M16.342S` kills 4/4 alive 1/1, `PT1M15.324S` kills 3/4 alive 1/2. With final 77.294 s the last sample at or below 76.294 s is `PT1M15.324S`.
- Round durations print as `PTnMn.nS`, `PTn.nS`, `PTnS`. `Series` prints `PTnHnMn.nS`.
- The first table frame of round N arrives after the first `WinType` frame of round N−1 on all 21 rounds.
  The first `WinType` frame arrives 8.34–9.30 s after END `occurredAt`, so always before END + 10 s. Every board END arrives before its table final (`work/plan-s7/end-final-lag.txt`).

Socket (`work/plan-s7/socket-markers.txt`): 78 disconnects. 72 come exactly 120.0 s after the last frame (GRID idle close). 4 come after 31–68 s.
Two fall inside map 3: `21:16:46` (120 s idle in the R2 pause) and `21:39:54` (31.2 s, during R12).

Orientation: `orient_outcomes("Damajuana", "Hacha Esports", "Damajuana", "HACHA", CS2_TEAM_ALIASES)` is `True`; with sides swapped it is `False` (`work/plan-s7/orientation.txt`).

### 1.2 Code the step touches

- `src/trader/grid_widget_types.py`: `TableCell` `:104-108`, MOBA `TableRow` `:123-136`, `Table` `:139-141`, `TableStateGroup` `:154-156` (comment names only `Series`/`Game`), `ScoreboardInfoText.text` comment `:53`.
- `src/trader/grid_widgets.py`: `Scoreboard` `:49-64` has no map name or clock color. `_scoreboard_at_index` `:117-151`. `read_map_scoreboard` `:182-185`.
  `read_net_worth` `:250-275` reads the `Game` group's `Player` table; on a CS2 payload it raises `KeyError 'NetWorth'`.
- `Scoreboard(` is built in two places: `grid_widgets.py:138` and `tests/trader_discovery_fixtures.py:236` (`make_scoreboard`).
  No code compares or serializes a `Scoreboard`. The GRID differential digests ticks and sides, not boards (`work/step-005/grid_reducer_diff.py:58-66`).
- `src/trader/grid_feed_spec.py`: `WidgetReducer.reduce_frame -> FeedEvent | KillTick | None` `:32`, `note_feed_gap -> None` `:36`. `GridFeedSpec` `:41-71`.
- `src/trader/grid_feed.py`: `GridOrientationError` `:56`, `FEED_GONE_SERVICE` `:51`, `GridFrameReducer._lock_sides` `:195-217`, `_on_feed_gone` `:219-225`, `replay_grid_records` `:338-346` (keeps `FeedEvent` only), `MobaGridFeed.read_board_sides` `:357-387`.
- `src/trader/grid_live_feed.py`: `AppliedWidgetFrame.event: FeedEvent | KillTick` `:35`, `_archive_feed_gone` `:58-67`, `ticks()` `:89-142`. `note_feed_gap()` is called only in the `WebSocketException` branch `:127`. A clean close returns at `:118`.
- `src/trader/live_feed.py`: `KillTick` `:145-151`, `LiveFeed.ticks -> AsyncIterator[FeedEvent | KillTick]` `:160`, `PriorAnchor` `:51-55`.
- `src/trader/strategy_spec.py:28`: `SignalSource.on_tick(tick: FeedEvent | KillTick, now_ns)`.
- `src/trader/follow300_signals.py:30-37`: `on_tick` handles `KillTick`, else reads `tick.snapshot`.
- `src/trader/match_worker.py:236-240`: `self._signals.on_tick(event, ...)`, then `if isinstance(event, KillTick): continue`, then the event is used as a `FeedEvent`.
- Prior anchor use: `_maybe_start_prior` `match_worker.py:589-604` needs phase `PRE_HORN` or `IN_PROGRESS`. `_fix_prior_anchor` `:606-632`: `prior_anchor None` waits; `PriorAnchor(None)` latches prior `None` and logs a block.
- `src/trader/source_picker.py`: private `_probe_first` `:83-114`, `probe_grid_delay` `:125-134` reads the table delay through `read_net_worth`. On CS2 that parse fails, `_frame_value` swallows `KeyError` (`:79`), and the probe ends in a timeout.
- `src/trader/feed_selection.py:96-121`: `profile.feed.build_reducer`, `profile.feed.services`, `profile.feed.probe_delay`. `GameProfile.feed` is `MobaGridFeed` (`game_profile.py:40`).
  So `grid_archive.py:38`, `archive_index/schedule.py:311`, `viewer/game_state_replay.py:191` see the concrete `GridFrameReducer` and do not change.
- Implementers of the widened protocols: `OddinLiveFeed.ticks` (`oddin_live_feed.py:249`, narrower, still fits), `GridLiveFeed.ticks`, `Follow300Signals.on_tick`, `tests/trader_session_fixtures.py:224` (`AsyncIterator[FeedEvent]`, fits). No other `on_tick`, `reduce_frame`, or `note_feed_gap` in `src`, `tests`, `scripts`.
- Reused offline pieces: `CS2_MAP_CODES` `src/cs2/constants.py:85-96`, `CS2_TEAM_ALIASES` `:47-84`, `opens_half` `src/cs2/s11_round_features.py:97-103`,
  `DOTA_PRIOR_ANCHOR_SECOND = -85` `src/shared/constants/dataset.py:13` (offline CS2 uses it: `src/cs2/s09_build_market_data.py:163`),
  `parse_utc` / `datetime_to_ns` / `NS_PER_SECOND` `src/shared/utils/match_time.py:7-31`, `ZERO_TOP` `shared.utils.top_players`.
  The trader already imports `cs2.s11_round_frame` (which imports `s11_round_features`) through `trader/cs2_models.py:4`.
- Discovery polls every 30 s (`src/trader/cadence.py:10`). An `upcoming` CS2 slot has no teams, so the worker attaches only after the map shows `live`.
- Test fakes for sockets: `tests/test_grid_feed.py:95-155` (`_ClosedSocket`, `_FrameSocket`), reconnect pattern `:334-372` (patches `trader.grid_live_feed.websockets.connect`, `asyncio.sleep`, `TRADER_DIR`).
  `tests/test_grid_feed.py` is 1196 lines. Do not add to it.
- `pyrightconfig.json` runs basedpyright `strict` over `src`, `tests`, `scripts`.

### 1.3 Baselines on `a2616d45`

- GRID differential: `work/plan-s7/grid-base.txt`, 24 427 lines, sha256 `df2fb0a83bf12b0b629fcbd582f7c0162aacffd134003975383506c315784c5e`, `cmp` equal to `work/step-005/grid-head.txt`. 11.4 s.
- Signal differential: `work/plan-s7/signals-base.txt`, 27 514 lines, sha256 `7b70dde69205f7823f4e2509ce33806dfe46ff162f14921873b2d4e16d3c445e`, `cmp` equal to `work/step-005/signals-head.txt`. 31.6 s.
- Targeted pytest, the 21 files in §5 step 3 without the two new files: `528 passed, 16 warnings, 2 subtests passed in 6.17s`.

## 2. Design decisions

1. **Shared round types in one light module.** New `src/trader/cs2_round_types.py` holds `HistoryLoss`, `MapHeader`, `RoundFacts`, `RoundLiveTick`, `RoundDecisionDue`, `RoundHistoryLost`.
   It imports only `dataclasses` and `enum`. The reducer, `live_feed.py`, and the STEP-008 feature builder import it. No cycle.
2. **One tick union.** `live_feed.py` gets `FeedTick = FeedEvent | KillTick | RoundLiveTick | RoundDecisionDue | RoundHistoryLost` (repo alias style, like `LoadedModel` in `strategy_spec.py:13`).
   `WidgetReducer.reduce_frame`, `WidgetReducer.note_feed_gap`, `GridLiveFeed.ticks`, `LiveFeed.ticks`, and `SignalSource.on_tick` use it.
   This is required: `GridLiveFeed` consumes `WidgetReducer`, and `Cs2GridFeed` must satisfy `GridFeedSpec`. MOBA producers return narrower types and still fit.
3. **The worker filter.** `match_worker.py:239` becomes "skip every tick that is not a `FeedEvent`". `Follow300Signals.on_tick` handles `KillTick` and `FeedEvent` and ignores the rest.
   Dota/LoL produce only `FeedEvent` and `KillTick`, so behavior is identical. The signal differential proves it.
4. **The due tick carries its inputs.** `RoundDecisionDue` holds `round_number`, `due_ns`, `team_a_ct`, `header`, and the completed `rounds`.
   STEP-009 `RoundSignals` then needs no reducer reference, and replay is deterministic.
5. **Team a is `BoardSides.radiant`, the first team of the pinned game.** All `_a`/`_b` fields orient to it. `FeedEvent.yes_is_radiant` is `outcome_0_is_radiant`.
6. **History loss is permanent for the map.** Any `HistoryLoss` stops every later `RoundDecisionDue` of this reducer. Board folding, `FeedEvent`, `RoundLiveTick`, and terminal events continue.
   A loss found on a later frame returns `RoundHistoryLost` once. A loss found on the lock frame (late join) returns the first `FeedEvent` and is only logged: no due can follow, so no BUY window can open.
7. **A socket gap is a loss (`FEED_GAP`).** `Cs2FrameReducer.note_feed_gap()` returns `RoundHistoryLost`. `GridLiveFeed` yields the value when it is not `None`. `GridFrameReducer.note_feed_gap` keeps `-> None`.
   This is the simplest rule the user chose for incomplete history. Its ceiling: after an abnormal disconnect the rest of the map has no calls.
   The upgrade is to keep calling after a gap when the next board continues the score sum and every round fact is present. Record this ceiling in `report.md` and `progress.txt`, not in a code comment.
8. **Late join = lock board score sum above 0.** History for round 1 is complete only if the reducer sees round 1's `Freezetime.Finished false → true` change. The same change rule gives every round its loadout. A missing loadout is `MISSED_ROUND`.
9. **The R1 call needs a pre-start lock.** `RoundDecisionDue(1)` is emitted only when the lock board has sum 0 and a stopped clock (the tape's first live board).
   Otherwise a mid-round white 20 could pose as the R1 freeze. R1 and R13 are pistol rounds (`apply_round_rules` gives ±1e-4, below the 0.01 entry threshold), so a skipped R1 call costs no BUY.
10. **Finals freeze at the first `WinType` frame.** Final kills and the duration come from that frame. `planted` is true if any frame of the round up to and including that frame shows a plant.
    Later frames of the round do not change the facts. R6's late plant stays false (gap to offline listed in §6).
11. **`FeedEvent` only from board frames.** Table frames return `None` unless they produce a due or a loss. CS2 `GameSnapshot`: `second = int(now.timestamp()) - map_live_unix`, `server_timestamp = clock_stamp_unix_seconds(board.occurred_at)`,
    phase `IN_PROGRESS` (live) or `FINISHED`, every MOBA field 0, `top=ZERO_TOP`, `paused = not board.clock_ticking`.
    `horn_unix_seconds = map_live_unix`. `prior_anchor = PriorAnchor(map_live_unix + DOTA_PRIOR_ANCHOR_SECOND)`; after a late join it is `PriorAnchor(None)`.
    `map_live_unix = clock_stamp_unix_seconds(occurredAt of the lock board)`. Offline zero is `ceil(spawn_at)` (`s09_build_market_data.py:150`), a different GRID event, so 1 s rounding does not matter.
12. **Reuse, no new helpers beyond two.**
    - Extract `orient_side_pair(side_0: TeamSide, side_1: TeamSide, outcome_0_name, outcome_1_name, aliases) -> BoardSideRead` from the tail of `MobaGridFeed.read_board_sides` (`grid_feed.py:373-387`). Both feeds call it. Pure move.
    - Add public `probe_grid_board_delay(series_id, map_number) -> int | None` in `source_picker.py` next to `probe_grid_delay`, over `_probe_first` with the scoreboard service. It returns `publish_delay` of `read_map_scoreboard(payload, map_number)` when that slot exists.
    - Reuse `opens_half`, `CS2_MAP_CODES`, `orient_outcomes`, `read_map_scoreboard`, `clock_stamp_unix_seconds`, `parse_utc`, `datetime_to_ns`, `GridOrientationError`, `FEED_GONE_SERVICE`.
13. **Two team row types.** `Game` and `Round` Team Summary rows differ (`LoadoutValue`/`Score` vs `WinType`). Two exact `TypedDict`s keep required keys required.
    The parser casts `tableRows` at the read boundary, the existing style (`grid_widget_types.py:7`). Parsed dataclasses carry only the fields the reducer reads.
14. **No comments and no docstrings in new code** (`.feature-json.config.json` review rule). Fix the two existing inline comments that become wrong (`:53`, `:155`).

## 3. File-by-file changes

### 3.1 `src/trader/grid_widget_types.py`

- `ScoreboardInfoText.text` comment: list `RADIANT`/`DIRE`, `BLUE`/`RED`, `T`/`CT`. `TableStateGroup.name` comment: add `Round`, `Freezetime`, `Timeout`.
- Add `TextCell` (`value: str | None`, `teamColor: str | None`) and `BoolCell` (`value: bool | None`, `teamColor: str | None`).
- Add `Cs2GameTeamRow`: `entity: TableEntity`, `Score`, `LoadoutValue`, `Kills`, `Deaths`: `TableCell`; `Side: TextCell`; `plantBomb`, `defuseBomb`, `explodeBomb`: `NotRequired[TableCell]`.
- Add `Cs2RoundTeamRow`: `entity`, `Kills`, `Deaths`: `TableCell`; `Side`, `WinType`: `TextCell`; the three bomb cells `NotRequired[TableCell]`.
- Add `Cs2PlayerRow`: `entity: TableEntity`, `CurrentHealth: TableCell`, `Kills`, `Deaths`: `TableCell`.
- Add `Cs2GeneralRow`: `Duration: TextCell`, `Finished: BoolCell`.

### 3.2 `src/trader/grid_widgets.py`

- `Scoreboard`: add `map_name: str` and `clock_color: str`. `_scoreboard_at_index` fills them from `game["mapName"]` and `clock["color"]`. MOBA ignores both.

### 3.3 New `src/trader/cs2_widgets.py` (parse only, no state)

- `Cs2TeamLoadout(team_id: str, loadout: int)`.
- `Cs2RoundTeam(team_id: str, kills: int, win_type: str, planted: bool, alive: int)`.
  `planted` is true when the row has `plantBomb` with a value of 1 or more. `alive` counts this team's `Round` Player rows with `CurrentHealth > 0`. `win_type` is `""` when the cell value is empty or null.
- `Cs2RoundTable(number: int, duration_s: float, freeze_finished: bool, teams: tuple[Cs2RoundTeam, ...])`.
- `Cs2Table(game_number: int, feed_delay: int, loadouts: tuple[Cs2TeamLoadout, ...], round_table: Cs2RoundTable)`. The field is not named `round`, to keep the builtin.
- `parse_duration_seconds(text: str) -> float`: one regex `^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$`. `PT` alone or a mismatch raises `ValueError`.
- `read_cs2_table(payload: str, feed_delay: int) -> Cs2Table | None`:
  1. `cast(SeriesTablePayload, json.loads(payload))`. Index groups by name.
  2. Return `None` when `Game`, `Round`, or `Freezetime` is missing or has no state. A MOBA payload has no `Round` group, so it is `None` before any row read.
  3. Read the last state of each group. Find entity groups by name: `Team Summary`, `Player`, `General`. Cast their first table's `tableRows`.
  4. Team id: `entity["teamColor"].rsplit(".", 1)[-1]`, as `grid_widgets.py:237`.
  5. `freeze_finished` comes from the `Freezetime` `General` row. `number` is `Round.sequenceNumber`. `game_number` is `Game.sequenceNumber`.
- Reuse the group-name constant `GAME_STATE_GROUP` from `grid_widgets.py`. Add `ROUND_STATE_GROUP`, `FREEZETIME_STATE_GROUP`, `TEAM_SUMMARY_ENTITY_GROUP`, `GENERAL_ENTITY_GROUP` here. Reuse `PLAYER_ENTITY_GROUP`.

### 3.4 New `src/trader/cs2_round_types.py`

- `HistoryLoss(StrEnum)`: `LATE_JOIN`, `SCORE_JUMP`, `SCORE_ROLLBACK`, `MISSED_ROUND`, `FEED_GAP`.
- `MapHeader(map_code: int, game_number: int, series_score_a: int, series_score_b: int)`.
- `RoundFacts`: `number`, `team_a_ct: bool`, `a_won: bool`, `win_type: str`, `loadout_a`, `loadout_b`, `kills_a`, `kills_b`, `seen_kills_a`, `seen_kills_b`, `seen_alive_a`, `seen_alive_b`: `int`, `planted: bool`, `duration_s: float`, `live_ns: int`, `end_ns: int`.
  `kills_*` are the first-`WinType` frame. `seen_*` are the last sample with `duration_s <= final duration - 1`. `team_a_ct` is the board side at this round's live start.
- `RoundLiveTick(round_number: int, live_ns: int)`.
- `RoundDecisionDue(round_number: int, due_ns: int, team_a_ct: bool, header: MapHeader, rounds: tuple[RoundFacts, ...])`. `team_a_ct` is the side for the called round.
- `RoundHistoryLost(round_number: int, reason: HistoryLoss)`.
- All frozen dataclasses.

### 3.5 `src/trader/live_feed.py`

- Import the three round ticks. Add `FeedTick`. `LiveFeed.ticks -> AsyncIterator[FeedTick]`.

### 3.6 `src/trader/grid_feed_spec.py`

- `WidgetReducer.reduce_frame -> FeedTick | None`. `WidgetReducer.note_feed_gap -> FeedTick | None`. Keep the protocol docstrings as they are.

### 3.7 `src/trader/grid_feed.py`

- Add module function `orient_side_pair` (decision 12). `MobaGridFeed.read_board_sides` keeps its `side_team` lookups and pending check, then returns `orient_side_pair(...)`.
- `GridFrameReducer` does not change.

### 3.8 `src/trader/grid_live_feed.py`

- `AppliedWidgetFrame.event: FeedTick`. `_archive_feed_gone -> FeedTick | None`. `_apply_widget_frame` keeps its `isinstance(event, FeedEvent)` cutoff check. `ticks() -> AsyncIterator[FeedTick]`.
- In the `WebSocketException` branch, keep the value of `self._reducer.note_feed_gap()` and yield it when it is not `None`, before the warning and the sleep.
  Do not archive the gap: a new record kind in `grid_state.jsonl` would reach MOBA archive readers.

### 3.9 `src/trader/source_picker.py`

- Add private `_board_delay_for_map(frame, map_number) -> int | None` and public `probe_grid_board_delay` (decision 12). Label `f"scoreboard map={map_number}"`.

### 3.10 `src/trader/strategy_spec.py`, `src/trader/follow300_signals.py`, `src/trader/match_worker.py`

- `SignalSource.on_tick(self, tick: FeedTick, now_ns: int)`.
- `Follow300Signals.on_tick`: `KillTick` → `_on_kill_tick` and return; `FeedEvent` → the two existing record lines. No other branch.
- `MatchWorker.run`: replace `isinstance(event, KillTick)` at `:239` with `not isinstance(event, FeedEvent)`. Drop the `KillTick` import there if it becomes unused.

### 3.11 New `src/trader/cs2_feed.py`

Module constants: `ROUND_LIVE_SECONDS = 115`, `FREEZE_SECONDS = 20`, `FREEZE_COLOR = "white"`, `CT_SIDE = "CT"`, `END_DUE_SECONDS = 10`, `FREEZE_DUE_SECONDS = 2`.

`Cs2GridFeed` (frozen dataclass, no fields):

- `services` property returns `REDUCED_SERVICES`.
- `read_board_sides`: pending when `len(board.teams) != 2`; else `orient_side_pair(board.teams[0], board.teams[1], ...)`.
- `probe_delay` awaits `probe_grid_board_delay`. Import that name into `cs2_feed`, so tests patch `trader.cs2_feed.probe_grid_board_delay`.
- `build_reducer(map_number, outcome_0_name, outcome_1_name, aliases) -> Cs2FrameReducer`, passing `self`.

`Cs2FrameReducer(map_number, outcome_0_name, outcome_1_name, aliases, feed: Cs2GridFeed)`:

- Public `board: Scoreboard | None` (last pinned board), as `GridFrameReducer`.
- Private state: locked sides, header, `map_live_unix`, pre-start flag, last scores of a and b, completed `rounds` list, `loss: HistoryLoss | None`, finished flag, last due round, last table round,
  and one mutable private draft per round number (`@dataclass` without `frozen`, like `PriorAnchorClock` in `live_feed.py:82`).
  Draft fields: `live_ns`, `team_a_ct`, `freeze_ns`, `end_ns`, `a_won`, `freeze_open_seen`, `loadout_a`/`loadout_b`, `samples` (frozen private `_RoundSample(duration_s, kills_a, kills_b, alive_a, alive_b)`), the final sample, `win_type`, `planted`.
- `reduce_frame(frame, now) -> FeedTick | None`: `FEED_GONE_SERVICE` → terminal `FeedEvent` from `board` (or `None` before lock). Empty payload → `None`. Scoreboard and table go to the two handlers below. Other services → `None`.
- `note_feed_gap() -> RoundHistoryLost | None`: after lock, before finish, without an earlier loss → set `FEED_GAP` and return the tick for the current round (`sum + 1`). Else `None`.
- Log each loss once on the module logger: map, round, reason.

Scoreboard frame, `board = read_map_scoreboard(payload, map_number)`. First match wins:

| # | Condition | Effect | Returns |
|---|---|---|---|
| 1 | `board` is `None` or `game_status == upcoming` | nothing (covers the flicker) | `None` |
| 2 | sides not locked | `read_board_sides`; pending → `None`; unresolved → `GridOrientationError`; `CS2_MAP_CODES.get(map_name)` missing → `ValueError` naming map and number. Lock sides, header (series scores from `maps_won`), `map_live_unix`, scores. Pre-start = sum 0 and clock stopped. Sum > 0 → `LATE_JOIN` (log only) | terminal `FeedEvent` if finished, else `FeedEvent` |
| 3 | `game_status == finished` | finished flag | terminal `FeedEvent` |
| 4 | sum fell | loss `SCORE_ROLLBACK` | `RoundHistoryLost` if new, else `FeedEvent` |
| 5 | sum rose by 2 or more | loss `SCORE_JUMP` | same as 4 |
| 6 | sum rose by 1 | END of round `old_sum + 1`: `end_ns`, `a_won`. Try facts, then due for the next round | due, loss, or `FeedEvent` |
| 7 | clock 115 and current draft has no `live_ns` | `live_ns`, `team_a_ct` from team a's `infoText` | `RoundLiveTick` |
| 8 | clock 20, color white, no `live_ns`, no `freeze_ns` | `freeze_ns`. Try due for the current round | due or `FeedEvent` |
| 9 | otherwise | none | `FeedEvent` |

`self.board` updates on every pinned non-upcoming board. Stamps are `datetime_to_ns(parse_utc(board.occurred_at))`. Team a and b scores come from `board.teams` by locked id, never by index.

Table frame, `table = read_cs2_table(payload, delay)`:

| # | Condition | Effect | Returns |
|---|---|---|---|
| 1 | `None`, other `game_number`, or sides not locked | nothing | `None` |
| 2 | round number > board round (`sum + 1`) | loss `MISSED_ROUND` | `RoundHistoryLost` if new |
| 3 | round number > last table round | if the last table round has no final, or the gap is more than 1 → loss `MISSED_ROUND`. Set last table round | loss or continue |
| 4 | round number < last table round | nothing | `None` |
| 5 | draft has no final | `planted` OR this frame. `freeze_finished` false → `freeze_open_seen`. True, `freeze_open_seen`, no loadout → loadout by id. Append the sample. Any `win_type` → final = this sample, `win_type` of the winner. Try facts, then due for the next round | due, loss, or `None` |

Build `RoundFacts(N)` once both `end_ns` and the final exist. Require `live_ns`, `team_a_ct`, the loadout, a seen sample, and `N == len(rounds) + 1`. Any miss → loss `MISSED_ROUND`.

Emit `RoundDecisionDue(N)` once, only without loss, before finish, when `N > last due round` and draft N has no `live_ns`:

- `N == 1`: pre-start lock and `freeze_ns` of round 1. `due_ns = freeze_ns + 2 s`. `rounds = ()`.
- `opens_half(N)`: `len(rounds) == N - 1` and `freeze_ns` of round N. `due_ns = freeze_ns + 2 s`.
- otherwise: `len(rounds) == N - 1`. `due_ns = rounds[-1].end_ns + 10 s`.
- `team_a_ct` from `self.board` at emission. For an opener the freeze board already shows the new sides (R13 on the tape).

A due whose `due_ns` already passed is still emitted; the STEP-009 caller waits `max(0, due - now)` and drops it after live start.

Ruff runs `C901` with the default limit 10. Split the two handlers into one small method per table row group (lock, score change, clock marks, table fold, facts, due) so no method passes the limit.
Keep `cs2_feed.py` under 1 000 lines; the estimate is 300–400.

## 4. Tests

### 4.1 New `tests/cs2_grid_widget_fixtures.py`

Real values from the tape, trimmed to the typed keys:

- Constants: `DAMAJUANA = "56753"`, `HACHA = "59048"`, series `3009765`, names `Damajuana`/`HACHA`, market outcomes `Damajuana`/`Hacha Esports`.
  Nicks: `Dunn`, `nikito`, `R4N`, `leo`, `gon` (56753) and `superex-`, `Kvns`, `Lucasye_-`, `toto-m`, `busan-` (59048).
  Stamps: map live `2026-10-21T21:12:59.416Z`, R1 live `21:13:21.315Z`, R1 END `21:14:18.67Z`, R12 END `21:39:58.377Z`, R13 freeze `21:40:19.124Z`, R13 bomb 20 `21:41:49.778Z`, map finished `21:58:37.295Z`.
- `cs2_scoreboard(...) -> ScoreboardPayload`: three games (ancient and inferno finished, nuke slot built from arguments: status, clock seconds, color, ticking, `occurredAt`, scores and sides by team). Series maps 1:1. Upcoming variant: `mapName ""`, `teams []`, `occurredAt ""`.
- `cs2_table(...) -> object`: a `series_table` payload from arguments (game number, round number, round duration text, freeze finished, loadouts, per-team kills, win type, plant presence, per-player health).
  Build rows with the new row `TypedDict`s. Omit `plantBomb` when the argument is `None` so the `NotRequired` path is real.
  Pass the result to `grid_widget_fixtures.wrap(service, delay, payload)`.
- Scripted frame lists for R1 (pre-start, freeze, duplicate 115, samples, final) and for R12→R13 (END, final, `20 error.0`, white 20 with flipped sides).

### 4.2 New `tests/test_cs2_widgets.py`

1. `parse_duration_seconds`: `PT1M17.294S` → 77.294, `PT0S` → 0.0, `PT40.043S` → 40.043, `PT1M` → 60.0, `PT1H2M3S` → 3723.0; `PT`, `1:17`, `PT-1S` raise `ValueError`.
2. R1 final frame: `game_number 3`, `round_table.number 1`, `duration_s 77.294`, team 56753 `win_type opponentEliminated`, kills 5, alive 1, `planted True`; team 59048 `win_type ""`, kills 4, alive 0.
3. Alive is `CurrentHealth > 0`: health 0 is dead, 1 is alive.
4. Parametrize the four win types `opponentEliminated`, `bombDefused`, `bombExploded`, `timeExpired`.
5. `plantBomb` absent → `planted False`; `plantBomb` null → `False`.
6. R6 freeze-finished frame: loadouts 26550/26950, `freeze_finished True`.
7. MOBA `SERIES_TABLE` from `tests/grid_widget_fixtures.py` → `None`. A `Game` + `Series` only frame → `None`.
8. Scoreboard: CS2 slot 3 live → `map_name "nuke"`, `clock_color` as given, two teams. Upcoming slot → `teams ()`, `game_status upcoming`. `read_map_scoreboard(payload, 4)` → `None`.
   Dota `SCOREBOARD` → `map_name "Defense of the Ancients"`, `clock_color "white"`.

### 4.3 New `tests/test_cs2_feed.py`

Fold frames with `parse_frame(wrap(...))` and a `now` equal to the frame's receipt stamp.

1. Orientation: outcomes `Damajuana`/`Hacha Esports` → first `FeedEvent.yes_is_radiant True`; swapped outcomes → `False`.
2. Pending: an upcoming slot before lock → `None`; the upcoming flicker after lock → `None` and no state change (the next live frame is a plain `FeedEvent`).
3. Name mismatch → `GridOrientationError`. Unknown `mapName` → `ValueError`.
4. First `FeedEvent`: `horn_unix_seconds == map_live_unix`, `prior_anchor == PriorAnchor(map_live_unix - 85)`, `paused True` on the stopped-clock frame, phase `IN_PROGRESS`, MOBA fields 0, `top == ZERO_TOP`.
5. R1 due: pre-start lock, then white 20 → `RoundDecisionDue(1, freeze + 2 s, rounds=())`. The second white 20 after the flicker → no second due.
6. Live start: 115 → `RoundLiveTick(1, R1 live ns)`. The duplicate 115 → `FeedEvent`.
7. R2 due, END first: END board → `FeedEvent`; final table → `RoundDecisionDue(2, END + 10 s)`. Final first (synthetic order): final table → `None`; END board → due with the same `due_ns`.
8. `RoundFacts(1)` in that due: `a_won True`, `win_type opponentEliminated`, loadout 3900/4000, kills 5/4, seen kills 3/4, seen alive 1/2 (the `PT1M16.342S` sample is excluded), `team_a_ct False`, `planted True`, `live_ns`, `end_ns`.
9. Freeze rules: loadout stays at the first `freeze_finished` frame when a later frame shows a lower `LoadoutValue`. A later frame with more kills after the first `WinType` (R4: 4/3 then 5/3) keeps 4/3.
10. No due after live start: R2's 115 arrives before the R1 final → no due for R2, ever.
11. No duplicate due: a repeated final table frame and a repeated END board give one due.
12. R13 opener: END R12, final R12 → no due yet; `20 error.0` → `FeedEvent`, no due; white 20 with team a on CT → `RoundDecisionDue(13, freeze + 2 s, team_a_ct=True)`.
    `rounds[-1].team_a_ct` (R12) is `False`. Team ids stay; only `infoText` flips.
13. Other map: interleaved `Game.sequenceNumber 2` table frames between map-3 frames change nothing.
14. Score jump by 2 → `RoundHistoryLost(SCORE_JUMP)`, then no due for any later round. Score rollback → `SCORE_ROLLBACK`.
15. Late join: lock board with sum 3 → `FeedEvent` with `prior_anchor == PriorAnchor(None)`; later END and finals give no due and no loss tick.
16. Missed round: a table frame of round 3 while round 2 has no final → `RoundHistoryLost(MISSED_ROUND)`. A round-1 table frame that is already `freeze_finished` first → no loadout → `MISSED_ROUND` at R1 facts.
17. Pause: `isTicking false` → `FeedEvent.snapshot.paused True`. A due built during the pause keeps `END + 10 s`.
18. Finished: finished board → terminal `FeedEvent` (`phase FINISHED`). A later final table → `None`, no due.
19. Feed gone: `FEED_GONE_FRAME` after lock → terminal `FeedEvent` from the last board; before lock → `None`.
20. Gap: `note_feed_gap()` after lock → `RoundHistoryLost(FEED_GAP)`; a second call → `None`; before lock → `None`; no due after a gap.
21. `GridLiveFeed` with a `Cs2FrameReducer`: scripted lock frames, then a socket close, then `ConnectionClosed` on every retry. `ticks()` yields the `RoundHistoryLost(FEED_GAP)` tick once.
    Use a small local fake socket and the patch set of `tests/test_grid_feed.py:355-357`.
22. `Cs2GridFeed`: `services == REDUCED_SERVICES`; a typed `feed: GridFeedSpec = Cs2GridFeed()` assignment (basedpyright checks the protocol); `read_board_sides` on an upcoming slot → pending;
    `probe_delay` returns the patched `trader.cs2_feed.probe_grid_board_delay` value.

### 4.4 Changed tests

- `tests/trader_discovery_fixtures.py:236`: pass `map_name` and `clock_color` to `Scoreboard`.
- `tests/test_grid_widgets.py`: assert the two new fields on the Dota fixture (one test).
- `tests/test_source_picker.py`: `probe_grid_board_delay` returns the board `publishDelay` from a scoreboard-only socket; returns `None` when the pinned slot never exists; `None` on a closed socket. Mirror `:117-188`.
- No other test changes. Existing MOBA reconnect tests already fail if `GridLiveFeed` yields an extra tick for a MOBA gap.

## 5. Verification

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`. `INV=../betting_workspace/investigations/2026-10-23-cs2-live-validation`. `PLAN7=../betting_workspace/tasks/cs2-live/orchestrate/work/plan-s7`. Write outputs to `$INV/work/step-007/`.

1. `.venv/bin/ruff check src tests scripts` and `.venv/bin/ruff format --check src tests scripts` → clean.
2. `.venv/bin/basedpyright` → `0 errors, 0 warnings, 0 notes`.
3. Targeted pytest:
   `PYTHONPATH=src:scripts:../prediction-market-backtesting .venv/bin/python -m pytest -p no:cacheprovider -q -n 8 tests/test_cs2_widgets.py tests/test_cs2_feed.py tests/test_board_signal_contract.py tests/test_discovery_cadence.py tests/test_follow300_signals.py tests/test_grid_feed.py tests/test_lol_prior.py tests/test_source_picker.py tests/test_trader_discovery.py tests/test_trader_durable_place.py tests/test_trader_live_execution.py tests/test_trader_lol_discovery.py tests/test_trader_match_lifecycle.py tests/test_trader_sell_venue_cancel.py tests/test_trader_session_quoting.py tests/test_trader_unsettled_buy_activation.py tests/test_trader_wallet_host.py tests/test_trader_wallet_sidecar.py tests/test_grid_widgets.py tests/test_lol_grid_widgets.py tests/test_lol_grid_feed.py tests/test_trader_game_profile.py tests/test_cs2_models.py`
   Baseline without the two new files: 528 passed. The result must be 528 plus the new and added tests, 0 failed.
4. `PYTEST_N=8 make test` → 0 failed. Known: `test_upcoming_rows_from_sidecars` fails only 23:00–00:00 Berlin; `test_follow300_replay` `dota_9007700576` flakes under xdist (rerun alone). Never run two `-n` suites at once.
5. GRID differential (Dota/LoL reducer and `orient_side_pair`):
   `PYTHONPATH=src:tests .venv/bin/python $INV/work/step-005/grid_reducer_diff.py data/trader $INV/work/step-005/sessions.txt > $INV/work/step-007/grid-head.txt 2> $INV/work/step-007/grid-head.err`
   → exit 0, 24 427 lines, `cmp $INV/work/step-007/grid-head.txt $PLAN7/grid-base.txt` equal (sha256 `df2fb0a8…784c5e`).
6. Signal differential (`Follow300Signals.on_tick`):
   `PYTHONPATH=src:tests .venv/bin/python $INV/work/step-005/signals_diff.py data/trader $INV/work/step-005/sessions.txt > $INV/work/step-007/signals-head.txt 2> $INV/work/step-007/signals-head.err`
   → exit 0, 27 514 lines, `cmp` equal to `$PLAN7/signals-base.txt` (sha256 `7b70dde6…c3445e`).
7. Import smoke: `PYTHONPATH=src .venv/bin/python -c "import trader.cs2_feed, trader.cs2_widgets, trader.cs2_round_types, trader.game_profile, trader.match_worker; print('import-ok')"`.
   `grep -rn --include='*.py' "from backtest\|import backtest" src/trader src/strategy src/cs2 src/shared` → only `src/strategy/signals.py:1`, as in STEP-006.
8. Tape smoke on real data, a script in `$INV/work/step-007/` (not a repo test; STEP-010 owns the CLI and the tape test).
   Read `data/cs2/live/cs2-damaju-hachae-2026-10-21/grid.jsonl`, skip `marker` lines, fold every `frame` through `Cs2GridFeed().build_reducer(3, "Damajuana", "Hacha Esports", CS2_TEAM_ALIASES)` with `now = parse_utc(received_at_utc)`.
   Expected: 21 `RoundLiveTick` (R1 live `21:13:21.315`), 21 `RoundDecisionDue` for rounds 1–21, 0 `RoundHistoryLost`, terminal `FeedEvent` at the board received `21:58:38.807`.
   Due R1 `21:13:01.416`, due R2 `21:14:28.670`, due R13 `21:40:21.124`. R1 facts as §4.3 test 8. R13 facts `team_a_ct True`, loadout 4000/4250. Per-round winners, win types, final kills, and loadouts as §1.1.
   Save the printed ledger and the command in `report.md`. If any value differs, stop and report; do not change expectations to fit.
9. `report.md`: append `## STEP-007` with commands, outputs, durations, the gap-ban ceiling (decision 7), and the R6 plant note. `investigations/` stays untracked.
10. `progress.txt` entry with learnings. Commit once in `esports-trader`.

## 6. Edge cases, risks, hand-offs

- **Gap ban cost.** On the tape, map 3 has one abnormal disconnect (`21:39:54`, during R12). Live, the rule would drop calls R13–R21. 4 of 78 tape disconnects are abnormal.
- **Clean idle close (STEP-012).** GRID closes after 120 s of silence (72 of 78 disconnects). `GridLiveFeed` returns on a clean close (`grid_live_feed.py:118`), so a long CS2 pause ends the feed today.
  If STEP-012 adds a reconnect for that case, it must not call `note_feed_gap`. Otherwise every map with a 2-minute pause loses the rest of its calls.
- **Late attach.** Discovery polls every 30 s and the upcoming slot has no teams. The window for complete history is about 31 s after map live (R1 freeze 22 s + ~9 s table lag). A later attach bans the map.
- **R1 call needs the pre-start board.** Attaching during the R1 freeze skips only the R1 call (pistol, no entry).
- **R6 late plant.** Planted counts up to the first `WinType` frame. The tape's R6 plant comes 5 s later, so live R6 `planted` is false. STEP-008/010 parity must expect this or change the rule with evidence.
- **Final kills.** Post-round kills after the first `WinType` frame (R4, R8, R15) are not counted. This matches PLAN.md 2C.
- **Freeze stamp at R1.** The first R1 freeze stamp (`21:12:59.416`) is 2 s before the re-sent one after the flicker (`21:13:01.376`). The plan uses the first. Both are long before live start.
- **One tape.** Every board/table rule rests on one CS2 map. An annulled round or an admin restart desyncs the score sum; the table-ahead check then ends calls (`MISSED_ROUND`).
- **Due after map end.** No due follows a finished board. A due can be emitted between the last END and the finished board only if the table beats the board, which never happens on the tape.
- **STEP-009 needs.** Register `Cs2GridFeed` in `GAME_PROFILES` and widen `GameProfile.feed`. `grid_archive.summarize`, `schedule._extract_grid`, and the dashboard read `reducer.board` and `read_pinned_horn_iso` today (STEP-003 learning).
  `BUY_CUTOFF_SECOND_BY_GAME["cs2"]` is still 480; with CS2 `second` counted from map live, `past_cutoff` would trip after 8 minutes until STEP-009 sets `10**9`.
  `RoundSignals` must treat `RoundLiveTick`, `RoundHistoryLost`, and the terminal `FeedEvent` as window close.
- **STEP-008 needs.** Opener rounds use the previous round's final state. `RoundFacts` carries `end_ns` and `live_ns` for the synthetic kill clocks. Scores before a round are the count of `a_won` in `rounds`.

## 7. Out of scope

- `GAME_PROFILES["cs2"]`, `RoundStrategy`, `RoundSignals`, `buys_open`, heartbeat, stale timeout, prior reading (STEP-009).
- Feature frames, cash, model calls (STEP-008).
- `replay_cs2_rounds` CLI and `test_cs2_tape_replay.py` (STEP-010).
- Dashboard labels for CS2 sides (STEP-011).
- Clean-close reconnect and any VPS check (STEP-012).
