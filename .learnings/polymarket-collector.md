# polymarket-collector

## Setup

- Project path: `../polymarket-collector` (sibling of `betting_workspace/`)
- TypeScript daemon that archives Polymarket Dota 2 and LoL markets. Not a fork.
- Yarn Berry + Node 24 + TypeScript 7 + Effect v4. Runs on the VPS through Docker Compose.
- Feedback loop: `yarn check` (typecheck + Effect diagnostics + oxlint + tests). Leave it green.
- This repo carries its own rules and specs. Read them before any change:
  `AGENTS.md`, `docs/polymarket_dota_archive_contracts.md` (normative), `docs/learnings.md`.

## Compose

One binary, five services. Only `archive-dota` has `build: .`; the others share
`image: polymarket-collector:latest`. There is no service named `archive`.

| Service | Role | Host bind | `POLYMARKET_TAG_ID` |
| --- | --- | --- | --- |
| `archive-dota` | collect | `/var/lib/polymarket-dota-archive` | `"102366"` |
| `compact-dota` | compact | `/var/lib/polymarket-dota-archive` | `"102366"` |
| `archive-lol` | collect | `/var/lib/polymarket-lol-archive` | `"65"` |
| `compact-lol` | compact | `/var/lib/polymarket-lol-archive` | `"65"` |
| `onchain` | fills | both archives + `/var/lib/polymarket-onchain-state` | none |

`POLYMARKET_TAG_ID` is required on collect/compact (no default). Empty/whitespace is rejected. Compose
pins the tag per those services; do not set the tag only in `.env`. Compose also pins
`COMPACTION_HOUR_UTC` (Dota 3, LoL 9) and `DUCKDB_MEMORY_LIMIT` (`1024MB`) on the two
compact services; the memory limit is DuckDB `memory_limit` and is what keeps the
LoL day compaction under about 2 GB RSS on the 16 GB VPS. In-container both
roots are still `ARCHIVE_ROOT=/data`. Traders mount those host roots read-only at
`/archive/dota` and `/archive/lol`. Compact is offline parquet; it does not affect
quoting. `onchain` has no tag: it reads both games' `metadata/markets/*.json` and
writes `parquet/onchain_fills` plus `manifests/onchain/<date>.json`. Import
`accept` holds `.onchain.lock` — it must finish before `up -d onchain`. Build
refreshes the image via `docker compose build archive-dota`. `ONCHAIN_RPC_URL_*`
and `ONCHAIN_START_DATE` stay in `.env` (never commit or echo the URLs).

Each daemon holds its lock directory for its whole life: `archive-*` holds
`.collector.lock`, `compact-*` holds `.compactor.lock`, `onchain` holds `.onchain.lock`.
A present lock directory with a fresh mtime is the running service, not a running import.
To see if an import runs, look for an `onchain-import` process or a `docker compose run` container.

## Journal resplit

`journal-resplit` ships in the image as `dist/journal-resplit-main.js`. It repairs a lost hour 00.
When the midnight rotation was skipped, hour 00 of day D sits at the end of `events/<D-1>/23.jsonl.gz`.

- Run: `docker compose run --rm --no-deps archive-<game> node dist/journal-resplit-main.js --root /data --date <D> [--dry-run]`.
- It cuts `events/<D-1>/23.jsonl.gz` at the first record with `receivedAtUs ≥ D 00:00`.
  The tail becomes `events/<D>/00.jsonl.gz`.
- It publishes only if `gunzip(head) ++ gunzip(tail)` equals `gunzip(original)`.
- The original goes to `<root>/.recovery/<ISO ts>/events/<D-1>/23.jsonl.gz`. `--backup-dir` overrides `<root>/.recovery/<ISO ts>`.
- `--dry-run` only reports counts and takes no lock.
- A real run takes `.compactor.lock`. `compact-<game>` holds it while it runs, so stop `compact-<game>` first.
- It refuses if `events/<D>/00.jsonl.gz` or `00.jsonl.partial` exists, or if no record of D sits in the file.
  It also refuses if a tail record falls outside hour 00 of D.
- After a resplit, recompact D-1 and D in date order. Move both manifests out of `manifests/`.
  Leave `checkpoints/` alone: `publishDay` rewrites them.

## WS↔onchain reconcile

The `onchain` service, not compact, writes `manifests/reconcile/<date>.json` in each game's archive root.

- A date qualifies when `manifests/onchain/<date>.json` is ready, `manifests/<date>.json` is complete, and no reconcile file exists.
- Dates go in ascending order, at most 7 per game per pass. A pass runs every 5 minutes. No RPC calls.
- The report joins WS `trades.trade_id` with onchain `tx_hash`. An onchain tx with no WS trade is a miss.
- Five or more misses, each within 120 s of the previous one, form a stall window.
- The Telegram `onchain pass` message gets `reconcile=<game>/<date> unmatched=<n>/<txs> windows=<k>`.
  A line with stall windows starts with `STALL`.
- The service never recomputes an existing file. To recompute after a recompaction, move the file out of `manifests/reconcile/`.
- Both manifests must exist. Days before the first `manifests/onchain/<date>.json` (2026-09-20) get no reconcile report.

## What it produces

`ARCHIVE_ROOT` (env) holds every output. Docker is a way to run it, not a storage format:
the outputs sit in a host bind mount.

- `metadata/markets/<condition_id>.json` — stable Market sidecar: `conditionId`, `marketSlug`,
  `marketKind` (`map_winner` / `series_winner`), `mapNumber`, `outcomes[].name`,
  `outcomes[].tokenId`, `tickSize`, `minOrderSize`, `negRisk`, `active`, `closed`,
  `acceptingOrders`, `startAt`, `gridSeriesId`. Every decimal is a string.
- `metadata/events/<event_id>.json` — latest full Gamma Event payload.
- Hourly append-only journal, then daily `book_snapshot_full` and `trades` Parquet,
  a daily checkpoint and a manifest. Parquet is Telonex-compatible.
- `market_id` in Parquet is the CTF `conditionId`, not the Gamma Market ID.
  `asset_id` is the full decimal CLOB token ID. Never turn a token ID into a number.

## Discovery

- Polls Gamma Events every 60 seconds with the service's `POLYMARKET_TAG_ID`,
  `active=true`, `closed=false`. Dota is `102366`, LoL is `65`. There is no v1
  default tag.
- Keeps two market kinds: `moneyline` + `Match Winner` (series) and
  `child_moneyline` + `Game N Winner` (map).
- A missing Event in one poll is not a signal to drop the subscription. A token leaves the
  stream only after a durable terminal payload is written.
- Any unseen `(sportsMarketType, groupItemTitle)` pair lands in the journal and the manifest.

## Reuse from other projects

- Do not write another Gamma discovery for Dota or LoL. Read `metadata/markets/*.json` instead.
- Do not copy the order book into another archive. Read it by `asset_id` and day.
- Reading the sidecars needs access to that game's archive root. Run the collector locally
  through its `compose.yaml`, or mount the VPS directory.
- Fresh sidecars (mtime last 2h) live under `metadata/markets/*.json`.
- Telegram alerts already exist here: `TG_BOT_API_TOKEN` and `TG_CHAT_ID`.
