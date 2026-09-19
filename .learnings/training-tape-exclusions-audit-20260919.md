# Dota and LoL: training examples lost to the whole-map tape filter

Read-only audit on 2026-09-19. No product code, dataset, model, backtest, or live
service was changed. Analysis used the current local raw data and production
prepare functions, with the trade-activity admission rule evaluated separately.
Existing feature/price quality gates were retained.

## Findings: research training period only

| Metric | Dota | LoL |
|---|---:|---:|
| Currently trained maps with labels | 1152 | 2139 |
| Additional maps with valid labeled examples, failing only tape admission | 903 | 760 |
| Additional labeled rows | 5757 | 256356 |
| Possible training maps after row-quality-only admission | 2055 | 2899 |
| Median usable rows per added map | 7 | 400 |
| Full configured window present | 1 of 903 (11 minute slots including prehorn) | 77 of 760 (541 second slots) |
| At least 90% of configured slots present | 211 (>=10 of 11) | 271 (>=487 of 541) |
| First three 5-minute tape buckets pass; a later bucket fails | 229 | 116 |

These are candidates for a controlled training experiment, not evidence that
including every candidate improves prediction or live PnL. Some maps have only
one usable example. Missing rows are not filled or synthesized.

## Method and exclusions retained

Dota: load the current match catalog, keep only maps before
`VALIDATION_START_TIME`, and subtract IDs already in the training parquet.
There were 1333 excluded candidates. Of these, 161 had no market cache, 269
produced no labeled training rows, and 903 produced usable rows while failing
the current tape rule. `load_match_inputs` and `build_minute_rows(..., 10)`
reconstructed the rows using the existing STRATZ validation, net-worth checks,
current-price checks, and +300s label checks. No exceptions or unexpected
tape-passing maps remained in this audit.

LoL: current prepare audit has 760 maps in the research-training event period
with reason `thin_telonex_trades` (the legacy reason name is retained even
though the reader now uses onchain fills). Typed per-map cached builds retain
rows before final tape exclusion. All 760 had already passed the source,
spawn/time, feature, prior, and row-level book gates; each has at least one
finite labeled row in seconds 0..540. All 760 still fail the current onchain
tape rule. Audit event starts were reconstructed as the minimum known map
start per event, preserving the existing series-level chronological split.

LoL training-period exclusions that this proposal does NOT relax:

- Missing prior: 295 maps.
- Missing books: 42.
- Zero labeled rows: 39.
- Livestats invariant violation: 3.
- Aborted feed, missing spawn, window/details mismatch, zero usable rows:
  one each.

Validation-period maps are not proposed for research training. They remain on
their original side of the chronological split. Backtest admission remains
unchanged for the proposed A/B experiment.

Current and future market midpoints use the existing two-sided quote,
freshness (5s), and pair-consistency gates. The proposed change removes only
the requirement for >=10 onchain records in EVERY full 300s bucket from spawn
through map end as a prerequisite for supervised learning.

## Concrete examples

**Dota, Team Falcons–BetBoom, May 29, 2026**, match `8830011623`,
slug `dota2-flc-bb4-2026-05-29`:

- All 11 configured minute examples (-60 through 540) have valid features,
  current prices, and +300s targets.
- Tape counts by 5-minute bucket after spawn:
  `[680, 696, 498, 316, 530, 326, 0, 142]`.
- The empty 30–35 minute bucket excludes the entire map, including its valid
  early training examples.

**LoL, Team Heretics–Fnatic, February 7, 2026, map 1**, game
`115548424308414203`:

- All 541 seconds in 0..540 have valid labeled examples.
- Tape buckets: `[304, 504, 302, 0, 0, 10, 0]`.
- The first 15 minutes pass the tape threshold, but later buckets exclude the
  entire map. All training labels end by game second 840.

Two further LoL examples with all 541 labeled seconds:

- Verdant–WLGaming, March 9, map 2, `116130138006737519`:
  `[52, 14, 18, 32, 44, 66, 40, 8, 130]`; later bucket has 8 rather than 10.
- Misa–S2G, February 19, map 2, `115729531998389598`:
  `[18, 16, 44, 38, 8, 28, 76, 88]`.

For all three LoL examples, reran `build_one_map` directly from raw captures and
books in memory (no cache writes): the full labeled rows exactly matched the
cached rows; all source/price gates passed and the tape gate alone still failed.
Other LoL candidates were inspected from the September 19 typed prepare cache,
with tape independently recalculated; they were not all rebuilt from raw captures.

## Data-distribution caution

Accepted vs candidate mean absolute +300s price change:

- Dota: 8.49c vs 8.57c; exactly-flat labels 2.21% vs 3.53%.
- LoL: 9.73c vs 8.37c; exactly-flat labels 2.22% vs 5.28%.

The excluded LoL pool is somewhat quieter on these summaries. The trade filter
can reflect both capture availability and real activity, so removing it may
change the market population. These summaries do not establish price quality
or predictive value beyond the existing prepare checks.

## Suggested experiment

For training, keep each row only if existing game/time/orientation/prior/current
mid/+300s-target checks pass; discard a whole map only if no usable training rows
remain. Remove whole-map tape activity as a training prerequisite. Keep the
current held-out maps, backtest data requirements, model recipe, and lags fixed.
Evaluate Dota and LoL independently. Do not change lag and training admission in
the same comparison.

## Reproduction

- Script: `/private/tmp/audit_training_tape_exclusions_20260919.py`.
- Log: `/private/tmp/training-tape-audit-20260919.log`.
- Per-map audit tables and summaries:
  `/private/tmp/training-tape-audit-20260919/{dota,lol}_excluded.parquet`,
  `{dota,lol}_summary.json`.
- Fresh LoL checks: `lol_fresh_checks.json` in the same directory.
- Input hashes recorded in `input_hashes.json`; Dota training parquet, LoL
  training parquet, and LoL prepare audit stayed unchanged throughout the scan.
