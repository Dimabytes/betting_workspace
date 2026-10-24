# FIX-001: GRID idle close, soft CS2 feed gap, per-game restart check

Fixes found after STEP-011, before STEP-012 (VPS rollout). Repo: `esports-trader`, branch `main`.
Evidence tape: `data/cs2/live/cs2-damaju-hachae-2026-10-21/` (map 3).

## Fix A. GRID idle close must reconnect, not end the feed

Problem. `src/trader/grid_live_feed.py:128-129`: after a clean socket close, `ticks()` returns.
`MatchWorker.run` exits, the host restarts the worker after 60 s backoff (`wallet_host.py:1495`, `RESTART_BACKOFF_SECONDS = (60, 120, 240)`, `MAX_CRASH_RESTARTS = 3`).
For CS2 the new reducer sees score > 0 and sets `LATE_JOIN`: no more calls on the map.
GRID closes a socket after 120 s of silence: 73 of 78 closes on the tape, one inside map 3 at 2026-10-21T21:16:46.620Z during the R2 pause.
The `return` came from commit `5545ca2b` ("Stop GRID test sockets from filling tmpfs with a reconnect archive loop"): test sockets close at once.

Change.

- When the socket closes cleanly, the map is not finished, and the socket was silent for at least `IDLE_CLOSE_SECONDS` before the close, treat it like a socket fault: call `self._reducer.note_feed_gap()`, yield its tick if not None, log one warning, sleep `RECONNECT_SECONDS`, and reconnect with the same reducer. Count it in `failures` like a fault.
- Silence = time since the last received frame, or since connect when no frame arrived. Use a monotonic clock.
- `IDLE_CLOSE_SECONDS = 60.0` lives next to `RECONNECT_SECONDS` in `src/trader/grid_widgets.py`.
- A clean close with shorter silence keeps today's `return`, so test sockets do not loop.
- This path is shared by Dota/LoL. For them `note_feed_gap` only arms `PriorAnchorClock.bridge_gap` before the −85 s freeze and is a no-op on a live map, which is what an abnormal close already does today.

Tests (no real 60 s sleeps: inject the clock or patch the constant):

- clean close after ≥ `IDLE_CLOSE_SECONDS` of silence on an unfinished map → reconnect, same reducer instance, `note_feed_gap` called once;
- clean close right after frames → `ticks()` returns, no reconnect;
- clean close after the finished snapshot → returns as today.

## Fix B. A CS2 feed gap must not ban the rest of the map

Problem. `src/trader/cs2_feed.py:187-190`: `note_feed_gap` calls `_lose(HistoryLoss.FEED_GAP, ...)`. `_due_for` then returns None for the rest of the map (`:532`).
On the tape the abnormal close at 2026-10-21T21:39:54.985Z fell inside R12: score 6:5 before and after, kills [0,1] → [2,4], plant visible in the first frame after reconnect, R12 ended at 21:40:07 after reconnect.
GRID frames are full state (cumulative kills, alive, plant flags, loadouts), so nothing was lost; the ban cut R13–R21.

Real losses are already caught by existing checks: `SCORE_JUMP` / `SCORE_ROLLBACK` (`_on_score_change`), missed end at live start (`_missed_end_at_live_start`), table round jump or a previous round without final (`_gate_round`), incomplete round facts (`_facts_incomplete`).

Change. `note_feed_gap` marks the gap in the round in progress (`self._score_a + self._score_b + 1`) instead of losing history. Two cases the checks above do not cover must make that round incomplete:

1. The gap covered the freeze end. `_absorb_freeze` would take the loadout from the first post-gap frame, which is lower than the freeze-end value (tape R12: 26200 → 23200 → 18200). Rule: if the round's loadout is not taken yet, reset `freeze_open_seen` to False, so the loadout is taken only after a freeze-open frame is seen again after the gap. If the gap covered the freeze end, the loadout stays None and `_facts_incomplete` bans the round.
2. The gap covered the round end. `_seen` would pick a pre-gap sample, so `seen_kills` is too low and the cash estimate counts extra late kills. Rule: drop the round's pre-gap samples (`draft.samples.clear()`), so `_seen` uses only post-gap samples; with no post-gap sample at least 1 s before the final, `seen` is None and the round is incomplete.

Delete `HistoryLoss.FEED_GAP` if nothing produces it anymore, and update the tests that use it (`tests/test_cs2_feed.py`, `tests/test_trader_round_signals.py`).

Replay parity. `src/trader/replay_cs2_rounds.py` `_note_disconnect` only records the marker. After Fix A, live calls `note_feed_gap` on every close (clean idle or abnormal). The replay must do the same on a `disconnect` marker after the lock and route the returned tick.

Tests:

- real tape map 3 with both disconnects replayed through `note_feed_gap`: calls exist for R13–R21; the R12 facts are complete;
- synthetic gap inside a round after the freeze end and before the round end, with a post-gap sample → round facts complete, next due emitted;
- synthetic gap covering the freeze end → that round `MISSED_ROUND`, no later dues;
- synthetic gap covering the round end (no post-gap sample ≥ 1 s before final) → that round `MISSED_ROUND`;
- synthetic gap followed by a 2-point score jump → `SCORE_JUMP` (existing behavior kept).

## Fix C. `--restart-check` per game

Problem. `src/dashboard/summarize.py:442-447` `restart_block` uses the Dota/LoL `BUY_CUTOFF_SECOND = 480` for every game.
An open CS2 map without a position after second 480 reads SAFE. A restart then rejoins late: the reducer sets `LATE_JOIN`, and the map is lost.

Change.

- `restart_block` takes the game. For `cs2` any open session blocks: position first (`position`), else `map_open`. Dota/LoL keep `position` / `in_window` (< 480) as today.
- Game comes from `game_from_meta(meta)` (`summarize.py:248`).
- Output: one verdict line per game, always all three, then the overall line. Exit 1 when any game is UNSAFE (one trader process serves all games, so a restart touches all):

  ```
  restart_check dota SAFE
  restart_check lol UNSAFE position <archive> second=612 pos_yes=12 pos_no=0
  restart_check cs2 UNSAFE map_open <archive> second=905 pos_yes=0 pos_no=0
  restart_check UNSAFE
  ```

  A `no_signal` open session also goes under its game. With no blocks: three `SAFE` game lines, then `restart_check SAFE`.
- `summarize.py` runs on the VPS with host `python3` outside the venv. Do not import anything heavy (no `trader.game_profile`, no lightgbm). `python3 src/dashboard/summarize.py --self-check` with the system python works today; keep it working.
- Extend `check_restart_block` (`--self-check`) with CS2 cases: flat CS2 at second 100 and 5000 → `map_open`; CS2 with 10 shares → `position`; Dota/LoL cases unchanged.
- Update `../betting_workspace/.shared-skills/vps-trader/SKILL.md` line 129 (and the command list if needed): per-game lines, the CS2 rule, the overall line is the gate.

## Checks (all must pass before each commit)

- `.venv/bin/ruff check src tests scripts`, `.venv/bin/ruff format --check src tests scripts`, `.venv/bin/basedpyright`
- targeted pytest for touched files, then `PYTEST_N=8 make test`
- `python3 src/dashboard/summarize.py --self-check` with the system python
- Dota/LoL differentials vs the STEP-011 baselines (scripts in `../betting_workspace/investigations/2026-10-23-cs2-live-validation/work/step-005/`): GRID reducer and signals must be byte-equal; core replay of the 13 sessions must have 0 plan/wake/digest mismatches
- CS2 tape replay CLI on map 3 (`trader.replay_cs2_rounds`), with `TG_BOT_API_TOKEN=disabled TG_CHAT_ID=disabled` in front: report the call ledger before/after, R13–R21 present after

Known flakes: `test_upcoming_rows_from_sidecars` fails 23:00–00:00 Berlin; `test_follow300_replay[dota_9007700576]` flaked twice under xdist. Rerun alone before calling a failure.

## Commits

One commit per fix (A, B, C) in `esports-trader`. The SKILL.md change is one commit in `betting_workspace` (stage only that file).
