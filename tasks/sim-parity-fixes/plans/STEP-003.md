# STEP-003 plan: fail-closed book guard and one merge retry on a stale relayer nonce (A4 + A8)

Code: `esports-trader` `main` at `fe6b713c` (clean `git status`). Paths below are relative to `esports-trader/`. `poly-maker` is read-only and is only cited. Planner scratch (a `git archive` copy of HEAD, patched; logs; mutation harness; reference diffs): `betting_workspace/tasks/sim-parity-fixes/orchestrate/work/plan-003/` (below: `work/plan-003/`). Reference diffs: `work/plan-003/step003_src.diff` and `work/plan-003/step003_tests.diff`.

No Figma links: the step has no `designReference`. `progress.txt` has STEP-001 and STEP-002; neither touches the files below.

## 0. Result in one paragraph

Two small safety fixes, both in live-only host code. The backtest and the golden replay are not affected.

**A4.** `_install_book_guard` (`src/trader/host_resources.py:144-198`) wraps `run_core_cycle` and `core.take_plan()` in `try/except Exception`. On an exception it logs `_quote_fault(<type>)`, cancels every order the fork holds for the map (`engine.state.orders_for(yes) + orders_for(no)`), and writes the `book_guard` diag record with a new `error` field. It then uses the cancel handling that already exists. The exception no longer reaches the fork `_quoter`, so there is no `quoter_error` and no 0.5 s retry while orders rest on a bad YES book.

**A8.** `merge_pairs_via_adapter` (`src/trader/ctf_merge.py:109-139`) becomes a wrapper around today's body, renamed `_merge_once`. If the outcome is `failed` and its reason contains `does not match on-chain nonce`, it logs one warning and runs `_merge_once` once more. That reads the nonce again (`_fetch_nonce`), re-signs the batch and posts it again. A second failure returns to `PairMerger` unchanged, so the 30 s pause applies as before.

Size: +45/−17 production lines in 2 files (most of the host_resources diff is re-indentation) and +98/−2 test lines in 3 files; 5 new test cases. No schema change, no new dependency, no `poly-maker` change. Verified on a scratch copy:
- The target files: 83 passed (78 on HEAD).
- The full suite without the golden replay: 3234 passed, plus the same 13 environment-only failures as HEAD (§6).
- basedpyright reports 0 errors, ruff check and format are clean, and no added line has a comment.
- Eight mutations of the patch (including HEAD itself) are each caught (§4.4).

## 1. What is broken today (evidence)

### 1.1 A4: a core exception in the guard escapes to the fork quoter

- Normal path: `quotes_adapter` (`host_resources.py:87-117`) catches any core exception, logs `_quote_fault` (`:71-73`) and returns an empty `TargetQuotes`. `esports_reconcile` then calls `core.take_plan()`. If there is no plan, it cancels every live fork order of the map (`src/trader/session_core.py:1068-1072`). `live` is `orders_for(yes) + orders_for(no)` (`poly-maker/src/polymaker/engine.py:455-456`). That is fail-closed.
- Guard path: a bad YES book (`_check_yes_book`, `host_resources.py:130-141`) on a follow300 map goes to `recompute_locked` (`:153-196`). That code calls `run_core_cycle` (`:160-173`) and `core.take_plan()` (`:174`) without a `try`.
  - `take_plan` itself can raise `AssertionError` on a stale plan cycle (`session_core.py:376-377`).
  - The exception reaches `_quoter` (`engine.py:318-340`), which logs `quoter_error` and sleeps 0.5 s (`:338-340`).
  - The next wake runs the same path again. The orders stay on the venue for as long as the YES book is bad.
- This is the deliberate simplification left by STEP-005 of `live-a-audit-fixes`. Its plan names the upgrade path (`tasks/live-a-audit-fixes/plans/STEP-005.md:245`): "copy the adapter `try/except` and cancel `engine.state.orders_for(yes) + orders_for(no)`".
- Frequency: `construct_quotes fault` and `quoter_error` appear 0 times in the VPS logs (`PLAN.md:83`). This is insurance, not a frequent bug.
- On HEAD the new test fails with `RuntimeError: core` raised out of `world.tick` → `engine._recompute` (`work/plan-003`, run on HEAD `host_resources.py`).

### 1.2 A8: a stale relayer nonce costs a 30 s pause

- Live B, 2026-10-08, map 9034882975 (`grid-3011821-m1`), from `docker compose logs live_b` (quoted in `PLAN.md:155-162`):
  - 14:46:00.748 `merge failed ... pause=30s tx=- reason=post: relayer 400: {'error': 'batch nonce 34 does not match on-chain nonce 35'}`.
  - Then the 30 s pause. New fills kept the `has_unacked_matched` gate closed until 14:46:32, and the merge went through at 14:46:38. It was the only failure among 35 merges that day.
- Code path:
  1. The nonce is read once per call (`_sign_merge_batch` → `_fetch_nonce`, `ctf_merge.py:190`, `:205-210`).
  2. A 400 with an explicit `error` becomes `failed` (`_classify_relayer_error`, `:471-476`, via `:240-241`). The reason text is exactly the logged one.
  3. `PairMerger._apply_outcome` sends any `failed` outcome to `_pause_merges` (`src/trader/pair_merge.py:207-209`, `:255-270`), which pauses `MERGE_PAUSES_S[0] = 30.0` s (`:25`, `:29-30`).
- Timing from the live B ledger copy (`investigations/2026-10-08-liveb-postmortem/work/data/trader_live_b/wallet/live.db`, queried read-only on a copy at `work/plan-003/liveb.db`, timestamps in UTC; the 14:46:38 MERGE row matches the log):
  - B merged at 14:35:34 (another map) and at 14:39:55 (same token, `26807632…`), then failed at 14:46:00 and merged at 14:46:38.
  - So the previous merge was 6 min 5 s before the failure. The stale nonce was not a sub-second race right after a mined merge (see risk 1 in §5).

## 2. Design decisions

1. **A4 mirrors the normal-path fault exactly.** The fault path cancels the same fork list that `esports_reconcile` cancels when the core has no plan.
   - The cancel goes through the wrapped `engine.gateway.cancel` → `_durable_cancel` (`src/trader/wallet_host.py:554-579`).
   - The core then hears the result through `note_cancel_result` → `LiveCore.note_cancel` (`session_core.py:540-555`). A BUY gets `CancelUnsettled` and waits for its `BuySettled`; a SELL gets `CancelAck`.
   - The core accepts both for orders it did not plan to cancel (`src/strategy/lifecycle.py:349-361`).
   - Successful BUY cancels still create `unsettled_buys` rows (`_owned_buy_cancels`, `wallet_host.py:505-524`), so reserves stay correct.
2. **One shared tail, no helper.**
   - The `try` computes `order_ids` from the plan; the `except` computes them from the fork state.
   - Both paths then share the existing `if not order_ids: return` → `reasons` → `gateway.cancel` → `log_event` → ok / not-ok handling (`host_resources.py:175-196`).
   - After a successful fault cancel, the fork state for both tokens is empty. Repeated faults then return without an HTTP call, like a plan without cancels does today. A test pins this.
3. **Diag field reading.** The step says "diag `book_guard` с `guard=fault` и типом ошибки". The plan reads `guard=fault` as today's kwarg: `guard` keeps the bad-book reason (`one_sided`, `crossed`, …). It adds `error=<exception type name>`. Normal records carry `error=None`.
   - No code reads `book_guard` records besides `tests/test_trader_book_guard.py` (grep over `src`, `scripts`, `docs` and the investigation folders).
   - If the orchestrator wants a literal `guard="fault"` instead, change one kwarg and one test assert. The cost is losing which book check fired.
4. **`_quote_fault` is reused as the step asks.** Its log line says `trader construct_quotes fault: <type>` (`host_resources.py:73`), so the existing VPS grep for `construct_quotes fault` catches guard faults too. Its docstring is not touched.
5. **A8 retries the whole `_merge_once`, not only the POST.**
   - The nonce is inside the EIP-712 `Batch` the signer signs (`py_builder_relayer_client/builder/deposit_wallet.py:28-33`, `:64`). The builder headers sign the body (`_builder_headers`, `ctf_merge.py:213-217`).
   - So a retry must read the nonce again, re-sign and rebuild the headers. That is exactly today's body.
   - Rebuilding `RelayClient` and the Web3 provider does no network I/O (`py_builder_relayer_client/client.py:73-100`). `_merge_once` closes its own provider in `finally` (`ctf_merge.py:138-139`).
6. **Retry only when the status is `failed`.**
   - `failed` with this text means the relayer explicitly rejected the batch, so no transaction exists and a resubmit cannot double-merge.
   - `unknown` means the transaction may be on chain (timeout, non-JSON body). It is never retried, even if the text mentions the nonce. A test pins this (`stale_nonce_as_text`).
   - The 200-with-`error` path (`_outcome_from_answer`, `:459-468`) is also `failed` and also retried, which is the same relayer verdict.
7. **The retry stays inside `ctf_merge`.**
   - It runs in the same `asyncio.to_thread` call (`pair_merge.py:191-199`), under the same `_chain_lock` (`:154`) and the same 190 s deadline (`MERGE_CALL_TIMEOUT_S`, `:26`). No other chain call can interleave, and the deadline checks in `_require_time` still bound it.
   - `PairMerger` is untouched: a successful retry leaves `_failures = 0` and no pause, and a second stale nonce takes the old `_pause_merges` path.
   - Both callers get it: the threshold merge and the final `merge_all` both use `_call_adapter`, the only caller of `merge_pairs_via_adapter` (grep over `src` and `scripts`).
8. **One `logger.warning` on the retry.** Without it a successful retry leaves no trace of the stale nonce, and that log line is the only evidence we have today. `ctf_merge.py` gets `get_logger` like `pair_merge.py:11`, `:22`.

## 3. Changes

### 3.1 `src/trader/host_resources.py` (A4)

Replace `recompute_locked` lines `:160-177` and add one kwarg to `log_event` (`:182-190`). The result:

```python
    async def recompute_locked(cid: str) -> None:
        meta = engine.metas[cid]
        fault = _check_yes_book(engine.md.book(meta.yes.token_id))
        core = cores.get(cid)
        if fault is None or core is None:
            await original(cid)
            return
        error: str | None = None
        try:
            run_core_cycle(
                core,
                regime=Regime.QUIET,
                yes=meta.yes.token_id,
                no=meta.no.token_id,
                now_ns=core_now_ns(),
                books=None,
                clock=core.latest_clock,
                limits=_market_limits(core, meta),
                store=store,
                cache=cache,
                cores=cores.values(),
                account_cap_usdc=account_cap_usdc,
            )
            planned = core.take_plan()
            order_ids = [] if planned is None else list(planned.to_cancel)
        except Exception as exc:
            error = type(exc).__name__
            _quote_fault(error)
            yes_orders = engine.state.orders_for(meta.yes.token_id)
            no_orders = engine.state.orders_for(meta.no.token_id)
            order_ids = [order.order_id for order in yes_orders + no_orders]
        if not order_ids:
            return
        reasons = sorted(
            {order.cancel_reason for order in core.state.orders if already_canceling(order)}
        )
        ok = await engine.gateway.cancel(order_ids)
        log_event(
            "book_guard",
            cid=cid,
            guard=fault,
            block=core.last_block,
            reasons=reasons,
            order_ids=order_ids,
            ok=ok,
            error=error,
        )
        if ok:
            for order_id in order_ids:
                engine.state.remove_order(order_id)
            return
        await engine._refresh_token_orders(meta, grace_s=10.0)
        engine._dirty[cid].set()
```

Notes for the implementer:
- `core.last_block` is a plain attribute (`session_core.py:311`, set at `:855`), and `core.state` is the immutable state, so both are safe to read after a core exception. On a fault record they come from the last successful cycle; `error` marks the record.
- `engine.state` is `WalletStateStore`, a `polymaker.state.store.StateStore` (`src/trader/wallet_store.py:134`, `poly-maker/src/polymaker/state/store.py:161-172`).
- The file grows from 371 to 380 lines. Ruff C901 stays clean.

### 3.2 `src/trader/ctf_merge.py` (A8)

- After the third-party imports (`:30`), add the first-party import and the logger, as in `pair_merge.py`:

```python
from shared.utils.log import get_logger

logger = get_logger(__name__)
```

- After `BATCH_DEADLINE_S = 3600` (`:55`): `STALE_NONCE_ERROR = "does not match on-chain nonce"`.
- Rename today's `merge_pairs_via_adapter` (`:109-139`) to `_merge_once` with its body unchanged, and put the public wrapper above it:

```python
def merge_pairs_via_adapter(
    *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
) -> MergeOutcome:
    outcome = _merge_once(
        cfg=cfg, condition_id=condition_id, amount_raw=amount_raw, deadline_s=deadline_s
    )
    if outcome.status != "failed" or STALE_NONCE_ERROR not in outcome.reason:
        return outcome
    logger.warning("merge stale relayer nonce, signing again: %s", outcome.reason)
    return _merge_once(
        cfg=cfg, condition_id=condition_id, amount_raw=amount_raw, deadline_s=deadline_s
    )


def _merge_once(
    *, cfg: Config, condition_id: str, amount_raw: int, deadline_s: float
) -> MergeOutcome:
    ...  # today's body of merge_pairs_via_adapter, unchanged
```

The file grows from 497 to 516 lines. The public signature does not change, so `pair_merge.py` and its `_ScriptedMerge` stub (`tests/test_trader_pair_merge.py:96-112`) need no edit.

### 3.3 Not changed

- `poly-maker`: frozen. Nothing in `engine.py` changes. The guard still replaces `_recompute_locked` from our side.
- `src/trader/pair_merge.py`, `MERGE_PAUSES_S`, `pick_merge_pause_s`, and the Telegram texts.
- `_quote_fault`, `quotes_adapter`, `make_esports_reconcile`, `_durable_cancel`.
- Two-sided (B): `_install_follow300_book_guard` stays follow300-only (`host_resources.py:201-211`). B gets A8 only.
- The backtest, golden smoke, paper mode.

## 4. Tests

Reference text: `work/plan-003/step003_tests.diff`.

### 4.1 `tests/test_trader_book_guard.py` (A4)

- In `test_bad_yes_book_cancels_resting_buys`, after `assert fields["ok"] is True` (`:323`), add `assert fields["error"] is None`.
- Before `test_failed_guard_cancel_retries_once_more` (`:386`), add a helper and a parametrized test:

```python
def _break_cycle(world: GuardWorld, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("core")

    if name == "run_core_cycle":
        monkeypatch.setattr("trader.host_resources.run_core_cycle", boom)
        return
    monkeypatch.setattr(world.cores["a"], "take_plan", boom)


@pytest.mark.parametrize("name", ["run_core_cycle", "take_plan"])
def test_core_fault_on_bad_yes_book_cancels_every_map_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, name: str
) -> None:
    world = _open(tmp_path, monkeypatch, request)
    faults: list[str] = []
    monkeypatch.setattr("trader.host_resources._quote_fault", faults.append)
    every_order = [*RESTING, "n1"]

    async def run() -> None:
        await _seed(world, "a")
        world.engine.state.upsert_order(OpenOrder("n1", "a-no", Side.SELL, 0.55, 10.0))
        places = len(world.gateway.places)
        _break_cycle(world, monkeypatch, name)
        world.set_yes("a", YES_BIDS, [])
        world.signal("a")
        await world.tick("a")
        assert world.gateway.cancels == [every_order]
        assert len(world.gateway.places) == places
        assert faults == ["RuntimeError"]
        fields = _guard_fields(world)
        assert fields["guard"] == "one_sided"
        assert fields["error"] == "RuntimeError"
        assert fields["order_ids"] == every_order
        assert fields["ok"] is True
        await world.tick("a")
        assert world.gateway.cancels == [every_order]
        assert faults == ["RuntimeError", "RuntimeError"]

    asyncio.run(run())
```

What it covers, item by item from the step:
- "`gateway.cancel` got every order of both tokens": the three seeded YES BUYs plus a NO SELL `n1` put into fork state.
- "`place` not called".
- "`_quoter` does not see the exception": `world.tick` is `engine._recompute`, the call `_quoter` makes. On HEAD it raises.
- "diag `book_guard` with the fault": `guard` and `error` are asserted.
- `run_core_cycle` is patched at its `host_resources` import, so the seeding cycle (`quote_cycle` → `session_core.run_core_cycle`) is unaffected.
- The second tick proves a repeated fault sends no empty cancel once the fork state is clear.
- `OpenOrder` and `Side` are already imported (`:12`).

### 4.2 `tests/test_trader_ctf_merge.py` (A8, unit)

- After `UNREACHABLE` (`:46`), add two constants, in the relayer's wire format:

```python
STALE_NONCE_TEXT = b"batch nonce 7 does not match on-chain nonce 8"
STALE_NONCE = b'{"error": "batch nonce 7 does not match on-chain nonce 8"}'
```

- `_FakeVenue` (`:83-120`):
  - add `self.stale_posts = 0` after `self.gets = 0` (`:91`);
  - the GET answer becomes `{"nonce": str(6 + self.gets)}`, so the first read is still `"7"` (`:158` keeps passing) and the second is `"8"`;
  - after the body is recorded (`:108-109`), add `if len(self.bodies) <= self.stale_posts: raise _relayer_error(400, STALE_NONCE)`.
- `test_relayer_answer_sets_outcome` (`:194-237`):
  - add the case `(_relayer_error(400, STALE_NONCE_TEXT), "unknown")` with id `stale_nonce_as_text` after `gateway_5xx`;
  - add `assert len(venue.bodies) == 1` at the end.
  - Together these pin that no other relayer answer is retried, and that an `unknown` outcome is never retried even when its text names the nonce.

### 4.3 `tests/test_trader_pair_merge.py` (A8, through `PairMerger`)

- Import `_install_venue` next to `_start_header_server` (`:14`).
- Append, after `test_slow_headers_finish_before_the_wallet_lock_opens` (ends `:951`). The test reuses `_open_host`, `_merge_cfg` (`:873-884`), `_record_alerts` and `_schedule_and_wait`. It needs a hex condition id, like the slow-headers test, because `_to_bytes32` parses it.

```python
@pytest.mark.parametrize(
    ("stale_posts", "failures", "yes_left"),
    [(1, 0, 30.0), (2, 1, 150.0)],
    ids=["second_post_merges", "second_stale_nonce_pauses"],
)
def test_stale_relayer_nonce_signs_again_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale_posts: int,
    failures: int,
    yes_left: float,
) -> None:
    alerts = _record_alerts(monkeypatch)
    venue = _install_venue(monkeypatch, answer={"transactionHash": TX}, receipt={"status": 1})
    venue.stale_posts = stale_posts
    host = _open_host(tmp_path, yes=150.0, no=120.0, price=0.50)
    host.engine.cfg = _merge_cfg(relayer_url="http://127.0.0.1:9", polygon_rpc="http://127.0.0.1:9")
    merger = PairMerger(
        host=cast(Any, host),
        cid="0x" + "cd" * 32,
        match_id=MATCH,
        yes_token=YES,
        no_token=NO,
        mode="live",
    )
    merger.attach_core(host.core)
    asyncio.run(_schedule_and_wait(merger))
    assert venue.gets == 2
    assert [body["nonce"] for body in venue.bodies] == ["7", "8"]
    assert venue.bodies[0]["signature"] != venue.bodies[1]["signature"]
    assert merger._failures == failures
    assert host.store.position(YES).size == pytest.approx(yes_left)
    assert _released(host)
    if failures == 0:
        assert merger._paused_until_s == 0.0
        assert alerts.messages == [f"trader merge: match {MATCH} pairs 120.00 tx {TX}"]
        return
    assert merger._paused_until_s - time.monotonic() == pytest.approx(30.0, abs=1.0)
    assert alerts.messages == [
        f"trader merge failed: match {MATCH} pause 30s tx -: post: relayer 400: "
        "{'error': 'batch nonce 7 does not match on-chain nonce 8'}"
    ]
```

This is the step's merge test as written:
- First answer is the nonce error and the second succeeds: one merge, no pause, nonce read twice, and the second body is re-signed with the new nonce.
- Two nonce errors in a row: a 30 s pause and the usual Telegram text, as today.

### 4.4 Fail-before evidence (scratch)

`work/plan-003/mutate.py` swaps one edit into the patched copy, runs the three target files, and restores. Output: `work/plan-003/mutate.out`.

| Mutation | Caught by |
|---|---|
| HEAD `host_resources.py` + `ctf_merge.py` | both fault cases (`RuntimeError` escapes), both pair-merge cases, 4 × `error is None` |
| `take_plan` moved out of the `try` | `[take_plan]` |
| fault path cancels YES orders only | both fault cases |
| `if not order_ids: return` removed | 8 tests (empty cancels) |
| `_quote_fault` not called | both fault cases |
| retry removed | both pair-merge cases |
| retry on any `failed` | `quota`, `signature`, `quota_on_500`, `parsed_error_without_tx` |
| retry on `unknown` too | `stale_nonce_as_text` |

## 5. Behavior changes and risks

1. **The relayer may still return the stale nonce on the immediate re-read.** The previous B merge was 6 min before the failure (§1.2), so the relayer's `/nonce` answer was stale for a long time, not for milliseconds. If the re-read returns 34 again, the retry fails the same way and `PairMerger` pauses 30 s. That is today's behavior, plus one wasted relayer round trip inside the same deadline.
   - The new warning line followed by `merge failed ... nonce` in `docker compose logs live_b` will show whether this happens.
   - Upgrade path if it does: sign the retry with the on-chain nonce from the error text ("on-chain nonce 35"). Not done now: the step names `_fetch_nonce`, and parsing depends on the relayer's message format.
2. **The text match is the contract.** If the relayer rewords the error, the retry silently stops and the old 30 s path returns. That is safe, not a regression. The constant is one line and the test uses the observed wording.
3. **The fail-closed guard pulls the exit SELL too.** The fault path cancels every fork order of the map, including a resting SELL. This is the same as the normal-path fault (`esports_reconcile` with no plan). The SELL comes back on the first cycle where the core works and the YES book is valid.
4. **Mixed batches skip prepared-cancel persistence.** `_dispatch_cancels` writes cancel commands only if every venue id maps to a core id (`wallet_host.py:442-443`). A fork order the core never knew (an orphan) in the same batch drops the prepared-cancel rows for the whole batch. HTTP, the result journal, `unsettled_buys` rows and core acks still happen. This is identical to the normal-path fault today, and fork and core orders normally match.
5. **A core that also fails in `note_cancel`.** `_durable_cancel` applies the result to the core after the HTTP cancel (`wallet_host.py:572-575`). If that raises too, the exception leaves the guard after the orders are already off the venue. This is the same as the fork path today. The plan does not add a second `try` layer for a fault inside a fault.
6. **Diag schema.** Every `book_guard` record gets an `error` key (`None` on the normal path). There are no readers besides the test.

## 6. Verification

Run from `esports-trader/` in the main checkout. Do not run the suite during a live map on the VPS, and set `PYTEST_N` only locally.

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

# targeted (scratch: 83 passed; HEAD has 78)
PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  tests/test_trader_book_guard.py tests/test_trader_ctf_merge.py tests/test_trader_pair_merge.py

# full suite without golden replay ("Tests pass" for STEP-001..013; scratch: 3234 passed in 26 s)
PYTEST_N=8 PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest -q \
  --ignore=tests/test_follow300_replay.py

# typecheck (scratch: 0 errors, 0 warnings)
uv run python -m basedpyright

# ruff check + format + repo hooks on the touched files
uv run python -m pre_commit run --files src/trader/host_resources.py src/trader/ctf_merge.py \
  tests/test_trader_book_guard.py tests/test_trader_ctf_merge.py tests/test_trader_pair_merge.py

# no new comments (expect no output)
git diff -U0 -- src tests | rg '^\+[^+].*#'
```

Expected result: 0 failures.
- On the scratch copy the full run gave 3234 passed and 13 failed. HEAD in the same scratch setup gave 3229 passed and the identical 13 failures (`diff work/plan-003/base.failed work/plan-003/patch.failed` is empty). That is the same list STEP-002 recorded.
- All 13 fail only because the copy has no gitignored `.env`. `env_value` falls back to the repo `.env` (`src/shared/utils/environment.py:13-20`), and without it there is no `ODDIN_BRAND_TOKEN`. They pass in the real checkout, as in STEP-001 and STEP-002.

This step needs no backtest, training or data rebuild: both changes are live-host code that the backtest does not import. The golden replay is not affected; STEP-014 owns it.

Optional check after deploy (owner, not this step): on the first B merge, `docker compose logs live_b | rg "stale relayer nonce|merge failed"` shows whether the retry fires and whether it succeeds.

## 7. Commit

In `esports-trader` on `main`, add only the 5 paths listed above (`git add <paths>`). Use the repo's message style, for example `fix: STEP-003 - cancel the map on a core fault in the book guard and retry a stale relayer nonce once`. Do not push.
