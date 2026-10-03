# leadlag-dev: external research — LoL data latency landscape vs Dota, and who trades Polymarket LoL
Status: FINAL

## Verdict (≤10 lines)

The LoL information landscape changed structurally inside our validation window: GRID became Polymarket's exclusive data partner (2026-06-21) and put the zero-delay widget (scoreboard ~7 s, table ~8 s) on every covered event page; GRID also sells server-level feeds advertised at sub-200 ms and, since 2026-09-10, a finished odds product (GRID Odds). Polymarket's official esports market maker (Sportstensor/Almanac) is contractually fed by GRID and publicly says esports odds are "soft". Meanwhile our fastest input is the same public widget everyone on the page sees — we have no informational lead in LoL; the 4–5 s death→mid reaction we measured is consistent with licensed-GRID-class participants. Dota is different: our GRID scoreboard feed is ~1.4 s on kills and tournament video/DotaTV is 2–15 min behind, so our relative position vs the crowd is much better. No purchasable feed gives a durable lead in LoL except a licensed GRID feed (custom-priced sportsbook tier, i.e. buying the same pipe the official MM uses); LPL is the one league where nobody — Polymarket widget, our socket, likely GRID itself — has fast data.

## Findings

### A. The LoL information stack in Aug–Oct 2026 (source → carries → latency vs real game time → access)

| Source | What it carries | Latency vs real game time | Access |
|---|---|---|---|
| GRID `widgets-v2/live` socket `?delay=zero` | scoreboard (clock, kills, side) + `series_table` (gold, XP, drakes, towers); "integrity-safe" subset only — some events withheld | scoreboard ~7 s (measured 2026-08-28, `lol-grid-widget.md`); table declared ~8 s | **free, public** — same socket Polymarket embeds on covered event pages (verified) |
| GRID widget `delay="public"` | same widgets | "broadcast speed" — aligned to the ~60 s official stream | free (GRID docs, verified) |
| GRID licensed Live Data / Series Events API | full event stream + series state | advertised "sub-200 ms … direct from the game server" (grid.gg/plans) | commercial license, sportsbook/prediction tier, custom pricing (verified) |
| GRID Odds (launched 2026-09-10) | finished odds: match/map winner, handicaps, totals, props, in-round micros | priced off the <200 ms pipe | commercial; esports.net + LinkedIn announcement (verified) |
| Riot `feed.lolesports.com/livestats` window/details | full stats: gold, kills, CS, HP, items, dragons/barons; 10 s frames | embargo: windows younger than ~60 s return 400 (measured on LEC 2026-08-28). **Worse on minor leagues**: third-party proxy sample showed `lagSeconds: 193` on an HRTS match (citoapi.com docs, 2026-07-14); my probe on 2026-10-03 (Demacia Cup GI) returned a dead feed — only 10 init frames, 0 kills, ~20 min stale | free, public, shared public key (verified) |
| `rfc460Timestamp` in livestats frames | frame capture (server) wall time = real game time, ms precision | ≈ real event time, NOT broadcast-shifted — else the mid's 4–5 s post-stamp reaction would appear ~50 s *before* the stamp in our probe (likely — inferred, no public doc defines it) | — |
| Official broadcasts | video | LEC 60 s (`getLive` offset −60000, measured); afreecatv on DCGI 80 s (measured today) | free |
| LPL streams | video | no official English broadcast at all in 2026 (community casts LPL_English/Nymaera restream); Chinese platforms (huya/bilibili/douyu) add their own delay, ~60 s+ typical | free (likely) |
| In-client spectator | game state | 180 s on Summoner's Rift; tournament realm custom (LoL wiki, verified) | client |
| PandaScore Low Latency Feed | timer, kills, towers, inhibs, dragons, barons, draft. **LoL: no gold, no XP** (our `.learnings/pandascore-fast-feed-20260928.md`) | "<5 s from event" claim | €4 000/mo per title, internal use only, TO can impose delay (verified) |
| gol.gg | post-match stats — **Polymarket's LoL resolution source** (verified on `lol-bro2-jdg-2026-10-03`, 2 h fallback) | hours | free web |

### B. Dota 2 contrast

| Source | Carries | Latency | Access |
|---|---|---|---|
| GRID widget/licensed (via TO partnerships: PGL, ESL/EFG, BLAST) | kills, net worth, XP, structures | scoreboard median **1.35 s** on kills, table ~9.65 s (live measurement, `.learnings/pandascore-fast-feed-20260928.md`) | same as LoL — but GRID has no exclusivity for Dota; coverage per-TO |
| Oddin | full state ~1 Hz | ~15 s (measured) | licensed |
| Steam GetTopLiveGame | sparse scoreboard snapshots | ~25–30 s behind PGL in our captures (`.learnings/pgl-source-delay-20260919.md`) | free |
| DotaTV | full spectate | organizer-set: 10 s/2 min/15 min lobby options (15 min option exists as a lobby flag, hawk.live patch note; typical pro-coverage delay 2–15 min); community-cast rules can demand extra delay | client |
| Twitch official streams | video | ~2–5+ min typical for tier-1 (5–10 min documented across industry, Abios/gameofnerds) | free |

Key asymmetry: in Dota our primary feed (GRID scoreboard) arrives ~1.4 s after kills — faster than almost every other public participant and far ahead of DotaTV/streams. In LoL our feed is the *same* public widget everyone has, while a licensed class (Sportstensor, GRID Odds customers) is contractually ahead of it.

### C. Who trades Polymarket LoL

- **Official MM exists**: Sportstensor is "the official market maker and liquidity provider for Polymarket"; since 2025-11-07 GRID is its "official esports data oracle" for CS2/Dota2/LoL/Valorant — server-level data (dotesports.com, esports.gg). CEO Leo Chan (taopill.ai interview): esports "probabilities and odds are soft, not many traditional sportsbooks know or even care to price them properly." Miners' signals are routed to Polymarket books via Almanac (beta 2025-12-01), API-first. (verified)
- **Scalper bots are public knowledge**: TeemuTeemuTeemu turned $900→$208k in ~3 months (≈Oct–Dec 2025) scalping LoL/Dota2/CS kills faster than the market; coverage notes "many users are now attempting to build or copy similar systems" (esports.net 2025-12-30, Kotaku). Copycat cohort started ~Jan 2026. (verified)
- **On-chain evidence of bot cohort**: Vultax (2026-09-06 study, window 2026-08-21..09-12): on 6 LPL/LCK playoff markets, "round-the-clock wallets" = 7.0 % of wallets, **24.6 % of trades, 13 % of notional** — the same cohort is only 2.6 %/19.8 % on Fed contracts; bots are over-represented on LoL. (verified)
- **Book structure**: median LoL book depth within 2 ¢ pre-match = **$413** (29 % of readings empty); Dota 2 = $24, 44 % empty; per-map markets "median spreads 41–97 ¢"; vs NFL $523K. "Esports prices are set by a broad crowd trading against a few thin resting orders." Largest wallet = 25 % of a typical LoL market's money. (verified)
- Volume: LoL = $92M traded 08-21..09-12 — the single biggest title on Polymarket in the window; record day 2026-09-12 $15.8M; playoff matches (iG–TES, Gen.G–HLE g3) were the top-5 markets on the whole platform on 9/6. (verified)

### D. Dated changes on the Polymarket LoL side (context + Aug–Sep 2026)

| Date | Change |
|---|---|
| 2025-09-14 | Sportstensor × Polymarket strategic partnership (official MM role formalized; Polymarket approached them after seeing their volume) |
| 2025-11-07 | GRID × Sportstensor — GRID becomes official data oracle of the MM |
| 2025-12-01 | Almanac (Sportstensor terminal on Polymarket books) public beta |
| 2025-12-29 | TeemuTeemuTeemu story breaks → bot copycat wave |
| 2026-01-27 | Polymarket US Market Maker Program effective (CFTC filing 2026-01-12) |
| 2026-03-17 | Polymarket US Liquidity Provider Program effective — weekly stipend for continuous two-sided quotes "particularly suited for newer or less liquid markets" (CFTC filing 2026-03-03) |
| 2026-06-21/23 | **GRID × Polymarket exclusive partnership live**: `delay=zero` widgets + faster-than-public streams on event pages, esports hub, "massive rewards". In-window LoL props already existed (~23 mkts/event avg in June → ~27 by Sep, my Gamma API pull of 1,400 events) |
| 2026-07-01 | Polymarket US exchange fee schedule live (taker θ=0.06, maker rebate −0.0125) |
| 2026-07-10 | **Sports taker-fee coefficient 0.03→0.05 (+67 %), maker rebate 25 %→15 %, ~3 h notice** (changelog via thecoinformer/eventtradinghub; docs.polymarket.com/trading/fees — esports has no own row, falls under Sports: likely) |
| 2026-07 | 0.25 ¢ tick rollout begins; esports volume record $790M (+33 % MoM) |
| 2026-08 | LoL turnover $245.9M across 6,478 contracts, avg $7.9M/day (cryptostruct; their July capture is partial so Aug is the first full month); Sep to 9/24 = $137.7M, $5.7M/day — daily turnover already cooling inside the late third; Polymarket US parlay beta |
| 2026-08-15+ | third-party full-depth LoL book capture products appear (marketlens.trade, cryptostruct) — retail analytics industrialized |
| **2026-09-10** | **GRID Odds launches for LoL** (and CS2/Dota2/Valorant): turnkey priced live markets off <200 ms server data |
| 2026-09-13 | Vultax reads Polymarket liquidity rewards: esports gets 0.6 % of $150k/day (~$900/day) — LoL makers are NOT paid a meaningful LIP subsidy; income = spread + 15 % taker-fee rebate |
| 2026-10-01 | polymarket.us/rewards lists esports LIP pools: Moneyline Live $115/day/137 mkts, Pre-Game $20, Props Live $4, Props Pre $2 (verified) |
| 2026-10-15 | Worlds 2026 starts (Play-Ins, LA) — **after** the validation window; the late-third collapse is not a Worlds effect |

**How this reads against our thirds**: early third (Jun 4–Jul 30) — GRID deal lands Jun 21, widget live but adoption ramping; edge holds. Middle (Jul 30–Aug 22) — record volume +67 % taker fee hike Jul 10 (takers pay ~2.5 ¢ round trip at 50 ¢ → only better-informed flow pays it; rebate cut to 15 %) — edge still positive. Late (Aug 22–Sep 29) — playoff season (LCK Aug 26–Sep 13, LPL, LEC finals Sep 20) with the biggest per-match attention of the year, GRID Odds live Sep 10, Almanac/MM infra matured for 9 months — markout → 0.4–0.6 ¢. Volume didn't leave (Aug was the biggest LoL month ever); the *counterparties* got faster. (likely — the mechanism, not a single dated cause)

### E. GRID coverage is per-league — LPL is the gap (verified, my own Gamma pulls)

`eventMetadata` on closed events (sampled 2026-10-03): LCK playoffs (4 events), LEC, LCS, EMEA Masters → `gridSeriesId` + `pandascoreMatchId` (62–73 markets per event). **LPL (3 playoff events checked), CBLOL, Circuito Desafiante, WSCI, Demacia Cup GI → pandascoreMatchId only, no GRID widget.** Matches our `no_live_feed:["LPL","LCK Challengers League"]` (config/lol_league_whitelist.json). So on LPL maps neither the page users nor we have a sub-10 s public feed — the residual LoL edge, if any, is likeliest on LPL (and other pandascore-only leagues). Prediction for tape analysis: LPL buy-300s markout should decay less than LCK/LEC.

### F. Broadcast/delay policy by league (as far as public sources go)

- LCK 2026: Korean broadcast rights exclusive to CHZZK + SOOP (5-yr deal signed Dec 2025; YouTube dropped). GRID real-time stats (win probability, match timeline) rendered *on the broadcast* — official data on-air. LoL Park audience sees play essentially live; arena broadcast ~tens of seconds. (verified: esports.gg, mk.co.kr, digitaltoday)
- LEC: 60 s stream delay (our measured offset; verified)
- LPL: no official English broadcast in 2026 — only community casts (LPL_English, Nymaera) restreaming Chinese feeds; TJ Sports (Tencent/Riot JV) runs production; delay ~60 s+ platform-dependent (likely)
- LTA/LCS (Americas): GRID-backed pages; delay ~60 s class (likely)
- Worlds 2026: Oct 15–Nov 14 US; not yet running. In-client spectator = 180 s baseline on live realm; tournament realm sets its own delay (verified: LoL wiki)

## What I ruled out / checked and found nothing

- **rfc460Timestamp**: no public doc defines whether it's game time or broadcast time; my inference (≈real event time — else the 4–5 s market reaction would show up as ~−50 s in the probe) is `likely`, not `verified`. Worth confirming against one GRID-vs-livestats archived pair (data task, not web).
- No public evidence that Polymarket's embedded widget has *lower* delay than the public socket — it IS the public socket (`?delay=zero`, measured ~7 s); Polymarket chose the integrity-safe zero-delay mode, which is already the fastest public product GRID offers.
- PandaScore LLF for LoL: confirmed via their docs it exists for LoL, but it carries no gold/XP — cannot replicate our feature set regardless of its <5 s delay.
- No evidence of a dedicated Polymarket esports MM stipend beyond the tiny LIP pools (~$900/day all esports) — i.e. Sportstensor monetizes via Almanac flow/spread, not a subsidy we'd also qualify for.
- No fee/API break specific to Aug–Sep that alone explains the cliff: the big fee change was **July 10** (before the middle third); the cleanest Sept-dated structural event is GRID Odds (Sep 10) plus the steadily compounding GRID/Sportstensor/Almanac machine.
- Minor leagues: livestats on the live Demacia Cup GI game was a dead 10-frame stub (probe today) — for tier-3/4 coverage the official feed itself is unreliable, consistent with `audit.parquet` incomplete-map counts being a data problem, not a market problem.

## Proposed experiments

1. **Lead/lag by league × period on the tape** (no retrain): on validation `results.parquet`, join per-map league from `split.parquet`; compute buy-300s markout per league × period-third. Prediction: LPL/CBLOL/tier-4 markout decays less than LCK/LEC/LCS. If confirmed → weight the whitelist toward pandascore-only leagues or drop GRID-covered majors where we are structurally last. Cost: one pandas pass over existing parquets.
2. **Measure the real margin of defeat**: replay archived livestats vs Telonex book mid around every kill (existing archive). Distribution of (mid_move_time − frame_stamp) per league. If LCK/LEC mode ≈4–6 s and LPL ≈10–30 s → the market prices off GRID where it exists and off slower sources for LPL — confirms the league split. (data task, ~1 day)
3. **Trial the GRID licensed feed** (the only lead-giving option): `grid.gg` sportsbook/prediction plan — Series Events API at sub-200 ms. Cost: custom pricing, likely material; ask sales. Confirm: does the license cover LPL? If yes it's the only LoL edge feed in existence; if no, LPL alpha must come from elsewhere (or nowhere). Alternatively ask whether Polymarket's own deal leaks faster data to any public channel — unlikely.
4. **GRID Odds as prior, not signal**: the new GRID Odds product outputs priced probabilities; if a cheap REST tier exists it could replace our market prior features — worth a pricing email only, not a build.
5. **Watch Worlds**: Worlds (Oct 15+) is the first GRID-covered event with maximal volume. Live-compare markout on Worlds maps vs Sept LCK maps for evidence the crowd/edge tightened again (monitor `data/backtests/lol_maker` live fills, no new infra).

## Scripts / artifacts

- `work/leadlag-dev/NOTES.md` — file inventory + per-league GRID coverage results
- `work/leadlag-dev/lol_events_props.json` — 1,400 closed LoL events: `[endDate, slug, n_markets, n_props]` (Gamma API pull)
- `work/leadlag-dev/ev.json`, `getlive.json`, `sched.json`, `window_sample.json` — live API captures (Gamma event, lolesports getLive/schedule, livestats window)
- Rerun per-league coverage: Gamma `events?slug=<slug>` → `eventMetadata.gridSeriesId|pandascoreMatchId|leagueTier` (User-Agent required or 403)
- Rerun livestats probe: `feed.lolesports.com/livestats/v1/window/{esportsGameId}?startingDate=<RFC3339>` — needs a real live game (none today except a dead DCGI feed)
- agent-browser session `leadlag-dev`: docs.grid.gg Widgets → "Data Source and Delay" page (delay `zero`|`public`, integrity-safe widget list)
