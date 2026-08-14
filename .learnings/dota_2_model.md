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
- Train on seconds `0, 60, ..., 540` with game at `T` and market at `T+8`. Validation rows are 1 Hz for decision seconds `8..899` with game at `T-8` and market at `T`; evaluate the model on `8..599`. Feature `second` on valid/backtest is the GRID clock (`T-8`); the parquet row key stays decision `T`.
- Backtest emits a model tick every `POLL_INTERVAL_SECONDS` (10); `MAX_SIGNAL_AGE_SECONDS = 10` so the last poll stays usable until the next. Fair between polls is live book + frozen `predicted_delta`. There is no `SIGNAL_LAG_SECONDS`.
- Require a published prior before dataset inclusion. Derive validation and backtest selection from that gated dataset.
- Quote on the `0.01` grid. Keep instrument `price_precision = 3`; log off-grid archived book prices and continue.
- Radiant and Dire books and trades mirror each other. Do not quote both equivalent levels; Nautilus duplicates fills. Keep trades for queue-position replay.
- `get_avg_px_for_quantity(q)` does not prove `q` is available. Check book depth before assuming full execution.
- Keep the framework checkout at `c76e77af00ef53472a9da8f66dae7fdd2d3e5928` clean. Patch `closedTime` expiration and `replay_end` locally.
- Schedule a clock alert for each order and cancel release. Nautilus drains latency queues only at visited timestamps.
- Keep one process-owned Nautilus log guard. Clear `set_backtest_force_stop(False)` before each batch.
- `--shard i/n` writes `shard_{i}of{n}/` under the canonical run dir; `--merge-shards n` concatenates those parquets into the parent checkpoint and writes summary. Merge does not require a parent `manifest.json` — shards-only dirs are valid.
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
- The server rebuilds the snapshot once per second. A faster poll returns identical bytes. Subtract the request time from the sleep or the cycle drifts to 1.3s and drops every third second.
- A pause freezes `game_time` while `match.timestamp` keeps ticking. Pause length is the growth of `timestamp - game_time`.
- `buildings` carries three `type` values: 0 tower, 1 barracks, 2 ancient. The fountain is not listed. A destroyed building loses its identity and becomes an anonymous stub (`team` 0, `type` 0, `tier` 0, `destroyed` true). Count survivors against 11 towers, 6 barracks and 1 ancient per side. The loser is the side with zero surviving `type` 2 buildings.
- `server_steam_id` comes from `GetTopLiveGame` (top 10 games) or OpenDota `/live`. `GetLiveLeagueGames` does not carry it.
- `graph_data.graph_gold` is a fixed 128-point downsample of the whole match. Use it for late-join backfill, not as a time series.
- Players carry `level`, not XP. STRATZ `radiantExperienceLeads` has no exact Steam equivalent. `xp_per_min` lives in `GetLiveLeagueGames`, together with Roshan and respawn timers.
- `GetMatchDetails` is dead: 500 on recent match ids, empty `{}` on old ones. Take the winner from the last snapshot's destroyed ancient, or keep OpenDota for `radiant_win`.
- Steam has no history. It records forward only; past matches stay with STRATZ and OpenDota.
- One snapshot is 16.6 KB, 3.6 KB gzipped. 1 Hz on a 40-minute match is 2400 requests. The daily budget is 27 hours of tracked game time; tracking only seconds 0..599 costs 600 requests per match.
