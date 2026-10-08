"""Normalize and match team names against a caller-supplied alias table."""

import re
from collections.abc import Mapping
from difflib import SequenceMatcher

PAIR_SCORE_MIN = 0.82
SIDE_SCORE_MIN = 0.72

STOPWORDS: frozenset[str] = frozenset(
    {
        "team",
        "esports",
        "esport",
        "gaming",
        "club",
        "the",
        "dota",
        "e",
        "sports",
        "gg",
    }
)

# Alias groups, one row per name that needs them. Matching is symmetric: both
# `score_team_name` arguments expand through this table, so a group needs only
# the rows that add reach. Never list a key inside its own value: the probe
# builder already prepends the normalized key.
TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "veroja": ("indo rejects",),
    "yakutou brothers": ("yakult brothers",),
    "yakult brothers": ("yakutou brothers",),
    # YB.Tearlaments (OpenDota 9579337) is not Yakult Brothers (9351740): they play
    # each other. Do not hub both names onto "yb tearlaments" or orientation ties.
    "yakult brothers tearlaments": ("yb tearlaments",),
    "tearlaments": ("yb tearlaments",),
    "yache123": ("яче123",),
    "tpb": ("trailer park boys",),
    "betboom": ("boomboys",),
    "1win": ("enjoy", "1w"),
    "parivision": ("team vision", "pvision"),
    "navi": ("natus vincere",),
    "natus vincere": ("navi",),
    "kalmychata": ("rune eaters",),
    "btc": ("red hot chili pibble",),
    "bug": ("thebug",),
    "poor rangers": ("power rangers", "powerrangers"),
    "power rangers": ("powerrangers",),
    "soloteam": ("solo team",),
    "playtime": ("ptime",),
    "l1ga": ("l1 team", "huligani"),
    "aim possible": ("two move",),
    "enjoy boys": ("hive",),
    "grind back": ("grind",),
    "inner circle": ("inner circle x insanity",),
    # Renames: OpenDota joins the current teams.name, so it only ever shows the
    # newest spelling and the old Polymarket title never scores against it.
    "tundra": ("iron wing",),
    "4ikibamboni": ("re arise",),
    "travoman": ("tpabomah",),
    "9z": ("shinden",),
    "lgd": ("lgd pinghu", "lgd ping"),
    "lgd pinghu": ("lgd",),
    "g time": ("ec1ipse",),
    "ec1ipse": ("g time",),
}


def normalize_team_name(value: object) -> str:
    """Normalize one team name to its meaningful lowercase latin and cyrillic tokens."""
    lowered = str(value or "").lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", lowered)
    tokens = [token for token in text.split() if token not in STOPWORDS]
    return " ".join(tokens or text.split())


def build_name_probes(value: str, aliases: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Return the normalized team name and its known aliases once each."""
    normalized = normalize_team_name(value)
    probes = [normalized]
    probes.extend(normalize_team_name(alias) for alias in aliases.get(normalized, ()))
    return tuple(dict.fromkeys(probe for probe in probes if probe))


def sort_name_tokens(value: str) -> str:
    """Return one normalized team name with its tokens in a stable order."""
    return " ".join(sorted(value.split()))


def _probe_pair_score(expected_probe: str, observed_probe: str) -> float:
    """Score one probe pair as written and with tokens sorted, keeping the better one.

    Sorting is an extra probe, never a replacement: sorting alone loses 12
    events the as-written comparison used to match.
    """
    as_written = SequenceMatcher(None, expected_probe, observed_probe).ratio()
    reordered = SequenceMatcher(
        None, sort_name_tokens(expected_probe), sort_name_tokens(observed_probe)
    ).ratio()
    return max(as_written, reordered)


def score_team_name(
    expected: str,
    observed_names: set[str] | frozenset[str],
    aliases: Mapping[str, tuple[str, ...]],
) -> float:
    """Return the best score over aliased spellings of expected and observed names."""
    expected_probes = build_name_probes(expected, aliases)
    observed_probes = [
        probe for observed in observed_names for probe in build_name_probes(observed, aliases)
    ]
    return max(
        (
            _probe_pair_score(expected_probe, observed_probe)
            for expected_probe in expected_probes
            for observed_probe in observed_probes
        ),
        default=0.0,
    )


def pick_pair_orientation(
    forward_a: float, forward_b: float, reverse_a: float, reverse_b: float
) -> bool | None:
    """Pick forward vs reverse name orientation, or None when the pair is rejected.

    An exact forward/reverse total tie is rejected. The higher orientation must
    pass both the pair-average and the per-side minimum thresholds. True means
    forward won.
    """
    forward_total = forward_a + forward_b
    reverse_total = reverse_a + reverse_b
    if forward_total == reverse_total:
        return None
    if forward_total > reverse_total:
        side_a, side_b = forward_a, forward_b
    else:
        side_a, side_b = reverse_a, reverse_b
    if (side_a + side_b) / 2 < PAIR_SCORE_MIN or min(side_a, side_b) < SIDE_SCORE_MIN:
        return None
    return forward_total > reverse_total


def orient_outcomes(
    outcome_0: str,
    outcome_1: str,
    side_a: str,
    side_b: str,
    aliases: Mapping[str, tuple[str, ...]],
) -> bool | None:
    """Orient two market outcomes against two side names.

    True means outcome 0 is `side_a` and outcome 1 is `side_b`. False means the
    reverse. None means the name pair failed the orientation thresholds.
    """
    a = frozenset({normalize_team_name(side_a)})
    b = frozenset({normalize_team_name(side_b)})
    return pick_pair_orientation(
        score_team_name(outcome_0, a, aliases),
        score_team_name(outcome_1, b, aliases),
        score_team_name(outcome_0, b, aliases),
        score_team_name(outcome_1, a, aliases),
    )
