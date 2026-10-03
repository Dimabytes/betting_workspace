# decay-dev: LoL Polymarket microstructure per period — did the market get faster/tighter?
Status: FINAL

## Verdict (≤10 lines)

Yes — the LoL Polymarket book measurably accelerated into August–September 2026. On ~60
whitelisted traded maps per month (Jun–Sep), book churn roughly doubled in September
(1,249 → 2,411 radiant-token snapshots/min), repricing of the largest 60 s gold swings now
completes *inside* our 11 s source lag (median time to +1 ¢ in swing direction 4.7 s, to
+2 ¢ 9.8 s in Sep, vs 7–9 s / 16–21 s in Jun–Aug), top-of-book depth within 2 ¢ fell ~60–75 %
vs July, taker flow got ~50 % more toxic (realized 30 s spread 0.56 ¢ vs 0.15–0.37 ¢), and
adverse excursion on events tripled (−1 ¢ → −3 ¢ median). September LoL books are ~2× faster
than September Dota books (t20 9.8 s vs 19.5 s), while June–July LoL was at Dota-like speed.
A maker whose signal lands 11 s after the frame now arrives mid-reprice; in June it arrived
before it. That is the microstructure signature matching the markout decay 2.5 ¢ → 0.5 ¢.

## Setup (what I measured)

- Population = the 1,239 maps traded in `data/backtests/lol_maker/LIVE/seed0/results.parquet`
  (whitelisted + eligible). Sample: ~60/month Jun–Sep stratified by league (241 maps;
  `sample_maps.parquet`). Dota comparison: 60 of 284 Sep maps traded in `dota_maker/LIVE/seed0`.
- Per map I load `book_snapshot_full` day files for both tokens over the wall window
  covering game seconds 0..480 (`state_ts_us` at second 0 → second 480; `state_ts_us` is the
  GRID frame stamp, and the model's "current mid" is sampled at stamp+11 s:
  `src/lol/05_prepare_dataset.py:269-284`, `src/shared/constants/lol.py:52`), plus the
  `trades` channel for the radiant token and `onchain_fills` for both tokens.
- Events: |Δ`radiant_nw_adv` over 60 s| ≥ global p90 (616 gold; per-month p90 is stable
  583–640), ≤ 8/map, ≥ 45 s apart, event second ∈ [60,480]. n = 89/131/101/100 events per
  month, ~130 for Dota Sep (129 with |move300| ≥ 0.5 ¢). Reaction measured on the
  *pair-normalized* market_p tape
  (`r/(r+d)`, |r+d−1| ≤ 0.05 — same semantics as `resolve_market_pair` in
  `src/shared/utils/telonex_book.py:73`).
- Caveats: radiant-token-only trades tape (dire-side takes are missed in trade attribution);
  snapshot rate counts exchange book events, not distinct quotes — consistent across months;
  ~60-map samples, league cells get thin.

## Per-month table (median per map, game seconds 0..480)

| metric | Jun | Jul | Aug | Sep | Dota Sep |
|---|---|---|---|---|---|
| book updates/min (radiant) | 1,429 | 1,425 | 1,249 | **2,411** | 1,711 |
| book updates/min (both tokens) | 2,858 | 2,849 | 2,498 | **4,821** | 3,421 |
| spread med (¢) | 2 | 1 | 1 | 1 | 2 |
| spread mean (¢) | 2.1 | 1.7 | 1.8 | 1.9 | 2.9 |
| depth within 2 ¢ (units, bid+ask) | 2,716 | 6,483 | 4,567 | **1,732** | 1,919 |
| book levels med | 52 | 68 | 66 | 59 | 58 |
| inter-update gap med (ms) | 9 | 5 | 6 | **4** | 6 |
| snaps w/ unchanged top prices | 97.9 % | 97.9 % | 97.4 % | 98.1 % | 97.4 % |
| radiant trades/min | 9.4 | 35.2 | 16.6 | 9.9 | 7.9 |
| trade size med (units) | 23 | 37 | 25 | 20 | 39 |
| mid moves per map window | 243 | 296 | 311 | **396** | — |
| mid moves w/ trade ≤1 s before | 41 % | 53 % | 38 % | **32 %** | — |
| effective half-spread mean (¢) | 0.63 | 0.57 | 0.71 | **0.80** | 0.90 |
| realized 30 s spread mean (¢) | 0.33 | 0.15 | 0.37 | **0.56** | 0.79 |
| **event reaction** | | | | | |
| first mid change after frame (s) | 2.79 | 2.45 | 1.46 | **1.20** | 2.27 |
| time to +0.5 ¢ in swing dir (s) | 4.98 | 5.03 | 2.34 | **2.07** | 6.22 |
| time to +1 ¢ (s) | 7.06 | 9.04 | 7.87 | **4.73** | 11.02 |
| time to +2 ¢ (s) | 16.0 | 19.2 | 21.4 | **9.82** | 19.5 |
| share of +2 ¢ moves done <11 s | 44 % | 42 % | 41 % | **52 %** | 38 % |
| book snaps in 3 s after event | 87 | 62 | 88 | **170** | 77 |
| signed move at +11 s (med) | 0 | 0 | 0 | 0 | 0 |
| signed move at +300 s (med/mean, ¢) | 2.5 / 1.9 | 3.0 / 2.5 | 3.0 / 1.9 | **2.0 / 1.1** | 3.0 / 4.2 |
| adverse excursion ≤120 s (med, ¢) | −1.0 | −1.0 | −1.0 | **−3.0** | −2.5 |
| mid-move half-life of 300 s move (s) | 102 | 73 | 69 | 69 | 61 |

Aug 22+ split (n=11 maps): upd/min 2,398, mid moves 495 — already at Sep speed.

## Findings

1. **The book churn rate doubled starting ~Aug 22 — exactly the late-third boundary.**
   [verified] Radiant-token snapshots per wall-minute during seconds 0..480: Jun 1,429 /
   Jul 1,425 / Aug 1,249 / **Sep 2,411**; splitting August at Aug 22: Aug 1–21 1,197 →
   **Aug 22+ 2,398** (n=11 maps, small but sharp) → Sep 2,411. The speed-up begins in the
   last week of August — the same window where the backtest markout collapses
   (context fact 1). Median inter-update gap 9 → 4 ms; mid moves per window 243 → 495 in
   Aug 22+. Same capture, same months: Dota Sep sits at 1,711/min — LoL-market-specific,
   not a Telonex artifact.

2. **Spreads did not tighten — they were already at the floor.** [verified] Median spread
   1 ¢ (= one tick) from July on. Sep books got faster *and shallower*, not tighter: depth
   within 2 ¢ of best fell to a median 1,732 units vs 6,483 in Jul (and below June's 2,716).
   August's high mean spread (6.2 ¢) is Hitpoint Masters microbooks entering the sample.

3. **Big gold swings get priced ~2× faster in Sep, inside our 11 s lag.** [verified]
   `event_response_lol.parquet`: conditional on reaching the threshold within 120 s, median
   times in the swing direction are +0.5 ¢ at 2.1 s, +1 ¢ at 4.7 s, +2 ¢ at 9.8 s in Sep vs
   +0.5 ¢ at ~5 s, +1 ¢ at 7–9 s, +2 ¢ at 16–21 s Jun–Aug. In Jun–Jul a state arriving at
   frame+11 s preceded the bulk of the reprice; in Sep the median +1 ¢ move is already done
   by ~5 s and the +2 ¢ move by ~10 s — our decision tick lands while/after the market
   finishes moving. On the Aug 22+ events the first-reaction metrics already sit at
   September levels (t05 median 1.2 s vs 2.5 s for Aug 1–21) while t20 stays long — the
   speed-up showed up first in churn/first-reaction, then in completion.

4. **…yet the median event hasn't moved at all by +11 s, in every month.** [verified]
   Median signed response at +11 s ≈ 0 everywhere; repricing is gradual/conditional, not an
   instant jump. So the mechanism is not "someone saw the frame 11 s before us on every
   tick"; it's that the *fast tail* of repricing grew (share of +2 ¢ moves completed inside
   11 s: 44/42/41 → 52 %; events reaching +2 ¢ at all fell 79 → 69 %) and the eventual net
   move shrank (mean resp_300s 1.9–2.5 ¢ → 1.1 ¢). The market extracts the same information
   *earlier and shallower* — exactly what a markout decay looks like for an 11 s-lagged model.

5. **Adverse excursion tripled.** [verified] Median worst-case signed move within 120 s of a
   big swing: −1.0 ¢ Jun–Aug → **−3.0 ¢ Sep**. Thin books + fast takers = whipsaw. For a
   maker holding inventory through the move, Sep's path to the same endpoint is twice as
   painful; consistent with the histfix tail-doubling (context: worst map −1,090 → −2,088).

6. **Takers start moves, makers carry them.** [verified, rough] ~69 % of first mid changes
   after an event have a radiant-token trade within ±1 s (stable 68–76 % all months —
   taker-initiated). But only 41/53/38/**32 %** of *all* mid moves have a preceding trade —
   most repricing is maker re-quoting, and Sep's maker share grew as its update rate doubled.
   Post-event taker volume actually fell (median 9.4 k units in Jul → 1.9 k in Sep), and in
   Sep nearly half of big-swing events saw *no* radiant-token trade within 60 s (52/98
   events with any trade vs ~all in Jun–Aug). Takers that do show up trade with the swing
   (directional volume share 0.59/0.61/0.63/0.56) ~1–3 s after the frame. September
   repricing is increasingly maker-pull, not taker-push.

7. **The tape is machine re-quoting all year; September just more so.** [verified] 97–98 %
   of consecutive snapshots keep the same best bid/ask prices (pure size churn at ~100+/s),
   > 97 % of inter-update gaps < 250 ms — that signature is resting-order bots resizing, not
   humans. Sep doubles the churn and raises the sub-1 s first-reaction share to events:
   28 % → 44 %; snapshots in the 3 s after an event 87 → 170.

8. **Participant counts did not grow.** [verified] Onchain fills per map window (both
   tokens): median unique makers 30.5/78/49/39.5, takers 46/187/86/47.5, fills 247/1,048/
   534/403, volume $9.7 k/$112 k/$33 k/$10.9 k. July was the peak-liquidity month (MSI+EWC);
   September is back to ~June headcount. The market didn't get more crowded — the same-size
   crowd quotes faster.

9. **League mix is a confound but doesn't explain the speed-up.** [verified] June's traded
   set is 58 % EMEA Masters; September's is more balanced and includes Hitpoint's microbooks
   (median spread 8–16 ¢, depth ~400–700, ~14 % of Sep maps). Excluding Hitpoint, Sep still
   shows upd/min 2,411 and t20 9.7 s. Within-league: EMEA Masters upd/min 1,042 → 2,165 and
   t10 10.3 s → 5.7 s Jun→Sep; LEC t20 6.9–37.4 s → 2.0 s; LCK upd/min 1,800–2,410 → 5,080.
   The speed-up is a within-market time effect, not only mix.

10. **Dota September = LoL June.** [verified] Same measurement on 60 Dota maps: t10 11.0 s,
    t20 19.5 s, resp_300s median +3.0 ¢, spread 2 ¢, upd/min 1,711. LoL books today reprice
    ~2× faster than Dota books on the same kind of state shock — and LoL Jun–Jul was at
    Dota's current speed. This is consistent with Dota's late-third edge *growing* (5.5 ¢
    markout) while LoL's collapsed: on Dota the 10 s GRID lag still precedes the reprice;
    on LoL it no longer does. (LoL's own lag constant is 11 s; Dota's 10 s.)

11. **Realized spread on taker flow rose ~50 % in Sep.** [verified] Per radiant-token trade
    in window, signed (mid after 30 s − mid at trade) in taker direction: mean 0.33/0.15/
    0.37/**0.56 ¢**; effective half-spread 0.63/0.57/0.71/**0.80 ¢** (median 0.5 ¢ = half
    tick everywhere). Dota Sep: 0.79 ¢ realized on far fewer trades. Takers in Sep LoL are
    more informed per trade — more adverse selection against anyone resting liquidity,
    including us.

## What I ruled out

- **Tighter spread as the channel** — spread hit the 1 ¢ tick floor in July and cannot
  compress further; the degradation is speed + depth, not spread.
- **Deeper books** — opposite: Sep has the thinnest top-of-book of the four months.
- **More participants** — unique makers/takers per map did not grow Jun→Sep; fill counts and
  event-window volume fell vs July.
- **Instant front-run of the frame stamp** — median signed move at +11 s is 0 in every
  month; even in Sep the market does not jump on the median big swing. What changed is the
  conditional reprice speed and the whipsaw.
- **Telonex capture artifact** — same capture shows no Jun→Sep doubling on Dota books.
- **Pure league-mix story** — within-league comparisons (EMEA, LEC, LCK) all show Sep faster.
  Note league mix *does* matter for level: July's majors (MSI/EWC) were deep and active, and
  Hitpoint microbooks drag Sep pooled medians — direction confirmed, magnitude has mix noise.

## What this implies for the 11 s maker signal

- The edge window is the gap between frame stamp and market repricing. Sep LoL reprice
  quantiles (t05 2.1 s, t10 4.7 s, t20 9.8 s) mean a signal delivered at +11 s arrives
  after ~half the move on events that move at all; to catch the median +1 ¢ reprice before
  it completes, state→decision must be ≲ **3–5 s**; ≲ 7 s still beats the median +2 ¢
  completion. Current chain: `LOL_SOURCE_LAG_SECONDS = 11` (GRID series_table → us), plus
  signal cadence on top.
- No current LoL source meets 3–5 s (GRID scoreboard push ~7 s, lolesports livestats ~55 s,
  streams 60 s+ — context fact 2). If leadlag-dev finds nothing faster, the structural
  answer is trading only the markets where the window survives — i.e., filter by measured
  book speed, not league name.

## Proposed experiments

1. **Faster-mid bound on the markout (no retrain).** Recompute the per-tick signal delta
   with the "current mid" sampled at frame+{3, 5, 7} s instead of +11 s on the existing
   validation tape, recompute which ticks fire |model−mid| ≥ 0.02 and their 300 s markout,
   split by period. Cheap pandas job on `validation.parquet` + telonex books (score the
   published ensemble on the ≤540 s rows; replace the +11 s mid lookup). Expected if speed
   is the mechanism: late-third (Aug 22–Sep 29) buy markout recovers toward ≥1.5 ¢ at +3–5 s.
   Number to watch: late-third buy-300 s markout, not the total.
2. **Backtest with `LOL_SOURCE_LAG_SECONDS`→3** (a "perfect feed" bound; still no retrain —
   the model input mid is just earlier). Command: edit `src/shared/constants/lol.py:52`,
   rerun the histfix-config backtest, compare late third vs 0.44–0.63 ¢ markout. If it
   recovers ≳1.5 ¢, feed lag is the binding constraint and nothing in the model will fix it.
3. **Book-speed league filter.** Compute per-league median t10/upd-rate from my parquets
   (already joinable via `league` column), whitelist only leagues with median t10 > ~8 s
   (Sep measurement: EMEA Masters, CBLOL, LRN/LPLOL/EBL tier — *not* LCK/LEC/LCS), rerun the
   LIVE-config backtest on that subset. Expected: late-third markout back toward ~1.5–2 ¢ on
   a smaller map count; quantifies how much of the decay is top-league speed.
4. **Pause/thin-book guard.** Given depth2c med 1.7 k and adverse excursion −3 ¢ in Sep:
   skip entries when depth within 2 ¢ < X (sweep X ∈ {1 k, 2 k, 5 k}) — expected: cuts the
   worst-tail maps; watch CVaR5 and worst-map on the histfix late third.

## Scripts

All under `work/decay-dev/`, run from the esports-trader root with
`PYTHONPATH=src:scripts uv run python <script>` (nice -n 10):

- `build_sample.py` → `sample_maps.parquet`, `traded_population.parquet` (population =
  LIVE seed0 results match_ids; league via universe + `lol_league_whitelist.json` aliases).
- `measure_maps.py` → `per_map_metrics.parquet` (241 maps × spread/depth/upd-rate/churn/
  trade stats) + `events.parquet` (423 big-swing events, half-life/first-change/trades).
- `measure_dota.py` → `dota_map_metrics.parquet`, `dota_events.parquet` (60 Dota Sep maps,
  same metrics).
- `event_response.py <lol|dota>` → `event_response_{lol,dota}.parquet` (signed pair-mid
  response at +3/6/11/20/30/60/120/300 s, time-to-{0.5,1,2}¢, excursions).
- `count_participants.py` → `participants.parquet` (unique maker/taker addresses, fills,
  volume per map from onchain_fills).
- `realized_spread.py <lol|dota>` → `realized_{lol,dota}.parquet` (per-trade effective and
  30 s realized half-spread in cents).
- Logs: `measure_maps.log`, `measure_dota.log`, `event_response_{lol,dota}.log`,
  `count_participants.log`, `realized_{lol,dota}.log`.
