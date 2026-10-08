"""Series formats and the last-map match-winner rule."""

from typing import Literal

# Steam / OpenDota `series_type` → best-of. BO2 (3→2) is a real format but not a
# two-sided last-map market: winning map 2 of a BO2 is not always winning the series.
SERIES_TYPE_TO_BEST_OF: dict[int, Literal[1, 2, 3, 5]] = {0: 1, 3: 2, 1: 3, 2: 5}
DECIDER_BEST_OF: frozenset[int] = frozenset({1, 3, 5})
GRID_FORMAT_TO_BEST_OF: dict[str, Literal[1, 3, 5]] = {
    "best-of-1": 1,
    "best-of-3": 3,
    "best-of-5": 5,
}


def series_winner_covers_map(best_of: int, map_number: int, *, map_winner_exists: bool) -> bool:
    """True when Match Winner is the map market for this map (no Game N Winner)."""
    return best_of in DECIDER_BEST_OF and map_number == best_of and not map_winner_exists


def best_of_from_grid_format(series_format: str) -> Literal[1, 3, 5] | None:
    """Parse GRID `series.format`; unknown or BO2 formats return None."""
    return GRID_FORMAT_TO_BEST_OF.get(series_format.strip().lower())
