# Historical data inventory: Dota 2 Polymarket (read-only)

**Verdict for two-sided MM + merge backtesting:** Local data is strong enough for a realistic L2/queue backtest on **map-winner** (and most **series-winner**) markets: full depth on **both** outcome tokens, aggressor-aware fills (WS trades through mid-Sep 2026, then on-chain), fees/rebates documented, and game-state joins via STRATZ + live trader feeds. Gaps that matter: **WS `trades` stop 2026-09-17** (collector sync deliberately skips them), **collector metadata/journals are VPS-only** (not mounted here), and there is **no historical CTF-merge tape** (merge is deterministic once YES+NO are held).

Primary corpus lives under `esports-trader/data/`, not `data/archive/` (that folder is old research dumps).

---

## 1. Polymarket order book data

### Location and size

| Path | Role | Size |
|---|---|---|
| `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket/book_snapshot_full/` | **Authoritative local L2 archive** (paid Telonex + VPS collector rsync) | **~51 GB** |
| Same tree `trades/` | Public trade prints | ~658 MB |
| Same tree `onchain_fills/` | Chain fills | ~2.0 GB |
| Same tree `_control/` | Availability sidecars | ~343 MB |
| VPS `/var/lib/polymarket-dota-archive` | Live collector root | **Not present on this Mac** |
| `esports-trader/data/archive/` | Research/dataset snapshots, **not** CLOB books | ~932 MB total |

Layout (Telonex-compatible, also the collector contract):

```text
data/raw/telonex/polymarket/book_snapshot_full/asset_id=<token_id>/<YYYY-MM-DD>.parquet
```

Defined in `polymarket-collector/docs/polymarket_dota_archive_contracts.md` §11 (`:522-571`) and mirrored by Telonex downloads (`src/shared/constants/telonex.py`).

### Depth: full L2, not top-of-book only

Schema (verified on a sample day file):

- `timestamp_us`, `local_timestamp_us`, `exchange`, `market_id` (= `conditionId`), `slug`, `asset_id`, `outcome`
- `bids` / `asks`: `list<struct<price: string, size: string>>` — **full side**, best first

Sample busy day (`2026-09-30`, ~66 MB, 1.23M rows): mean **~29 bid / ~22 ask levels** populated.  
Contract: each row is a **full token book** after a `book` snapshot or one applied `price_change` batch (`contracts.md` §7, §11.1).

### Both tokens (YES and NO)

Against `data/new_processed/universe/universe.parquet`:

| Kind | Markets | Both tokens in `book_snapshot_full` |
|---|---:|---:|
| `map_winner` | 5 070 | **4 919** (97%) |
| `series_winner` | 3 246 | **2 842** (88%) |
| `other` (props etc.) | 67 928 | 0 (not archived by design) |

Match catalog (`3356` linked maps): **3261 / 3356** have both-token books.

Partitions are **per token**, not per market — you join siblings via universe/`token_id_0`/`token_id_1` or collector market sidecars.

### Frequency

- Event-driven (every book / price_change), **not** fixed N-second polling.
- Busy sample inter-arrival: p50 **5 ms**, p90 **0.28 s**, p99 **~21 s** (quiet gaps exist).
- Microstructure study (see §8): map books ~**1 100 updates/min** in the model window.

### Date range and scale

| Metric | Value |
|---|---|
| Calendar days with book files | **353** (`2025-10-14` → `2026-10-05`) |
| Asset dirs (`book_snapshot_full`) | **15 552** |
| Day-parquet files | **32 772** |
| Typical assets/day (recent) | ~44–98 |

Provenance mix: early data from paid Telonex; from collector era, `scripts/sync_collector_parquet.py` rsyncs VPS parquet into this same tree (`CHANNELS = ("book_snapshot_full", "onchain_fills")` — **trades excluded**, lines 40–41 / 153).

### Derived / thinner market series (not L2)

| Path | What | Size / count |
|---|---|---|
| `data/new_processed/market_seconds/v*/` | Per-second `market_p_radiant` (+ 30s/300s signals) from books | ~454 MB across 3 versions; ~2.3k–3.2k match files |
| `data/raw/polymarket_dota/prices_history/*.json.gz` | CLOB price history points `{t,p}` (~360/file typical) | ~41 MB, **10 468** files |
| `data/duck.db` | Stale DuckDB views to relative paths; **broken / not a live store** | 268 KB |

---

## 2. Polymarket trade prints

### WS / Telonex `trades` channel

- Path: `data/raw/telonex/polymarket/trades/asset_id=<id>/<day>.parquet` (~658 MB)
- **14 338** asset dirs; **20 808** day files; days **`2025-10-14` → `2026-09-17` only**
- **No local trade files after 2026-09-17** (collector sync excludes `trades/`)

Schema (verified):

`timestamp_us`, `local_timestamp_us`, `exchange`, `market_id`, `slug`, `asset_id`, `outcome`, `price`, `size`, **`side`** (`buy`/`sell` = aggressor in that token projection), `trade_id`, **`origin_asset_id`**

Mirroring: one economic trade → rows on **both** tokens (`contracts.md` §8 `:412-444`).

Coverage vs universe: map_winner both-token trades **4490 / 5070**; series **2679 / 3246**.

### On-chain fills (continues after trades gap)

- Path: `.../onchain_fills/` (~2.0 GB), **15 820** assets, days through **`2026-10-05`**
- Columns include **`maker` / `taker` addresses**, `maker_side` / `taker_side`, `amount`, `price`, `order_hash`, `mirrored`, sibling asset ids
- Map_winner both-token onchain: **5016 / 5070**

For post-2026-09-17 fill simulation, use **`onchain_fills`** (maker/taker present). Micro study notes on-chain notional often **1.5–2.5×** WS trades (WS can miss prints).

---

## 3. Game-state feeds

### STRATZ (training / backtest game clock) — local bulk

- `data/raw/stratz_matches/`: **~19 546** `match_*.json.gz` (~1.5 GB)
- Fields (sample): `durationSeconds`, `didRadiantWin`, teams, `radiantNetworthLeads`, `radiantExperienceLeads`, `players`, tower/barracks status, optional `playbackData`
- Pipeline uses second-level rebuild (`src/prepare_dataset/stratz_seconds.py`); AGENTS.md: train/BT replay STRATZ seconds, not live GRID frames
- Match catalog: **all 3356** winners sourced `stratz`; `horn_at` ~`2025-10-04` → `2026-10-04`

### GRID (live + archived in trader)

- Live archives: `data/trader/*/grid_state.jsonl(.gz)` — **~811** dirs
- Services observed: `integrity_safe_series_scoreboard_v2`, `integrity_safe_series_table` (kills/clock vs net worth / deaths)
- Latency (`.learnings/pandascore-fast-feed-20260928.md`): scoreboard median **1.35 s** (p90 9.6 s); table **9.65 s**; training/live lag constant **10 s**
- Cadence: live signals ~6–11 s apart by game phase (not 1 Hz); BT `grid-v1` matches that
- Also: `data/raw/polymarket_dota/grid_game_starts/` (~53 MB) for series starts/candidates

### Oddin (live satellite, no-XP model)

- `data/trader/*/oddin_state.jsonl(.gz)` — **~101** dirs
- Snapshot/WS payload: map pause, scores, `currentMap.{home,away}Team` with **kills, netWorth, towers, barracks, roshans, players** (`alive`, `netWorth`, `deaths`, `hero`, …) — **no XP**
- Latency: median **~15.1 s**; ~**1 Hz** signal cadence in live tape

### PandaScore LLF

- **Not archived locally** as a historical feed. Research only (`.learnings/pandascore-fast-feed-20260928.md`): claimed &lt;5 s lag, Dota fields without XP; ROI study used STRATZ proxies for cadence/lag experiments

### PGL / broadcast

- Delay study (`.learnings/pgl-source-delay-20260919.md`): PGL ~**25–30 s** behind Steam/Hawk references — diagnostic only, not a stored feed corpus under `data/`

---

## 4. Bookmaker odds (`data/book_vs_pm`, `data/oddspapi`)

### `data/book_vs_pm/` (~3 MB) — one LoL finals case study

- AL vs IG, LPL GF, **2026-09-12**, maps 3–5
- JSONL polls (~5 s) of **1xbet**, **BetBoom**, Polymarket map odds + plots
- Dota two-sided MM: **not a general historical bookmaker tape**

### `data/oddspapi/` (~60 MB)

- `dota/`: `ticks.parquet` (**205 755** rows: `match_id`, `ts`, `price`, `active`), `hist/*.json.gz` (**874**), `links.json`, journals
- `lol/` similar (~39 MB)
- Coverage research: `.learnings/oddspapi-bookmakers-20260924.md` (Pinnacle/vave/bcgame; history from Jan 2026; live paid)

---

## 5. Series-winner vs map-winner

**Polymarket has both** for Dota, and the collector archives both:

| Kind | Gamma filter (`contracts.md` §3.2) | Universe count | Book both tokens |
|---|---|---:|---:|
| Map | `child_moneyline` + `Game N Winner` | 5 070 | 4 919 |
| Series | `moneyline` + `Match Winner` | 3 246 | 2 842 |

Props/totals (`other`, 67 928) are inventoried in Gamma dumps but **not** subscribed/archived to books.

Deciders: recent Dota often has **no Game-3/5 map market**; Match Winner is the last-map instrument (`.analysis/series-market-2026-09-25/reports/micro-devin.md`).

Live trader today: **map_winner only** (`match.json` / sidecars); series books still sit in Telonex for research.

---

## 6. Market metadata

| Store | Fields | Notes |
|---|---|---|
| Collector sidecar `metadata/markets/<condition_id>.json` | `conditionId`, tokens, `marketKind`, `mapNumber`, `tickSize`, `minOrderSize`, `negRisk`, `feesEnabled`, `gridSeriesId`, `pandascoreMatchId`, … | Normative (`contracts.md` §4.2). **On VPS only** here |
| Gamma closed pages `data/raw/polymarket_dota/universe/events/tag_102366_closed/` | Full event+market payloads | **34** pages; includes `orderPriceMinTickSize`, `orderMinSize`, `negRisk`, `feesEnabled`, `feeSchedule`, `makerBaseFee`/`takerBaseFee`, volume |
| Gamma market dumps `data/raw/polymarket_dota/markets/tag_102366_*.json` | Offset pages | ~14 MB |
| `data/new_processed/universe/universe.parquet` | 76 244 rows: `conditionId`, tokens, `contract_kind`, teams, `seconds_delay`, closed times, … | **No** tick/fee columns |
| `data/trader/*/match.json` + `session.jsonl` `sidecar_binding` | `tick_size`, `min_order_size`, `neg_risk`, token ids | Live maps: tick mostly **`0.01`** (858) vs **`0.001`** (40); `neg_risk` always false in sample |

Example Gamma map market (`sports_fees_v3`): `feesEnabled: true`, `feeSchedule: {rate: 0.05, rebateRate: 0.15, takerOnly: true}`, `orderMinSize: 5`, `orderPriceMinTickSize: 0.001`.

---

## 7. Own live trading records (`data/trader`)

| Item | Value |
|---|---|
| Size | **~9.0 GB** |
| Dirs | **~1033** (archive index: 893 audited, **832 admitted**) |
| Span | horns into **2026-10-06**; feeds **grid 797 / oddin 101** |

Per-map files (typical recent):

| File | Contents |
|---|---|
| `match.json` | Market binding, ticks/min size, feed delays, final/PnL |
| `session.jsonl` | `session_start/end`, **`signal`**, **`quote`**, **`fill`** (`is_maker`, price, size, `net_cash`, `fill_key`, …), `tick_size_change` |
| `core_trace.jsonl(.gz)` | High-rate core events (plans/places/cancels/limits); e.g. **~113k** rows on one map |
| `grid_state.jsonl(.gz)` / `oddin_state.jsonl(.gz)` | Raw feed archive |
| `execution_cleanup.json` | Post-session cleanup |

Index: `data/archive_index/index.parquet` + `schedules/` (~485 MB) + `report.md` (generated 2026-10-05).

Sync from VPS: `scripts/sync_trader.py` (see `docs/rebuild-order.md`).

---

## 8. Existing microstructure / fee analysis

| Doc | Findings (Dota map markets, recent windows) |
|---|---|
| `.analysis/series-market-2026-09-25/reports/micro-devin.md` | Map: spread p50 **3¢**, best depth **~$37–44**, within-3¢ **~$375–392**, **~4.4 prints/min**, **~$306/min**, ~30 makers / 38 takers per model window. Series: tighter/thinner, **~0.5 prints/min**. Same pros quote both. |
| `.learnings/pandascore-fast-feed-20260928.md` | Avg spread ~**3.7¢**; taker fee ~**2.5¢** at 50¢; best ask depth median **$58–80** |
| `src/backtest/postprocess.py:64-83` | Modeled sports maker rebate **`0.15 * 0.05 * qty * p*(1-p)`**; Liquidity Rewards **not** modeled; queue fill assumptions documented |
| `.analysis/dota-audit-2026-09-26/` | Crossed books, queue honesty, Telonex vs collector eras, fill parity |
| LoL-focused `investigations/2026-10-03-lol-edge/reports/decay-dev.md` | Parallel methodology on LoL books (spreads/depth/churn) |

Fees on esports: Gamma `sports_fees_v3` / backtest constants **rate 0.05, rebateRate 0.15**; maker gets rebate share of generated taker fee; paid as `MAKER_REBATE` activity (~daily).

---

## Implications for two-sided MM + merge BT

**Have locally (enough for realism):**

1. Full L2 on **both** tokens for almost all map markets and most series markets (~51 GB, event-level).
2. Aggressor flow: WS trades → mid-Sep 2026; **on-chain maker/taker** through Oct 2026.
3. Join keys: universe + match catalog + (live) tick/min/neg_risk from `match.json` / Gamma pages.
4. Game state for signals: STRATZ catalog + optional live GRID/Oddin schedules (832 admitted).
5. Fee/rebate formula already used in maker BT.

**Gaps / caveats:**

1. After **2026-09-17**, do **not** expect `trades/` — use `onchain_fills` (or re-sync collector trades if you change the sync script).
2. Collector **journals/metadata/checkpoints** are not on this machine; only compacted parquet in the Telonex tree.
3. **Merge** is not in the public tape; simulate CTF merge from YES+NO inventory (deterministic). Competitor merge activity appears in workspace reports (`reports/2026-10-07-two-sided-merge/`), not as a local historical merge dataset.
4. Current maker BT (`src/backtest`) is one-sided + queue on tape — two-sided + merge needs new engine logic, but **inputs exist**.
5. `data/duck.db` is not a usable warehouse; prefer parquet paths above.

**Practical backtest window suggestion:** Prefer maps with both-token books **and** either trades (≤2026-09-17) or onchain fills; catalog **~3261** maps already book-complete; admitted live schedules **832** if you need real feed cadence.