# link-devin — collection and linking audit (history and live)

Status: complete.

Scope of this report: Polymarket → lolesports linking (history), Polymarket → GRID
linking (live), orientation/labels/map numbers, and the live-vs-backtest map
population. Model, execution, and trading-logic stages are owned by other agents.

## TL;DR

- **Live linking is clean.** 305 archived LoL GRID sessions audited: zero
  wrong-map, wrong-side, or wrong-winner bindings. `radiant_fair` vs
  `market_p_radiant` correlates at median 0.99 with *no* anti-correlated map —
  a swapped live side would show up there and none does.
- **Historical linking is almost clean, with one real defect class:** 4 of 5,565
  linked games have lolesports `gameMetadata` blue/red team IDs swapped relative
  to the actual in-game rosters. All 4 produce feature/label-inverted training
  rows. 3 are LPL (excluded from the traded-league backtest by `no_live_feed`
  but still in training data); 1 is NACL (in backtest scope):
  `116884625219920161` (Blue Otter vs CCG Esports, game 1).
- **Historical coverage has real gaps, none of them mislinks.** 41 of 5,501
  series have fewer processed games than the official final score; 16 of those
  are linked series; 17 resolved PM markets exist for games that were never
  fetched. Zero extra/remake rows; no series exceeds its official score, so
  numbering never shifts.
- **The live-vs-backtest gap is mostly a population/admission story, not a
  linking story.** Pre-whitelist live sessions included 61 maps in
  non-whitelisted leagues (216 fills, -224.55 session PnL). Post-whitelist, all
  admitted maps are whitelisted leagues, and still lost -177.08 over 117 maps —
  so the whitelist fixed admission but did not fix PnL.
- **Backtest admission is much stricter than live admission.** `thin_telonex_
  trades` alone drops 1,142/5,565 historical maps (20%); live has no tape-density
  gate — 48 live maps priced <50% of the 0–540s window still traded 231 fills
  for -130.43.
- Bottom line for the meta-question: collect/link contributes one small,
  concrete defect (4 inverted training rows, 1 in backtest scope) plus real
  coverage gaps. It does **not** explain the flat live PnL by itself; the bigger
  lever visible from this stage is that live trades a wider, thinner-tape
  population than the backtest admits.

## Method notes that matter for reading the numbers

- `radiant_win` is *defined* as `resolved_outcome_index == radiant_token_index`
  (src/lol/05_prepare_dataset.py:306,427). The label comes from the Polymarket
  resolution through the same orientation mapping used to build features, so a
  label-vs-tape check cannot detect an orientation bug by itself. All checks
  below therefore use independent ground truth: official series scores, in-game
  summoner rosters, GRID scoreboard `won` flags, and end-of-game net worth.
- Live side binding: GRID `series_scoreboard_v2` `games[map_number-1].teams[]`
  binds team_id → `infoText` BLUE/RED → `won`. `read_board_sides`
  (src/trader/grid_feed.py:65-93) resolves radiant = BLUE side's team and sets
  `outcome_0_is_radiant` (`yes_is_radiant`) by matching market outcome names to
  GRID team names via `orient_outcomes`. Table net worth uses each player's
  `entity.teamColor` → team_id (src/trader/grid_widgets.py:221-242), so the whole
  live feature chain is keyed on GRID's own team_id ↔ side binding.
- `LOL_ARCHIVE_ROOT=/archive/lol` is VPS-only. The collector Gamma event files
  the live league filter reads are not on this machine; league lookups below use
  `data/lol/processed/universe/markets.parquet` instead, which is a historical
  snapshot and lacks the newest events (see "Needs from VPS").

## Historical: Polymarket → lolesports

### Link integrity — clean

- 5,565 links in `data/lol/processed/lolesports_links/links.parquet`.
- Zero duplicate links (unique `condition_id`, unique `esports_game_id`).
- Orientation re-check: every link's `radiant_token_index` re-derives from the
  market's outcome names vs the linked game's team names — 0 mismatches.
- Loading anchors increase with `game_number` within every linked series — 0
  ordering violations, so no cross-map pinning drift inside a series.
- PM resolution vs linked series outcome: no case where processed-game winners
  contradict the PM match-winner resolution once fetch gaps are accounted for
  (see below).

### Numbering and coverage — gaps, not shifts

Official final score = `team_a_game_wins + team_b_game_wins` in
`data/lol/processed/lolesports/games.parquet`.

- 41 / 5,501 series have fewer processed game rows than the official score.
- 16 of those series contain linked maps; 17 resolved PM game markets point at
  games that were never fetched/processed. Examples (esports_game_id of sibling
  games in the affected series): `115548147900553473`, `115615924499588603`,
  `115615924499588651`, `115654899804988441`, `115654899804988459`,
  `115854003664427552`, `116130138006737521`, `116249441374234438`,
  `116566854547704008`, `116713163972559104`, `117009042046592078`.
- 0 extra or remake rows; no series' processed tally exceeds its official score.
- Missing games keep their official `game_number` (holes, not renumbering), so
  later maps in the same series are not shifted.

These are lolesports fetch gaps (window/details never downloaded), i.e. lost
coverage. A PM market resolved against a game we never processed simply can't
be a training row; it cannot corrupt a different map's row.

### Confirmed defect — swapped blue/red metadata (4 games)

`blue_esports_team_id` / `red_esports_team_id` in games.parquet come from the
window `gameMetadata` side blocks only (src/lol/03_link_lolesports.py:510-535);
there is no second source to cross-check. Leave-one-out roster scan over all
linked games (does the blue-side five appear under the *red* team id in every
other game they play?) found exactly 4 swapped records:

| esports_game_id | match | game | league (PM) | in backtest scope? |
|---|---|---|---|---|
| 115654899804988430 | Xi'an Team WE vs TOP ESPORTS | 1 | LPL Knights Rivals | no (LPL → no_live_feed) |
| 115615926685761092 | EDWARD GAMING vs Ultra Prime | 3 (match-winner decider market) | LPL | no (LPL) |
| 115615926685761105 | Ultra Prime vs Suzhou LNG Esports | 2 | LPL | no (LPL) |
| 116884625219920161 | CCG Esports vs Blue Otter | 1 | North American Challengers League | **yes** |

Each is a -5/5 vote: all five blue-side summoners are the opponent's roster
(e.g. `116884625219920161`: metadata calls the blue side "Blue Otter" but the
blue-side participants are `CCG Fiji, CCG Dardoch, CCG Leza, CCG Spawn,
CCG Only35`).

Effect: for these maps the features attributed to the radiant-named team are
the other team's stats, while `radiant_win` still comes from the PM resolution
— so the dataset rows are feature/label inverted. The NACL row also poisons the
backtest directly; the 3 LPL rows only pollute training (4/5,565 ≈ 0.07% of
maps — real but small).

### End-state and correlation outliers — adjudicated, no further mislinks

- Midpoint↔net-worth sign correlation over 1,686 maps: mean +0.524. Four maps
  had raw corr < -0.2: `116884625219920161` (-0.97, the confirmed swap),
  `116884625039565024` (-0.59, NACL Conviction vs Winthrop g1),
  `116895891142270590` (-0.63, Rift Legends — not whitelisted anyway),
  `116929405062330571` (-0.30, KeSPA Cup BO1). The last three have *correct*
  rosters per the swap scan; corr(mid, NW) is not a pure link check (the market
  prices more than gold), so they remain unexplained anomalies rather than
  proven mislinks — worth a manual look by whoever owns features.
- End-game NW/deaths vs `radiant_win`: 11 disagreements; all adjudicated
  consistent with official series scores (base-race finishes where the winner
  was behind on gold — labels correct, stats just look odd).
- 2 end-mid-vs-label "contradictions": one was a tape gap (book stale after
  second 606 of a ~1800s game, last mid 0.645 — `thin_telonex` class), one a
  legitimate comeback the market priced at 0.44 near the end (mCon vs Dynasty
  g2 — PM resolution agrees with the official 2-0).

## Live: Polymarket → GRID

Audited all 305 `data/trader/grid-*` LoL archives (`audit_live.py`).

- **0 genuine issues** across: market↔series binding, map_number ↔ slug
  `gameN`, GRID blue/red names ↔ `match.json` teams, `yes_is_radiant` ↔ outcome
  ordering, GRID `won` flag ↔ terminal market mid, match-winner markets pinned
  to the expected decider map.
- `match-winner` slugs (no `gameN`) account for the only structural flag —
  expected, they bind to the decider map correctly.
- Name variants are benign: `Cloud9` vs `Cloud9 Kia`, `Meavedron` vs
  `UP2U Meavedron`, `INTZ` vs `INTZ e-Sports` — resolved by `orient_outcomes`
  aliases/fuzzy matching; never a wrong side.
- **Independent swap check:** corr(`radiant_fair`, `market_p_radiant`) per map —
  median 0.99, p5 0.95, min -0.00 across 293 maps with ≥20 priced signals. A
  GRID BLUE/RED swap would make fair anti-track the market; nothing does.
- Residual risk, noted not found: live side labels inherit the same
  single-source weakness as history — nothing cross-checks GRID's `infoText`
  BLUE/RED against an independent side source. The fair/mid check would catch a
  swap only via its PnL footprint.

### Live data quality (population-relevant)

- Priced coverage in the 0–540s model window: 295/305 maps produced signal rows;
  233 ≥90% priced, 14 at 50–90%, 44 <50%, **4 at 0%** (`grid-2965526-m1/m2`,
  `grid-2968611-m1/m2` — signals existed, market quotes never did).
- 2 maps never saw `market_radiant_prior` (`grid-2971821-m1`,
  `grid-2973273-m1`) — 0 fills; the prior gate blocks trades naturally.
- 48 thin-coverage maps (<50% priced) still produced **231 fills, -130.43** —
  the live path has no equivalent of the backtest's `thin_telonex_trades`
  admission gate.

## Population: live vs backtest

Session PnL = `session_end.equity` (net_cash + inventory, per-session, sums
correctly). Boundary = whitelist deploy commit `9675656` (2026-09-08 14:55 UTC).

- 305 LoL archives: 298 `execution_mode: "live"`, 7 without the field
  (oldest schema, Aug 31–Sep 2, all zero-fill — harmless either way).
- **Pre-filter (166 maps):** 61 non-whitelisted — NLC 9, LJL 8, LFL 8, unknown
  7, Rift Legends 7, Circuito Desafiante 6, LIT 6, Prime League 3, HLL 3,
  LRS 2, TCL 2 — 216 fills, **-224.55**. Whitelisted 105 maps: 420 fills,
  -30.00.
- **Post-filter (139 maps):** 117 whitelisted — 813 fills, **-177.08**. 22 maps
  show league=None *in this local analysis only*: all are EMEA Masters (a
  whitelisted league) whose event_ids (1055589, 1055595, 1055597, 1055598,
  1055600, 1061298, 1061299, 1061302, 1061304) are absent from the stale
  `markets.parquet` snapshot. `LolLeagueFilter.read_event_league` fails closed
  on missing/unreadable/mismatched event files (src/trader/
  lol_league_filter.py:25-48, 79-100), so on the VPS these were almost
  certainly admitted correctly via the collector archive. **Not** a fail-open
  bug — but it needs one VPS-side confirmation (below).
- League × session PnL, all 305 maps: LFL -120.65 (8 maps!), Road Of Legends
  -74.28, Hitpoint Masters -35.47, LPLOL -35.47, LEC -34.46, NACL -25.54,
  Rift Legends -59.41, HLL -41.58 — note the tail is dominated by
  pre-filter non-whitelisted leagues *plus* Road Of Legends (-74.28, in-scope).
- Backtest/training admission (datasets/audit.parquet, 5,565 linked maps):
  accepted 3,825 (68.7%), thin_telonex_trades 1,142, missing_prior 399,
  missing_books 141, zero_labeled_rows 39, livestats_invariant_violation 9,
  no_spawn_frame 4, aborted_feed 4, window_details_mismatch 1,
  zero_usable_rows 1. Live has **no analog** of thin-tape/book-density
  admission — it trades whatever has a quote at decision time.

## Checked and OK

- Link uniqueness, orientation re-derivation, anchor ordering — 0 issues.
- Map numbering preserved across fetch gaps; no phantom/extra games.
- PM match-winner resolutions agree with official series scores everywhere
  checked (apparent contradictions were missing-decider fetch gaps).
- Live: side binding, map pinning, winner consistency, `yes_is_radiant`,
  match-winner decider pinning — 0 issues in 305 maps.
- Whitelist deployment boundary works: zero non-whitelisted fills after
  2026-09-08 (the 22 league=None maps are whitelisted EMEA Masters + a local
  snapshot artifact).
- `missing_prior` live: only 2 maps, 0 fills.

## Confirmed defects

1. 4 swapped lolesports `gameMetadata` side records → inverted feature/label
   rows (table above). 1 in backtest scope (`116884625219920161`).
2. 41 series with missing lolesports games → 17 resolved PM markets
   unlinkable; pure coverage loss.
3. Live trades thin tape the backtest rejects: 48 maps <50% priced in-window
   carried 231 fills / -130.43.

## Open questions for the owner

1. Repair or exclude the 4 swapped games? The metadata itself is wrong
   upstream; safest fix is a denylist of those esports_game_ids in
   03_link_lolesports (or swap the team ids at ingest) and rebuild.
2. Refetch the 41 gapped series' windows/details — recovers up to 17 resolved
   PM markets as training rows and removes the "missing decider" ambiguity from
   future checks.
3. The 3 unexplained mid-vs-NW anti-correlated maps (NACL + KeSPA, in scope) —
   worth one manual replay each.
4. Should live get a tape-density/prior admission gate mirroring
   `thin_telonex_trades`/`missing_prior` so the live population matches the
   backtest's?
5. Live single-source side labels (GRID `infoText`): add a cross-check against
   a second source (e.g. lolesports schedule side info) or accept the risk?

## Needs from VPS

- `/archive/lol/metadata/events/{event_id}.json` for the 9 EMEA Masters
  event_ids listed above — confirm `parse_event_league` resolved "EMEA Masters"
  (whitelisted) rather than None at admission time. Expected outcome:
  confirms correct admission; the None here is the missing local snapshot.
- If the collector archive shows league=None for them instead, then
  `read_event_league` correctly failed closed and the sessions must have been
  bound by an older code path — that would be a real admission bug worth a
  git-bisect on the trader deploy history.

## Scripts and outputs

All under `.analysis/lol-live-gap-2026-09-23/work/link-devin/`:

- `audit_links.py` — link dedup/orientation/anchor checks.
- `audit_numbering.py` — game-number continuity vs official scores.
- `audit_gaps.py` — fetch-gap quantification (41 series, 17 markets).
- `audit_anchor_vs_pm.py`, `audit_endstate.py`, `audit_corr.py`,
  `audit_series.py`, `audit_series2.py` — end-state/correlation/series checks.
- `audit_roster_swap.py`, `audit_roster_swap2.py` — leave-one-out roster swap
  scan (found the 4 swaps); `drill_sides.py`, `drill_scores.py`,
  `drill_endstate.py` — case drills.
- `audit_live.py` — full live binding/orientation/winner audit (305 maps,
  0 issues). `audit_live_corr.py` — per-map corr(radiant_fair,
  market_p_radiant) swap detector.
- `audit_population.py`, `audit_population2.py`, `audit_population3.py` —
  live-vs-backtest population, league whitelist split, tape coverage;
  `/tmp/pop3_rows.json` — per-map facts used above.
