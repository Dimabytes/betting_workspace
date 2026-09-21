# US-002 fix pass — review findings

Commit `3e0f892` in `polymarket-collector` (on top of `d230388`). `yarn check`
green. US-002 `passes: true` kept; progress.txt appended. Not pushed.

## Fixed — finding 1: `Option` out of `run`, gate next to the ledger

- `OnchainRpc.run` now returns `Effect<A, RpcError>` — a committed range is no
  longer smuggled through the transport API as `Option.none`.
- `resolveDay`'s `exec` wrapper and its `Option.getOrThrow` lie are gone;
  `timestampOf` calls `rpc.run` directly.
- New `unlessCommitted(ledger, range, effect)` in `src/onchain-ledger.ts`
  returns `Option<A>` — honest there because skipping is its whole contract.
  The caller wraps whatever effect it owns (typically a whole
  `fetchLogsRange` walk), which also fixes the reviewer's composition hole:
  halved sub-spans never reach the gate, so no orphan ledger keys.
- `OnchainLedger.markCommitted(range, receipt: unknown)` →
  `markCommitted(range)` — the ignored second argument was a hole, not a
  seam; US-003 defines the durable record's fields when it implements it.
- `RpcTask.range` field deleted — nothing consumes it now (`BlockRange`
  import dropped from `onchain-rpc.ts`).
- `makePool(a, b, ledger)` → `makePool(a, b)`; `OnchainRpcLive` no longer
  requires `OnchainLedger` (the durable ledger is a planner dependency, not a
  dispatch one).

## Fixed — finding 2: unrolled the N-endpoint scheduler

`run` is now one flat pass loop over the pair:

- One `snapshot` helper, one read per pass — the copy-pasted inner/outer
  `Effect.forEach` state blocks are gone.
- Ready endpoints are tried least-`inFlight` first (tie → `a`); a failed task
  attempts the sibling — including `RpcRangeTooWide` (different tier caps).
- Re-passes happen only for `RpcTransport`/`RpcRateLimited`; range-too-wide
  and invalid payload stop immediately — *unless* a paused sibling never got
  its attempt this pass (`paused.length === 0` guard), which the new test
  pins: quota-dead A + paused B → B is tried after its resume.
- All-surviving-paused sleeps until the earliest `pauseUntil`; pass-end sleep
  is `min(earliest resume, exponential backoff)`; bounded by
  `MAX_DISPATCH_PASSES = 6`.
- `prefer`, the `tried` set, and the inner `for(;;)` are deleted.

## Skipped / disagreed

- Nothing skipped. The reviewer's two findings were applied in full; the
  committed-skip acceptance test survives via `unlessCommitted` (and now also
  asserts the uncommitted span actually dispatches).
- `onBoth` agreement calls stay direct on `a`/`b`, per the review — unchanged.

## Tests

`yarn check` in `polymarket-collector`: typecheck 0 errors; diagnostics
0 errors / 0 warnings / 5 messages (pre-existing); oxlint 0/0 on 96 files;
vitest 40 files / **523 tests** passed (was 522; +1 for the
paused-sibling-after-quota-death case). Pre-commit hook re-ran it — green.

## Commits

- `d230388` — US-002 implementation (unchanged).
- `3e0f892` — `fix: [US-002] - Committed-range gate out of dispatch; flat
  two-endpoint loop` (4 files, +127/−112).
