# polymarket-collector

## Setup

- Project path: `../polymarket-collector` (sibling of `betting_workspace/`)
- TypeScript daemon that archives Polymarket Dota 2 markets. Not a fork.
- Yarn Berry + Node 24 + TypeScript 7 + Effect v4. Runs on the VPS through Docker Compose.
- Feedback loop: `yarn check` (typecheck + Effect diagnostics + oxlint + tests). Leave it green.
- This repo carries its own rules and specs. Read them before any change:
  `AGENTS.md`, `docs/polymarket_dota_archive_contracts.md` (normative), `docs/learnings.md`.

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

- Polls Gamma Events every 60 seconds with `tag_id=102366`, `active=true`, `closed=false`.
  The tag id is explicit configuration; do not change the v1 default automatically.
- Keeps two market kinds: `moneyline` + `Match Winner` (series) and
  `child_moneyline` + `Game N Winner` (map).
- A missing Event in one poll is not a signal to drop the subscription. A token leaves the
  stream only after a durable terminal payload is written.
- Any unseen `(sportsMarketType, groupItemTitle)` pair lands in the journal and the manifest.

## Reuse from other projects

- Do not write another Gamma discovery for Dota. Read `metadata/markets/*.json` instead.
- Do not copy the order book into another archive. Read it by `asset_id` and day.
- Reading the sidecars needs access to `ARCHIVE_ROOT`. Run the collector locally through its
  `compose.yaml`, or mount the VPS directory.
- Telegram alerts already exist here: `TG_BOT_API_TOKEN` and `TG_CHAT_ID`.
