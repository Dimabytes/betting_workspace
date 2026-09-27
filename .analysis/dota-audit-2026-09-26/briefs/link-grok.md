# Brief: link-grok — market↔game linking and side orientation, end to end

Report: `$R/reports/link-grok.md`. Work dir: `$R/work/link-grok/`.

## Scope (read line by line)

- Collect: `src/collect/{s01_build_universe.py, s02_link_opendota.py, s03_merge_archive_links.py,
  s06_publish_catalog.py, s07_link_readiness.py}`, `src/collect/common/{opendota_candidates.py,
  catalog_types.py, window_ids.py}`, `src/archive_index/*`.
- Shared: `src/shared/utils/{team_names.py, polymarket.py, match_catalog.py, series_format.py}`.
- Live: `src/trader/{discovery.py, match_meta.py, session_binding.py, bindings.py, collector_sidecars.py,
  archived_markets.py, oddin_catalog.py, oddin_discovery.py, source_picker.py, feed_selection.py}`.
- Backtest: `src/backtest/context.py` (`radiant_token_index`), settlement mapping in `marks.py`.

## Questions

1. **Radiant ↔ token.** How is the market's YES token mapped to Radiant for training (`market_p_radiant`,
   prior, label), for the backtest (`radiant_token_index`, settlement), and for live (which token is
   "radiant" for the model inputs, which token gets the BUY)? Find every path where orientation depends on
   team-name matching that can fail silently (aliases, "Team X" vs "X", Cyrillic, tags, stand-ins). Where is
   Radiant/Dire known per source (STRATZ/OpenDota `isRadiant`, Steam team ids, GRID side, Oddin), and how is it
   matched to the market's outcome names?
2. **Map number.** Market "Game N" ↔ the N-th map of the series: remakes, forfeits, unplayed map 3, a replayed
   map, rematches of the same teams on the same day, relisted markets (same slug, new condition id).
3. **Data check (required).** For all catalog maps: the book tells the truth after the game. Count maps where
   the final radiant mid contradicts `radiant_win` (strong sign of swapped sides or a wrong map link). Do the
   same for live archives: `match.json` winner vs the final book, and vs the collector/Telonex book. Also check
   the prior: a prior far from the first in-game mid on the other side of 0.5 is a hint of flipped orientation.
4. **Live binding.** Can a session bind to the wrong map (the map 2 market while map 1 is still running) or the
   wrong game after a rebind? How does the new Disir catalog (today, commits `dec98290`..`bbb28897`) match Oddin
   ids to markets?
5. **Series vs map.** Can a series market be mistaken for a map market anywhere?

Deliver the counts from question 3 with the script and the list of suspicious maps.
