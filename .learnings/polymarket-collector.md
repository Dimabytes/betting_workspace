# polymarket-collector

## Setup

- Project path: `../polymarket-collector` (sibling of `betting_workspace/`)
- TypeScript daemon that archives Polymarket Dota 2 and LoL markets. Not a fork.
- Yarn Berry + Node 24 + TypeScript 7 + Effect v4. Runs on the VPS through Docker Compose.
- Feedback loop: `yarn check` (typecheck + Effect diagnostics + oxlint + tests). Leave it green.
- This repo carries its own rules and specs. Read them before any change:
  `AGENTS.md`, `docs/polymarket_dota_archive_contracts.md` (normative), `docs/learnings.md`.

## Compose

One binary, four services. Only `archive-dota` has `build: .`; all four share
`image: polymarket-collector:latest`.

| Service | Role | Host bind | `POLYMARKET_TAG_ID` |
| --- | --- | --- | --- |
| `archive-dota` | collect | `/var/lib/polymarket-dota-archive` | `"102366"` |
| `compact-dota` | compact | `/var/lib/polymarket-dota-archive` | `"102366"` |
| `archive-lol` | collect | `/var/lib/polymarket-lol-archive` | `"65"` |
| `compact-lol` | compact | `/var/lib/polymarket-lol-archive` | `"65"` |

`POLYMARKET_TAG_ID` is required (no default). Empty/whitespace is rejected. Compose
pins the tag per service; do not set the tag only in `.env`. In-container both
roots are still `ARCHIVE_ROOT=/data`. Traders mount those host roots read-only at
`/archive/dota` and `/archive/lol`. Compact is offline parquet; it does not affect
quoting.

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
