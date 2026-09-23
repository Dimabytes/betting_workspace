# Brief: feat-devin — feature parity audit from code (training vs backtest vs live)

Your name: `feat-devin`. Report: `$R/reports/feat-devin.md`. Work dir: `$R/work/feat-devin/`.

Goal: for each of the 12 model features, prove that live computes the same quantity
(definition, units, orientation, missing handling) as training and backtest, or show
where it does not.

1. Table: feature → training code (`file:line`, livestats window/details field) → backtest
   code → live code (GRID field) → difference. Features: `second, radiant_nw_adv,
   radiant_nw, dire_nw, radiant_xp_adv, deaths_radiant, deaths_dire, top1_nw_adv,
   radiant_top1_nw_ratio, dire_top1_nw_ratio, market_radiant_prior, market_p_radiant`.
2. Focus:
   - Net worth: training rebuilds totalGold − consumed items (`src/lol/networth.py`,
     Data Dragon tables per patch). Live uses GRID `NetWorth`: team row or sum of players?
     Does it include unspent gold? Same scale?
   - XP: `xp_source=level` → `LOL_LEVEL_XP` staircase. Live: level from GRID
     (`increaseLevel`?) or raw `ExperiencePoints`? Same staircase?
   - Deaths, top-1 net worth, the ratio denominators.
   - `second` (clock source in each path).
   - "Radiant" in LoL: blue side? YES token? How live maps GRID BLUE/RED (`infoText`, `Side`)
     to radiant/dire and to the YES/NO token, per map of a series (sides swap between maps).
   - `market_radiant_prior`, `market_p_radiant`: which token, which time, which book.
3. Live update semantics: GRID `series_table` (declared 8 s delay) vs `series_scoreboard_v2`
   (~7 s). Does live mix a newer clock with an older table state in one feature vector?
   Training takes all features from one livestats frame.
4. Missing or partial data: what live does when a table field is missing, a team has
   ≠ 5 players, or the table stops updating. Defaults, NaN, zeros. Training drops
   invariant-violating frames and whole maps; live cannot drop — does it trade on bad frames?
5. Note which transforms are shared with Dota (`src/shared/utils/gbm.py`,
   `src/trader/game_profile.py`) and which are LoL-only.
