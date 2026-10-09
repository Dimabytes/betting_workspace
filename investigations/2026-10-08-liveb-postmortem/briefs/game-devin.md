# Brief: game-devin — game state as a signal for the maker

## Question

Wallet B quotes from the book only. We receive live game state with a delay (GRID about 8 s, Oddin about 15 s; check `grid_delay_s` / `oddin_delay_s` in `match.json` and measure the real lag). Can game state make the two-sided maker lose less or earn more?

## Task

1. Align game state and the book for the 10 maps. Game snapshots: `signal.game_snapshot` rows in `$D/trader_live_b/<match>/session.jsonl` (parsed: net worth adv, deaths per side, phase, paused, `feed_received_at_utc`) and the raw frames in `grid_state.jsonl` / `oddin_state.jsonl` (kills, towers, Roshan, buildings if present; stream, these are 50–90 MB). Book: `$D/book_journal_20261008_liveb.jsonl.gz`.
2. Lead/lag. For every big book move (mid jump ≥ 3 ticks within 10 s), find the game event behind it (kill burst, tower, Roshan, team fight) and measure: book move time vs the time our feed delivered the event. Distribution of (feed time − book move time). Does the book move before our feed? By how much?
3. Regime signal. Even with the delay, game state may tell us when the market is about to be volatile: e.g. after the first kill of a fight arrives, more kills follow; late game, high-ground pushes, Roshan, a big net worth gap swing. Build simple features from the delayed feed (kills in the last 10/30 s, net worth swing, game minute, deaths with long respawn) and test whether they predict a large mid move in the next 5/15/30 s. Report precision/recall and the lift vs base rate.
4. Translate to $: for each of our fills (`fill` in `session.jsonl`), compute the 30 s and 120 s markout (mid after − fill price, times size). Split fills by "regime flag on at fill time" vs off. If pulling quotes while the flag is on would have avoided the adverse fills and kept the good ones, sum the $ per map. Include the pairs we would have lost.
5. Model skew. The Dota model (production model under `esports-trader/data/new_model/production/`; see `src/trader` for how A uses it) predicts the mid 300 s ahead. If you can run it offline on these snapshots cheaply, test: skew our bids toward the model's predicted direction. If running the model is too costly, use a simple proxy (net worth advantage trend) and say so.
6. Also check the pre-horn and early game (second −60 to ~300) and the late game separately: where do our losses concentrate in game time?

## Output

Report: `$R/reports/game-devin.md`. Scripts: `$R/work/game-devin/`. Local jobs up to ~1 h are fine.
