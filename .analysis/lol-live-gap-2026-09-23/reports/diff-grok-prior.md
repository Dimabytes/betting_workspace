# diff-grok — live `market_radiant_prior`

Date: 2026-09-23. Read-only on `esports-trader`, plus GET `https://clob.polymarket.com/prices-history` (fidelity 1). The live fetch is `fetch_market_prior` (`src/trader/market_prior.py:15-42`): last aligned minute pair strictly before `horn_unix_seconds - 90`, window 6 hours (`QUOTE_TRAILING_SECONDS`, `HORN_OFFSET_SECONDS = 90`).

## TL;DR

1. **The 15 worst LoL gaps are the horn−90 minute bar, not a flipped side or a swapped token.** Replaying today's prices-history with `match.json`'s tokens, `yes_is_radiant`, and horn reproduces the session prior to 0.0¢ on 14/15 maps (one map is 4.6¢ off the current API). The bar is 1–65 s before the anchor. The GRID horn is within 1 s of the livestats spawn. The training prior is the Telonex book in `[spawn−61s, spawn)`, so it sees a jump the minute print at horn−90 has not printed yet. On the three headline maps that jump crosses ~0.5, so `1 − live_prior` lands within 2.5¢ of the training prior. That is the same token moving, not an orientation bug. **verified.**
2. **Prior vs first model mid, every local tape with both.** LoL 293 maps: **30.0% (88) >5¢, 9.6% (28) >10¢**, median 3.0¢. Dota 276 maps: **40.2% (111) >5¢, 11.2% (31) >10¢**, median 4.0¢. Dota GRID 37.2% / 11.1%, Dota Oddin 54.0% / 12.0%. **verified.**
3. **`_maybe_start_prior` does not run before `_pin_feed_orientation`.** The first tick pins `yes_is_radiant` and does not fetch. The fetch starts on a later tick, after `_quoting` is set, and passes that pinned flag. **verified.**
4. **Dota does not have the LoL train-vs-live prior gap.** On 155 Dota tapes whose `steam_match_id` is in the catalog, live prior vs catalog `radiant_prior` is median **0¢**, mean abs **1.17¢**, **8.4% >5¢**, **2.6% >10¢**. Both sides are prices-history. LoL training is a different print (the book in the last minute before spawn). The prior-vs-in-game-mid gap in (2) is shared, and larger on Dota. **verified.**

## 1. Fifteen worst LoL maps

Worst by `|live_prior − train_prior|` in `work/orchestrator/prior_parity_lol.csv`. Replay uses the trader window: `startTs = horn−90−6h`, `endTs = horn−90`, same `last_aligned_pre_anchor_pair` and `normalize_pair_mids`. Horn and tokens come from `data/trader/<dir>/match.json`. Spawn is `validation.parquet` `state_ts_us` at `second == 0`.

| dir | train | session prior | replay | Δ replay−session | yes=radiant | bar age | session mid | mid token | horn−spawn |
|---|---:|---:|---:|---:|---|---:|---:|---|---:|
| grid-2965525-m1 | 0.360 | 0.660 | 0.660 | 0.0¢ | true | 6 s | 0.415 | yes 0.415 | −0.8 s |
| grid-3005562-m3 | 0.155 | 0.425 | 0.425 | 0.0¢ | false | 4 s | 0.205 | no 0.205 | −0.7 s |
| grid-3004388-m5 | 0.625 | 0.400 | 0.400 | 0.0¢ | true | 4 s | 0.635 | yes 0.635 | −1.0 s |
| grid-2965529-m3 | 0.600 | 0.721 | 0.675 | −4.6¢ | false | 60 s | 0.665 | no 0.665 | −1.0 s |
| grid-2973272-m4 | 0.545 | 0.435 | 0.435 | 0.0¢ | false | 7 s | 0.545 | no 0.545 | −0.2 s |
| grid-3005563-m2 | 0.560 | 0.660 | 0.660 | 0.0¢ | false | 22 s | 0.600 | no 0.600 | −0.6 s |
| grid-2968612-m3 | 0.725 | 0.625 | 0.625 | 0.0¢ | false | 19 s | 0.560 | no 0.560 | −0.7 s |
| grid-2968612-m2 | 0.305 | 0.395 | 0.395 | 0.0¢ | true | 65 s | 0.315 | yes 0.315 | −1.0 s |
| grid-3002599-m3 | 0.575 | 0.495 | 0.495 | 0.0¢ | true | 20 s | 0.505 | yes 0.505 | −0.8 s |
| grid-3005562-m1 | 0.500 | 0.575 | 0.575 | 0.0¢ | false | 33 s | 0.335 | no 0.335 | no second-0 row |
| grid-2999024-m1 | 0.270 | 0.200 | 0.200 | 0.0¢ | true | 34 s | 0.505 | yes 0.505 | −0.7 s |
| grid-2999024-m2 | 0.245 | 0.175 | 0.175 | 0.0¢ | false | 36 s | 0.300 | no 0.300 | −0.4 s |
| grid-3000371-m2 | 0.415 | 0.485 | 0.485 | 0.0¢ | false | 3 s | 0.435 | no 0.435 | −0.6 s |
| grid-3002602-m2 | 0.555 | 0.625 | 0.625 | 0.0¢ | false | 29 s | 0.585 | no 0.585 | −0.2 s |
| grid-3002597-m2 | 0.755 | 0.695 | 0.695 | 0.0¢ | false | 51 s | 0.765 | no 0.765 | −0.3 s |

Each history has 359–361 points, i.e. a minute slot across the whole 6 h. The timestamp on the chosen bar is never hours behind the anchor.

**Class for all 15: minute bar at horn−90, while the spawn book has moved.** Not a stale previous-map bar, not an orientation flip, not a token mix-up.

Why it is not a flip. On every map the first model `market_p_radiant` equals `yes_mid` when `yes_is_radiant` is true and `no_mid` when it is false. The prior uses that same flag (`market_prior.py:31-34`). A wrong flag would have moved the prior and the mid together. Here the mid sits next to the training prior, and the prior is an earlier price of the same token.

Why `1 − prior` looks like the training prior on the headlines. The price moved through ~0.5 inside the 90 s the anchor skips, so the complement of the old print is near the new print.

- **grid-2965525-m1.** Trader window: YES 0.66, NO 0.34, 6 s before horn−90, flag true → 0.66. A second request that extends past the horn (buckets can differ from the trader window; used only as the path) shows YES still 0.66 at about horn−40 s and **0.36 at about horn+20 s**. Training prior is 0.36. Live mid 0.415 is the YES book at the first model tick. `1−0.66 = 0.34` is 2¢ from 0.36 because YES itself went 0.66 → 0.36.
- **grid-3005562-m3.** Flag false, so radiant is the NO token: 0.425 at horn−90. The extended path shows YES 0.575 until the horn and **0.805 about 20 s later**, so NO/radiant goes 0.425 → ~0.195. Training prior 0.155 and the live mid 0.205 (the NO book) are that post-jump price.
- **grid-3004388-m5.** YES 0.39 and NO 0.585 at the anchor, sum 0.975, normalized 0.39/0.975 = 0.40. Extended path: YES still ~0.375 a minute after the horn, then **0.635**. Training prior 0.625 and live YES mid 0.635 are the post-jump book. The minute series is about a minute late to it. `1−0.40 = 0.60` is 2.5¢ from 0.625 for the same reason.

The other twelve are the same shape at 6–12¢: the print before horn−90 and the book at spawn differ by a pre-horn move, and the live mid follows the spawn book (or a later in-game print), not the old minute. **grid-2965529-m3** is the only replay that misses the session (0.675 vs 0.721). Today's bar 60 s before the anchor is YES 0.325; the session stored a normalized 0.721. The API series no longer contains that print. The mid is still the NO book (0.665) and the horn still matches spawn, so it is the same class plus a revised history, not a second bug.

**grid-3005562-m1** has no `second == 0` validation row, so horn−spawn was not measured. Replay still matches the session prior exactly.

## 2. Prior vs first model mid, all local tapes

Script walks `data/trader/*/session.jsonl` until the first `signal` with `reason == "model"` after a non-null `market_radiant_prior`. Gap is `|prior − market_p_radiant|`. 585 archives: LoL 298 with a session (293 usable), Dota 287 (276 usable). The rest never latched both.

| | n | >5¢ | >10¢ | median | mean | p90 |
|---|---:|---:|---:|---:|---:|---:|
| LoL GRID | 293 | 88 (30.0%) | 28 (9.6%) | 3.0¢ | 4.31¢ | 10.0¢ |
| Dota all | 276 | 111 (40.2%) | 31 (11.2%) | 4.0¢ | 5.31¢ | 10.3¢ |
| Dota GRID | 226 | 84 (37.2%) | 25 (11.1%) | | | |
| Dota Oddin | 50 | 27 (54.0%) | 6 (12.0%) | | | |

This gap is not the train-vs-live gap in section 1. It is the prior (a minute print from before the horn) against the book at the first model tick, which on these tapes is often around a minute into the map. A move after the horn shows up here even when the prior matches training.

## 3. Can the prior start before the feed pin?

No, on the current `MatchWorker.run` loop.

- `__init__` copies discovery's `yes_is_radiant` and sets `_quoting = False` (`src/trader/match_worker.py:178-183`).
- The first tick calls `_pin_feed_orientation`, which overwrites `_yes_is_radiant` from `event.yes_is_radiant` (`:210-211`, `:240-243`), then `_try_attach` (`:218`), then `continue` (`:219`). `handle_event` is not called on that tick.
- `_quoting` becomes true only at the end of a successful `_try_attach` (`:344`), which has already happened after the pin.
- Later ticks call `handle_event` only if `_quoting` (`:221-222`). That calls `_maybe_start_prior` (`:357`, `:502-509`). `_load_prior` passes `yes_is_radiant=self._yes_is_radiant` into the thread (`:515-521`). Nothing else writes the flag.

So the fetch cannot observe the discovery flag. The 14 exact replays used the flag stored in `match.json`, which is the pinned value, and matched the session prior. A race here is not what produced the 20–30¢ gaps.

## 4. Dota

Live Dota calls the same `fetch_market_prior` (horn−90, 6 h, minute bars) and the same pin-then-fetch order. Section 2 is the in-game consequence: Dota's prior is further from the first model mid than LoL's, and Oddin is the worst of the three feeds.

Training is not the LoL book window. Dota's catalog prior is the last aligned prices-history pair before the **horn**, on tokens already oriented by `radiant_token_index` (`src/collect/s05a_fetch_prices_history.py:70-73` and `:192-214`). Live is the same API, 90 s earlier, oriented by the pinned flag.

Join of live tapes to `data/new_processed/match_catalog/match_catalog.parquet` on `steam_match_id` (155 of 276 Dota maps with a model prior):

| live prior − catalog prior | |
|---|---:|
| n | 155 |
| median abs | 0¢ |
| mean abs | 1.17¢ |
| share >5¢ | 8.4% |
| share >10¢ | 2.6% |

LoL's comparable figure, from the orchestrator file (161 maps, book prior vs session prior), was mean abs 2.74¢ and 13% above 5¢, with a 30¢ tail. That tail is the horn−90 minute versus the spawn book. Dota never takes the spawn book, so the two prices-history anchors usually print the same number.

## What would change the LoL prior

Words only. Point the live anchor at the horn, or at the last book in `[horn−61s, horn)`, the way `lookup_strict_prior` builds the training prior (`src/lol/05_prepare_dataset.py:180-183`). The 90 s offset is what skips the jump on grid-2965525-m1, grid-3005562-m3, and grid-3004388-m5. Do not flip `yes_is_radiant` to chase `1 − prior`: the mid and the training book are already on the session's side.

## Scripts and outputs

- `work/diff-grok/prior_followup.py` — tape scan plus the 15 replays. Writes `prior_vs_first_mid.csv`, `worst15_repro.csv`.
- `work/diff-grok/prior_path.py` — extended prices-history around the horn, and horn versus spawn. Writes `worst15_path.csv`. The extended window is not the trader request; bucket edges can move. The replay table uses the trader window only.
- Dota catalog join was an inline `uv run python` against `match_catalog.parquet` and `prior_vs_first_mid.csv`. Numbers are in section 4.
