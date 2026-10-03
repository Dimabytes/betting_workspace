# live-dev: live LoL trader results vs backtest on the same maps
Status: FINAL

## Verdict

Live LoL on Polymarket **lost −$448.93 net cash across 431 archived sessions (Aug 31 – Oct 2, 280 maps traded)**. On the **290 maps** the backtest also replayed in archive-schedule mode (joined by `market_slug` only), live lost **−$263.97** while the backtest printed **+$4,111.22 engine PnL (+$964.36 modeled maker rebate → ≈ +$5,076)**.

The backtest **overstates live LoL by ~4.7 percentage points per buy-dollar** (live ROI −2.5% on $10,488 shared buy notional vs backtest +2.2% engine ROI on $190,948, +2.7% with rebate) — and by ~$4.4k in absolute terms on shared maps.

Why (ranked by evidence strength):

1. **Fill-count/scale asymmetry** (verified): live buys 799× / $10.5k total vs backtest 3,570× / $190.9k on the same 290 maps — ~4 live fills per live-traded map at ~$5–$10 clip vs ~18 bt fills per bt-traded map at $300 layers. ~89% of bt buys sit within 120 s of a live fill, so both sides see the same episodes; the bt simply monetizes ~18× more of each one.
2. **Live fills land on adverse seconds** (verified): live buy markout@300s = **−1.35¢/share mean** (+0.5¢ median) vs bt **+1.64¢ quantity-weighted** on the same maps. At matched seconds (|Δt|≤3s), bt's own fills mark out **−3.48¢** vs live −4.21¢ — i.e. the moments live actually gets filled are toxic in the bt's own accounting. Live's resting $5 maker orders fill when flow trades through the bid; the bt queue model books uniform fills across the episode.
3. **The backtested model never ran live** (verified): the 431 sessions used 9 different **12-column** models (20260831T120859Z … 20260927T155503Z); the backtest pins the **77-column** histfix model `20261003T014429Z` (manifest seed0/manifest.json). Live-vs-dataset *input* parity is good (see F5), so the live pipeline is faithful — but the bt measures a different predictor.
4. **Backtest edge is one league** (verified): +$4,333 of the +$4,111 comes from 29 LCK maps (live captured +$14.80). The rest of the shared set is net-negative for the bt too (LEC −$1,341, Hitpoint −$259, EMEA −$246; CBLOL +$597).
5. **Feed lag tail hit the worst live weeks** (verified): scoreboard receipt−occurredAt lag p50 2.7 s / p90 12.2 s / p99 58 s across 431 maps; 11% of frames >10 s, 4.5% >30 s, concentrated in W39 (several maps p90 lag 44–153 s — the week bt claims +$437 and live got −$19).

What to fix first: **re-run the archive-schedule backtest at live-realistic sizing/gating** (clip $5, one open position, live quoter rules) and **make the queue fill model reproduce live's adverse-second fill sampling** (E1, E2 below). Until then the bt's LoL number is not a live forecast.

## Findings

### F1. Live LoL inventory and cash PnL (verified)

Source: `session.jsonl` + `match.json` over `data/trader/` via `work/live-dev/inventory.py`, `live_pnl.py` → `live_maps.parquet`, `live_fills.parquet`.

- 980 archive dirs total; 431 LoL + 414 Dota with sessions; 135 empty/feed-only.
- LoL PM net cash **−$448.93** (Σ `final.pnl.realized_pnl_usdc` = −$448.63; the ~$0.3 delta is dust residuals — `final.pnl` is session `net_cash`, src/trader/match_meta.py:444). Kalshi venue rows inside the same journals: **−$62.61**, kept separate.
- Weekly PM cash (LoL): **W36 −$201.5 (150 maps/101 traded), W37 −$216.4 (94/72), W38 −$25.2 (39/24), W39 −$19.2 (65/25), W40 +$13.4 (83/58)**.
- **Dota live, same tree: +$682.02 over 414 archives / 279 traded maps** — weekly +$14.9 / +$61.3 / +$148.6 / +$95.7 / **+$1,267.3 (W39)** / −$905.8 (W40). Dota clips vary $5–$400 by title (`[clips.dota]` tiers, config/trading.toml:87-92); LoL is flat $5 since Sep 29 (`[clips.lol] default = 5.0`, trading.toml:94-96; earlier weeks ran larger clips — W36 fill p90 = $100, W40 p90 = $5).
- 151/431 LoL sessions had **zero PM fills** (W36 49, W37 22, W38 15, W39 40, W40 25); no-fill maps span all leagues (EMEA Masters 46 is the biggest single group) — mostly "delta never crossed while quoting / quotes never hit", not a whitelist effect.
- Maker-rebate estimate on live LoL fills: **~$85** (0.15×0.05×Σ qty·p·(1−p), src/shared/utils/trading.py:54-63). Rebate is paid daily to the wallet ≥$1 and is **not in session journals** — not recoverable from local archives (activity.parquet snapshot covers only Aug 11–12).
- Residual positions at session end: dust (≤~$4 worst map, 14 maps nonzero). The one large manual-sell incident (grid-3008273-m1, Sep 29) is fully journaled — final cash −$16.15 matches the audit.

### F2. Paired live-vs-backtest on 290 shared maps (verified)

Join: exact `market_slug` only (the earlier event-slug fallback collided maps — e.g. grid-3002601-m1 was paired to an unrelated game-5 dataset row showing a spurious −16.7¢ anchor gap; fixed join → `lol_join3.parquet`). All 290 shared maps are `signal_mode=schedule` (bt replays the live tape).

| week | live PM cash | bt engine PnL |
|------|-------------:|--------------:|
| W36  |      +$13.03 |     +$3,872.19 |
| W37  |     −$205.70 |       −$180.00 |
| W38  |      −$25.22 |     −$1,008.56 |
| W39  |      −$19.23 |       +$436.66 |
| W40  |      −$26.86 |       +$990.93 |
| all  |   **−$263.97** | **+$4,111.22** |

- Live maps not in the bt population: 141, cash −$184.96 (out-of-sample for this bt).
- Per-map corr(live cash, bt pnl) = **0.24**, same-sign rate **58%** — weak per-map predictivity.
- bt maker rebate on shared-map fills: +$964.36 → bt total ≈ +$5,076.
- League split of bt's +$4.1k: **LCK +$4,333 on 29 maps (live +$14.80)**; LEC −$1,341; Hitpoint −$259; EMEA −$246; CBLOL +$597.

### F3. Entry parity is near-perfect; exits are not the gap either (verified)

- Of 698 live buys on both-traded maps, **527 (75%) match a bt buy within 120 s**; matched pairs: live_px − bt_px median 0, mean −0.4 to −0.6¢; first-buy lead median ≈ +0.04 s. Entries are not the problem.
- Live sells nearly everything (unsold ≈ 0.1% of bought qty) vs bt 3.4% carried to settlement; live sell VWAP is 1.13¢ below bt's; mean per-map round-trip margin live −0.45¢ vs bt −1.21¢ — live exits are not worse.

### F4. The gap is fill sampling: adverse-second fills + 18× less notional (verified + likely)

- On 290 shared maps: live 799 buys / $10.5k; bt 3,570 buys / $190.9k per seed (layers=3 × $300, max_position_levels=9 — seed0/manifest.json). On the 162 both-traded subset: live 689 buys / $9.4k.
- **~89% of bt buys are within 120 s of a live buy** → same episodes; bt trades through them, live gets a sliver.
- Live buy markout@300s **−1.35¢ mean / +0.5¢ median** (n=689, live's own mid tape, as-of lookup matching src/backtest/marks.py:42-49); bt `markout_300s` wmean **+1.64¢** on the same maps (fills.parquet, postprocess.py:151-155). At |Δt|≤3 s matched pairs: live −4.21¢ vs bt −3.48¢ — same seconds, same toxicity.
- Live buys with no bt fill ±120 s (n≈130–171): mean markout **−2.9¢**, 58% negative — live's "extra" fills are its worst fills (filled when the book swept through).
- Mechanism (likely): live's quoter stops opening a new episode while holding (`position_open`, src/strategy/quoting.py:319-323) and its $5 resting bids fill preferentially when flow trades through the price — the adverse-selected tail. The bt `queue` fill_model books fills at every trade-through across the whole episode, so its fill sample averages +1.6¢ where live's realized subset averages −1.4¢.
- Forensic example grid-2987004-m4 (LCK): live 2 buys / +$2.30; bt 75 buys / +$910.88 in one episode starting second 121 (~$2,511 notional). Live deployed ~$10 there.

### F5. Live model inputs ≈ dataset rows on shared maps (verified, with caveats)

`feature_parity.py`: live `core_trace` SignalUpdate deltas vs the **same archived live model** re-scored on `validation.parquet` rows at the same second (16 maps sampled; 4 had no core_trace and were skipped — not counted as failures). Columns: `dMAE` = |live delta − dataset delta| mean; `bias` = live−ds signed; `anch` = |live anchor − dataset mid| mean; `dth` = deaths agreement radiant/dire (`n/a` = not recorded in trace).

| map | live model | ticks | dMAE | bias | anch | dth |
|-----|-----------|------:|-----:|-----:|-----:|-----|
| grid-2987005-m3 | 20260904T193238Z | 415 | 0.0090 | −0.0004 | 0.0134 | n/a |
| grid-2968612-m3 | 20260904T193238Z | 287 | 0.0057 | −0.0046 | 0.0049 | n/a |
| grid-3000371-m2 | 20260912T100410Z | 520 | 0.0156 | −0.0138 | 0.0102 | n/a |
| grid-3002601-m1 | 20260912T100410Z | 337 | 0.0192 | +0.0017 | **0.2200** (bad join) | n/a |
| grid-3002604-m1 | 20260919T112946Z | 315 | 0.0063 | +0.0038 | 0.0086 | n/a |
| grid-3000372-m2 | 20260919T112946Z | 461 | 0.0123 | +0.0047 | 0.0165 | n/a |
| grid-2965529-m1 | 20260912T100410Z | 344 | 0.0097 | −0.0074 | 0.0082 | n/a |
| grid-3000375-m4 | 20260915T210431Z | 667 | 0.0051 | +0.0017 | 0.0120 | n/a |
| grid-3000376-m2 | 20260924T195444Z | 340 | 0.0061 | −0.0024 | 0.0144 | 0.95/0.97 |
| grid-3000377-m3 | 20260924T195444Z | 550 | 0.0073 | +0.0039 | 0.0070 | 0.97/0.98 |
| grid-3008213-m1 | 20260921T095813Z | 335 | 0.0053 | +0.0010 | 0.0123 | n/a |
| grid-3000376-m3 | 20260924T195444Z | 533 | 0.0025 | −0.0008 | 0.0045 | 0.96/0.97 |
| grid-3012775-m1 | 20260927T155503Z | 386 | 0.0060 | −0.0011 | 0.0125 | 0.91/0.94 |
| grid-3008288-m1 | 20260927T155503Z | 424 | 0.0037 | −0.0013 | 0.0078 | 0.95/0.91 |
| grid-3008273-m1 | 20260927T155503Z | 367 | 0.0039 | +0.0030 | 0.0050 | 0.97/0.92 |
| grid-3012784-m1 | 20260927T155503Z | 335 | 0.0040 | +0.0000 | 0.0052 | 0.62/0.63 (prior also off 9¢) |

- Excluding the bad-join map, delta MAE **0.0025–0.019** (median ≈0.006), anchor |mean diff| ≤ 0.005; delta corr 0.907 on the cleanest map (grid-3000376-m2); sign flips |Δ|>0.02 ≤ 1%.
- Pre-Sep-24 traces carry `deaths` as null → "0% match" rows are missing fields, not side flips; where recorded (models 20260924T195444Z+), death agreement 91–98% both sides.
- `grid-3002601-m1` anchor −0.167 is a **join collision** (archive is map 1; dataset row linked via event slug to game_number=5), not a parity failure — evidence for keeping market_slug-only joins.
- Live sessions used nine 12-col models; none equals the bt's 77-col `20261003T014429Z`. Live-vs-dataset input parity verifies the pipeline; it does not make the bt's model live-tested.

### F6. Live cadence and lag vs the bt's bands (verified)

`cadence.py` → `live_cadence.parquet` (431 maps); `score_lag.py` → `live_score_lag.parquet` (257,771 lag samples).

- GRID series_table inter-frame gap: **median 1.21 s, p90 10.8 s**; 6.2% of gaps >15 s. Scoreboard gaps: 1.0 s / 10.6 s. Signal-row seconds: median gap 3 s, p90 12 s.
- Scoreboard feed lag (received − `occurredAt`, delay=0 service): **p50 2.72 s, p90 12.2 s, p99 58 s; 11% >10 s, 4.5% >30 s**. Worst maps cluster in W39: grid-3008222-m2 p90 lag 153 s, 3008221-m2 138 s, 3008222-m1 80 s, 3008221-m1 51 s.
- Table frames carry upstream `delay=8` (GRID-side); no occurredAt — real age ≈ 8 s + transit.
- Signal reasons (weighted): model 87.2%, missing_prior 8.3% (map-start prior build), one_sided_book 2.5%, missing_book 0.9%, **stale 0.6%** — the stale gate rarely fires.
- `LOL_GRID_V1_BANDS` (src/backtest/signals.py:106-111: 8 s→180, 6 s→360, 5 s after) applies only to the 949 synthetic grid-v1 maps — the 290 shared maps replay the live tape itself, so cadence is identical by construction there. Caveat for grid-v1 maps: real cadence (~1.2 s median) is much faster than the synthetic 5–8 s, and real lag has a far heavier tail than a fixed `source_lag_seconds=11` (manifest).

## What I ruled out

- **Entry slippage**: matched live/bt buys differ by <1¢ median at ≈0 s offset (F3).
- **PnL accounting**: `final.pnl` = session net_cash; manual sells journal correctly (grid-3008273-m1); residual positions are dust; Kalshi separated.
- **Map-join artifacts**: event-slug fallback produced a fake −16.7¢ "parity failure"; corrected to market_slug-only (F5).
- **Deaths/side orientation**: zero-match death rows are null fields in pre-Sep-24 traces; where recorded, 91–98% agreement.
- **Stale-feed model skips**: only 0.6% of signal ticks are `stale` — not the cause of missing fills.
- **Live exiting badly**: live sells more completely (∼0.1% unsold) at comparable prices (F3).
- **bt selecting better maps**: the 39 bt-only maps actually lost −$431 in the bt; live's 32 exclusive maps lost −$11.
- **Settlement fallback in bt markouts**: bt uses last-mid as-of + settlement only when no mid exists (marks.py:42-49, postprocess.py:135-148); my live markouts now use the same as-of rule — the −1.35¢/…+1.64¢ gap is not a methodology artifact.

## Proposed experiments

**E1 — Live-scale replay of the archive-schedule bt (highest priority).**
Re-run the same 290-map schedule set with live-realistic sizing and gating: `layer_usdc=5`, `layers=1..3`, live clip table, and a gate that stops opening new episodes while a position is held (mirror quoting.py:319-323). Command sketch:
`uv run python src/backtest/run.py --game lol --signals archive --layer-usdc 5 --layers 1 --layers-structure live ...` (exact flag names per the bt CLI; restrict `selected_matches` to the 290 shared match_ids). Expected: if scale+single-position is the dominant cause, engine PnL collapses toward ~live's per-$ ROI; residual gap ⇒ fill-model optimism. Metrics: engine PnL, ROI per buy-$, fills/map, per-map corr vs live cash. Cost: one bt replay, minutes–hour.

**E2 — Fill-sampling realism in the queue model.**
Add a pessimistic fill rule and re-run seed0 only: e.g. fill only when traded-through volume strictly exceeds `queue_ahead`, or drop fills whose `markout_30s < −2¢` (adverse-fill proxy). Expected: bt mean buy markout@300s falls from +1.6¢ toward live's −1.4¢; quantifies how much of +$4.1k is unreachable queue fills. Metrics: wmean markout_300s, engine PnL, fill count vs live's 799. Cost: small change in the fill evaluator + one replay.

**E3 — Same-model live replay.**
Score the 290 schedule maps with the *oldest* live 12-col model (20260904T193238Z) inside the bt — direct A/B of predictor vs predictor on identical tapes. Expected: if histfix is genuinely better, its buy markouts beat the 12-col model's on the same fills; if not, the bt's edge is mostly fill-model, not model quality. Metrics: markout distribution, per-map PnL diff by model. Cost: one replay per model.

**E4 — Feed-age entry gate (live-side, cheap).**
Live: skip entries when scoreboard lag >15 s (11% of frames >10 s, 4.5% >30 s, worst in W39). Validation replay: drop signal ticks whose frame age >15 s on the 290-map schedule set and compare engine PnL. Expected: small positive; removes the worst lag-tail trades (W39-type weeks). Metrics: fills skipped by map-week, PnL delta. Cost: ~10-line live guard + one replay.

**E5 — League-tiered LoL clips (config-only, live).**
`[clips.lol]` is flat $5 (trading.toml:94-96) while bt edge concentrates in LCK (+$4.3k on 29 maps). Add tiers e.g. LCK/LEC/LCS clip $50–100 with capped rungs; keep minors at $5. Expected: captures the concentrated positive edge without scaling toxic leagues (EMEA/Hitpoint were negative even in bt). Metrics: live ROI per league over 2–3 weeks. Cost: config edit; capital at risk bounded by rung cap.

## Scripts (investigations/2026-10-03-lol-edge/work/live-dev/)

- `inventory.py` — scan all match.json + session.jsonl → `live_inventory.parquet`
- `live_pnl.py` — per-map venue-split PnL + fills → `live_maps.parquet`, `live_fills.parquet`
- `cadence.py` — per-map GRID frame gaps + signal reasons → `live_cadence.parquet`
- `score_lag.py` — scoreboard received−occurredAt lag → `live_score_lag.parquet`
- `feature_parity.py` — live SignalUpdate vs same-model dataset rescoring → `feature_parity_summary.parquet`, `feature_parity_ticks.parquet`
- `markout_parity.py` — live buy markouts vs bt markout_300s → `live_markouts.parquet`, `permap_bt_markout.parquet`
- joins/artifacts: `lol_join3.parquet` (market_slug join), `parity_maps.parquet`, `parity_fills.parquet`, `parity_sell.parquet`, `signal_parity.parquet`, `match_meta_scan.parquet`, `live_unmatched_fills.parquet`
