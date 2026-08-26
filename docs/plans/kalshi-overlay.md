# Add Kalshi

Add Kalshi next to live Polymarket.

This file is the implementation plan. Update it when a decision below changes.

## Goal

On one Steam/GRID map:

- Polymarket quotes as today, with Polymarket prior and Polymarket mid.
- If Kalshi has the same map or series market, quote there too, with
  Kalshi prior and Kalshi mid.
- Sizes, orders, positions, and venue state are independent.
- A Kalshi miss does not stop Polymarket.
- A Kalshi fault does not halt the Engine. It does halt Kalshi locally: clear
  its desired quote, cancel its strategy-owned resting orders, and block new
  Kalshi orders until the venue is proven healthy again.

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
- A Kalshi economic `RiskManager`, daily-loss kill, portfolio exposure cap, or
  automatic market-order flatten. These are separate from the mandatory
  operational safety and reconciliation rules below.

## Library

Use **`kalshi-sdk`** (`pip install kalshi-sdk`, import `kalshi`).
Source: [TexasCoding/kalshi-python-sdk](https://github.com/TexasCoding/kalshi-python-sdk).
MIT. Python `>=3.12` (we are 3.13). Pin an exact version in `pyproject.toml`.

It is the execution library, not a market-maker. Take REST plus WebSocket from
it. Keep s2-join in `live_paper`.

The library owns signing, request/response models, pagination, and WebSocket
parsing. It does **not** choose the correct API generation, price/count units,
order semantics, retry policy, reconnect policy, or safety behavior for us.
Those contracts stay explicit in this plan and in our thin wrapper:

- use the SDK's V2 event-order surface (`/portfolio/events/orders`), not the
  legacy YES/NO order endpoint;
- use `Decimal` fixed-point dollar prices and fixed-point contract counts;
- use `post_only=true` for every resting s2-join order;
- treat `client_order_id` as the idempotency key;
- never blindly retry a write whose result is unknown;
- use `price_level_structure` and `price_ranges`, not the removed scalar
  `tick_size` field;
- use the series fee fields and fee-change endpoint, not a hard-coded market
  list;
- surface sequence gaps, disconnects, auth failures, and rate limits to the
  venue-local safety state.

`kalshi_client.py` is the only module that sees SDK models. It converts them to
small frozen internal dataclasses so an SDK upgrade cannot leak through the
worker/executor. Tests mock this wrapper, not SDK internals. Before pinning or
upgrading a version, run the demo execution smoke described below.

| Package | Role here |
|---|---|
| `kalshi-sdk` | REST orders, public market list, candlesticks/history, orderbook WS, user-order WS |
| Official OpenAPI / AsyncAPI | Source of truth if the SDK lags the API |
| `kalshi_python_async` | Do not depend on it. REST only, no WS |
| `pykalshi` | Skip. Same job as `kalshi-sdk`, extra pandas |
| `pmxt` | Skip |
| `rodlaf/KalshiMarketMaker` | Skip. Wrong strategy |
| Official demo host | SDK demo config (`external-api.demo.kalshi.co`) |

Auth for live orders: API key id + RSA-PSS PEM. Public `GET /markets` does not
need a key, so matcher dry-run can run without an account. Kalshi WebSocket
handshakes do require auth even for `orderbook_delta`; prior + paper therefore
requires a read-scoped key, and live requires a write-scoped key.

## When to search

Not in `MarketDiscovery.discover()`. That loop runs every 30s over every
sidecar. Putting Kalshi HTTP there would hammer the API for markets we do not
trade.

Start one asynchronous resolve task per map as soon as `select_feed` returns a
live Steam or GRID feed and the session is announced. Code seam:
`WalletHost._pick_and_run` after a non-None feed. Identity is already frozen
there (`announced=True`): team names, map number, `market_kind`, and
`yes_is_radiant`.

Do **not** await Kalshi before starting `MatchWorker`. `select_feed` only builds
the feed; Steam/GRID reading and archive writes begin inside `feed.ticks()`.
Waiting for Kalshi in `_pick_and_run` would delay the first feed read and lose
archive time. Pass the resolve task into the worker and let it run concurrently
with the feed.

The first `FeedEvent` stays archive-first:

1. write the raw Steam/GRID event;
2. write schema-4 `match.json` immediately with `kalshi.reason="pending"` if
   the resolve task has not finished;
3. open the existing Polymarket session and keep it independent;
4. when resolve finishes, atomically replace only the unfinished document's
   `kalshi` object;
5. then subscribe to the Kalshi book and start the Kalshi prior fetch.

Do not wait for horn (`second >= 0`) to start matching. Do not block feed
consumption while matching, connecting Kalshi, or fetching history.

The model window opens at `MODEL_START_SECOND = -60`. Seconds −90…−61 are
spawn (`pre_horn`); s2-join does not buy yet. −90 is `HORN_OFFSET` for the
prior anchor, not the quote start. Target: ticker, Kalshi book WS, and Kalshi
prior fetch in flight so Kalshi can quote at −60 when the booster opens.
The exact readiness target is: ready for the first model-eligible tick that we
actually observe. If the first yielded tick is already −15, quoting at −60 is
already impossible; bind and become ready as soon as possible without delaying
the feed.

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

If the first search returns no unique ticker, retry **once** after
`MatchPhase.IN_PROGRESS` has been observed. If the first event is already
`IN_PROGRESS`, run this hook in the first-event setup branch before its current
`continue`. If the first resolve completes later, retry immediately when the
latest observed phase is already `IN_PROGRESS`. Then stop; never poll for the
whole game.

Before the first `FeedEvent` there is no game `second`, so the initial public
market-list request cannot truthfully apply `BUY_CUTOFF_SECOND`. Once the first
event exists, `second >= BUY_CUTOFF_SECOND` discards an in-flight match result
and starts no book, prior, or execution work. It also suppresses the retry.

Fail closed: 0 tickers, 2+ tickers, or an orientation tie. Log `kalshi none`
and keep Polymarket. The resolve state machine is exactly `pending` → one of
`matched` / `none` / `ambiguous` / `off_grid` / `cutoff` / `error`; only
`none` gets the single in-progress retry.

## How matching works

Input: `DiscoveredMatch` (already Steam/GRID linked).

Keep v1 deliberately simple: teams + map/kind, then fail closed. Do not add a
second schedule/tournament/rules linker before the simple matcher has measured
results.

1. Pick series: `map_winner` → `KXDOTA2MAP`, `series_winner` → `KXDOTA2GAME`.
2. List **all pages** of open markets for that `series_ticker` (host-level
   cache, TTL 60s).
3. For `map_winner`, keep events whose event-ticker map component equals
   `map_number`. For `series_winner`, keep the full-match event.
4. Orient the response's exact `yes_sub_title` / `no_sub_title` outcome names
   against Radiant/Dire with `orient_outcomes` (same name helper as
   Polymarket). Do not parse prose rules or infer the YES team only from the
   market ticker suffix.
5. Require exactly one oriented ticker. Store `event_ticker`, `ticker`,
   Kalshi YES/NO outcome names, `yes_is_radiant`, `price_level_structure`, and
   `price_ranges`.

Time, tournament, and rules text are logged with every candidate for dry-run
diagnosis but are not v1 match keys. If teams + map/kind produce two candidates,
return `ambiguous`; do not break the tie with a guess. Add a time/tournament
filter later only if dry-run shows a real collision or false match.

Kalshi has no GRID id. Do not use `grid_series_id` as a join key.

Ticker shape we already know:
`KXDOTA2MAP-26MAY150600LIQUIDVP-1-LIQUID`.
The event is `KXDOTA2MAP-26MAY150600LIQUIDVP-1`; the final `-LIQUID` belongs to
the binary market and identifies its YES outcome.

Kalshi Dota coverage is thinner than Polymarket. Many maps will miss. That is
accepted for v1.

## Two fairs, one booster

`predict_fair` is `fair = mid + delta`. `delta` reads dota plus **that** mid
and **that** prior. `evaluate_entry` uses `predicted_delta = fair − mid`.

Each venue runs this once per tick:

| | Polymarket | Kalshi |
|---|---|---|
| Prior | CLOB `/prices-history` YES/NO mids before horn − 90 | Kalshi candlesticks / history before the same anchor |
| Live mid | MDS YES/NO pair, `normalize_pair_mids` | one Kalshi binary book, converted to a two-sided YES/NO pair and oriented to P(Radiant) |
| `predict_fair(snapshot, mid, prior)` | PM numbers only | Kalshi numbers only |
| Join | PM bids | Kalshi post-only joins with fee-adjusted edge |

Steam/GRID is not a market. It is the shared `GameSnapshot`.

Do not mix. A Kalshi join must not see Polymarket mid, Polymarket prior, or
Polymarket fair. A missing Kalshi prior is `missing_prior` on Kalshi only.
Polymarket can still buy. The reverse is true.

The booster stays the Polymarket-trained production model. v1 feeds it Kalshi
probabilities as the same two market features. That is not a second model, but
it is an explicit transport assumption: a model trained on future Polymarket
midpoint changes is assumed useful on Kalshi features. The paper maps
(phase 3 below) must show a sane paired PM/Kalshi basis, prior difference,
signals, fills, and fee-adjusted paper PnL before live. A Kalshi-trained
booster is later, if this drifts.

### Kalshi book and mid

Kalshi orderbook snapshots/deltas carry resting YES bids and NO bids. Build the
complete binary pair without mixing in Polymarket:

- `yes_bid = best YES bid`;
- `yes_ask = 1 - best NO bid`;
- `no_bid = best NO bid`;
- `no_ask = 1 - best YES bid`;
- YES and NO mids are the midpoint of their own bid/ask; orient once to
  P(Radiant).

Require both bid sides, finite fixed-point prices, positive sizes, and
`yes_bid < yes_ask`. Missing/one-sided/crossed data blocks Kalshi only. Maintain
prices and contract counts as `Decimal`/scaled integers through book, sizing,
fees, and order serialization; convert to finite float only at the model call.

Every subscription generation is unready until a full snapshot is applied.
Deltas must be contiguous by sequence. A gap, disconnect, parse fault, auth
fault, or book-age timeout clears readiness and enters Kalshi local safe mode;
never continue from the last cached book.

A quiet market is not stale merely because it has no deltas. At the book-age
threshold, stop publishing targets and request a fresh snapshot. An unchanged
but successfully received snapshot refreshes readiness; a failed/timed-out
snapshot enters safe mode. Private-WS blindness means a dead/auth-failed
connection or an overdue REST reconciliation, not simply "no user fills".

### Kalshi prior

Kalshi prior uses the same map-load rule as Polymarket, not draft. Start the
fetch as soon as the ticker is bound and the first horn-clock event supplies
the horn Unix time. Same `HORN_OFFSET_SECONDS = 90` anchor.

Request one-minute market candlesticks over a bounded range ending at the
anchor. Use the latest fully closed candle whose `end_period_ts <= anchor` and
whose YES bid and YES ask closes are both present and valid; their midpoint is
the Kalshi YES prior, oriented once to P(Radiant). Never use a candle ending
after the anchor, a last trade without a two-sided quote, or event-aggregated
candles across markets. Missing history is `missing_prior` on Kalshi only.

Use the SDK async REST client so HTTP does not block the loop. Retry only
idempotent reads with bounded backoff. A failed prior task may retry while the
model window is open; it never restarts matching and never affects PM.

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
| Economic risk | current Engine `[risk]` | v1 size + one open round per ticker; no daily kill, total-exposure cap, or auto flatten |
| Operational safety | Engine halt/reduce-only | independent Kalshi local safe mode, cancel, readiness, and reconciliation |
| Wallet | `live.db` | `data/live_paper/wallet/kalshi.db` (orders, fills, position/cash ledger, reconciliation checkpoints) |
| Fees | no taker fee on our join-bids; sports maker rebate after the fact | no rebate. Taker `ceil(0.07×qty×p×(1−p))` if we cross. Resting maker is 0 on most series, else `ceil(0.0175×qty×p×(1−p))` |
| Halt | Engine halt | Kalshi only; PM Engine stays live |

Do not port poly-maker `RiskManager`, day kill, or USDC exposure multiples onto
Kalshi in v1. That choice does not weaken protocol safety. A fault can never
leave Kalshi quoting blindly merely because its economic risk policy is simple.

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
`fee_type` / `fee_multiplier` from the series response and its scheduled fee
changes. Do not infer it from the market payload and do not change the
Polymarket path.

The pre-trade/paper fee function uses the exchange's current series parameters
and fixed-point rounding. Live accounting persists the exact fee reported on
each exchange fill; it never replaces that value with a local recomputation.
Apply fees to every filled leg, including an exit. `post_only` rejection is a
normal no-quote outcome, not permission to resend without `post_only`.

Keep the existing model `predicted_delta` gate unchanged. After
`evaluate_entry` chooses a candidate side/price, compute its fixed-point count
and apply a separate Kalshi fee gate:

`qty * (side_fair - entry_price) - maker_fee(entry) - maker_fee(projected_exit) >= 0`.

Use the candidate entry price and the side fair rounded to the valid grid for
the conservative projected maker exit. If the series has no maker fee, both
terms are zero. This gate blocks the Kalshi candidate only and does not mutate
the shared `evaluate_entry` or PM behavior.

`MIN_ENTRY_PRICE = 0.35` stays. For v1 accept only a `linear_cent` Kalshi
`price_level_structure` whose `price_ranges` prove a $0.01 step across the
usable range. Any tapered/sub-cent/unknown structure is `off_grid` for Kalshi
only. Fixed-point contract count supports 0.01-contract granularity: compute
`base_size_usd / entry_price`, quantize down to an exchange-valid count, and
skip zero.

Teardown (`WalletHost.teardown`) first closes Kalshi quoting, cancels all
strategy-owned Kalshi resting orders, proves the Kalshi fence through REST,
then runs the existing Engine `cancel_all`. Failure to prove the Kalshi fence
is logged and alerted independently; it does not skip PM teardown.

## Kalshi local safe mode

Kalshi has its own process-wide session state; it is not an Engine regime. A
ticker may publish an order target only when all of these are true:

- matcher is uniquely bound and the market is open;
- a full book snapshot for the current WS generation has been applied;
- book deltas are contiguous and the book is not stale;
- the authenticated fills/orders stream is live in `live` mode;
- startup/reconnect reconciliation is proven;
- the prior, model inputs, tick grid, position, and order state are valid;
- the shared Dota feed is inside the existing model window and is fresh.

Any of the following enters local safe mode: book disconnect/staleness,
sequence gap, private-WS blindness, auth/rate-limit exhaustion, malformed SDK
payload, client/executor/store exception, ambiguous write result, order/position
divergence, or failed cancel/fence.

Entering safe mode is idempotent and does this in order:

1. clear the Kalshi desired order immediately;
2. block all new Kalshi writes except cancel/reconciliation;
3. cancel every strategy-owned resting order on the affected ticker; for a
   blind private stream or account-level reconciliation fault, cancel them on
   every live Kalshi ticker;
4. poll REST until those orders are proven absent or the fence times out;
5. preserve filled positions and the durable ledger; do not market-flatten;
6. alert once per changed fault state;
7. reconnect, request a new full snapshot, and reconcile before reopening.

A cancel failure never removes an order from `kalshi.db`. An unproven fence
keeps Kalshi locked for the rest of that session. None of these steps calls
Engine halt, `engine.gateway`, or changes a Polymarket `StrategyCell`.

## Live ownership and crash reconciliation

Use one host-owned Kalshi REST client, authenticated WebSocket session, sqlite
store, and wallet flock for all match workers. Prefer a dedicated Kalshi
subaccount. Every strategy order carries:

- configured subaccount;
- a process-owned order-group id when available;
- a deterministic `client_order_id` namespace containing strategy, ticker,
  side, and desired-order generation.

At live boot, before discovery or any Kalshi quote:

1. acquire the `kalshi.db` flock;
2. read local open orders, fills, positions, and the last reconciliation
   checkpoint;
3. fetch all resting orders, recent fills, and unsettled positions for the
   configured subaccount from REST, following pagination;
4. ingest fills idempotently by exchange fill id, including exact fees;
5. cancel strategy-owned resting orders left by an earlier process;
6. prove they are absent and compare exchange positions with the derived local
   ledger;
7. write a reconciliation checkpoint and only then mark Kalshi ready.

Never cancel manual/foreign orders merely because they share the account;
scope ownership by subaccount + client-order namespace/order group. A foreign
position or an unexplained position delta blocks Kalshi and alerts rather than
being silently adopted.

For place/cancel/amend writes, persist intent before the network call. A clear
exchange response advances state. On timeout/disconnect after submission,
query REST by order/client identity and reconcile; never blindly resend. User
WS is the low-latency update path, while periodic REST reconciliation is the
authority and repairs missed messages. Partial fills, duplicate WS/REST fills,
out-of-order order updates, settlement, and process restart must all be
idempotent.

## Config and env

New env (gitignored `.env`):

- `KALSHI_TRADING` = `off` / `observe` / `paper` / `live` (default `off`)
- `KALSHI_KEY_ID` + `KALSHI_PRIVATE_KEY_PATH` required for `paper` and `live`
- `KALSHI_SUBACCOUNT` = integer subaccount (default `0`; prefer a dedicated
  one before live)

`observe` runs only the public matcher, metadata update, logs, and Telegram.
It creates no authenticated WS or prior/execution tasks and needs no key. This
is phase 1.

`paper` uses the public book and simulated fills (same idea as
`PaperGateway`: touch does not fill, one-sided book does not fill). No Kalshi
order. It still needs a read-scoped key because Kalshi authenticates the
orderbook WebSocket handshake. Local paper is the strategy-validation mode.

`live` uses the SDK production config and its recommended external Trade API
hosts (`external-api.kalshi.com` REST and `external-api-ws.kalshi.com` WS).
Do not hand-build or hard-code the compatibility `api.elections` URLs outside
the SDK wrapper.

Mount the PEM as a read-only Docker secret/file. Do not put a multiline private
key or its contents in logs, Telegram, sqlite, `match.json`, session fixtures,
or recorded SDK transports. Key scopes are read-only for paper and read+write
for live.

New TOML table `[kalshi]`. `base_size_usd` is the only money number; the other
values are operational safety timeouts:

```toml
[kalshi]
base_size_usd = 10.0
book_stale_s = 30.0
private_ws_blind_s = 15.0
reconcile_interval_s = 20.0
fence_timeout_s = 20.0
```

Do not add economic risk caps in v1 and do not derive this from
`base_size_usdc`. Operational readiness, cancel/fence, and reconciliation are
mandatory and are not config money knobs.

`KALSHI_TRADING=off` means WalletHost starts no Kalshi HTTP or extra tasks. The
separate manual smoke command is unaffected and runs only when explicitly
invoked.

## Execution smoke (real API lifecycle, not strategy validation)

Add a separate manual command, `scripts/kalshi_execution_smoke.py`. It is not
started by WalletHost and never reads Dota/Polymarket. Its job is the missing
equivalent of the old wallet smoke: prove that our pinned SDK, credentials,
order API, fills, cancel, positions, fees, and reconciliation agree end to end.
It must reuse `kalshi_client`, `kalshi_store`, and the normal reconciliation /
fence primitives; only market selection and the forced round trip are
smoke-specific.

Default manual-safe environment is the Kalshi demo exchange with its
separate demo key pair:

```bash
uv run python scripts/kalshi_execution_smoke.py --env demo
```

It reads `KALSHI_DEMO_KEY_ID` and `KALSHI_DEMO_PRIVATE_KEY_PATH`, never the
production credentials by fallback. Demo and production keys are not
interchangeable.

The command:

1. lists open demo markets and chooses one `linear_cent`, two-sided binary
   market (or accepts `--ticker`);
2. records initial balance and position;
3. places the minimum valid YES buy through the V2 endpoint, with an explicit
   client id and bounded IOC/FOK semantics for the smoke only;
4. observes the fill through WS and confirms it through REST;
5. places a reduce-only sell for exactly the filled count;
6. cancels any remainder, fences all smoke-owned orders, and reconciles;
7. requires the position delta to return to zero and prints order ids, fills,
   exact fees, balance delta, and PASS/FAIL without secrets.

No step retries an ambiguous write. If there is no suitable/liquid demo market
or the closing leg does not fill, cancel/fence and report FAIL with the leftover
position; do not hunt markets or trade repeatedly.

Production smoke is disabled by default. If added later, require an explicit
`--env production --ticker ... --confirm-live` combination, the minimum valid
size, and a named ticker chosen by the operator. Never choose a random live
market or automatically cross real-money books. Local paper evaluates the
strategy; demo smoke validates execution integration; production canary is the
first real-money Dota order.

## Journal and alerts

Same folder `data/live_paper/<match_id>/`. No extra files for Kalshi identity.

**`match.json` schema 4.** Add one always-present `kalshi` object next to
`market` (Polymarket stays as today). Open the file and see whether we bound
Kalshi.

```json
"kalshi": {
  "event_ticker": "KXDOTA2MAP-…-2",
  "ticker": "KXDOTA2MAP-…-2-LIQUID",
  "series_ticker": "KXDOTA2MAP",
  "yes_outcome": "Team Liquid",
  "no_outcome": "Virtus.pro",
  "yes_is_radiant": true,
  "price_level_structure": "linear_cent",
  "tick_size": "0.01",
  "reason": "matched"
}
```

On a pending/miss: `event_ticker`, `ticker`, outcome names, and
`yes_is_radiant`, price structure, and normalized tick are JSON null.
`series_ticker` is present once kind selects the series; otherwise null.
`reason` is `pending` / `none` / `ambiguous` / `off_grid` / `cutoff` / `error`
/ `off`. `off` when `KALSHI_TRADING=off`. An `off_grid` bind keeps the resolved
ticker/outcomes/price structure for diagnosis but starts no book or execution.
Do not omit the key.

The first `write_match_start` must not wait for matching. Resolve completion
and the one `IN_PROGRESS` retry may replace the `kalshi` object on an
unfinished start document. The update reads schema 4, verifies `final is null`
and the immutable match/PM/feed/model binding, and atomically writes only the
new full document. It never changes `market`, `feed_source`, or an already
finalized file.

Boot/resume must read schema 3 (old maps, no `kalshi` key) and schema 4. New
match starts write 4. An unfinished schema-3 match resumes its existing PM
session but keeps Kalshi `off` for that map; do not introduce a venue midway
through a legacy start document. `final.pnl` stays Polymarket USDC. Do not put
Kalshi PnL there.

**`session.jsonl`** stays the per-match trade tape. Bump its session-start
schema to 6. New signal, quote, fill, trading-error, and session-end records
carry `venue: "polymarket" | "kalshi"`; schema 1–5 records with no `venue` are
read as Polymarket. Kalshi records have venue-named prior/mid/fair/order/fee
fields and never put Kalshi numbers into PM `market_p_radiant` or
`market_radiant_prior` fields. Binding for humans is `match.json`; there is no
extra `kalshi_bind` record.

Kalshi fills are written only after their exchange fill id and exact fee are
durable in `kalshi.db`; duplicate WS/REST delivery writes no duplicate tape
record. A Kalshi `session_end` durably records leftover YES/NO, net cash, book
inventory mark, fees, and reconciliation state. Telegram is a view of this
state, not the only PnL record.

Telegram `session started` stays as today. One extra line after bind:
`live-paper kalshi: match <id> ticker …` or `ticker none reason …`.

Session finished: Kalshi leftover and Kalshi net on their own line. Do not
sum USD and USDC.

## Code layout

Live-paper only. No collect/train/backtest in v1.

| File | Job |
|---|---|
| `src/live_paper/kalshi_match.py` | ticker resolve, fail-closed |
| `src/live_paper/kalshi_client.py` | only SDK boundary: internal dataclasses, list/history/fees/orders/portfolio/WS |
| `src/live_paper/kalshi_prior.py` | map-load P(Radiant) from Kalshi history, same horn − 90 anchor |
| `src/live_paper/kalshi_store.py` | sqlite intents/orders/fills/ledger/checkpoints, idempotency, flock |
| `src/live_paper/kalshi_session.py` | host-owned WS generations, readiness, local safe mode, boot/reconnect reconcile/fence |
| `src/live_paper/kalshi_executor.py` | paper/live desired-order sync from Kalshi fair/entry, V2 bid/ask translation |
| `src/live_paper/wallet_host.py` | start resolve task after `select_feed`; boot/teardown one Kalshi session |
| `src/live_paper/match_meta.py` | schema 4 `kalshi` object; read 3 and 4 |
| `src/live_paper/match_worker.py` | consume resolve concurrently; second `predict_fair` + `evaluate_entry` on Kalshi numbers |
| `src/live_paper/session_journal.py` | schema 6 venue discriminator and durable Kalshi tape records |
| `config/dota-map.toml` | Kalshi size plus operational stale/reconcile/fence timeouts |
| `scripts/kalshi_execution_smoke.py` | explicit demo create/fill/close/cancel/reconcile smoke |
| `tests/test_live_paper_kalshi_match.py` | name/map/kind fixtures, ambiguity |
| `tests/test_live_paper_kalshi_prior.py` | orientation, missing history, no mix with PM tokens |
| `tests/test_live_paper_kalshi_store.py` | duplicate/out-of-order fills, intent recovery, position and fee ledger |
| `tests/test_live_paper_kalshi_session.py` | snapshot generation, gap/stale/blind safe mode, boot/reconnect fence |
| `tests/test_live_paper_kalshi_executor.py` | paper fills, V2 mapping, post-only, fee edge, partial fills |
| existing wallet/meta/journal tests | concurrent resolve timing, schema 3/4 and 1–6 resume, PM independence |

No generic `Venue` protocol. The PM path stays direct; the worker delegates
Kalshi lifecycle/state to the dedicated host-owned Kalshi session.

## Phases

Timeline (decided 2026-08-26): matches resume 2026-08-28. All code lands on
2026-08-26/27; the first 1–2 live maps run in paper; then the operator flips
live. The safety gates stay, the calendar is compressed.

1. **Build (2026-08-26/27).** Everything in `current-task/feature.json`:
   matcher, schema-4 `kalshi` object, prior, book WS, store, session/safe
   mode, paper and live executor, journal schema 6, smoke script, tests.
2. **Demo execution smoke (before the 28th).** Run the explicit round trip
   against the fake-money exchange with the demo key pair. Prove V2
   create/fill/close/cancel, private WS, exact fees, `client_order_id`,
   sqlite idempotency, restart reconciliation, and a final zero position
   delta. Demo validates plumbing, not the strategy or liquidity. Required
   PASS before any production key is configured.
3. **Paper maps (2026-08-28).** First 1–2 live maps with
   `KALSHI_TRADING=paper` and a read-scoped key. This combines the old
   matcher dry-run and paper validation: manually verify every matched
   ticker in `match.json` and Telegram, check miss/ambiguous reasons, watch
   readiness at the first observed model tick, PM/Kalshi mid and prior
   basis, signals, simulated fills, and exact simulated fees. Stop on the
   first false-positive match. No separate weeks-long measurement window
   and no reason-aggregation script; 1–2 maps are inspected by hand.
4. **Production canary.** After clean paper maps the operator sets
   `KALSHI_TRADING=live` with the write-scoped production key: minimum
   valid size, one explicitly observed matched map, proven boot
   reconciliation and local safe mode. Inspect
   orders/fills/fees/position/fence before increasing to
   `base_size_usd = 10`.
5. **Prod live.** Normal configured size. Every teardown and map finish
   cancels and fences strategy-owned Kalshi orders; periodic REST
   reconciliation stays active.

Do not configure a production key before the demo smoke passes and the paper
maps are clean. Do not increase to the normal size until at least one canary
round finishes with no unexplained order, fill, fee, position, or cancel
state.

## Checks

- Matcher: two teams + map 2 → exactly one `KXDOTA2MAP-…-2-…` ticker. Swap
  names. Ambiguous alias or two team/map candidates → none. Pagination cannot
  hide a candidate. Record `event_ticker` and exact YES/NO names.
- Resolve task starts from `select_feed`, not from `discover()`, and does not
  delay `feed.ticks()`, the raw archive, first `match.json`, or PM attach.
- A slow matcher writes `kalshi.reason=pending`, then atomically updates only
  the schema-4 `kalshi` object.
- Kalshi `predict_fair` arguments are Kalshi prior and Kalshi mid only.
- Polymarket `predict_fair` arguments are unchanged.
- Missing Kalshi prior blocks Kalshi buys only.
- Prior selects the latest complete candle ending at/before horn−90; a candle
  after the anchor and an event-aggregated candle are rejected.
- Book snapshot + contiguous deltas build the implied YES/NO pair. One-sided,
  crossed, nonfinite, stale, pre-snapshot, and sequence-gap books never quote.
- Taker haircut at p=0.50, qty=1 is 2 cents (`ceil(0.07*0.25)`). Maker on a
  default series is 0. Maker on a listed series is 1 cent
  (`ceil(0.0175*0.25)`). Fractional-count rounding uses current fixed-point
  exchange rules. Live tape takes the exchange fill fee, not the estimate.
- V2 order mapping covers buy YES, buy NO, sell held YES, and sell held NO.
  Every resting order is post-only. Post-only rejection never downgrades to a
  crossing order.
- No Kalshi daily-loss/exposure halt in tests. Separately, every book/private
  WS/store/executor/reconciliation fault clears targets, blocks new writes, and
  cancels/fences strategy orders without touching PM.
- Startup with an orphan resting order cancels and proves it absent before
  ready. Startup with an unexplained position stays locally halted.
- Timeout after place resolves by REST/client id and never creates a duplicate.
  Duplicate and out-of-order WS/REST fills produce one durable fill/fee/ledger
  update.
- Partial fills update desired remainder and position exactly. Restart between
  intent, REST response, fill persistence, journal write, cancel, and fence is
  covered.
- Schema 4 `match.json` always has `kalshi`. A miss still has the key.
- Schema 3 folders still boot PM and keep Kalshi off. New maps write 4.
- Session schema 1–5 reads missing `venue` as PM. New schema 6 records keep PM
  and Kalshi numeric fields separate and write a durable Kalshi session end.
- Worker with `kalshi.reason=none` still attaches Engine and quotes Polymarket.
- Worker with a ticker places Kalshi paper fills without calling
  `engine.gateway`.
- Initial resolve may already be in flight before a game clock exists.
  `second >= 540` discards it and starts no Kalshi book/prior/execution HTTP;
  no retry runs.
- First search miss, later/already `IN_PROGRESS` → exactly one retry, including
  when the first feed event is `IN_PROGRESS`.
- Demo smoke proves create → fill → reduce-only close → cancel/fence → REST
  reconcile and a zero position delta, or fails closed with a visible leftover.
- Teardown cancels/fences Kalshi first and still runs PM `cancel_all` if the
  Kalshi fence fails.
- `make lint-all` on every new Python file.
