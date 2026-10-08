"""Shape of the gzipped Stratz match cache written by s05b_fetch_stratz_matches.

Derived from a 1200-file sample of data/raw/stratz_matches/. Two cache profiles
exist: `stratz_rich_v2` and `stratz_rich_v3`; v3 adds `firstBloodTime` and
`towerDeaths` on the match and `stats` on each player. Those three are
NotRequired — everything else is present in both.

These are static types only: `cast()` at the read boundary, no runtime
validation. Run `python src/shared/types/stratz.py` to check the cache still
matches (see check_cache below).
"""

from typing import Any, Literal, NotRequired, TypedDict

Lane = Literal["SAFE_LANE", "OFF_LANE", "MID_LANE", "ROAMING", "JUNGLE"]
Position = Literal["POSITION_1", "POSITION_2", "POSITION_3", "POSITION_4", "POSITION_5"]
Role = Literal["CORE", "LIGHT_SUPPORT", "HARD_SUPPORT"]
Award = Literal["NONE", "MVP", "TOP_CORE", "TOP_SUPPORT"]
LeaverStatus = Literal["NONE", "DISCONNECTED"]


class StratzTeam(TypedDict):
    id: int
    name: str


class StratzPlayerStats(TypedDict):
    """v3 only. Level-up seconds, per-minute series, and sparse event lists."""

    level: list[int]
    networthPerMinute: list[int]
    itemPurchases: list["StratzItemPurchase"]
    deathEvents: list["StratzPlayerDeathEvent"]


class StratzItemPurchase(TypedDict):
    itemId: int
    time: int


class StratzPlayerDeathEvent(TypedDict):
    time: int
    timeDead: int


class StratzPlayerUpdateGoldEvent(TypedDict):
    """One absolute networth update from player playback."""

    time: int
    gold: int
    networth: int
    networthDifference: int
    unreliableGold: int


class StratzExperienceEvent(TypedDict):
    """One experience gain event from player playback."""

    time: int
    amount: int
    positionX: int
    positionY: int


class StratzPlayerPlayback(TypedDict):
    """Per-player playback fields used for exact-second validation features."""

    playerUpdateGoldEvents: list[StratzPlayerUpdateGoldEvent]
    experienceEvents: list[StratzExperienceEvent]


class StratzPlayer(TypedDict):
    matchId: int
    steamAccountId: int
    heroId: int
    playerSlot: int
    isRadiant: bool
    isVictory: bool
    level: int
    kills: int
    deaths: int
    assists: int
    numLastHits: int
    numDenies: int
    gold: int
    goldSpent: int
    goldPerMinute: int
    experiencePerMinute: int
    networth: int
    heroDamage: int
    heroHealing: int
    towerDamage: int
    leaverStatus: LeaverStatus
    roleBasic: Role
    # null in ~4% of players (abandons, unparsed drafts)
    lane: Lane | None
    position: Position | None
    role: Role | None
    award: Award | None
    imp: int | None
    item0Id: int | None
    item1Id: int | None
    item2Id: int | None
    item3Id: int | None
    item4Id: int | None
    item5Id: int | None
    backpack0Id: int | None
    backpack1Id: int | None
    backpack2Id: int | None
    neutral0Id: int | None
    # ponytail: ~23 replay event lists, non-null in ~15% of matches. Left as Any
    # until something actually reads it — see PLAYER_PLAYBACK_KEYS below.
    playbackData: dict[str, Any] | None
    stats: StratzPlayerStats


class StratzRuneEvent(TypedDict):
    """Match-level rune event. Note `action`/`rune` are ints here but strings in
    the per-player playbackData."""

    action: int
    rune: int
    location: int
    indexId: int
    positionX: int
    positionY: int
    time: int


class StratzTowerDeathEvent(TypedDict):
    """Match-level: surviving tower bitmask per side after the death."""

    radiant: int
    dire: int
    time: int


class StratzWardEvent(TypedDict):
    action: str
    wardType: str
    fromPlayer: int
    playerDestroyed: int | None
    indexId: int
    positionX: int
    positionY: int
    time: int


class StratzMatchPlayback(TypedDict):
    runeEvents: list[StratzRuneEvent]
    towerDeathEvents: list[StratzTowerDeathEvent]
    wardEvents: list[StratzWardEvent]
    # always [] across the whole sample — shape unknown, type when one shows up
    courierEvents: list[Any]
    roshanEvents: list[Any]
    buildingEvents: list[Any]


class StratzTowerDeath(TypedDict):
    """v3 match-level towerDeaths — which tower, not a bitmask."""

    npcId: int
    isRadiant: bool
    time: int


class StratzMatchBase(TypedDict):
    """Every match field except the lead arrays, which the two shapes below narrow."""

    id: int
    leagueId: int
    seriesId: int
    didRadiantWin: bool
    durationSeconds: int
    startDateTime: int
    endDateTime: int
    gameMode: str  # CAPTAINS_MODE | ALL_PICK | ... — open set, not a Literal
    lobbyType: str  # PRACTICE for everything we collect
    radiantTeamId: int
    direTeamId: int
    radiantTeam: StratzTeam
    direTeam: StratzTeam
    towerStatusRadiant: int
    towerStatusDire: int
    barracksStatusRadiant: int
    barracksStatusDire: int
    playbackData: StratzMatchPlayback | None
    players: list[StratzPlayer]  # always exactly 10
    firstBloodTime: NotRequired[int]
    towerDeaths: NotRequired[list[StratzTowerDeath]]


class StratzMatch(StratzMatchBase):
    """The raw cache shape. STRATZ leaves both lead arrays null in ~2% of matches."""

    radiantNetworthLeads: list[int] | None
    radiantExperienceLeads: list[int] | None


class UsableStratzMatch(StratzMatchBase):
    """A match that passed the collect gate in stratz_match_unusable_reason."""

    radiantNetworthLeads: list[int]
    radiantExperienceLeads: list[int]


class StratzMatchData(TypedDict):
    match: StratzMatch


class StratzMatchCache(TypedDict):
    """Top level of match_<id>.json.gz."""

    match_id: int
    cache_profile: Literal["stratz_rich_v2", "stratz_rich_v3"]
    source: str
    fetched_at: str
    response_bytes: int
    graphql_errors: list[Any]
    data: StratzMatchData


PLAYER_PLAYBACK_KEYS = (
    "abilityActiveLists",
    "abilityLearnEvents",
    "abilityUsedEvents",
    "assistEvents",
    "buyBackEvents",
    "csEvents",
    "deathEvents",
    "experienceEvents",
    "goldEvents",
    "healEvents",
    "heroDamageEvents",
    "inventoryEvents",
    "itemUsedEvents",
    "killEvents",
    "playerUpdateAttributeEvents",
    "playerUpdateBattleEvents",
    "playerUpdateGoldEvents",
    "playerUpdateHealthEvents",
    "playerUpdateLevelEvents",
    "playerUpdatePositionEvents",
    "purchaseEvents",
    "runeEvents",
    "towerDamageEvents",
)


def check_cache(payload: dict[str, Any]) -> None:
    """Raise if a cached payload has drifted from the types above."""

    def check(name: str, td: Any, obj: dict[str, Any]) -> None:
        missing = set(td.__required_keys__) - obj.keys()
        extra = obj.keys() - td.__required_keys__ - set(td.__optional_keys__)
        if missing or extra:
            raise AssertionError(f"{name}: missing={sorted(missing)} extra={sorted(extra)}")

    check("StratzMatchCache", StratzMatchCache, payload)
    match = payload["data"]["match"]
    check("StratzMatch", StratzMatch, match)
    for player in match["players"]:
        check("StratzPlayer", StratzPlayer, player)
        if "stats" in player:
            check("StratzPlayerStats", StratzPlayerStats, player["stats"])
    if match["playbackData"] is not None:
        check("StratzMatchPlayback", StratzMatchPlayback, match["playbackData"])


if __name__ == "__main__":
    import gzip
    import json
    import random
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from shared.constants.paths import RAW_STRATZ_MATCHES_DIR

    paths = sorted(RAW_STRATZ_MATCHES_DIR.glob("*.json.gz"))
    sample = random.sample(paths, min(300, len(paths)))
    for path in sample:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            check_cache(json.load(f))
    print(f"ok: {len(sample)} of {len(paths)} cached matches match the types")
