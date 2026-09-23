# Brief: link-devin — collection and linking audit (history and live)

Your name: `link-devin`. Report: `$R/reports/link-devin.md`. Work dir: `$R/work/link-devin/`.

Goal: find mislinked maps, wrong orientation, wrong labels, or wrong map numbers between
Polymarket and lolesports (history), and between Polymarket and GRID (live).

1. Read `src/lol/01_build_universe.py`, `03_link_lolesports.py`, `lolesports_match.py`,
   `live_schedule.py`, `02_fetch_telonex_books.py`, `04_fetch_lolesports.py`. Document the
   chain: market → event → series → map number → `esports_game_id`; team names ↔ outcomes;
   blue/red ↔ radiant/dire; YES token ↔ radiant; book ↔ token id; winner label.
2. Data audit over all training + validation maps:
   a. The radiant-token mid near map end must go to ~1 if `radiant_win=1`, ~0 otherwise
      (use `market_seconds.parquet` or books near map end). Count contradictions.
   b. Team names in livestats metadata vs Polymarket outcome names for the radiant side.
   c. Map number vs livestats game number in the series.
   d. The same `esports_game_id` linked to two markets; one market linked to two games.
   e. Series with a remake, forfeit, or a missing game (map numbering shifts).
   Give counts and concrete examples.
3. Live: for every LoL live map in `$E/data/trader/grid-*` (`game=lol`), check `yes_is_radiant`,
   `teams.radiant/dire` against GRID BLUE/RED `infoText` and team ids in `grid_state.jsonl`
   for that game index; check the pinned GRID game equals the Polymarket `gameN` market;
   check the final market price agrees with the GRID winner. List any live map with a wrong
   orientation or a wrong game. Read the live linking code (`src/trader/discovery.py`,
   `match_meta.py`, `session_binding.py`, `grid_feed.py`).
4. Population: which live-traded LoL maps would fail the training/backtest admission rules
   (whitelist, missing prior, thin tape, missing books)? Does live trade maps unlike the
   training population?
