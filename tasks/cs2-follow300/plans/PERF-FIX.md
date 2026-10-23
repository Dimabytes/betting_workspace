# PERF-FIX — fewer clock timers and strategy passes (F1–F3), one CS2 selection per run (F4)

Code: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader-cs2`, branch `cs2-signal`. Planned at `f66c7b40` (PILOT-FIX-3 part A). At 21:50 HEAD is `62626b84` (touches only `src/cs2/s05_fetch_bo3_replays.py`, `tests/test_cs2_replays.py`), and part B sits uncommitted in the worktree. Implementation starts on top of the PILOT-FIX-3 part B commits.
Spec: fixes F1, F2, F3, F4 of `orchestrate/reports/btperf-1-btperf.md`. F5 and the signal cache are out of scope.
This is not a feature.json step. Do not change `passes`.

Do not write `data/cs2` or `data/backtests`. Oracle runs write only to `orchestrate/work/<implementer>/`. Other agents edit the same worktree: run every oracle from a `git archive` snapshot, never from the live `src/`.

Scratch of this plan: `orchestrate/work/planperf/` (`probe_ts.py`, `probe_sig.py`, `bt_prof.py` with flag passthrough and a `lol` branch, `run_checks*.sh`, `run_lol.sh`, `src_head/`, `lol_root/`, `logs/`, `out/`, Nautilus 1.226.0 sources in `nt/`).

## 1. Goal

CS2 warm `run_batch` per map 42.55 s → 10.77 s with F1+F3 (measured, fact 21); cold map ~44.9 s → ~14 s (btperf §5). Dota warm match 3.58 s → ~2.1 s (btperf §5); Dota Oddin archive maps 41.99 s → 6.28 s mean (fact 20). `results.parquet`, `fills.parquet` and `quote_events.parquet` stay byte-for-byte equal (`compare_runs.py`: `ALL EQUAL`) on Dota `grid_v1`, Dota archive schedules, LoL, and CS2 with orders.

F4 removes the repeated CS2 map gate: today every shard and every merge of every seed runs it (3 seeds × (4 shards + 1 merge) = 15 gates per `run_seeds.sh`, ~1 406 s each: 4 177 input maps × 0.337 s, btperf report lines 127-130, 200-201).

## 2. Facts I checked

### Nautilus 1.226.0 timer order (source in `work/planperf/nt/`)

1. `TestClock.set_time_alert_ns` builds `self.timer_names` (all active names, split and sorted) on every call for `Condition.not_in` (`.venv/.../nautilus_trader/common/component.pyx:651`, `:613`). Cost grows with active timers (btperf `clock_bench.py`: 7.3 µs at 5, 154.4 µs at 3 000).
2. The backtest heap orders handlers by `(ts_event, name, ts_init, event_id)` (`nt/timer.rs:293-304`; `nt/accumulator.rs` `BinaryHeap<Reverse<TimeEventHandler>>`; upstream test `test_accumulator_pop_same_timestamp_in_name_order`). Insert order does not matter.
3. A time alert event has `ts_init == ts_event` (`nt/timer.rs:467-472`). Our timer names are unique while active (Cython rejects a duplicate name, `component.pyx:651`). So the pop order is fully fixed by `(ts_event, name)`.
4. All three engine loops re-advance every clock after each callback, so an alert set inside a callback joins the heap before the next pop (`backtest/engine.pyx` `_advance_time` ~1747-1766, `_flush_accumulator_events` ~1918-1927, `_process_raw_time_event_handlers` ~1985-1995). `_process_next_timer` takes the earliest pending timer across clocks.
5. An alert time in the past is moved to `now` and fires at `now` (`nt/clock.rs:310-316`).
6. Venues settle (`_process_and_settle_venues`) only when the popped `ts_event` changes, not between two handlers at the same ns (`engine.pyx` same loops).

### Strategy (`src/backtest/strategy.py`)

7. `on_start` arms one `SIGNAL:{match}:{ts}` alert per unique feed, board and clear timestamp ≤ `game_end_ns` (`:344-358`): 2 925 per CS2 map, 4 761–5 198 feed ticks on the Dota Oddin archive maps (fact 20). `_on_signal_alert` (`:568-574`) does not arm anything itself.
8. Book path: one queue and one `BOOK_RECEIVED:{token}:{received_ns}` alert per token (`:380-404`). `_apply_received_book` (`:406-420`) applies the deltas, sets `_book_ts_ns = now_ns`, and runs `_drive(BookUpdate)`, `_arm_wake`, `_wake_quoter` for every token.
9. `step(BookUpdate)` only replaces `state.books` (`src/strategy/engine.py:52-55`), opens `schedule.dirty_open_ns` if it is `None` (`src/strategy/scheduling.py:42-63`), never evaluates (`should_evaluate`, `scheduling.py:133-151`), and returns `next_wake_ns(final state, now)`. `_drive` skips `_shim_from_core` for it (`strategy.py:869-877`); `_touch_uptime(now)` twice at one ns equals once. `_wake_quoter` twice at one ns equals once (`:590-595`).
10. Our timer prefixes: `BOOK_RECEIVED:`, `BUY_CUTOFF:`, `CANCEL:`, `CANCEL_SEND:`, `FILL_NOTICE:`, `GAME_END:`, `KILL:`, `PLACE_AT:`, `QUOTER:`, `QUOTER_WAKE:`, `RELEASE:`, `SIGNAL:` (`strategy.py:139-143`, `maker_orders.py:23-24`). No name sorts between `BOOK_RECEIVED:0:…` and `BOOK_RECEIVED:1:…`, and `BOOK_RECEIVED:{digits}` takes the same place against every other name.
11. `_books` and `_book_ts_ns` change only in `_apply_received_book` (`:410-415`) in `src/`. Tests write both directly in ~30 places (`tests/test_backtest_maker.py:440-445`, `:677-678`, `:1194-1196`, `tests/adapter_contract_fixtures.py:357-364`, `tests/test_extraction_oracle.py:108-132`). Example: `test_follow300_bad_yes_book_cancels_without_placing` snapshots, then puts a new `_FakeBook` and a new ts, then expects `books is None`.
12. `TokenBook` is built only from the book, `ts_ns` and `token_index`; `measure_rung_depth` is pure (`src/strategy/signals.py:150-155`).

### Data order (F3 relies on it)

13. The Telonex native builder sets `ts_init = ts_event` on every book row (`../prediction-market-backtesting/crates/core/src/telonex.rs:415`, `:611`, `:640`, `:871`; its tests assert it at `:1620`, `:1645`, `:1697`; PMXT the same, `pmxt.rs:840`). The engine feeds data in `ts_init` order. So `received_ns = ts_event + 30 ms` never goes back across the two tokens. Probe `work/planperf/probe_ts.py`: Dota 8909498494 — 165 840 deltas records, 0 with `ts_event != ts_init`, 0 steps back in the merged order; CS2 187809 — 596 406 records, 0 and 0.

### Selection (F4)

14. `main` calls `load_run_selection` in every process (`src/backtest/run.py:2223`); the CS2 branch calls `cs2_inputs.load_cs2_selection` (`run.py:1620-1637`), which runs the gate `_select_ids` → `_gate_map` (`src/backtest/cs2_inputs.py:224-308`). btperf: 0.337 s per validation map, 97.96 of 101.7 s in `read_window_levels`.
15. `run_seeds.sh` starts `SHARDS` shard processes and one `--merge-shards` process per seed, each with `--validation --name NAME ... "${EXTRA[@]}"` (`scripts/run_seeds.sh:68-90`). Seeds are re-invocations of the script with the same `EXTRA` (`:136-142`). The CS2 run has no archive stage (`:132-134`).
16. The gate output is `Cs2EligibleMaps(coverage, eligible_ids, book_rows)` (`cs2_inputs.py:266-308`). It does not depend on the seed, `--limit`, `--match-id`, or any strategy flag. `--limit` and `--match-id` apply after the gate (`:322-330`).
17. Other callers of `load_run_selection`: `src/backtest/replay_inputs.py:236`, `src/backtest/seed0_replay.py:69`, `tests/test_since_match.py:292`, `:317`. `load_cs2_selection` callers: `run.py:1621`, `tests/test_cs2_backtest.py:735`, `:742`, `:749`.
18. `shared.utils.json_io.read_json` / `write_json` exist; `write_json` writes through a `.tmp` file and `replace` (`src/shared/utils/json_io.py:9-22`).

### PILOT-FIX-3 part B lines (plan `plans/PILOT-FIX-3.md` §5.3)

19. Part B edits `cs2_inputs.py` only at `CS2_SIGNAL_COLUMNS` (`:78-87`), `build_cs2_clock_tape` (`:198-215`) and `_prepare_map` (`:431-462`). F4 edits `:266-342` and the import block. No line overlaps. Part B does not touch `strategy.py`, `run.py` or `run_seeds.sh`. The uncommitted part B diff at 21:50 (`git diff -U0`) confirms it: `cs2_inputs.py` hunks at `-85,0`, `-198,3`, `-210,0`, `-213`, `-435`; `tests/test_cs2_backtest.py` hunks at `-125` … `-203`. After part B, `test_thin_book_is_excluded` sits at `:748-766` (was `:731-755`) and F4's `cs2_inputs.py` zone moves down 5 lines.

### Oracle runs for this plan (`work/planperf/run_checks.sh`, `run_checks3.sh`, `run_lol.sh`, `run_checks4.sh`; logs in `work/planperf/logs/`)

Code under test: `git archive f66c7b40` in `work/planperf/src_head` (`lol_root` for LoL). "F1+F3" and "F1+F2+F3" are btperf's monkeypatch flags in `bt_prof.py` (`--chain-signals`, `--coalesce-pair`, `--cache-snapshot`), not the code of section 4. Compare: `compare_runs.py <a>/backtests <b>/backtests`.

20. Dota Oddin archive 9007208887, 9007700576, 9015175653, base vs F1+F2+F3: `ALL EQUAL` (results 3, fills 241, quote_events 13 615). `run_batch` 69.32 / 20.29 / 36.36 s → 10.47 / 3.05 / 5.32 s; strategy Python mean 31.37 → 3.00 s. All three have 0 board ticks, 0 clears, 0 kill gates (`logs/dota_arch_all3.log`). So the 3 golden archive smokes do not cover the board, clear and KILL path.
21. CS2 186774, 187815, 187795, 187809, 188704 vs btperf `out/cs2_warm` (base): F1+F3 `ALL EQUAL`, F1+F2+F3 `ALL EQUAL` (results 5, fills 0, quote_events 97 601). `run_batch` mean: base 42.55 s (btperf), F1+F3 10.77 s, F1+F2+F3 10.64 s. F2 saves 0.13 s per map.
22. Dota has three signal kinds (`src/backtest/results.py:61-76`): `grid_v1` (`feed_source` `''`), `schedule` + `grid`, `schedule` + `oddin`. `data/backtests/dota_maker/LIVE/seed0/results.parquet`: 530 / 261 / 97 matches.
23. `--min-abs-delta 0.005` alone exits 2: `backtest: error: --exit-abs-delta must not exceed the entry floor` (default `EXIT_ABS_DELTA = 0.015`, `src/shared/constants/strategy.py:8`). The CS2 order run needs `--min-abs-delta 0.005 --exit-abs-delta 0.004`.
24. GRID archive schedules carry the board, clear and KILL inputs. `work/planperf/probe_sig.py` on the 40 GRID archive matches of `LIVE/_archive` (`dota_grid_archive_ids.txt`): all 40 have board ticks (20–72) and kill gates (24–74); 20 of 40 have clears (1–8). I picked the 3 with the most clears for section 6.2.
25. CS2 with orders, 187809 and 188704, `--min-abs-delta 0.005 --exit-abs-delta 0.004`, base vs F1+F2+F3: `ALL EQUAL` (results 2, fills 212, quote_events 29 847). All 212 fills are on 187809. `run_batch` 36.50 / 22.46 s → 10.12 / 10.66 s; strategy Python 32.91 / 20.01 s → 7.63 / 7.99 s.
26. Dota `grid_v1`, 8909498494, 8854443395, 8891325877, 8837869969, 8911784562, 8933879286, base vs F1+F2+F3: `ALL EQUAL` (results 6, fills 129, quote_events 29 165). These maps carry board ticks 29–66, clears 0–3 (3 maps > 0) and kill gates 34–73 (`logs/dota_grid_all3.log`). `run_batch` mean 8.10 → 3.48 s.
27. LoL 115564793879469302 and 116634566264113530 (the 2 golden smoke maps), base vs F1+F2+F3, one run per id from `work/planperf/lol_root`: both `ALL EQUAL` (results 1 + 1, fills 12 + 19, quote_events 4 833 + 2 143). Board ticks 19 / 20, kill gates 20 / 21, clears 0. `run_batch` 10.59 / 4.33 s → 3.41 / 1.66 s. The LoL run writes only to `OUT/backtests` (patched `lol_inputs.LOL_MAKER_BACKTESTS_DIR`), a `tempfile` tree and the scratch `XDG_CACHE_HOME`; `src/backtest/lol_inputs.py` has no other file write.
28. Dota GRID archive 9026171638, 8978986214, 9010104415, base vs F1+F2+F3: `ALL EQUAL` (results 3, fills 33, quote_events 32 000). Board ticks 20 / 26 / 43, clears 8 / 7 / 5, kill gates 34 / 36 / 54 (`logs/dota_garch_all3.log`). `run_batch` 61.22 / 8.01 / 13.09 s → 28.21 / 4.89 / 6.95 s. 9026171638 still spends 21.51 s in strategy Python after F1–F3 (46.54 s before; 23 381 quote events). I did not profile it.

## 3. Decisions

1. **F1: chain the SIGNAL alerts.** The strategy computes the sorted unique alert times once in `__init__`. `on_start` arms the first one. `_on_signal_alert` arms the next one as its first statement, before the `_finished` return. So the set of SIGNAL events and every `(ts_event, name)` stay the same as today; only the number of active timers drops (facts 2–6). KILL alerts stay as they are (20–74 per Dota or LoL match, facts 24, 26, 27).
2. **F3: one book queue and one alert for both tokens.** One callback per ns applies every due delta of both tokens, then runs one `BookUpdate` drive, `_arm_wake`, `_wake_quoter`. Same final state (fact 9), same slot in the heap (fact 10), queue stays sorted (fact 13). The alert name loses the token: `BOOK_RECEIVED:{received_ns}`.
3. **F2 is not implemented.** After F1+F3 it saves 0.13 s per CS2 map (10.77 → 10.64 s, 1.2%; fact 21), about 2 minutes of wall time on STEP-015 (3 900 maps, 4 shards). F3 already halves the snapshots, and both tokens change at every ns (btperf: 100% shared timestamps), so the cache misses on every book drive. The cache would add a dataclass, a helper and a non-obvious validity rule. Section 4.3 keeps the design in case the orchestrator wants it anyway.
4. **F4: gate once per `run_seeds.sh`, file shared by every shard and merge of every seed.** Two CS2-only flags: `--write-selection PATH` gates, writes JSON, exits; `--selection PATH` reads the JSON instead of gating. `run_seeds.sh` writes `data/backtests/cs2_maker/_logs/<NAME>/selection.json` before the seeds start, then appends `--selection <file>` to `EXTRA`. Every `run_seeds.sh` call writes the file fresh, so a rerun never reads a stale gate. The file stores the pre-limit gate output; `--limit` and `--match-id` still apply in each process.
5. **F4 keeps signal rows out of the file.** Part B adds `round_dropped` to `CS2_SIGNAL_COLUMNS`; the file holds only ids, coverage and book row counts, so part B and F4 do not meet.
6. **Order of work: F1 → F3 → F4**, an oracle check after each fix (section 6).
7. **Commits: C1 = F1 + F3 (`strategy.py` + tests); C2 = F4 (`cs2_inputs.py`, `run.py`, `run_seeds.sh` + tests).** C1 is shared Dota/LoL/CS2 code and C2 is CS2 only, so each can be reverted alone (as PILOT-FIX-3 part B used B1/B2).
8. **No new docstrings, no new comments, no new suppressions.** Keep the existing comment in `on_start`. Function names are verbs.

## 4. Changes by file

### 4.1 `src/backtest/strategy.py` — F1

- `__init__`: add
  ```python
  self._signal_alerts_ns: tuple[int, ...] = tuple(
      sorted(
          {
              ts_ns
              for ts_ns in (
                  *config.feed_timestamps_ns,
                  *config.board_tick_ns,
                  *config.clear_timestamps_ns,
              )
              if ts_ns <= config.game_end_ns
          }
      )
  )
  self._next_signal_alert: int = 0
  ```
- `on_start`: replace the loop at `:344-358` by `self._arm_next_signal()`. Keep the comment line above it.
- New method:
  ```python
  def _arm_next_signal(self) -> None:
      index = self._next_signal_alert
      if index == len(self._signal_alerts_ns):
          return
      self._next_signal_alert = index + 1
      ts_ns = self._signal_alerts_ns[index]
      self.clock.set_time_alert_ns(
          name=f"SIGNAL:{self._config.match_id}:{ts_ns}",
          alert_time_ns=ts_ns,
          callback=self._on_signal_alert,
      )
  ```
- `_on_signal_alert`: first line `self._arm_next_signal()`, then the existing body.

### 4.2 `src/backtest/strategy.py` — F3

- `__init__`: `self._received_books: deque[ReceivedBook] = deque()` (one deque, not a tuple of two).
- Replace `:380-420` by:
  ```python
  def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
      if self._finished:
          return
      received_ns = int(deltas.ts_event) + BOOK_VIEW_DELAY_MS * NS_PER_MS
      self._received_books.append(ReceivedBook(received_ns=received_ns, deltas=deltas))
      if len(self._received_books) == 1:
          self._arm_book_received()

  def _arm_book_received(self) -> None:
      head = self._received_books[0]
      self.clock.set_time_alert_ns(
          name=f"{BOOK_RECEIVED_PREFIX}{head.received_ns}",
          alert_time_ns=max(head.received_ns, self.clock.timestamp_ns()),
          callback=self._on_book_received,
      )

  def _on_book_received(self, event: TimeEvent) -> None:
      now_ns = int(event.ts_event)
      queue = self._received_books
      while queue and queue[0].received_ns <= now_ns:
          self._apply_received_book(queue.popleft().deltas, now_ns=now_ns)
      if not self._finished:
          out = self._drive(
              event=BookUpdate(now_ns=now_ns, books=self._snapshot_books()), execute=False
          )
          self._arm_wake(out.next_wake_ns)
          self._wake_quoter(now_ns)
      if queue:
          self._arm_book_received()

  def _apply_received_book(self, deltas: OrderBookDeltas, *, now_ns: int) -> None:
      if self._finished:
          return
      instrument_id = deltas.instrument_id
      book = self._books.get(instrument_id)
      if book is None:
          book = OrderBook(instrument_id, book_type=BookType.L2_MBP)
          self._books[instrument_id] = book
      book.apply_deltas(deltas)
      self._book_ts_ns[instrument_id] = now_ns
  ```
- Why the timer set stays the same while `_finished`: today the per-token callbacks keep popping and re-arming after `_finished` (they only skip the apply). The code above does the same, so the BOOK_RECEIVED timestamps, and with them the venue settle points (fact 6), stay as today.
- `on_order_book_deltas` drops `_token_index_of`: the strategy subscribes only its two instruments (`on_start`), so no other id arrives.
- `partial` stays imported (fill notice, delayed place).

### 4.3 `src/backtest/strategy.py` — F2 (not implemented; only if the orchestrator asks for it)

Key point: the cache must be keyed by the book object and its `ts_ns`, not cleared inside `_apply_received_book`. Tests replace `_books[...]` and `_book_ts_ns[...]` directly after a snapshot (fact 11); a cache cleared only on apply returns the old book there. In real runs the key is exact: every apply sets `ts_ns = now_ns`, and after F3 one callback per ns applies all due deltas before the snapshot. `None` results are not cached. Off-grid warnings move into the rebuild (log lines only). Then add the F2 rows of section 5 and rerun section 6.2.

- Module level, next to `ReceivedBook`:
  ```python
  @dataclass(frozen=True)
  class CachedTokenBook:
      book: OrderBook
      ts_ns: int
      token: TokenBook
  ```
- `__init__`: `self._token_books: dict[int, CachedTokenBook] = {}`.
- `_snapshot_books` (`:1272-1305`) becomes:
  ```python
  def _snapshot_books(self) -> BookPair | None:
      tokens: list[TokenBook] = []
      for token_index, instrument_id in enumerate(self._config.instrument_ids):
          book = self._books.get(instrument_id)
          ts_ns = self._book_ts_ns.get(instrument_id)
          if book is None or ts_ns is None:
              return None
          cached = self._token_books.get(token_index)
          if cached is None or cached.book is not book or cached.ts_ns != ts_ns:
              token = self._read_token_book(token_index=token_index, book=book, ts_ns=ts_ns)
              if token is None:
                  return None
              cached = CachedTokenBook(book=book, ts_ns=ts_ns, token=token)
              self._token_books[token_index] = cached
          tokens.append(cached.token)
      if tokens[0].bid >= tokens[0].ask:
          return None
      return BookPair(tokens=(tokens[0], tokens[1]))
  ```
- `_read_token_book(*, token_index, book, ts_ns) -> TokenBook | None` holds today's per-token body (`:1279-1300`): best bid/ask (`None` → `None`), both `_warn_off_grid` calls, sizes, `measure_rung_depth`, `TokenBook(...)`.
- Drop `if len(tokens) != 2: return None`: `instrument_ids` is a 2-tuple (`DotaMakerConfig.instrument_ids`), and every path that leaves the loop appended two tokens.

### 4.4 `src/backtest/cs2_inputs.py` — F4 (lines `:266-342` + imports only)

- Imports: `from dataclasses import asdict, dataclass`; `from shared.utils.json_io import read_json, write_json`.
- After `_select_ids`, add:
  ```python
  def select_cs2_eligible_maps(min_book_events: int) -> Cs2EligibleMaps:
      validation_ids = read_cs2_validation_ids(CS2_SPLIT_PATH)
      catalog = {row.match_id: row for row in read_catalog_rows(CS2_MATCH_CATALOG_PATH)}
      statuses = frame_from_table(
          pq.ParquetDataset(CS2_VALIDATION_DATASET_PATH).read(columns=["match_id", "market_status"])
      )
      return _select_ids(validation_ids, catalog, _ok_match_ids(statuses), min_book_events)


  def write_cs2_eligible_maps(path: Path, maps: Cs2EligibleMaps) -> None:
      write_json(
          path,
          {
              "coverage": asdict(maps.coverage),
              "eligible_ids": list(maps.eligible_ids),
              "book_rows": [maps.book_rows[match_id] for match_id in maps.eligible_ids],
          },
      )


  def read_cs2_eligible_maps(path: Path) -> Cs2EligibleMaps:
      payload = read_json(path)
      eligible_ids = tuple(int(match_id) for match_id in payload["eligible_ids"])
      book_rows = [int(rows) for rows in payload["book_rows"]]
      return Cs2EligibleMaps(
          coverage=ValidationCoverage(**payload["coverage"]),
          eligible_ids=eligible_ids,
          book_rows=dict(zip(eligible_ids, book_rows, strict=True)),
      )
  ```
- `load_cs2_selection(*, match_id, limit, eligible: Cs2EligibleMaps)`: drop `min_book_events`; drop the `_select_ids` call (`:321`); use `eligible` where `chosen` was. It still reads split, catalog, full signal rows and seconds delays as today (`:314-320`).
- If `ValidationCoverage(**...)` fails basedpyright on `Any`, build it field by field with `int(...)`.

### 4.5 `src/backtest/run.py` — F4

- Parser (after `--warm-cache`):
  ```python
  parser.add_argument(
      "--write-selection",
      type=Path,
      metavar="PATH",
      help="cs2: gate the validation maps once, write them to PATH, and exit",
  )
  parser.add_argument(
      "--selection",
      type=Path,
      metavar="PATH",
      help="cs2: read the gated validation maps from PATH instead of gating again",
  )
  ```
- `parse_args`: one check, next to `_check_match_ids_file`: if either flag is set and `args.game != "cs2"` → `parser.error("--selection and --write-selection are cs2-only")`.
- `load_run_selection`: new kwarg `cs2_selection: Path | None = None`, the same default pattern as `validation_dataset`, `lag_seconds`, `match_ids` in this signature (so `replay_inputs.py`, `seed0_replay.py`, `test_since_match.py` stay untouched). CS2 branch:
  ```python
  eligible = (
      cs2_inputs.select_cs2_eligible_maps(MIN_BOOK_EVENTS)
      if cs2_selection is None
      else cs2_inputs.read_cs2_eligible_maps(cs2_selection)
  )
  cs2 = cs2_inputs.load_cs2_selection(match_id=match_id, limit=limit, eligible=eligible)
  ```
- `main`, right after `game: Game = args.game`:
  ```python
  if args.write_selection is not None:
      cs2_inputs.write_cs2_eligible_maps(
          args.write_selection, cs2_inputs.select_cs2_eligible_maps(MIN_BOOK_EVENTS)
      )
      return
  ```
  and pass `cs2_selection=args.selection` to `load_run_selection` (`:2223`).

### 4.6 `scripts/run_seeds.sh` — F4

After the archive line (`:132-134`), before the seed loop:
```bash
if [[ "$GAME" == "cs2" ]]; then
  selection="${log_root}/selection.json"
  run_bt --game cs2 --validation --name "$NAME" --write-selection "$selection" "${EXTRA[@]}" \
    >"${log_root}/selection.log" 2>&1
  EXTRA+=(--selection "$selection")
fi
```
`set -e` stops the script if the gate fails. Seeds re-invoke the script with `"${EXTRA[@]}"`, so every shard and merge gets `--selection`. `run_12_seeds.sh` calls `run_seeds.sh` four times and gates four times (today: 60 times).

## 5. Unit tests

Test command (as PILOT-FIX-3):
`PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q --ignore=tests/test_extraction_oracle.py --deselect tests/test_follow300_replay.py::test_seed0_map_replay_matches_current_policy_smoke`, then `uv run python -m basedpyright`.

| Rule | File | Test |
| --- | --- | --- |
| F1 chain order | `tests/test_backtest_maker.py` | config with feed `(10, 20, 30)` s, board `(15,)`, clear `(20,)` (duplicate), one feed ts > `game_end_ns`. After `on_start` exactly one `SIGNAL:` alert (10 s). Fire `SIGNAL:` alerts with `fire_due_alerts` up to `game_end_ns`: the fired times are `[10, 15, 20, 30]` s; the ts > `game_end_ns` never arms. |
| F1 chain after finish | same | set `strategy._finished = True`, fire the first SIGNAL: the next one is still armed. |
| F3 one drive per ns | same | `on_order_book_deltas` for RADIANT and DIRE with the same `ts_event`: one `BOOK_RECEIVED:` alert. Count `_drive` calls with a wrapper: one `BookUpdate` after firing. Both `_book_ts_ns` equal `received_ns`; `_core.books` holds both new bids. |
| F3 two times | same | RADIANT at 250 ms, DIRE at 260 ms: two alerts, fired in time order, two `BookUpdate` drives. |
| F2 reuse and rebuild (only with F2) | same | `_snapshot_books()` twice with no change: the second `TokenBook` objects are the same objects (`is`). New `_FakeBook` for RADIANT only: token 0 rebuilt, token 1 the same object. New `_book_ts_ns` for DIRE: token 1 rebuilt. |
| F4 file round trip skips the gate | `tests/test_cs2_backtest.py` | `write_selection_files(thick, 25, vary=True)`; `maps = select_cs2_eligible_maps(25)`; write to `tmp_path / "selection.json"`; patch `CS2_TELONEX_ROOT` to an empty dir; `load_cs2_selection(match_id=None, limit=None, eligible=read_cs2_eligible_maps(path))` keeps `(1,)`, `book_rows[1] == 50`, and the same coverage as `maps.coverage`. |
| F4 existing gate test | same | `test_thin_book_is_excluded`: the three calls become `load_cs2_selection(match_id=None, limit=None, eligible=select_cs2_eligible_maps(25))`. |
| F4 flags | `tests/test_backtest.py` | `--game dota --validation --name x --selection p` → `SystemExit` (as `test_parse_args_rejects_warm_cache_with_merge_or_resume`). |
| F4 script | `tests/test_run_seeds_sh.py` | the text holds `--write-selection "$selection"` and `EXTRA+=(--selection "$selection")`. |

Existing tests that must pass unchanged: `test_follow300_book_row_wakes_a_cycle_after_the_view_delay`, `test_follow300_two_book_rows_inside_the_debounce_run_one_cycle`, `test_follow300_bad_yes_book_cancels_without_placing`, the `SIGNAL:` test at `tests/test_backtest_maker.py:1040-1071`, and every test that writes `_books` / `_book_ts_ns`.

## 6. Equality proof (oracle runs, scratch only)

### 6.1 Harness

1. Snapshots: `git archive <B> src config | tar -x -C <W>/src_base` and the same for the PERF-FIX commit into `<W>/src_new` (`<B>` = the last part B commit, `<W>` = `orchestrate/work/<implementer>`). In each, `data` is a symlink to the worktree `data` (as `work/btperf/src_head`).
2. Harness: copy `orchestrate/work/planperf/bt_prof.py` (btperf's script plus flag passthrough after `--` and `board_ticks` / `clears` / `kill_gates` counts). Run it without the `--chain-signals`, `--cache-snapshot`, `--coalesce-pair` flags; the code under test is the snapshot. Caches: copy `work/btperf/cache/{cs2,dota}` into `<W>/cache/` first (warm), never point at btperf's cache. LoL starts from an empty `<W>/cache/lol`.
3. Command, from `esports-trader-cs2`: `PYTHONPATH=<W>/src_<x>/src:../prediction-market-backtesting uv run --group backtest python <W>/bt_prof.py <game> <label> <cache> <ids_file> [-- run flags]`.
4. Compare: `uv run python orchestrate/work/btperf/compare_runs.py <W>/out/<a>/backtests <W>/out/<b>/backtests` → `ALL EQUAL` on all three files.

### 6.2 Runs (base vs new, each pair must print `ALL EQUAL`)

| Path | Ids | Flags | What it proves |
| --- | --- | --- | --- |
| Dota `grid_v1` | 8909498494, 8854443395, 8891325877, 8837869969, 8911784562, 8933879286 | — | grid feed, board ticks 29–66, clears on 3 maps, KILL alerts 34–73, 129 fills (fact 26) |
| Dota GRID archive schedule | 9026171638, 8978986214, 9010104415 (`work/planperf/dota_grid_arch3_ids.txt`) | — | `board_tick_ns`, `clear_timestamps_ns`, KILL alerts: board 20 / 26 / 43, clears 8 / 7 / 5, kill gates 34 / 36 / 54 (`work/planperf/logs/probe_sig.log`, fact 24) |
| Dota Oddin archive schedule | 9007208887, 9007700576, 9015175653 | — | the 3 golden archive smokes; feed only, no board, clears or KILL (fact 20) |
| CS2, no orders | 186774, 187815, 187795, 187809, 188704 | — | btperf set |
| CS2 with orders | 187809, 188704 | `-- --min-abs-delta 0.005 --exit-abs-delta 0.004` (fact 23) | CS2 order path; the base run must have fills > 0 |
| LoL | 115564793879469302, 116634566264113530 | harness `lol` branch (`--game lol --match-id <id>`, one run per id) | LoL path: board ticks 19 / 20, KILL 20 / 21, fills 12 / 19 (fact 27) |

LoL needs data the worktree lacks (`data/lol/processed`, `data/lol/raw`). For the LoL pair, make `<W>/src_<x>/data` a real dir, as `work/planperf/lol_root/data`: a symlink per worktree `data/*` entry, a real empty `data/backtests`, `data/lol/models` → worktree `data/lol/models`, `data/lol/processed` and `data/lol/raw` → `../esports-trader/data/lol/` (read-only). `LOL_DATA_DIR` resolves to `<snapshot>/data/lol` (`src/shared/constants/lol.py:6`, `parents[3]`). The harness `lol` branch already patches `backtest.lol_inputs.LOL_MAKER_BACKTESTS_DIR` to `OUT/backtests`. This layout ran in this plan (fact 27).

### 6.3 Goldens and suite

1. Fast suite and basedpyright (section 5).
2. Goldens, in the worktree after the commit: `PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q tests/test_extraction_oracle.py tests/test_follow300_replay.py`. Only the 2 `lol_*` smokes may fail, and only for missing data. The smokes pin fills and place/cancel events, not `no_quote` rows; section 6.2 covers `quote_events`.

### 6.4 Speed (warm, one process, same machine load for both runs)

`python3 orchestrate/work/btperf/summarize.py <labels>` on the CS2 5-map runs and the Dota grid runs. Expect CS2 `run_batch` mean ≤ 11.5 s (btperf F1–F3 10.41 s, planperf F1+F3 10.77 s) and Dota `grid_v1` 8909498494, 8854443395, 8891325877 ≤ 2.4 s (btperf 2.09 s, planperf 2.03 s).

### 6.5 F4 end to end (CS2, 4 maps 186774, 187815, 187795, 187809)

1. `--write-selection <W>/sel.json`, then `-- --shard 0/2 --selection <W>/sel.json`, `-- --shard 1/2 --selection ...`, `-- --merge-shards 2 --selection ...`, all with one harness label so they share `OUT/backtests`. The harness must not clear `OUT/backtests` when the run flags hold `--shard` or `--merge-shards`.
2. An unsharded run of the same 4 maps without `--selection` → `compare_runs.py` vs the merged run → `ALL EQUAL`.
3. On the pilot inputs (`work/btperf/snap`, 302 maps): `select_cs2_eligible_maps(25)` once, write, read back; `load_cs2_selection(eligible=read)` equals `load_cs2_selection(eligible=select)` on `selected_ids`, `coverage`, `book_rows`. Time the read path (expect < 1 s vs ~102 s for the gate).

## 7. Merge with PILOT-FIX-3 part B

- Implement on top of the part B commits. `strategy.py`, `run.py`, `run_seeds.sh`: part B does not touch them.
- `cs2_inputs.py`: F4 edits the import block and `:266-342` only (fact 19). If part B is still open when C1 is ready, land C1 first and C2 after part B; C1 does not touch `cs2_inputs.py`.
- Part B makes `round_dropped` seconds paused (`build_cs2_clock_tape`). That changes the CS2 pause tape, not the timer machinery; F1–F3 equality holds on part B output too, because base `<B>` already contains part B.

## 8. Order of work

1. F1 in `strategy.py`, unit tests, oracle CS2 5 maps + Dota `grid_v1` 8909498494, 8854443395, 8891325877 → `ALL EQUAL`.
2. F3, unit tests, the same oracle pair; fast suite.
3. Commit C1. Full section 6.2 table, goldens, section 6.4 timing.
4. F4 (`cs2_inputs.py`, `run.py`, `run_seeds.sh`), unit tests, section 6.5. Commit C2.
5. Report with every `compare_runs.py` line, the timing table, and the goldens result.

## 9. Edge cases

1. **Every SIGNAL time > `game_end_ns`, or none at all.** `_signal_alerts_ns` is empty; `_arm_next_signal` returns. Today: no SIGNAL alert either.
2. **SIGNAL times before the clock at `on_start`.** Nautilus moves each to `now` (fact 5). Chained, each next one is set inside the previous callback at the same `now` and fires at `now`, in the same name order as today.
3. **`_finished` set mid-map.** SIGNAL chain continues (decision 1); BOOK_RECEIVED alerts keep popping and re-arming without applying (section 4.2). Both match today's timer set.
4. **One token's book missing at map start.** The single drive stores `_snapshot_books()` as today; today the first per-token drive may store `None` and the second overwrites it in the same ns.
5. **Two deltas of one token in one callback.** Both apply in arrival order before the single snapshot, as today's per-token loop does.
6. **Tests with a fake clock.** `fire_due_alerts` pops by time then list order, not by name. The F1 chain and the F3 single alert both work with it (section 5 rows).

## 10. Risks

- **Harness flags are not the section 4 code.** Facts 20–28 prove the btperf monkeypatches equal, not the final `strategy.py`. Differences: btperf names the pair alert `BOOK_RECEIVED:P:{ns}` (section 4.2: `BOOK_RECEIVED:{ns}`; both sort in the same slot, fact 10) and clears its F2 cache on apply. Section 6.2 must rerun on the real commit.
- **Timing.** Other agents share the machine. Compare speed only between runs made back to back on the same maps and flags; orders add strategy work (188704: strategy Python 7.99 s with orders vs 4.88 s without, facts 25 and 21).
- **Slow Dota maps stay slow.** 9026171638 keeps 21.51 s of strategy Python after F1–F3 (fact 28). Its cost is outside the timer machinery; this plan does not fix it.
- **Nautilus upgrade.** F1 and F3 rely on the `(ts_event, name)` heap order and on re-advance after each callback (facts 2–4). A Nautilus bump must rerun section 6.2.
- **A future loader with `ts_init != ts_event`** would break the merged queue order (fact 13) silently. Today both Telonex and PMXT builders set them equal by construction.
- **`selection.json` lives under `data/backtests/cs2_maker/_logs/<NAME>/`.** A manual shard run without `run_seeds.sh` and without `--selection` gates as today.
