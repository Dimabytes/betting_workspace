---
name: vps-live-paper
description: Inspect live Dota 2 Polymarket trading on this VPS. Use when the user asks how a match is going, today's PnL, live-paper logs, why there were no bets, whether the daemon is healthy, to restart the live-paper container after a pull or size change, or to look for bugs/suspicious behavior in matches, Steam, books, halt, dust, or quoting.
---

# VPS live paper

This machine runs the live trader. Read this before grepping the repo to rediscover log layout.

Answer first with the match, PnL, and whether anything is actually wrong. Then the evidence. Do not restart Docker, git pull, or edit config unless the user asked.

## Layout

| What | Where |
|---|---|
| Live daemon | `/root/work/dota_2_model` service `live-paper` (`WalletHost`) |
| Collector + compact | `/root/work/polymarket-collector` services `archive`, `compact` |
| Match archives | `/root/work/dota_2_model/data/live_paper/<match_id>/` |
| Wallet sqlite | `data/live_paper/wallet/live.db` (real money). `paper.db` is old paper mode |
| Engine journal | `data/live_paper/wallet/engine_journal/live.jsonl` (fork noise, not the trade tape) |
| Live model | `data/new_model/production/` (bind-mounted read-only). `research/` is train/backtest |
| Size / risk | `config/dota-map.toml` `[profiles.dota-map]` and `[risk]` |
| Collector archive | `/var/lib/polymarket-dota-archive` (live-paper mounts it read-only as `/archive`) |

`src`, `config`, and `data/new_model` are bind-mounted. A Python/TOML/model change needs `docker compose restart`, not `--build`. Rebuild only for Dockerfile, deps, or poly-maker.

WalletHost reads `dota-map.toml` once at boot. A new match does not re-read it.

## First commands

Health:

```bash
docker compose -f /root/work/dota_2_model/compose.yaml ps
docker compose -f /root/work/polymarket-collector/compose.yaml ps
docker compose -f /root/work/dota_2_model/compose.yaml logs --since 30m live-paper
```

Match / day summary (run this, do not re-parse JSONL by hand):

```bash
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --today
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --match 8959222564
python3 /root/work/betting_workspace/.shared-skills/vps-live-paper/scripts/summarize.py --live
```

`--today` is Europe/Berlin (the user's UTC+2 clock). Record timestamps in files are UTC.

`--live` means "no `session_end` yet". That includes the current map and also abandoned archives that never finalized. The current map is the newest `joined_at_utc`, or the match id in the latest `session started` docker line without a matching `session finished`.

## Which file answers what

| Question | Source |
|---|---|
| Who played, map, market, model, winner | `<match>/match.json` |
| Did we bet, fills, quotes, why no entry | `<match>/session.jsonl` kinds `fill`, `quote`, `signal` |
| Match PnL the user saw in Telegram | docker logs / `session finished` line. Formula: `net = realized + imv + rebate` |
| Engine cash at Steam final | `session.jsonl` last `session_end`, or `match.json` `final.pnl` |
| Still quoting / leftover shares (live) | last `fill.position_after` and last `quote` in `session.jsonl` |
| Orders proven gone | `<match>/execution_cleanup.json` exists |
| Steam snapshots | `<match>/state.jsonl`. Do not dump it. Sample tail only if feed/pause/game_state is the bug |
| Wallet-wide cash hole | `live.db` `fill_ledger`. Not per-match PnL. Open inventory looks like a cash loss |
| Halt / 429 / Steam 400 / Telegram | `docker compose logs live-paper` |

## PnL rules (strong)

1. Report Telegram `net` as the match result. That is `realized + imv + rebate`.
2. `match.json` `final.pnl` is often `0.0` after flatten. Ignore it for "сколько заработали".
3. Sqlite `SUM(cash_delta)` is the wallet inventory ledger, not exchange-settled day PnL. A still-open or leftover position shows as negative cash. Do not tell the user they are down that amount.
4. Rebate is not cash in the next fill. It is the sports maker rebate `0.15 * 0.05 * size * p * (1-p)` on maker fills, paid later by Polymarket.
5. Sum "today" from finished `session finished` / `session_end` rows for matches whose `joined_at_utc` falls on the Berlin calendar day. Do not mix in sqlite day equity.

## Live match checklist

1. Run `summarize.py --live`.
2. Confirm the container is up and logs are still appending.
3. Last `signal.reason`: `model` means the window is open. `pre_horn` / `paused` / `missing_book` / `outside_window` are usually not daemon crashes.
4. Last `quote.decision`: `normal` vs `reduce_only`.
5. Fills: BUY then SELL is the s2-join clip. `entry_block=position_open` after a fill means the next clip waits until flat. That is strategy, not a hang.
6. `missing_book` / `one_sided_book` with a visible Polymarket book in the UI can still be our MDS. Check recent logs for WS/halt before calling it an outage.
7. Steam HTTP 400 on `GetRealtimeStats` is Valve-side for that `server_steam_id`, not a bad key. Neighboring games in the same second can return 200.

## Common false alarms

- **No bets this map.** Histogram `signal.reason` and `entry_block`. `min_delta`, `nw_velocity`, `cutoff` (after t=540), `no_edge`, `missing_book` are skips, not misses of discovery. Discovery miss is: no `session_start` for that match at all.
- **Stopped quoting after one clip.** Round-trip then dust below `min_order_size` (usually 5 shares) is forgotten on purpose. Check leftover sizes on `session finished`.
- **Telegram start but no finish.** Finish fires after Steam `game_state==6` and cleanup. A 400-zombie / no-snapshot death goes through backoff, not `session finished`.
- **Halt leftover.** Wallet-wide. New matches will not size in until restart or the halt clears. Do not restart just to "see if it helps" unless asked.
- **Log spam in `live.jsonl`.** Fork engine journal. Health comes from docker logs + `session.jsonl`.

## Restart (only when asked)

From `/root/work/dota_2_model`:

```bash
docker compose restart live-paper
docker compose logs --since 2m live-paper
```

Confirm production model name from `data/new_model/production/model.json` and `base_size_usdc` from `config/dota-map.toml` after restart. Size/risk/Python/model changes need this restart. Do not `compose down`. Do not rebuild unless deps/Dockerfile/poly-maker changed.

If a match is live, say so before restarting: restart detaches that market.

## Size change

Live size is `config/dota-map.toml` `base_size_usdc`, not `BASE_SIZE_USDC` in Python. When asked to scale "лимиты тоже", scale the profile (`q_max_usdc`, `merge_min_size`) and the `[risk]` USDC caps in the same ratio, then restart. `merge_min_size` is the fork's inventory merge threshold, not the clip size.

## Collector

Separate compose. Live-paper discovery reads `ARCHIVE_ROOT/metadata/markets/*.json`. If live-paper is up but never starts sessions, check collector `archive` is running and sidecar mtimes are fresh (last 2h). Compact is offline parquet; it does not affect live quoting.

## Details

Record kinds, signal reasons, and entry blocks: [log-map.md](log-map.md)
