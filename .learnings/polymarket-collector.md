# polymarket-collector

## Setup

- Project path: `../polymarket-collector` (sibling of `betting_workspace/`)
- TypeScript daemon that archives Polymarket Dota 2 and LoL markets. Not a fork.
- Yarn Berry + Node 24 + TypeScript 7 + Effect v4. Runs on the VPS through Docker Compose.
- Feedback loop: `yarn check` (typecheck + Effect diagnostics + oxlint + tests). Leave it green.
- This repo carries its own rules and specs. Read them before any change:
  `AGENTS.md`, `docs/polymarket_dota_archive_contracts.md` (normative), `docs/learnings.md`.

## Compose

One binary, six services. Only `archive-dota` has `build: .`; the others share
`image: polymarket-collector:latest`. There is no service named `archive`.

| Service | Role | Host bind | `POLYMARKET_TAG_ID` |
| --- | --- | --- | --- |
| `archive-dota` | collect | `/var/lib/polymarket-dota-archive` → `/data` | `"102366"` |
| `archive-lol` | collect | `/var/lib/polymarket-lol-archive` → `/data` | `"65"` |
| `archive-cs2` | collect | `/var/lib/polymarket-cs2-archive` → `/data` | `"100780"` |
| `compact` | compact | Dota → `/data-dota`, LoL → `/data-lol`, CS2 → `/data-cs2` | none |
| `onchain` | fills | all three archives + `/var/lib/polymarket-onchain-state` | none |
| `ship` | NAS push + prune | all three archives + `/var/lib/polymarket-ship` → `/state` | none |

`POLYMARKET_TAG_ID` is required on collect only (no default). Empty/whitespace is rejected.
Compose pins the tag per collect service; do not set the tag only in `.env`. The three
collect services use `ARCHIVE_ROOT=/data`. `compact` has no tag and no `ARCHIVE_ROOT`:
it reads `ARCHIVE_ROOT_DOTA=/data-dota`, `ARCHIVE_ROOT_LOL=/data-lol` and
`ARCHIVE_ROOT_CS2=/data-cs2` and takes `game=` from that fixed list. At
`COMPACTION_HOUR_UTC=3` it compacts Dota, then LoL, then CS2, in
one process; a failed day or scan of one game does not skip the other. Compose pins
`DUCKDB_MEMORY_LIMIT` (`1536MB`) on `compact`; it is DuckDB `memory_limit` and keeps one
day under about 2 GB RSS on the 16 GB VPS. Traders mount the host roots read-only at
`/archive/dota` and `/archive/lol`. Compact is offline parquet; it does not affect
quoting. `onchain` has no tag: it reads each game's `metadata/markets/*.json` and
writes `parquet/onchain_fills` plus `manifests/onchain/<date>.json`. A pass walks
Dota, then LoL, then CS2. `ONCHAIN_START_DATE_CS2=2026-10-23` is pinned in Compose,
not `.env`. Dota and LoL keep `ONCHAIN_START_DATE`. CS2 takes part in an onchain day
only from 2026-10-23 UTC; earlier days need no CS2 manifest and are not reopened.
Import
`accept` holds `.onchain.lock` — it must finish before `up -d onchain`. Build
refreshes the image via `docker compose build archive-dota`. `ONCHAIN_RPC_URL_*`
and `ONCHAIN_START_DATE` stay in `.env` (never commit or echo the URLs).

Each daemon holds its lock directory for its whole life: `archive-*` holds
`.collector.lock`, `compact` holds `.compactor.lock` in every archive root, `onchain` holds `.onchain.lock`,
`ship` holds `.ship.lock` in every archive root.
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
- A real run takes `.compactor.lock`. `compact` holds it in both roots while it runs, so stop `compact` first.
  That pauses compaction of both games. Start it again with `docker compose up -d compact`.
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

## Ship to NAS and prune

`ship` (`node dist/ship-main.js`) pushes closed days of Dota, LoL and CS2 to the NAS `pizza` over Tailscale with rsync + ssh.
Contract: `docs/polymarket_dota_archive_contracts.md` §13.2. Runbook: `docs/docker-compose.md` "NAS ship service".
`ship` owns new CS2 days on the NAS `cs2/` from the first `archive-cs2` day.
`esports-trader` `src/cs2/s08_fetch_telonex_books.py` writes only days before that day, never a day `ship` sent or prune removed from the VPS.

- `SHIP_TARGET=ship@100.67.62.33` in `.env`. Use the IP: the container does not resolve MagicDNS names.
- The NAS key runs `rrsync -wo -no-del`: it adds and overwrites files, never reads or deletes them.
  You cannot list the NAS through this key. Check NAS contents on `pizza` itself.
- On `pizza` the target is ZFS dataset `tank/polymarket` (`dota/`, `lol/`, `cs2/`). Sanoid takes one snapshot
  a day and keeps 30. Restore a file from `/tank/polymarket/.zfs/snapshot/<snapshot>/<path>`.
- A pass runs at start, then every `SHIP_INTERVAL_MINUTES` (60). A day is sha256-checked locally, pushed,
  checksum-verified on the NAS, then receipted in `manifests/shipped/<date>.json` or `manifests/shipped/onchain/<date>.json`.
- A receipt is live when its `manifestSha256` matches the current manifest bytes. A republished manifest ships the day again.
- A pass logs `ship day ok` per day and nothing at its end. The prune result goes only to Telegram.
- Telegram alerts, once a day each: NAS unreachable 24 h, manifests not listable, a closed day not shipped
  48 h after it ended, an archive filesystem above 80% used.

Prune is on since 2026-10-23 (`SHIP_PRUNE=true` in `.env`). It deletes on the VPS only files of a manifest
with a live receipt whose sha256 and size still match.

- Journal day D goes when receipts D and D−1 are live and D < today − `EVENTS_KEEP_DAYS` (default 14).
- Parquet day D goes when its receipt is live and D < today − `PARQUET_KEEP_DAYS` (default 45).
- `metadata/`, `manifests/`, `checkpoints/` stay on the VPS forever.
- The first run freed 172 GB (310 → 138 GB used on `/`). Steady state is about 77 GB of archive.
- A pruned journal day cannot be recompacted or resplit on the VPS. Copy its `events/<D>/` back from `pizza` first.
- Do not move `ONCHAIN_START_DATE` back past today − 45 days, and do not delete reconcile files of such days,
  until their `trades` parquet is back from the NAS. Reconcile of those days fails every pass and fills all 7 slots.
- Legacy Telonex `onchain_fills` (days without `manifests/onchain/<date>.json`, everything before 2026-09-20)
  have no receipt. `ship` never sent them and prune never deletes them: 3.6 GB stay on the VPS.
- The live trader reads the journal 24 h back (`esports-trader/src/trader/book_prior.py`) and `metadata/`.
  Backtests read books from the NAS. Both sit inside the prune windows.
- If the NAS is lost: `book_snapshot_full`, `trades`, `onchain_fills` come back from Telonex (same format,
  not byte-identical). The journal older than 14 days is gone; it is needed only to recompact.

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
- `onchain_fills` from 2026-09-18 (collector-decoded): `taker_asset_id` is the token
  opposite the maker on every row (`src/onchain-decode.ts`), not the taker's token.
  Up to 2026-09-17 (Telonex import) it meant the maker's token on a direct trade. Read
  the side from `maker_asset_id` and `maker_side`; the backtest does
  (`esports-trader/src/backtest/onchain_side.py`). Checked 2026-10-09.

## Discovery

- Polls Gamma Events every 60 seconds with the service's `POLYMARKET_TAG_ID`,
  `active=true`, `closed=false`. Dota is `102366`, LoL is `65`, CS2 is `100780`. There is no v1
  default tag.
- Keeps two market kinds: `moneyline` + `Match Winner` (series) and
  `child_moneyline` + `Game N Winner` or `Map N Winner` (map). CS2 names map markets `Map N Winner`.
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
