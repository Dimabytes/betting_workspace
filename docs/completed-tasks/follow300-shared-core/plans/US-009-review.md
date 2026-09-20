# US-009 review

Verdict: **REQUEST CHANGES**, then **FIXED** in this session.

Diff reviewed: `40558d9` (US-008 review-fix) → `8669778` (feat US-009), plus review-fix `f95b650`.

## Findings

### P1 — encoding ran outside the write try/except

`write_event` / `write_header` / `write_reset` built payloads (`encode_event`, `digest_state`, `jsonable`, `allow_nan=False`) before `_write`. A NaN book price raised `ValueError` in `LiveCore.apply` and could stop quoting. Plan: a broken trace never raises into trading.

**Fixed:** one `_emit(build)` wraps encode + dumps + write; catches `OSError`, `ValueError`, `TypeError`. Test: `test_nonfinite_event_does_not_raise_into_core`.

### P1 — corrupt trace open could abort attach

`try_open_trace` only caught `OSError`. `_resume_seq` uses `json.loads` + `require_int`; a bad last line raised `JSONDecodeError` / `StrictJsonError` into `_try_attach`. Plan: open failure → `trace=None`, keep trading.

**Fixed:** catch `OSError | ValueError | TypeError | UnicodeError`. Test: `test_corrupt_trace_open_does_not_raise`.

### P1 — `core_trace.py` at 984 lines; `match_worker.py` 1113→1151

Codec + writer in one file would cross 1k with any fault-isolation code. Header wiring grew an already-over-1k worker.

**Fixed:**
- Split codec into `core_trace_codec.py` (685). Writer `core_trace.py` is 277.
- `apply_checkpoint` / `restore_last_buy` moved to `core_persistence.py` (canonical restore; LiveCore and replayer still share them).
- Execution cleanup helpers moved to `archive_paths.py` (wallet_host already treated them as a separate API).
- `start_market_trace` collapses the header-fail / write-header branches in `open_core`.

`match_worker.py` is **1106** (below the pre-US-009 1113; still over 1k). Further split of the attach/quote/fence loop is the same MatchWorker decomposition previous reviews deferred.

### P2 — later header ignored `policy_sha`

A resume header with a new policy kept the first header's policy, so post-restart events replayed against the old clip. Plan: `policy_sha` change is a boundary, not a mismatch.

**Fixed:** switch archive policy on a later header with a new sha. Test: `test_later_header_policy_change_replays`. Did not add a CLI “boundary note” print (NIT).

### P2 — unused / fake encoder table; unvalidated `BlockReason`

`EVENT_ENCODERS` was `get_args(InboundEvent)` names, not an encoder table (encoding is generic `jsonable`). `_block_reason` cast any string.

**Fixed:** deleted `EVENT_ENCODERS`; completeness test is `get_args` vs `EVENT_DECODERS` plus `DIGEST_GROUPS`. Invalid block reasons raise `StrictJsonError`. Replayer compares `DIGEST_GROUPS` instead of a duplicated tuple.

## NIT skipped

- CLI does not print a policy-sha boundary note on later headers (switch is enough for a clean replay).
- `match_worker.py` remains over 1k (1106). Getting under 1000 means extracting pre-existing `note_*` / sidecar / quiesce, not US-009 logic.
- `encode_plan` is a thin `jsonable` + cast; it is the public codec API.
- `_mismatch` in the replayer repeats constructor kwargs; not worth a new type.

## Gates

- `make test`: 1778 passed
- `uv run python -m pre_commit run --all-files`: passed (ruff, format, basedpyright)

US-009 `passes: true`. US-010 unchanged (`false`). Did not run 12-seed, daemons, or recapture goldens.
