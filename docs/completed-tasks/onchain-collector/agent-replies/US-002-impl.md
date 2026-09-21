# US-002 implementation report — RPC client, two endpoints, UTC block bounds

Status: done. `yarn check` green; committed in `polymarket-collector` as `d230388`.
`current-task/feature.json` US-002 `passes: true`; progress.txt appended. Not pushed.

## Files changed (polymarket-collector)

- `src/onchain-rpc.ts` (new) — single-endpoint JSON-RPC layer:
  - `CU_COSTS` {eth_getLogs: 75, eth_getBlockByNumber: 16, eth_blockNumber: 10,
    eth_chainId: 0}; `V2_MIN_BLOCK = 94_154_038` — first block with non-empty
    `eth_getCode` for CTF Exchange 0xe111…996b, binary-searched against
    polygon-bor-rpc.publicnode.com on 2026-09-20 (Neg Risk deploys at
    94154045, four blocks later).
  - Typed errors (all `Schema.TaggedError`, endpoint label + method):
    `RpcRangeTooWide`, `RpcRateLimited` (bounded `retryAfterMs`),
    `RpcQuotaExhausted`, `RpcTransport`, `RpcInvalidPayload`
    (carries `rpcCode`/`rpcMessage`); union `RpcError`.
  - `makeEndpoint(config)` → `EndpointHandle` plain record:
    `config`/`execute`/`pauseUntil`/`quotaDead`/`usedCu`/`inFlight`.
    Per call: Semaphore permit → pauseUntil sleep → CU token-bucket
    reservation (`Ref.modify` on nextSlotMs) → POST via
    `HttpClientRequest.bodyJsonUnsafe` → 60s timeout → classify.
    Envelope is parsed on EVERY status; markers classify quota → range →
    rate-limit (bare 429 forces rate-limit) → else invalid payload.
    Malformed/missing results are `RpcInvalidPayload`, never empty success.
    URLs never enter errors/logs/spans: messages are generated, request
    tracing disabled via `TracerDisabledWhen`, log annotations carry
    label+method only.
  - `getLogsTask`/`getBlockTask`/`getBlockNumberTask`/`chainIdTask` plus
    `getLogs`/`getBlock`/`chainId`/`blockTimestamp` (caller-owned Map cache);
    `fetchLogsRange` ports the Python chunk-halving walk (min 1-block span).
- `src/onchain-days.ts` (new) — `OnchainRpc` service + `makePool(a, b, ledger)`:
  least-inFlight dispatch with `prefer` override, one attempt per endpoint per
  pass, failover incl. range-too-wide, `isCommitted` gate returns `Option.none`
  (committed ranges never reassigned), bounded passes (6) with backoff /
  pause-resume sleeps, deterministic errors break early.
  `firstBlockAtOrAfter` — lower-bound binary search, ±200k guess window.
  `resolveDay(date)` — `utcDayBoundsUs` → chain-id 137 agreement on both
  endpoints → `eth_getBlockByNumber("finalized")` head only (null →
  `finalized_unsupported`, head before day end → `unfinalized`, never
  `latest`/`eth_blockNumber`) → bound search → control-block number/hash
  agreement on both endpoints (`control_hash_disagree`) → `to <= V2_MIN_BLOCK`
  fails `OnchainUnsupportedPeriod`, straddle clamps `from` to V2_MIN_BLOCK.
  `OnchainRpcLive(config)` layer requires `OnchainLedger` (US-003 provides
  the durable implementation).
- `src/onchain-ledger.ts` (new) — explicit US-003 seam: `OnchainLedger`
  service (`isCommitted`/`markCommitted`) + `OnchainLedgerMemory` test layer.
  No file I/O.
- `src/onchain-config.ts` (new) — `OnchainSettings` service +
  `validateOnchainSettings`/`loadOnchainSettings`/`OnchainSettingsLive`
  mirroring `config.ts`: `ONCHAIN_RPC_URL_A/B` (required, Redacted),
  `ONCHAIN_CU_PER_SECOND_A/B` (default 300), `ONCHAIN_CONCURRENCY_A/B`
  (default 4), `ONCHAIN_MONTHLY_CU_A/B` (optional pre-emptive cutoff).
- `src/onchain.testing.ts` (extended) — `fakeHttpClient` (handler returns a
  `Response` or `Effect<Response, HttpClientError>`), `scriptedRpc` (records
  every call), `rpcResult`/`rpcErrorBody`/`httpResponse`/`blockJson`,
  `makeTestEndpoint` (Infinity CU pacing so frozen TestClock never sleeps,
  concurrency 8, no quota).
- `src/onchain-rpc.test.ts` (new) — 16 tests: recorded getlogs fixture parse +
  request filter shape (both addresses, both topics), 400-range halving with
  full `[1,200]` coverage, 1-block-still-too-wide, 400 without markers →
  invalid, 200-envelope range classification, 429 Retry-After pause + queued
  call resumes under TestClock, quota-dead permanence, null/non-array/bad-log/
  non-JSON → `RpcInvalidPayload`, transport + timeout → `RpcTransport` with no
  URL in the message, CU pacing 750ms slots + `usedCu`, concurrency ceiling 2,
  5xx → transport, V2_MIN_BLOCK pin.
- `src/onchain-days.test.ts` (new) — 14 tests: bound resolution on/off the 2s
  grid (midnight-exact and mid-block edge), unfinalized, finalized-null,
  chain-id disagree, control-hash disagree, missing `to` block, transport
  failover A→B, range-too-wide A→B without halving, 429 migrate, committed
  range skip (zero getLogs calls), unsupported pre-V2 day, straddle clamp,
  both-dead → `RpcQuotaExhausted`.
- `test/fixtures/onchain/rpc/` (new) — `error-400-range.json`,
  `error-quota.json`, `block-finalized.json`.
- `docs/learnings.md` — prepended entry: parse the JSON-RPC envelope on every
  HTTP status; URL redaction via generated errors + disabled request tracing;
  finalized-only readiness; frozen-TestClock + sub-ms CU sleeps deadlock
  unforked fibers (pacing tests must fork + adjust).

## Tests run

`yarn check` in polymarket-collector: typecheck 0 errors; Effect diagnostics
0 errors / 0 warnings / 5 messages (pre-existing suggestions); oxlint
0 warnings / 0 errors (96 files); vitest 40 files / 522 tests passed.
A pre-commit hook re-ran the same check during `git commit` — green.

## Commit

`d230388` `feat: [US-002] - RPC client, two endpoints, UTC block bounds`
(11 files, +1807/−1). Work stays on `main`, not pushed.

## Leftover risks / notes for US-003+

- Marker tables are Python-verbatim + conservative fallbacks; the real
  Alchemy monthly-quota body shape is unverified — confirm on the US-004
  live day and adjust markers if the text differs.
- `usedCu` is per-process; provider quota remains authoritative. `monthlyCu`
  cutoff is opt-in.
- `resolveDay` agreement calls (`onBoth`) hit both endpoints directly — a
  transport failure there fails the day as `RpcError` rather than pending;
  that is the contract (agreement is impossible one-sided).
- Unknown methods fall back to the heaviest CU weight (75) — conservative.
- `OnchainLedgerMemory` forgets on restart by design; US-003 swaps in the
  durable layer and owns `markCommitted` receipts.
- No planner, publication, compose service, entrypoint, or env wiring yet —
  all US-003/US-004 scope as planned.
