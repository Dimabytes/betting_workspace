# Brief: decay-dev — LoL market microstructure per period: who moves the mid, and did it change

Question: the LoL backtest's buy-300s markout falls from ~2.5 ¢ (June–Aug) to ~0.5 ¢ (Aug 22–Sep 29) while Dota's grows (context fact 1). Measure the Polymarket LoL market itself across the validation period and say whether it became faster / tighter / more competitive, and when.

Data: Telonex LoL books `data/lol/raw/telonex/polymarket/` (see `src/shared/utils/telonex_book.py`, `src/backtest/telonex_local.py` for the reader and layout; files are per day per channel — read only the asset/day you need; `data/lol/processed/telonex/catalog.parquet` maps market_id ↔ asset ids ↔ coverage), per-map market seconds `data/lol/processed/datasets/market_seconds.parquet`, dataset `validation.parquet` (`market_p_radiant`, `signal_market_p_radiant_300s`, `radiant_nw_adv`, `second`, `state_ts_us`), onchain fills if present (`scripts/sync_collector_parquet.py` docstring tells where they land). Universe `data/lol/processed/universe/markets.parquet` for league and event dates.

Do (sample ≈ 60 whitelisted maps per month June..September, stratified by league; keep per-map intermediate parquet under `work/decay-dev/`):
1. Spread at best, depth within 2 ¢ of best, number of book updates per game-minute, during seconds 0..480 of the game. Per month.
2. Mid reaction speed: for large gold swings (|Δ radiant_nw_adv| over 60 s above the 90th pct) measure how many seconds after the frame stamp the mid has done half of its 300 s move. Per month. (leadlag-sol does this on the whole tape with a different method; your job is the microstructure view: does the book get re-quoted within 1–3 s of the event, and by how many levels.)
3. Taker vs maker flow around those events if trades/onchain fills are available: who takes, how big, at what delay.
4. Count distinct patterns of quote behaviour that look like bots (same-size orders re-posted every N s, instant re-quote after each frame) per month. Rough is fine; say how you detected them.
5. Compare one Dota month (September, `data/raw/telonex/polymarket/`, `data/processed/...` equivalents, see `docs/as-is.md`) on metrics 1–2 so the reader sees whether LoL books are structurally faster.

Deliver: a per-month table of spread / depth / reaction-half-life / update-rate, a verdict on whether the market got faster or tighter in Aug–Sep, and what that implies for a maker strategy that acts 11 s after the frame stamp. Proposed experiments: if the market is faster, what cadence/lag would our signal need (quantify) and is there any LoL source that delivers it (link to leadlag-dev's domain; just state the requirement).
