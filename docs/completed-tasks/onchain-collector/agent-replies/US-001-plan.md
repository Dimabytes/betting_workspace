US-001 plan written: current-task/plans/US-001.md
Scope: catalog from both archives' market sidecars (conflict diagnostics, conservative include), OrderFilled/OrdersMatched decoder for both V2 exchanges, DuckDB writer for the 26-col ONCHAIN_SCHEMA.
New files: src/schema/onchain.ts, src/onchain-catalog.ts, src/onchain-decode.ts, src/onchain-writer.ts, src/onchain.testing.ts + 3 tests + test/fixtures/onchain/.
One edit: duckdb-session.ts gets optional workRootName (".onchain" for onchain scratch).
Verified now: V2 ABI layout + topic hashes vs official Events.sol; real Telonex files are 26-col (0x hashes) but a 20-col vintage exists — documented as mismatch.
Key gotchas captured: per-contract taker-aggregate drop, (contract,tx) OrdersMatched keying, 6-dec half-even bigint price, mirrored=1−rounded-p, spec sort differs from Python's.
Fixtures: copy real Telonex parquets; no recorded RPC exists — plan marks an optional one-time eth_getLogs capture, else synthetic builders.
Gate: yarn check green; no new deps; US-002/003/004 explicitly out of scope.
