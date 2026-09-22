Train + backtest report — archive-linking-and-feed-replay, run on main @ 25734ed0
Session date: 2026-09-21. Working dir: /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader (main, clean at start).

TL;DR — both games' backtests are blocked by dataset gaps that the brief's
"do not edit product code" rule does not let me fix:

- Dota: `make prepare` hard-fails (exit 2) on 24 archive-admitted validation
  candidates that have no STRATZ replay (playbackData null, verified live).
  `make train` and the Dota backtests cannot run — no datasets, no
  game_features.parquet.
- LoL: `make lol-prepare` + `make lol-train` succeeded (after fetching the
  missing Data Dragon item table for patch 16.18 — a provided maintenance
  script). The LoL seed-0 backtest crashes deterministically:
  `dataset not ready for archive replay` — 161 of 209 schedule-bound LoL
  matches have non-stale schedule ticks at game_seconds with no feature row,
  because LoL's schedule-feature source (validation.parquet) only has rows at
  market-ok seconds.

Everything below is verbatim commands, exit codes, and measured evidence.

==================================================================
COMMANDS AND EXIT CODES
==================================================================

1. make market-data
   exit 0
   tail of output:
     [2026-09-21 05:08:11,272] market-data skipped=3062 queued=175
   (175 match caches built this run; 3062 already cached; total cache covers
   all 3237 catalog rows)

2. make prepare
   exit 2  *** BLOCKER — Dota ***
   verbatim output tail:
     [2026-09-21 05:08:20,996] prepare missing train market caches (50): [8577907652, 8577926044, 8577982630, 8578012189, 8578065966, 8584248639, 8584303494, 8584357150, 8584468848, 8584493873, 8603059957, 8603074586, 8603145326, 8603199591, 8603380188, 8603488410, 8603500020, 8603604691, 8603614526, 8604733776, 8604733543, 8604809016, 8604819918, 8604851194, 8604888305, 8604941293, 8604935721, 8604974662, 8605069135, 8605115782, 8605192234, 8605190938, 8605215449, 8605308834, 8605323294, 8615212174, 8615341751, 8615442050, 8636668127, 8636761702, 8643782171, 8643905304, 8645272548, 8645376228, 8645473240, 8651177649, 8651586097, 8654388900, 8654581479, 8655134079]
     validation candidates missing playback: 8978147287, 8978242189, 8978292022, 8978685544, 8978758321, 8979484553, 8980211577, 8981829611, 8981891261, 8986955347, 8987079581, 8992777443, 8992825325, 8997785564, 8998136794, 8998181660, 8998371586, 8998366112, 8998434688, 8998438013, 8998498850, 8998575635, 8998855174, 8998987066
     make: *** [prepare] Error 1
   Note: the 50 missing train market caches are under MAX_TRAIN_HARD_MISSES=160
   (warning only, dropped from train). The fatal line is the playback gate.

3. make train
   NOT RUN — prepare never emitted training/validation/game_features datasets.
   Running it would publish models trained on the stale 2026-09-19 datasets
   (pre-archive catalog) and produce a misleading report.

4. PYTHONPATH=src uv run python scripts/fetch_lol_item_tables.py
   exit 0
   output tail: "wrote 31 item tables -> /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/src/lol/ddragon_items"
   New file on disk: src/lol/ddragon_items/16.18.1.json (untracked; required
   by patch 16.18.819.1060 games). This is the script the file's own docstring
   says to re-run "when a new patch appears" — no product code was edited.

5. make lol-prepare (first attempt)
   exit 2
   tail: ValueError: no item table for game patch 16.18.819.1060
   (crashed at ~5500/5565 accepted=4953; fixed by step 4)

6. make lol-prepare (second attempt)
   exit 0
   verbatim summary:
     missing_books: 141
     accepted: 3825
     zero_labeled_rows: 39
     missing_prior: 399
     thin_telonex_trades: 1142
     window_details_mismatch: 1
     livestats_invariant_violation: 9
     no_spawn_frame: 4
     aborted_feed: 4
     zero_usable_rows: 1
     train: 2139
     validation: 1686
     map_build_cache_hit: 0
     map_build_cache_miss: 5565
   (all 5565 maps rebuilt — the input stamp changed with the new item table)

7. make lol-train
   exit 0
   verbatim:
     [2026-09-21 07:06:46,885] ensemble default_sub90 k=10 trees: 29-37 mean=33 (of 3000) | train matches: 2139 | validation matches: 1686 | train rows: 1120284 | validation rows: 892006
     [2026-09-21 07:06:46,949] published: /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/lol/models/research | archived previous model: 20260919T112924Z
     [2026-09-21 07:06:58,384] production train: publishing /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/lol/models/production | ensemble default_sub90 k=10 trees: 33-33 mean=33 | train matches: 3825 | train rows: 2012290
     [2026-09-21 07:06:58,422] published: /Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/lol/models/production | archived previous model: 20260919T112946Z

8. SEEDS=1 SHARDS=4 scripts/run_seeds.sh dota archive-s2-20260921
   exit 1  (all 4 shard workers failed)
   every shard_*.log ends with:
     File "src/backtest/run.py", line 748, in build_run_manifest
       payload["game_features_sha256"] = sha256_file(GAME_FEATURES_DATASET_PATH)
     FileNotFoundError: [Errno 2] No such file or directory: 'data/new_processed/dataset/game_features.parquet'
   Shard logs also show feed resolution WORKING before the crash:
     archive exclusion: match 8994847185 - archive:record:no_terminal
     archive exclusion: match 8995259364 - archive:record:no_terminal
     archive exclusion: match 8996676912 - schedule_identity_mismatch
     validation matches: 558 | book gap: 2 | no map market: 0 | no signal rows: 0 | no local Telonex: 0 | archive excluded: 12 | eligible: 556
   (the stale Sept-19 research split was used since `make train` never ran;
   the run dies purely on the missing prepare artifact)

9. SEEDS=1 SHARDS=4 scripts/run_seeds.sh lol archive-s2-20260921
   exit 1  (all 4 shard workers failed)  *** BLOCKER — LoL ***
   per-shard fatal error:
     shard_0: ValueError: dataset not ready for archive replay: match 116792888905448381: no feature row at game_second 1394
     shard_1: ValueError: dataset not ready for archive replay: match 116792888905448382: no feature row at game_second 2311
     shard_2: ValueError: dataset not ready for archive replay: match 117030752644841644: no feature row at game_second 2297
     shard_3: ValueError: dataset not ready for archive replay: match 116793933806066957: no feature row at game_second 1991
   (raised by backtest.signals.DatasetReadinessError at
   _match_schedule_decisions; wraps into ValueError at run.py:1586)

10. SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh dota archive-s2-20260921
    exit 1 — all 8 shard logs show the same FileNotFoundError
    (game_features.parquet). Deterministic, --resume cannot help.

11. SEED_LIST="1 2" SHARDS=4 scripts/run_seeds.sh lol archive-s2-20260921
    exit 1 — all 8 shard logs show DatasetReadinessError
    "no feature row at game_second". Deterministic.

Commands not run: `make train` (no datasets — see 3). Nothing else.

==================================================================
BLOCKER 1 (Dota) — validation candidates missing playback
==================================================================

prepare_dataset.select_matches() has a zero-tolerance gate: every catalog
entry in the validation window (start_time >= 1780563592, i.e. after
2026-06-04) that passes match_passes_tape_buckets must have
playback_available=True. 24 candidates fail it.

Measured facts:
- match_catalog.parquet: 3237 rows; 752 validation-window entries pass tape
  buckets; 41 of those have playback_available=False (all archive-admitted,
  archive_feed_source=grid, horn_source=archive); 24 of the 41 reach the gate
  (the other 17 fail tape buckets first).
- All 24 are archive-admitted matches played 2026-09-01..2026-09-14 (slugs
  like dota2-z10-spirit1-*, dota2-yg-iac-*, dota2-nem-yg-*, iac/tm6/balu/etc).
- Their STRATZ cache files (fetched 2026-09-20 ~22:56-22:58, zero GraphQL
  errors, profile stratz_rich_v3) have playbackData=null at BOTH match level
  and per-player level. I re-queried api.stratz.com live twice this session:
  still null.
- Identity verified, not a bad link: stratz startDateTime, durationSeconds,
  didRadiantWin all agree with catalog horn/radiant_win. lobbyType=PRACTICE —
  replays for these lobbies were never parsed by STRATZ; consistent with the
  repo note that playbackData is absent for stale/unparsed matches.
- Why it matters: build_exact_second_states needs
  players[].playbackData.playerUpdateGoldEvents. Without it these matches can
  never produce validation rows or game_features rows; even if split-admitted
  they would be feed-plan exclusions downstream. The gate is correct that
  they are undeliverable; the problem is that collect admits them as
  validation candidates at all.
- Implication of fixing it: dropping the 24 leaves ~711 deliverable
  validation-window candidates (752 - 24 - any validation market-cache
  misses, cap 20).

Possible product fixes (needs a decision; I did not pick one):
a) s06 keep_complete drops validation-window rows without playback
   (catalog no longer offers them to prepare);
b) prepare treats missing_playback like missing market caches — drop with
   warning (changes the gate's zero-tolerance semantics);
c) s03/archive admission refuses validation-window matches without playback.
Any of them changes dataset semantics and is outside "a missing path/flag in
the new CLI".

==================================================================
BLOCKER 2 (LoL) — schedule ticks with no feature row
==================================================================

The LoL seed-0 crash is not one bad match — it is systemic. Measured against
the real artifacts (resolve_lol_feed_plans + validation.parquet +
market_seconds.parquet + schedules):

- 1686 validation matches; resolve_lol_feed_plans binds 209 to schedules,
  leaves 1473 on grid_v1, excludes 4 (archive:record:feed_gone x2,
  archive:feed:no_window_updates x1, archive:record:no_terminal x1).
- Of the 209 schedule-bound: 161 have >=1 NON-STALE tick at a game_second
  with no feature row (these crash); 2 have missing ticks only in stale
  regions (fine); 46 are fully clean.
- Root cause: load_game_feature_rows(game="lol") reuses signal_rows =
  data/lol/processed/datasets/validation.parquet as the feature table. That
  parquet only contains rows at market-ok seconds — verified exactly:
  for match 116792888905448381 the set (market_seconds.second minus
  validation.second) == the set of non-ok market_status seconds (215 missing
  rows = 128 stale_quote + 87 missing_quote; its 200s hole at game_seconds
  1389-1587 is one missing_quote stretch). Raw livestats details DO have
  frames through the hole, so the data exists — the market join is what
  makes the table sparse.
- Dota does not have this problem because prepare emits
  game_features.parquet — a complete per-second feature table built from
  STRATZ playback, independent of market status. LoL has no equivalent;
  LOL_GRID_START_SECOND=0 also means any schedule tick at a negative
  game_second (e.g. -5 observed in match 115548681803406328's schedule) can
  never have a row.
- The design intent ("a missing feature row is a dataset readiness failure,
  not a gap") assumed bound schedules have complete coverage; that holds for
  Dota's dedicated table but not for LoL's market-joined one.

Possible product fixes (needs a decision; I did not pick one):
a) emit a LoL game_features equivalent in 05_prepare_dataset — feature rows
   for every livestats grid slot regardless of mid resolution (features come
   from livestats, not the market), including negative seconds;
b) treat a feature-missing non-stale tick like a market gap (skip the
   decision) in _match_schedule_decisions;
c) check feature coverage at feed-plan admission and exclude sparse matches.

==================================================================
BACKTEST RUN DIRECTORIES / ARTIFACTS
==================================================================

- data/backtests/lol_maker/validation_join_delta02_x015_cut480_p35_archive-s2-20260921
  — created, 0B (crash at signal resolution before any results; nothing to resume)
- data/backtests/lol_maker/_logs/archive-s2-20260921/seed{0,1,2}/shard_{0..3}.log
  — all 12 logs present, each ending in the DatasetReadinessError above
- data/backtests/dota_maker/_logs/archive-s2-20260921/seed{0,1,2}/shard_{0..3}.log
  — all 12 logs present, each ending in the game_features FileNotFoundError
- No dota_maker run root was created (crash precedes run-dir creation)
- LoL models published: data/lol/models/research, data/lol/models/production
  (previous published models archived under
  data/lol/models/archive/research/20260919T112924Z and
  .../production/20260919T112946Z)
- Dota models unchanged (data/new_model/* untouched — train never ran)
- Working tree deltas I produced: src/lol/ddragon_items/16.18.1.json (new),
  data/lol/models/{research,production}/member_*.txt + model.json (retrained),
  new archive dirs above. No commits made.

==================================================================
REQUIRED REPORT OUTPUTS
==================================================================

- format_terminal_report / scripts/report_seeds.py two-column output:
  DOES NOT EXIST for either game — no seed produced a results.parquet, so
  report_seeds.py was never reached (run_seeds.sh exits before the report
  step on seed failure).
- vs-LIVE comparison (scripts/compare_backtests.py): NOT PRINTED — same
  reason.
- Dataset/model output paths + hashes: LoL model dirs above; no sha256 was
  printed by prepare/train (they print counts only). Dota datasets not
  emitted.

==================================================================
WHAT NEEDS A DECISION BEFORE RETRY
==================================================================

1. Dota: pick a fix for the 24 no-playback validation candidates (options
   a/b/c in Blocker 1). Until then `make prepare` cannot pass and
   game_features.parquet will never exist, so `make train` and every Dota
   seed run fail as shown.
2. LoL: pick a fix for sparse schedule-feature coverage (options a/b/c in
   Blocker 2). Until then ~77% of schedule-bound LoL matches crash every
   shard at signal resolution.
3. After either fix lands, rerun from the failed stage: prepare (dota),
   train, then the four run_seeds.sh commands in the brief's order
   (seed 0 x4 shards per game first, then SEED_LIST="1 2").
