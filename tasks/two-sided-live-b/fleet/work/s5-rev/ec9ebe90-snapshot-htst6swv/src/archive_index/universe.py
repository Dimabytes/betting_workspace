"""Universe lookup: condition id -> the Polymarket market identity it binds.

The index uses the universe only to establish identity (event id, token pair,
team names, map number); it never derives feed timing from it.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from shared.constants.lol import LOL_UNIVERSE_PATH
from shared.constants.paths import DOTA_UNIVERSE_PATH
from shared.utils.parsing import json_list, opt_int, opt_str


@dataclass(frozen=True)
class UniverseMarket:
    """The universe row one condition id resolves to.

    `token_ids` keeps the market's outcome order so index 0 is the YES side.
    """

    condition_id: str
    event_id: str
    team_a: str | None
    team_b: str | None
    token_ids: tuple[str, ...]
    outcome_names: tuple[str, ...] | None
    game_number: int | None
    contract_kind: str
    market_slug: str | None
    event_slug: str | None


def _json_str_list(raw: object) -> tuple[str, ...]:
    """Parse a universe JSON-array cell into a tuple of strings."""
    return tuple(str(item) for item in json_list(raw))


def load_dota_universe(path: Path = DOTA_UNIVERSE_PATH) -> dict[str, UniverseMarket]:
    """Index the Dota universe by lowercase condition id."""
    frame = pd.read_parquet(path)
    markets: dict[str, UniverseMarket] = {}
    for row in frame.itertuples(index=False):
        condition_id = str(row.conditionId).lower()
        markets[condition_id] = UniverseMarket(
            condition_id=condition_id,
            event_id=str(row.event_id),
            team_a=opt_str(row.team_a),
            team_b=opt_str(row.team_b),
            token_ids=(str(row.token_id_0), str(row.token_id_1)),
            outcome_names=None,
            game_number=opt_int(row.game_number),
            contract_kind=str(row.contract_kind),
            market_slug=opt_str(row.market_slug),
            event_slug=None,
        )
    return markets


def load_lol_universe(path: Path = LOL_UNIVERSE_PATH) -> dict[str, UniverseMarket]:
    """Index the LoL universe by lowercase condition id; `included` rows win."""
    frame = pd.read_parquet(path)
    markets: dict[str, UniverseMarket] = {}
    ordered = frame.sort_values("included", ascending=False)
    for row in ordered.itertuples(index=False):
        condition_id = str(row.condition_id).lower()
        markets.setdefault(
            condition_id,
            UniverseMarket(
                condition_id=condition_id,
                event_id=str(row.event_id),
                team_a=opt_str(row.team_a),
                team_b=opt_str(row.team_b),
                token_ids=_json_str_list(row.clob_token_ids_json),
                outcome_names=_json_str_list(row.outcomes_json) or None,
                game_number=opt_int(row.game_number),
                contract_kind=str(row.contract_kind),
                market_slug=None,
                event_slug=opt_str(row.event_slug),
            ),
        )
    return markets


def load_universe(game: str) -> dict[str, UniverseMarket]:
    """Load the universe index for one game; an absent parquet is an operator error."""
    if game == "lol":
        return load_lol_universe()
    if game == "dota":
        return load_dota_universe()
    raise ValueError(f"no universe loader for game {game!r}")
