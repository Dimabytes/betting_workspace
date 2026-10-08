---
name: vps-trader
description: Inspect Dota 2 and LoL Polymarket trading on this VPS (live, paper, collectors, onchain fills). Use when the user asks how a match is going, today's PnL, trader logs, why there were no bets, rebate accrued since the last payout, why we are not selling, whether a restart is safe, whether the daemon is healthy, to restart a trader after a pull or size change, or to look for bugs/suspicious behavior in matches, feeds, books, halt, dust, or quoting. Do not restart unless asked.
---

# VPS trader

This machine runs the live and paper traders. Read this before grepping the repo to rediscover log layout.

Answer first with the match, PnL, and whether anything is actually wrong. Then the evidence. Do not restart Docker, git pull, or edit config unless the user asked.

Answer Dota vs LoL separately: process, state dir, model, gold-velocity, GRID-only LoL, GRID/Oddin Dota. Steam only links Dota markets in discovery; it is never a live feed.

Wallet B is a second live daemon, service `live_b`, for the two-sided strategy on BLAST Slam and PARI Universe. It is not paper and it is not wallet A. Use the Wallet B section for its PnL, restart gate, and deploy.

## Layout

| What | Where |
|---|---|
| Live trader | `/root/work/esports-trader` service `live` (`WalletHost`, `--mode live`). Container `esports-trader-live-1`, host `data/trader_live` |
| Wallet B | service `live_b`, container `esports-trader-live_b-1`, host `data/trader_live_b`, config `config_b`, chat `TG_CHAT_ID_B`. See Wallet B |
| Paper trader | same compose, service `paper` (`--mode paper`). Container `esports-trader-paper-1`, host `data/trader_paper` |
| Pre-rollout tape | host `./data/live_paper` is the leftover pre-rollout tape. Read-only history; `--live` omits it. Do not delete |
| Collectors | `/root/work/polymarket-collector` services `archive-dota`, `compact-dota`, `archive-lol`, `compact-lol`, `onchain` |
| Match archives (in-container) | `data/trader/<match_id>/` inside each trader |
| Host state | `data/trader_live/`, `data/trader_paper/`, plus the leftover pre-rollout `data/live_paper` |
| Wallet sqlite | in-container `data/trader/wallet/live.db` (real money). `paper.db` is paper mode, not the Polymarket live funder |
| Engine journal | `wallet/engine_journal/live.jsonl` (fork noise, not the trade tape) |
| Dota model | `data/new_model/production/` (bind-mounted read-only). `research/` is train/backtest |
| LoL model | `data/lol/models/production/` |
| Size / risk | `config/trading.toml` `[profiles.dota-map]` / `[profiles.lol-map]`, `[risk]` |
| Collector archives | `/var/lib/polymarket-dota-archive` → `/archive/dota`, `/var/lib/polymarket-lol-archive` → `/archive/lol` |
| On-chain state | `/var/lib/polymarket-onchain-state` (ledger, import, `.onchain.lock`) |

`src`, `config`, `data/new_model`, and `data/lol/models` are bind-mounted. A Python/TOML/model change needs `docker compose restart` of the process that loaded it, not `--build`. Rebuild only for Dockerfile, deps, or poly-maker. Do not `--build` this checkout: there is no `.dockerignore`, so host `.env` can bake into the image.

WalletHost reads `trading.toml` and production models once at boot. A new match does not re-read them.

Bind-mounted files are visible on disk, but the running WalletHost does not re-import them. Restart the process that loaded the change, and never `compose down`.

## First commands

Health:

```bash
docker compose -f /root/work/esports-trader/compose.yaml ps
docker compose -f /root/work/polymarket-collector/compose.yaml ps
docker compose -f /root/work/esports-trader/compose.yaml logs --since 30m live
docker compose -f /root/work/esports-trader/compose.yaml logs --since 30m paper
```

Match / day summary (run this, do not re-parse JSONL by hand):

```bash
python3 /root/work/esports-trader/src/dashboard/summarize.py
python3 /root/work/esports-trader/src/dashboard/summarize.py --today
python3 /root/work/esports-trader/src/dashboard/summarize.py --game dota
python3 /root/work/esports-trader/src/dashboard/summarize.py --game lol
python3 /root/work/esports-trader/src/dashboard/summarize.py --match 8959222564
python3 /root/work/esports-trader/src/dashboard/summarize.py --live
python3 /root/work/esports-trader/src/dashboard/summarize.py --rebate
python3 /root/work/esports-trader/src/dashboard/summarize.py --restart-check
```

`--today` is Europe/Berlin (the user's UTC+2 clock). Record timestamps in files are UTC.

`--rebate` answers "сколько ребейта накопилось" directly: it finds the newest paid `MAKER_REBATE` in Polymarket activity (payout lands ~00:45 UTC daily) and sums the maker-fill rebate estimate in `session.jsonl` since that timestamp — live, legacy, and still-open maps included, paper excluded. It is also printed automatically at the end of `--today` as `rebate_accrued`. Never ask the user "since when" — the window is the last payout.

`--live` is open sessions in `trader_live` / `trader_paper` only: no `session_end`, no `final` (GRID often stores `winner: null`), no `execution_cleanup.json`, and a file written in the last 15 minutes. A restart or a feed that ends without a final leaves a session with no `session_end` forever. `--today` labels such a dead session `ORPHAN`, not `LIVE`. The pre-rollout `data/live_paper` tree is omitted (crash leftovers look LIVE there forever). The current map is still the newest `joined_at_utc` without a `final`, or the match id in the latest `session started` docker line without a matching `session finished`. On `--today`, a row with `final` is not labeled LIVE. Do not delete those leftover dirs; they are the tape.

Read-only Streamlit dashboard: `make dashboard` in `/root/work/esports-trader`, then `ssh -L 8501:localhost:8501 sun` and open localhost:8501.

## Which file answers what

| Question | Source |
|---|---|
| Who played, map, market, model, winner, game | `<match>/match.json` (`game`; missing → `dota`) |
| Did we bet, fills, quotes, why no entry | `<match>/session.jsonl` kinds `fill`, `quote`, `signal` |
| Match PnL the user saw in Telegram | docker logs / `session finished` line. Formula: `net = realized + imv + rebate` |
| Engine cash at match final | `session.jsonl` last `session_end`, or `match.json` `final.pnl` |
| Still quoting / leftover shares (live) | last `fill.position_after` and last `quote` in `session.jsonl` |
| Why no SELL while long, stuck order, exit slot | `core_state.py <match_id>` — the `VERDICT:` line. `session.jsonl` cannot answer this |
| Exit state per feed tick | `session.jsonl` `signal.exit_state` + `pos_yes` / `pos_no` |
| Orders proven gone | `<match>/execution_cleanup.json` exists |
| Old Steam snapshots | `<match>/state.jsonl`, only maps traded on Steam before 2026-10-05. Do not dump it |
| GRID / Oddin snapshots | `<match>/grid_state.jsonl` / `<match>/oddin_state.jsonl` |
| Wallet-wide cash hole | `live.db` `fill_ledger`. Not per-match PnL. Open inventory looks like a cash loss |
| Halt / 429 / Telegram | `docker compose logs --since 30m live` / `paper` |
| Settled day on Polymarket | `summarize.py --today` line `polymarket_today` (BUY/SELL/REDEEM/rebate + open marks) |
| Rebate accrued since last payout | `summarize.py --rebate` (or `rebate_accrued` in `--today`) |

## PnL rules (strong)

1. For "сколько сегодня на Polymarket" read `polymarket_today pnl` from `summarize.py --today`. That is Berlin-day cash (`-buy + sell + redeem + merge + rebate`) plus open position marks. `MERGE` activity is cash in (collateral from a YES+NO merge), same sign as redeem. Say that number. Do not invent a second day total from telegram, sqlite, or leftover BUYs. Wallet B is the same line with `--root /root/work/esports-trader/data/trader_live_b` so the funder comes from B's `live.db`.
2. Telegram `net` (`realized + imv + rebate`) is one map **when `session_end` exists**. `sum_net` on `--today` is only those maps. Maps without `session_end` can still settle on Polymarket. Never call telegram `sum_net` the day.
3. `match.json` `final.pnl` is often `0.0` after flatten. Ignore it for "сколько заработали".
4. Sqlite `SUM(cash_delta)` and `wallet_day` equity are inventory accounting, not exchange-settled day PnL. A leftover position looks like a cash loss in sqlite even after Polymarket `REDEEM`. Positions API / activity is source of truth for leftover size.
5. Accrued rebate in `session.jsonl` is an estimate. The day number uses paid `MAKER_REBATE` from activity.
6. Leftover BUY is not a loss until you check **which token** (`yes` vs `no` on `--match` FILL lines), **who won**, and whether activity has a `REDEEM`. Buying NO while YES mid crashes is the other side; a winning leftover pays via redeem, not via SELL.
7. On-chain USDC.e on the Safe can be 0. Cash sits in Polymarket CLOB. For exact USDC, tell the user to read the Polymarket UI. Do not quote sqlite or chain as the tradable balance.

## Live match checklist

1. Run `summarize.py --live`.
2. Confirm the container is up and logs are still appending.
3. Last `signal.reason`: `model` means the window is open. `pre_horn` / `paused` / `missing_book` / `own_liquidity_only` / `outside_window` are usually not daemon crashes.
4. Last `quote.decision`: `normal` vs `reduce_only`.
5. Long with no resting SELL: run `core_state.py <match_id>`. Do not wait it out and do not sell by hand before the verdict line says WEDGED.
6. Fills: BUY then SELL is the s2-join clip. `entry_block=position_open` after a fill means the next clip waits until flat. That is strategy, not a hang.
7. `missing_book` / `one_sided_book` with a visible Polymarket book in the UI can still be our MDS. `own_liquidity_only` is different: the raw book is there, and our orders are the whole bid or ask. Check recent logs for WS/halt before calling `missing_book` an outage.
8. Steam is discovery only: one `GetLiveLeagueGames` per cycle (`STEAM_KEYS`) links Dota markets and fills `steam_match_id`. It never feeds a live map. LoL has no Steam.

## Common false alarms

- **No bets this map.** Histogram `signal.reason` and `entry_block`. `min_delta`, `nw_velocity` (cap 350, Dota and LoL), `cutoff` (after t=480), `no_edge`, `missing_book`, `own_liquidity_only` are skips, not misses of discovery. `own_liquidity_only` means our orders are the whole bid or ask. Discovery miss is: no `session_start` for that match at all.
- **Bought but not selling / quoting stopped with inventory.** `entry_block` is buy-side only — it never explains a missing SELL. Do not histogram anything; replay the core:

  ```bash
  cd /root/work/esports-trader
  PYTHONPATH=src uv run python /root/work/betting_workspace/.shared-skills/vps-trader/scripts/core_state.py <match_id>
  ```

  It leads with `VERDICT: OK` or `VERDICT: WEDGED`, and exits 1 when wedged. `--all --quiet` sweeps the recent live matches. A SELL outside `status=live` for more than 30s holds the exit slot for the rest of the map through `sell_occupied`. Faster first pass over every match at once: the wedge sweep in `log-map.md`, plus `docker compose logs live | grep -E 'orphaned|unproven|unmapped'`.
- **Stopped quoting after one clip.** Round-trip then dust below `min_order_size` (usually 5 shares) is forgotten on purpose. Check leftover sizes on `session finished`.
- **Telegram start but no finish.** Finish fires after the feed's finished phase and cleanup. A no-snapshot feed death goes through backoff, not `session finished`. GRID finish is pinned-map `status == finished`.
- **Halt leftover.** Wallet-wide. New matches will not size in until restart or the halt clears. Do not restart just to "see if it helps" unless asked.
- **Log spam in `live.jsonl`.** Fork engine journal. Health comes from docker logs + `session.jsonl`.
- **Paper fills on LoL.** PaperGateway writes `fill` rows. They are not CLOB. Absence of PK on `paper` is the live-order gate.
- **Cancelled BUY still occupying a rung, or cash short after a cancel.** `gone` holds the rung until `BuySettled`. The money is the `unsettled_buys` row, not the core reserve. MATCHED lowers that reserve. Only BUY `CONFIRMED` resolves a positive qty. The worker ending, or the map finishing, does not release the row. Alert key `unsettled_buy:` plus the first 8 characters of `venue_id`. Triage that exact venue, token, and session: Polymarket UI or REST maker trades, then `fill_ledger` status. `get_order` returning None, a cancel timeout, and venue `not found` are not proof the size was zero. `not found` means the order is off the book.

## Restart (only when asked)

Run `summarize.py --restart-check` first. `restart_check SAFE` → restart is allowed. `restart_check UNSAFE ...` → quote the line and stop: a live map holding ≥5 shares (`position`) or still inside the buy window at `second < 480` (`in_window`) blocks a restart. `--live` alone is not the gate — a live map past 480 with a flat book is SAFE. Never `compose down`.

Bind-mounted code/model: `restart` the process that loaded it. An `.env` change (modes, keys) needs `up -d --force-recreate live paper`, because `restart` does not re-interpolate the environment.

From `/root/work/esports-trader`:

```bash
docker compose restart live
docker compose logs --since 2m live
# or trader-paper
```

Confirm production model names from `data/new_model/production/model.json` and `data/lol/models/production/model.json`, and clips from `config/trading.toml`. Size/risk/Python/model changes need this restart. Do not rebuild unless deps/Dockerfile/poly-maker changed.

## Unsettled BUY reserve rollback

Steps 001–004 deploy together, and only after STEP-004 plus a separate user command.

Before a separately authorized rollback, inspect the live wallet (`data/trader/wallet/live.db` inside the live container):

```sql
SELECT COUNT(*) FROM unsettled_buys WHERE resolved=0;
```

If the count is not zero, wait until reconcile resolves those rows, or explicitly accept the previous cancellation behavior for those orders. Old code ignores these money rows. An `unknown` order with cancel reason `unsettled` is still waiting for cancel; it is not a fresh core.

Version-3 checkpoints stay loadable by old code. Old code sees that order as `unknown` with cancel reason `unsettled` and keeps inventory, the episode, and `sell_only`.

Old replay tooling cannot decode `CancelUnsettled` or `BuySettled` trace events. That limit is the replay tool. Checkpoint load is separate and still works.

Do not resolve a row because a cancel timed out. After the on-chain activity for that order is CONFIRMED, and the credited size is 0 (no fill, or the ledger row is `SUPERSEDED`, which credits 0):

```sql
UPDATE unsettled_buys SET resolved=1, qty=0 WHERE venue_id='<venue>';
```

Steps 001–004 go out together, and only on a separate user command. This note does not record a deploy, a measured post-deploy latency, or part-A readiness. After that deploy, check `CancelUnsettled` / `gone` and `BuySettled` in the trace, that open-row counts and reserve fall after reconcile, and the `wait_ms` distribution plus the same-rung cancel-to-next-place delay. Report restored clocks separately: `wait_ms=unavailable origin=restored`. The 2026-09-29 journal comparison, not a live measurement from this change, saw user-WS cancellation 41–46 ms before the HTTP ack, about one wake when `size_matched` was 0. Without that WS proof the row waits at least 60 seconds plus a reconcile. A canceled order still sitting in the market WS book is the grid-3008273 ghost; this does not claim a market-feed repair.

## Size change

Clips live in `config/trading.toml` `[profiles.dota-map]` and `[profiles.lol-map]`, not `BASE_SIZE_USDC` in Python. Restart the process that loaded that profile. When asked to scale "лимиты тоже", scale that profile (`q_max_usdc`, `merge_min_size`) and remember `[risk]` USDC caps are derived from the **sum** of loaded clips. `merge_min_size` is the fork's inventory merge threshold, not the clip size.

## Wallet B (live_b)

Second live daemon beside Follow300 on wallet A. Same image, `daemon --mode live`, its own state and config. `stop_grace_period` is 240s (one merge can take up to 190s, then the fence and the drain), not 200s. A stop with nothing in flight is about 25–30s. Docker waits until the process exits.

| What | Where |
|---|---|
| Service / container | `live_b`, `esports-trader-live_b-1` |
| Host state | `/root/work/esports-trader/data/trader_live_b` (in-container `data/trader`) |
| Config | `config_b` mounted at `/app/config` |
| Telegram | `TG_CHAT_ID_B` mapped to `TG_CHAT_ID`. A blank chat does not stop the process |
| Strategy | `DOTA_STRATEGY=two_sided`, Dota only, `signature_type` 3 |

`scripts/core_state.py` does not apply to B. B opens the core with no trace, so the script has nothing to replay. Do not run it for a B match.

Logs and the day:

```bash
docker compose -f /root/work/esports-trader/compose.yaml logs --since 30m live_b
python3 /root/work/esports-trader/src/dashboard/summarize.py \
  --root /root/work/esports-trader/data/trader_live_b --today
python3 /root/work/esports-trader/src/dashboard/summarize.py \
  --root /root/work/esports-trader/data/trader_live_b --two-sided --restart-check
python3 /root/work/esports-trader/src/dashboard/summarize.py \
  --root /root/work/esports-trader/data/trader_live_b --wallet
```

`--root` swaps the live tree and `DIR/wallet/live.db` only. Rows tagged `[paper]` or `[legacy]` are still A's trees. `polymarket_today` uses the funder in B's `live.db`, and its cash includes Polymarket `MERGE` activity. `--wallet` sums sqlite `MATCHED+CONFIRMED+MERGED`; that is inventory, not the day. `--two-sided --restart-check` prints `restart_check UNSAFE open_map <id>` for every open session. A flat map past second 480 is still unsafe. `restart_check SAFE` is the only restart gate.

B has no per-map dollar cap. A YES+NO pair settles to $1, so only the unpaired tail is at risk. B's brakes are `NET_MAX_SHARES` 30, pair merges when held value is at least $130 and at least 5 pairs, and the wallet's cash.

After a B restart, a late fill of a pre-restart order pulls both bids for the rest of that map. That is a fail-safe (`ownership_unresolved`). The start `cancel_all` makes those orders terminal, so B stops quoting that map and keeps the inventory. Do not restart B to clear it. The final merge still runs at map end.

Never print secrets. `docker compose config` interpolates `.env` and writes private keys into the output. Use `docker compose config --services` or `docker compose config --no-interpolate`. Do not `cat` `.env`.

### Env check before start

Prints `set` or `empty` and nothing else. Every line must say `set`. `TG_CHAT_ID_B empty` does not crash B: the log is `telegram message skipped: TG_BOT_API_TOKEN or TG_CHAT_ID not set`, and B trades with no Telegram. Do not start until that line says `set`. A blank `PK_WALLET_B` or builder key does fail closed at start.

```bash
python3 - <<'PY'
from pathlib import Path
keys = (
    "PK_WALLET_B",
    "BROWSER_ADDRESS_B",
    "POLY_BUILDER_KEY_B",
    "POLY_BUILDER_SECRET_B",
    "POLY_BUILDER_PASSPHRASE_B",
    "TG_CHAT_ID_B",
)
found: dict[str, str] = {}
for line in Path("/root/work/esports-trader/.env").read_text(encoding="utf-8").splitlines():
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        continue
    name, _, value = text.partition("=")
    found[name.strip()] = value.strip().strip('"').strip("'")
for key in keys:
    print(f"{key}={'set' if found.get(key) else 'empty'}")
PY
```

### Deploy

From `/root/work/esports-trader`, after the owner has pushed. A stays up the whole time. Never `compose down`. Never `docker compose build` (no `.dockerignore`, so a host `.env` can bake into the image). The first `up` builds the `live_b` image when it is missing.

```bash
cd /root/work/esports-trader
git pull
if [ -d data/trader_live_b ] && [ -n "$(find data/trader_live_b -mindepth 1 -print -quit)" ]; then
  echo "trader_live_b is not empty; stop"
  exit 1
fi
mkdir -p data/trader_live_b
docker compose config --services
# live, live_b, paper, compress
```

Adapter read, no `--send`, no `--approve`, no `--buy-pairs`. `--slug` is a current binary CTF v1 market (not neg-risk). Expect `adapter approved: True` and pUSD about 190. The probe prints the funder address; that is not a key. If approved is not True, stop. Do not send an approval.

```bash
python3 - <<'PY'
import os
import subprocess
from pathlib import Path
mapping = {
    "PK_WALLET_B": "PK",
    "BROWSER_ADDRESS_B": "BROWSER_ADDRESS",
    "POLY_BUILDER_KEY_B": "POLY_BUILDER_KEY",
    "POLY_BUILDER_SECRET_B": "POLY_BUILDER_SECRET",
    "POLY_BUILDER_PASSPHRASE_B": "POLY_BUILDER_PASSPHRASE",
}
env = os.environ.copy()
for line in Path(".env").read_text(encoding="utf-8").splitlines():
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        continue
    name, _, value = text.partition("=")
    env[name.strip()] = value.strip().strip('"').strip("'")
missing = [src for src in mapping if not env.get(src)]
if missing:
    raise SystemExit("empty " + " ".join(missing))
for src, dst in mapping.items():
    env[dst] = env[src]
env["PYTHONPATH"] = "src"
slug = "PUT_A_BINARY_CTF_V1_SLUG_HERE"
raise SystemExit(subprocess.call(
    ["uv", "run", "python", "scripts/merge_probe.py",
     "--config-dir", "config_b", "--signature-type", "3", "--slug", slug],
    env=env,
))
PY
live_id=$(docker compose ps -q live)
docker compose up -d --no-deps live_b compress
test "$(docker compose ps -q live)" = "$live_id"
docker compose ps
```

### After start

Logs, in order: `trader assigned: mode=live games=dota`, then `trader wallet: strategy=two_sided signature_type=3 funder=<BROWSER_ADDRESS_B>` (`trader wallet: strategy=%s signature_type=%d funder=%s`, funder is `browser_address`). The whitelist is every name in `[clips.dota].tiers` of `config_b` (now `BLAST Slam`, `PARI Universe`); a new league is one config line. An empty `[clips.dota].tiers` name list refuses start before `engine.start`: `DOTA_STRATEGY=two_sided needs names in [clips.dota].tiers: they are the title whitelist`. The container then restart-loops. A title outside those leagues logs `discovery skip reason=title_whitelist cid=<condition_id> title=<title>` once at info; later passes for the same cid are debug.

Collateral: within 20s, `trader collateral cache empty: BUY blocked until first REST read` must not be the lasting line. There is no log that prints the balance. That warning means the cache is still 0 and BUY is blocked. Reconcile is 20s, so the first REST read lands around then.

First map: two post-only BUY orders of 20 shares, prices sum to at most 0.99, no SELL. Bids come off on pause and at map end. The first fill is in `session.jsonl` and in `fill_ledger`. At held pairs worth at least $130 the log is `merge match=<id> pairs=… tx=…` and Telegram is `trader merge: match <id> pairs … tx …`. The final merge runs before the Telegram map total, `trader session finished`.

### Rollback and restart

```bash
docker compose stop live_b
docker compose ps
```

A is still up. In the Polymarket UI for wallet B, confirm there is no open order. Do not place or cancel orders from here. Redeem an unpaired tail by hand in the UI after each map.

Restart B only when the two-sided restart check says `restart_check SAFE`. Then `docker compose restart live_b` for a bind-mounted code change, or `docker compose up -d --no-deps --force-recreate live_b` when `.env` changed (`restart` does not re-read the environment). Do not recreate `live`.

## Tests on this VPS

Do not run `make test` / pytest while a map is live. Use `PYTEST_XDIST_AUTO_NUM_WORKERS=1`. `/tmp` is tmpfs (RAM); pytest tmp goes to `/var/tmp/pytest-esports-trader`. If a test is killed, `rm -rf /var/tmp/pytest-esports-trader /tmp/pytest-of-root`.

## Collector

Separate compose. Five services: `archive-dota`, `compact-dota`, `archive-lol`, `compact-lol`, `onchain`. `POLYMARKET_TAG_ID` is required on collect/compact (compose pins `"102366"` / `"65"`). `onchain` has no tag — it writes `parquet/onchain_fills` and `manifests/onchain/` into both archives. Discovery for quoting still reads that game's `<archive>/metadata/markets/*.json`. If a trader is up but never starts sessions, check the archive for **that game** is running and sidecar mtimes are fresh (last 2h). Compact is offline parquet; it does not affect quoting. Do not `compose down`. Image refresh is `docker compose build archive-dota` (no service named `archive`).

## LoL deploy / verify / rollback (US-015 is done; kept for the rollback path)

```bash
python3 /root/work/esports-trader/src/dashboard/summarize.py --live
# If a Dota map is live, wait. Recreate detaches it.

# In /root/work/esports-trader/.env (delete LIVE_TRADING entirely):
# DOTA_TRADING_MODE=live
# LOL_TRADING_MODE=paper
# Compose already sets DOTA_ARCHIVE_ROOT to /archive/dota and LOL_ARCHIVE_ROOT to /archive/lol.

cd /root/work/esports-trader
docker compose up -d --force-recreate live paper
# Do not: docker compose restart — it does not re-interpolate the environment.
```

Verify assignment and no live LoL CLOB:

```bash
docker compose -f /root/work/esports-trader/compose.yaml ps
docker compose -f /root/work/esports-trader/compose.yaml logs --since 5m live
docker compose -f /root/work/esports-trader/compose.yaml logs --since 5m paper
# live:  trader assigned: mode=live games=dota,lol
# paper: trader idle: mode=paper assigned=()
# neither: LIVE_TRADING is removed

docker compose -f /root/work/esports-trader/compose.yaml exec paper env | grep -E '^(PK|BROWSER_ADDRESS|LIVE_TRADING)=' || true
# empty: paper has no wallet secrets

# Paper session tape (host path after recreate):
# data/trader_paper/<match>/session.jsonl  session_start.execution_mode == paper
# PaperGateway fills are simulated; they are not CLOB. Absence of PK is the live-order gate.
```

Rollback LoL (collectors untouched):

```bash
# .env: LOL_TRADING_MODE=paper   # stay paper
#    or LOL_TRADING_MODE=off     # trader-paper idles (assigned=())
cd /root/work/esports-trader
docker compose up -d --force-recreate live paper
```

Both games live: `LOL_TRADING_MODE=live` with Dota live. `live`
gets `games=dota,lol`; `paper` idles (`assigned=()`).

## Chain balance logs

A chain read is used only when its block is at least as new as the last CONFIRMED fill for that token (or process start, whichever is later) and the block is not more than 30 seconds old. `POLYGON_RPC` on `live` is the trader's own Alchemy URL. Public nodes are the fallback. Check that the variable is set; do not print its value.

| Log / Telegram | Meaning |
|---|---|
| `position_divergence stale block` | Chain answer ignored. sqlite is unchanged |
| `chain balance stale block` | Every RPC that answered had a head that was too old. sqlite is unchanged |
| `chain balance encode failed` | The token id or funder could not be encoded. No RPC was called |
| `trader position restored from ledger` | A fresh chain balance matched the fill ledger, and sqlite was put back |
| `trader rest size-down ignored` | REST tried to shrink sqlite by more than half a share tick. Only a fresh chain read may shrink it |
| `SELL blocked` | The core's SELL was dropped for 30 seconds. A size drop names the wanted size and sqlite. A frozen SELL says `reason=frozen` |

## Details

Record kinds, signal reasons, and entry blocks: [log-map.md](log-map.md)
