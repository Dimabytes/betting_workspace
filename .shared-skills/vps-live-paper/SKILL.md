---
name: vps-live-paper
description: Inspect Dota 2 and LoL Polymarket trading on this VPS (trader-live, trader-paper, four collectors). Use when the user asks how a match is going, today's PnL, live-paper logs, why there were no bets, whether the daemon is healthy, to restart a trader after a pull or size change, or to look for bugs/suspicious behavior in matches, Steam, books, halt, dust, or quoting. Do not restart unless asked.
---

# VPS live paper

This machine runs the live and paper traders. Read this before grepping the repo to rediscover log layout.

Answer first with the match, PnL, and whether anything is actually wrong. Then the evidence. Do not restart Docker, git pull, or edit config unless the user asked.

Answer Dota vs LoL separately: process, state dir, model, gold-velocity, GRID-only LoL, Steam Dota.

## Layout

| What | Where |
|---|---|
| Live trader | `/root/work/dota_2_model` service `trader-live` (`WalletHost`, `--mode live`). After rollout: `dota_2_model-trader-live-1`, host `data/live_paper_live` |
| Paper trader | same compose, service `trader-paper` (`--mode paper`). After rollout: `dota_2_model-trader-paper-1`, host `data/live_paper_paper` |
| Until US-015 | running container is still `dota_2_model-live-paper-1` on `./data/live_paper`. Do not recreate until the controlled rollout |
| Collectors | `/root/work/polymarket-collector` services `archive-dota`, `compact-dota`, `archive-lol`, `compact-lol` |
| Match archives (in-container) | `data/live_paper/<match_id>/` inside each trader |
| Host state | `data/live_paper_live/`, `data/live_paper_paper/`, leftover `data/live_paper` until US-015 |
| Wallet sqlite | in-container `data/live_paper/wallet/live.db` (real money). `paper.db` is paper mode, not the Polymarket live funder |
| Engine journal | `wallet/engine_journal/live.jsonl` (fork noise, not the trade tape) |
| Dota model | `data/new_model/production/` (bind-mounted read-only). `research/` is train/backtest |
| LoL model | `data/lol/models/production/` |
| Size / risk | `config/trading.toml` `[profiles.dota-map]` / `[profiles.lol-map]` and `[risk]` |
| Collector archives | `/var/lib/polymarket-dota-archive` → `/archive/dota`, `/var/lib/polymarket-lol-archive` → `/archive/lol` |

`src`, `config`, `data/new_model`, and `data/lol/models` are bind-mounted. A Python/TOML/model change needs `docker compose restart` of the process that loaded it, not `--build`. Rebuild only for Dockerfile, deps, or poly-maker. Do not `--build` this checkout before US-015 (no `.dockerignore`; host `.env` can bake into the image).

WalletHost reads `trading.toml` and production models once at boot. A new match does not re-read them.

Until US-015: do not `docker compose restart` / `up` / `--force-recreate` / `down` on `dota_2_model`. Bind-mounted files are visible on disk; the running WalletHost does not re-import them.

## First commands

Health:

```bash
docker compose -f /root/work/dota_2_model/compose.yaml ps
docker compose -f /root/work/polymarket-collector/compose.yaml ps
# until US-015 compose has no live-paper service; log the running container:
docker logs --since 30m dota_2_model-live-paper-1
# after US-015:
# docker compose -f /root/work/dota_2_model/compose.yaml logs --since 30m trader-live
# docker compose -f /root/work/dota_2_model/compose.yaml logs --since 30m trader-paper
```

Match / day summary (run this, do not re-parse JSONL by hand):

```bash
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --today
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --game dota
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --game lol
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --match 8959222564
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --live
```

`--today` is Europe/Berlin (the user's UTC+2 clock). Record timestamps in files are UTC.

`--live` means "no `session_end` yet". That includes the current map and also abandoned archives that never finalized even if `match.json` already has a winner. The current map is the newest `joined_at_utc` without a Steam `final`, or the match id in the latest `session started` docker line without a matching `session finished`. On `--today`, a row with a winner is not labeled LIVE.

## Which file answers what

| Question | Source |
|---|---|
| Who played, map, market, model, winner, game | `<match>/match.json` (`game`; missing → `dota`) |
| Did we bet, fills, quotes, why no entry | `<match>/session.jsonl` kinds `fill`, `quote`, `signal` |
| Match PnL the user saw in Telegram | docker logs / `session finished` line. Formula: `net = realized + imv + rebate` |
| Engine cash at Steam final | `session.jsonl` last `session_end`, or `match.json` `final.pnl` |
| Still quoting / leftover shares (live) | last `fill.position_after` and last `quote` in `session.jsonl` |
| Orders proven gone | `<match>/execution_cleanup.json` exists |
| Steam snapshots | `<match>/state.jsonl`. Do not dump it. Sample tail only if feed/pause/game_state is the bug |
| LoL GRID snapshots | `<match>/grid_state.jsonl` |
| Wallet-wide cash hole | `live.db` `fill_ledger`. Not per-match PnL. Open inventory looks like a cash loss |
| Halt / 429 / Steam 400 / Telegram | until US-015: `docker logs --since 30m dota_2_model-live-paper-1`. After rollout: `docker compose logs trader-live` / `trader-paper` |
| Settled day on Polymarket | `summarize.py --today` line `polymarket_today` (BUY/SELL/REDEEM/rebate + open marks) |

## PnL rules (strong)

1. For "сколько сегодня на Polymarket" read `polymarket_today pnl` from `summarize.py --today`. That is Berlin-day cash (`-buy + sell + redeem + rebate`) plus open position marks. Say that number. Do not invent a second day total from telegram, sqlite, or leftover BUYs.
2. Telegram `net` (`realized + imv + rebate`) is one map **when `session_end` exists**. `sum_net` on `--today` is only those maps. Maps without `session_end` can still settle on Polymarket. Never call telegram `sum_net` the day.
3. `match.json` `final.pnl` is often `0.0` after flatten. Ignore it for "сколько заработали".
4. Sqlite `SUM(cash_delta)` and `wallet_day` equity are inventory accounting, not exchange-settled day PnL. A leftover position looks like a cash loss in sqlite even after Polymarket `REDEEM`. Positions API / activity is source of truth for leftover size.
5. Accrued rebate in `session.jsonl` is an estimate. The day number uses paid `MAKER_REBATE` from activity.
6. Leftover BUY is not a loss until you check **which token** (`yes` vs `no` on `--match` FILL lines), **who won**, and whether activity has a `REDEEM`. Buying NO while YES mid crashes is the other side; a winning leftover pays via redeem, not via SELL.
7. On-chain USDC.e on the Safe can be 0. Cash sits in Polymarket CLOB. For exact USDC, tell the user to read the Polymarket UI. Do not quote sqlite or chain as the tradable balance.

## Live match checklist

1. Run `summarize.py --live`.
2. Confirm the container is up and logs are still appending.
3. Last `signal.reason`: `model` means the window is open. `pre_horn` / `paused` / `missing_book` / `outside_window` are usually not daemon crashes.
4. Last `quote.decision`: `normal` vs `reduce_only`.
5. Fills: BUY then SELL is the s2-join clip. `entry_block=position_open` after a fill means the next clip waits until flat. That is strategy, not a hang.
6. `missing_book` / `one_sided_book` with a visible Polymarket book in the UI can still be our MDS. Check recent logs for WS/halt before calling it an outage.
7. Steam HTTP 400 on `GetRealtimeStats` is Valve-side for that `server_steam_id`, not a bad key. Neighboring games in the same second can return 200. LoL has no Steam.

## Common false alarms

- **No bets this map.** Histogram `signal.reason` and `entry_block`. `min_delta`, `nw_velocity` (Dota only, cap 350), `cutoff` (after t=540), `no_edge`, `missing_book` are skips, not misses of discovery. Discovery miss is: no `session_start` for that match at all. LoL skips gold-velocity.
- **Stopped quoting after one clip.** Round-trip then dust below `min_order_size` (usually 5 shares) is forgotten on purpose. Check leftover sizes on `session finished`.
- **Telegram start but no finish.** Finish fires after Steam `game_state==6` and cleanup. A 400-zombie / no-snapshot death goes through backoff, not `session finished`. LoL GRID finish is pinned-map `status == finished`.
- **Halt leftover.** Wallet-wide. New matches will not size in until restart or the halt clears. Do not restart just to "see if it helps" unless asked.
- **Log spam in `live.jsonl`.** Fork engine journal. Health comes from docker logs + `session.jsonl`.
- **Paper fills on LoL.** PaperGateway writes `fill` rows. They are not CLOB. Absence of PK on `trader-paper` is the live-order gate.

## Restart (only when asked)

Check `--live` (and logs) first. If a match is live, say so before restarting: restart detaches that market. Never `compose down`. Never restart this production-mounted checkout during the development window before US-015.

Bind-mounted code/model: `restart` the process that loaded it. Env / service migration (`.env` modes, first leave of `live-paper`): `up -d --force-recreate trader-live trader-paper`. Do not `up -d` without naming those two services while `live-paper` still runs.

From `/root/work/dota_2_model` after US-015:

```bash
docker compose restart trader-live
docker compose logs --since 2m trader-live
# or trader-paper
```

Confirm production model names from `data/new_model/production/model.json` and `data/lol/models/production/model.json`, and clips from `config/trading.toml`. Size/risk/Python/model changes need this restart. Do not rebuild unless deps/Dockerfile/poly-maker changed.

## Size change

Clips live in `config/trading.toml` `[profiles.dota-map]` and `[profiles.lol-map]`, not `BASE_SIZE_USDC` in Python. Restart the process that loaded that profile. When asked to scale "лимиты тоже", scale that profile (`q_max_usdc`, `merge_min_size`) and remember `[risk]` USDC caps are derived from the **sum** of loaded clips. `merge_min_size` is the fork's inventory merge threshold, not the clip size.

## Collector

Separate compose. Four services: `archive-dota`, `compact-dota`, `archive-lol`, `compact-lol`. `POLYMARKET_TAG_ID` is required (compose pins `"102366"` / `"65"`). Discovery reads that game's `<archive>/metadata/markets/*.json`. If a trader is up but never starts sessions, check the archive for **that game** is running and sidecar mtimes are fresh (last 2h). Compact is offline parquet; it does not affect quoting.

## Initial LoL paper deploy / verify / rollback (US-015; do not run until asked)

```bash
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --live
# If a Dota map is live, wait. Recreate detaches it.

# In /root/work/dota_2_model/.env (delete LIVE_TRADING entirely):
# DOTA_TRADING_MODE=live
# LOL_TRADING_MODE=paper
# Compose already sets DOTA_ARCHIVE_ROOT to /archive/dota and LOL_ARCHIVE_ROOT to /archive/lol.

cd /root/work/dota_2_model
docker compose up -d --force-recreate trader-live trader-paper
# Do not: docker compose restart
# The old live-paper container is an orphan after the YAML rename; US-015 removes it.
# Do not `up -d` without naming the two trader services while live-paper still runs.
```

Verify assignment and no live LoL CLOB:

```bash
docker compose -f /root/work/dota_2_model/compose.yaml ps
docker compose -f /root/work/dota_2_model/compose.yaml logs --since 5m trader-live
docker compose -f /root/work/dota_2_model/compose.yaml logs --since 5m trader-paper
# trader-live:  live-paper assigned: mode=live games=dota
# trader-paper: live-paper assigned: mode=paper games=lol
# neither: LIVE_TRADING is removed

docker compose -f /root/work/dota_2_model/compose.yaml exec trader-paper env | grep -E '^(PK|BROWSER_ADDRESS|LIVE_TRADING)=' || true
# empty: paper has no wallet secrets

# Paper session tape (host path after recreate):
# data/live_paper_paper/<match>/session.jsonl  session_start.execution_mode == paper
# PaperGateway fills are simulated; they are not CLOB. Absence of PK is the live-order gate.
```

Rollback LoL (collectors untouched):

```bash
# .env: LOL_TRADING_MODE=paper   # stay paper
#    or LOL_TRADING_MODE=off     # trader-paper idles (assigned=())
cd /root/work/dota_2_model
docker compose up -d --force-recreate trader-live trader-paper
```

`LOL_TRADING_MODE=live` is later, not now.

## Details

Record kinds, signal reasons, and entry blocks: [log-map.md](log-map.md)
