# Brief: leadlag-dev — external research: LoL data latency, broadcast delay, and who trades Polymarket LoL

Question: our live LoL signal is GRID `series_table` at ~11 s after the livestats frame stamp; `docs/experiments/lol-grid-widget.md` measured GRID scoreboard ~7 s behind wall, livestats ~55 s, LEC video 60 s, and a market reaction peaking 4–5 s after the frame stamp. Establish, from external sources, what the real-time information landscape for LoL esports is in Aug–Oct 2026 and how it differs from Dota 2:

1. Riot broadcast delay policy per league in 2026 (LCK, LPL, LEC, LCS/LTA, Worlds 2026 — Worlds started around late September; dates and format). Is there an in-client spectator delay? Are there low-delay/zero-delay data products (GRID, Bayes/Esports Charts, Oracle's Elixir, PandaScore live, Abios, Riot's own `feed.lolesports.com` window/details API)? Published latencies. URLs + dates.
2. GRID for LoL: documented delay options for `widgets-v2/live` and the data API (`?delay=zero`, `publishDelay`), licensing tiers, whether Polymarket's event page embeds a GRID widget with lower delay than the API we poll. Where does Polymarket get its LoL resolution/score data.
3. Dota 2 for contrast: GRID/Steam GSI/DotaTV delays (DotaTV is typically 2–5 min at LANs), what the esports-trader repo assumes (`docs/experiments/pgl-source-delay-20260919` style notes exist under `betting_workspace/.learnings/`; read them).
4. Polymarket LoL markets: evidence of market-making bots or dedicated esports MMs in 2026 (Polymarket docs, Discord/forums/X posts, Dune dashboards, GitHub repos for esports bots), their reaction latencies, and anything that changed in Aug–Sep 2026 (new MM program, fee/rebate change, liquidity rewards for esports, API changes). Also: Polymarket maker rebate schedule for esports in Sep 2026.
5. Community reverse-engineering of lolesports livestats (`livestats/v1/window`, `details`) timing: is the `rfc460Timestamp` the real game time or the broadcast time? Known offsets.

Method: `agent-browser --session leadlag-dev` for pages that need JS; otherwise curl/fetch. Save page extracts under `work/leadlag-dev/`. No logins, no signups.

Deliver: a table "source → what it carries → latency vs real game time → who can access it", for LoL and Dota, with citations; a dated list of anything that changed on the Polymarket LoL side in Aug–Sep 2026; and a verdict on whether a feed exists that would give us a real lead in LoL (with cost/terms if public). Mark each line verified (URL quoted) / likely / speculative.
