# dota_2_model historical (removed SessionSupervisor / Popen)

These notes describe the child-process US-012 supervisor (`_stop_child`,
Popen, quarantined handles) and the Thermos structural pass that still
named `run_session` / `PaperTrading`. Do not reintroduce them.
Production is WalletHost; live rules stay in `.learnings/dota_2_model.md`.

## US-012 review fixes (commit 764b317)

- The durable-final marker is identity-matching: only a strict JSON object
  with an exact-string `match_id == requested_id` and a non-null `final`
  completes. OSError/UnicodeError/JSONDecodeError/RecursionError reads return
  false (never raise), so a corrupt, foreign-id or invalid-UTF-8 match.json
  never suppresses a launch or a backoff retry; `match_archive_dir` ValueError
  for an invalid id still propagates as the path guard.
- Every archive mkdir/spawn OSError and every child poll OSError is one
  bounded unresolved failure: the frozen record/identity is retained, the
  attempt counter increments exactly once, the standard 60/120/240 backoff
  and MAX_CRASH_RESTARTS cap apply, and the same one public type-only
  exhaustion alert fires. A poll OSError first stops the child best-effort
  (terminate -> bounded wait -> kill) so it can never become an unobserved
  orphan.
- `_stop_child` is the one isolated stop helper: terminate, bounded wait
  (TERMINATE_WAIT_SECONDS), SIGKILL only when the wait actually timed out —
  a pid that cannot be signalled or waited on may have been recycled and must
  never be killed. Every terminate/wait/kill OSError/ProcessLookupError is
  contained per step, all supervisor records are still cleared, and one
  failing child can never block the cleanup of the rest or mask the daemon
  cancellation.
- The production launcher spells `shell=False` explicitly (Popen default,
  now pinned and test-asserted).

## US-012 review fixes (commit 965c0da)

- Luna P1: after `child.poll()` raises OSError the old reap path stopped the
  child but unconditionally cleared process/retries, so a same-match
  replacement could launch while the original might still be alive;
  `terminate_all` had the same false-dead clearing. Rule: `_stop_child`
  reports whether the death is PROVEN (reaped wait, ProcessLookupError at any
  terminate/wait/kill/poll step = gone pid — never signal it again, it may be
  recycled; delivered SIGKILL). A generic OSError leaves liveness unknown and
  the handle must be kept.
- An unproven stop retains the process handle (the `quarantined` FIELD is gone;
  a non-null `record.process` is the retained-handle fact — see the structural
  pass section): reconcile never spawns/rebaselines while
  unknown (the retained-handle guard in `_handle_match`), later cadences
  and `terminate_all` continue bounded best-effort stop/re-observation, and
  each poll OSError still counts one unresolved failure on the 60/120/240
  cap with exactly one public alert. `_register_unresolved_failure` early-
  returns on exhausted records (never recount, never a second alert).
- Coherent resolution: an eventually observable exit, a later proven stop or
  a poll ProcessLookupError clears the quarantine and resolves through the
  durable match.json final — final completes, otherwise one crash failure
  and at most one replacement, only after the proven death. Static identity
  stays frozen and changed handoffs stay rejected while quarantined.
- `terminate_all` isolates each child, clears only proven deaths and retries
  retained unknown handles on every later call; it cannot promise to kill a
  process its permissions forbid, but never treats it as dead. In-memory
  records (incl. quarantined handles) live for the daemon lifetime; the
  durable final is the only cross-restart completion evidence. Lifecycle:
  running -> proven death -> final/backoff -> relaunch ... cap -> tombstone
  (one alert, no restarts); unknown liveness -> quarantine -> retried stop/
  re-observation -> proven death -> normal resolution.
- Test seam pinning now also covers `SessionSupervisor._matches` records
  (process/quarantined/attempts) — update the header comment when moving
  those seams. Mutation gate: 5 targeted mutations (exit state clearing,
  process/quarantine launch guard, quarantine retention, terminate_all
  clearing, exhausted alert-once guard) each fail dedicated tests
  (14/3/7/6/1 failures respectively).

## US-012 review fixes (commit f7a92d7)

- Luna P1: `process.kill()` returning without an exception is
  only a delivered SIGKILL, not a reaped death — `_stop_child` returned True
  right after the kill call, so a poll-faulted record was cleared and a
  replacement could launch while the original child was still alive. The
  kill step now reaps the death with a bounded post-kill wait
  (`KILL_WAIT_SECONDS`, same 5.0 bound as the SIGTERM wait): a reaped wait
  or a ProcessLookupError (gone pid — never signalled again) proves the
  death; TimeoutExpired or a generic OSError returns False/unknown, the
  quarantined handle is retained and later cadences/terminate_all retry the
  stop — the existing one-failure-per-fault counting and the frozen
  identity are untouched (no duplicate failure, no rebaseline).
- Test fake: FakeChild `wait_outcomes` scripts one outcome per wait call
  ("timeout"/"oserror"/"lookup"/"exit", then flat flags); every outcome
  raises/returns immediately so no test can hang. The pinned composition is
  poll OSError -> terminate ok -> wait TimeoutExpired -> kill ok (child
  still alive) -> post-kill wait TimeoutExpired/OSError => one
  child/record retained, no replacement through backoff, exactly one
  replacement after the post-kill wait finally reaps the death.
  terminate_all coverage: post-kill unknown retains the handle and the
  second call retries it. Mutation gate: deleting the post-kill wait block
  fails the 6 post-kill tests.

## Thermos review of US-009..US-012 (structural pass)

Historical. `session.py` / `session_trading.PaperTrading` were later removed;
WalletHost + MatchWorker own the live loop. Remaining `session_*` helpers
(types, config, binding, journal, engine seams, quoting) are still used.

This pass changed no behavior at the time. Several names and file locations
from the US-011 sections above moved.

- `live_paper.session` is six modules now, none over 900 lines (it was one
  2403-line file):
  `session_types` (value types only: `SignalReason`, `TradingDisabled`/
  `SidecarUnavailable`, `RawBookPair`, `SignalDecision`, `ModelFair`,
  `SidecarBinding`, `SidecarMeta`, `SessionEndSnapshot`, the `PHASE_*` labels —
  no behavior, so every other module can import it without a cycle),
  `session_config` (dota-map template -> generated config/strategy/markets TOML
  in one TemporaryDirectory), `session_binding` (sidecar selection, decimal
  parsing, `MarketMeta` build, binding record write/parse),
  `session_journal` (`SessionJournal`, `FaultReporter`),
  `session_engine` (fork composition seams: `StrategyCell`,
  `make_yes_fair_adapter`, `GatedRegimeMachine`, `FreshnessWatchdog`,
  `build_engine`, `seed_catalog`, `close_engine_resources`, the two Gamma
  overrides, `bounded_task_join`), `session_quoting` (join-bid
  `construct_quotes` overlay), `session_trading` (`PaperTrading`: decision
  path, MDS bridge, sidecar refresh loop) and `session` itself (`run_session`,
  the feed loop, provenance setup, the terminal path).
- Names shared ACROSS these modules are public (no leading underscore):
  `PaperTrading`, `SessionJournal`, `FaultReporter`, `build_engine`,
  `materialize_config_dir`, `build_sidecar_meta`, … Only `session_trading`
  still carries `# pyright: reportPrivateUsage=false`, because only it touches
  the fork's private engine attributes; `session.py` needs no suppression.
- Test-seam rule after the split: `monkeypatch.setattr` must target the
  IMPORTER, not the definer. `build_engine` and `scan_sidecars` are patched on
  `session_trading`; `ENGINE_TASK_JOIN_TIMEOUT_S` and `FEED_STALE_SECONDS`
  on `session_engine`. `notify_in_background` now has two importers that
  matter: `session` (setup/provenance alerts) and `session_journal` (the fault
  reporter's deduped alerts) — an alert test patches both.
- Tests follow the same seams: `tests/live_paper_session_fixtures.py` holds
  every builder and fake (`build_discovered`, `build_trading`,
  `build_real_trading`, `FakeEngine`, `AlertRecorder`, …), and the suite is
  `test_live_paper_session{,_config,_binding,_journal,_engine,_quoting,_trading}.py`
  plus `test_live_paper_wallet_{store,host}.py` and `test_live_paper_engine_seams.py`.
- `shared.utils.strict_json` is the one strict JSON decoder for every
  untrusted document (sidecars, Steam rows, the daemon<->child handoff, our own
  provenance): `require_object/exact_keys/str/nonempty_str/int/bool/list/
  str_list/nullable_*` raise `StrictJsonError` (a `ValueError`) with a
  caller-supplied label. Exact `type(x) is T`, so `bool` never passes as `int`.
  A missing key fails every non-nullable helper and reads as null in the
  nullable ones — pair the reads with `require_exact_keys` when an optional
  field must still be PRESENT as an explicit null (our durable records).
  Range and domain checks (positive ids, known enum values, cross-field rules)
  stay at the call site. All five `live_paper` readers use it; never hand-roll
  another `type(x) is not str` ladder.
- `archive_paths.FsyncedJsonlWriter` is the one append-only JSONL writer:
  mkdir, crash-tail truncation, `is_fresh`, then per record
  sanitize-nonfinite -> compact dumps with `allow_nan=False` -> write -> flush
  -> fsync. `StateWriter` and `SessionJournal` are thin wrappers over it (the
  fsync test seam is `archive_paths.os.fsync`, and the handle is
  `writer._writer._handle`).
- `SignalReason` is a `StrEnum`, not 15 constants plus a parallel `Literal`.
  A member IS its wire string, so `session.jsonl` records are byte-identical;
  compare with `is`/`is not`.
- `_ManagedMatch.quarantined` is GONE. A non-null `record.process` is the one
  liveness fact: it means the handle is RETAINED (running, or a stop that could
  not prove the death). The old flag was true only when `process is not None`,
  so the `process is not None or quarantined` guard carried a dead clause.
  Reconcile never spawns while a handle is retained; only a proven death clears
  it. Everything else about the lifecycle is unchanged.
- `cadence.poll_discoveries` is back on `asyncio.to_thread`. The 50-line daemon
  thread + `call_soon_threadsafe` wrapper defended against `asyncio.run`
  joining a blocked discovery, but on Python 3.13 `asyncio.run` waits at most
  `asyncio.constants.THREAD_JOIN_TIMEOUT` (300 s) for the default executor and
  then proceeds — a bounded wait on a call that is itself bounded by the httpx
  timeout. If that ever becomes a real problem, `asyncio.wrap_future` over a
  `concurrent.futures.Future` filled by a daemon thread is ~10 lines, and the
  same fix must then also cover `session._next_feed_event`, which has the exact
  same shape.
- The durable-final marker reads `meta_path.read_bytes()` and
  `json.loads(raw, parse_constant=...)` (json.loads takes bytes; no manual
  decode). The O_NONBLOCK/O_NOFOLLOW/S_ISREG/size-bounded read was removed: our
  own child writes that file by atomic rename into a directory the parent
  creates, so a FIFO/symlink/oversized attacker there could just write a valid
  final instead. NaN/Infinity rejection, the identity check and
  "never raise on unreadable data" all stay.
- Compensation-before-report is now pinned by a test
  (`test_compensation_runs_before_the_fault_report`): an observing reporter
  must see the gate already closed and the entry BUYs already gone. Reporting
  does I/O, so it can never run while a stale fair still opens the gate.
- Mutation gate for this pass (each fails a test): dropping the
  `record.process is not None` launch guard (4), dropping
  `if self._shutdown_done` in `_cancel_entry_buys` (1), reporting before
  compensating in `on_event` (1), dropping `os.fsync` from
  `FsyncedJsonlWriter.write_record` (6), accepting `bool` in
  `strict_json.require_int` (5).
