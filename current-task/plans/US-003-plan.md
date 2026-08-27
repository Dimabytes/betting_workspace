# US-003 Implementation Plan

Story: **US-003** — «kalshi_match: резолв тикера, fail closed»

Repo: `/root/work/dota_2_model` on `main`. Matcher only. Do **not** implement anything in `/root/work/poly-maker` (frozen). Do **not** rebuild Docker (US-015). Do **not** wire `WalletHost` / `MatchWorker` / `match.json` (US-004 / US-005). Do **not** import `kalshi` here (US-002 is still the only SDK boundary). Do **not** add fields to `KalshiOpenMarket`.

---

## Binding decisions (feature.json / overlay / US-002 learnings)

Cross-ref `current-task/feature.json` FR-1, US-003 `changes`, Resolved Questions, Non-Goals, and `docs/plans/kalshi-overlay.md` § How matching works.

1. **Join key is `DiscoveredMatch.sides` + `map_number` + `market_kind`.** Radiant/Dire names come from `sides.radiant` / `sides.dire` (Steam/GRID), not from Polymarket `market.outcome_0_name` / `outcome_1_name` / `market.yes_is_radiant`. `grid_series_id` is not a key. Time, tournament, and rules are logged with candidates and do not filter.
2. **Series pick is kind-only.** `map_winner` → `KXDOTA2MAP`. `series_winner` → `KXDOTA2GAME`. `market_kind is None` (legacy handoff) → no HTTP, `reason="none"`, `series_ticker=None`.
3. **Map filter is event-ticker only, fail closed.** For `map_winner`, keep markets whose event-ticker map component equals `discovered.map_number`. For `series_winner`, keep full-match events (no map component). Unparseable event tickers are skipped, not guessed. Do not parse the market-ticker YES suffix as a team.
4. **`orient_outcomes` is the only name matcher.** Import it from `shared.utils.team_names`. Do not parse prose rules. Do not derive the YES team from the ticker suffix. An orientation `None` (including an exact forward/reverse tie) drops that event.
5. **Exactly one oriented ticker, else fail closed.** 0 oriented events → `none`. 2+ oriented events → `ambiguous`. Log `kalshi none` for both. A unique bind whose grid is not proven $0.01 `linear_cent` → `off_grid` (keep ticker/names/structure). `KalshiRestError` → `error`.
6. **`pending` / `cutoff` / `off` are not produced here.** The overlay state machine is `pending` → one of `matched` / `none` / `ambiguous` / `off_grid` / `cutoff` / `error`. This function starts from a finished search. `pending` is the pre-search document (US-004/US-005). `cutoff` is `second >= BUY_CUTOFF_SECOND` in US-005. `off` is `KALSHI_TRADING=off` in US-005. Do not take a game clock argument.
7. **Host-level cache lives on an injectable class, not a module global.** TTL 60s per `series_ticker`, `time.monotonic`. WalletHost (US-005/US-013) will own one instance. Tests construct a fresh one. Do not put the cache on `KalshiRestClient`.
8. **poly-maker is frozen.** Zero file changes there. No new dependency.

---

## Verified live ticker surface (2026-08-26, production GET /markets)

Public `status=open&series_ticker=KXDOTA2MAP|KXDOTA2GAME` (no key). This is the shape the matcher must handle. Overlay's Liquid/VP example is the same event/market split; the wire format is two YES-team markets per event, not one binary with two different subtitles.

| Kind | Series | Event ticker | Market tickers in that event |
|---|---|---|---|
| map_winner | `KXDOTA2MAP` | `KXDOTA2MAP-26AUG281200PCKCPDYN-2` | `…-2-PCKCP`, `…-2-DYN` |
| series_winner | `KXDOTA2GAME` | `KXDOTA2GAME-26AUG281200PCKCPDYN` | `…-PCKCP`, `…-DYN` |

Observed fields on every sampled row:

- `price_level_structure == "linear_cent"`
- `price_ranges == [{start: "0.0000", end: "1.0000", step: "0.0100"}]`
- `yes_sub_title == no_sub_title ==` the YES team display name (`"PuckChamp"`, `"DYNASTY"`, …)
- payload `status` is `"active"` (list query is still `status="open"`; US-002 already does that)
- Map number is the **last hyphen-separated token of `event_ticker`**, a base-10 integer (`1`, `2`, …). GAME events have **no** such token (`rsplit("-", 1)[-1]` is the date+teams slug, not digits).

**Why this matters for `orient_outcomes`:** calling `orient_outcomes(yes_sub_title, no_sub_title, radiant, dire)` on a live row is `orient_outcomes("PuckChamp", "PuckChamp", …)`. Forward and reverse totals are equal → `None` → every real map would be `none`. That is not fail-closed, that is a dead matcher.

**Required reading of "orient yes_sub_title / no_sub_title":** the two outcome names for one event are the two markets' `yes_sub_title` values. Group by `event_ticker` first. Then `orient_outcomes(name_a, name_b, radiant, dire)`. Do **not** read the team out of `-PCKCP` / `-DYN`.

A one-market event with distinct `yes_sub_title != no_sub_title` is the overlay's binary shape; still support it (one ticker, those two names). A lone market with `yes_sub_title == no_sub_title` (sibling missing) cannot form a pair → skip that event (fail closed, no suffix guess).

Two complementary YES markets in **one** event collapse to **one** candidate ticker (see Design). Two **events** that both orient → `ambiguous`. Counting each YES-team row as its own candidate would make every live map `ambiguous`.

---

## Current code to reuse (do not reinvent)

| Existing | Reuse how |
|---|---|
| `shared.utils.team_names.orient_outcomes` | Only name matcher. `True` → first name is Radiant. `None` → drop the event. Same helper as `discovery._link_steam_games` / `grid_feed.read_board_sides`. |
| `live_paper.bindings.DiscoveredMatch` | Input type. Key fields: `sides.radiant`, `sides.dire`, `map_number`, `market_kind`. `map_number` is always an `int` (for `series_winner` it is the live current map from Steam/GRID, **not** a Kalshi map filter). |
| `live_paper.collector_sidecars.MarketKind` | `"map_winner" \| "series_winner"`. |
| `live_paper.kalshi_client.KalshiRestClient.list_open_markets` | All pages, already. Matcher must not re-paginate or cap the tuple. |
| `KalshiOpenMarket`, `KalshiPriceRange`, `KalshiRestError` | Outward types. Matcher never imports `kalshi`. |
| `shared.utils.log.get_logger` | `logger = get_logger(__name__)`. |
| `tests/test_live_paper_kalshi_client.py` | Fake-client + `asyncio.run` + `cast(Any, sdk)` pattern. Copy that, not Engine session fixtures. |
| `tests/live_paper_discovery_fixtures.make_discovered_match` | Too PM-centric (no `market_kind`). **Do not import** `live_paper_session_fixtures` (pulls `Engine`). Local helper in the new test file. |

`discovery.py` is the fail-closed **pattern** (0 / 2+ name links drop). Do not call it. Kalshi matching does not run inside `MarketDiscovery.discover()`.

---

## Design

### Outward types (`src/live_paper/kalshi_match.py`)

```python
KalshiMatchReason = Literal["matched", "none", "ambiguous", "off_grid", "error"]

@dataclass(frozen=True)
class KalshiBinding:
    """One uniquely oriented Kalshi ticker. Present on matched and off_grid."""

    event_ticker: str
    ticker: str
    series_ticker: str
    yes_outcome: str
    no_outcome: str
    yes_is_radiant: bool
    price_level_structure: str
    price_ranges: tuple[KalshiPriceRange, ...]

@dataclass(frozen=True)
class KalshiResolveResult:
    """Fail-closed resolve. binding is set only for matched and off_grid."""

    reason: KalshiMatchReason
    series_ticker: str | None
    binding: KalshiBinding | None
```

Field names `yes_outcome` / `no_outcome` match US-004's `match.json` object. `price_ranges` stay on the binding (US-004 persists a derived `tick_size` later; this story does not write JSON).

`KalshiMatchReason` is the set **this function returns**. Document `pending` / `cutoff` / `off` in the module docstring as caller-owned. Do not put them on this Literal yet (US-004 owns `match.json` reason including `off`).

### Cache

```python
OPEN_MARKET_CACHE_TTL_SECONDS = 60.0

class KalshiOpenMarketCache:
    """Host-owned open-market list, 60s TTL per series_ticker. Not a module global."""

    def __init__(self, client: KalshiRestClient) -> None: ...
    async def list_open_markets(self, series_ticker: str) -> tuple[KalshiOpenMarket, ...]: ...
```

- Key: `series_ticker` only (one client is one host; demo vs production is the client's problem).
- Hit: `time.monotonic() - fetched_at < OPEN_MARKET_CACHE_TTL_SECONDS`.
- Miss: `await client.list_open_markets(series_ticker)`, store `(fetched_at, rows)`. Empty tuple is a valid cached value. Do **not** cache `KalshiRestError`.
- In-flight coalescing: `dict[str, asyncio.Task[tuple[KalshiOpenMarket, ...]]]`. Two maps in one discovery cycle share one GET. On failure, drop the task so the next caller retries. ponytail: per-series inflight, not a global lock.
- Clock is `time.monotonic` (not `time.time`). Tests monkeypatch `live_paper.kalshi_match.time.monotonic`. Do not add a `clock=` constructor argument (AGENTS.md: no optional args; production would have to pass `time.monotonic` every time).
- No module-level `_CACHE: dict`. A leftover entry from a previous test would leak. Each test builds `KalshiOpenMarketCache(fake_client)`.

US-005/US-013 construct **one** cache on `WalletHost` and pass it into every resolve. This story only creates the class.

### Resolve entry point

```python
async def resolve_kalshi_match(
    discovered: DiscoveredMatch, cache: KalshiOpenMarketCache
) -> KalshiResolveResult:
    """Bind exactly one Kalshi ticker for this map, or fail closed."""
```

No optional args. No `second`. No `grid_series_id` parameter.

### State machine (this story vs later)

```
                    US-005 starts task
                           │
                        pending          (not returned here)
                           │
              resolve_kalshi_match  ──── this story
                           │
        ┌─────────┬─────────┼──────────┬──────────┐
      matched   none   ambiguous    off_grid    error
        │         │         │          │          │
        │         └─ log "kalshi none"
        │
        └─ US-005 may replace any in-flight result with cutoff
           when second >= BUY_CUTOFF_SECOND
```

Only `none` is retried later (US-005, one `IN_PROGRESS` retry). `ambiguous` / `off_grid` / `error` are terminal for that map.

### Algorithm (flat steps)

1. If `discovered.market_kind is None`: log `kalshi none`, return `KalshiResolveResult("none", None, None)`.
2. `series = "KXDOTA2MAP"` if `map_winner` else `"KXDOTA2GAME"`.
3. `try: markets = await cache.list_open_markets(series)` except `KalshiRestError`: log, return `("error", series, None)`.
4. Keep rows that pass the event-ticker filter:
   - `map_winner`: `_map_component(event_ticker) == discovered.map_number`
   - `series_winner`: `_map_component(event_ticker) is None` (full-match event)
5. Group remaining rows by `event_ticker` (plain `dict[str, list[KalshiOpenMarket]]`).
6. For each event, `_bind_event(rows, radiant, dire)` → `KalshiBinding | None`. Collect the non-None bindings.
7. Log every grouped event (tickers + yes/no titles) plus `match_id`, `map_number`, `market_kind`, `tournament`, `grid_series_id` (diagnostic only).
8. `len == 0` → `("none", series, None)` and log **`kalshi none`**.
9. `len >= 2` → `("ambiguous", series, None)` and log **`kalshi none`**.
10. `len == 1`: if `_cent_grid(binding)` → `("matched", series, binding)` else `("off_grid", series, binding)` (ticker/names/structure/ranges kept).

Do not break a 2-event tie with time/tournament/rules.

### Event-ticker map component

```python
def _map_component(event_ticker: str) -> int | None:
    """Last hyphen token as a map number, or None when it is not a base-10 integer."""
```

- `event_ticker.rsplit("-", maxsplit=1)[-1]`
- Accept only `tail.isascii() and tail.isdigit()` (reject empty, signs, unicode digits).
- `int(tail)` is the map number. Live samples: `…PCKCPDYN-2` → `2`. GAME: `…PCKCPDYN` → `None`.
- No prefix check on `KXDOTA2MAP`. No parse of the date/team slug. If the tail is not an integer, the event is not a map event.

**Assumption (explicit):** Kalshi keeps encoding map N as the last `-{N}` on `KXDOTA2MAP` event tickers, and `KXDOTA2GAME` event tickers never end in `-{integer}`. If GAME ever grows a numeric tail, series_winner would drop those events (`none`). If MAP ever uses `MAP2` / `G1` instead of `2`, those events skip (`none`). Fail closed; do not add a second parser until dry-run shows it.

### Orient one event → at most one ticker

```python
def _bind_event(
    rows: list[KalshiOpenMarket], radiant: str, dire: str
) -> KalshiBinding | None:
    """Orient one event's titles against Radiant/Dire. None if the pair cannot bind uniquely."""
```

Sort `rows` by `ticker` so the bound ticker is deterministic (not list-page order).

**Two markets:** `orient_outcomes(a.yes_sub_title, b.yes_sub_title, radiant, dire)`.

- `None` → skip event (includes identical names / score tie).
- `True` → bind **`a`** (lexicographically first ticker). `yes_is_radiant=True`. `yes_outcome=a.yes_sub_title`. `no_outcome=b.yes_sub_title` (sibling YES name, **not** `a.no_sub_title`, which equals `a.yes_sub_title` on the live wire).
- `False` → bind **`a`**. `yes_is_radiant=False`. Same outcome fields.

Name-swap of Radiant/Dire then flips `yes_is_radiant` on the **same** ticker. That is the story's swap case.

**One market and `yes_sub_title != no_sub_title`:** `orient_outcomes(yes, no, radiant, dire)` on that row. Bind it. This is the overlay binary shape.

**Any other shape** (0, 1 with equal titles, 3+): return `None`. Do not look at the ticker suffix to invent the missing team.

`series_ticker` / `price_level_structure` / `price_ranges` on the binding come from the **bound** market row (the ticker we will quote).

### Cent grid (off_grid)

```python
_CENT_STEP = Decimal("0.01")

def _cent_grid(binding: KalshiBinding) -> bool:
    """True only for linear_cent with every band step exactly $0.01."""
```

- `price_level_structure != "linear_cent"` → False.
- `price_ranges` empty → False.
- Any `band.step != Decimal("0.01")` → False. `Decimal("0.0100") == Decimal("0.01")` is True (live wire).
- Do not require `[0, 1]` span. Overlay asks to **prove** a $0.01 step, not to re-derive a scalar `tick_size` (US-004 will stringify that later).

### Logging

`logger = get_logger(__name__)`. Fail-closed 0 / 2+ / orientation miss: **`kalshi none`** in the message (story + overlay wording). Include `reason=none|ambiguous`, `match=`, `series=`, compact `candidates=` (event + tickers + titles). `tournament=` and `grid_series_id=` may be `None`; still print, do not filter on them.

`off_grid` / `error` / `matched`: separate lines (`kalshi off_grid` / `kalshi error` / `kalshi matched`). Do not use `kalshi none` for a unique off_grid bind.

Never log PEM, keys, or SDK models.

### What this story does not touch

- `wallet_host.py`, `match_worker.py`, `match_meta.py`, `kalshi_client.py`, `kalshi_config.py`, `dota-map.toml`
- Retry-on-`IN_PROGRESS`, BUY_CUTOFF, Telegram bind (US-005)
- Auth REST / WS (US-006)

---

## Ordered implementation steps

Work in `/root/work/dota_2_model`. One story, smallest diffs, no new abstractions.

### Step 1 — Matcher module

1. Create `src/live_paper/kalshi_match.py` as designed. All imports at module top. No `import kalshi` / `from kalshi`. One-/two-line docstrings on the public types and functions.
2. Keep helpers private (`_map_component`, `_bind_event`, `_cent_grid`, `_series_for_kind`). Two-branch kind → series, not a lookup table of strategies.

### Step 2 — Tests

1. Create `tests/test_live_paper_kalshi_match.py` (cases below).
2. Build `KalshiOpenMarket` rows in-process (no SDK models). Fake client: one `async def list_open_markets(self, series_ticker: str)` that records calls and returns a scripted tuple. Wrap with the real `KalshiOpenMarketCache`. `cast(Any, fake)` into the cache constructor like US-002.
3. Drive async with `asyncio.run`.
4. Local `DiscoveredMatch` helper: `TeamSides`, `map_number`, `market_kind`, dummy `MarketReference` (condition ids unused). Default teams `"PuckChamp"` / `"DYNASTY"` so titles match live samples without aliases.

### Step 3 — Quality gate

See Verification. `git add` the new files before `make lint-all` (pre-commit skips untracked).

### Step 4 — Bookkeeping (after green, implement-step skill)

- `dota_2_model` commit on `main`. Message focuses on why: fail-closed Kalshi ticker bind from names+map+kind so we never quote the wrong event.
- Set `userStories` US-003 `passes: true` in `betting_workspace/current-task/feature.json`.
- Append `current-task/progress.txt` and `learnings.txt` (two-YES-market event, map component = last hyphen integer, cache is a class not a global, `no_outcome` is the sibling title).
- Do not commit `/root/work/poly-maker`. Do not docker compose.

---

## Test plan

### New: `tests/test_live_paper_kalshi_match.py`

Happy-path fixtures use the **live two-YES** shape (`yes_sub_title == no_sub_title`). Include one binary `yes != no` row only if needed to lock that branch.

| Test | Setup | Expect |
|---|---|---|
| two teams + map 2 → one ticker | `market_kind=map_winner`, `map_number=2`, sides PuckChamp/DYNASTY; open list has map-1 pair + map-2 pair (`…-2-PCKCP` and `…-2-DYN`) plus an unrelated event | `reason=="matched"`, exactly the lex-smaller map-2 ticker, `event_ticker` ends with `-2`, `series_ticker=="KXDOTA2MAP"`, `yes_outcome`/`no_outcome` are the two titles, `yes_is_radiant` matches the bound ticker vs Radiant |
| name swap | same Kalshi rows; swap `sides.radiant`/`sides.dire` | **same** `ticker`; `yes_is_radiant` flipped |
| ambiguous on two candidates | two different map-2 events that both orient (e.g. two time slugs, same titles) | `reason=="ambiguous"`, `binding is None`; log contains `kalshi none` |
| candidate on last pagination page | dummy map-1 (or other-team) rows first, matching map-2 pair **last** in the tuple | `matched` on that pair; fake `list_open_markets` called once (matcher does not slice) |
| off_grid | unique map-2 pair with `price_level_structure="tapered"` **or** `step=Decimal("0.001")` **or** empty `price_ranges` | `reason=="off_grid"`; `binding.ticker` and names and `price_level_structure` still set |
| map 1 does not bind as map 2 | only `…-1-…` rows | `none`, `kalshi none` |
| series_winner keeps full-match event | `market_kind=series_winner`, `map_number=2` (live current map, must be ignored); GAME event `KXDOTA2GAME-26AUG281200PCKCPDYN` plus a decoy whose event ticker ends in `-2` | `matched` on the GAME event; `series_ticker=="KXDOTA2GAME"`; series arg to cache is `KXDOTA2GAME` |
| lone equal-title market is not a suffix guess | one row, `yes=no="PuckChamp"`, ticker `…-2-DYN` (suffix is the other team) | `none` (do not bind DYNASTY from the suffix) |
| orientation miss | map-2 pair titled `"Team Liquid"` / `"Virtus.pro"` vs sides PuckChamp/DYNASTY | `none`, `kalshi none` |
| binary yes≠no still works | single map-2 market, `yes="PuckChamp"`, `no="DYNASTY"` | `matched` on that ticker |
| missing kind | `market_kind=None` | `none`, `series_ticker is None`, **zero** client calls |
| REST error | fake raises `KalshiRestError` | `reason=="error"`, `binding is None`, `series_ticker=="KXDOTA2MAP"` |
| cache TTL 60s | two `resolve_kalshi_match` calls; monotonic 0 then 59 then 60 | 1 client call before 60s, 2nd call at t=60 |
| inflight coalescing | two concurrent resolves, client `list_open_markets` awaits a gate | one client call, both results match |
| grid_series_id is not a key | same names/map/kind, `market.grid_series_id="999"` vs `None` | both `matched` to the same ticker |
| PM yes_is_radiant is ignored | `market.yes_is_radiant=False` while Kalshi bound YES is Radiant | `binding.yes_is_radiant is True` |
| import fence | read `src/live_paper/kalshi_match.py` | no `import kalshi` / `from kalshi` |

Caplog: at least the `none` / `ambiguous` cases assert `"kalshi none"` in `record.getMessage()`.

No browser. No live Kalshi HTTP in tests. Do not add `kalshi` imports to production files.

The five story-named cases are the first five rows; the rest are cheap locks on fail-closed edges the story named in prose (kind, suffix, cache, series_winner).

---

## Verification

Run inside `/root/work/dota_2_model`:

```bash
make test
git add src/live_paper/kalshi_match.py tests/test_live_paper_kalshi_match.py
make lint-all
```

Exact meaning:

- `make test` → `PYTHONPATH=src:scripts:../prediction-market-backtesting uv run --group backtest python -m pytest`
- `make lint-all` → `uv run python -m pre_commit run --all-files` (ruff check/format + basedpyright strict). New untracked files are skipped until `git add`.

Both must be clean. A local `pytest tests/test_live_paper_kalshi_match.py` is the right first check, not the only gate.

Do **not** run `make install` as a bare `uv sync` (US-001 learning: drops nautilus). No new dependency.

---

## Risks / open points

1. **Event-ticker map encoding.** Plan treats the last hyphen token of `event_ticker` as map N when it is ASCII digits. Confirmed on production 2026-08-26 (`…PCKCPDYN-2`). GAME events have no numeric tail. If Kalshi changes this, matcher fail-closes to `none` rather than guessing. Do not parse `26AUG281200` as time.
2. **Two YES-team markets per event.** Overlay text assumed one binary ticker with two subtitles. Live API duplicates the YES name onto `no_sub_title` and lists both teams as separate markets. Grouping by `event_ticker` is required; treating each row as a candidate would make every map `ambiguous`. Bound `no_outcome` is the **sibling** `yes_sub_title`. US-012 will quote YES/NO on the **one** bound ticker (NO = the other team on a two-team map). If Kalshi later makes those two markets non-complementary, fail closed is still correct.
3. **Lexicographic ticker pick inside an event.** We bind the sorted-first ticker so name-swap flips `yes_is_radiant` without changing `ticker`. US-007 already wants a `yes_is_radiant=False` path. Do not always re-bind to the Radiant team's ticker (that would freeze the flag at `True`).
4. **`series_winner` `map_number` is the live current map**, not Kalshi's map index. Using it as a GAME event filter would drop every real GAME event. The full-match rule is "no map component", not "map component == discovered.map_number".
5. **KalshiOpenMarket has no close_time / rules / tournament.** Log `DiscoveredMatch.tournament` and candidate titles. Do not extend US-002 in this story just to log Kalshi rules text.
6. **Cache is not wired to WalletHost yet.** Intentional (US-005). Tests prove TTL and inflight on a locally owned instance.
7. **`status=open` vs payload `active`.** Already handled by `list_open_markets`. Matcher does not re-filter status.
8. **Do not enable a second schedule/tournament linker** when two events collide. `ambiguous` is the product.

No Figma. No poly-maker patch. No new dependency.
