# dota_2_model

## Setup

- Project path: `../dota_2_model` (sibling of `betting_workspace/`)
- Own full pipeline: collect data → datasets → LightGBM price-delta model → maker backtest. Not a fork.
- Predicts the Polymarket midpoint 300 seconds ahead during the first ten minutes of a Dota 2 map. STRATZ / OpenDota / GRID / Polymarket.
- Python 3.13+, `uv`, `make install` / `make help`. Run scripts with `uv run python` or `make run F=<script>`.
- After a folder move, `.venv/bin/*` shebangs still point at the old path.
  `uv run pre-commit` and `uv run pytest` then fail with `No such file or directory`
  even though the files exist. Recreate scripts with `uv sync --reinstall`.
  `make lint-all` and `make test` call `uv run python -m pre_commit` /
  `uv run python -m pytest`, which do not use those shebangs.
- Does not place live orders. Execution lives in sibling `poly-maker`.
- Backtests go through sibling `prediction-market-backtesting` (read-only source checkout) plus `nautilus_trader` as a package.

## Agent rules
- `make lint-all` runs `pre-commit run --all-files`, which skips untracked new files: `git add`
  new files and re-run lint before committing, or the commit hook may reformat/fail on them.

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

Read these rules before data, timing, or backtest work.

- Anchor game time to the horn: `horn = grid_game_started_at + 90s + pre-horn pauses`. OpenDota `start_time` is lobby/draft start.
- GRID and OpenDota share no game ID. Match exact `game.clock.currentSeconds == duration` within `0 <= startedAt - start_time <= 1800s`.
- Most unmatched GRID games are absent from the Open Access catalog. Do not widen the 1800-second guard or add name matching.
- Keep the Polymarket series-start window at four hours; six hours increases ambiguous OpenDota links.
- In `score_team_name`, use the maximum original-order and token-sorted scores. OpenDota exposes current names only; add verified renames to `TEAM_ALIASES`.
- Build `radiant_prior` only from paired `/prices-history` minute mids before `grid_game_started_at`. Do not use the trade tape as a fallback.
- Reject a prior only when `|radiant_price + dire_price - 1| > 0.05`. Do not add age or timestamp-skew gates without new measurements.
- The model predicts `signal_market_p_radiant_300s - market_p_radiant`. Restore price with `clip(market_p_radiant + predicted_delta, 0, 1)`.
- `market_p_radiant` is a feature. `market_radiant_prior` is audit and dataset-gate data, not a feature or `init_score`.
- STRATZ `stats.networthPerMinute[k]` is the player's net worth at second `60 * k`. The team lead array is one longer and offset: `sum(radiant npm[k]) - sum(dire npm[k]) == radiantNetworthLeads[k + 1]`. Train reads npm; validation reads playback gold and checks `playback_nw == npm[second // 60]` at every minute. Rank for the seven top-player features is recomputed each row by `(-networth, player_index)`.
- `FEATURE_COLUMNS` is 13 names: the original six plus `top1_nw_adv`, `radiant_top1_nw_ratio`, `dire_top1_nw_ratio`, `radiant_top1_deaths`, `radiant_top2_deaths`, `dire_top1_deaths`, `dire_top2_deaths` (inserted before `market_p_radiant`). Kept after `docs/experiments/top1-player-features.md` (s2 net +41.15 vs +39.24, buy_300s +1.43¢ vs +0.79¢). Live `model_server._build_feature_values` still maps the old six; a 13-feature `current/` loads then every `predict_fair` raises silent `ModelPredictionError`. Do not copy `data/new_model/current/` to the VPS until live gains the features.
- Train on seconds `0, 60, ..., 540` with game at `T` and market at `T+2`. Validation rows are 1 Hz for decision seconds `2..899` with game at `T-2` and market at `T`; evaluate the model on `2..599`. Feature `second` on valid/backtest is the GRID clock (`T-2`); the parquet row key stays decision `T`.
- Backtest emits a model tick every `POLL_INTERVAL_SECONDS` (1); `MAX_SIGNAL_AGE_SECONDS = 1` so the last poll stays usable until the next. Fair between polls is live book + frozen `predicted_delta`. There is no `SIGNAL_LAG_SECONDS`.
- STRATZ `stats.level` may repeat a second (two level-ups in one second). `assert_level_timeline` only rejects time going backwards. 54/1748 train matches and 11/328 valid matches are short by one late level-up after second 899; that is a prepare log, not a drop gate.
- A4 (model `20260814T165239Z` vs `20260813T233731Z`, 327/328 matches, b0+s2): MAE gain 0.233 vs 0.235 cents (overlapping CI). s2 PnL +29.57 / +0.1808% vs old +17.01 / +0.1040%. b0 PnL -67.13 vs old -43.54. s32 was not run. Live go/no-go is not in US-001.
- Require a published prior before dataset inclusion. Derive validation and backtest selection from that gated dataset.
- Quote on the `0.01` grid. Keep instrument `price_precision = 3`; log off-grid archived book prices and continue.
- Radiant and Dire books and trades mirror each other. Do not quote both equivalent levels; Nautilus duplicates fills. Keep trades for queue-position replay.
- `get_avg_px_for_quantity(q)` does not prove `q` is available. Check book depth before assuming full execution.
- Keep the framework checkout at `c76e77af00ef53472a9da8f66dae7fdd2d3e5928` clean. Patch `closedTime` expiration and `replay_end` locally.
- Schedule a clock alert for each order and cancel release. Nautilus drains latency queues only at visited timestamps.
- Keep one process-owned Nautilus log guard. Clear `set_backtest_force_stop(False)` before each batch.
- 1Hz books are local files in `data/raw/telonex/polymarket/`. The Telonex API subscription is dead; do not call it. New days come from polymarket-collector parquet on the VPS (`sun:/var/lib/polymarket-dota-archive/parquet/`). Before a collect that should reach the current date, run `uv run python scripts/sync_collector_parquet.py` first (rsync `--ignore-existing` into the Telonex tree, skips today UTC). Freeze `make collect AS_OF=` to that run's book `max` day (`T23:59:59Z`). `make prepare` skips up to `MAX_VALIDATION_HARD_MISSES` (20) validation matches with a missing book (collector day holes) and logs them; more than 20, or an invalid cache / worker crash, still abort.
- `--shard i/n` writes `shard_{i}of{n}/` under the canonical run dir; `--merge-shards n` concatenates those parquets into the parent checkpoint and writes summary. Merge does not require a parent `manifest.json` — shards-only dirs are valid. Validation backtest is five processes **in parallel**: start `--shard 0/5` … `--shard 4/5` at the same time (never a sequential for-loop — that is one fat process and defeats sharding). When all five exit 0, one `--merge-shards 5`. If `current/` rotated, pass `--name <model>` so the run dir does not reuse an older model's checkpoint.
- B0 fair is book mid and ignores `min_abs_delta`. That gate is S2-only so B0 stays a model-free baseline.
- 30s gold velocity is `|nw[t] - nw[t-30]|`. Missing t-30 is NaN and gates as `missing_nw`, not a fallback to the match's first nw.
- Treat framework result dictionaries as untrusted input. Require both instrument results and exclude `terminated_early` rows from PnL aggregates.
- Replay metadata cache misses call Gamma and CLOB per market. If local DNS blocks Polymarket, run with the VPN.
- Set engine `taker_fee` to zero. Calculate maker rebate only in post-processing: `0.15 * 0.05 * qty * p * (1 - p)`.
- Audit archived validation terms with `scripts/check_gamma_trading_terms.py`. Do not reject old training markets inside shared loaders.

## Steam live feed

Measured 2026-08-14 on league 19719. Watcher: `../dota_2_model/scripts/watch_steam_live.py`.

- Steam is the free live source. Key in `.env` as `STEAM_KEY`, 100k requests/day. `GetRealtimeStats` is fresh on every request; `GetLiveLeagueGames` refreshes every 15s; OpenDota `/live` every 60s.
- The feed runs 1.4-2.3s behind the game server. Derive the lag as `match.start_timestamp + match.timestamp` minus the local clock. Valve does not apply the league `stream_delay_s` to the API.
- The tournament stream runs about 7s behind the game: our feed beats it by 5s, measured on two kills.
- `match.game_time` is the horn clock with no offset: t=2291 read 38:11 and t=2837 read 47:17 on the broadcast. Steam needs no horn anchoring.
- `match.match_id` is null on a live draft and on a dead server. Liveness comes only from `game_state` (7 = disconnect); never branch on `match_id`.
- `match.timestamp` is seconds since `match.start_timestamp` (Unix lobby/server start), not Unix itself. Horn Unix = `start_timestamp + timestamp - game_time`. Treating `timestamp` as Unix writes `horn_at_utc` in 1970.
- Steam team numbers are fixed: 2 = Radiant, 3 = Dire. Resolve sides by `team_number`, never by list order.
- The feed's pause flag is True on the first tick (unknown), then the growth of `timestamp - game_time` vs the previous snapshot; a repeated `game_time` with an equal offset is a duplicate response, not a pause.
- httpx 0.28 `Response.json()` is stdlib `json.loads` on the raw bytes: invalid UTF-8 raises `UnicodeDecodeError`, bad JSON raises `JSONDecodeError` - catch both at the decode boundary.
- Wrong-shaped 2xx JSON passes a TypedDict cast and blows up in the reduction as `KeyError`/`TypeError`/`ValueError` (Steam `level` 31+ from `experience_at_level`); treat all three as one failed tick so a malformed Steam response cannot kill the feed. Draft `level: 0` is 0 XP, not malformed — archiving pre-horn ticks is required.
- Finished is exactly `game_state == 6` (post-game). State 7 still ends with no event. State 8 is team showcase during draft; `>= 6` treats it as post-game, writes a one-tick `final`, and the orchestrator never relaunches. Empty teams on state 6 still yield one header-only finished event so `finalize_match` sees a terminal record.
- After `yield`, measure the sleep from the request-cycle monotonic start on resumption: the consumer's archive/model work between yields counts against the 1 Hz period.
- The server rebuilds the snapshot once per second. A faster poll returns identical bytes. Subtract the request time from the sleep or the cycle drifts to 1.3s and drops every third second.
- A pause freezes `game_time` while `match.timestamp` keeps ticking. Pause length is the growth of `timestamp - game_time`.
- `buildings` carries three `type` values: 0 tower, 1 barracks, 2 ancient. The fountain is not listed. A destroyed building loses its identity and becomes an anonymous stub (`team` 0, `type` 0, `tier` 0, `destroyed` true). Count survivors against 11 towers, 6 barracks and 1 ancient per side. The loser is the side with zero surviving `type` 2 buildings.
- `server_steam_id` comes from `GetTopLiveGame` (top 10 games) or OpenDota `/live`. `GetLiveLeagueGames` does not carry it. GetTopLiveGame `match_id` is a decimal JSON string (`"8946860406"`), not an int — `require_int` drops every top-live row. Discovery reads int or ASCII decimal string. Draft still has a server id (`game_time` negative); a miss is a parse miss, not "still in draft".
- `graph_data.graph_gold` is a fixed 128-point downsample of the whole match. Use it for late-join backfill, not as a time series.
- Players carry `level`, not XP. STRATZ `radiantExperienceLeads` has no exact Steam equivalent. `xp_per_min` lives in `GetLiveLeagueGames`, together with Roshan and respawn timers.
- `GetMatchDetails` is dead: 500 on recent match ids, empty `{}` on old ones. Take the winner from the last snapshot's destroyed ancient, or keep OpenDota for `radiant_win`.
- Steam has no history. It records forward only; past matches stay with STRATZ and OpenDota.
- One snapshot is 16.6 KB, 3.6 KB gzipped. 1 Hz on a 40-minute match is 2400 requests. The daily budget is 27 hours of tracked game time; tracking only seconds 0..599 costs 600 requests per match.

## Live paper daemon

- `src/live_paper/` is a namespace package (no `__init__.py`), on PYTHONPATH via src/.
- Telegram: `notify.send_telegram_message(message)` reads TG_BOT_API_TOKEN / TG_CHAT_ID
  (same env names as polymarket-collector), never raises, and keeps the token-bearing URL
  out of logs (httpx INFO suppressed, status/type-only logging). `notify.notify_in_background`
  delivers that send on a daemon thread so Steam retries and discovery cycles are not stalled.
  Pytest must never call the real Bot API: `tests/conftest.py` autouse-stubs
  `notify.send_telegram_message` (skip only `test_live_paper_notify.py`, which mocks
  httpx). `notify_now` looks up `send_telegram_message` at call time, so that same
  stub covers the session-finished path. Patch `notify_in_background` at the importer
  (`session`, `discovery`, …) when the test asserts alert text — callers bind that
  name at import time. Session-finished text is `session.notify_now`.
- Steam keys: `steam_client.load_steam_keys()` reads STEAM_KEYS (comma-separated) with
  legacy STEAM_KEY fallback; `SteamClient.get(url, params)` injects the current key with
  `follow_redirects=False` (the key is in the query string) and rotates one key forward on
  HTTP 429/403, Telegram-alerting once per response with status + 1-based ordinal. No
  persistence: restart = key 1 again. Callers that want failed ticks use `get_ok` +
  `decode_json` (JSON `null` is a successful decode of None, not `JSON_FAILED`).
- No `from __future__ import annotations` anywhere: a classmethod returning its own class
  must annotate with `typing.Self` (a plain class-name annotation raises NameError).
- httpx logs the full request URL at INFO; any module that puts secrets in URLs must
  silence the `httpx` logger (watch_steam_live does `logging.getLogger("httpx")` ->
  WARNING, notify does it internally).

- `state.jsonl` archive record (US-005): `{request_started_at_utc, received_at_utc,
  payload}` — the raw decoded GetRealtimeStats object plus the two process wall-clock
  stamps captured in the feed (request start, response receipt), never write-time.
- `StateWriter` appends one fsynced compact JSON line per feed event and on reopen
  truncates only an unterminated crash tail after the last newline; completed records
  are never parsed or rewritten. The writer never reads `payload["match"]["match_id"]`
  (null in draft/live); the match id comes from the discovery/session boundary.

- `StateWriter` validates `match_id` before touching the filesystem: one relative path
  component only (ValueError for empty/dot/absolute/separator ids), because
  `Path(root) / absolute_id` silently replaces the archive root. Canonical ids are ASCII
  decimal Steam match ids. Per record the writer emits exactly one compact LF-terminated
  UTF-8 JSON line, then write -> flush -> fsync in that order; all three error types
  propagate (a lost snapshot must not be silent).

- The match_id path guard lives once in `live_paper.archive_paths`
  (`validate_match_id`, `match_archive_dir(root, match_id)`); StateWriter and match_meta
  both resolve their directory through it, so `state.jsonl` and `match.json` can only land
  in `data/live_paper/<match_id>/`.
- `match.json` v1 (US-006): start document (schema_version 1, binding fields, joined_at_second,
  joined_at_utc, horn_at_utc = start_timestamp + timestamp - game_time as UTC-Z, market with string decimals, model name/trained_at, final null)
  replaced at finish with the same fields plus final (duration, winner, pause_seconds,
  missing_seconds, snapshot_count, pnl-or-null). Winner = the side whose ancient still stands
  in the LAST snapshot: 0 Radiant + >=1 Dire surviving ancients => "dire", 0 Dire + >=1
  Radiant => "radiant", anything else null. Writes are temp + fsync + atomic rename + dir
  sync; a reader sees the full start or the full finalized document, never a partial one.
- Finish ordering: close StateWriter before `finalize_match`; the archive must end in a
  terminal snapshot (`game_state == 6`), otherwise final stays null for a restart. Pause total
  = positive adjacent growth of `timestamp - game_time`; missing seconds = positive
  `game_time` jumps minus 1 between consecutive records (duplicates and late joins are not gaps).

- `live_paper.model_server` (US-008) is the one live model boundary:
  `load_current_model()` requires the canonical `data/new_model/current/` pair
  (model.txt + model.json) to be regular files, reads metadata once, validates
  the live contract, constructs one `lgb.Booster` and returns one frozen
  `ModelServer`; the returned object is the cache/pin (no module cache, no
  re-read, no hot-reload — a later load sees a new `current/`, a running object
  keeps its model). The contract gate compares `features` to
  `train_model.train_model.FEATURE_COLUMNS` by ordered list equality (never a
  set), lag/poll to `SOURCE_LAG_SECONDS`/`POLL_INTERVAL_SECONDS` (exact ints,
  bool rejected), and `booster.feature_name()` to the validated metadata list.
  Parse failures raise `ModelLoadError`, contract violations
  `ModelContractError(ModelLoadError)`, both carrying a fixed safe `reason`
  (never a path/JSON/model content). Every failed load logs and alerts exactly
  once via `notify.notify_in_background("live-paper model startup blocked:
  <reason>")` and re-raises; there is no retry loop.
- LightGBM's C++ side prints `[LightGBM] [Fatal] ...` with the model path
  straight to the raw stderr file descriptor (fd 2) before raising; Python
  logging and caplog never see it. Wrap `lgb.Booster(model_file=...)` and
  `booster.predict` calls in the model_server `_SilencedStderr` fd-level
  redirect (dup2 to devnull), or the path leaks into the daemon log. capfd
  captures fd-level writes and can assert the suppression. NEVER redirect
  process fd 2 without a process-wide lock held across the whole
  enter-body-exit window: overlapping redirects save each other's devnull
  fds, one thread's exit restores the real stderr mid-window of another
  thread's native call, and the last exit leaves fd 2 on devnull (stderr
  silently lost). model_server uses an `threading.RLock` held across the
  whole window with unconditional finally release, independent best-effort
  descriptor closes, and a fail-safe for an irrecoverable restore failure:
  one fixed CRITICAL line to the still-open saved stderr channel, the restore
  error raised out of __exit__ (chained to the body exception), and a broken
  flag that makes every later suppression attempt fail closed until process
  restart (closing the devnull descriptor does NOT close fd 2 — dup2 shares
  the open file description). Reuse that class for any future
  native-suppressed call. Also: LightGBM
  failure normalization catches (LightGBMError, OSError, ValueError,
  RuntimeError) at the constructor/predict/feature_name boundaries — the
  constructor can raise OSError on a file race after `is_file`, and
  `feature_name`/`predict` raise LightGBMError on a broken booster state.
- `ModelServer.predict_fair(snapshot, market_p_radiant)` returns the RADIANT
  fair price `clip(market_p_radiant + delta, 0, 1)`: one float64 row `(1, 6)` in
  `FEATURE_COLUMNS` order from validated finite numbers (market in `[0, 1]`),
  plain `booster.predict(row)` (default delta semantics), exactly one finite
  scalar delta required (non-ndarray, wrong shape, and bool/string/object
  dtype outputs are rejected: `dtype.kind` must be f/i/u — `np.asarray(...,
  dtype=float64)` would silently coerce `[True]` to 1.0 and `["0.25"]` to
  0.25). `snapshot.second` goes in as-is (negative allowed,
  float64) — the lag is already inside the trained contract, never subtract
  `SOURCE_LAG_SECONDS` again. No window/pause gates here (US-011 owns the 0..600
  gate) and per-prediction `ModelPredictionError` does no I/O or Telegram.
- `yes_fair_from_model(radiant_fair, yes_is_radiant)` is the only Radiant->YES
  conversion: pure, validates a finite Radiant probability in `[0, 1]` and an
  exact `bool`, returns the value for True and the complement for False; it
  never calls the booster. The US-011 engine FV wrapper closes over the last
  YES fair computed from the mid pair (mid -> market_p_radiant ->
  `predict_fair` -> `yes_fair_from_model`) and falls back to the original
  `compute_fair_value` without a signal.

- Binding types live in `live_paper.bindings`: `DiscoveredMatch` is the discovery output;
  `MatchStart` subclasses it with `model`; US-011 uses `discovered.with_model(model)`.
  `match_meta` only writes JSON. Sidecar scan/validation is `live_paper.collector_sidecars`
  (`load_archive_root`, frozen `FreshSidecar`). `poll_discoveries` lives in
  `live_paper.cadence`; US-012 async-fors it and owns no loop/sleep of its own.
- `live_paper.discovery` (US-007) finds tradable markets per cycle: scan frozen collector-v1
  sidecars (`ARCHIVE_ROOT/metadata/markets/*.json`, mtime >= now - 2h, schemaVersion 1,
  exact scalar types, two distinct canonical outcomes, file name `<conditionId>.json`),
  require `active is True`, `closed is False`, `acceptingOrders is True`,
  `enableOrderBook is True`, then one GetLiveLeagueGames + the four GetTopLiveGame partners
  through the injected SteamClient. The module never writes the external archive, never
  calls OpenDota, and the collector remains the sole Gamma/CLOB producer. ARCHIVE_ROOT is
  a bind mount and intentionally lives outside `shared.constants.paths`.
- Discovery linking: `pick_pair_orientation` in `team_names` (same rule as OpenDota linker);
  exact ties rejected; `yes_is_radiant` = accepted forward. `map_winner` trades when
  mapNumber equals radiant+dire series wins + 1. `series_winner` trades when Steam
  `series_type` is 0/1/2 (Bo1/Bo3/Bo5), the current map equals that best-of (map 1/3/5),
  and no eligible Game-N sidecar exists for the same event (fail-closed; Game N wins).
  BO2 and missing `series_type` never authorize Match Winner. Ambiguities (sidecar -> 2
  games, or match -> 2 conditions) emit nothing; conflicting server ids and conflicting
  live-list rows for one match_id fail closed; missing server id is an info skip.
  Results sort by (numeric match id, condition id).
  Historical OpenDota linker uses the same `series_winner_covers_map` predicate: a
  played last map with no Game N Winner attaches the series condition. A BO5
  event that only lists Game 1/2/3 Winner plus Match Winner links `[1, 2, 3, 5]`:
  map 4 is skipped (no map market). `validate_links` allows unique increasing
  game numbers, not a contiguous 1..N prefix. Do not rewrite Polymarket
  `scheduled_ts` to last-map time; GRID `startedAt` of that game is the horn.
- `poll_discoveries(discovery)` in `live_paper.cadence` is the US-012 cadence seam: async-for
  over it, first result immediate, `asyncio.to_thread` serializes `discover()` (never overlap
  on the rotating client), sleep = max(0, 60 - elapsed from cycle start) including consumer
  time. US-012 owns no loop/sleep of its own.
- Discovery Telegram policy: name-link misses, ambiguities, map mismatch, missing server,
  no live games, malformed sidecars and Steam failures are log-only. Telegram fires once
  when the orchestrator actually spawns a polling child (`live-paper session started:`
  plus public match/sides/map/market/kind/condition ids, never secrets/tokens/paths).
  A crash restart of the same match_id does not send a second start page. Exhaustion
  after MAX_CRASH_RESTARTS still pages once. On Steam post-game the child sends
  `live-paper session finished:` via `notify.notify_now` (same thread, then the
  process exits). `notify_in_background` is a daemon thread: the parent can use it
  because it keeps running, the child cannot — a daemon POST is killed on exit.
  The finished line is realized (`net_cash`), IMV (`inventory_value` at last FV
  mark, leftover is not resolved to 0/1), post-process maker rebate (same formula
  as `make live-report`), net = realized+IMV+rebate, leftover YES/NO sizes.
  Missing engine cash renders as `n/a`. Start/fault alerts stay on
  `notify.notify_in_background`.
- basedpyright strict gotchas when validating JSON: `isinstance(x, T)` is flagged unnecessary
  once a TypedDict `.get()` already returns `T` — use `type(x) is not T` for exact runtime
  checks (also rejects bool-as-int). An `object`-annotated local still leaks its initializer's
  type into isinstance narrowing; route container validation through an object-typed parameter
  or `cast(object, ...)` first, then `cast(list[object], ...)` to iterate.

- `live_paper.paper_gateway` (US-009) is the paper engine gateway:
  `PaperGateway(ExecutionGateway)` with the exact fork constructor
  `(cfg, journal=None, *, paper=False)`; it raises ValueError unless `paper=True` and
  overrides every engine-reachable network read (positions/balances/REST books) to empty
  results, so paper mode performs no live requests even with wallet secrets present.
  The one public seam is the one-time `bind_fill_sink(state, on_fill)` — US-011 composes
  `gateway.bind_fill_sink(engine.state, engine._on_fill)` after `Engine(...)`; rebind or
  pre-bind processing raises RuntimeError. Fill simulation is fed inline from the
  MarketDataService callback path: `process_book_update(token_id, book)` and
  `process_trade_print(trade)` are synchronous, return only applied fills, and must run on
  the engine loop (no locks; a future thread producer must marshal into the loop).
- Paper fill rules: BUY fills only when `best_ask.price < limit` or `trade.price < limit`;
  SELL mirrors with `>`; touch never fills; one full-size maker fill per crossed order at
  the order's own limit price (`trade_id="<order_id>:fill"`, source ts); malformed/zero/
  nonfinite sources and one-sided books fail closed: BOTH the bid and the ask side must
  carry a valid level (finite in-range price, positive finite size) before any book fill, so
  an ask-only or bid-only frame never fills; a SELL bigger than
  `state.position(...).size` stays open (no clamped/short fill). Order ids are
  `paper-<uuid4-hex>-<counter>` (session-namespaced against the persistent fills table),
  always LIVE, and `open_orders()` is never empty after place, so the 20s
  `replace_open_orders` reconcile keeps the orders. Fill ordering: `state.apply_fill`
  first (raise leaves the order live for retry), then the terminal order is dropped even
  on a duplicate trade id, and the callback runs only for a genuinely new durable fill.
- Fork gotchas: `Side` is a str-Enum, so exact-side checks must use identity (`is`), not
  `in` value comparison. RUF003 flags the standalone Russian preposition "с" in comments —
  keep mandated Russian ponytail text with a line-level `# noqa: RUF003`.

(The three US-011/US-012 sections below describe behavior that is still exact.
Module and symbol NAMES in them moved in the structural pass at the end of this
file — read that section for the current layout.)

- `live_paper.session.run_session(discovered, steam_client, archive_root)` (US-011) is
  the one-match session: load the model once, `discovered.with_model(model.model_reference)`,
  archive every feed event to state.jsonl BEFORE any trading code (write_match_start on the
  first event, session.jsonl provenance, StateWriter.write for all events incl. pre-horn/
  pause/>600/terminal), then trade through the paper engine. A model-load/contract failure
  re-raises (US-008 loader alerts once); every post-start trading failure is isolated:
  safe reduce-only + scoped entry-BUY cancel, archive continues. A dead server with no
  event leaves no final metadata; terminal order = safe-gate -> close StateWriter ->
  session_end -> risk snapshot -> engine shutdown + task join -> finalize_match.
- Config materialization: the session reads `config/dota-map.toml` once (tomllib), validates
  exactly [engine]/[risk]/[profiles.dota-map], and writes a session TemporaryDirectory with
  generated config.toml (template tables + session [paths] into the match archive),
  strategy.toml and markets.toml; `Config.load(tempdir, load_env=False)` only ever sees the
  generated directory, never the template. Tuning values flow from the template through a
  tiny typed TOML emitter — no tuning literals in Python.
- The collector is the sole Gamma metadata source. Engine.start() unconditionally calls
  `refresh_market_metadata()` (GammaClient) even with a seeded CatalogStore, so the session
  assigns a per-engine async no-op override before start (instance attribute, not a fork
  edit/global patch) and the sidecar refresh loop owns accepting/closed/min/tick: it rescans
  only ARCHIVE_ROOT/metadata/markets every `cadence.DISCOVERY_POLL_INTERVAL_SECONDS`,
  validates immutable token identity under `engine._locks[cid]`, `dataclasses.replace` the
  live meta tick/min, `catalog.upsert_market`, `set_tick_size` both books, wakes the cid;
  a tick change emits one fsynced tick_size_change record (collector decimal strings) and
  cancels only orders off the new Decimal grid (never cancel_all/cancel_asset). A missing/
  changed/non-tradeable/tick-less sidecar disables buying (safe reduce-only + entry-BUY
  cancel) and is never retried within the session. Never import
  `polymaker.catalog.scanner` or re-match team names in the session.
- The engine module-global lease: patch `polymaker.engine.compute_fair_value` (exact
  (micro, flow_z, tick) adapter returning the precomputed cell YES fair, else the captured
  original fork FV — never predict_fair) and `polymaker.engine.ExecutionGateway` =
  PaperGateway BEFORE `Engine(...)`; restore both only in a finally AFTER
  `await engine.shutdown()` AND `gather(*engine._tasks, *_aux_tasks,
  return_exceptions=True)` (shutdown cancels but does not await tasks). Restore also on
  constructor/start failures. These module globals prohibit concurrent sessions in one
  interpreter — one subprocess owns one session (US-012 invariant).
- REDUCE_ONLY gate: the fork's EVENT priority beats `risk_reduce_only`, so the session
  installs a per-engine `_GatedRegimeMachine` subclass on `engine.regime_m[cid]` after
  start: delegate everything (incl. cooloff), return REDUCE_ONLY when the forced gate is
  true unless the delegate returned genuine HALTED. The forced gate pairs with
  `_cancel_entry_buys` under `engine._locks[cid]`: cancel only Side.BUY state orders,
  remove only successful ids, preserve SELLs (closes the concurrent normal-place race).
  A position below `meta.min_order_size` cannot legally receive a SELL (fork `_maybe_exit`).
- Decision pipeline: read raw best bid/ask from BOTH token books (strict finite
  0 < bid < ask < 1; missing/empty = missing_book, one side = one_sided_book, crossed and
  nonfinite are their own labels — check `book.bids`/`book.asks` BEFORE `is_empty`, which
  treats one-sided as empty); map the raw pair by `yes_is_radiant`, then
  `normalize_pair_mids(tolerance=PAIR_SUM_TOLERANCE)`; only a valid pair in the exact
  window (0 <= second <= 600, not paused, not finished, fresh arrival, usable sidecar)
  calls `predict_fair` + `yes_fair_from_model` (YES=Dire complements exactly once). Every
  other state clears the cell and forces reduce-only; ModelPredictionError is silent,
  unexpected failures write one type-only trading_error + deduped background Telegram.
- MDS fill bridge: wrap only this engine's `md._on_dirty`/`md._on_trade` — the book is
  already applied when _on_dirty fires and the print already parsed when _on_trade fires,
  so call `gateway.process_book_update(token_id, book)` / `process_trade_print(trade)`
  synchronously on the same loop (no locks), journal returned fills, then invoke the
  original callback. A processor/callback exception is caught at the session boundary,
  recorded by type only, flips safe mode and schedules scoped BUY cancellation; it never
  kills MDS/archival and a post-durable-fill callback failure is never replayed.
- Freshness: a generation-guarded `asyncio.call_later(POLL_INTERVAL_SECONDS)` watchdog is
  re-armed per snapshot; on expiry it forces reduce-only, wakes the cid and schedules the
  scoped BUY cancel; stale generations are ignored (cancellation is not trusted without the
  generation check). Every snapshot after engine readiness updates the cell/gate and wakes
  the cid exactly once. The sync 1 Hz feed iterator is advanced via `asyncio.to_thread` with
  a StopIteration sentinel helper so its blocking sleep never freezes engine tasks.
- `session.jsonl` (US-014 input): first record session_start (schema_version, public
  match/condition ids, model name/trained_at, `git rev-parse HEAD` or fixed "unknown";
  resume keeps the original provenance), then signal/quote/fill/tick_size_change/
  trading_error (phase + type only)/session_end (positions + net_cash/inventory_value/
  equity or null). Never config/env/URLs/exception text/secrets/raw Steam payloads or full
  books; the fork's own engine/MDS journal is never copied. SessionJournal is loop-owned
  and shares `archive_paths.truncate_after_last_newline` with StateWriter (both append
  UTF-8 compact LF records with write -> flush -> fsync per line; reopen truncates only an
  unterminated crash tail). finalize_match gets SessionPnl(risk.net_cash,
  risk.inventory_value) only when finite, else None; no resolution PnL/OpenDota.
- First feed event is archived but never traded (engine starts after it) — the first
  in-window decision therefore comes from the second event; trading.start() wakes the cid
  once with the empty already-safe cell.
- US-011 review fixes (Luna P1/P2, Terra remediation): the session's fair/gate cell
  starts FORCED (reduce-only) and only a valid in-window snapshot opens it — an engine
  wake before the first Steam snapshot must never quote entries on the original fork
  FV. A one-event terminal feed finalizes after the first event's archival without
  starting the engine. The fill sink is the session wrapper `_fill_sink_callback`
  (calls engine._on_fill once, swallows callback errors into a type-only fault + safe
  mode), so a durable fill is always journaled exactly once by the bridge; never bind
  raw engine._on_fill. Bridged MDS handlers guard the gateway processing and the
  original callback invocation independently. Failure-prone seams
  (bind/journaling/bridge) live in the async start path: a start failure runs the full
  shutdown (session tasks + engine shutdown + engine-task join + closes + globals +
  tempdir) before re-raising; `_close_engine_resources(engine)` is the one idempotent
  close shared with the constructor-failure path, and shutdown also cancels/joins
  `_scheduled_cancels` so a stuck scoped cancel can never act afterward. The immutable
  sidecar binding is the full frozen identity (event_id/event_slug/condition/slug/kind/
  map/ordered names+tokens/neg_risk/grid_series_id); tick/min are the only dynamic meta
  fields, liveness flags are the no-trade gate, question is display-only; any binding
  change fails closed permanently and the refresh loop stops after the first semantic
  SidecarUnavailable. Both YES/NO in-memory book tick sizes are applied unconditionally
  on every refresh AND right after engine start (fresh OrderBooks carry the fork
  default 0.001). Strict no-Gamma: per-engine instance closures replace BOTH
  Engine.start boundaries — `_resolve_markets` reproduces the native per-market state
  from the seeded CatalogStore without ever constructing GammaClient, and
  `refresh_market_metadata` is a noop; a real-start test with GammaClient raising and
  an idle md.run proves it end to end. SessionJournal writes with allow_nan=False and
  nonfinite raw levels are sanitized to null + reason; readers may parse with
  parse_constant=raise. The dota-map template schema is pinned in the session (exact
  key sets, raw literal types, finite non-negative ranges — zero is a valid lifecycle
  tuning value). Window/stale/pause/finished gates evaluate BEFORE pair diagnostics and
  the books are not read once a gate fired; finite out-of-range levels carry
  out_of_range_pair, nonfinite_pair is strictly nonfinite. Test-harness rules: bound
  "loop must exit" tests with asyncio.wait_for (a removed break fails as TimeoutError,
  never a hang), scope pkill -f patterns to the target binary (the pattern text is
  in the caller's own cmdline), and never let pytest reach the Telegram Bot API
  (`tests/conftest.py` stubs `notify.send_telegram_message`; a session fault test
  that cares about alert text still installs `AlertRecorder` on
  `session.notify_in_background`). `env_value()` falls back to `dota_2_model/.env`
  when the process env is empty, so an unpatched `_FaultReporter` during pytest
  POSTs to the real operator chat — that is not live-paper trading.
- US-011 review round 3: session cleanup is cancellation-proof by construction
  (start catches BaseException and shields the shutdown; shutdown's close/join/
  lease-restore/temp-cleanup live in a pure-sync innermost finally; _start_trading
  and run_session clean partial trading under cancellation). DiscoveredMatch carries
  an explicit immutable market_kind from discovery (kw_only, compare=False so the
  US-006 match.json resume binding check stays untouched): the session requires the
  fresh sidecar kind to equal the handoff before any Engine (map_winner: same map
  number; series_winner: discovered map in {1, 3, 5} + null sidecar map;
  missing/legacy kind fails closed - a Bo1/Bo3/Bo5 decider series session is
  accepted when the kind is carried). The canonical nonsecret sidecar binding is
  pinned into the session.jsonl
  provenance (session_start.sidecar_binding) at first acceptance and re-read on
  resume: a changed static sidecar across a restart is a typed TradingDisabled with
  no Engine; never extend match.json for this. Archive-first ordering: the first
  Steam event reaches state.jsonl before any session-journal setup (a journal fault
  loses no raw event and the archive still finalizes). Both JSONL writers sanitize
  nonfinite floats to null (shared.utils.json_safe) and dump with allow_nan=False -
  corrupt floats never lose a record or emit invalid JSON. Journal-wrapped
  place/cancel never diverge engine state and gateway: a failing quote-journal write
  cancels the just-placed orders, flips safe mode and returns [] (engine quarantine
  path); a failing cancel-journal keeps ok=True. Raw levels require the full
  0 < bid < ask < 1 (zero/negative ask is out_of_range_pair, never crossed_book).
- US-011 review round 4: one canonical serialized key convention for the sidecar
  binding record (writer and reader both use grid_series_id — roundtrip non-null
  values in tests). Fault boundaries are no-raise and compensation-first:
  _FaultReporter.report never propagates journal I/O failures, and the safe gate /
  clear-fair / gateway-state compensation run BEFORE the best-effort
  journal/Telegram reporting in on_event, journaling_place and journaling_cancel, so
  a broken session journal can never leave a stale fair or an untracked live order.
  shutdown()'s entry is fully synchronous before its first await (latch, watchdog
  disarm, refresh-task cancel, snapshot+cancel of _scheduled_cancels) with a
  pure-sync innermost finally for close/lease-restore/temp-cleanup, and
  _cancel_entry_buys is a no-op once shutdown began (tasks ignoring cancellation
  cannot act on closed state). A corrupt session.jsonl provenance is a
  trading-disabled setup fault: close the corrupt journal, alert once type-only,
  keep archiving and finalize. _open_session_journal closes the journal when
  write_start fails. Test tip: asyncio cancels a never-started task without running
  its coroutine — sleep(0) before exercising cancellation-ignoring tasks.
- US-011 review round 5: SessionJournal.first_binding(match_id, condition_id)
  validates the whole immutable session_start root (schema/kind/match/condition/
  model/git_commit) with exact `type(x) is` checks and the nested binding's
  schema_version is exact-int (bool rejected); corrupt provenance is a
  TradingDisabled archive-only fault. The session_end journal write is
  best-effort and finalize_match runs in a finally around trading.shutdown(), so
  a terminal match is always finalized while a shutdown exception propagates
  afterwards. Tick changes run the required off-grid cancellation BEFORE the
  best-effort tick journal record and advance the pinned tick string first. The
  raw range is the full 0 < bid < ask < 1. CRITICAL asyncio rule: never
  wait_for-over-gather for cancellation-driven joins (wait_for cancels the
  gather, and the gather waits for the cancelled children - non-cooperative
  tasks hang it forever); use asyncio.wait with bounded timeouts plus explicit
  two-phase recancels and retrieve done-task exceptions.
- US-011 review round 6: resume-baseline integrity — capture prior-archive
  evidence (match.json or a non-empty state.jsonl) BEFORE any writer opens or
  truncates; a resumed archive whose session.jsonl baseline is missing/empty is
  a TradingDisabled archive-only fault and never writes a new provenance, so a
  changed immutable sidecar can never be re-pinned. Provenance records (root
  session_start and nested sidecar binding) validate exact key sets with
  required-key semantics: nullable fields must be present with explicit null
  (missing grid_series_id / series map_number / root sidecar_binding key are
  corruption), preserving the legitimate fresh start and the initial
  no-sidecar explicit-null provenance.

## US-012 orchestrator (daemon + child-session CLI)

- `live_paper.orchestrator` owns both CLI modes. `daemon`: one
  load_archive_root/client/SteamClient/MarketDiscovery for the process lifetime,
  then `async for` over `cadence.poll_discoveries` — the daemon owns no
  loop/sleep and never reimplements discovery. `session`: the child command
  `sys.executable -m live_paper.orchestrator session --archive-root ... 
  --discovered-json <strict public JSON>`; fresh clients, `asyncio.run(run_session(...))`.
  One subprocess per match keeps the engine module-global lease safe.
- `SessionSupervisor.reconcile(discovered)` is nonblocking and cadence-driven:
  poll (never wait) children, reap exits, accept/reject/launch. Records keyed by
  match_id only; duplicate id tuples and changed static handoffs rejected
  (log-only). Parent creates only data/live_paper/<match_id> via match_archive_dir
  (the one path guard) before Popen(shell=False); the child always gets the
  FROZEN original handoff.
- Completion is durable, never an exit code: matching match.json `final`
  non-null (checked at exit AND before every launch). Code 0 without a final is
  a crash/retry; nonzero with a final succeeds. Never kill a live child when a
  final appears — reap after exit. New match_id (next map) = independent record.
- Crash policy: MAX_CRASH_RESTARTS=3, backoff (60,120,240) on an injected
  monotonic clock, only on discovery cycles; failures retained through temporary
  absence; the fourth unresolved failure tombstones + exactly one
  public/type-only notify_in_background alert. Direct termination only on daemon
  cancellation (terminate_all: SIGTERM, bounded wait, SIGKILL stragglers).
- Cross-cycle static identity EXCLUDES dynamic tick_size/min_order_size
  (US-011 re-reads them live): a tick/min change must neither suppress a crash
  restart nor conflict a running record. Static = server/league/sides/map,
  condition/market/event slugs, ordered tokens, polarity, neg_risk,
  grid_series_id, explicit market_kind.
- Handoff TypedDicts in shared/types/live_paper.py (DiscoveredMatchHandoff/
  Sides/Market): strict decode = exact key sets + exact scalar types (type(x)
  is checks, bool-as-int rejected, explicit nulls). `subprocess.Popen`
  satisfies a small ChildProcess Protocol so supervisor tests use plain fakes.

## US-012 review fixes (commit 764b317)

- The durable-final marker is identity-matching: only a strict JSON object
  with an exact-string `match_id == requested_id` and a non-null `final`
  completes. OSError/UnicodeError/JSONDecodeError/RecursionError reads return
  false (never raise), so a corrupt, foreign-id or invalid-UTF-8 match.json
  never suppresses a launch or a backoff retry; `match_archive_dir` ValueError
  for an invalid id still propagates as the path guard.
- Every archive mkdir/spawn OSError and every child poll OSError is one
  bounded unresolved failure: the frozen record/identity is retained, the
  attempt counter increments exactly once, the standard 60/120/240 backoff
  and MAX_CRASH_RESTARTS cap apply, and the same one public type-only
  exhaustion alert fires. A poll OSError first stops the child best-effort
  (terminate -> bounded wait -> kill) so it can never become an unobserved
  orphan.
- `_stop_child` is the one isolated stop helper: terminate, bounded wait
  (TERMINATE_WAIT_SECONDS), SIGKILL only when the wait actually timed out —
  a pid that cannot be signalled or waited on may have been recycled and must
  never be killed. Every terminate/wait/kill OSError/ProcessLookupError is
  contained per step, all supervisor records are still cleared, and one
  failing child can never block the cleanup of the rest or mask the daemon
  cancellation.
- The production launcher spells `shell=False` explicitly (Popen default,
  now pinned and test-asserted).

## US-012 review fixes (commit 965c0da)

- Luna P1: after `child.poll()` raises OSError the old reap path stopped the
  child but unconditionally cleared process/retries, so a same-match
  replacement could launch while the original might still be alive;
  `terminate_all` had the same false-dead clearing. Rule: `_stop_child`
  reports whether the death is PROVEN (reaped wait, ProcessLookupError at any
  terminate/wait/kill/poll step = gone pid — never signal it again, it may be
  recycled; delivered SIGKILL). A generic OSError leaves liveness unknown and
  the handle must be kept.
- An unproven stop retains the process handle (the `quarantined` FIELD is gone;
  a non-null `record.process` is the retained-handle fact — see the structural
  pass section): reconcile never spawns/rebaselines while
  unknown (the retained-handle guard in `_handle_match`), later cadences
  and `terminate_all` continue bounded best-effort stop/re-observation, and
  each poll OSError still counts one unresolved failure on the 60/120/240
  cap with exactly one public alert. `_register_unresolved_failure` early-
  returns on exhausted records (never recount, never a second alert).
- Coherent resolution: an eventually observable exit, a later proven stop or
  a poll ProcessLookupError clears the quarantine and resolves through the
  durable match.json final — final completes, otherwise one crash failure
  and at most one replacement, only after the proven death. Static identity
  stays frozen and changed handoffs stay rejected while quarantined.
- `terminate_all` isolates each child, clears only proven deaths and retries
  retained unknown handles on every later call; it cannot promise to kill a
  process its permissions forbid, but never treats it as dead. In-memory
  records (incl. quarantined handles) live for the daemon lifetime; the
  durable final is the only cross-restart completion evidence. Lifecycle:
  running -> proven death -> final/backoff -> relaunch ... cap -> tombstone
  (one alert, no restarts); unknown liveness -> quarantine -> retried stop/
  re-observation -> proven death -> normal resolution.
- Test seam pinning now also covers `SessionSupervisor._matches` records
  (process/quarantined/attempts) — update the header comment when moving
  those seams. Mutation gate: 5 targeted mutations (exit state clearing,
  process/quarantine launch guard, quarantine retention, terminate_all
  clearing, exhausted alert-once guard) each fail dedicated tests
  (14/3/7/6/1 failures respectively).

## US-012 review fixes (commit f7a92d7)

- Luna P1: `process.kill()` returning without an exception is
  only a delivered SIGKILL, not a reaped death — `_stop_child` returned True
  right after the kill call, so a poll-faulted record was cleared and a
  replacement could launch while the original child was still alive. The
  kill step now reaps the death with a bounded post-kill wait
  (`KILL_WAIT_SECONDS`, same 5.0 bound as the SIGTERM wait): a reaped wait
  or a ProcessLookupError (gone pid — never signalled again) proves the
  death; TimeoutExpired or a generic OSError returns False/unknown, the
  quarantined handle is retained and later cadences/terminate_all retry the
  stop — the existing one-failure-per-fault counting and the frozen
  identity are untouched (no duplicate failure, no rebaseline).
- Test fake: FakeChild `wait_outcomes` scripts one outcome per wait call
  ("timeout"/"oserror"/"lookup"/"exit", then flat flags); every outcome
  raises/returns immediately so no test can hang. The pinned composition is
  poll OSError -> terminate ok -> wait TimeoutExpired -> kill ok (child
  still alive) -> post-kill wait TimeoutExpired/OSError => one
  child/record retained, no replacement through backoff, exactly one
  replacement after the post-kill wait finally reaps the death.
  terminate_all coverage: post-kill unknown retains the handle and the
  second call retries it. Mutation gate: deleting the post-kill wait block
  fails the 6 post-kill tests.

## Thermos review of US-009..US-012 (structural pass)

This pass changed no behavior. `make test` stayed green at every step and the
five-mutation gate below still fails a test each. Read it before touching any
`live_paper` module: several names and file locations from the sections above
moved.

- `live_paper.session` is six modules now, none over 900 lines (it was one
  2403-line file):
  `session_types` (value types only: `SignalReason`, `TradingDisabled`/
  `SidecarUnavailable`, `RawBookPair`, `SignalDecision`, `ModelFair`,
  `SidecarBinding`, `SidecarMeta`, `SessionEndSnapshot`, the `PHASE_*` labels —
  no behavior, so every other module can import it without a cycle),
  `session_config` (dota-map template -> generated config/strategy/markets TOML
  in one TemporaryDirectory), `session_binding` (sidecar selection, decimal
  parsing, `MarketMeta` build, binding record write/parse),
  `session_journal` (`SessionJournal`, `FaultReporter`),
  `session_engine` (fork composition seams: `StrategyCell`,
  `make_yes_fair_adapter`, `GatedRegimeMachine`, `FreshnessWatchdog`,
  `build_engine`, `seed_catalog`, `close_engine_resources`, the two Gamma
  overrides, `bounded_task_join`), `session_quoting` (join-bid
  `construct_quotes` overlay), `session_trading` (`PaperTrading`: decision
  path, MDS bridge, sidecar refresh loop) and `session` itself (`run_session`,
  the feed loop, provenance setup, the terminal path).
- Names shared ACROSS these modules are public (no leading underscore):
  `PaperTrading`, `SessionJournal`, `FaultReporter`, `build_engine`,
  `materialize_config_dir`, `build_sidecar_meta`, … Only `session_trading`
  still carries `# pyright: reportPrivateUsage=false`, because only it touches
  the fork's private engine attributes; `session.py` needs no suppression.
- Test-seam rule after the split: `monkeypatch.setattr` must target the
  IMPORTER, not the definer. `build_engine` and `scan_sidecars` are patched on
  `session_trading`; `ENGINE_TASK_JOIN_TIMEOUT_S` and `FEED_STALE_SECONDS`
  on `session_engine`. `notify_in_background` now has two importers that
  matter: `session` (setup/provenance alerts) and `session_journal` (the fault
  reporter's deduped alerts) — an alert test patches both.
- Tests follow the same seams: `tests/live_paper_session_fixtures.py` holds
  every builder and fake (`build_discovered`, `build_trading`,
  `build_real_trading`, `FakeEngine`, `AlertRecorder`, …), and the suite is
  `test_live_paper_session{,_config,_binding,_journal,_engine,_quoting,_trading}.py`.
- `shared.utils.strict_json` is the one strict JSON decoder for every
  untrusted document (sidecars, Steam rows, the daemon<->child handoff, our own
  provenance): `require_object/exact_keys/str/nonempty_str/int/bool/list/
  str_list/nullable_*` raise `StrictJsonError` (a `ValueError`) with a
  caller-supplied label. Exact `type(x) is T`, so `bool` never passes as `int`.
  A missing key fails every non-nullable helper and reads as null in the
  nullable ones — pair the reads with `require_exact_keys` when an optional
  field must still be PRESENT as an explicit null (our durable records).
  Range and domain checks (positive ids, known enum values, cross-field rules)
  stay at the call site. All five `live_paper` readers use it; never hand-roll
  another `type(x) is not str` ladder.
- `archive_paths.FsyncedJsonlWriter` is the one append-only JSONL writer:
  mkdir, crash-tail truncation, `is_fresh`, then per record
  sanitize-nonfinite -> compact dumps with `allow_nan=False` -> write -> flush
  -> fsync. `StateWriter` and `SessionJournal` are thin wrappers over it (the
  fsync test seam is `archive_paths.os.fsync`, and the handle is
  `writer._writer._handle`).
- `SignalReason` is a `StrEnum`, not 15 constants plus a parallel `Literal`.
  A member IS its wire string, so `session.jsonl` records are byte-identical;
  compare with `is`/`is not`.
- `_ManagedMatch.quarantined` is GONE. A non-null `record.process` is the one
  liveness fact: it means the handle is RETAINED (running, or a stop that could
  not prove the death). The old flag was true only when `process is not None`,
  so the `process is not None or quarantined` guard carried a dead clause.
  Reconcile never spawns while a handle is retained; only a proven death clears
  it. Everything else about the lifecycle is unchanged.
- `cadence.poll_discoveries` is back on `asyncio.to_thread`. The 50-line daemon
  thread + `call_soon_threadsafe` wrapper defended against `asyncio.run`
  joining a blocked discovery, but on Python 3.13 `asyncio.run` waits at most
  `asyncio.constants.THREAD_JOIN_TIMEOUT` (300 s) for the default executor and
  then proceeds — a bounded wait on a call that is itself bounded by the httpx
  timeout. If that ever becomes a real problem, `asyncio.wrap_future` over a
  `concurrent.futures.Future` filled by a daemon thread is ~10 lines, and the
  same fix must then also cover `session._next_feed_event`, which has the exact
  same shape.
- The durable-final marker reads `meta_path.read_bytes()` and
  `json.loads(raw, parse_constant=...)` (json.loads takes bytes; no manual
  decode). The O_NONBLOCK/O_NOFOLLOW/S_ISREG/size-bounded read was removed: our
  own child writes that file by atomic rename into a directory the parent
  creates, so a FIFO/symlink/oversized attacker there could just write a valid
  final instead. NaN/Infinity rejection, the identity check and
  "never raise on unreadable data" all stay.
- Compensation-before-report is now pinned by a test
  (`test_compensation_runs_before_the_fault_report`): an observing reporter
  must see the gate already closed and the entry BUYs already gone. Reporting
  does I/O, so it can never run while a stale fair still opens the gate.
- Mutation gate for this pass (each fails a test): dropping the
  `record.process is not None` launch guard (4), dropping
  `if self._shutdown_done` in `_cancel_entry_buys` (1), reporting before
  compensating in `on_event` (1), dropping `os.fsync` from
  `FsyncedJsonlWriter.write_record` (6), accepting `bool` in
  `strict_json.require_int` (5).

## Live paper offline converter (US-013)

- `make live-parquet` runs `uv run python -m live_paper.state_to_parquet`
  (argparse CLI, `--root` defaults to LIVE_PAPER_DIR). Idempotent by design:
  run it after any batch of finished matches; nonzero exit only when a match
  failed. The converter is the ONLY live-archive caller of OpenDota.
- Per-match artifact `data/live_paper/<match_id>/state.parquet`, one row per
  state.jsonl record (duplicate game_times kept, so len == final.snapshot_count
  stays an honest completeness check). The complete parquet IS the converted
  marker — no manifest. Write is atomic (state.parquet.tmp + replace); a stale
  tmp is ignored and rewritten.
- Row schema = `SteamStateParquetRow` TypedDict in shared/types/steam.py;
  column order derived from its `__annotations__`. Player lists per side are
  equal-length and sorted by accountid (Steam has no slot). Building counters
  count survivors per side per type (0 tower / 1 barracks / 2 ancient).
  Pause comes from the public `build_game_snapshot` chain; header-only
  terminal rows (teams unparseable) fall back to zeros/empty lists via the
  now-public `build_header_snapshot`. `_resolve_teams` became public
  `resolve_teams` for the same reuse (basedpyright reportPrivateUsage).
- Winner resolution: match.json winner wins outright; `winner: null` triggers
  exactly one GET `{OPENDOTA_API}/matches/{id}` per run. 404 / missing key /
  non-bool / transport error -> `pending_winner`, no parquet, retried next
  run. Deliberately NOT `get_json`: tenacity retries would turn a 404 into
  five requests. `opendota_params()` puts api_key in the query string, so the
  fetch calls `suppress_http_url_logging()` first. match.json is never
  rewritten by the backfill; `radiant_win` lives only in parquet.
- Completeness check: readable parquet, all schema columns present, bool
  dtype `radiant_win` with no nulls (one None makes pandas read the column as
  object dtype), len(rows) == final.snapshot_count.
- Statuses: converted / skipped_complete / skipped_unfinalized (unfinalized or
  malformed meta never converts — a running match must never be marked ready)
  / pending_winner / failed (per-match isolation, type-only log, exit 1).

## Live paper operations (US-015)

Operator layout. Implementation details stay in the sections above.

### Model registry

Live loads `data/new_model/current/` (`model.txt` + `model.json` + `split.parquet`).
`make train` calls `rotate_current_model_dir`: read the name from
`current/model.json`, move `split.parquet` aside, rename `current/` to
`data/new_model/<name>/`, create a new `current/`. A running session pins one
booster; the next match sees the new `current/`.

### Steam contract

`SOURCE_LAG_SECONDS = 2`, `POLL_INTERVAL_SECONDS = 1` in
`src/shared/constants/dataset.py`. `radiant_xp_adv` comes from player levels
via `LEVEL_XP` / `radiant_xp_advantage` — the same helper in prepare and live.
`model.json` stores lag, poll, and features; `load_current_model()` refuses a
mismatch.

### Archive layout, Steam keys, offline scripts

```
data/live_paper/<match_id>/
  state.jsonl      # 1 Hz raw Steam, fsynced JSONL
  match.json       # start binding + final summary
  session.jsonl    # paper decisions
  state.parquet    # make live-parquet (complete file = converted marker)
```

`STEAM_KEYS` is a comma-separated list. All requests use key 1 until HTTP
429/403, then key 2 for the process lifetime, with one Telegram alert per
rotation. Restart (including Docker restart) returns to key 1. Legacy
`STEAM_KEY` is still one key.

Offline only: `make live-parquet` (the only OpenDota caller; winner=null
backfill) and `make live-report` (session.jsonl PnL, no network).

### Where it runs

The live daemon runs in Docker Compose on the VPS beside polymarket-collector,
reading the same host path `/var/lib/polymarket-dota-archive` as `/archive`.
The local machine keeps datasets, train, and backtest. There is no dry-run
flag; the first live check is a real match. Session `git_commit` is `"unknown"`
in Docker (no `.git` mount). `orchestrator.main()` calls `setup_logging()`
before daemon or session dispatch (root INFO). Without that, Docker's default
WARNING hides discovery cycle summaries, map mismatches, and missing server ids.

### What changed — what to restart

| What changed                             | What to do                     | Running matches                |
| ---------------------------------------- | ------------------------------ | ------------------------------ |
| Numbers in `config/dota-map.toml`        | save the file                  | keep the old numbers           |
| Session / strategy / model / feed Python | save the file                  | keep the old code              |
| `current/` model                         | put the directory in place     | keep the old booster           |
| Orchestrator or discovery                | `docker compose restart`       | die; archive resumes by append |
| Deps, `pyproject.toml`, Dockerfile       | `docker compose up -d --build` | die; archive resumes by append |

`src`, `config`, and `data/new_model` are bind-mounted, so the next session
picks up a saved file with no rebuild. `poly-maker` is copied into the image,
not mounted.

## Join-bid live-paper overlay (s2-join lifecycle)

Live paper now quotes the same way as backtest s2-join. The fork is not edited.

- Shared numbers live in `src/shared/constants/strategy.py`. `backtest/run.py`
  imports `BUY_CUTOFF_SECOND`, `MIN_ABS_DELTA`, `MAX_ABS_NW_DELTA_30`,
  `UNWIND_AFTER_SECONDS` from there. Model window is
  `MODEL_SIGNAL_END_SECOND = 899`. Freshness watchdog uses
  `FEED_STALE_SECONDS = 3.0` (patch that name on `session_engine`, not
  `POLL_INTERVAL_SECONDS`).
- `StrategyCell` replaced `FairGateCell`. `None` state means no model
  (`forced`); `cell.unwinding` is independent so an exit can still fire after
  the model ends. There is no `set_fair` helper — tests call `publish`.
- Engine module lease is three names: `compute_fair_value`,
  `ExecutionGateway`, and `construct_quotes`. Patch
  `polymaker.engine.construct_quotes` — the engine imported the name.
  Overlay is `live_paper.session_quoting.build_dota_quotes`. A fault in the
  overlay journals `trading_error` and returns an empty quote set (never the
  fork AMM).
- Entry is one join-bid BUY: `floor(bid)` on the 0.01 grid, size
  `base_size_usdc / price` rounded to 2 decimals. Live gate chain is
  `evaluate_entry` + `EntryGateInputs`. Backtest chain is
  `_choose_buy_target` + `nw_velocity_block_reason`. Shared numbers only
  live in `shared/constants/strategy.py`; parity is those constants plus
  tests, not one shared function. Grid snap is
  `shared.utils.trading.floor_to_grid` / `ceil_to_grid`.
- `off_grid` reads `engine.metas[cid].tick_size` (same as min size), never
  the constructor snapshot. A live 0.01→0.001 change must block new entries.
- Exit is one `ceil(ask)` SELL. Unwind after 300 wall-clock seconds from the
  first fill that opened the position; otherwise require
  `ceil(ask) >= fair_token`. Size is `floor(size*100 + 1e-6)/100`. HALTED
  empties quotes; EVENT still sells and does not open. A first engine
  recompute on microprice then a model fair can trip EVENT if the jump is
  `>= event_jump_ticks` (default profile is 8, dota-map is 15).
- Journal `schema_version` is 2: `signal.entry_block`, `quote.second`,
  `fill.second` (last Steam game second, markout as-of) + `fill.ts_utc`
  (wall clock when the paper fill was applied). `first_binding` and
  `make live-report` accept schema 1 and 2; one journal is one schema.
  Round `unwind` uses `ts_utc` hold, not game seconds. Old archives have
  no `entry_block` histogram and no wall-clock hold.
- Compare live to backtest via markout in cents and PnL per share, not PnL
  per round: live sizes $5 notional, backtest holds 5 shares. Paper still has
  no queue, no insert latency, and full fills at the limit.
