# game-devin — game state as a signal for the maker
Status: FINAL

## Answer

- **The book moves before our feed tells us why.** When the feed delivers the event behind a mid move, a median ~2–2.5 ticks of the ~2.5–4-tick move is already done (~70–80%). Oddin lag is a constant 15.0 s (measured, `lastUpdatedAt`→receive); GRID board kills arrive ~6–9 s after the mid starts moving (stated delay 8 s). Game state cannot front-run the first leg of a move. Verified.
- **But kills cluster into fights, and the drift continues after the alert.** 77% of victim-side events are followed by another victim-side event within 30 s. After a GRID board-kill the mid keeps drifting in event direction: mean +1.7 ticks/30 s (median +1.0). That is the window where our maker keeps bidding for the hurt side. Verified.
- **Fills on the just-hurt side are the toxic cluster.** 523 maker BUYs, 120 s markout total −$87.5. The 266 fills made ≤30 s after a victim-side feed event sum −$98; the other 257 fills net **+$10.7**. On settlement value (360 GRID fills): fills inside the victim window −$141 vs +$52 outside. Verified.
- **A kill gate is the actionable rule.** Wallet A already has one (`src/strategy/kill_gate.py`: buy-block the victim token while its table deaths lag the board kill, hold ≤10 s); `two_sided_quoting.py` never calls it — B bids through teamfights blind (`model_evaluated:false` on every signal). Blocking the victim token while its last delivered kill is <20 s old removes 187 fills with −$86 markout and keeps the rest ≈ flat (−$0.003/fill). First-order saving ≈ $6–9/map. Verified (markout), likely (net PnL).
- **~30% of adverse fills fire before the event arrives.** 151 fills where the causative kill reached us only after the fill (median +9 s) carry −$18. No game signal helps those; a book-momentum gate does: blocking fills while the book moved ≥0.5 tick against the side in 10 s covers most of the residual. Union rule (kill ≤15 s OR book move against) keeps 140 fills at +$25.9 and blocks −$113. Verified.
- **The Dota model does not fix this.** Reconstructed offline on the session tapes it predicts the 300 s mid fine on all signals (corr 0.25), but at fill times corr(model delta, fill markout) ≈ 0.02 — it cannot tell toxic fills from good ones at the moment we get filled. Verified (with reconstruction caveat below).
- **Losses concentrate late.** Fills at game second <300: +$2.6 total. gs>1800: −$66 of −$87. Pre-horn/early quoting is safe as-is; the gate matters from mid-game on. Verified.

## Method

Data: `work/data/trader_live_b/<match>/` session.jsonl (signals + `game_snapshot`, fills, quotes), match.json (teams, token ids, `yes_is_radiant`, horn, `grid_delay_s`/`oddin_delay_s`), raw `grid_state.jsonl` / `oddin_state.jsonl`; book tape `book_journal_20261008_liveb.jsonl.gz` (3.7 M market events, `receivedAtUs` µs). All scripts in `work/game-devin/`:

- `extract.py` — session rows → per-map `signals/fills/quotes.parquet`; one streaming pass over the book journal → `book.parquet` (every `price_change`/`book` top-of-book for both tokens) and `trades.parquet`.
- `build_mid.py` — p_radiant mid series replicating `book_p_radiant` (radiant mid normalised by pair sum, tol 0.02; `src/strategy/signals.py:31`), clipped to session ±180 s; move detector: |p(t+10 s)−p(t)| ≥ 0.03, 10 s cooldown → 1,119 moves.
- `parse_feeds.py` — GRID: reuses `trader.grid_widgets.parse_frame/read_map_scoreboard/read_net_worth` → `feed_board.parquet` (scoreboard kills+clock, team ids) and `feed_table.parquet` (per-team deaths/kills/NW). Oddin: snapshot fields → `feed_oddin.parquet` (kills, towers, roshans, barracks, per-player alive/deaths/respawn, faction→side direct).
- `build_events.py` — event stream per map: `board_kill` (GRID), `kills`/`alive_drop`/`deaths`/`tower`/`roshan` (Oddin), `table_death` (GRID confirmation), `nw_jump` (GRID |Δnw_adv|≥800 — proxy for objectives; GRID's `Structures destroyed` is victim-side and missing on 3 maps).
- `event_anchor.py` / `leadlag.py` — for each event: mid already moved (pre, 20 s) vs still to move (post 5/15/30 s), signed to the beneficiary side; move-onset lag = delivery − first 1-tick departure.
- `regime.py` — 30,332 decision points (every signal): P(|Δp|≥3 t in next 5/15/30 s) vs base rate under feed/book flags.
- `markout.py` — per fill: token mid at T/+30 s/+120 s → markout $; victim/beneficiary event ages; game second; paused. (`fill_markouts.parquet`)
- `model_eval.py` — offline run of the actual live models `archive/production/20261005T081741Z` (GRID, 81 cols) and `archive/production-noxp/20261005T081914Z` (Oddin, 70 cols): `SnapshotHistory` tape replay over received signals, `BoardHistory` replay (record_board kills, record_table deaths), prior = p_radiant at horn−85 s, market features at fill time. Predict = mean member delta.

Assumptions/caveats:
- Markout ≠ realised PnL: a filled share can still pair and merge at $1, or sit unpaired to settlement. 120 s markout overstates per-share loss for later-paired fills; settlement markout (only on the 6 GRID maps with known winner) is the harder number. Both are reported; the gate conclusion is the same under both.
- Counterfactual "gate on" = fill would not have happened (resting bid pulled <1 s after the event, no new bid). Blocked fills may be partly re-filled later at different prices; the kept-group mean is the estimate. Rebate foregone on blocked fills ≈ $6/day (rebate = 0.15·0.05·size·p(1−p), ≈ $0.03/fill; `src/shared/utils/trading.py:48`).
- Model reconstruction: features rebuilt from the received signal tape exactly like the live path (`model_server.py`), but snapshot staleness up to a few seconds and board/table replay approximations may shave a little edge; the ~0 fill-time correlation is also what a slower game-blind feature would show.

## Results

### Feed lag and move anatomy (per event, signed to beneficiary side)

| feed | event | n | pre-move med (ticks) | post-30 med | post-30 mean | lag p25/p50/p75 (s, fresh only) |
|---|---|---|---|---|---|---|
| grid | board_kill | 384 (120 fresh) | 1.0 | +1.0 | +1.7 | 6.0 / 19.2 / 25.9 |
| grid | table_death | 381 (33 fresh) | 2.5 | 0.0 | +0.2 | 9.5 / 14.5 / 24.0 |
| grid | tower (victim) | 66 | 2.5 | +0.5 | +0.6 | 11.6 / 20.7 / 22.5 |
| oddin | kills | 240 (70 fresh) | 3.0 | +0.5 | +1.0 | 12.8 / 15.0 / 25.4 |
| oddin | alive_drop | 249 (75 fresh) | 2.5 | +0.5 | +1.0 | 12.5 / 15.1 / 25.4 |
| oddin | tower | 49 (17 fresh) | 0.5 | +0.5 | +1.8 | 14.1 / 20.8 / 24.3 |

Lag = feed delivery − first 1-tick mid departure (30 s look-back). Fresh = no directional event in prior 25 s; for chained events the onset proxies the whole cascade so medians stretch to ~20–28 s. Oddin p50 = 15.0 s = its stated delay; GRID board p25 = 6 s ≈ 8 s delay minus book reaction — the book is already moving when our earliest channel fires. `event_anchor.parquet`, `fresh_anchor.parquet`.

Book leads the feed on every event type; the post-delivery residual is ~0.5–1.7 ticks — too thin to take (taker fee ≈1.25 t at p=0.5) but exactly the slice where our bids get run over.

### Regime signal (30,332 decision points; base rate = P(|Δp|≥3 t in horizon))

| flag | cover | P(5 s) lift | P(15 s) lift | P(30 s) lift |
|---|---|---|---|---|
| (base rate) | 100% | 0.151 | 0.344 | 0.513 |
| any feed ev ≤10 s | 33.5% | ×1.27 | ×1.14 | ×1.08 |
| victim ev ≤10 s | 23.2% | ×1.37 | ×1.21 | ×1.10 |
| book \|dp10\|≥1 t | 47.4% | ×1.53 | ×1.39 | ×1.30 |
| victim ev ≤10 s AND \|dp10\|≥1 t | 17.9% | ×1.87 | ×1.55 | ×1.41 |

Cascade confirmation: P(≥1 more victim-side event delivered ≤30 s | one just arrived) = 76.5% GRID, 78.7% Oddin (65–70% within 10 s). The feed's unique contribution is *direction and continuation*, not the move's start.

### Fill markouts (523 maker BUYs, 10 shares each; $ = (mid−price)·size)

| split | n | 30 s sum | 120 s sum | settle sum (360 GRID fills) |
|---|---|---|---|---|
| all fills | 523 | −$50.7 | −$87.5 | −$89.1 |
| victim ev ≤30 s | 266 | −$50.9 | −$98.1 | −$141.3 |
| no victim ev ≤30 s | 257 | +$0.3 | +$10.7 | +$52.2 |
| victim ev ≤5 s | 128 | — | −$81.7 (mean −0.64/fill) | — |
| event arrives 0–30 s AFTER fill | 151 | — | −$17.8 | −$44.8 |
| no victim ev ±30 s | 115 | — | +$31.4 | +$12.8 |
| beneficiary ev ≤30 s | 254 | — | −$25.9 | −$58.6 |

Worst fill-time toxicity: victim_age 0–5 s (−$0.67/fill) and 15–20 s (−$0.76/fill); fills >90 s after the last victim event are profitable (+$0.09–0.14/fill) — the damage is the chase, not dip-buying in general.

Book momentum at fill time (signed to the bought side): fills while the book already moved ≥2 t *against* the side in the prior 10 s: −$0.24/fill (n=252); fills while it moved ≥2 t *for* the side: −$0.24/fill (n=127, fills get lifted on retrace or repriced then revert); near-zero momentum (|dp10|<0.5 t, n=30): **+$0.32/fill**. Fast book in either direction = toxic fills.

### Gate counterfactuals (avoided fill markout, 120 s)

| rule | blocked | avoided sum | kept n / mean |
|---|---|---|---|
| kill events only, W=20 s | 187 | −$86.3 | 336 / −0.00 |
| any victim event, W=30 s | 266 | −$98.1 | 257 / +0.04 |
| kill ≤15 s OR \|dp10\|≥0.5 t against | 383 | −$113.4 | 140 / +0.19 |
| victim30 OR model Δ<−1 t | 286 | −$74.1 | 237 / −0.06 |

Per-map (kill gate W=20): grid-3011821-m1 −$46.1 avoided, grid-3011820-m2 −$28.5, 9034957701 −$12.6, 9034789047 −$7.8; two maps where it blocks +$2.6/+5.3 of *good* dip fills. Savings concentrate exactly where the day's losses were.

**Widen ≠ pull.** For the 187 gated fills, would a victim bid 2 ticks lower still have been hit (token mid dips ≤ price−0.02 within 30 s)? 64% yes — and those still-hit fills carry −$126 while the dodged third was +$40 of good dips. A wider bid keeps exactly the deepest cascades. Pull the leg, don't shade it.

### Model evaluation (offline, fill-time features)

- All-signal correlation delta vs realised 300 s move: 0.254 (grid m1), 0.245 (oddin) — reconstruction works.
- Fill-time only: corr = −0.03 (grid), 0.16 (oddin); corr(delta·side-sign, markout/share) = 0.015. Model veto alone at τ=0.01 keeps −$61.7 of −$87.5 markout — no discrimination; it adds little over the kill gate (`model_fills.parquet`).

### Game-time concentration (fill markout by game second)

| gs | n | 120 s sum | settle |
|---|---|---|---|
| <0 (pre-horn) | 2 | +0.8 | 0 |
| 0–900 | 61 | +0.5 | −9.9 |
| 900–1500 | 138 | −4.8 | −16.1 |
| 1500–2400 | 99 | −26.1 | −36.4 |
| >2400 | 142 | −37.4 | −21.1 |

Pauses contributed zero fills.

## Recommendations (ranked by expected $/map)

1. **Kill gate on the victim leg (≈ +$4–9/map, high confidence).** When a kill arrives whose victim is side S, cancel and do not re-place the bid on S's token until S's table deaths confirm it or ~15–20 s pass with no further victim-side events (re-arm per event, not per first event — cascades last 20–60 s). Pull, don't widen — 64% of gated fills would still hit a 2-tick-lower bid and they are the worst tail. Reuses the existing `KillTick` machinery (`src/trader/grid_feed.py:311`, `src/strategy/kill_gate.py`); Oddin needs the same gate on snapshot kills/alive drops. Avoided markout −$86 to −$98/day vs ~$6 rebate and ~$10 of good dip-fills foregone. Note GRID board kills already lead table deaths by ~8 s — gating on board kills (not table) is what catches the second half of fights.
2. **Book-momentum pull for the pre-event slice (≈ +$2–5/map, medium confidence).** ~$18/day is lost on fills that fire 0–30 s *before* the event arrives — no game feed can see them. Pull both bids (or widen ≥2 t) while |Δmid|≥0.5–1 t/10 s; union with the kill gate blocked −$113 of −$87 markout in-sample. This overlaps sim-devin/sim-grok's replay brief — verify there before shipping.
3. **Keep early game unchanged.** gs<300 fills were +$2.6 — the gate costs nothing there but also buys nothing; simplest is to leave the gate armed always.
4. **Skip model-driven skew.** The model has no fill-time edge over the book (corr ≈0.02); using it to pick which side to shade replicates the victim flag with extra machinery at best.

## Refuted / open questions

- *"Game state predicts the move."* Refuted for the first leg: 70–80% of the move is priced before our feed delivers the event (feed delay ≥ book reaction). It does predict continuation (77% fight clustering) — useful for gating, not for entry.
- *"The model sees what the book misses at fill time."* Refuted at fill-time granularity (corr 0.02); it may still help as a slower regime skew — that needs a full quote replay (sim agents' brief), not fill sampling.
- *"Roshan/towers are missed signals."* Towers help marginally (Oddin tower post-30 +1.8 t mean, n=49; roshan n=12, slightly negative); GRID roshan is not in the feed at all.
- Open: does the gate hurt pair economics (blocked first leg → later second leg repriced)? Markout says the skipped leg was the losing one anyway; a replay sim should confirm net $/map including merges.
- Open: `nw_jump` (≥800 adv) proxy for GRID objectives is noisy — GRID lacks tower/roshan fields, so objective events on GRID rely on kills+NW swings.
