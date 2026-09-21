US-002 plan written: current-task/plans/US-002.md
Scope: JSON-RPC over Effect HttpClient (chain 137, both V2 exchanges, OrderFilled+OrdersMatched), typed errors incl. 400-body range classification (no filterStatusOk), two Redacted endpoints with per-endpoint CU pacing/429 pause/quota, failover + committed-range gate, finalized-only UTC day bounds with consensus checks, unsupported-period error.
New files: src/onchain-rpc.ts, src/onchain-ledger.ts (stub seam; durable impl is US-003), src/onchain-days.ts, src/onchain-config.ts + 2 tests + test/fixtures/onchain/rpc/ bodies; extends src/onchain.testing.ts.
Verified now: beta.105 HttpClient API surface and fake seam (HttpClient.make + fromWeb); no built-in rate limiter (hand-rolled CU bucket); HttpClientError.message leaks the URL — typed errors generate their own messages.
Key semantics copied from parity_onchain_rpc.py: RANGE_ERROR_MARKERS, ±200k guess-window binary search (predicate flipped to first>=), chunk halving, bounded retries + Retry-After.
Design decisions flagged: unclassified JSON-RPC errors -> RpcInvalidPayload (never empty success); range-too-wide may migrate to the other endpoint before the splitter halves; V2_MIN_BLOCK pinned at implementation time via eth_getCode probe.
Test matrix covers every required case offline: 400/range split, 429+Retry-After under TestClock, partial quota, lost/invalid payload, disagreeing endpoints, unfinalized head, CU pacing, concurrency ceiling, committed-skip, unsupported period.
Non-goals explicit: planner/ledger/publish/compose/onchain-main/VPS deferred; OnchainLedger interface ships now, durable impl lands US-003.
Gate: yarn check green; no new deps; esports-trader read-only reference, poly-maker untouched.
