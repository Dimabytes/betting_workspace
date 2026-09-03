# esports-trader

## Setup

- Project path: `../esports-trader` (sibling of `betting_workspace/`)
- Read `../esports-trader/AGENTS.md` before any work.
- Compose file: `live` (`--mode live`) and `paper` (`--mode paper`).
  The running containers are `esports-trader-live-1` on
  `./data/trader_live` and `esports-trader-paper-1` on
  `./data/trader_paper`. Host `./data/live_paper` holds only the pre-rollout tape.
- `src`, `config`, `data/new_model`, and `data/lol/models` are bind-mounted.
  Python/TOML/model edits skip `--build`. WalletHost still loads
  `config/trading.toml` and each assigned production model once at boot.
- This VPS checkout is bind-mounted into production. `docker compose restart`
  loads a code, config, or model change; never `compose down`, and never
  restart while a map is live.

## Backtest launch

Full commands live in `../esports-trader/AGENTS.md` (Backtest). `--name` is
required on `--validation`. Default arm is `s2-join`; B0 is opt-in.
Baseline is `data/backtests/<dota|lol>_maker/LIVE` (retarget with
`make promote-backtest RUN=...`). Do not set `BACKTEST_LOG_LEVEL` or
`PYTHONUNBUFFERED=1` on validation shards. Redirect each shard to
`data/backtests/<dota|lol>_maker/<name>/logs/shard_i.log`. Do not wrap
`make backtest` / `make lol-backtest` in `rtk`. `INFO` is only for a
live `--match-id` you are watching.

## What to restart

From `/root/work/esports-trader`. Do not `compose down`. Check `summarize.py --live`
plus logs first: a restart detaches that market. Running matches die.

This production-mounted checkout: restart only when asked, and only after
`--live` is empty.

| What changed | Service(s) | Restart vs recreate | Running matches |
| --- | --- | --- | --- |
| `src/` Python, `config/trading.toml`, Dota `data/new_model/production`, LoL `data/lol/models/production` | `live` and/or `paper` (the process that loaded it) | `docker compose restart` after `--live` is empty | die; archive resumes by append |
| `.env` modes / PK | `live` `paper` | `up -d --force-recreate live paper` | die |
| compose command/mounts/service split | those trader services | recreate, not restart | die |
| deps / Dockerfile / poly-maker copy | both traders | `docker compose build` then `up -d` | die. No `.dockerignore`; `--build` can bake host `.env` into `/app/.env` |
| Docs only | nothing | nothing on the VPS | unchanged |
