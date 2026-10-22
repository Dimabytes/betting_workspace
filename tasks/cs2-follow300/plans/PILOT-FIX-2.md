# PILOT-FIX-2 — pilot fix pack (s03 shared game numbers, typed empty fills.parquet)

Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, branch `cs2-signal`, HEAD `a804a623`.
Spec: bugs 3 and 4 of `orchestrate/reports/pilot-2-continue.md`, the PILOT-FIX-2 orchestrator decision at the end of `progress.txt`, section «Баги» of `investigations/2026-10-18-cs2-follow300/report.md`.
This is not a feature.json step. Do not change `passes` in feature.json. One commit for parts A and B.

No pilot job runs now. Still: do not write `data/cs2` or `data/backtests`. Tests write only to pytest tmp dirs.

Probe scripts and outputs of this plan: `orchestrate/work/planpf2/` (all read-only on `data/`, outputs in that folder).

## 1. What the pack does

1. **Bug 3 (P1), part A.** s03 drops every GRID game whose `sequenceNumber` repeats inside one Series State. A series left with no games gets the status `no_games`. s03 prints the count of dropped games.
   This is **not** the pilot's rule ("keep the last finished game"). On the 7 real series that rule joins a map to the wrong Polymarket market (section 2, fact 3).
2. **Bug 4 (P3), part B.** `write_fills_parquet` writes the `EnrichedFill` columns also when there are no fills. `report_seeds.py` and the other fills readers then work on a 0-fill run. Output with fills stays byte-identical.
3. **Part C (outside the spec, needs an orchestrator yes/no).** s04 links a GRID map to a bo3 map only when the bo3 map number equals the GRID game number. 41 linked full-period maps fail this today, and at least 8 of them sit on the wrong market. Section 7.

Every fix gets a test (section 5).

## 2. Facts I checked — bug 3

### The 7 real series

1. `probe_dup_series.py` reads each Series State from `data/cs2/raw/grid/` and the bo3 maps of the same match from `data/cs2/raw/bo3/` (output `probe_dup_series.out`). Polymarket winners come from `data/cs2/universe/markets.parquet`.
2. Which GRID row is the real map, per repeated `(series, sequenceNumber)`:

   | Series | GRID rows of the repeated number (map, score, finished, rounds) | bo3 match: real map | Real map is |
   |---|---|---|---|
   | 2887611 (bo3, game 1) | overpass 12:5 False 20; nuke 13:7 True 20 | 108375: #1 overpass 18 rounds, **#2 nuke 20 rounds**, same fingerprint as the GRID nuke | **both** rows are real maps. The GRID nuke is map **2**, mislabeled as 1 |
   | 2891084 (bo3, game 3) | anubis 0:0 False 4; anubis 0:0 False 4; anubis 4:3 True 7 (Leo won) | 109422: #3 anubis 30 rounds, **Lazer Cats** won. Polymarket series winner: Lazer Cats | none. The "finished" 4:3 row has the wrong winner |
   | 2902652 (bo3, game 2) | ancient 9:5 False 18; mirage 6:6 True 12 | 111776: #2 **ancient** 23 rounds, ENCE Academy won | the **first** (unfinished) row. The "finished" mirage 6:6 is junk |
   | 2904174 (bo1, game 1) | nuke 10:6 False 19; ancient 8:13 True 21 | 111420: #1 **ancient** 21 rounds, same fingerprint | the last finished row |
   | 2910893 (bo3, game 2) | dust2 0:0 False 1; dust2 0:1 False 4; nuke 16:19 True 35 | 112501: #2 nuke **18** rounds | none matches (GRID rounds 35, 26 finished) |
   | 2912675 (bo3, game 2) | inferno 0:0 False 1; inferno 12:16 True 22 | 113619: #2 inferno 28 rounds, Vitality won | the last finished row (GRID lost 6 rounds) |
   | 2925859 (bo3, game 3) | overpass 0:0 False 1; overpass 14:11 True 27 | 117373: #3 overpass, 3 rounds in bo3 | the last finished row |

   Two shapes exist. A restart has 0:0 stubs on the same map and then the real game (2891084, 2912675, 2925859, 2910893). A mislabel has two different maps on one number (2887611, 2902652, 2904174). In the mislabel shape the last finished row is the real map once (2904174), a different real map once (2887611), and junk once (2902652).
3. `probe_s04_rules.py` runs the s04 decision (`collect_grid_maps`, `load_bo3_pool`, `build_link_table`) in memory on the 7 series for three rules (output `probe_s04_rules.out`):

   | Rule | Game rows | Linked | Wrong market | Junk GRID rows kept |
   |---|---:|---:|---:|---:|
   | all games (today; `validate_grid_tables` raises) | 23 | 7 | 1 | 4 |
   | last finished per number (pilot proposal) | 14 | 7 | **1**: 2887611 nuke (bo3 #2) on `cs2-lk-ace1-2026-01-24-game1` (map 1 market) | 2 (2891084 anubis 4:3 with the wrong winner, 2902652 mirage 6:6) |
   | **drop every game of a repeated number** | 7 | 5 | 0 | 0 |

   The drop rule loses one correct link: 2904174 ancient (bo1, series market). Every other kept-by-"last" row is `grid_rounds_incomplete` or `no_bo3_map` anyway.
4. The map 1 market of 2887611 traded on overpass (12:35–13:20). The nuke started at 13:26, so under "last" the catalog joins nuke game state to a decided market. Without bo3 the two mislabel cases (2887611 wrong, 2904174 right) look the same in GRID: same `sequenceNumber`, finished last row, the last row starts at the exact `ended_at` of the first row.
5. Full period, cache only, drop rule (`probe_drop_rule_full.py`, output `probe_drop_rule_full.out`, 6 940 matched links):
   - dropped game rows: 16, all in `ok` series (0 in `no_games` and `grid_team_mismatch` series);
   - statuses: ok 5 862 → **5 860**, no_games 432 → **434** (2887611, 2904174), no_series_state 642, grid_team_mismatch 4;
   - games 13 448, rounds 282 343. `(series_id, game_number)`, `game_id`, and `(game_id, round_number)` are unique.
   - The pilot's scratch run without the 7 series had 13 441 games and 282 184 rounds. The 7 series add 7 rows and 159 rounds.
6. s04 on the pilot scratch tables (`work/scratch_full/bo3/links.parquet`): 13 441 maps, linked 11 093, no_bo3_map 1 198, excluded_series_rule 684, grid_rounds_incomplete 445, ambiguous_bo3 21. No bo3 game id of the 7 series is linked there, so the 5 new links do not collide in `release_shared_bo3_maps`.

### Code

7. `rows_for_series` (`src/cs2/s03_fetch_grid_rounds.py:382-397`) adds every game of `state["games"]`. `validate_grid_tables` (`:461-469`) raises on a repeated `(series_id, game_number)` (`:466-467`).
8. `decide_series_status` (`:370-379`) returns `no_games` for an empty `state["games"]`. s07 calls it for every matched series without game rows (`src/cs2/s07_link_readiness.py:83-103`) and prints `s03 <status>`. If the filter stays only in `rows_for_series`, s07 prints `s03 ok` for 2887611 and 2904174.
9. `GridTables` (`:176-181`) is built only in `build_grid_tables` (`:437-458`) and in `tests/test_cs2_grid_rounds.py:560-565`.
10. Consumers of `games.parquet` / `rounds.parquet`: s04 (`s04_link_bo3.py:747-748`), s05 (`s05_fetch_bo3_replays.py:247-248`), s06 (`s06_publish_catalog.py:562`), s07 (`s07_link_readiness.py:219`). They all key on `game_id`. A dropped game is absent everywhere, like a game GRID never had.

## 3. Facts I checked — bug 4

1. The only writer of `fills.parquet` is `write_fills_parquet` (`src/backtest/postprocess.py:815-818`): `pd.DataFrame([asdict(fill) for fill in fills])`. With 0 fills this frame has no columns. Callers: per-run checkpoint (`src/backtest/run.py:1074`), shard merge (`run.py:1240`), shared archive (`src/backtest/shared_archive.py:112`). Dota, LoL, and CS2 all go through it.
2. The pilot file `data/backtests/cs2_maker/validation_join_delta02_x015_cutend_p45_pilot4/seed0/fills.parquet`: 0 rows, empty arrow schema, 636 bytes.
3. Readers that break on that file:
   - `scripts/report_seeds.py:238` `fills["match_id"]` → `KeyError` (the pilot crash);
   - `scripts/cleanup_backtest_intermediates.py:80` `pq.read_table(path, columns=["match_id", "ts_ns"])` → `ArrowInvalid: No match for FieldRef.Name(match_id)` (`bug4/probe_writer.out`);
   - `src/backtest/inspect/tape.py:184` reads `_FILL_COLUMNS` with a `match_id` filter: same failure class.
   `read_fills_checkpoint` (`postprocess.py:821-835`) returns `[]` on an empty frame, and `find_parquet_file_problems` (`src/backtest/quote_store.py:182-196`) checks columns only when rows > 0. Both work with either file.
4. Repro on a scratch copy of the pilot seed dir (`bug4/before`): `report_seeds.py` exits 1 with `KeyError: 'match_id'`. On a second copy whose `fills.parquet` I rewrote with the planned writer (`bug4/write_typed_empty.py`), it exits 0 and prints the seed table, the three time thirds (94 maps each, 0 traded) and the `grid_v1` cohort with 282 maps (`bug4/after.out`).
5. `bug4/probe_writer.py` (output `bug4/probe_writer.out`):
   - 3 fills (BUY/SELL, NaN, other strings): old writer bytes == new writer bytes (24 332 bytes). So Dota and LoL runs with fills keep identical files;
   - 0 fills, new writer: 0 rows, columns == `fields(EnrichedFill)`. `read_fills_checkpoint` → `[]`, `find_parquet_file_problems` → `()`, `_MatchSums.add` → `{}`, `load_match_fills` → 0 rows.
6. The empty columns are arrow `null` type (pandas `object`). Every reader above works with that. `asdict` keys follow `fields()` order, so `columns=` does not reorder non-empty frames.

### Baseline

Fast suite at `a804a623` (task command): `3380 passed, 64 warnings, 2 subtests passed in 32.50s`.

## 4. Decisions

1. **Drop the whole repeated number (bug 3).** A new function in s03 keeps only games whose `sequenceNumber` occurs once in the Series State. Reason: fact 2.3. "Last finished" keeps 1 wrong market join and 2 rows with false results; "drop" keeps none and loses 1 correct map of ~11 000. This departs from the orchestrator text "keep one game per sequenceNumber". The text also says "verify the choice against bo3/s04 on the 7 real series": that check rejects "keep one".
2. **One filter, two callers.** `decide_series_status` and `rows_for_series` both use the filter. A series with only repeated numbers is `no_games` in s03 and in the s07 reasons. No new status.
3. **Count.** `GridTables` gets `shared_number_games: int`, summed over every Series State that exists (`state is not None`). `print_grid_summary` prints `games with a shared game number: N` after `unfinished series`. Full period: 16.
4. **Keep `validate_grid_tables` as is.** It stays the guard for the table invariant.
5. **Fix bug 4 at the writer.** `write_fills_parquet` passes `columns=[field.name for field in fields(EnrichedFill)]`. One line in the shared writer fixes `report_seeds.py`, `cleanup_backtest_intermediates.py`, and `inspect/tape.py` for 0-fill runs. No change in `report_seeds.py`.
6. **No arrow schema per column.** Typed empty columns (int64, float64) would need a dtype map per field. Every reader works with `null` columns (fact 3.5). Not worth the code.
7. **Old 0-fill files stay broken.** The fix applies to runs written after it. The pilot's `..._pilot4/seed0/fills.parquet` keeps 0 columns. Rerun the backtest or rewrite that file only if someone needs its report.
8. **No new docstrings, no new comments, no new suppressions** (STEP-013 user rule, review config). Names are verbs.
9. **One commit** for A and B, message like `Drop CS2 GRID games that share a game number and write typed empty fills.` with the `Co-Authored-By` line. If the orchestrator accepts part C, it goes in the same commit.

## 5. Changes by file

### `src/cs2/s03_fetch_grid_rounds.py` (bug 3)

- New function next to `decide_series_status`:
  ```python
  def drop_shared_number_games(games: list[Cs2Game]) -> list[Cs2Game]:
      numbers = Counter(game["sequenceNumber"] for game in games)
      return [game for game in games if numbers[game["sequenceNumber"]] == 1]
  ```
  `Counter` is already imported (`:2`).
- `decide_series_status`: `if not drop_shared_number_games(state["games"]): return "no_games"` in place of `if not state["games"]:`.
- `rows_for_series`: `for game in drop_shared_number_games(state["games"]):`.
- `GridTables`: new field `shared_number_games: int` after `unfinished_series`.
- `build_grid_tables`: `shared_number_games = 0`; after the `unfinished_series` lines:
  ```python
  if state is not None:
      shared_number_games += len(state["games"]) - len(drop_shared_number_games(state["games"]))
  ```
  Pass it to `GridTables`.
- `print_grid_summary`: `print(f"games with a shared game number: {tables.shared_number_games}")` after `unfinished series`.

### `src/backtest/postprocess.py` (bug 4)

- `write_fills_parquet`:
  ```python
  columns = [field.name for field in fields(EnrichedFill)]
  frame = pd.DataFrame([asdict(fill) for fill in fills], columns=columns)
  ```
  `fields` (`:10`) and `EnrichedFill` (`:26`) are already imported; `read_fills_checkpoint` uses both. Keep the existing docstring as is.

### Tests

| Fix | File | Test |
| --- | --- | --- |
| Bug 3 rule, count, status | `tests/test_cs2_grid_rounds.py` | `test_shared_game_number_drops_every_game_of_that_number`. Series `SERIES_ID`: game 1 = `sample_games()[0]` (24 rounds); game 2 three times (`make_game` ids `g2-a`, `g2-b`, `g2-c`, maps dust2, dust2, nuke, segments 1/4/35 by `make_segment(n, round_started_at(n), "PT100S", "counter-terrorists", True, True)`, then set `finished = False` on the first two). Series `"3000005"`: game 1 twice (nuke `finished=False` 19 segments, ancient `finished=True` 21 segments). Write both states, `build_grid_tables(raw_dir, [two SeriesLinks])`. Assert: `statuses["ok"] == 1`, `statuses["no_games"] == 1`; `list(tables.games["game_id"]) == ["g-mirage"]`; `len(tables.rounds) == 24` and every round row has `game_id == "g-mirage"`; `tables.shared_number_games == 5`; `validate_grid_tables(tables)` passes; `print_grid_summary` prints `games with a shared game number: 5`. Before the fix: 5 game rows, `validate_grid_tables` raises. |
| Typing fallout | `tests/test_cs2_grid_rounds.py` | `test_summary_counts_maps_that_have_no_round_rows` (`:560`) builds `GridTables(...)` by hand: add `shared_number_games=0`. |
| Bug 4 | `tests/test_report_seeds.py` | `test_time_rows_with_zero_fills`. One seed dir: `results.parquet` as in `test_time_table_uses_equal_match_thirds_and_turnover` (6 maps) with `buy_fills=0`, `buy_quantity=0.0`; fills written by the real `write_fills_parquet(report_dir=seed_dir, fills=[])` (import from `backtest.postprocess`). `load_seed_frames(tmp_path, (0,), TIME_RESULT_COLUMNS)` and `build_time_rows(frames, (0,))` → 3 rows, `traded == 0`, `turnover == 0.0`, `buy_300s == 0.0` on each. Before the fix: `KeyError: 'match_id'`. |

## 6. Edge cases

1. **A restart whose real game has a full fingerprint.** The drop rule loses that map. On the 7 real series this happens once (2904174). If restarts grow, part C (bo3 number check) makes "last finished" safe, and the rule can change then.
2. **A Series State where every number repeats.** Status `no_games`. It is counted in s03 and s07 like a series without games.
3. **A repeated number in a `grid_team_mismatch` series.** Counted in `shared_number_games`; its rows are dropped anyway. Full period: 0.
4. **`sequenceNumber` repeats with the same `game_id`.** Not seen. `validate_grid_tables` still raises on a repeated `game_id`.
5. **Repeated number in the decider of a series.** The series market of that map has no map row (2891084, 2925859). Same as a `grid_rounds_incomplete` map today.
6. **Some shards with fills, some without.** The merge reads each shard with `read_fills_checkpoint` (empty → `[]`) and writes the union. Works before and after.
7. **Resume from an old no-column fills file.** `read_fills_checkpoint` returns `[]` for an empty frame, as today.

## 7. Part C — bo3 map number guard in s04 (needs an orchestrator decision)

Not part of bugs 3–4. Do it only if the orchestrator writes "PILOT-FIX-2 part C: yes" in `progress.txt`.

### Facts

1. `probe_number_match.py` on the pilot full-period s04 table (`work/scratch_full/bo3/links.parquet`, output `probe_number_match.out`): **41 of 11 093** linked maps have bo3 `number` ≠ GRID `game_number`.
2. `probe_number_side.py` (output `probe_number_side.out`) compares the Polymarket winner of the market s04 picked with the GRID winner of the map. **8 rows certainly sit on the wrong market** (different winner): 2902996 g1, 2921072 g1, 2945101 g1, 2963124 g1, 2990158 g1 and g2, 2994673 g1 and g2. In those series GRID lost map 1 and numbers maps 2–3 as 1–2. The other 33 have the same winner on both maps or names I cannot compare (2 markets have no winner).
3. This is the bug-3 failure without a repeated number: the market comes from `game_number` (`s04_link_bo3.py:308`), the bo3 link from fingerprint and time only (`:583-594`).

### Change

- `Bo3Map`: new field `number: int`; `build_bo3_map` sets `number=game["number"]`.
- `Cs2Bo3LinkStatus` and `STATUS_NAMES`: add `"bo3_number_mismatch"` before `"linked"`. s06 `S04_DROP_STATUSES` and the s07 reasons pick it up from `STATUS_NAMES` (`s06_publish_catalog.py:54-56`).
- `decide_bo3_link`: in the `count == 1` branch, if `candidates[0].number != grid_map.game["game_number"]`, return `LinkDecision(status="bo3_number_mismatch", candidate_count=1, bo3_map=None)`.
- Test in `tests/test_cs2_link_bo3.py`: copy of `test_one_round_difference_is_no_bo3_map` with the matching fingerprint and `bo3_game["number"] = 2` against GRID game 1 → status `bo3_number_mismatch`, `bo3_game_id` NA.

### Effect

Full period after A + C: linked 11 098 − 41 = **11 057** (the 5 new links of section 2 all have equal numbers). Loss 0.37% of linked maps; removes at least 8 wrong-market maps from training and the backtest.

## 8. Checks

Run from `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`. Write outputs to `orchestrate/work/<impl agent>/`.

1. Fast suite (task command):
   `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`
   Baseline 3380 passed. Expect 3382 (3383 with part C), 0 failed.
2. `uv run basedpyright` — 0 errors. `uv run ruff check` and `uv run ruff format --check` on every changed file.
3. Bug 3 on real data, cache only, writes nothing:
   `PYTHONPATH=src uv run python /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-18-cs2-follow300/work/s03_dup_games.py data/cs2/links data/cs2/raw/grid <impl work dir>/s03_dup_games_after.csv`
   Expect: `games 13448, rounds 282343`, statuses `ok 5860, no_games 434, no_series_state 642, grid_team_mismatch 4`, `duplicated (series_id, game_number): 0 series, 0 game rows`.
4. Bug 4 on a scratch copy (not on `data/backtests`):
   - copy `data/backtests/cs2_maker/validation_join_delta02_x015_cutend_p45_pilot4/seed0` into `<impl work dir>/bug4/seed0`;
   - rewrite its `fills.parquet` with the new writer: `PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python -c "import sys; from pathlib import Path; from backtest.postprocess import write_fills_parquet; write_fills_parquet(report_dir=Path(sys.argv[1]), fills=[])" <impl work dir>/bug4/seed0`;
   - `PYTHONPATH=src:../prediction-market-backtesting:scripts uv run --group backtest python scripts/report_seeds.py <impl work dir>/bug4 --expected-seeds 1` → exit 0, three time rows with 94 maps each, cohort `grid_v1` 282 maps.
5. Goldens: not in the task command. Non-empty fills files are byte-identical (fact 3.5). If the orchestrator applies the STEP-013 rule for shared backtest code, run the goldens once: expect 19 passed, 2 failed (the known `lol_*` seed-0 smokes, `FileNotFoundError` for `data/lol/processed/datasets/backtest_audit.parquet`).
6. `git status` clean after the commit except the untracked `data/cs2/`; `git log -1` shows the `Co-Authored-By` line. No push.

## 9. Risks and open questions

1. **Departure from the orchestrator text** (decision 1): drop, not keep-one. Cost 1 correct map (2904174); benefit 1 wrong market join and 2 false GRID results removed.
2. **GRID numbering is wrong also without repeats** (part C facts). Without part C, the full catalog keeps at least 8 maps on the wrong market. That is the larger data problem; bug 3 is 1 such map.
3. **2910893 map 1** (not repeated) is anubis 17:11 in GRID and ancient 17 rounds in bo3. GRID map names can be wrong too. It is `grid_rounds_incomplete`, so it does not reach the catalog.
4. **After the fix, pilot task #3** reruns s03 and s04 from the cache. Expect s03 as in check 3 and `games with a shared game number: 16`. Expect s04: 13 448 maps, linked 11 098, grid_rounds_incomplete 447, no_bo3_map 1 198, excluded_series_rule 684, ambiguous_bo3 21 (with part C: linked 11 057, bo3_number_mismatch 41). s04 without `--fetch` needs the bo3 days of the 7 series; `probe_dup_series.py` read them from the cache.
5. **The old pilot `fills.parquet` stays without columns** (decision 7). `report_seeds.py` on that dir still fails until the file is rewritten or the run repeats.

## 10. Not in this pack

- Any rule that renumbers GRID games from bo3 (would recover 2887611 nuke as map 2 and the 8 shifted maps). Part C only drops them.
- A per-column arrow schema for empty fills.
- Rewriting the pilot's 0-fill `fills.parquet` or rerunning s03/s04 on `data/cs2`: pilot task #3 does the reruns.
- s03 GRID 503 outer retry (STEP-015), check 1 rule change, stale round_state summaries 166843 and 166847 (user decisions before STEP-015).
