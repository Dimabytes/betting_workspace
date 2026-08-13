# dota_2_model

## Setup

- Project path: `../dota_2_model` (sibling of `betting_workspace/`)
- Own full pipeline: collect data → datasets → LightGBM price-delta model → maker backtest. Not a fork.
- Predicts the Polymarket midpoint 300 seconds ahead during the first ten minutes of a Dota 2 map. STRATZ / OpenDota / GRID / Polymarket.
- Python 3.13+, `uv`, `make install` / `make help`. Run scripts with `uv run python` or `make run F=<script>`.
- Does not place live orders. Execution lives in sibling `poly-maker`.
- Backtests go through sibling `prediction-market-backtesting` (read-only source checkout) plus `nautilus_trader` as a package.

## Agent rules

- Python 3.13+. No `from __future__ import annotations`.
- Type-check: basedpyright strict (`pyrightconfig.json`).
- After editing any Python file, run `make lint-all` and fix everything it
  reports before reporting the work as done.
- Code in `src/collect` and `src/live_dashboard` is old and shitty. Don't use it as a reference.
- Prefer named intermediate variables over nested one-liners; keep steps readable.
- Every new/updated function gets a one- or two-line docstring saying what it does.
- Work on `main` by default, unless otherwise specified.
- `__init__.py` files not needed.
- Function names should be action verbs.
- No `dict[str, Any]` for our own data: frozen dataclass for internal values,
  TypedDict in `src/shared/types` only for JSON/parquet shapes we read.
- No anonymous `tuple[A, B]` (or longer) for our own multi-field values: use a
  frozen dataclass with named fields.
- All imports at module top. No imports inside functions.
- Load once, pass the result as an argument. Never call the same loader twice.
- Before writing a reader/path/parser, grep `src/shared` — it is probably there.
- No branches for inputs that cannot occur today. Delete the dead one.
- Always put decision drivers in commit messages.
- When you create functions don't use optional arguments. Make all arguments required. Of course there are exceptions to this rule (but like 1%).
- Prefer a short sequence of plain steps and a few flat `if`s (or early exit) over nested conditions, inline `a if x else b if y else z`, and “parse then check None then check again” ladders. If a block needs a second nesting level, flatten it: pull the branch out, use an early `raise`/`return`, or a small helper.

## Domain learnings

Hard-won facts. Read before touching data/timing code. Moved here from `dota_2_model/docs/learnings.md`.

### Match start time: everything is anchored to the horn

The horn (creep spawn, game clock 0) is the only valid time anchor. Two traps:

- **OpenDota `start_time` is lobby/draft start, not the horn.** Draft runs
  ~8-14 min and varies, so `start_time + match_second` lands ~10 min early.
  No constant offset fixes it.
- **GRID `game.startedAt` is map load, clock at -1:30 — the horn is exactly
  90s later.** Validated on 1311 games: `wall_duration - clock.currentSeconds
  - pauses = 90s` (p5..p95 = 86..91). Using `startedAt` as the horn compared
  market prices ~90s early and inflated backtest edge.

Rule: `horn = grid_game_started_at + 90s + pre-horn pauses`

### A GRID game is found by duration, never by team name

GRID and OpenDota share no id: `gameIdByExternalId(dataProviderName: "STEAM")`
returns nothing and `externalLinks` is empty on both series and games. Team
names are a third spelling space, so matching them needs its own alias table
and still guesses.

They do share an exact number: GRID `game.clock.currentSeconds` **equals** the
OpenDota `duration` of the same game, and `game.startedAt` is that match's
OpenDota `start_time` plus the draft (median 800s, observed 67..1053s over 2318
games). So `04_fetch_grid_starts.py` finds the game by fingerprint:

`|clock.currentSeconds - duration| <= 1s` and `0 <= startedAt - start_time <= 1800s`

On the full cache this is unique for every market that has one — zero ambiguous.
A ±2s tolerance makes 6 games ambiguous, ±0s misses 2. The name matcher it
replaced put ~1% of markets on the wrong game (deltas of ±1h), which silently
compared prices from a different map.

### Unmatched GRID windows are a catalog limit, not a matcher limit

Funnel on 2026-08-06: 3,637 OpenDota links → 2,434 GRID windows (67%). Of the
1,203 misses, 1,079 sit in 28 leagues where the Open Access token sees zero
series — PGL Wallachia all seasons+qualifiers ~421, TI 2026 regional
qualifiers 144, Premier Series 133, Road to EWC 105, FISSURE 72, 1win Essence
69, CIS Battle 49, … Inside leagues the token does see, coverage is already
95.2% (124 misses across 32 leagues, mostly GRID data quality: absent or
never-started game records, clocks off by 2-3s).

- The 922 "exact duration but outside the draft guard" candidates are random
  duration collisions with unrelated games: deltas run hours to months, and
  only 1 link falls in 1800..3600s. Widening the guard adds wrong games, not
  missing ones.
- Live allSeries probes (2025-10-03..05, a heavy-miss day) return the same 12
  series with `PUBLISHED+DELETED` and all explicit `types`; no filter hides
  anything. The catalog is per-token: `api-op.grid.gg` is the Open Access
  portal ("select tournaments" by design). GRID carried PGL data rights
  commercially (DPC 2023), so the missing leagues are a sales conversation,
  not code.
- Anchor alternatives measured and rejected: STRATZ `endDateTime` equals
  `startDateTime + durationSeconds` — derived, same lobby anchor as OpenDota.
  OpenDota `draft_timings` exists for only 368/2,434 matched games (parsed
  replays); the residual `(grid_started - od_start) - draft_sum` has median
  150s with 88% inside ±60s — too coarse for a pre-game anchor without
  leaking in-game prices into the prior.

Ceiling: ~2,455 via fragile in-catalog fixes (not worth the logic); ~85-90%
only if GRID sells the missing leagues; 100% is impossible — TI qualifiers
and community leagues are not on GRID under any package.

### Token-sorted name scoring is an extra probe, never a replacement

`score_team_name` compares each probe to each observed OpenDota name twice: as
written, and with both sides' tokens sorted. The sorted pass is what links
`Cheeki_Breeki` to OpenDota's `Breeki Cheeki` — plain `SequenceMatcher` scores a
word swap at 0.73, under the 0.82 pair floor.

Sorting *instead of* comparing as written is a net loss: measured over the full
universe it drops 12 already-linked events, because sorting reorders strings
that used to align. Taking `max` of both passes gains 13 events.

Measured on 2266 linkable events (universe frozen at 2026-07-24), each step on
top of the previous:

| change | matched | ambiguous | linked maps |
|---|---|---|---|
| before | 1860 | 1 | 3532 |
| token-sorted extra probe | 1873 | 1 | 3558 |
| PM start window 2h -> 4h | 1887 | 3 | 3586 |
| five rename aliases | 1918 | 3 | 3637 |

No previously matched event is lost at any step. 6h was tried and rejected:
`ambiguous` climbs to 18 and `matched` falls back to 1931.

### OpenDota shows only a team's current name, so renames need aliases

The candidate query joins `teams rt ON rt.team_id = m.radiant_team_id`, so the
name is a property of the team id, not of the match — every one of the 2024 team
ids in the cache carries exactly one name. There is no name history to mine and
no way to recognise a rename from the data: matching a series by team id and
reading the old spelling off an earlier match gains exactly zero events.

Renames only come out of `TEAM_ALIASES` by hand. Deriving candidates by taking
series where one side matches and assuming the other side is the counterpart
produces mostly garbage: `winter bear` "maps" to three different opponents that
way, and feeding all 27 auto-derived pairs in pushes `ambiguous` from 3 to 10.
Only pairs that repeat across several dates are safe.

### The pre-game prior comes from paired minute mids, never from the trade tape

Measured on the 2434 GRID windows of 2026-08-06, anchor = `grid_game_started_at`,
after `05a --fetch` filled every window:

| source | markets priced |
|---|---|
| paired minute mid, ≤300s old, skew ≤60s, \|sum−1\| ≤0.05 | **2384 (98.0%)** |
| trade tape, any pre-anchor trade within 60m | 1832 (75%) |
| trade tape, both outcomes within 15m | 1344 (55%) |
| Telonex local capture (book from −2h, secondly) | 2254 markets |

Mid age is p50 33s / p90 56s, so second-level sources buy nothing for a number
that barely moves pre-game. 511 games price off the mid while the tape is empty
or stale, and the tape adds nothing the mid misses. Telonex covers fewer markets
than the mid and costs an 11GB scan — it stays execution data for the backtest.

Freshness is not a knob worth tuning: relaxing 300s → 900s gains 2 markets.
Measuring it on a partly stale cache said 39, which was an artifact of payloads
fetched against an older anchor, not of quiet markets — refetch before concluding
anything about staleness.

This was settled twice before in the opposite direction and reverted: the tape
was the primary prior (3108 accepted), `/prices-history` was added as the
empty-tape fallback and recovered 691 of 782 (3799), and on 2026-07-24 the tape
was deleted outright for `fresh_midpoint` only. Do not re-add it as a fallback
without new numbers.

The residual 50, and what each would cost to recover: 21 have no pre-anchor quote
at all (dead book — the tape has 1 of them), 13 fail skew >60s, 10 fail the sum
tolerance (5 return at tol 0.10), 4 are stale, 2 fail on several counts. Nothing
cheap is left on the table.

### Only the sum tolerance survived as the published prior gate

`05a` publishes `radiant_prior` and rejects a market on one condition:
`|radiant_price + dire_price − 1| > 0.05` (`PAIR_SUM_TOLERANCE`). Two sides that
do not add up are two halves of a book that do not know about each other, so
normalizing that pair invents a price: `8515536844` quotes 0.615/0.655, which
becomes a fabricated 0.4843 "coin flip". 12 of 2434 windows go this way, leaving
2401 published.

The other two conditions in the table above are deliberately **not** applied
downstream, because on the same data they reject good rows:

- `skew ≤ 60s` drops 20 markets whose only fault is quote timestamps 61–81s
  apart, e.g. `8769581167` at 0.495/0.505 (skew 81s) — a clean pair.
- `age ≤ 300s` drops 4, of which 3 sum to exactly 1.000 at ages 448s / 603s /
  4181s — a quiet consistent book, not a broken one, and consistent with
  "freshness is not a knob worth tuning" above.

Both are subsumed by the sum check: a stale or skewed pair that still sums to 1
is fine, and one that does not is already rejected. Do not re-add them without
new numbers.

### The model predicts a 300-second market-price delta

The live paired midpoint, `market_p_radiant`, is the last LightGBM feature.
`market_radiant_prior` remains in the dataset for audit and pregame gating, but
it is neither a feature nor an `init_score`.

The label is:

```text
target_delta = signal_market_p_radiant_300s - market_p_radiant
```

The saved `regression_l1` model emits that price delta. Consumers restore the
future midpoint with `clip(market_p_radiant + predicted_delta, 0, 1)`.
`model_p_radiant` in validation metrics stores this restored future price.

Training rows are minute boundaries `0, 60, ..., 540` inside
`TRAIN_END_SECOND_EXCLUSIVE` (600). Validation dataset rows cover every second
`0..899` (`TRAIN_END + MODEL_TARGET_HORIZON`) so the maker backtest can fair-gate
SELLs after the BUY window; `train_model` still evaluates `0..599`. A missing
future 300-second price removes a row from the 300-second metric but does not
remove it from shorter markouts.

Coverage cost: the dataset keeps only matches with a published prior, so 32 of
2424 usable STRATZ matches drop out. That gate lives in
`prepare_dataset.py:main`, and it propagates for free — the split is derived from
the dataset, so those matches never reach the backtest's validation funnel and
`ValidationCoverage` needs no counter for them.

### The live quote grid is one cent, not the archived Gamma tick

All 328 validation markets carry `orderPriceMinTickSize: 0.001`. That is a
post-close snapshot with `bestBid` at 0.999 — the fine grid exists only in the
extremes. Measured on 187,773 usable validation seconds: 100% of top-of-book
prices lie on 0.01; spread is 1 tick in 51.6%; prices past 0.96 / 0.04 are 0.27%.

- Place our orders on 0.01. It is a multiple of 0.001, so always legal.
- Never read the trading tick from `orderPriceMinTickSize` or assert it is 0.01.
- Keep instrument `price_precision` at 3 — archived L2 delta ingestion needs it.
- An off-grid book price is not a fidelity error. Log it, do not raise.

### The paired tokens are exact mirrors, and so is the trade tape

`radiant_mid + dire_mid` is 1.000 at every percentile. The Telonex `trades`
channel stores each trade twice, once per `asset_id`: same `trade_id` and
`size`, complementary `price`, opposite `side`. `side` is the taker's — `sell`
prints on the bid 78% of the time, `buy` on the ask 82%.

So SELL Radiant at `p` and BUY Dire at `1 - p` are one CLOB level. Quoting both
is one order live and two in replay: each Nautilus instrument has its own
matching engine, so the same trade fills both and liquidity counts twice.

`queue_position=True` fills limit orders only after the quantity ahead at
placement is traded through, and requires `trade_execution=True` — hence
`telonex_local.py` treats the `trades` channel as required.

### Nautilus `get_avg_px_for_quantity` does not confirm fillable size

`get_avg_px_for_quantity(q)` averages only the depth that exists and still
returns a price when less than `q` is available (e.g. 1@0.20 → avg for 5 is
0.20). Confirm fillable size with `sum(l.size() for l in book.asks())`.

### Keep settlement compatibility local to this repository

The stock `prediction-market-backtesting` revision pinned by this project is
`c76e77af00ef53472a9da8f66dae7fdd2d3e5928`. It parses Polymarket's date-only
`endDateIso` as instrument expiration and does not advance the Nautilus clock
through an empty replay tail. For hold-to-resolution, we need expiration from
`closedTime` and a clock event at `replay_end`.

Local commit `9ba2e90` demonstrated both fixes in the framework repository, but
it is not pushed or upstream and must not become an implicit dependency. The
local-only replacement is:

- a local experiment wrapper that copies `closedTime` into each loaded
  instrument's `expiration_ns` and appends one neutral Nautilus custom-data
  boundary at `replay_end`, so an empty post-game tail reaches settlement;
- regression tests in this repository for exact expiration, empty-tail clock
  advancement, and `settlement_pnl_applied`.

The sibling framework checkout is restored to its pinned revision and must stay
clean. Do not open an upstream PR unless this decision is explicitly revisited.

### Nautilus in-flight latency only releases on a visited timestamp

Nautilus `LatencyModel` heappushes `SubmitOrder` with release key
`command.ts_init + insert_latency_nanos`, but `_drain_commands` runs only from
`_process_and_settle_venues`, which fires per data-event timestamp, per distinct
timer timestamp inside `_advance_time` (`engine.pyx:1743/1771`), and once after
the loop. Nothing wakes the engine at the release deadline by itself.

So effective latency is `max(modeled, gap to next replay event)` unless a
component-clock time alert forces a visit at exactly that instant. The alert
callback can be a no-op — the visit drains the queue.

`ReplayEndBoundary` makes this worse without the fix: an order submitted at the
last book delta drains at `replay_end`, i.e. fills timestamped at settlement.

### One process can build many engines only if we own the log guard

Nautilus installs its Rust logger exactly once per process. `BacktestEngine`
skips `init_logging()` when `is_logging_initialized()`, but `engine.dispose()`
drops the `LogGuard` the kernel created, which clears that flag — so the second
batch's engine tries to install a logger again and the Rust side panics with
"attempted to set a logger after the logging system was already initialized".

`run.py` calls `init_logging()` itself before the first batch and keeps the
guard alive for the whole run. Side effect: every engine's `LoggingConfig`
level is then ignored, so the level is chosen once at install time.

### The Nautilus force-stop flag is process-global

`is_backtest_force_stop()` / `set_backtest_force_stop()` live as a module global
that engine `reset()` / `dispose()` never clear. A multi-batch validation run
must call `set_backtest_force_stop(False)` before each `backtest.run()`, or one
`AccountBalanceNegative` marks every later batch `terminated_early`.

### Framework instrument results are a trust boundary

`_finalize_replay_results` returns per-instrument dicts. Read them through
`ReplayInstrumentResult` (TypedDict in `src/shared/types/backtest.py`), not
`dict.get(... or 0)`. Validation folds always check `terminated_early`: those
rows stay in `results.parquet` but are excluded from PnL / ROI / drawdown
aggregates and counted separately in `summary.terminated`. Empty results and
two filled legs raise before a silent KeyError or mislabeled row.

### The framework resolves market metadata from live Polymarket, per market

`PolymarketDataLoader.from_market_slug` hits `gamma-api.polymarket.com` and
`clob.polymarket.com` for every replay leg (disk-cached under
`~/.cache/nautilus_trader/polymarket_metadata`, TTL-bound). Books and trades
come off the local Telonex capture, but a validation run still needs those two
hosts reachable — Spanish ISP DNS answers `*.polymarket.com` with a block
address, so the run only works with the VPN on. A market whose metadata cannot
be fetched loads no legs, and the batch stops naming that match.

### Maker backtest zeros engine fees; archived sports terms are an offline audit

Live Gamma `feeSchedule.rate` reaches `instrument.taker_fee`, and
`PolymarketFeeModel.get_commission` then credits a rebate on **any** LIMIT fill
at a rate inferred from bps (~25%), not the archived sports maker rebate
(15%). The maker path therefore sets `taker_fee = 0` in
`rewrite_replay_instrument` so every fill is raw price x quantity, and US-009
owns fee/rebate math in post-processing:

`maker_rebate = 0.15 * 0.05 * qty * p * (1 - p)`.

Those constants match today's archive — measured on demand by
`scripts/check_gamma_trading_terms.py`:

| slice | expected sports terms (incl. delay=1) |
|---|---|
| validation matches | **328 / 328** |
| train matches | **189 / 1,749** |
| linked Dota map markets | 1,678 / 3,789 |
| all archived markets | 42,377 / 67,823 |

Fee fields alone used to match 624 train markets; requiring `seconds_delay = 1`
drops that to 189 — many `sports_fees_v2` markets still carry delay 3. Validation
stays clean at delay 1.

`load_gamma_markets()` / `parse_market` stay permissive on purpose: a strict
refusal inside the shared loader would drop 1,125 of 1,749 training matches on
the next `make prepare` (pre-`sports_fees_v2` markets). Validation is already
clean, so the check is a script that exits 1 on any validation mismatch — run
it before progon 3 on new matches. The audit also requires `seconds_delay = 1`
because replay latency is built from that field.

The taker path and `shared.utils.trading` (`calculate_net_edge`) are gone; the
maker strategy never reads `instrument.taker_fee`. Fee/rebate math stays in
US-009 post-processing only.
