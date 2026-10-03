# grok-timeline: LoL changes since 2026-09-20 mapped to backtest numbers
Status: FINAL
## Verdict
The owner's "cur (LIVE)" fingerprint is `w540lv6` (model `20261002T190131Z`), LIVE until `2a0f4b7e`. Per-seed CVaR −433.27/−493.48/−444.05, worst −1107.39/−1055.88/−1055.88, buy turnover $761,692 / $765,024 / $762,384, after-rebate mean net **$25,524.32**. The figure $25,657 is not in these summaries. Current LIVE is the symlink to `histfix-20261003r2`: CVaR −597.13/−622.79/−632.39, worst −1912.97/−1914.68/−2088.49, turnover ~$875k, mean net **$27,232.92**.
The tail break is not the history retrain by itself. Same model, cap 6→9, on the post-`ea5367b9` backtest (`cur-20261003r2`) already moves mean CVaR −457→−614 and late buy-300s ~0.52¢→~0¢. `cur`→`histfix` (policy retrain only) is −$1,119 mean net, CVaR flat, worst map ~$487 worse, late markout back to ~0.53¢.
77-col full-map is `cat77` / levels-matched `cat77lv6` (`20261002T165228Z`, train_matches 2713). Cutting the fit to ≤540 (`w540lv6`, train_matches 2551) added +$2,852 mean net on an otherwise identical manifest and did not fix the late third (still ~$3.4k, markout 0.44–0.61¢).
`rebuild-20260930`→`cat77` is not a pure feature A/B: feed-schedule v6→v8 plus a new validation parquet. From `cat77` through `histfix` those hashes match. Manifests do not store the esports-trader commit: Dota `hist77` vs `hist77cur` is a zero-key diff with incomplete maps 4→31.

## Findings
1. **`w540lv6` is the "cur (LIVE)" column; `histfix-20261003r2` is "histfix" and today's LIVE.** verified. `report_seeds` mean/per-seed rows (command in Scripts). `lol_maker/LIVE` is a symlink to `validation_join_delta02_x015_cut480_p45_histfix-20261003r2` (`readlink`). Seed summaries match the brief's histfix triple exactly (CVaR printed −597.1315/−622.7869/−632.3868, worst −1912.97/−1914.68/−2088.49, mean net 27232.9185). The cur triple matches `w540lv6` (CVaR −433.2656/−493.48/−444.05, worst −1107.386/−1055.88/−1055.88). The brief's −1,055 is that −1055.88 truncated; the context table's −1,056 is the same number rounded. Mean net on that run is 25524.3220, also the number in promote commit `9ccf74cc` (there labeled "pre-rebate"; `report_seeds` `net_pnl` is after rebate). Pre-rebate mean of `w540lv6` is $21,691.74 (seed `pnl_before_rebate` 24235.88 / 19205.81 / 21633.53). No p45 catalog's 3-seed mean is $25,657. The directory named `cur-20261003r2` is a different run: same model sha `41ff16d1`, cap 9, mean net $28,352.16, CVaR −592/−637/−614.

2. **Which step moved which metric (LoL).** verified. One row per step. Deltas are vs the previous row, mean of 3 seeds, after rebate. Late is the equal-match third from `report_seeds` (410 maps on the first three rows, 406 on the last two because shared completed maps shrink). Buy-300s in the total column is the mean of the three seed estimates; late buy-300s is the share-weighted third.

| step | run | model | feats | train | cap | sched | incomplete | net mean (s0/s1/s2) | Δ net | CVaR mean | Δ CVaR | worst s0/s1/s2 | buy fills | buy-300s | late net s0/s1/s2 | late buy-300s |
|---|---|---|---:|---|---:|---|---:|---|---:|---:|---:|---|---:|---:|---|---|
| 12-col ≤540 | `rebuild-20260930` | `20260930T191131Z` | 12 | 2551 maps, rows metric 945,018 | 6 | v6 | 23 | 21,892 (27,288/17,965/20,422) | — | −455 | — | −1,090/−1,706/−1,090 | 10,112 | 2.13¢ | +2,700/+1,939/+1,677 | −0.24/−0.39/+0.01¢ |
| 77-col full-map, cap-matched | `cat77lv6` | `20261002T165228Z` | 77 | 2713 maps, trees 52 | 6 | v8 | 23 | 22,673 (25,557/23,050/19,411) | +781 | −433 | +22 | −937/−865/−877 | 12,460 | 1.55¢ | +2,868/+1,841/+1,866 | 0.56/0.35/0.33¢ |
| 77-col ≤540 | `w540lv6` | `20261002T190131Z` | 77 | 2551 maps, trees 51, dir_300 1.431¢ | 6 | v8 | 23 | 25,524 (28,078/23,035/25,459) | **+2,852** | −457 | −24 | −1,107/−1,056/−1,056 | 11,388 | 2.15¢ | +3,334/+3,225/+3,706 | 0.61/0.52/0.44¢ |
| same model, new code, cap 9 | `cur-20261003r2` | `20261002T190131Z` sha `41ff16d1` | 77 | same as w540 | 9 | v8 | **53** | 28,352 (32,356/26,421/26,279) | +2,828 | **−614** | **−157** | −1,587/−1,387/−1,481 | 13,426 | 1.91¢ | +3,449/+2,895/+2,697 | **0.03/−0.16/+0.01¢** |
| histfix = LIVE | `histfix-20261003r2` | `20261003T014429Z` | 77 | 2551 maps, trees 47, dir_300 1.401¢ | 9 | v8 | 53 | 27,233 (30,347/27,087/24,266) | −1,119 | −617 | −3 | **−1,913/−1,915/−2,088** | 12,981 | 2.03¢ | +5,038/+4,699/+4,265 | 0.56/0.41/0.63¢ |

Sibling, not a chain step: `cat77` is the same full-map model at cap 9 (mean net $27,910, CVaR −571, worst −1,292/−1,274/−1,405, buy fills 14,682, buy-300s 1.51¢, late +4,132/+898/+2,926 at 0.52/0.17/0.30¢). Cap 9, not the window, is what inflates that total. Commit `9af459c0` compares `w540lv6` to `cat77lv6`, which is the cap-matched row above.

Reading the deltas: history columns plus the dataset rebuild (`rebuild`→`cat77lv6`) are +$781 net, inside the seed sd ($4,832 and $3,090), with buy-300s 2.13¢→1.55¢. The ≤540 retrain (`cat77lv6`→`w540lv6`) is the only clean model step that raises total net (+$2,852) and puts buy-300s back to 2.15¢; late markout stays sub-cent. The jump the owner calls "histfix made tails worse" is already present in `cur` (CVaR −157, late markout to ~0) before the retrained catalog. The retrain then gives back late markout and late net (~+$1.7k) and spends it on a worse worst map. Holdout `dir_300` on the shared 840,345-row metric: full-map 1.244¢, ≤540 1.431¢, histfix 1.401¢ (`model.json` `metrics.dir_300_cents`).

3. **Data hashes: only `rebuild`→`cat77` changes the tape. Later rows share one dataset. The backtest code change is not in the manifest.** verified. `compare_backtests.describe_manifest_diffs` on seed0 (script `manifest_diffs.py`).

- `rebuild`→`cat77`, 9 diffs. Unexpected (the tool would refuse without `--allow-drift`): `feed_schedule_rules_version` v6→v8, `signals_sha256` `29357628…`→`65d5bb70…`, `split_sha256` `f10ac368…`→`b30da97d…`. Also `game_features_sha256` `2ed4c110…`→`d9de77d2…`, `schedule_map_sha256` `5c070731…`→`1fb35b13…`, cap 6→9, model sha. `signals_sha256` equals that model's `validation_dataset_sha256`. Unchanged, so not a diff: `audit_sha256`, `market_seconds_sha256`, `league_whitelist_sha256` `0cd64bea…`, the 7 archive exclusions, `source_lag_seconds` 11, `buy_cutoff_second` 480, `selected_matches` 1239, `framework_commit` `c76e77af`, `layer_usdc` 300, `min_abs_delta` 0.02, `exit_abs_delta` 0.015.
- `cat77`→`cat77lv6`: only `max_position_levels` 9→6. Unexpected: none.
- `cat77lv6`→`w540lv6`: only model name/sha (`3ef29c12`→`41ff16d1`). Same game_features, split, signals, schedule map. This pair is the clean window comparison.
- `w540lv6`→`cur`: only `max_position_levels` 6→9. Unexpected: none. Incomplete maps still go 23→53. The tool would call the inputs comparable.
- `cur`→`histfix`: model name/path/sha only. Same cap 9, same dataset hashes. This is the clean policy comparison. `w540lv6`→`histfix` adds the cap on top of the model (5 keys, still no unexpected key) and hides the code change.
- Dota `hist77-20261002`→`hist77cur-20261003r2`: **0 manifest keys differ**. Net $36,764→$33,320, incomplete 4→31. Same model sha `e17004e8`, same `validation_dataset_sha256` `18d69e54…`, same `game_features_sha256` `a2969908…`. The history-policy backtest code is invisible to the manifest because it does not record the esports-trader git sha. `framework_commit` stays `c76e77af` on every arm in this report.

`EXTRACTION_RULES_VERSION = "feed-schedule-v8"` is `src/archive_index/schedule.py:55`, introduced on the way through `c0763471` / `49d73afb` (2026-10-01). `rebuild-20260930` was extracted as v6.

4. **Train window and history policy are in the model artifacts and the commits, not in the backtest manifest.** verified.
- Full-map publish: `a66a4191` (2026-10-02, "no more 540 cap on training/production rows", "Stage 06 fits on all labeled rows") and `cf59c757` (publishes the retrained catalog; archives `20260930T191131Z`). Artifact `20261002T165228Z`: 77 features, `train_matches` 2713, `validation_matches` 2142.
- ≤540 restore: `9af459c0` (2026-10-02 22:00 +0200). `LOL_TRAIN_END_SECOND = 540` blamed to that commit at `src/lol/constants.py:126`. Current slice is `src/lol/06_train_model.py:81-87`. Artifact `20261002T190131Z`: 77 features, `train_matches` 2551, `validation_matches` 2080, same `train_dataset_sha256` `3fe170fe…` as the full-map model (the window is a slice, not a new parquet).
- History policy: `ea5367b9` (2026-10-03 03:01 +0200 = 01:01 UTC). `GRID_HISTORY_POLICY` is `src/shared/utils/dota_features.py:86-87` (`start_second=60`, `max_pivot_gap_seconds=30`, `drop_gap_ticks=True`). Histfix catalog `20261003T014429Z` trained_at `2026-10-03T01:44:29Z`, after that commit, `train_matches` 2551. Live research `20261003T091511Z` (`a2a9e145`): all 10 `member_*.txt` byte-identical to the experiment catalog; `model.json` differs (name/timestamps). Promote of the backtest baseline is `2a0f4b7e`. That commit's "pre-rebate 27,232 vs 25,524" equals the after-rebate means. The experiment page's pre-rebate means (cur 23,840, histfix 22,856 in `docs/experiments/hist-policy-20261003.md`) match `pnl_before_rebate` (cur 27,779/21,981/21,760; histfix 25,921/22,767/19,879).

5. **Dota's parallel chain, same reporter.** verified. After-rebate. Dota LIVE symlink is `hist77-20261002`, not histfix (`readlink`). `oldcat12` model `20260930T180848Z` is 12 features, train_matches 1563. `hist77` / `hist77cur` model `20261001T220726Z` is 77 features, train_matches 1745, trees 69. `histfix` `20261003T014226Z` is 77 features, train_matches 1745, trees 67. `w540` `20261002T213020Z` is 77 features, train_matches 1563, trees 46, **2 seeds** (seed2 absent; `dota-train-540.md`).

| step | run | code era | incomplete | net mean (seeds) | Δ net | CVaR mean | worst mean | buy fills | buy-300s | late net (seeds) | late buy-300s |
|---|---|---|---:|---|---:|---:|---:|---:|---:|---|---|
| 12-col | `oldcat12-20261002` | pre-policy | 3 | 38,564 (40,038/36,534/39,121) | — | −590 | −1,919 | 6,708 | 3.34¢ | +20,026/+20,298/+19,837 | 5.14/5.13/5.08¢ |
| 77-col = Dota LIVE | `hist77-20261002` | pre-policy | 4 | 36,764 (36,542/36,269/37,481) | −1,800 | −379 | −1,010 | 6,291 | 3.52¢ | +17,534/+17,854/+17,614 | 5.53/5.55/5.52¢ |
| same 77-col model | `hist77cur-20261003r2` | policy code, 0 manifest diffs | 31 | 33,320 (seed sd 719) | −3,444 | −399 | −968 | 6,165 | 3.23¢ | +16,961/+17,116/+16,930 | 5.26/5.27/5.23¢ |
| histfix model | `histfix-20261003r2` | policy code | 31 | 38,796 | **+5,476** | −409 | −1,022 | 6,588 | 3.30¢ | +18,568/+18,709/+18,585 | 5.44/5.45/5.46¢ |
| 12-col on that code | `oldcat12-20261003r2` | policy code | 17 | 36,571 | — | −585 | −1,919 | 6,548 | 3.20¢ | +20,050/+20,331/+19,860 | 5.14/5.14/5.08¢ |
| ≤540, old rule | `w540` (seeds 0–1) | pre-policy (recorded in `08f640a5` before `ea5367b9`; incomplete 6 on 2 seeds) | 6 | 39,728 | — | −402 | −1,023 | 6,070 | 3.66¢ | +19,282/+19,582 | 5.44/5.45¢ |

Dota history columns (row 1→2, same v8 tape, cap 9) cost ~$1.8k net and cut the tail roughly in half. Turning the policy **code** on without retraining (row 2→3) costs another ~$3.4k with no manifest diff. Retraining under the policy (row 3→4) gets that back and more (+$5.5k) with tails unchanged. LoL's matching retrain (finding 2, `cur`→`histfix`) does not. Both games' late thirds stay in character across every row: Dota late buy-300s stays ~5.1–5.5¢; LoL late buy-300s never exceeds ~0.6¢ on any arm here. `w540fix` (policy + ≤540, seed2 missing) was not re-reported; `hist-policy-20261003.md` already calls it a tie with histfix on pre-rebate (7 shared seeds, −$1,345, p=0.69).

6. **Earlier LoL experiments already answer pieces of the owner's question. Numbers below are the experiment pages, not re-run here.** cited.
- Horizon (`docs/experiments/lol-horizon-train.md`, 2026-09-01, size $100, cut 540): vs 300s base +$4,156 pre-rebate, h180 +$1,012 (paired t p=0.238, trimmed mean negative), h600 −$1,109, h900 −$3,442 (p=0.028). 300s stayed. Not the same as the 2026-10-02 window change, which keeps the 300s label and only changes which seconds are fit.
- Whole-map model (`lol-late-phase/README.md`, 2026-09-01): +$2,094 vs baseline (p=0.010) and "every dollar of it lands before 2026-08-04". Same shape as today's late third. The 2026-10-02 full-map catalog (`cat77lv6`) lost to ≤540, so this page's "whole-map wins on old maps" is the result that still stands.
- Buy cutoff 900 (`lol-cut900.md`, 2026-09-12): +$121/seed pre-rebate, p=0.81; ¢/share 1.27→0.93; CVaR −$61→−$78. Offline LoL DIR 541–900s was 1.13¢ vs 1.90¢ in 0–540. Live cutoff is now 480 (`b19c5b3c`, 2026-09-18), so a rerun would not be the same experiment.
- League-whitelist retrain (`lol-league-whitelist.md`, 2026-09-08): −$1,039 pre-rebate on 943 shared maps, every seed negative, ¢/share 1.35→0.82. The gate stayed; the filtered model was dropped. Still the standing answer to "just train on the 17 leagues."
- Exit model (`lol-exit-model.md`, 2026-10-02, baseline `w540lv6`): exit-full-l1 mean −$1,633 pre-rebate; sells after second 540 markout −0.413¢→−0.381¢. A full-map exit catalog does not price the late sell. Channel reverted (`9069e177`).
- Gold-velocity off (`lol-nwoff.md`, 2026-09-02): seed deltas +$254/+$356/−$157, late third opposite sign. `2b1ae180` (2026-09-20) then dropped the 30s gate as the Follow300 default. `w540lv6` seed0 summary has `gate_nw_velocity_seconds: 0`.
- Cadence (`grid-v1-cadence.md`, 2026-09-02): kept. LoL 3-seed mean net +$1,590 at the old $100 size. Every run in the table above is already grid-v1.
- No-unwind (`lol-no-unwind.md`, 2026-08-31): kept; the runner no longer takes an unwind clock. Not an open knob.

7. **Dated LoL line since 2026-09-20, only the changes that touch this question.** verified for the commits; backtest numbers are finding 2 unless noted.
- 2026-09-20 `2b1ae180` Follow300 2¢/1.5¢, gold-velocity gate dropped.
- 2026-09-23 `0e843075` LoL book join at 11s; `ca0183e5` pause cancels and cutoff aligned. Lag is 11 in every manifest in this report.
- 2026-09-30 `50053d9b` records `rebuild-20260930` (12-col, v6).
- 2026-10-01 `c0763471` / `49d73afb` schedule extraction v8 and the shared 77/70 catalog contract (Dota first).
- 2026-10-02 `a66a4191` + `cf59c757` LoL 77-col full-map, runs `cat77` and `cat77lv6`.
- 2026-10-02 `9af459c0` ≤540 retrain; `9ccf74cc` promotes `w540lv6`.
- 2026-10-02 `93f19508` exit-model negative, not promoted.
- 2026-10-03 `ea5367b9` one HistoryPolicy; `cbb22aa0` records `cur` and `histfix`; `a2a9e145` + `2a0f4b7e` promote the model and the LIVE backtest link.

## What I ruled out
- A separate run whose mean net is $25,657. The CVaR/worst/turnover fingerprint is `w540lv6` at $25,524.32 after rebate. `cur-20261003r2` is the cap-9 replay of that same model, mean $28,352.
- Treating `w540lv6`→`histfix` as a one-variable step. It changes the model, the cap (6→9), and the backtest code (incomplete 23→53). `cur`→`histfix` is the one-variable model step.
- Treating `rebuild`→`cat77` as "history features only." Unexpected manifest keys: schedule v6→v8, `signals_sha256`, `split_sha256`, plus `game_features_sha256` and `schedule_map_sha256`.
- A dataset rebuild between `cat77`, `w540lv6`, `cur`, and `histfix`. `game_features_sha256` `d9de77d2…`, `split_sha256` `b30da97d…`, `signals_sha256` `65d5bb70…`, `schedule_map_sha256` `1fb35b13…`, whitelist, audit, market seconds, and the 7 archive exclusions are identical.
- Re-proposing horizon 60–900, whitelist-only training, exit-full-l1/l2, cutoff 900, or a gold-velocity toggle. Each has a negative or non-significant page (finding 6), and the velocity gate is already off on these runs.
- Dota histfix as evidence the same retrain should have helped LoL. On the matched new-code pair, Dota histfix is +$5,476 after rebate vs its old 77-col model; LoL histfix is −$1,119 vs `cur`.

## Proposed experiments
The missing LoL cell is the policy at cap 6 (the cap `w540lv6` was promoted at). `cur` vs `histfix` already holds cap 9 fixed, so this is only worth running if live LoL stays at cap 6. Watch the late third, not the total.

```
# from esports-trader, current HEAD, one seed at a time, then merge.
# old model, cap 6, policy code:
uv run --group backtest python -m backtest.run --game lol --validation \
  --name cap6-w540model --model-dir data/lol/models/archive/research/20261002T190131Z \
  --max-position-levels 6 --level-usdc 300 --signal-cadence-seed 0
# histfix model, cap 6:
uv run --group backtest python -m backtest.run --game lol --validation \
  --name cap6-histfix --model-dir data/lol/models/experiments/hist-policy-20261003 \
  --max-position-levels 6 --level-usdc 300 --signal-cadence-seed 0
```

Repeat seeds 1 and 2. Compare with `scripts/compare_backtests.py` against each other and against `w540lv6` (`--allow-drift` only for the cross-code pair). Expected if the cap-9 result generalizes: histfix − w540model on this code is noise on total net, worse worst map, better late buy-300s. Number to watch: late-third net and late buy-300s (the `w540lv6` late band is +$3.2–3.7k and 0.44–0.61¢). Cost: two 3-seed validation catalogs, same shape as the 2026-10-03 A/B (that one used 6 shards).

Do not rerun full-map vs ≤540. `cat77lv6` vs `w540lv6` is already that pair on one tape and one cap: +$2,852 mean net, late markout still ~0.5¢.

## Scripts
`report_seeds.main` writes `<run>/seeds.json` under `data/backtests`. That path is read-only here, so `work/grok-timeline/run_reports.py` points `write_seeds_json` at `work/grok-timeline/<run>-seeds.json` and calls the same `main`. Printed tables are the script's. Rerun from the esports-trader root:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting nice -n 10 uv run --group backtest python \
  /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-03-lol-edge/work/grok-timeline/run_reports.py
```

Manifest diffs use `compare_backtests.describe_manifest_diffs` and `EXPECTED_MANIFEST_DIFFS` (no paired stats, no files under `data/`):

```
PYTHONPATH=src:scripts:../prediction-market-backtesting nice -n 10 uv run --group backtest python \
  /Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-03-lol-edge/work/grok-timeline/manifest_diffs.py
```

Equivalent single-run command, if a `seeds.json` overwrite in `data/` is acceptable:

```
PYTHONPATH=src:scripts:../prediction-market-backtesting nice -n 10 uv run --group backtest python \
  scripts/report_seeds.py data/backtests/lol_maker/validation_join_delta02_x015_cut480_p45_<name> --expected-seeds 3
```
