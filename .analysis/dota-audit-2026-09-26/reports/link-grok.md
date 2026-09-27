# link-grok — market↔game linking and side orientation

Status: FINAL

HEAD `bbb28897` (2026-09-26). Script: `.analysis/dota-audit-2026-09-26/work/link-grok/check_links.py`. Counts below are from that run (`PYTHONPATH=src uv run python` in `esports-trader`).

## Summary

- Catalog orientation holds on the clock N4 says is right. Of the 2,436 maps with an `ok` mid in `market_seconds/v9c88adc2` within 180s of `duration`, 2,276 use a `grid_derived` horn. **0** of those have a final radiant mid settled on the wrong side (`radiant_win` and mid ≤ 0.10, or a loss and mid ≥ 0.90). When Radiant won, the median end mid is 0.99 (p05 0.845); when Dire won, the median is 0.01 (p95 0.124). The other 160 are `archive` horns, early by the pre-horn pause (orchestrator N4/N4b), so that mid is not the settlement print. N3’s rebuild of 34 cached maps matched the stored `market_p`, so this read is the current orientation, not a stale cache.
- The training rule “YES token = event `team_a`” is true for every candidate market whose Gamma outcome names resolve. 4,904 map-winner candidates and 324 series-winner candidates orient forward. **0** candidates orient backward. Introduced in `211823e52` (2026-08-06), still at `s02_link_opendota.py:390`.
- Live agrees with that catalog on the overlap: 174 archived `match.json` files share a condition id with the catalog, and **0** disagree on winner or on `yes_is_radiant` vs `radiant_token_index == 0`. 281 sessions with a final Polymarket `market_p_radiant` and a feed winner have **0** hard flips. **0** of 657 archives have a slug `-gameN` that disagrees with `map_number`.
- Series contracts in the catalog are only deciders: 254 BO1 as map 1, 410 BO3 as map 3, 13 BO5 as map 5. Re-checked after N2: the BO3 rows whose catalog is missing game 1 or 2 are still link game 3 of a series that played three maps. One of those game-2 markets never linked (F4). Map-winner `game_number` matches the universe label on all 2,530 catalog map markets.
- One real gate is weak. `s03_merge_archive_links.py` still attaches an archive whose Steam match id differs from the OpenDota id when the winners agree (`0d1ae767`, 2026-09-21). Two catalog rows are in that state. Both have the same teams, the same winner, and the same duration to the second, so they look like one map under two ids, not a swapped series. The check would also accept a true collision that happened to share a winner.

## Findings table

| ID | Sev | Layer | Title | Confidence | Impact |
|---|---|---|---|---|---|
| link-grok-F1 | S3 | collect link | Archive with a different Steam id attaches when winners agree | verified | 2 catalog maps today; a future collision with the same winner would train and replay the wrong horn/schedule on that market |
| link-grok-F2 | S4 | archive index | Dota universe lookup drops Gamma outcome names | verified | Side audit cannot see a YES/team_a swap from Gamma labels; it did not hide one in this snapshot |
| link-grok-F3 | S4 | collect link | A map under 10 minutes drops the whole OpenDota series, and the audit reason does not say so | verified | Real short maps never enter via OpenDota; a remake longer than 10 minutes can still shift Game N. No shifted map showed up in the book check |
| link-grok-F4 | S3 | collect link | Admitted GRID map with no Steam id and no OpenDota row never becomes a link | verified | 2 played map-winner markets dropped (`no_steam_no_link`). One is game 2 of a BO3 whose decider is in the catalog |

## Findings detail

### link-grok-F1 — Different Steam id still attaches when the winner matches

Where: `src/collect/s03_merge_archive_links.py:328-341` and `:369-373`, HEAD. Commit `0d1ae767` (2026-09-21).

`check_condition_attach` rejects a Steam-id mismatch only when `_winner_conflict` is true. That helper returns `None` when the archive has no winner or the OpenDota file is missing, and `False` when both say Radiant or both say Dire. `None` and `False` both fall through to `_attach`, which stamps `identity_conflict="archive_steam_mismatch"` and keeps the row. Catalog publish (`s06_publish_catalog.py:161-166`) drops a row only when the archive winner contradicts STRATZ `radiant_win`. Agreement keeps the archive horn and schedule on the OpenDota match id.

Evidence: `match_links` has 2 such rows, both in the catalog (3,207 rows).

| catalog match | archive steam | slug | game | duration | both winners |
|---|---|---|---|---|---|
| 8983815575 | 8983774433 | `dota2-pi-pckcp-2026-09-05-game1` | 1 | 2435s | radiant |
| 8989070993 | 8989036769 | `dota2-synaps-pi-2026-09-08` | 3 (series decider) | 2579s | radiant |

OpenDota `match_8983815575` is Pipsqueak+4 vs PuckChamp, start `2026-09-05T12:18:52Z`, duration 2435, radiant win. The live archive `data/trader/grid-2996018-m1/match.json` is the same teams, winner radiant, `final.duration_seconds` 2435, horn `2026-09-05T12:23:25Z` (273s after the OpenDota start). The second pair is Team Synapse vs Pipsqueak+4, duration 2579 on both sides, horn 281s after the OpenDota start (`grid-2996031-m3`). The other Steam ids are not in `data/raw/opendota_matches`. Identical duration is the same replay recorded under two ids (live stats id vs the id OpenDota stored), not two maps.

Impact: these two rows are not a side swap and not a wrong market. The gate is still wrong for the next collision: about half of unrelated maps share a winner, and the horn/schedule of the archive would then be applied to a different OpenDota match. STRATZ features stay on the catalog `match_id`; the book stays on `map_condition_id`. A wrong horn shifts every feature join for that map.

Confirm or fix: reject (or keep the OpenDota row and drop the archive stamp) unless the Steam ids are equal. Do not treat “both radiant” as identity. The ~270s between OpenDota `start_time` and the archive horn on these two rows is a different comparison from N4 (archive horn early versus the GRID-derived horn). Here the durations match to the second, so the ids are one map.

### link-grok-F2 — Dota universe index never stores outcome names

Where: `src/archive_index/universe.py:42-60`. `outcome_names=None` on every Dota row. `audit_identity` (`src/archive_index/index.py:184-192`) then orients the archive’s outcome labels against `team_a`/`team_b` only.

The Gamma cache does have the labels (`gamma_markets_index.json`, 74,492 universe rows, 0 token-order mismatches against `token_id_0/1`). Candidate markets resolve as YES = `team_a` (see Checked and OK). The hole matters the day a candidate’s YES token is `team_b`: the archive audit would not notice from Gamma, and `s02` would still write `radiant_token_index` from the team-id assumption. That day has not happened in this snapshot (0 reversed candidates).

### link-grok-F3 — Short map drops the series without a distinct reason

Where: `src/collect/s02_link_opendota.py:157-199`. `MIN_CONFIDENT_MAP_DURATION_SECONDS = 600`. Any map under 10 minutes prevents the series from being emitted, which also throws away the longer maps of that series. `opendota_link_audit` then says `no_complete_series_in_window` (516 events) with no short-map split. Two events are `multiple_complete_series` and were dropped rather than guessed (`s02:332-338`).

This is the remake protection: a sub-10-minute replay does not become Game 1 and shift every later Game N. The leftover is a remake that lasts at least 10 minutes. Catalog maps with OpenDota duration 600–899s: 12. None of them produced a hard book-vs-winner flip on a `grid_derived` horn, so no shifted Game N is visible in settlement. Map-winner universe `game_number` equals the link `game_number` on all 2,530 catalog map markets (the shift would still show the same number on both columns; the book check is the real test).

### link-grok-F4 — No Steam id and no OpenDota row means no link

Where: `src/collect/s03_merge_archive_links.py:398-399`. `resolve_new_slot` returns `no_steam_no_link` when the archive’s condition is not already an OpenDota row and `steam_match_id` is null. The audit then stops. Introduced with the merge in `de273edd` (2026-09-21).

Two admitted Dota archives are in that bucket (`match_link_audit`: `no_steam_no_link` = 2). Both are played Game 2 markets:

| archive | slug | winner | duration |
|---|---|---|---|
| `grid-3007988-m2` | `dota2-yes-tm6-2026-09-16-game2` | dire | 2009s |
| `grid-3007246-m2` | `dota2-nemiga-z10-2026-09-15-game2` | dire | 2990s |

`grid-3007988` did play three maps: m1 Radiant 1368s, m2 Dire 2009s, m3 Radiant 3324s on the series market. Map 3 is a real decider. Map 2 is the hole. Two other null-Steam archives (`grid-2996037-m1`, `grid-3005969-m2`) attached by condition onto an existing OpenDota row, so a missing Steam id is fatal only when the condition is new.

Impact: those two maps are absent from the catalog, the dataset, and the backtest. Neighbours in the same series are present, which makes the series look like it skipped a map. Not a side swap.

Confirm or fix: mint the link from `grid-{series}-m{n}` when Steam is missing, or keep the condition id as the match key. The row already has a condition, a map number, and a winner.

## Architecture / performance / debuggability notes

1. Three orientation paths, and they meet. Training (`s02:390`) sets `radiant_token_index` from OpenDota team id vs event `team_a`, assuming token 0 is YES and YES is `team_a` (`s01:295-298` stores the YES token as `token_id_0`; teams come from `event.sports.teams` or the title, `s01:279-283`). Live and the archive index call `orient_outcomes` on the outcome labels (`discovery.py:560-568`, `archive_index/index.py:194-204`) and do not assume YES is index 0 (`index.py:155-156`). Oddin features use the payload `faction` field, not the market (`oddin_feed.py:132-151`). The market side is `yes_is_radiant` on the feed event, locked from the first tick (`match_worker.py:254-267`) into `radiant_token_index` 0 or 1 (`match_worker.py:990`). Backtest settlement pays the token at `radiant_token_index` (`marks.py:53-60`).
2. A running worker will not follow a later discovery rebind. Once a session is pinned, a change of map number, tokens, or match id is skipped (`wallet_host.py:1115-1121`). While the worker is up, its `yes_is_radiant` is copied onto the new identity before the compare (`wallet_host.py:240-249`), so a GRID side relabel does not look like a rebind. The map gate that runs before launch is `_expected_map` (`discovery.py:853-875`): a Game N market binds only when the source’s current map is N.
3. Disir does not carry an Oddin id on the market. `unique_oddin_match_id` (`oddin_discovery.py:68-113`) keeps the single open card whose home/away names orient against the already chosen Radiant/Dire, or, if several cards match, the single one whose Disir snapshot is already a map in progress. A probe fault counts as not playing. The Oddin id is stamped onto a Steam or GRID source; it does not choose the map. Catalog scan is `oddin_catalog.py` `DisirCatalog` (commits `dec98290`..`bbb28897`).
4. Series vs map is a classifier, then a decider rule. `classify_contract` (`s01:250-261`) marks `Game N Winner` or `child_moneyline` as `map_winner`, a scoreline as `other`, and a moneyline / “A vs B” question as `series_winner`. `series_winner_covers_map` (`series_format.py:16-18`) is BO1/BO3/BO5 and only the last map, and only when that Game N market does not already exist. The same rule is used when an archive mints a link (`s03:405-420`) and when live discovery emits a series market (`discovery.py:862-875`).
5. Debuggability: `identity_conflict` is easy to miss. It is not a catalog column, and `s07` prints resolution counts but the two attached mismatches look like ordinary `opendota+archive` rows. `winner_source` on the catalog is always `stratz` (`s06:254`). Archive admission on this index: 219 admitted, 68 `condition_not_in_universe`, 10 `feed:no_window_updates`, 7 `record:no_terminal`, 1 duplicate, 1 corrupt feed. No `side_mismatch` or `sides_conflict` in the published Dota index.

## Checked and OK

Question 3 counts. Catalog 3,207 maps. Book cache `data/new_processed/market_seconds/v9c88adc2` (newest of the three version dirs, 2026-09-24 16:45). Join is `market_status = 'ok'`.

| check | n | result |
|---|---|---|
| catalog maps | 3207 | |
| any ok mid in that cache | 2868 | 339 maps have no ok quote here, so they are unchecked |
| ok mid within 180s of `duration` | 2436 | 2276 `grid_derived` (settlement sample), 160 `archive` (horn early by the pre-horn pause, N4; not a settlement read) |
| hard flip on `grid_derived` (win and p≤0.10, or loss and p≥0.90) | 0 | same 0 if the 160 archive-horn rows are included; those mids are early |
| end mid on the other side of 0.5 | 24 | listed below; not settled swaps |
| prior and first in-game ok mid on opposite sides of 0.5, both at least 8c from 0.5 | 13 | every one of these settles on the correct side (`prior_flips.csv`) |
| live `match.json` | 657 | 292 have `final.winner` |
| live condition id also in the catalog | 174 | 0 winner disagreements, 0 `yes_is_radiant` vs `radiant_token_index` disagreements |
| live session last Polymarket `market_p_radiant` vs feed winner, same hard threshold | 281 | 0 flips |
| slug `-gameN` vs `map_number` | 657 | 0 mismatches |
| candidate duplicate slugs | 0 | |
| catalog duplicate slugs | 0 | |

Gamma names vs `team_a`/`team_b` (`orient_outcomes`, `TEAM_ALIASES`), candidates only:

| inventory | kind | verdict | n |
|---|---|---|---|
| candidate | map_winner | YES is team_a | 4904 |
| candidate | map_winner | unresolved | 4 |
| candidate | map_winner | no Gamma names | 36 |
| candidate | series_winner | YES is team_a | 324 |
| candidate | series_winner | no Gamma names | 1 |
| candidate | either | YES is team_b | 0 |

The 1,396 markets where YES is team_b are all `contract_kind=other` and `inventory_status=excluded` (props / scorelines). They are not linked.

The 4 unresolved candidates are name-pair failures, not reversed YES. Outcomes say “Team Cobra”; the event team is `TeamCompromiso` (`dota2-pari-tc-2025-11-16-game1/2`, `dota2-ngx-tc-2025-11-17-game1/2`). Side B misses `SIDE_SCORE_MIN` (0.72), so `orient_outcomes` returns None even though outcome 0 is PARIVISION or Nigma Galaxy, i.e. `team_a`. None of the four slugs are in the catalog. Fail closed.

End mids on the wrong side of 0.5 (the suspicious list). `gap_s` is `duration - last_ok_second`. A swapped token would sit near 0 or 1 for the whole map and contradict the prior. These do not.

| match_id | slug | radiant_win | last_p | gap_s | prior | what the path shows |
|---|---|---|---|---|---|---|
| 8992034384 | dota2-navi-ks-2026-09-10-game1 | True | 0.120 | 0 | 0.66 | archive horn `16:19:40Z`, pause 449s at second −47. N4b: that horn is the first PRE_HORN pin; the GRID-derived horn is ~16:27:08. The 0.12 mid is ~7.5 min before the true end, not a settlement |
| 8644754899 | dota2-xctn-nem-2026-01-11-game2 | True | 0.125 | 97 | 0.42 | underdog won; 13% of ok seconds were ≥0.5; book stopped 97s before the end |
| 8671373589 | dota2-roar-ybtear-2026-01-31 | True | 0.155 | 91 | 0.29 | first ok second is 612; underdog; book ended 91s early |
| 8733000977 | dota2-lynx-z10-2026-03-17-game1 | True | 0.165 | 61 | 0.42 | mid-map 0.85, then a late swing |
| 8782773236 | dota2-sar1-cb3-2026-04-23-game1 | True | 0.255 | 32 | 0.26 | price stayed with the prior (Dire); Radiant won anyway |
| 8778923632 | dota2-iac-satan-2026-04-20 | True | 0.350 | 83 | 0.45 | 75 ok seconds, first at second 430 |
| 8735563038 | dota2-pain-l1ga-2026-03-19-game2 | True | 0.360 | 54 | 0.64 | 94% of seconds ≥0.5, then a late swing |
| 8920655724 | dota2-pckcp-z10-2026-07-30-game2 | True | 0.415 | 2 | 0.80 | opened 0.83 |
| 8780553618 | dota2-z10-l1ga-2026-04-21-game1 | True | 0.415 | 15 | 0.42 | first ok second 351 |
| 8517060495 | dota2-tundra-tftwo-2025-10-18 | True | 0.460 | 106 | 0.18 | 175 ok seconds, first at second 1319 |
| 8689505222 | dota2-flc-liquid-2026-02-13-game3 | True | 0.465 | 8 | 0.54 | coin-flip ending |
| 8728628201 | dota2-ic-shpili-2026-03-14-game2 | True | 0.470 | 75 | 0.40 | |
| 8809244962 | dota2-ts8-vg-2026-05-13-game2 | True | 0.485 | 29 | 0.62 | |
| 8755468072 | dota2-vg-yb1-2026-04-03-game2 | True | 0.490 | 1 | 0.31 | |
| 8693807797 | dota2-l1ga-lynx-2026-02-16-game2 | False | 0.510 | 60 | 0.62 | |
| 8677288942 | dota2-z10-1win-2026-02-04-game2 | False | 0.520 | 30 | 0.48 | |
| 8796591894 | dota2-enjoyb-stels-2026-05-03 | False | 0.555 | 29 | 0.65 | |
| 8600568696 | dota2-nem-amaru-2025-12-11-game1 | False | 0.560 | 10 | 0.76 | |
| 8910982575 | dota2-aion-dan-2026-07-24-game2 | False | 0.570 | 43 | 0.37 | |
| 8736339236 | dota2-1win-pain-2026-03-20-game1 | False | 0.595 | 25 | 0.52 | |
| 8796495549 | dota2-enjoyb-stels-2026-05-03-game2 | False | 0.625 | 4 | 0.67 | closest loser-side miss; still 0.62, not ~1 |
| 8697596107 | dota2-ty-pain-2026-02-19-game2 | False | 0.630 | 180 | 0.78 | last ok quote is exactly 180s before the end |
| 8663668912 | dota2-yes-ic-2026-01-25-game1 | True | 0.430 | 73 | 0.40 | |
| 8592272555 | dota2-flc-ty-2025-12-06-game2 | True | 0.445 | 126 | 0.37 | |

The other 23 rows in that list use a `grid_derived` horn, so N4’s clock check applies to them: the end mid is the end of the map, and it is a late swing or an underdog win, not a token swap. The 13 prior-vs-first-mid crossings are in `work/link-grok/prior_flips.csv`. Eleven have a first ok second of 357 or later (the book was missing at the horn, so the “first” mid is mid-map). The one quote at second 0 is match 8657020036: prior 0.34, first mid 0.655, last mid 0.035, Radiant lost. Settlement matches `radiant_win`.

Question 1, token mapping, as it stands in code and in this snapshot.

- Training and the prior. `radiant_token_index` 0 means the YES token is Radiant. `s05a_fetch_prices_history.py:103-110` reads that index and pulls `radiant_prior` from that token. Catalog stores the index; `build_market_data.py:108-109` prices `market_p_radiant` off it. Labels and the prior therefore share one index.
- Backtest. `MarketContext.radiant_token_index` (`context.py:81`) and `settlement_value_for_token` (`marks.py:53-60`).
- Live. Discovery sets `yes_is_radiant` from `orient_outcomes` of the outcome labels against Steam Radiant/Dire (`discovery.py:560-568`) or against GRID’s RADIANT/DIRE info text (`grid_feed.py:89-102`, `game_profile.py:54-55`). The BUY is the favoured token after `yes_fair_from_model` flips the model’s Radiant fair when YES is Dire (`model_server.py` via `match_worker.py:446`).
- Name failures fail closed: exact forward/reverse tie, pair average under 0.82, or either side under 0.72 (`team_names.py:123-142`). Ambiguous Steam name links drop the Steam source (`discovery.py:584-592`). Two events with two complete series in the 4-hour window were left unlinked.

Question 2, map number. OpenDota maps are ordered by `start_time` inside a team-id + league series (`s02:166-202`) and joined to the market’s `Game N` (`s02:434-438`). Unplayed map 3 is simply absent: a 2–0 series has two maps, so Game 3 is not linked. A remake under 10 minutes drops the series instead of shifting it (F3). Relists: 0 duplicate candidate slugs, and `validate_links` rejects a repeated `map_condition_id` (`s02:461`). The two Steam-id disagreements are F1, not a second market on the same slug.

Question 4, live binding. A Game N sidecar is emitted only when the source map equals N (`discovery.py:799-800` and `:868-869`). Series markets emit only on the decider. 0 slug/map mismatches in the local archives. Rebind before the first tick replaces the handoff (`wallet_host.py:314-318`); after the archive exists it refuses (`match_meta.py:305-322` refuses a different `map_number` or market on an existing `match.json`). Disir matching is name-based, described above.

Question 5, series vs map. Catalog contract kinds: 2,530 `map_winner`, 677 `series_winner`, 0 missing from the universe. The 677 break down as best-of × link game number: (1,1)=254, (3,3)=410, (5,5)=13, and nothing else. N2 is right: this is the decider rule, not a mislabeled map market. Re-check of the BO3 series rows that do not have both game 1 and game 2 in the catalog (25 by `game_number`: 3 with none, 22 with one): every one is still link game 3. 24 of those events have games 1, 2, and 3 in `match_links`. The exception, event `1030305` (`dota2-yes-tm6-2026-09-16`), has no game-2 link, but `grid-3007988-m2` was played (Dire, 2009s) between m1 (Radiant) and m3 (Radiant on the series market), so map 3 was 1–1 going in. That missing game 2 is F4, not a series market used as an early map. 0 `map_winner` rows have a null `game_number`.

## Open questions / Needs from VPS

- None for this scope. Local `data/trader/*/match.json` plus `session.jsonl` covered the live side. Collector book snapshots on the VPS were not read; the Telonex-derived `market_seconds` cache and the trader’s own session mids are the two books that were compared to winners.
- 339 catalog maps have no `ok` mid in `v9c88adc2`, so their settlement was not checked.
- Whether any catalog map of 600–899s is a remake rather than a stomp is not separable from duration alone. Settlement does not show a shifted Game N.
