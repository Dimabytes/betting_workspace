# bt-devin — why the LoL backtest is so profitable and whether live can realize it

Workspace: `E=/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader`
Research root: `R=/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23`
Scratch: `R/work/bt-devin/` (scripts + outputs referenced below)

All numbers reproduced by read-only parquet/json reads; no backtests, training, or tests were run.

## TL;DR

1. **The LoL backtest signal clock is ~8 s ahead of anything live can see.** `grid_v1` signals fire at `state_ts_us` = the livestats frame's own wall stamp (`src/lol/05_prepare_dataset.py:281`, `src/lol/livestats_frames.py:633-656`). Measured against real GRID archive arrival for the same game second: `state_ts_us − received_ns` p50 = **−8.0 s** across all 163 archive-schedule matches (`work/bt-devin/clock_offset_per_match.csv`, per-match p50 range −6.8…−13.1 s). Live sees the same state ~8 s later.
2. **The fill engine then lets orders trade on the market reaction to the very event that generated the signal.** Backtest signal→submit median 0.125 s, accept +85 ms (`order_timing.py` out: signal→submit p50 0.125 s, submit→accept 0.085 s); median BUY fill lands **4.6 s after the signal stamp** — i.e. inside the +4–5 s death→mid reaction window documented in shared context. **68 % of `grid_v1` buy quantity fills before `state_ts_us + 8 s`**, before a live order could even exist in the book.
3. **This is the whole +0.51¢ markout mystery.** LoL BUY mk30: `grid_v1` +0.619¢ vs `schedule` (signals emitted at real GRID `received_ns` ticks) **+0.086¢**. The archive-schedule cohort — the only mode with honest feed timing — already shows ~flat entry markout. Dota avoids the trap differently: its dataset keys features 10 s *stale* vs the market timestamp (`src/prepare_dataset/prepare_dataset.py:152,154`, `208`), so its backtest markout is a realistic **−0.383¢**.
4. **Size does not rescue it, it shrinks it.** Backtest runs 3×$100 = $300/map capacity; live LoL ran $5–$20/level ($15–$60/episode). Capping recorded fills to live clips captures only **8 % ($15 cap) to 32 % ($60 cap)** of backtest fill quantity. Live turnover since 9-18 is $686 across 44 maps (~$16/map) vs backtest $224/map.
5. **Universe works against live too.** The 945 validation maps (2026-06-04→09-19) are dominated by majors that are profitable in backtest (LCK +$13.60/map ×153, LEC +$7.84 ×129, LES +$12.14 ×77, MSI +$8.63, EWC +$13.86). Live since 8-31 is 305 maps dominated by tier-2 leagues that are *negative even in the backtest*: Hitpoint −$7.29/map, NACL −$9.59, LPLOL −$10.74, LRN −$7.46 (since-8-31 validation slice). Live lost −$458.67 on those 305 maps.
6. **Closed-loop livecohort replays confirm the harness can reproduce live, and confirm the gap is upstream (feed timing), not in the matching engine.** On the same 16 live-traded maps, `archive-baseline-v5` replay = −$23.38 vs live −$26.46 (fills nearly 1:1), while `lol-current-sep14v3` (current policy) = +$4.91 — a $31 policy/selection swing on an identical tape.
7. **Verdict:** live (~$0 since 9-18) is consistent with the realistically-timed `schedule` economics (~$8–12 expected at live size); the headline $6.79/map is unreachable because ~⅔ of it is earned in a temporal window live cannot occupy. Live −$458.67 since 8-31 is *worse* than even a size-discounted backtest CI — indicating additional execution/config losses on top of the timing haircut.

---

## Findings

### F1 — LoL `grid_v1` signals are stamped ~8 s before live can observe the same state  ·  stage: backtest ·  severity: critical ·  confidence: verified

**Claim.** For the 782 `grid_v1` matches in `LIVE/seed0`, every signal/decision tick is keyed to the dataset's `state_ts_us`, which is the wall-clock stamp of the selected livestats frame — not the time a GRID feed would deliver that state.

**Evidence.**

- `src/lol/05_prepare_dataset.py:281` — dataset row: `"state_ts_us": slot.state_wall_us`; `src/lol/livestats_frames.py:633-656` — `select_grid_rows` stores `state_wall_us = round(chosen.wall_seconds * 1_000_000)` (the frame's own rfc460 wall stamp), choosing the latest frame with `game_time <= second`, age ≤ 2 s.
- `src/backtest/signals.py:261` — `feed_timestamps_ns` for replay = `ordered["state_ts_us"] * NS_PER_US`; decisions are emitted at these instants.
- Measured offset, `work/bt-devin/clock_offset.py` → `clock_offset_per_match.csv`: for each of 163 archive-schedule matches, `state_ts_us(second) − min received_ns(game_second)`; pooled p50 **−8.0 s** (mean −8.03, p10 −8.59, p90 −7.46; every match negative).
- Live corroboration from `data/trader/grid-3008219-m1/grid_state.jsonl` (+ all recent LoL tapes, n=7,950 scoreboard frames): `received_at_utc − gameClock.occurredAt` p50 **+2.76 s** (p25 2.32, p75 3.39; GRID `publishDelay` field = 2 s typically). GRID's `occurredAt` itself sits ~5–6 s behind the true event (shared context: scoreboard ≈7 s behind wall) — consistent with the −8.0 s archive measurement.

**Mechanism.** The backtest's "now" for LoL signals is the event's true wall time (livestats frame stamp ≈ GRID `occurredAt`). Live must wait for GRID transport (~2.8 s after `occurredAt`) — so every `grid_v1` decision is computed ~8 s before the equivalent live decision could be.

### F2 — Fills land inside the market-reaction window the live order cannot reach  ·  stage: backtest ·  severity: critical ·  confidence: verified

**Claim.** The queue model is mechanically sound (trade-through or level-deletion, no free touch-fills), but the *order exists earlier than physically possible* for `grid_v1` matches, so it is filled by the reaction trades caused by the same game event that produced the signal.

**Evidence.**

- `src/backtest/run.py:567` — `ExecutionModelConfig(queue_position=True, latency_model=…)`; Nautilus doc `.venv/.../nautilus_trader/backtest/config.py:120-124`: with `queue_position`, a limit order fills only after the quantity ahead at placement **"has been traded through or the price level is deleted"**. `postprocess.py:66-71` documents the same assumption. Accept = submit + `NETWORK_LATENCY_MS` (85 ms).
- `order_timing.py` output (`LIVE/seed0` fills + quote_events): signal→submit p50 0.125 s; submit→accept 0.085 s fixed; **signal→fill p50 4.573 s** (p25 1.75, p75 10.2); accept→fill p50 3.76 s.
- Shared context: archived death→midpoint reaction probe peaks **+4–5 s after the frame stamp**. Median fill at +4.6 s sits exactly on the reaction.
- Fills before live could exist: 3,921 BUY fills / **241,947 sh fill before `state_ts_us + 8 s`** (68 % of grid_v1 buy qty = 199,706/294,474 sh for grid_v1 alone), mk30 +0.66¢ on that early subset.
- Markout by signal→fill latency (order_timing.py): grid_v1 fills arriving 0–6 s after the signal stamp earn +0.40…+1.04¢ mk30; the same window for `schedule` matches (real GRID timing) is **−0.22…−0.32¢** — the classic post-event adverse fill live actually experiences.
- `markout_decomp.txt`: all 5,759 LoL BUY fills `is_maker=True` (join placement); no taker fills exist.

**Mechanism.** Order accepted at T+0.2 s joins the *pre-reaction* queue; reaction trades at T+4–5 s (or level deletions as makers pull) fill it. Live's earliest possible accept is ~T+8.3 s — after the move. So the backtest is systematically filled *on the move the signal predicted*, an impossibility live.

### F3 — Dota avoids this only because its dataset lags features 10 s; the `source_lag_seconds=10` parity is illusory  ·  stage: prepare ·  severity: high ·  confidence: verified

**Claim.** Both pipelines nominally use `source_lag_seconds=10`, but the semantics differ: LoL pairs a *fresh* state with a *same-instant* price; Dota pairs a *10-s-old* state with the current price.

**Evidence.**

- LoL: `src/lol/05_prepare_dataset.py:311` — `current = lookup_market_p_after(…, slot.state_wall_us, 0)` — market mid sampled **as-of the frame's own stamp** (features fresh, price synchronous).
- Dota: `src/prepare_dataset/prepare_dataset.py:152-154` — validation row keyed to `market_second` with features from `market_second − lag_seconds`; `state_ts_us` = market-wall time of `second`. Features are deliberately 10 s stale relative to the price.
- Same measured clock: Dota `state_ts_us − received_ns` p50 = **−7.30 s** (`dota_clock_offset.py`, pooled n=54,142) — i.e. for Dota the signal stamp is *also* ~7 s before the feed tick for the same-numbered second, but since features are already 10 s old at the stamp, live effectively sees the same features ~2.7 s *earlier* than the backtest does.
- Consequence in markout (`markout_decomp.txt`): Dota BUY mk30 −0.383¢ overall (grid_v1 −0.454¢, schedule +0.039¢); every game-second bucket flat-to-negative. LoL BUY mk30 +0.513¢, concentrated in early buckets (0–120 s: +1.54¢; 360–480 s: +0.06¢ — edge dies exactly where late-game micro slows).

**Mechanism.** Dota's 10-s feature staleness means the market has already digested the state the model evaluates — the +4–5 s reaction is inside the lag window — so entries are realistically adverse. LoL evaluates fresh state vs pre-reaction price: the model captures not-yet-realized drift. This single convention difference explains the mk30 sign flip between games and why Dota live (+$380 on ~98 maps ≈ $3.9/map) matches its backtest ($4.48/map) while LoL does not.

### F4 — Markout decomposition: the positive edge is an artifact of timing, not signal quality  ·  stage: backtest ·  severity: high ·  confidence: verified

Source: `work/bt-devin/markout_decomp.txt` (script `decompose_markout.py`).

| slice | LoL mk30 | Dota mk30 |
|---|---|---|
| overall | **+0.513¢** (5,759 fills, 367,562 sh) | **−0.383¢** (1,836 fills, 145,634 sh) |
| `grid_v1` | +0.619¢ (4,424 fills) | −0.454¢ |
| `schedule` | **+0.086¢** (1,335 fills) | +0.039¢ |
| second 0–120 | +1.543¢ | −0.956¢ |
| second 120–240 | +0.617¢ | −0.088¢ |
| second 240–360 | +0.675¢ | −0.414¢ |
| second 360–480 | +0.062¢ | −0.497¢ |
| signal age 0–2 s | +0.587¢ | −0.296¢ |
| time-since-event 0–2 s | **−2.433¢** (466 fills) | +1.170¢ |
| tse 5–20 s | +1.6…+1.9¢ | −0.1…−0.7¢ |
| price 0.4–0.6 | +0.82…+1.01¢ | −0.03…−0.54¢ |
| price ≥0.7 | ~0¢ | ~0¢ |

Notes:
- `queue_ahead` buckets (size ahead at submit): LoL mk30 positive in all buckets (+0.32…+0.79¢) including >2,000 sh ahead — consistent with level-deletion fills as makers pull pre-move.
- Per-map `engine_pnl`: grid_v1 mean **+$5.99** (n=782) vs schedule mean **+$2.99** (n=163) — and the schedule cohort still benefits from `$300` size and includes favorable-period matches; it is the upper bound of live-realizable.
- The −2.43¢ tse 0–2 s bucket: fills that land inside 2 s of the detected event are adversely selected even in the sim — the +0.5¢ overall comes from the 4–15 s post-event window that live misses entirely.

### F5 — Size: $300 capacity vs live $15–$60 cuts captured quantity ~3–12×  ·  stage: live ·  severity: medium ·  confidence: likely

**Evidence.**

- Backtest `base_size_usdc=300` = 3 layers × $100 (LIVE/seed0 summary); live `core_trace.jsonl` header for `grid-3008219-m1` shows `level_usdc=20.0, level_count=3` → $60/episode; earlier live ran $5/level (=$15).
- Per-order analysis (`fills.parquet`, `order_id` groupby): mean filled 109.5 sh/order (median 123.5). Capping each order's fill at live layer size: **$5/level → 7.7 %** of backtest qty, **$20/level → 27.5 %**, $60/level → 68.6 %. Episode-level cap: $15 → 8.3 %, **$60 → 31.9 %**, $300 → 96.8 %.
- Live actuals: since 9-18, 67 BUY fills, 1,050 sh, **$686 turnover** (≈$15.6/map); since 8-31, 785 BUY fills, 22,276 sh, **$13,145** (≈$43/map) vs backtest $224/map bought.
- Live signal→order-submit latency (`core_trace` now_ns anchored to `opened_wall_s`): p50 **0.170 s**, p90 2.39 s — live order-side latency is *not* the problem; the feed is.

**Mechanism.** Queue-priority is by time, not size, so a smaller order at the same instant would fill at least as well per share — markout/share is roughly size-invariant — but filled quantity scales ~linearly with cap in the observed partial-fill regime. Effect: even if the backtest edge were realizable, $60-clip live captures ≈⅓ of the per-map PnL; the $5-clip era captured <10 %.

### F6 — Universe: live trades a different, worse league mix  ·  stage: live ·  severity: medium ·  confidence: verified

**Evidence** (`val_maps_league.parquet`, `live_lol_maps.parquet`; leagues via `universe/markets.parquet` condition_id join, 283/305 live maps resolved):

- Validation 945 maps, 2026-06-04→09-19: LCK 153 (+$13.60/map), LEC 129 (+$7.84), NACL 81 (+$0.73), EMEA Masters 78 (−$4.26), LES 77 (+$12.14), LCS 64 (+$1.18), MSI 59 (+$8.63), CBLOL 57 (+$1.05), EWC 48 (+$13.86), KeSPA 48 (+$8.08); tier-2 minors negative: Hitpoint −$3.98, LPLOL −$3.76, LRN −$4.83.
- Live since 8-31 (305 maps, **−$458.67**): Hitpoint 42, LCK 32, LEC 31, RoadOfLegends 23 (−$77 live), NACL 22, LCS 21, LPLOL 19, LFL 9 (**−$120.65**), Rift Legends 7 (−$59.41), HLL 4 (−$41.58) — the mix is ~70 % leagues that are negative in the backtest.
- Live since 9-18 (44 maps, **+$0.68**): LEC 10 (+$6.10), LCS 9 (+$7.28), Hitpoint 3, unknown-league 22 (−$12.69; EMEA/tier-3 slugs: sc/ap/vdn/mcn/ucam/piv/lds/bublik/nbs/esb/…).
- Expected live PnL (per-map dist: mean $5.47, std $37.18, n=945):
  - 44 maps @ $60-ep scale (×0.319): E ≈ **$77**, 95 % CI **[−77, 231]** → live +$0.68 inside.
  - 44 maps @ $15-ep scale (×0.083): E ≈ $20, CI [−20, 60] → inside.
  - League-adjusted for the actual mix ×0.32: E ≈ $63.
  - 305 maps @ ~15 % scale: E ≈ $250, CI [59, 441] → live −458.67 is **outside, below** — the historical bleed exceeds what size+timing alone predict; consistent with config churn (clip flapping $200→$5, midspike variants, crashdiag runs) and the −$120 LFL episode.
- Alternative per-share check: backtest edge incl. rebate 1.742¢/sh → on 22,276 live shares E ≈ $388 vs actual −$459; using the honest `schedule`-cohort per-share rate (~$487/41k sh ≈ 1.19¢/sh) → E ≈ $265 still ≫ −$459. For 9-18: 1,050 sh × 1.2¢ ≈ $12 expected vs $0.68 — inside noise.

### F7 — `livecohort_*` runs: closed-loop replays of the live tape; they reproduce live when configured like it  ·  stage: backtest ·  severity: info ·  confidence: verified

**What they are.** `data/backtests/lol_maker/livecohort_*/seed0/` — replays whose book feed is the live session's own `core_trace`/tape (`summary.json` assumptions: `"source": "data/trader core_trace closed-loop"`, `"PnL is approximate vs live; compare variants on the same tape"`). Each run carries `live_reference.parquet` with per-map `live_equity`, live fill counts, and the cohort is live-traded maps since `grid-2987006-m2` (Sept 10–13 slugs: gen-hle1, mea-brt, lll-png1, tos-wd, sen-fly).

**Results** (16-map cohorts; `live_equity_sum = −26.46` on all):

| run | fill_model | policy | bt PnL | Δ vs live |
|---|---|---|---|---|
| archive-baseline-v2 | live_touch | archive | −27.12 | −0.66 |
| archive-baseline-v4/v5 | hybrid | archive | −23.38 | +3.08 |
| lol-current-sep14(v3) | hybrid | current | +4.91 | +31.4 |
| midspike / v2 | live_touch | midspike | +11.23 | +37.7 |
| midspikev3 | live_touch | midspikev3 | −5.99 | +20.5 |
| midspikev2-fixsell (4 maps) | live_touch | — | −9.83 | −0.76 |

**Per-map compare** (`live_reference` × `results` on slug): `archive-baseline-v5` nearly mirrors live fills (e.g. sen-fly-game2 8/5 fills → +12.80 = live +12.80; mea-brt-game2 2/1 → −13.71 vs live 4/11 → −17.73). `lol-current-sep14v3` differs chiefly by *not* trading the worst map (`lol-mea-brt-2026-09-10-game2`: bt 0 fills/0.00 vs live −17.73) and slightly better exits on sen-fly-game3 (−10.64 vs −14.17).

**Read.** When the backtest consumes the same tape live saw, results track live within a few dollars — so the matching engine is not the gap. Policy/fill-model variants swing ±$30 on 16 maps, i.e. these runs measure *policy deltas on a live tape*, not headline profitability. The +$4.91 of `current` policy on this cohort is selection, not evidence of the $6.79/map edge.

### F8 — What actually explains live ≈ flat since 9-18  ·  stage: synthesis ·  confidence: likely

Decomposition of the $6.79/map headline:

- Timing haircut (F1–F4): the grid_v1 share of edge is unreachable; realistic entry mk30 ≈ schedule +0.09¢ and per-map engine PnL ≈ $2.99 at full size.
- Size haircut (F5): ×~0.32 at current $60 episodes (×0.08 in the $5-clip era) → ~$0.95/map.
- Universe haircut (F6): live mix is weighted to leagues where even the optimistic backtest loses.

Composite expectation for the 44-map window: ~$5–20 total (order-of-magnitude from three independent scalings: schedule-per-map×0.32 ≈ $42 full-mix-unadjusted, league-adjusted ×0.32 ≈ $63 *if* edge were realizable, per-share realistic ≈ $12). Observed **+$0.68** (shared summary computed +$2.27 — same order; mine sums `final.pnl.realized_pnl_usdc` over the 44 match.json files). Live is inside the (very wide) CI in all framings — the flat live result is *consistent* with the model having a small real edge that is simply too small to see at these clips and this league mix. It is **not** consistent with the $6.79/map headline.

---

## Checked and OK

- **Queue model itself is not the bug** (verified): `queue_position=True` (`run.py:567`); fills require trade-through of queue-ahead size or level deletion (`config.py:120-124`, `postprocess.py:66-71`); all fills `is_maker=True`; `queue_ahead` is recorded at submit (`strategy.py:1094`) and populated in `fills.parquet`.
- **Latency model applied** (verified): submit→accept is a flat +85 ms (`NETWORK_LATENCY_MS`), present in every fill record.
- **No free touch fills observed** (verified): 489 fills had `queue_ahead ≤ 0` (join-at-best or better) — all other fills required volume ahead to clear; mk30 positive across all queue buckets rules out "fills only when touched at bad prices".
- **Live-side order latency is fine** (verified): live signal→submit p50 0.17 s on `grid-3008219-m1` (core_trace `now_ns` vs `opened_wall_s` anchor); not the bottleneck vs the ~8 s feed gap.
- **Dota convention sanity** (verified): `dota_maker/LIVE/seed0` — 583 matches, mk30 −0.383¢ CI [−0.69, −0.08], net $4.48/map; live Dota ≈ $3.9/map realized. The honest-timing pipeline produces honest, realizable numbers.
- **`signal_mode` plumbing** (verified): `signals.py:261` feed stamps = dataset `state_ts_us`; archive-schedule matches instead emit decisions at real `received_ns` ticks (`feed_schedules.py`, `_match_schedule_decisions`), which is why the schedule cohort is the honest-timing control group.

## Open questions for the owner

1. **Retrain/regenerate the LoL dataset with the Dota convention?** I.e. key rows to market second and pull features from `market_second − 10 s` (or from the GRID-archive arrival second). That would make LoL markouts honestly negative like Dota's and give a trustworthy PnL estimate. Cheaper alternative: keep features but re-stamp `state_ts_us` to `wall(second) + ~8 s` so fills can't land pre-reaction.
2. **Is `state_wall_us` the frame's event-time or receipt-time stamp?** My measurements are consistent with event-time (≈GRID `occurredAt`), but if any livestats frames carry receipt-time stamps the −8 s offset would partly reflect a different effect. Worth a one-off check on one raw livestats pull.
3. **Why did live lose −$458.67 while even pessimistic scaling predicts ≥ −$100?** Candidates: clip flapping ($200→$5 on 9-16), midspike variants, the −$120 LFL cluster, crashdiag anomalies. May deserve a per-map live PnL audit against config-at-the-time — beyond my read mandate but cheap with `live_lol_maps.parquet` + core_trace headers.
4. **Sell-side**: LoL SELL mk30 is also worse than Dota's (−0.59¢ vs −0.14¢ in summary). Exits use the same signal clock — the same 8 s distortion likely cuts both ways; I did not decompose sells.
5. **Liquidity Rewards** are not modeled (noted in assumptions) — irrelevant to the gap direction (would only add PnL).

## Needs from VPS

- None required for this analysis. If the owner wants the live-side equivalent of the −8 s measurement on the VPS host (collector clock skew vs this Mac), the grid_state `received_at − occurredAt` per-map stat is the one to recompute there.

## Scripts and outputs

All under `$R/work/bt-devin/`:

| file | what |
|---|---|
| `decompose_markout.py` → `markout_decomp.txt` | BUY mk30/mk300 decomposition for LoL LIVE/seed0 + Dota LIVE/seed0 by signal_mode, second, signal age, time-since-event, league, price, queue_ahead, is_maker; per-map engine_pnl by mode |
| `clock_offset.py` → `clock_offset_per_match.csv` | per-match `state_ts_us − received_ns` for 163 archive-schedule LoL matches (p50 −8.0 s) |
| `dota_clock_offset.py` | same offset for Dota (p50 −7.30 s, n=54,142) |
| `order_timing.py` | signal→submit / submit→accept / signal→fill latencies; mk30 by signal→fill bucket; fills before `state+8s` split. (Stdout captured in transcript; final parquet write crashed on an interval dtype — stdout numbers are complete and used above.) |
| `lol_buys_enriched.parquet`, `dota_buys_enriched.parquet` | per-fill enriched frames used by the decomposition |
| `val_maps_league.parquet` | 945 validation maps × league × engine_pnl |
| `live_lol_maps.parquet` | 305 live LoL maps × slug × horn × league × realized PnL |

Commands used (all from `$E`): `PYTHONPATH=src uv run python $R/work/bt-devin/<script>.py`; pandas/pyarrow reads of `data/backtests/lol_maker/*/seed0/{summary.json,results.parquet,fills.parquet,quote_events.parquet,live_reference.parquet}`, `data/lol/processed/{datasets,universe}/*.parquet`, `data/archive_index/{index.parquet,schedules/trader/*/*.json}`, `data/trader/grid-*/{match.json,session.jsonl,core_trace.jsonl,grid_state.jsonl}`.
