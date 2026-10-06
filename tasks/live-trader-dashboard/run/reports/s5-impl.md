879efa1d
Status: FINAL

# STEP-005 — Сводка игры из signal и архива

Typed, immutable game-summary layer consumed by STEP-006's match page.
One `GameStateReader.read(entry, now_s) -> GameSummary` call projects the
journaled `kind=signal` decision slice plus the selected-map board/table
state folded from a bounded tail of the feed archive — for Dota/Oddin,
Dota/GRID, and LoL/GRID. Read-only: no GRID/Oddin connections, no trader
writes, no legacy full replay on the fast path.

## Changed files (esports-trader, commit 879efa1d)

- `src/dashboard/tails.py` — one stat/read/verify loop now backs both
  `read()` (unchanged `TailView` summary, same cached-object identity on
  unchanged files) and new `read_records()` (`RecordTail` with the parsed
  record tuple). Verified reads update both caches; unverified or raced
  reads return the last stable value. Same 256 KiB / 512-record bounds,
  gzip transparency via `resolve_jsonl`, malformed/truncation flags.
- `src/dashboard/game_grid.py` (new) — `GridFold` folds GRID scoreboard
  and table frames for the pinned map using the existing
  `read_map_scoreboard` / `read_net_worth` / `read_board_sides` /
  `select_board_players` helpers. Locks side orientation on the first
  board, rejects wrong-map / pre-board / wrong-active-map table frames,
  dedupes identical snapshots, reconstructs the table second from board
  clock + feed delay (never the table's own `occurredAt`), tracks
  feed-gone and finished terminals, keeps the last valid board across
  torn tails.
- `src/dashboard/game_oddin.py` (new) — `OddinFold` pipes archived
  websocket records through the real `OddinSnapshotReducer`, honors
  reconnect resets, keeps the last live slice across terminal events,
  passes through per-player alive/respawn/aegis where the payload
  carries them; board/table share the record's receipt time.
- `src/dashboard/game_state.py` (new) — frozen DTOs
  (`GameIdentity`, `DecisionSlice`, `SeriesScore`, `BoardSlice`,
  `PlayerSummary`, `SideSlice`, `Objectives`, `TableSlice`,
  `SourceFacts`, `GameSummary`) plus `GameStateReader`. Per-map state is
  keyed on (archive dir, source, game, map, `yes_is_radiant`, outcome
  names) so a catalog orientation flip rebuilds the fold instead of
  reusing a stale one; fingerprint overlap enables warm incremental
  folding and reconstruction on file replace/truncate. Last
  `kind=signal` journal record projects into `DecisionSlice` with
  missing/invalid -> `None` + notes; signal-vs-archive comparison yields
  `aligned`/`different`/`unknown` conservatively; continuity is
  `cold`/`warm`/`reconstructed` with `archive reconstruction; acceptance
  unknown` surfaced in table notes. Selected-archive-only reads, lock
  for concurrent `read()`, bounded state/meta caches.
- `tests/dashboard_game_fixtures.py` (new) — synthetic GRID/Oddin/journal
  record builders shared by the test suite.
- `tests/test_dashboard_game_state.py` (new) — 44 focused tests covering
  all three feed combos, signal provenance, XP semantics, wrong-map
  rejection, terminal states, timestamp separation, table-second
  reconstruction, bounded/gzip/torn tails, cache identity, concurrency,
  file replacement, same-match-id isolation, no-full-replay, and DTO
  immutability.

## Verification

- `PYTHONPATH=src uv run python -m pytest tests/test_dashboard_game_state.py -x -q`
  -> 44 passed
- `PYTHONPATH=src uv run python -m pytest tests/test_dashboard_game_state.py
  tests/test_dashboard.py tests/test_dashboard_home.py
  tests/test_dashboard_live_hub.py tests/test_dashboard_app.py
  tests/test_grid_feed.py tests/test_lol_grid_feed.py
  tests/test_oddin_feed.py tests/test_oddin_reducer.py
  tests/test_archive_index.py -x -q` -> 253 passed
- `uv run ruff check` -> all checks passed
- `uv run ruff format --check` -> clean
- `uv run python -m basedpyright` -> 0 errors, 0 warnings, 0 notes
- Pre-commit hooks all passed on commit.
- Full suite (backtest-aware env): 2855 passed, 8 failed — all
  pre-existing/environmental (seven `test_follow300_replay` artifact-hash
  mismatches, one `test_trader_session_config` clip-config case); none
  touch `src/dashboard/` or dashboard tests.

## Notes

- `feature.json` not modified (orchestrator sets `passes` after review).
- Progress appended to `tasks/live-trader-dashboard/progress.txt`
  (uncommitted, per brief).
- Not pushed, per workspace git rules.
