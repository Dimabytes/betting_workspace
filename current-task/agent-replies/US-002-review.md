# US-002 review — RPC client, two endpoints, UTC block bounds

Commit `d230388` in `polymarket-collector`. The single-endpoint pipeline is the right shape: envelope on every HTTP status, generated errors (no URL leak), no `filterStatusOk`, CU bucket / semaphore / pause / quota as handle state rather than two `Context.Service`s. `fetchLogsRange` as a callback walker, `firstBlockAtOrAfter` as a lower-bound search, and `resolveDay` as a pending/ready/unsupported decision are doing the job the step asked for. Approval still fails: `OnchainRpc.run` is a persistence gate plus a general N-endpoint scheduler, and that contract is what US-003 will have to live with.

## 1. Ledger skip inside `run` is the wrong layer. `Option.none` is not an RPC result.

`OnchainRpc.run` returns `Option.Option<A>`. `none` means “this exact `{from,to}` is already in the ledger.” That is not a JSON-RPC outcome. It is a planner invariant smuggled through the transport API.

Consequences already in this step:

- `resolveDay` immediately `Option.getOrThrow`s every `run` call, with a comment that range-free tasks never skip. The Option is a lie for `eth_chainId` / `eth_getBlockByNumber`. The type does not say that; the comment does.
- `fetchLogsRange` still takes `Effect<ReadonlyArray<RpcLog>, RpcError>`. It cannot take `run` without an unwrap. The tempting unwrap is `none → []`, which would publish an empty span for a committed range. Empty logs and “already committed” are different facts. US-003 will have to remember that on every call site.
- `OnchainLedger.markCommitted(range, receipt: unknown)` ignores `receipt`. The stub’s second argument is a hole in the contract, not a seam.
- Halved spans are different `BlockRange` keys than the parent walk. The dispatcher’s exact-range skip does not compose with the splitter that actually issues `eth_getLogs`. “Never reassigned” cannot be enforced here.

The code-judo move: `run(task): Effect<A, RpcError>`. Keep `OnchainLedger` + `OnchainLedgerMemory` if this step must prove the skip, but as a helper next to the ledger (`isCommitted` then execute, else skip). The planner in US-003 is the canonical owner of “do not resubmit a committed chunk.” Delete the Option from the pool.

Exposing `a` / `b` for agreement (`onBoth`) is correct and should stay direct. Do not make those calls go through a skip-aware `run`.

## 2. The dispatcher is a general N-endpoint scheduler for a pair. Unroll it.

`makePool` is nested `for pass` / `for (;;)` with a `Set<EndpointHandle>`, a duplicated `Effect.forEach` snapshot (inner loop and again after break), pause-wait inside the inner loop, then another pause/backoff sleep on the outer loop, plus a `prefer?: "a" | "b"` override that no test passes.

Two Alchemy accounts are a pair, not a pool of N. The behavior the step needs is:

1. pick the non-dead, non-paused handle with fewer `inFlight` (tie → `a`)
2. on failure, attempt the other
3. if both are paused, sleep until the earlier `pauseUntil`
4. bounded re-passes for transport / 429 only; range-too-wide and invalid payload stop

That is one pass loop and a two-element pick. No `tried` set, no inner infinite loop, no `prefer`. The inner “sleep paused endpoints in place so the pass still tries them” is the same wait the outer loop already does after a failed pass.

`options.prefer` was listed as a test hook. The tests never use it; equal `inFlight` already picks `a`. Delete it until a test exists that cannot pin order another way.

Copy-pasted snapshot:

```114:122:../polymarket-collector/src/onchain-days.ts
          const states: Array<EndpointState> = yield* Effect.forEach(
            endpoints,
            (handle) =>
              Effect.all({
                dead: Ref.get(handle.quotaDead),
                pauseUntil: Ref.get(handle.pauseUntil),
                inFlight: Ref.get(handle.inFlight)
              }).pipe(Effect.map((s) => ({ handle, ...s })))
          )
```

appears again at the outer-loop `aliveStates` read. After unroll, both copies go away with the inner loop.

---

No file crosses 1k lines. Marker tables, finalized-only readiness, and the recorded 400-range fixture are the right constraints. `onBoth` for chain-id / control-hash agreement is the right split from failover.

Fix 1 before US-003 builds the planner on an RPC result that sometimes means “no logs” and sometimes means “do not look.” Fix 2 in the same pass — it is the same function.
