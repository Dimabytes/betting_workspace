# US-004 implementation report — Compose onchain service, import CLI, local live Alchemy day

Status: done. `yarn check` green (49 files / 579 tests); live UTC day
2026-09-20 collected locally through the real two-account Alchemy pairing and
published; PyArrow physical-type check via esports-trader passed on the live
output; real-Mac import dry-run produced a schema-valid bootstrap report with
zero copies. Committed in `polymarket-collector` as `feat: [US-004] - Compose
onchain service, import CLI, local live Alchemy day`.
`current-task/feature.json` US-004 `passes: true`; progress.txt appended. Not
pushed. US-005 not started.

## Files changed (polymarket-collector)

- `src/onchain-app.ts` (new) — the composed process: `runOnchainTick` =
  ensure both archive layouts → make stateDir → acquire `.onchain.lock` →
  sweep stale on-chain work (chunk staging, publish temps, staged `*.tmp`) →
  build both `EndpointHandle`s + `makePool` → `runOnchainPass` →
  `buildPassReport` (today / lastFullDay / lagDays / remainingDays /
  uncoveredTokens / outcomes / fills per game / per-endpoint counters) →
  JSON log + `formatOnchainPass` Telegram. `runOnchainApp` loops the tick on
  a 5-minute cadence (`Effect.repeat` with Schedule) unless `runOnce`;
  per-tick error isolation logs and continues; the disk gate
  (`checkOnchainDisk`, `DiskProbe.statfs` over both roots + stateDir,
  `diskLevelOf`) skips the pass on `stop` (no coverage marked, one deduped
  `formatDiskStop` notice per tick) and notifies once per `warning`
  transition; SIGTERM via `NodeRuntime` releases the lock through the
  scoped acquire.
- `src/onchain-main.ts` (new) — entrypoint: `OnchainSettingsLive` →
  `OnchainDuckDbLive` → `TelegramLive` → platform layers → `runMain`.
- `src/onchain-rpc.ts` — `EndpointHandle` gained `requests` / `bytesIn` /
  `retries` Refs (`requests`++ per wire attempt, `bytesIn` += response byte
  length via `arrayBuffer`, `retries`++ on `RpcRateLimited`/`RpcTransport`),
  and `dispatch` now exposes them for the pass report. Envelope decode moved
  to `Schema.fromJsonString` (no raw `JSON.parse`).
- `src/onchain-config.ts` — added `ONCHAIN_CU_PER_SECOND_{A,B}` (300),
  `ONCHAIN_CONCURRENCY_{A,B}` (4), `ONCHAIN_MONTHLY_CU_{A,B}` (optional
  pre-emptive cutoff), `DISK_WARN_GIB`/`DISK_STOP_GIB` (10/5, stop < warn
  enforced), `ONCHAIN_RUN_ONCE`, blank-URL validation per side, and
  `OnchainDuckDbLive` (the shared `DuckDbSettingsLive` binds the collect
  `Settings` service — unusable inside the onchain app).
- `src/onchain-day.ts` — `runOnchainPass` now returns `{ plans, outcomes }`
  so the report sees both scheduled and skipped days; `DayOutcome`
  `published`/`failed` carry `committedChunks`/`totalChunks` (durable
  progress is reported, not implied).
- `src/fs/layout.ts` — `ONCHAIN_LOCK_FILE_NAME` shared with the sweeper.
- `src/telegram.ts` — `formatOnchainStarted`, `formatOnchainPass`
  (key=value lines, endpoint labels only — never URLs), `formatDiskStop`,
  `formatDiskWarn`.
- `src/onchain-import.ts` (new) — `inventoryOnchainFills` walks
  `asset_id=<token>/<YYYY-MM-DD>.parquet` under a legacy root; keeps only
  real `UtcDate` stems `< today`; per file sha256 + bytes + a DuckDB probe
  (`columnNames`/`columnTypesJson` vs the pinned tree) + row-count/min/max
  stats; anything else lands in `skipped` with a reason; emits
  `OnchainInventory` JSON. `acceptOnchainImport` decodes every inventory,
  loads the catalog, treats a ready `manifests/onchain/<date>.json`
  covering the token as the ONLY `full_day` proof, verifies staged sha256 +
  physical schema + `asset_id`/relPath/date sanity, classifies
  `legacy_imported` (verified non-empty), `legacy_empty_unverified`
  (0 rows — never proof), `rejected`, `conflict` (different bytes at rest —
  preserved), `already_present`, then copies verified files into
  `parquet/onchain_fills/asset_id=<t>/<d>.parquet` via atomic publish,
  computes per-token day `gaps`, and writes the `BootstrapReport`
  (`schemaVersion:1`, `onchainStartDate` = `--start-date` or run date with
  `startDateRule` recorded). `dryRun` writes the same report with zero
  copies and never takes the import lock; the report never lands in
  `manifests/onchain/`.
- `src/onchain-import-main.ts` (new) — `onchain-import inventory --root
  <tree> --game <dota|lol> --out <file>` and `onchain-import accept
  --inventory <file>... --staging-root <dir> [--dry-run]
  [--start-date <d>] [--out <file>]`; `parseArgs` CLI edge; archive roots +
  stateDir from `ARCHIVE_ROOT_*`/`ONCHAIN_STATE_DIR` only (no RPC/start-date
  requirement); `runMain` exit codes.
- `compose.yaml` — fifth service `onchain`: image `polymarket-collector`,
  `init`, `restart: unless-stopped`, `stop_grace_period: 30s`,
  `env_file: [.env]`, `command: node dist/onchain-main.js`, archive binds
  `/var/lib/polymarket-{dota,lol}-archive`, dedicated
  `/var/lib/polymarket-onchain-state`, env interpolation with `:-` so other
  services' compose commands still parse before the operator sets them;
  same logging block; no `POLYMARKET_TAG_ID`/`PROCESS_ROLE`/`nofile`.
- `.env.example` — commented `ONCHAIN_RPC_URL_A`/`_B`, `ONCHAIN_START_DATE`,
  chunk/CU/disk knobs with defaults noted.
- `docs/docker-compose.md` — fifth-service paragraph (key-free env, lock,
  5-minute cadence, disk gate, `docker compose run --rm --no-deps onchain
  node dist/onchain-import-main.js …` shape); existing pinned strings kept.
- `docs/learnings.md` — two new entries (range-error masking by a
  wrong-error sibling endpoint; two legacy onchain parquet schemas).
- Tests: `src/onchain-app.test.ts` (new — single-pass publish e2e on a
  synthetic chain, runOnce no-loop, disk stop→recovery, lock contention,
  stale-work sweep, endpoint counter plumbing, deduped stop notice),
  `src/onchain-import.test.ts` (new — inventory filters, all accept
  statuses, dry-run no-copy/no-lock, gaps, report decode),
  `onchain-rpc.test.ts` (+counter assertions incl. the retry-after-0 clamp
  under TestClock), `onchain-day.test.ts`/`onchain-config.test.ts`/
  `docker.test.ts`/`onchain.testing.ts` updated for the new shapes.

## Verification

### Tests / static checks

`yarn check` (typecheck + Effect diagnostics + oxlint + vitest): 49 files,
579 tests, exit 0. `yarn build` produced `dist/onchain-main.js` and
`dist/onchain-import-main.js`.

### Live Alchemy catch-up (completed UTC day 2026-09-20)

Real sidecars rsynced from the VPS (979 dota + 1087 lol) into
`/tmp/onchain-day/{dota,lol}`; state at `/tmp/onchain-day/state`.
`ONCHAIN_RPC_URL_A`/`_B` exported from `esports-trader/.env`
(`ALCHEMY_POL_ENDPOINT` / `ALCHEMY_POL_ENDPOINT_RESERVE` — two independent
Alchemy accounts; keys never printed or committed), `ONCHAIN_START_DATE=
2026-09-20`, `ONCHAIN_RUN_ONCE=1`.

Result: `tag: "published"` — 2026-09-20 complete for both games
(`committedChunks: 1/1`; the V2 contracts deployed mid-day at block
94,154,038 so the day clamps to a ~8.3k-block supported suffix → one
10000-block ledger chunk). `lastFullDay: 2026-09-20`, `lagDays: 1`,
`remainingDays: 0`, `uncoveredTokens: 0`, fills `dota=2,204` + `lol=18,844`.
386 `parquet/onchain_fills/asset_id=*/2026-09-20.parquet` files plus
`manifests/onchain/2026-09-20.json` under each root (manifest row totals
match the fill counts).

Measurements (no speed claim): wall 6m40s. Endpoint a: 3,732 requests,
795,653,181 bytes in, 115,687 CU, 0 retries. Endpoint b: 14 requests,
31,044 bytes, 798 CU, 0 retries — the pool fails over correctly (b serves
the consensus calls and stands ready); the serial split walk rides a.
Both apps are free-tier (`eth_getLogs` ≤10 blocks, reported as `-32600`
"free tier … 10 block range" → `RpcRangeTooWide`), so `fetchLogsRange`
halved 10000→…→9 and walked ~920 subdivided calls; correct, just slow —
a PAYG tier would take the day in ~1 chunk call per span.

**Reserve-app incident (resolved):** `ALCHEMY_POL_ENDPOINT_RESERVE`
initially returned `-32600 "MATIC_MAINNET is not enabled for this app"` on
every call — the app existed but Polygon was not enabled in the Alchemy
dashboard (dashboard-side, not a code fault; day resolution correctly
refused to run single-ended). The operator enabled Polygon on that app
mid-session; after a few seconds of propagation the same endpoint answered
`0x89` and the day above published with both accounts. Earlier probing of
public RPC endpoints (publicnode/drpc/1rpc) was diagnostic only and is
abandoned — their pruning and unclassified rate-limit codes mask
`RpcRangeTooWide` and stall the split walk; they are not the second
account.

### PyArrow physical-type check (esports-trader, read-only)

`pq.read_table(<live file>).schema.equals(ONCHAIN_SCHEMA)` from
`shared.utils.telonex_onchain` — True on every checked file (8 largest lol
files + all 200 dota files, 0 failures); `validate_target(path, asset_id,
ONCHAIN_REQUIRED_COLUMNS)` valid with matching row counts (5,254 / 1,655 /
719 / 697-row files all pass). The writer's 26-column SNAPPY output is
physically identical to the pinned Telonex schema.

### Real-Mac import dry-run

`inventory` over both real trees (`esports-trader/data/.../onchain_fills`):
dota 19,318 files inventoried / 12,060 skipped; lol 65,302 / 19,032.
`accept --dry-run` with staging symlinked at the same trees (source IS the
staging locally) and archive roots at the scratch roots: 84,620 entries in
~90s — `legacy_imported` 32,916, `legacy_empty_unverified` 51,704, zero
`rejected` (every staged sha256 + schema verified), zero `conflict`,
zero `full_day` (no ready manifests in the scratch roots), zero copies
(confirmed — the archive roots still hold no legacy parquet),
`onchainStartDate: 2026-09-21` (`startDateRule: "run_date"`), 1,332
per-token gap ranges, `tokensWithoutFiles` 112 dota / 100 lol.

**Legacy schema variance (finding, not silently loosened):** 31,092 of the
~116k files carry an older 20-column layout — missing `builder`,
`contract_address`, `metadata`, `outcome_id`, `taker_fee`,
`taker_order_hash`, with several id columns dictionary-encoded — and were
classified `skipped` at inventory (`probe.schema`). They are real dated
fills; whether to accept them with column-filling is a decision for the
operator/US-005 — the check stays strict as planned.

## Parallel-feature safety

Acknowledged in `current-task/agent-replies/US-004-ack-parallel.md` and
`US-004-rpc-ack.md`: only US-004 collector files were touched/committed;
no destructive git operations; `esports-trader` untouched (PyArrow check
was read-only); no trader commits; docs outside this feature untouched.

## Known follow-ups (not this story)

- `ALCHEMY_POL_ENDPOINT_RESERVE` needed Polygon enabled — done by the
  operator during this session; production `.env` should pair two
  Polygon-enabled Alchemy apps (same tier or better) since a dead
  endpoint blocks day resolution by design.
- Free-tier `eth_getLogs` (≤10 blocks) makes catch-up correct-but-slow;
  consider PAYG for the VPS pairing, or accept the multi-minute day walk.
- The 31k old-schema legacy files need an explicit accept path or a
  documented exclusion before VPS bootstrap (US-005).
