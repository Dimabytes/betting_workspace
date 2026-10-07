# Run: reverse-engineer trader 0x6e2c and design a two-sided + merge strategy for Dota

Run dir (`$R`): `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/reports/2026-10-07-two-sided-merge`

## Goal

A Polymarket wallet `0x6e2c0e9474af7d720e5aba2a5b0bb6f723b7787c` (profile name `0x99a093771ad58bcfc3023cd75566415f`) made ~$495k realized PnL since late July 2026 on CS2 / LoL / Valorant map- and series-winner markets. It almost never sells: it BUYs both outcomes and MERGEs pairs (1 YES + 1 NO -> $1). We run a one-sided directional maker on Dota 2 (and LoL) map-winner markets and earn ~3 cents per $1 bought on ~$22k/week of buys. We want to understand his mechanism precisely enough to (a) backtest a two-sided + merge strategy on our Dota L2 archive and (b) decide whether to build it live. The orchestrator writes the final plan; you deliver evidence.

## Known facts (verified unless marked)

Trader, last 30 days (2026-09-07 .. 2026-10-07, data-api activity, 375,961 rows):

- 367,893 BUY / 22 SELL trade records, 6,784 MERGE ($9.24M returned), 1,201 REDEEM ($0.61M). Buy cost $9.74M incl. fees.
- Games by cost: CS2 72% ($7.0M, 1,545 markets), LoL 14.6% ($1.42M, 500 markets), Valorant 13.5% ($1.32M, 65 markets). **No Dota.** 61% of cost in map markets, 39% in series (BO3) markets.
- Role by records: 78% maker / 22% taker; by $: 51% maker / 49% taker. Mean maker fill $17, taker fill $58. Median fill $7.24 (18 shares).
- Both outcomes bought in 2,066 of 2,110 markets. Median 110 fills per market; median market cost $1,167; median active span 40 min.
- Fill price buckets by cost: <0.20: 3%; 0.20-0.45: 21%; 0.45-0.85: 63%; 0.85-1: 13%.
- `pair_avg_cost` (avg YES price + avg NO price per market) < $1 in ~70% of markets, > $1 in ~30%.
- LoL timing (222 maps with GRID clocks, 61k fills): first fill median ~1:31 game time, last fill median ~28:30. 19% of fills in 0-8 min, 40% in 8-20, 41% after 20. Prematch negligible.
- Example: CS2 G2 vs PARIVISION BO3 2026-10-06: 1,150 buys (610 G2 @avg 0.406, 540 PRV @avg 0.578), cost $204k, 50 merges returning $207k, 0 sells, cash net +$2,899, peak unreturned capital ~ $16.4k.

Trader, all-time (data-api user pnl, 1h points): strategy starts late July 2026. Weekly since Aug: realized $13k..$92k, volume_usdc $0.8M..$4.4M, gross `trade_pnl` ~3.5-4.0 cents per $ volume, `fees_paid` ~1.5-1.9 cents per $ volume (so fees eat ~40% of gross), rebates on top (30-day: $14.9k maker + $37.0k taker rebate). All-time: realized $495.7k, trade_pnl $824k, fees -$356k, volume_usdc $28.1M, 790k trades. Feb-Apr 2026 was a different, small, fee-refund era.

Polymarket esports market mechanics (verified in our code/data):

- Binary market = 2 CTF tokens (YES/NO). One unified CLOB: a BUY of NO at p is matched against a BUY of YES at 1-p (the operator mints/merges). So "bid YES at b_y and bid NO at b_n" is a two-sided market on YES with spread 1 - b_y - b_n.
- Fees (`sports_fees_v3`): `feesEnabled: true`, `feeSchedule {rate: 0.05, rebateRate: 0.15, takerOnly: true}`. Our backtest models taker fee = 0.05 * qty * p * (1-p), maker rebate = 0.15 * that fee. Tick 0.01 (some 0.001), min order 5 shares, `negRisk` false for map markets.
- Dota map markets (our archive, model window): spread p50 3 cents, best-level depth ~$37-44, ~$375-392 within 3 cents, ~4.4 prints/min, ~$306/min WS taker notional (on-chain is 1.5-2.5x WS), ~30 makers / 38 takers per window. Series markets thinner (~0.5 prints/min).
- Merge = on-chain `mergePositions` (CTF or NegRisk adapter). poly-maker implements it (`Merger`, `_maybe_merge`, trigger `min(yes,no) >= merge_min_size`). No split, no redeem in poly-maker.

Our current live Dota strategy ("Follow300", esports-trader): LightGBM predicts radiant midpoint delta over 300 s; if |delta| >= 2 cents, game second < 480, price in [0.45, 0.85), spread <= 6 ticks -> join bid ladder on ONE token (3 rungs, 1 tick apart, size = clip/price, clip $5..$400 by tournament), exit by continuous maker SELL at max(ask, fair); kill gate blocks victim-token BUYs for up to 10 s after a scoreboard kill. One episode = one token; never both; never merge. Read `$R/work/orchestrator/explore/strategy.md` for details with file:line.

Our backtest (esports-trader `src/backtest`): Nautilus L2 replay of both tokens' full books + on-chain fills, queue-position maker fill model, insert latency 175 ms, cancel 60 ms, fees in postprocess. One strategy class per match (`DotaMakerStrategy`), `STRATEGY_PATH` hardcoded in `run.py`. No merge op, single-sided episode logic. Read `$R/work/orchestrator/explore/backtest.md`.

poly-maker (frozen fork, `../poly-maker`): a two-sided BUY-YES + BUY-NO maker around microprice with inventory skew and CTF merge. esports-trader reuses its body (gateway, books, user stream, risk, merge) and replaces its brain (`construct_quotes`, `reconcile`). Read `$R/work/orchestrator/explore/polymaker.md`.

Data inventory: read `$R/work/orchestrator/explore/data.md`.

## Data paths (all read-only)

Trader raw data (copied from the previous session): `$R/data/poly_target/`

| file | content |
|---|---|
| `activity_compact.json` | 375,961 activity rows: `timestamp, condition_id, type (TRADE/MERGE/REDEEM/MAKER_REBATE/TAKER_REBATE), size, usdc_size, transaction_hash, price, token_id, side, outcome_index, title, slug, event_slug, outcome, role (MAKER/TAKER, on TRADE)` |
| `activity30.json` | same, raw API payload (402 MB) |
| `taker30.json` | 81,396 rows from `data-api /v2/trades?taker_only=true` (his taker fills) |
| `markets_summary.json` | 2,110 per-market aggregates (see summary.json `top_markets` for the shape) |
| `summary.json`, `timing_summary.json`, `methodology.json`, `examples.json` | the previous session's results and method |
| `lol_clocks.json` | 312 LoL maps: `condition_id, match_id (grid-...), horn, anchors [(unix_ts, game_seconds, ticking, status)]` |
| `timed_trades.json` | 61,018 of his LoL fills with `second` (game clock) attached |
| `lol_chain.json` | 146,814 on-chain fills in his LoL markets: `maker, taker, mirrored, maker_side, taker_side, asset_id, sibling_asset_id, amount, price, tx_hash, block_timestamp_us, market_id` |
| `positions.json` | data-api positions (current) |
| `user-pnl.json` | data-api user pnl time series (all-time, 1h fidelity), `stats-now.json` current totals |

esports-trader repo: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` (read-only for you; on `main`; owner may edit during the run). Python: `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/.venv/bin/python` (pyarrow, pandas, duckdb, numpy, lightgbm available; no polars). Or `cd` there and `uv run python <script>` with `PYTHONPATH=src`.

- Dota L2 books: `data/raw/telonex/polymarket/book_snapshot_full/asset_id=<token_id>/<YYYY-MM-DD>.parquet` (full depth both sides, event-driven, `timestamp_us`, `bids`/`asks` list<struct<price,size>> as strings, best first). Same tree: `onchain_fills/` (maker, taker, maker_side, taker_side, amount, price, mirrored, sibling asset ids; through 2026-10-05), `trades/` (WS prints with aggressor `side`; only through 2026-09-17).
- LoL L2 books, same layout: `data/lol/raw/telonex/polymarket/{book_snapshot_full,onchain_fills,trades}/asset_id=<token_id>/<day>.parquet`. **488 of his 500 LoL markets have both-token books and on-chain fills locally; 286 of the 289 clock-matched ones.**
- Universe / metadata: Dota `data/new_processed/universe/universe.parquet` (conditionId, token ids, contract_kind map_winner/series_winner, teams, ...); match catalog `data/new_processed/match_catalog/`; LoL `data/lol/processed/universe/markets.parquet`. Gamma dumps with tick/fee fields: `data/raw/polymarket_dota/universe/events/tag_102366_closed/`.
- Our live Dota trading records: `data/trader/<session>/{match.json, session.jsonl (signal/quote/fill rows), core_trace.jsonl.gz, grid_state.jsonl.gz}`; index `data/archive_index/index.parquet`.
- Backtest outputs: `data/backtests/dota_maker/LIVE/...` (`summary.json`, `fills.parquet`, `results.parquet`).

poly-maker: `/Users/dimabytes/work/polymarket/dota_2_bot/poly-maker` (frozen, read-only; `src/polymaker/{engine.py,strategy/quoting.py,merge.py,...}`).
prediction-market-backtesting (Nautilus-based library): `/Users/dimabytes/work/polymarket/dota_2_bot/prediction-market-backtesting`.
Workspace learnings: `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.learnings/*.md`.

Public APIs (read-only GET is fine, be polite: <= 4 req/s, retry on 429): `https://data-api.polymarket.com/v2/activity?user=<wallet>&limit=500&offset=..`, `/v2/trades?user=..&taker_only=true`, `/positions?user=..`, `/v2/user-pnl?user=..`, `https://gamma-api.polymarket.com/markets?..`, `/events?..`, `https://clob.polymarket.com/prices-history?market=<token_id>&interval=..&fidelity=..`, `/book?token_id=..`.

## Hard rules

- You run with auto-approve; no permission prompt will stop you. Never delete, overwrite, or destroy anything you did not create in this run: no `rm`, no `mv` onto existing files, no `git clean`/`reset --hard`/`checkout --`/`stash`, no `kill`/`pkill` outside your own `work/<name>/` processes, no dropping/truncating files, dirs, rows, tables, branches. If something is in the way, write beside it or stop and report.
- Read-only on all code repos (esports-trader, poly-maker, polymarket-collector, prediction-market-backtesting). Write only to `$R/work/<name>/` and `$R/reports/<name>.md`. If a repo file looks half-written, the owner is editing it: `git show HEAD:<path>`.
- No SSH to the VPS, no production access, no accounts, no money, no signups, no trading. No pytest, no model training, no full backtests. Small replays are fine: <= 30 maps, <= 3 parallel processes, <= 6 GB RAM total for you (other agents share this 12-core / 34 GB Mac).
- Python from the esports-trader venv (above). Work files under ~200 MB each (aggregate / sample / parquet).
- Browser only via `agent-browser --session <your name>`; never `agent-browser close --all`.
- Every claim with `file:line` / URL + date / command + numbers. Mark each key finding `verified` (you ran it), `likely` (strong indirect evidence), or `speculative`.
- Report: `$R/reports/<name>.md`. Line 1: `# <title>`. Line 2: `Status: WIP` while working; make it exactly `Status: FINAL` only when complete. Update the file as you go (sections may be rough first). Scripts and intermediate parquet/json in `$R/work/<name>/`; mention the path of every script that produced a number.
- Report structure: `## Summary` (<= 12 bullets, the numbers that matter), `## Findings` (sections per question in the brief), `## Open questions / what I could not verify`, `## Files` (scripts, outputs).
- Write in English. Be concrete: tables with numbers beat prose.

## Agents in this run

| name | model | brief |
|---|---|---|
| `sol-lol-micro` | GPT-6.1-Sol | LoL fill-level reconstruction of his quoting against our L2 books (placement, markouts, inventory, merge, taker) |
| `sol-policy` | GPT-6.1-Sol | Cross-game policy reconstruction from activity: inventory skew, merge timing, taker triggers, capital, PnL decomposition, losing markets |
| `grok-bt` | Grok 4.7 | In-repo design: how to add `TwoSided` strategy + merge accounting to our Nautilus backtest; file-by-file plan |
| `grok-sim` | Grok 4.7 | Experiment zero: standalone two-sided quoter replay on Dota L2 tapes + Dota flow capacity numbers |
| `grok-live` | Grok 4.7 | Live path: poly-maker body + esports-trader seams for two-sided + merge, CTF merge on Gnosis Safe, Dota competition (who makes markets in Dota maps) |
| `swe-fees` | Devin SWE-2 Max | Polymarket fee/rebate/merge mechanics research + API verification of the trader's numbers |
| `swe-history` | Devin SWE-2 Max | The trader's full history, scaling vs edge, similar wallets, Dota vs CS2/LoL/Valorant volume on Polymarket |
| `swe-theory` | Devin SWE-2 Max | MM theory for binary markets with settlement + jump risk; concrete quoting rule using our 300 s model; parameter table |

Reports of other agents are in `$R/reports/`; you may read them, but do not edit them.
