# Add Kalshi

Add Kalshi next to live Polymarket.

This file is the implementation plan. Update it when a decision below changes.

## Goal

On one Steam/GRID map:

- Polymarket quotes as today, with Polymarket prior and Polymarket mid.
- If Kalshi has the same map or series market, quote there too, with
  Kalshi prior and Kalshi mid.
- Sizes and risk caps are independent.
- A Kalshi miss does not stop Polymarket.
- A Kalshi fault does not halt the Engine.

The two venues are independent s2-join loops. They share the dota snapshot.
They do not share market numbers.

## Out of scope

- Edits in `../poly-maker`.
- Replacing `Engine`, `engine_seams`, or `WalletStateStore`.
- Independent Kalshi discovery (no Polymarket sidecar).
- Trading a Kalshi-only match.
- `pmxt` (hosted Kalshi writes do not exist; self-host is a Node sidecar).
- rodlaf MM, newyorkcompute MM, Avellaneda–Stoikov.
- One shared sqlite for both venues.
- One fair from Polymarket executed on Kalshi.
- Putting Kalshi prices into `market_radiant_prior` / `market_p_radiant` of
  the Polymarket path, or the reverse.
- A second LightGBM trained on Kalshi (same booster, two feature vectors).
- A second match folder or `match-kalshi.json`.

## Library

Use **`kalshi-sdk`** (`pip install kalshi-sdk`, import `kalshi`).
Source: [TexasCoding/kalshi-python-sdk](https://github.com/TexasCoding/kalshi-python-sdk).
MIT. Python `>=3.12` (we are 3.13). Pin an exact version in `pyproject.toml`.

It is the execution library, not a market-maker. Take REST plus WebSocket from
it. Keep s2-join in `live_paper`.

| Package | Role here |
|---|---|
| `kalshi-sdk` | REST orders, public market list, candlesticks/history, orderbook WS, user-order WS |
| Official OpenAPI / AsyncAPI | Source of truth if the SDK lags the API |
| `kalshi_python_async` | Do not depend on it. REST only, no WS |
| `pykalshi` | Skip. Same job as `kalshi-sdk`, extra pandas |
| `pmxt` | Skip |
| `rodlaf/KalshiMarketMaker` | Skip. Wrong strategy |
| Official demo host | `https://demo-api.kalshi.co/trade-api/v2` |

Auth for live orders: API key id + RSA-PSS PEM. Public `GET /markets` does not
need a key. Phase 0 can run without an account.

## When to search

Not in `MarketDiscovery.discover()`. That loop runs every 30s over every
sidecar. Putting Kalshi HTTP there would hammer the API for markets we do not
trade.

Search **once per map**, as soon as `select_feed` returns a live Steam or GRID
feed and the session is announced. Code seam: `WalletHost._pick_and_run` after
a non-None feed, before waiting on the first `FeedEvent`. Identity is already
frozen there (`announced=True`): team names, map number, `market_kind`,
`yes_is_radiant`. GRID may still be silent (draft, no `series_table` yet).
That silence is the time we need.

Do not wait for the first feed tick. Do not wait for horn (`second >= 0`).

The model window opens at `MODEL_START_SECOND = -60`. Seconds −90…−61 are
spawn (`pre_horn`); s2-join does not buy yet. −90 is `HORN_OFFSET` for the
prior anchor, not the quote start. Target: ticker, Kalshi book WS, and Kalshi
prior fetch in flight so Kalshi can quote at −60 when the booster opens.

Healthy GRID joins on this VPS (after the 2026-08-25 feed fixes) land in
spawn, before horn: `joined_at_second` −84, −42, −15 on the last maps, all
`feed_source=grid`, `steam_delay_s=900`, `grid_delay_s=8`. An older dump of
80 `match.json` files mixed in mid-map bug joins (median +66 s after horn).
Do not use that dump as the design. If the first yielded tick is already −15,
a search at `write_match_start` is too late for the −60 window.

### Steam vs GRID (does not change the Kalshi key)

Discovery and the feed picker are two steps.

1. In one discovery cycle the sidecar tries Steam, then GRID, and takes the
   first source that clears names and map. That is identity, not the tape.
2. At worker launch, `pick_source` keeps delays `<= 61`. Smaller wins. Steam
   wins a tie. `steam_delay_s=900` is unusable, so these leagues start GRID.
   No usable source → `waiting_for_feed`, retry next cycle, not a crash.

After announce or `match.json`, `feed_source` is pinned. A later faster GRID
does not replace a running Steam worker. Kalshi matching does not read the
feed. The join key is names + map + kind from `DiscoveredMatch`.

### Retry

If the first search returns no unique ticker, retry **once** on the first
`MatchPhase.IN_PROGRESS` tick. Then stop. This covers a map-2 market that
Kalshi lists at horn, without polling for the whole game.

Skip the search (and the retry) when the clock is already
`second >= BUY_CUTOFF_SECOND` (540). We will not buy anyway.

Fail closed: 0 tickers, 2+ tickers, or an orientation tie. Log `kalshi none`
and keep Polymarket.

## How matching works

Input: `DiscoveredMatch` (already Steam/GRID linked).

1. Pick series: `map_winner` → `KXDOTA2MAP`, `series_winner` → `KXDOTA2GAME`.
2. List open markets for that `series_ticker` (host-level cache, TTL 60s).
3. Keep rows whose ticker map suffix equals `map_number` (map markets), or
   the series event (series markets).
4. Orient Kalshi titles against Radiant/Dire with `orient_outcomes` (same
   helper as Polymarket).
5. Require exactly one ticker. Store `ticker`, `yes_is_radiant`, tick size.

Kalshi has no GRID id. Do not use `grid_series_id` as a join key.

Ticker shape we already know:
`KXDOTA2MAP-26MAY150600LIQUIDVP-1-LIQUID`.

Kalshi Dota coverage is thinner than Polymarket. Many maps will miss. That is
accepted for v1.

## Two fairs, one booster

`predict_fair` is `fair = mid + delta`. `delta` reads dota plus **that** mid
and **that** prior. `evaluate_entry` uses `predicted_delta = fair − mid`.

Each venue runs this once per tick:

| | Polymarket | Kalshi |
|---|---|---|
| Prior | CLOB `/prices-history` YES/NO mids before horn − 90 | Kalshi candlesticks / history before the same anchor |
| Live mid | MDS YES/NO pair, `normalize_pair_mids` | Kalshi book, oriented to P(Radiant) |
| `predict_fair(snapshot, mid, prior)` | PM numbers only | Kalshi numbers only |
| Join | PM bids | Kalshi bids minus the series fee (maker 0 on most event markets) |

Steam/GRID is not a market. It is the shared `GameSnapshot`.

Do not mix. A Kalshi join must not see Polymarket mid, Polymarket prior, or
Polymarket fair. A missing Kalshi prior is `missing_prior` on Kalshi only.
Polymarket can still buy. The reverse is true.

The booster stays the Polymarket-trained production model. v1 feeds it Kalshi
probabilities as the same two market features. That is not a second model.
A Kalshi-trained booster is later, if this drifts.

Kalshi prior uses the same map-load rule as Polymarket, not draft. Start the
fetch as soon as the ticker is bound, on `PRE_HORN` or earlier if the horn
unix is already known from the handoff. Same `HORN_OFFSET_SECONDS = 90`
anchor. HTTP off the loop, retry on failure like `fetch_market_prior`.

## Dual trading

Same `MatchWorker`. Two `predict_fair` calls when both venues are live.
Two `evaluate_entry` calls.

| | Polymarket | Kalshi |
|---|---|---|
| Engine | current `Engine` | not used |
| Id | `condition_id` + two token ids | one ticker, YES/NO on that ticker |
| Book | MDS YES/NO pair | Kalshi orderbook WS |
| Prior / mid / fair | own | own |
| Size | `base_size_usdc` | `base_size_usd` (the only Kalshi money knob) |
| Risk | current Engine `[risk]` | none. No daily kill, no exposure cap, no auto flatten. Watch by hand |
| Wallet | `live.db` | `data/live_paper/wallet/kalshi.db` (fills/orders only) |
| Fees | no taker fee on our join-bids; sports maker rebate after the fact | no rebate. Taker `ceil(0.07×qty×p×(1−p))` if we cross. Resting maker is 0 on most series, else `ceil(0.0175×qty×p×(1−p))` |
| Halt | Engine halt | cancel on process teardown only |

Do not port poly-maker `RiskManager`, day kill, or USDC multiples onto Kalshi.
v1 is size plus s2-join. A later Kalshi engine can add halt rules; this plan
does not.

**Fees.** Polymarket CLOB does not charge a trading fee on a resting join-bid.
Live PnL then adds the sports maker rebate `0.15 * 0.05 * qty * p * (1-p)`
(paid to the wallet if the UTC-day total is at least $1). Kalshi pays no
rebate. The official MM program is a contract with the exchange, not a flag
we get by posting joins. Ignore it until we are in that program.

On Kalshi event contracts (schedule as of 2026-07): a taker fill (we lift
the book) pays `ceil_cent(0.07 * qty * p * (1-p))`. A resting maker fill
pays nothing unless that series is on the maker-fee list, then
`ceil_cent(0.0175 * qty * p * (1-p))`. Cancel of an unfilled rest is free.
s2-join is a rest, so the haircut is maker (often 0), not always 0.07. Read
the series fee from the market payload. Do not change the Polymarket path.

`MIN_ENTRY_PRICE = 0.35` stays. Tick is 1 cent on both, but still read
Kalshi's tick from the market payload. If it is not 0.01, skip Kalshi that
map (`off_grid`), do not skip Polymarket.

Teardown (`WalletHost.teardown`) cancels Kalshi resting orders for live
tickers, then the existing Engine `cancel_all`.

## Config and env

New env (gitignored `.env`):

- `KALSHI_TRADING` = `off` / `paper` / `live` (default `off`)
- `KALSHI_KEY_ID` + `KALSHI_PRIVATE_KEY_PEM` required for `live`

`paper` uses the public book and simulated fills (same idea as
`PaperGateway`: touch does not fill, one-sided book does not fill). No Kalshi
order. No Kalshi demo API. `demo-api.kalshi.co` is their fake-money exchange
with a second key pair. Skip it. Local paper, then prod.

`live` talks to production `api.elections.kalshi.com`.

New TOML table `[kalshi]`. The only money number:

```toml
[kalshi]
base_size_usd = 10.0
```

Do not add risk caps. Do not derive this from `base_size_usdc`.

`KALSHI_TRADING=off` means no HTTP and no extra tasks.

## Journal and alerts

Same folder `data/live_paper/<match_id>/`. No extra files for Kalshi identity.

**`match.json` schema 4.** Add one always-present `kalshi` object next to
`market` (Polymarket stays as today). Open the file and see whether we bound
Kalshi.

```json
"kalshi": {
  "ticker": "KXDOTA2MAP-…-2-LIQUID",
  "series_ticker": "KXDOTA2MAP",
  "yes_is_radiant": true,
  "reason": "matched"
}
```

On a miss: `ticker`, `series_ticker`, and `yes_is_radiant` are JSON null,
`reason` is `none` / `ambiguous` / `cutoff` / `error` / `off`. `off` when
`KALSHI_TRADING=off`. Do not omit the key.

Search runs in `_pick_and_run` before the first feed tick, so the first
`write_match_start` already has the bind. The one `IN_PROGRESS` retry may
replace a miss with a ticker: rewrite only the `kalshi` object on the
unfinished start document. Do not change `market` or `feed_source`.

Boot/resume must read schema 3 (old maps, no `kalshi` key) and schema 4.
New writes are 4. `final.pnl` stays Polymarket USDC. Do not put Kalshi PnL
there.

**`session.jsonl`** stays the trade tape. Kalshi signals, quotes, and fills
need Kalshi prior/mid/fair (second kind or a `venue` field). Do not put
Kalshi mids into `market_p_radiant`. Binding for humans is `match.json`,
not a required extra `kalshi_bind` line.

Telegram `session started` stays as today. One extra line after bind:
`live-paper kalshi: match <id> ticker …` or `ticker none reason …`.

Session finished: Kalshi leftover and Kalshi net on their own line. Do not
sum USD and USDC.

## Code layout (fewest files)

Live-paper only. No collect/train/backtest in v1.

| File | Job |
|---|---|
| `src/live_paper/kalshi_match.py` | ticker resolve, fail-closed |
| `src/live_paper/kalshi_client.py` | thin wrap of `kalshi-sdk`: list, history, place, cancel, WS |
| `src/live_paper/kalshi_prior.py` | map-load P(Radiant) from Kalshi history, same horn − 90 anchor |
| `src/live_paper/kalshi_executor.py` | paper/live sync from the Kalshi fair/entry, not from the PM cell |
| `src/live_paper/wallet_host.py` | search after `select_feed`; boot/teardown of one Kalshi session |
| `src/live_paper/match_meta.py` | schema 4 `kalshi` object; read 3 and 4 |
| `src/live_paper/match_worker.py` | second `predict_fair` + `evaluate_entry` on Kalshi prior/mid |
| `config/dota-map.toml` | `[kalshi] base_size_usd` only |
| `tests/test_live_paper_kalshi_match.py` | name/map/kind fixtures, ambiguity |
| `tests/test_live_paper_kalshi_prior.py` | orientation, missing history, no mix with PM tokens |
| `tests/test_live_paper_kalshi_executor.py` | paper fills, fee haircut, PM still quotes on Kalshi miss |

No `Venue` protocol. The worker calls two functions.

## Phases

Do these in order. Each phase is shippable on the VPS.

1. **Matcher dry-run.** Search at `select_feed` success. Write `kalshi_bind`.
   Telegram the ticker or `none`. Run through a few live maps. Measure
   unique-match rate. Stop if the rate is near zero.
2. **Prior + paper executor.** Kalshi history prior, Kalshi book, second
   `predict_fair`. Simulated fills. Confirm PM path still uses only PM
   numbers. Confirm Kalshi is ready by −60 on a spawn join.
3. **Prod live.** Real orders on production. Cancel-all on teardown.

Do not start phase 3 before phase 1 has a measured hit rate on this VPS.

## Checks

- Matcher: two teams + map 2 → exactly one `KXDOTA2MAP-…-2-…` ticker. Swap
  names. Ambiguous alias → none.
- Search starts from `select_feed`, not from `discover()`, not from the first
  feed tick.
- Kalshi `predict_fair` arguments are Kalshi prior and Kalshi mid only.
- Polymarket `predict_fair` arguments are unchanged.
- Missing Kalshi prior blocks Kalshi buys only.
- Taker haircut at p=0.50, qty=1 is 2 cents (`ceil(0.07*0.25)`). Maker on a
  default series is 0. Maker on a listed series is 1 cent
  (`ceil(0.0175*0.25)`).
- No Kalshi daily-loss halt in tests: size in, flatten only via s2-join.
- Schema 4 `match.json` always has `kalshi`. A miss still has the key.
- Schema 3 folders still boot. New maps write 4.
- Worker with `kalshi.reason=none` still attaches Engine and quotes Polymarket.
- Worker with a ticker places Kalshi paper fills without calling
  `engine.gateway`.
- `second >= 540` → no Kalshi HTTP.
- First search miss, later `IN_PROGRESS` → one retry, not a loop.
- `make lint-all` on every new Python file.
