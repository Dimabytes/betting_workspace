# Dota pipeline audit — shared context (read fully before your brief)

Date: 2026-09-26. Orchestrator: Claude. You are one of 14 audit agents
(8 × devin SWE-2 Max, 3 × Grok 4.7 High, 3 × GPT-6 Luna max).

- Run dir (`$R`): `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26`
- Product repo (`$E`): `/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader` — read `$E/AGENTS.md` first.
- Workspace (`$W`): `/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace` — prior research in
  `$W/.analysis/`, `$W/.learnings/`, `$W/docs/`.
- Sibling repos (read-only): `../polymarket-collector` (TypeScript daemon on the VPS: market metadata
  sidecars, book snapshots, onchain fills), `../prediction-market-backtesting` (Nautilus backtest framework;
  `$E/Makefile` pins commit `c76e77af`), `../poly-maker` (frozen fork; the live Engine is patched from
  `$E/src/trader/engine_seams.py`). Paths relative to `$E/..`.

## The owner's request (translated)

"Audit the project for bugs, problems and weaknesses that affect money, speed, performance, and how hard
it is to debug. I need an architectural review and a detailed bug review. Small things at a low level grow
into big ones: if I collected data wrong, the model trained on it is wrong, and a wrongly trained model
performs badly live. If the backtest is broken or uses data wrongly, it gives me wrong numbers, and I make
wrong decisions on them. Check every level separately.

I did such a pass over the whole LoL pipeline. We found several fundamental bugs, fixed them, and LoL got
better. Do the same for Dota now: the whole Dota pipeline, from data collection on. The project is 3 months
old and a lot has accumulated: junk, leftovers, things built at the start and never revisited. Maybe I
downloaded some data long ago (STRATZ or something else) and it was bad. Maybe I started to use something in
the backtest long ago and it stayed wrong.

Also look at what kinds of bugs we already found — there were 200–300 on this project — and dig near them
for similar ones. Output: a list of problems with a description of the impact and how it shows, sorted by
criticality: by how much it affects the correctness of the system, money, and whether decisions and data are
right. Fix effort does not matter for the order. Focus on Dota; shared code used by both games counts."

## What a good finding looks like

Every serious bug so far was found by reading code line by line and then confirming it on data: swapped
sides, a wrong training time join, a backtest that acted at event time without the live feed delay, filters
that used future information, a prior computed differently in live and in training, metadata that says one
thing while the code does another. Errors compound stage by stage:
collect → link market↔game → market data → prepare dataset → train → backtest → live.
A clean "this part is OK, here is the evidence" is also a valid result. Do not pad the list with style nits.

## Severity scale (use exactly this)

- **S1 critical** — makes training data, labels, backtest numbers, or live money flow wrong in a way that
  changes decisions or loses money now. Examples: look-ahead in the backtest, swapped sides, a wrong
  label/time join, a live order-state bug that can lose or strand money.
- **S2 high** — material bias or loss, but bounded or intermittent. Examples: train/serve skew in a
  feature, selection bias in which maps are evaluated, a live path that fails closed and loses edge on many
  maps, a prior mismatch.
- **S3 medium** — a real bug with small or rare money impact, or a large debuggability/performance cost
  (hours per iteration, misleading reports or metadata, silent data drops).
- **S4 low** — cleanup, dead code, leftovers, naming, minor performance. List briefly.

Sort by severity, then by estimated $ or decision impact.

## The system (short; verify in code what you rely on)

Maker bot on Polymarket Dota 2 map-winner markets. LoL shares most of the code.

- **Model.** LightGBM ensemble (K=10, `sub90` bootstrap members) predicts the change of the map market's
  radiant mid 300 s ahead (Δ̂). 12 features: `second, radiant_nw_adv, radiant_nw, dire_nw, radiant_xp_adv,
  deaths_radiant, deaths_dire, top1_nw_adv, radiant_top1_nw_ratio, dire_top1_nw_ratio,
  market_radiant_prior, market_p_radiant`. `source_lag_seconds=10`, horizon 300 s.
- **Models on disk.** Research `20260924T183856Z` (1563 train maps; used by backtests) and production
  `20260924T183900Z` (2225 maps, 39 trees per member; used live): `$E/data/new_model/{research,production}`.
  No-XP variants: `research-noxp`, `production-noxp` (check which live source uses them).
- **Dataset design** (`src/prepare_dataset/prepare_dataset.py`, `src/shared/constants/dataset.py`):
  research train = STRATZ **minute** states (second −60..540 step 60) of maps before
  `VALIDATION_START_TIME=1780563592` (~2026-06-04), state at S, market at S+10. Validation = STRATZ
  **exact-second** states of maps after that date, one row per market second M with state at M−10, rows
  through map duration. Production train = train + validation minute rows. So there are three feature
  sources: STRATZ minute states (train), STRATZ exact-second reconstruction (validation/backtest), and
  live Steam/GRID/Oddin frames (live).
- **Policy** `follow300-v5` (`src/strategy/`, `src/shared/constants/strategy.py`): when |Δ̂| ≥ 2c
  (hysteresis exit 1.5c) join the best bid of the favoured token with 3 BUY rungs (L0 best bid, L1 −1c,
  L2 −2c). BUY only while game clock < 480 s, entry price in [0.40, 0.85), spread < 6 ticks. Gates: kill
  gate, `mid_spike`, lonely-L0, stale signal (16 s), `nw_velocity` (350). SELL price =
  max(ceil(best ask), ceil(token fair)); exit stale > 45 s → SELL joins the ask. The same pure core runs in
  live (through `src/trader`) and in the Nautilus backtest (`src/backtest/strategy.py`).
- **Live.** VPS host `sun` (HEAD `00c3dd19` today). Containers: `esports-trader-live-1` (WalletHost, real
  money), `esports-trader-paper-1`, `esports-trader-compress-1`; collectors in `../polymarket-collector`
  compose. Dota live feeds: Steam `GetRealtimeStats`, GRID socket, Oddin (1 s cadence). Clips
  (`config/trading.toml`): Steam/GRID $60, Oddin $200. Discovery moved from Bitsler to an in-memory Disir
  catalog today (commits `dec98290`..`bbb28897`): fresh code.
- **Local copies of live.** `$E/data/trader/<match_id>/` (synced from the VPS): `match.json`,
  `session.jsonl`, `core_trace.jsonl`, `state.jsonl` (Steam), `grid_state.jsonl`, `oddin_state.jsonl`.
  Files may be `.gz`; read them with `src/shared/utils/jsonl_io.py` helpers.

## Pipeline map (Dota): stage → code → outputs

1. **Collect** (`make collect`): `src/collect/s01_build_universe.py` (Gamma events/markets →
   `data/new_processed/universe`), `s02_link_opendota.py` (map ↔ OpenDota match), `s03_merge_archive_links.py`
   (links from live archives via `src/archive_index`), `s04_fetch_grid_starts.py` (GRID series state → game
   starts), `s05_fetch_opendota_matches.py`, `s05a_fetch_prices_history.py` (pregame prior from CLOB
   prices-history), `s05b_fetch_stratz_matches.py`, `s06_publish_catalog.py` (→ `match_catalog`),
   `s07_link_readiness.py`. Raw: `data/raw/{opendota_*,pro_matches,polymarket_dota,stratz_matches}`.
2. **Market data.** `data/raw/telonex/polymarket/{book_snapshot_full,trades,onchain_fills}`. Older days are
   paid Telonex history; newer days come from our collector via `scripts/sync_collector_parquet.py`. Check
   provenance and semantics. Code: `src/shared/utils/telonex_book.py`, `telonex_capture.py`,
   `price_history.py`, `src/market_data/build_market_data.py` → `data/new_processed/market_seconds/v<hash>/`.
3. **Prepare:** `src/prepare_dataset/prepare_dataset.py`, `stratz_seconds.py` → `data/new_processed/dataset/`.
4. **Train:** `src/train_model/train_model.py`, `market_metrics.py`, `src/shared/utils/gbm.py`,
   `model_registry.py` → `data/new_model/*`.
5. **Backtest:** `src/backtest/run.py` (entry, 2223 lines), `signals.py`, `feed_schedules.py`,
   `replay_inputs.py`, `live_archives.py`, `selection.py`, `context.py`, `telonex_local.py`, `strategy.py`,
   `maker_orders.py`, `quote_store.py`, `strip_own_book.py`, `marks.py`, `postprocess.py`, `results.py`,
   `report*.py`, `wallet_path.py`, `seed0_replay.py`, `extraction_identity.py`, `series_*` (new: series
   markets), `src/archive_index/` (feed schedules from live archives). Scripts: `scripts/run_seeds.sh`,
   `report_seeds.py`, `promote_backtest.py`, `compare_backtests.py`.
   **LIVE catalog:** `$E/data/backtests/dota_maker/LIVE -> validation_join_delta02_x015_cut480_p4_archive-s3-20260924`
   (seeds 0–2; each has `summary.json`, `manifest.json`, `results.parquet`, `fills.parquet`,
   `quote_events.parquet`). Manifest: 613 maps, research model, `signal_source=auto`,
   `backtest_lag_seconds=10`, `max_signal_age_seconds=16`, `fill_model=queue`, `network_latency_ms=85`,
   `quoter_tick_s=2.0`, `debounce_ms=100`, 3 layers × $100.
6. **Strategy kernel:** `src/strategy/*`.
7. **Live:** `src/trader/*`: orchestrator → wallet_host → discovery / match_worker → feeds (`steam_*`,
   `grid_*`, `oddin_*`) → session_core / session_engine / session_quoting → core_execution / persistence /
   recovery / trace → poly-maker Engine via engine_seams; wallet_store (sqlite); paper_gateway.
8. **Tools:** `src/viewer`, `src/backtest/inspect` (Streamlit), `scripts/*`.

## What earlier research already found (leads — re-verify before you rely on them)

A. **LoL audit 2026-09-23** (`$W/.analysis/lol-live-gap-2026-09-23/REPORT.md` and its `reports/`). Classes
   found in LoL; several can exist in Dota or in shared code:
   1. The backtest acted at event time without the feed delay (LoL synthetic `grid-v1`, lag 0). Dota uses
      lag 10. Verify it is right for every Dota signal source (grid-v1, Steam, GRID, Oddin archive schedules).
   2. The training join lag did not match live. `model.json` said `source_lag_seconds=10`; checks compared
      constants, not the real join.
   3. The live prior was computed differently from the training prior. For Dota the report said "both from
      prices-history, mean |Δ| 1.17c" — re-check.
   4. A whole-map liquidity filter used future information. It was reportedly removed for Dota in
      `1e894b24` ("Drop the look-ahead tape filter"). Check for residue: e.g. `find_longest_book_gap` in
      `prepare_dataset.py` marks `backtest_book_gap_excluded` from seconds −60..900 of the whole map.
   5. Research early stopping runs on the validation maps, which are also the backtest maps.
   6. The synthetic `grid-v1` feed does not model feed gaps and staleness.
   7. XP undercount after feed gaps; the model runs live outside its training window; the first ~80 s of the
      live window were empty.
   8. Dota live vs backtest on the same GRID maps (09-01..09-19): live +0.32% vs backtest +4.54% PnL per BUY $.
      Dota GRID table lag 9.65 s; the market moves half of its 60 s move in 9.60 s.
B. **Series-edge 2026-09-26** (`$W/.analysis/series-edge-2026-09-26/report.md` §6): Dota LIVE backtest seed 0
   engine PnL $2,849 = cash flow −$2,685 + settlement $5,533. 39 positions were held to map end
   (5,533 shares) and **all 39 won** per catalog `radiant_win`. LoL: 27 held, 97.5% of shares won. Mechanism
   not established. 39/39 is too clean: top suspect for look-ahead or a settlement/marking bug. It can also be
   a real effect (SELL rests at fair above the market; winners run away from it). Prove which.
C. **Hold-longer 2026-09-26** (`$W/.analysis/hold-longer-2026-09-26/`): exit rule details; the model keeps
   producing a fair after 480 s and after 540 s although it was trained on seconds −60..540.
D. **`$W/.learnings/`**: `dota-training-filter-and-zero-lag-20260919.md`,
   `training-tape-exclusions-audit-20260919.md`, `strategy-seed-sensitivity-20260912.md` (seed-to-seed CV
   35–60% for Dota), `esports-trader-backtest-performance-2026-09-07.md`, `hysteresis-nw-readiness-20260920.md`,
   `pgl-*.md`. `$E/docs/as-is.md` is stale (2026-09-04). `$E/docs/experiments/*.md` records experiments and
   reverts.
E. **Git history:** 1194 commits, ~275 fix-like. Use `git log --grep`, `git log -S`, `git blame`.

## Hard rules

1. You run with auto-approve — no permission prompt will stop you. Never delete, overwrite, or destroy
   anything you did not create this run: no `rm`, no `mv` onto existing files, no `git clean` / `reset --hard`
   / `checkout --` / `stash`, no `kill` / `pkill` outside your own `work/<name>/` processes, no dropping or
   truncating files, dirs, rows, tables, or branches. If something is in the way, write beside it or stop and
   report.
2. Read-only on all code repos and data: do not edit, create, move, or delete anything in `$E` (src, config,
   tests, scripts, data, docs, models), `../poly-maker`, `../polymarket-collector`,
   `../prediction-market-backtesting`, or `$W` outside `$R`. Write only to `$R/work/<your-name>/` and
   `$R/reports/<your-name>*.md`. Git: read-only (`log`, `show`, `diff`, `blame`, `grep`). The owner may edit
   `$E` during the run; if a file looks half-written, read `git show HEAD:<path>`.
3. The VPS `sun` trades real money. Only `sun-devin` may SSH, read-only, as its brief says. Everyone else:
   no SSH. If you need VPS data, write the exact request under "Needs from VPS".
4. No pytest / `make test`, no model publishing, no full backtests, no `make collect/prepare/train/market-data`,
   no network writes, no paid API calls. Small analysis scripts are expected. Small offline LightGBM fits are
   allowed only where your brief says so, and only into your work dir.
5. Run Python from `$E` so project modules load:
   `cd $E && PYTHONPATH=src uv run python $R/work/<name>/x.py`. Backtest modules need
   `PYTHONPATH=src:../prediction-market-backtesting uv run --group backtest python ...`. Reuse project readers
   (grep `src/shared` first) instead of writing new parsers.
6. The machine is shared with other running agents (12 cores, 32 GB RAM). Keep each script under ~4 GB RAM.
   Prefer polars/duckdb lazy scans and per-map reads. Use `nice -n 10` for anything longer than 1 min. No
   process pools above 2 workers. `data/raw/telonex` is 52 GB: never scan it whole. Keep work files under
   200 MB each.
7. No accounts, signups, or money. Browser only via `agent-browser --session <your-name>`; never
   `close --all`. Web search is fine for API semantics (STRATZ, OpenDota, GRID, Steam, Polymarket CLOB,
   Nautilus).
8. Evidence for every claim: `file:line`, the command and the numbers it printed, or URL + date. Mark each
   finding `verified` (you reproduced it), `likely`, or `speculative`. Check that the finding still exists at
   HEAD (`git log -S`, `git blame`). A bug fixed since is not a finding (mention it under history if useful).
9. Other agents cover neighboring topics in parallel (table below). Think independently. Stay mostly in your
   scope, but report any S1/S2 you trip over elsewhere.

## Report format (`$R/reports/<your-name>.md`)

- Line 1: `# <name> — <topic>`
- Line 2: `Status: WIP` while you work; `Status: FINAL` only when the report is complete.

Then:

1. **Summary** — 3–8 bullets, most severe first.
2. **Findings table** — ID (`<name>-F1`, …), severity S1–S4, layer, title, confidence, estimated impact.
3. **Findings detail** — one section per finding: where (`file:line` at HEAD); what is wrong; mechanism;
   evidence (command, numbers); impact (money / data / decisions / speed / debug) with a size estimate if you
   can; how to confirm or fix (1–3 lines); the commit that introduced it, if cheap to find.
4. **Architecture / performance / debuggability notes** for your scope (short, ranked).
5. **Checked and OK** — what you verified is correct, with evidence (short).
6. **Open questions / Needs from VPS.**

Update the file as you go (WIP), so progress survives interruptions.

## Agents

| name | model | scope |
|---|---|---|
| collect-devin | SWE-2 Max | collect fetchers and parsers: STRATZ, OpenDota, GRID starts, prices-history prior, `stratz_seconds`; raw-data provenance and leftovers |
| signal-devin | SWE-2 Max | backtest inputs and signals: what the model sees at each backtest tick; map selection; market cache |
| fill-devin | SWE-2 Max | backtest execution realism inside the Nautilus framework; settlement tail (pair with fill-grok) |
| kernel-devin | SWE-2 Max | strategy kernel and live/backtest adapter parity |
| live-devin | SWE-2 Max | live execution, wallet, orders, risk, recovery, latency |
| feed-devin | SWE-2 Max | live feeds, live features, discovery/Disir, model server, live prior |
| history-devin | SWE-2 Max | bug archaeology: taxonomy of past bugs, sibling hunt, leftovers |
| sun-devin | SWE-2 Max | VPS docker logs (read-only SSH) → live error taxonomy mapped to HEAD |
| fill-grok | Grok 4.7 High | backtest execution and PnL accounting code; settlement tail 39/39 (pair with fill-devin) |
| time-grok | Grok 4.7 High | timing and look-ahead across dataset, backtest, live |
| link-grok | Grok 4.7 High | market↔game linking and side orientation, end to end |
| parity-luna | GPT-6 Luna max | empirical live vs backtest parity on the same Dota maps |
| data-luna | GPT-6 Luna max | data quality: catalog, market cache, datasets, provenance, distribution shift |
| train-luna | GPT-6 Luna max | training, evaluation, inference parity |
