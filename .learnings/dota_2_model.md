# dota_2_model

## Setup

- Project path: `../dota_2_model` (sibling of `betting_workspace/`)
- Read `../dota_2_model/AGENTS.md` before any work.
- Compose file: `trader-live` (`--mode live`) and `trader-paper` (`--mode paper`).
  After rollout the containers are `dota_2_model-trader-live-1` /
  `dota_2_model-trader-paper-1`. Until US-015 the running name is still
  `dota_2_model-live-paper-1` on `./data/live_paper`.
- `src`, `config`, `data/new_model`, and `data/lol/models` are bind-mounted.
  Python/TOML/model edits skip `--build`. WalletHost still loads
  `config/trading.toml` and each assigned production model once at boot.
- This VPS checkout is bind-mounted into production. Do not
  `docker compose restart` / `up` / `--force-recreate` / `down` until the
  controlled US-015 recreate.

## Kalshi overlay

Kalshi sits next to Polymarket in the same WalletHost. Independent size, book,
fair, orders, sqlite. A Kalshi miss does not stop PM. Kalshi opens when this
process has assigned games. A LoL-only process can start Kalshi.
`KALSHI_TRADING=off` still opens no HTTP or WS.

| What | Where |
| --- | --- |
| Env | `.env` (gitignored): `KALSHI_TRADING`, `KALSHI_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, optional `KALSHI_SUBACCOUNT` (default 0). Smoke-only: `KALSHI_DEMO_KEY_ID`, `KALSHI_DEMO_PRIVATE_KEY_PATH`. Never `KALSHI_PRIVATE_KEY`. |
| PEM | Host file mounted read-only into the container at the same path as `KALSHI_PRIVATE_KEY_PATH`. Unencrypted PKCS8. Do not log contents. |
| TOML | `config/trading.toml` `[kalshi]`: `dota_base_size_usd` ($5), `lol_base_size_usd` ($10). Not derived from USDC clips. `0.0` forbids new entries for that game; exits still close actual size. Timeouts: `book_stale_s`, `private_ws_blind_s`, `reconcile_interval_s`, `fence_timeout_s`. Not copied into fork TOML. Clip is one entry on one ticker, not a day or portfolio cap. |
| Sqlite | In-container `data/live_paper/wallet/kalshi.db` + flock `kalshi.db.lock`. Host live bind is `data/live_paper_live/wallet/`; paper bind is `data/live_paper_paper/wallet/`. Until US-015 the running tree is `data/live_paper/wallet/`. Paper and live share the file inside one process (`source` column). |
| match.json | Writes schema 5 with `game`. Schema 4+ always-present `kalshi` object. Schema 3 reads as Kalshi off. Missing `game` on read → `dota`. Bind writers live in `kalshi_meta.py`. |
| session.jsonl | Schema 6, `venue` `polymarket` \| `kalshi` on trade rows. Schema 1–5 omit venue = PM. Kalshi writers live in `kalshi_journal.py` (fill cursor = last `position_ledger.seq`). |
| Ownership | `client_order_id` prefix `d2m-{sub}-` (hyphens; demo rejects dots). |
| Smoke | `uv run python scripts/kalshi_execution_smoke.py --env demo`. WalletHost never starts it. `--env production` is rejected. |

Module map (`src/live_paper/`):

| File | Role |
| --- | --- |
| `kalshi_leg.py` | `OutcomeSide`, `BookSide`, `KalshiLeg`, `series_ticker_from_kind(game, kind)` → Dota `KXDOTA2MAP`/`KXDOTA2GAME`, LoL `KXLOLMAP`/`KXLOLGAME` |
| `kalshi_types.py` | REST/WS errors and frozen values. Consumers import from here, no re-exports |
| `kalshi_ws.py` | Auth WS client and frame parsers |
| `kalshi_client.py` | REST only |
| `kalshi_meta.py` | `match.json` kalshi block |
| `kalshi_journal.py` | session JSONL Kalshi rows |
| `process_lock.py` | `acquire_file_lock(path)`. Wallet flocks the db file; Kalshi flocks `kalshi.db.lock` |
| `kalshi_runtime.py` | WS pumps, poll (survives exceptions), `KalshiHostWire` |
| `kalshi_observe.py` | Public resolve + prior. Retries after a candle GET that dies on null close |

`KALSHI_TRADING`: `off` / `observe` / `paper` / `live`. Default `off`. Independent
of `--mode`. First cards 2026-08-28: `paper`.

Boot log line (health check): `live-paper config loaded [engine] [risk] [profiles] [wallet] [kalshi] kalshi_trading=…`.

A 1-minute candle with `yes_bid.close: null` fails the whole candlestick GET
inside the SDK. Prior load retries. `_poll_loop` logs and continues; it is not
a one-shot task.

## What to restart

From `/root/work/dota_2_model`. Do not `compose down`. Check `summarize.py --live`
plus logs first: a restart detaches that market. Running matches die.

This production-mounted checkout: no restart until the controlled US-015
rollout. `restart: unless-stopped` after a crash is not that rollout.

| What changed | Service(s) | Restart vs recreate | Running matches |
| --- | --- | --- | --- |
| `src/` Python, `config/trading.toml`, Dota `data/new_model/production`, LoL `data/lol/models/production` | `trader-live` and/or `trader-paper` (the process that loaded it) | `docker compose restart` after `--live` is empty | die; archive resumes by append |
| `.env` modes / Kalshi / PK | `trader-live` `trader-paper` | `up -d --force-recreate trader-live trader-paper` | die |
| compose command/mounts/service split / first leave of `live-paper` | those trader services | recreate, not restart | die |
| deps / Dockerfile / poly-maker copy | both traders | `docker compose build` then `up -d` | die. No `.dockerignore`; `--build` can bake host `.env` into `/app/.env` |
| Docs only | nothing | nothing on the VPS | unchanged |

Paper→live on the same `kalshi.db` is safe: REST compare ignores `source=paper`
leftover. Do not invent a second db name.
