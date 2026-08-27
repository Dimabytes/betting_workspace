# dota_2_model

## Setup

- Project path: `../dota_2_model` (sibling of `betting_workspace/`)
- Read `../dota_2_model/AGENTS.md` before any work.
- Live daemon on this VPS: Docker Compose service `live-paper` from
  `../dota_2_model` (`compose.yaml`). Container name `dota_2_model-live-paper-1`.
- `src`, `config`, and `data/new_model` are bind-mounted. Python/TOML/model
  edits skip `--build`. WalletHost still loads `dota-map.toml` and `production/`
  once at boot.

## Kalshi overlay

Kalshi sits next to Polymarket in the same WalletHost. Independent size, book,
fair, orders, sqlite. A Kalshi miss does not stop PM.

| What | Where |
| --- | --- |
| Env | `.env` (gitignored): `KALSHI_TRADING`, `KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, optional `KALSHI_SUBACCOUNT` (default 0). Smoke-only: `KALSHI_DEMO_KEY_ID`, `KALSHI_DEMO_PRIVATE_KEY_PATH`. Never `KALSHI_PRIVATE_KEY`. |
| PEM | Host file mounted read-only into the container at the same path as `KALSHI_PRIVATE_KEY_PATH`. Unencrypted PKCS8. Do not log contents. |
| TOML | `config/dota-map.toml` `[kalshi]`: `base_size_usd` (not derived from `base_size_usdc`), `book_stale_s`, `private_ws_blind_s`, `reconcile_interval_s`, `fence_timeout_s`. Not copied into fork TOML. |
| Sqlite | `data/live_paper/wallet/kalshi.db` + flock `kalshi.db.lock`. Paper and live share the file (`source` column). |
| match.json | Schema 4, always-present `kalshi` object. Schema 3 reads as Kalshi off. Bind writers live in `kalshi_meta.py`. |
| session.jsonl | Schema 6, `venue` `polymarket` \| `kalshi` on trade rows. Schema 1–5 omit venue = PM. Kalshi writers live in `kalshi_journal.py` (fill cursor = last `position_ledger.seq`). |
| Ownership | `client_order_id` prefix `d2m-{sub}-` (hyphens; demo rejects dots). |
| Smoke | `uv run python scripts/kalshi_execution_smoke.py --env demo`. WalletHost never starts it. `--env production` is rejected. |

Module map (`src/live_paper/`):

| File | Role |
| --- | --- |
| `kalshi_leg.py` | `OutcomeSide`, `BookSide`, `KalshiLeg`, `series_ticker_from_kind` |
| `kalshi_types.py` | REST/WS errors and frozen values. Consumers import from here, no re-exports |
| `kalshi_ws.py` | Auth WS client and frame parsers |
| `kalshi_client.py` | REST only |
| `kalshi_meta.py` | `match.json` kalshi block |
| `kalshi_journal.py` | session JSONL Kalshi rows |
| `process_lock.py` | `acquire_file_lock(path)`. Wallet flocks the db file; Kalshi flocks `kalshi.db.lock` |
| `kalshi_runtime.py` | WS pumps, poll (survives exceptions), `KalshiHostWire` |
| `kalshi_observe.py` | Public resolve + prior. Retries after a candle GET that dies on null close |

`KALSHI_TRADING`: `off` / `observe` / `paper` / `live`. Default `off`. Independent
of `LIVE_TRADING`. First cards 2026-08-28: `paper`.

Boot log line (health check): `live-paper config loaded [engine] [risk] [profiles] [wallet] [kalshi] kalshi_trading=…`.

A 1-minute candle with `yes_bid.close: null` fails the whole candlestick GET
inside the SDK. Prior load retries. `_poll_loop` logs and continues; it is not
a one-shot task.

## What to restart

From `/root/work/dota_2_model`. Do not `compose down`.

| What changed | What to do |
| --- | --- |
| `pyproject.toml` / `uv.lock` / Dockerfile / kalshi-sdk / poly-maker copy | `docker compose build`, confirm green, then `docker compose up -d` |
| `src/` Python (`kalshi_*.py`, `process_lock.py`, `match_meta.py`, `session_journal.py`, `wallet_host.py`, …), `config/dota-map.toml` including `[kalshi]`, production model | `docker compose restart live-paper` (bind-mounted; no rebuild) |
| `.env` `KALSHI_*` or the PEM volume in `compose.yaml` | `docker compose up -d` (recreate). `restart` does not re-read `env_file` |
| Docs only (`docs/live-paper.md`) | nothing on the VPS |

A restart detaches any live market. Matches start 2026-08-28.

Paper→live on the same `kalshi.db` is safe: REST compare ignores `source=paper`
leftover. Do not invent a second db name.
