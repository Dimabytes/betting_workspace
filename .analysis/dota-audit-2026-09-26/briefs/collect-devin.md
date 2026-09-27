# Brief: collect-devin — collect fetchers, parsers, game-state data, raw-data provenance

Report: `$R/reports/collect-devin.md`. Work dir: `$R/work/collect-devin/`.

## Scope (read line by line)

- `src/collect/s04_fetch_grid_starts.py`, `s05_fetch_opendota_matches.py`, `s05a_fetch_prices_history.py`,
  `s05b_fetch_stratz_matches.py`, `s06_publish_catalog.py` (keep/drop rules), `s07_link_readiness.py`,
  `src/collect/common/*`.
- `src/prepare_dataset/stratz_seconds.py` (minute states and exact-second states).
- `src/shared/utils/{stratz.py, opendota.py, grid_series_state.py, match_time.py, dota_levels.py,
  level_xp.py, top_players.py, price_history.py, match_catalog.py, parsing.py}`,
  `src/shared/types/{stratz.py, opendota.py, dataset.py}`, `src/shared/constants/dota.py`.
- Raw data: `data/raw/{stratz_matches, opendota_matches, opendota_candidate_index, pro_matches,
  polymarket_dota, opendota_constants}` and `data/new_processed/{grid_game_starts, pregame_quotes,
  stratz_match_index}`.
- link-grok owns linking and side orientation (s01–s03). You own the game-state values and times.

## Questions

1. **Clock.** How do collect and `stratz_seconds.py` define second 0 (horn) and spawn? Is it the same
   `second` that live uses (Steam game time, GRID clock, Oddin clock)? Does STRATZ time include pauses? Does
   the exact-second reconstruction handle pauses the way live does?
2. **Look-ahead inside the reconstruction.** How does `build_exact_second_states` compute NW, XP, deaths,
   top1 NW at each second: events, interpolation, or per-minute snapshots? Does the state at second S use any
   event or snapshot with time > S (for example, the next minute's NW)? That would be look-ahead in every
   validation/backtest feature. Prove it on 2–3 maps: exact-second states at minute marks vs minute states,
   and a search for any input with timestamp > S.
3. **Feature definitions vs live.** STRATZ networth vs Steam `GetRealtimeStats` `net_worth` vs GRID vs Oddin
   (gold + items? buyback? neutral items? courier?). XP: STRATZ experience events vs live XP (levels mapped
   through `LEVEL_XP`? quantized?). Deaths: kills of the other team? suicides/denies? Top1 NW: player set,
   ties, disconnected players. Write a table: feature × source × definition × expected skew.
4. **Orientation inside game data.** `isRadiant`, team ids, `radiant_win` from STRATZ/OpenDota; remakes,
   abandoned games, reconnect gaps.
5. **Silent drops.** "Drop corrupt STRATZ samples" (commit `b08b81d1`) and later rules: what counts as corrupt
   today, how many maps each rule drops, and whether the dropped subset is biased (league, month, game length).
6. **Pregame prior (`s05a`).** Which bar/window becomes `radiant_prior`, relative to horn and spawn? Token
   orientation? Bar fidelity? Missing-value handling? Can the prior include in-game prices (look-ahead into
   the first minutes)? Give the exact rule; feed-devin compares it to the live prior.
7. **GRID starts (`s04`).** What is taken from GRID; can a cached `series_state/*.json` be stale (fetched
   before the series ended) and still be used?
8. **Provenance and leftovers.** When was each raw dataset downloaded (mtime, git log of the fetch code)? Is
   any raw cache frozen from an old API version or old query fields (STRATZ GraphQL fields changed? OpenDota
   cache from the TLS-intercept period)? Sample maps across months (2025-10 → 2026-09) and look for schema
   drift, empty `playbackData`, zero NW, truncated matches, duplicated players.
9. **Catalog keep/drop (`s06`).** Which rules drop maps, how many per rule, is the dropped set biased, and are
   the drop reasons visible anywhere?

## Allowed

Small scripts on local data. Web search for STRATZ / OpenDota / Steam Web API semantics. No network calls to
STRATZ/OpenDota APIs from scripts (read caches only).
