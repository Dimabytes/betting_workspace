# Train + backtest AFTER steps 3A and 3B

You are a NEW session. Work in `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` on `main`.
Code for this feature is done: step 2 + 3A (`199705d4`, `21274237`) + 3B (`85eccfc8`, `25734ed0`).
Do not edit product code unless a command fails because of a missing path/flag in the new CLI.
Do not push. Do not promote LIVE. Do not start 3A/3B again.

## Output contract

APPEND progress to:
`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/progress.txt`

Write the full final report to:
`/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/docs/archive-linking-and-feed-replay/agent-replies/train-backtest.md`

Chat: ONLY that path.

The report MUST include, as plain text (no markdown tables, no JSON dump):
- commands and exit codes
- dataset/model output paths + hashes if printed
- each backtest run directory
- the two-column `format_terminal_report` / `scripts/report_seeds.py` output verbatim for Dota and LoL
- vs-LIVE comparison if `scripts/run_seeds.sh` printed it

## Do not touch

- `betting_workspace/current-task/`
- `../polymarket-collector`
- esports-trader onchain: `telonex_onchain.py`, `sync_onchain_fills.py`, `parity_onchain_rpc.py`, `sync_collector_parquet.py`
- `../poly-maker`
- Dirty files you did not write

## What the user already did

Collect for Dota and LoL already ran. Onchain fills are downloaded.

You must:
1. Dota `make market-data` then `make prepare` (needed for `game_features.parquet` from 3A)
2. Dota `make train` (research + production + no-XP)
3. LoL `make lol-prepare` then `make lol-train`
4. Backtests with cache warm — NOT `SEEDS=3` in one shot

## Commands

```bash
cd /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader

make market-data
make prepare
make train

make lol-prepare
make lol-train

# seed 0 x 4 shards first (warms Nautilus/Telonex cache), then seeds 1-2
SEEDS=1 SHARDS=4 scripts/run_seeds.sh dota archive-s2-20260921
SEEDS=1 SHARDS=4 scripts/run_seeds.sh lol archive-s2-20260921
SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh dota archive-s2-20260921
SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh lol archive-s2-20260921
```

Same `--name` so seeds land in one run root. Finish that game's seed 0 merge before seeds 1–2. Do not overlap Dota and LoL shard workers if the machine is already busy; sequential games is the default.

`--policy` / `--live-since` are gone. Do not pass them. If a seed dies, artifacts are left for `--resume`.

Do not `make test`. Do not restart live/paper traders. Append progress after each major stage.
