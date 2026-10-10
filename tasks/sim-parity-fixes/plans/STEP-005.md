# STEP-005 plan: logs for wallet B — two-sided trace header, `cancels_out`, raw `reprice_since_ns` (A7)

Base: `esports-trader` `main` at `d8e1de0c` (clean tree). Reference diffs, checked with `git apply --check` against that HEAD:
`orchestrate/work/plan-005/step005_src.diff` (5 files, +137/−27) and `orchestrate/work/plan-005/step005_tests.diff` (3 files, +131/−8).

## 0. Result in one paragraph

Wallet B (`TwoSidedWorker`) writes `core_trace.jsonl` from now on. It gets its own header type, `TwoSidedTraceHeader`, which has the 14 `TwoSidedPolicy` keys, a `policy_sha`, and the marker `"strategy": "two_sided"` where follow300 has its `model` block. The codec gets `decode_two_sided_header_row`. `decode_header_row` and the follow300 row are byte-for-byte unchanged. Every event row, for A and for B, gets the raw `reprice_since_ns` pair next to `plan`. `_durable_cancel` writes one engine-journal row `cancels_out` before the HTTP cancel, with one item per venue id: `venue_id`, `core_id`, `cancel_reason`, and `decision_ns` (`BuyCancelClock.started_ns`, null for a SELL). `SlimJournal` keeps the new kind. The source change is 5 files and +137/−27 lines; the tests add 4 items and update 2. In scratch, all six mutations of the new logic fail a test.

## 1. What happens today (evidence)

### 1.1 B has no trace

- `two_sided_worker.py:96-127` `open_core` takes `trace` and drops it with `del trace` (`:104`), then builds `LiveCore(..., trace=None)` (`:117`).
- `two_sided_worker.py:129-131` overrides `_open_trace` to return `None`, so `MatchWorker._try_attach` (`match_worker.py:428`, `trace=self._open_trace(archive_dir)`) never opens a file for B.
- The test pins this today: `tests/test_trader_two_sided_worker.py:217-234` (`..._without_trace`) and `:400` (`assert not (archive_dir / CORE_TRACE_FILENAME).exists()` inside `_restart_attach`, which 3 tests use).
- Follow300 opens the trace through `match_worker.py:1182-1183` (`try_open_trace`), builds a header in `:1185-1219` (`header_for_market`, then `start_market_trace`), and closes it in `_close_journal` → `core.detach_trace()` (`:1432-1439`). B inherits `_close_journal` unchanged.
- The header is follow300-only: `TraceHeader.policy: Follow300Policy` (`core_trace_codec.py:172-186`), `policy_sha(policy: Follow300Policy, …)` (`:221`), `_HEADER_KEYS` with `model` (`:148-168`), `decode_header_row` → `decode_policy` with exact Follow300 keys (`:830-851`, `:423-477`). `_header_row` writes a `model` block (`core_trace.py:258-280`, `:271`).

### 1.2 No cancel row and no hold timer in the logs

- `_durable_cancel` (`wallet_host.py:554-579`) commits the sqlite cancel command (`_dispatch_cancels`, `:432-453`), then awaits the venue (`:569`). It writes nothing to the engine journal.
- The engine journal (`polymaker/journal.py:15-27`, line-buffered, `{"ts","kind","data"}`) is the same object as the gateway's: `engine.py:63,107` pass `journal=self.journal`. The gateway writes `orders_out` there (`execution/gateway.py:155`). After `patch_engine_classes` it is a `SlimJournal`, and that keeps only `_JOURNAL_KINDS = {"orders_out","user_order","user_trade"}` (`engine_seams.py:104-114`). It is enabled by default (`polymaker/config.py:42`).
- The core's decision time for a BUY cancel already exists: `BuyCancelClock(order_id, started_ns)` (`session_core.py:95-99`), armed in `_arm_buy_cancel` (`:671-685`), kept in `self._buy_cancel_timing` (`:305`). `find_owned_order(venue_id)` (`:392-403`) gives the order. `RestingOrder.cancel_reason` (`strategy/types.py:145`) keeps the first reason (`lifecycle.py:310-320`).
- `StrategyState.reprice_since_ns` (`types.py:299`) is the 300 ms hold timer. Only `two_sided_quoting.py:181-259` sets it, and `digest_state` (`core_trace_codec.py:239-267`) does not hash it. `write_event` (`core_trace.py:97-115`) writes `plan` and `digests` only.

### 1.3 Isolation and size

- A and B run in separate containers with separate data dirs: `compose.yaml:38` `./data/trader_live`, `:88` `./data/trader_live_b`, `:75` `DOTA_STRATEGY: two_sided`. The compress job covers both (`:162-168`). So B's trace lands in `data/trader_live_b/<match>/core_trace.jsonl` and never collides with A's.
- Size, measured on the 40 newest local traces (`fd core_trace.jsonl data | xargs ls -t | head -40`, raw bytes): median 47.6 MB, max 125.7 MB, median 88,354 event rows, 538 bytes/row. The new key `,"reprice_since_ns":[null,null]` is 31 bytes, so rows grow about 5.8% (median). The cap stays `MAX_TRACE_BYTES = 256 MiB` (`core_trace.py:34`). B goes through the same `CoreTrace._emit` / `_truncate_cap`.

## 2. Design decisions

1. **A separate `TwoSidedTraceHeader`, not a generic `TraceHeader`.** Making `TraceHeader.policy: Policy` with an optional model would touch `header_for_market`, the replay decoder and every `_header(...)` test, and it would add a `None` model branch to follow300. The new frozen dataclass has the same 11 shared fields plus `TwoSidedPolicy`. Follow300 code paths do not change.
2. **Marker `"strategy": "two_sided"` replaces `model`.** B has no model file, so it has no `model` block. The key sets differ (`_TWO_SIDED_HEADER_KEYS = (_HEADER_KEYS - {"model"}) | {"strategy"}`), so each strict decoder rejects the other's row. A test pins this. `CORE_TRACE_SCHEMA_VERSION` stays 1: the follow300 header is unchanged, and the readers that need only `kind=="header"` plus `opened_wall_s`/`opened_now_ns` keep working on B (`strip_own_book.py:55-62,99-117`, `viewer/live_tape.py:478`, `dashboard/catalog.py:420-456`, `dashboard/match_trace.py:28-34`).
3. **`policy_sha` takes `Policy`.** It already hashes `jsonable(policy)`, so this is a signature-only change (`core_trace_codec.py:221`).
4. **B inherits `MatchWorker._open_trace`** (delete the override and the `Path` import). Same `try_open_trace` and the same 256 MB cap, as the step asks.
5. **`reprice_since_ns` is written raw as `list(state.reprice_since_ns)`, next to `plan`.** A always writes `[null, null]`. It is not added to `digest_state`, so replay digests and checkpoints do not change.
6. **The `cancels_out` row is written after the sqlite commit and before `original_cancel`.** Inside the `_durable_cancel` stack (`wallet_host.py:814-818`) that is also before the `_cancel_bucket` wait in `_cancel_with_proof` (`engine_seams.py:601-624`). So `ts − decision` measures core-to-dispatch time, and the HTTP and bucket time shows up in the later `user_order` cancel row. `decision_ns` uses the core's monotonic clock: it joins trace `now_ns` directly and maps to wall time through `clock_offset_ns`. One row per `gateway.cancel` call, with a list of items in the `data` field, the same shape as `orders_out`.
7. **The four fields come from the core with no new state.** `core_id` comes from `_venue_to_core`, the same as `_dispatch_cancels` (`wallet_host.py:437-441`). `cancel_reason` is set only for a `RestingOrder` (an `OrderRecord` has no reason). `decision_ns` comes from `_buy_cancel_timing`. A worker with a detached core (`worker.core is None`) writes nothing, the same as `_owned_buy_cancels` (`:507-509`).

## 3. Changes

### 3.1 `src/trader/core_trace_codec.py` (+63/−3)

- Import `Policy, TwoSidedPolicy` next to `Follow300Policy` (`:11`).
- Add these after `_HEADER_KEYS` (`:168`):

```python
TWO_SIDED_STRATEGY = "two_sided"
_TWO_SIDED_POLICY_KEYS = frozenset(item.name for item in fields(TwoSidedPolicy))
_TWO_SIDED_HEADER_KEYS = (_HEADER_KEYS - {"model"}) | {"strategy"}
```

- Add `@dataclass(frozen=True) class TwoSidedTraceHeader` after `TraceHeader` (`:186`). Its fields are `session_id, match_id, game, execution_mode, git_commit: str`, `policy: TwoSidedPolicy`, `policy_sha: str`, `limits: MarketLimits`, `freshness: FreshnessLimits`, and `yes_token, no_token: str`.
- Change the signature at `:221` to `def policy_sha(policy: Policy, limits: MarketLimits, freshness: FreshnessLimits) -> str:`.
- Add `decode_two_sided_policy(fields, label) -> TwoSidedPolicy` after `decode_policy` (`:477`). It does `require_exact_keys(fields, _TWO_SIDED_POLICY_KEYS, label)`, then `require_int` for `order_shares, net_max_shares, half_spread_ticks, quote_from_second, max_bid_sum_ticks, reprice_hold_ns, reprice_now_ticks, debounce_ms`, and `require_number` for `skew_per_share, band_hi, tick, fallback_timer_s, level_usdc, sell_min_life_s`. These match the field types in `strategy/policy.py:116-131`.
- Add this after `decode_header_row` (`:851`), with the rest of the body written like `decode_header_row`:

```python
def decode_two_sided_header_row(fields: dict[str, object]) -> TwoSidedTraceHeader:
    require_exact_keys(fields, _TWO_SIDED_HEADER_KEYS, "header")
    if require_str(fields, "strategy", "header") != TWO_SIDED_STRATEGY:
        raise StrictJsonError(f"header 'strategy' must be {TWO_SIDED_STRATEGY!r}")
    return TwoSidedTraceHeader(
        session_id=require_str(fields, "session_id", "header"),
        ...
        policy=decode_two_sided_policy(_object_field(fields, "policy", "header"), "header policy"),
        ...
    )
```

### 3.2 `src/trader/core_trace.py` (+21/−6)

- Import `TWO_SIDED_STRATEGY, TwoSidedTraceHeader`.
- Widen `write_header` (`:94`), `start_market_trace` (`:247`) and `_header_row` (`:258`) to `TraceHeader | TwoSidedTraceHeader`.
- In `write_event` (`:111`), add `"reprice_since_ns": list(state.reprice_since_ns),` after `"plan"`.
- `_header_row` splices one `source` dict in place of the `model` literal (`:271-275`). The follow300 key order is unchanged:

```python
    if isinstance(header, TwoSidedTraceHeader):
        source: dict[str, object] = {"strategy": TWO_SIDED_STRATEGY}
    else:
        source = {
            "model": {
                "name": header.model_name,
                "trained_at": header.model_trained_at,
                "sha256": header.model_sha256,
            }
        }
    return { ..., "freshness": jsonable(header.freshness), **source, "yes_token": ..., ... }
```

### 3.3 `src/trader/two_sided_worker.py` (+26/−15)

- Delete `from pathlib import Path` (`:6`) and the `_open_trace` override (`:129-131`).
- Import `start_market_trace` and `from trader.core_trace_codec import TwoSidedTraceHeader, policy_sha`.
- In `open_core` (`:96-127`), delete `del trace`. Build `policy`, `limits` and `freshness` once, then build the header and pass the trace:

```python
        header = TwoSidedTraceHeader(
            session_id=self._cid,
            match_id=self._discovered.match_id,
            game=self._discovered.game,
            execution_mode=self._mode,
            git_commit=self._host.git_commit,
            policy=policy,
            policy_sha=policy_sha(policy, limits, freshness),
            limits=limits,
            freshness=freshness,
            yes_token=self._yes,
            no_token=self._no,
        )
        core = LiveCore(policy=policy, limits=limits, freshness=freshness, ...,
                        trace=start_market_trace(trace, header), ...)
```

### 3.4 `src/trader/wallet_host.py` (+30/−1)

- Change `:11` to `from dataclasses import asdict, dataclass, replace`.
- Add this after `_owned_buy_cancels` (`:524`):

```python
@dataclass(frozen=True)
class _CancelOut:
    venue_id: str
    core_id: str | None
    cancel_reason: str
    decision_ns: int | None


def _cancel_out(core: LiveCore, venue_id: str) -> _CancelOut:
    core_id = core._venue_to_core.get(venue_id)
    order = core.find_owned_order(venue_id)
    clock = None if core_id is None else core._buy_cancel_timing.get(core_id)
    return _CancelOut(
        venue_id=venue_id,
        core_id=core_id,
        cancel_reason=order.cancel_reason if isinstance(order, RestingOrder) else "",
        decision_ns=None if clock is None else clock.started_ns,
    )


def _journal_cancels_out(engine: Engine, worker: MatchWorker, order_ids: list[str]) -> None:
    core = worker.core
    if core is None:
        return
    rows = [asdict(_cancel_out(core, venue_id)) for venue_id in order_ids]
    engine.journal.write("cancels_out", rows, time.time())
```

- In `_durable_cancel`, inside `if worker is not None:`, add one line after `buys = _owned_buy_cancels(...)` (`:568`): `_journal_cancels_out(host.engine, worker, order_ids)`.

### 3.5 `src/trader/engine_seams.py` (+1/−1)

`:104` becomes `_JOURNAL_KINDS = frozenset({"orders_out", "cancels_out", "user_order", "user_trade"})`.

### 3.6 Not changed

- `replay_core_trace.py:327` and `core_state_report` stay follow300-only, so `decode_header_row` refuses a B trace (see §5).
- `scripts/journal_to_core_trace.py:65,126` filters by kind, so it ignores `cancels_out`.
- No docs.
- No `poly-maker` changes.

## 4. Tests

| Step bullet | Test |
|---|---|
| two-sided header is written and decoded; follow300 header reads as before | `test_core_trace.py::test_two_sided_header_round_trips_beside_follow300` (new) |
| event row has `reprice_since_ns` | `test_core_trace.py::test_event_row_carries_raw_reprice_since_ns` (new) |
| `cancels_out` passes `SlimJournal` with four fields | `test_core_trace.py::test_durable_cancel_journals_cancels_out_before_http` (new) |
| `TwoSidedWorker.open_core` gets a non-empty trace | `test_core_trace.py::test_two_sided_worker_opens_its_trace` (new) and `test_trader_two_sided_worker.py::_restart_attach` (flipped) |

### 4.1 `tests/test_core_trace.py` (+4 items, +127 lines)

New imports:
- `add_order, open_buy_rig, run_cancel` from `test_trader_unsettled_buy_activation`. `test_trader_buy_terminal_proof.py:24-25` already imports from that file.
- `clock_offset_ns`
- `two_sided_policy`, `TwoSidedTraceHeader`, `decode_header_row`, `decode_two_sided_header_row`
- `SlimJournal`, `TwoSidedWorker`

New helpers: `_two_sided_header()` and `_first_row(path)`, placed after `test_both_games_write_headers` (`:753`).

- **Round trip.** Write one B header and one follow300 header (`_header(pinned_policy())`), then check:
  - The B row has `strategy == "two_sided"` and no `model`.
  - `decode_two_sided_header_row(b_row) == header` and `decode_header_row(a_row) == follow300`.
  - Each decoder raises `StrictJsonError` on the other's row.
  - `clock_offset_ns(b_dir)` equals the header's `opened_wall_s`/`opened_now_ns` offset, which shows the STEP-011 strip reader works on B.
- **reprice_since_ns.** `core.apply(Wake)` on a follow300 core writes `[None, None]`. A direct `trace.write_event(..., state=replace(core.state, reprice_since_ns=(7, None)))` writes `[7, None]`.
- **cancels_out.**
  - Build `open_buy_rig` and add a SELL `c1`/`venue-sell` with `add_order`.
  - Apply `PermissionsUpdate(7 s, halt)`. The halt plan cancels both orders with reason `halt` and arms `BuyCancelClock(c0, 7 s)`, as confirmed in scratch.
  - Set `rig.host.engine.journal = SlimJournal(tmp/journal)`.
  - Call `run_cancel(..., during=read_journal)`, which reads `live.jsonl` while the HTTP call is "in flight".
  - Assert exactly one row, `kind == "cancels_out"`, and `data == [{venue-buy, c0, halt, 7e9}, {venue-sell, c1, halt, None}]`.
- **TwoSidedWorker.** Call `build_attached_worker(..., trace=CoreTrace.open(...), worker_class=TwoSidedWorker)`, then check:
  - `core._trace is trace`.
  - After `detach_trace()`, `decode_two_sided_header_row(first row).policy == core.policy`, the yes token matches, and `git_commit == "test-commit"`.

### 4.2 `tests/test_trader_two_sided_worker.py` (2 edits)

- Change `:217-234`: rename the test to `test_two_sided_worker_builds_a_two_sided_core` and drop the three no-trace assertions (`core._trace is None` and the `trace_probe` lines).
- In `_restart_attach`, replace `:400` with `assert _core(worker)._trace is not None` and `assert (archive_dir / CORE_TRACE_FILENAME).exists()`. This drives the real `_try_attach → _open_trace → open_core` path in 3 tests.

### 4.3 `tests/test_trader_unsettled_buy_activation.py` (fixture)

Add `journal=SlimJournal(tmp_path, enabled=False)` to the `open_buy_rig` engine `SimpleNamespace` (`:130-141`) and import `SlimJournal` (`:44`). Without it, 19 existing tests fail with `AttributeError: 'types.SimpleNamespace' object has no attribute 'journal'`. These are in `test_trader_unsettled_buy_activation`, `test_trader_buy_terminal_proof` and `test_trader_wallet_host` `test_durable_cancel_*` (scratch run `work/plan-005/src_only.log`). No other fake reaches `_durable_cancel` without a journal: after the fixture edit the full suite shows no new failures.

### 4.4 Mutation evidence (scratch, `work/plan-005/mutate.py`)

All 6 mutations fail a test:
- `cancels_out` dropped from `_JOURNAL_KINDS`
- the journal write moved after `await original_cancel`
- `decision_ns=None`
- the `_open_trace → None` override restored
- the `reprice_since_ns` key removed
- `trace=None` in `open_core`

## 5. Behavior changes and risks

- **Disk.** B now writes a trace per map. A's rows grow by 31 bytes (median +5.8%; the largest of the 40 newest traces goes from 125.7 to about 133 MB). The cap and the truncate row are unchanged, and B has its own volume (`compose.yaml:88`).
- **Strict readers refuse B.** `replay_core_trace.py:327` and `core_state_report` call `decode_header_row`, so a B trace raises `StrictJsonError` on the header keys. Raw readers (strip, viewer, dashboard) work. Upgrade path: dispatch on `"strategy" in fields`, about 15 lines, when B needs replay.
- **`decision_ns` is null for every SELL and for a BUY restored without a clock**, because `BuyCancelClock` is BUY-only (`session_core.py:671-685`). The SELL's decision time can be recovered by joining `core_id` to the trace event row whose plan cancels it. Generalizing the clock is out of scope.
- **Rows that are not `_durable_cancel` cancels.**
  - `cancel_asset` / `cancel_all` (`gateway.py:220,237`) skip `_durable_cancel` and write no `cancels_out`.
  - Orphan cancels (no worker) write no row, but `_warn_orphaned` still logs them.
  - Book-guard fault cancels (STEP-003) carry whatever reason the core set.
- **Resends repeat the row.** `_venue_cancels` (`session_core.py:874-891`) resends a still-canceling order every cycle, so each resend writes a `cancels_out` row with the same `decision_ns`. This is useful for counting resends and costs about 170 bytes per venue id per resend (`json.dumps` of one item with a 66-character venue id is 167 bytes).
- **No error guard on the journal write.** `Journal.write` can raise `OSError` on a full disk. The sqlite commit in `_dispatch_cancels` just before it fails first in that case, so the call adds no new failure mode. Same as the gateway's `orders_out` write.
- **Old traces have no `reprice_since_ns`.** Readers use `.get`, and replay ignores unknown event keys: `test_later_header_policy_change_replays` and the contract-tape replays pass on rows that have the key.

## 6. Verification

Run from `esports-trader/` on `main`. No live map running (AGENTS.md). Do not set `PYTEST_N` on the VPS.

```bash
# targeted (scratch: 271 passed + 12 .env-only failures in test_trader_wallet_host.py; expect 283 passed)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_core_trace.py tests/test_trader_two_sided_worker.py tests/test_trader_unsettled_buy_activation.py \
  tests/test_trader_buy_terminal_proof.py tests/test_trader_wallet_host.py tests/test_trader_engine_seams.py

# full suite without golden replay ("Tests pass" for STEP-001..013).
# scratch: HEAD 3239 passed + 22 env-only failures, patched 3243 passed + the same 22; impl-004-fix saw 3261 passed here, so expect 3265 passed
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (HEAD here: 0 errors, 0 warnings, 0 notes; scratch: the 8 changed files 0 errors)
uv run python -m basedpyright

# ruff check + format + repo hooks on the touched files
uv run python -m pre_commit run --files src/trader/core_trace_codec.py src/trader/core_trace.py \
  src/trader/two_sided_worker.py src/trader/wallet_host.py src/trader/engine_seams.py \
  tests/test_core_trace.py tests/test_trader_two_sided_worker.py tests/test_trader_unsettled_buy_activation.py

# no new comments: expect no output
git diff -U0 -- src tests | rg '^\+\s*#|^\+.*\s#\s'
```

About the 22 scratch-only failures:
- 13 need `.env`: `test_trader_wallet_host.py` ×12 (`ODDIN_BRAND_TOKEN`) and `test_trader_unsettled_buy_recovery.py::test_user_stream_is_bound_on_the_host_run_path`.
- 9 `test_lol_backtest.py` failures need `data/archive_index/index.parquet`, which the `git archive` copy lacks.

They are the same set on HEAD scratch and on patched scratch (`work/plan-005/base.failed` = `full.failed`, `comm` empty). The 37+6+2+3 basedpyright errors in scratch `src/backtest/run.py`, `context.py`, `tests/test_backtest.py` and `tests/test_lol_backtest.py` come from `../prediction-market-backtesting` not resolving from the scratch path. The real checkout reports 0.

Not run in this step: `tests/test_follow300_replay.py` (STEP-014 rewrites it), any backtest, training, data rebuild, or live start.

## 7. Commit

Paths: `src/trader/core_trace_codec.py`, `src/trader/core_trace.py`, `src/trader/two_sided_worker.py`, `src/trader/wallet_host.py`, `src/trader/engine_seams.py`, `tests/test_core_trace.py`, `tests/test_trader_two_sided_worker.py`, `tests/test_trader_unsettled_buy_activation.py`. Add them by name, not with `-A`.

Message, in the style of earlier steps: `fix: STEP-005 - trace wallet B with a two-sided header, journal cancels_out and log reprice_since_ns`.

Do not push. Do not set `passes`.
