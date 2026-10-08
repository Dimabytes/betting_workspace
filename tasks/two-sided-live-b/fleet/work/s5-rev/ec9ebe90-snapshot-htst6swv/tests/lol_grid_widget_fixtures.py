"""Compact GRID widget payloads from the 2026-08-29 CBLOL and LEC recordings."""

import json

from trader.grid_widget_types import (
    ScoreboardInfoText,
    ScoreboardPayload,
    SeriesTablePayload,
    TableRow,
)

FLUXO = "48070"
LOS = "47946"
NAVI = "55749"
GIANTX = "53168"
CBLOL_SERIES_ID = "2973268"
LEC_SERIES_ID = "2966907"

BLUE_INFO: ScoreboardInfoText = {"text": "BLUE", "color": "#16B4CC"}
RED_INFO: ScoreboardInfoText = {"text": "RED", "color": "#EE455C"}

# team, nick, champion uuid, net worth, kills, deaths, assists, level-ups
CBLOL_NET_WORTHS = (
    (FLUXO, "Momochi", "8bee9844-a12e-32cd-a20c-6d59dd9a07c4", 2588, 0, 2, 1, 4),
    (FLUXO, "Zothve", "7552f226-fa3b-3053-b692-0dcdd9c79cf4", 4824, 2, 2, 1, 9),
    (FLUXO, "cody", "31822a27-64be-3b58-9cfa-17fd705fc871", 4472, 0, 0, 0, 9),
    (FLUXO, "Peach", "5179b7c8-f751-3f66-b40d-6671d54aab37", 5522, 1, 2, 2, 8),
    (FLUXO, "BAO", "5748a92e-ed31-3fbf-854d-a37923f7c7ae", 5039, 1, 0, 0, 8),
    (LOS, "Duduhh", "7889d995-c5bd-3d3a-a142-cbec510a328e", 4615, 0, 1, 0, 8),
    (LOS, "Ackerman", "9104bd4f-7d7d-3895-8151-d3210b34e3d2", 2677, 1, 0, 4, 5),
    (LOS, "Feisty", "642972b0-ab79-3cac-812a-29cfbdb6526a", 4601, 2, 0, 2, 8),
    (LOS, "Zest", "717061ba-4e85-30e1-9340-aef80368dfa0", 4064, 0, 3, 4, 9),
    (LOS, "Curse", "bbd16ab8-92ed-3f82-8748-b23c20eca335", 5494, 3, 0, 3, 8),
)

# Same CBLOL map, first table frame: GRID has not sent increaseLevel yet.
CBLOL_NET_WORTHS_BEFORE_LEVEL_UP = (
    (FLUXO, "Momochi", "8bee9844-a12e-32cd-a20c-6d59dd9a07c4", 450, 0, 0, 0),
    (FLUXO, "Zothve", "7552f226-fa3b-3053-b692-0dcdd9c79cf4", 500, 0, 0, 0),
    (FLUXO, "cody", "31822a27-64be-3b58-9cfa-17fd705fc871", 500, 0, 0, 0),
    (FLUXO, "Peach", "5179b7c8-f751-3f66-b40d-6671d54aab37", 500, 0, 0, 0),
    (FLUXO, "BAO", "5748a92e-ed31-3fbf-854d-a37923f7c7ae", 500, 0, 0, 0),
    (LOS, "Duduhh", "7889d995-c5bd-3d3a-a142-cbec510a328e", 450, 0, 0, 0),
    (LOS, "Ackerman", "9104bd4f-7d7d-3895-8151-d3210b34e3d2", 450, 0, 0, 0),
    (LOS, "Feisty", "642972b0-ab79-3cac-812a-29cfbdb6526a", 450, 0, 0, 0),
    (LOS, "Zest", "717061ba-4e85-30e1-9340-aef80368dfa0", 500, 0, 0, 0),
    (LOS, "Curse", "bbd16ab8-92ed-3f82-8748-b23c20eca335", 500, 0, 0, 0),
)

LEC_NET_WORTHS = (
    (NAVI, "Poby", "a6ba29b1-2523-38e5-9a2c-69a35156f2df", 829, 0, 0, 0, 2),
    (NAVI, "Rhilech", "bbd16ab8-92ed-3f82-8748-b23c20eca335", 836, 0, 0, 0, 2),
    (NAVI, "SamD", "a0cf8bf6-39d8-30fb-9dab-c1e8af747901", 904, 0, 0, 0, 1),
    (NAVI, "Maynter", "a6c6341f-f06c-3428-a0e3-797a0d0e2638", 1013, 0, 0, 0, 2),
    (NAVI, "Parus", "431d18c8-1aee-3d01-8b0a-8e46172e7761", 708, 0, 0, 0, 1),
    (GIANTX, "Jackies", "8a23d389-2c48-35ec-9d16-a225fc7c3dfd", 776, 0, 0, 0, 2),
    (GIANTX, "TH Flakked", "e5eeaee9-eaa4-330f-85a1-e90230d9e0ad", 620, 0, 0, 0, 1),
    (GIANTX, "ISMA", "28c76f65-a320-3efc-8dbf-f5ccd8f30078", 910, 0, 0, 0, 2),
    (GIANTX, "Oscarinin", "682a5d5e-4e20-3f12-89c8-943f80995220", 862, 0, 0, 0, 1),
    (GIANTX, "Jun", "31360e91-4470-348a-9a2b-ce10e9a107b1", 602, 0, 0, 0, None),
)


def make_row(
    team: str,
    nick: str,
    champion_id: str,
    net: int,
    kills: int,
    deaths: int,
    assists: int,
    level_ups: int | None,
) -> TableRow:
    """Build one LoL `series_table` player row after the map's first level-up."""
    return {
        "entity": {
            "value": nick,
            "hexColor": "#5b6f7e",
            "teamColor": f"teams.{team}",
            "iconUrl": f"https://cdn.grid.gg/assets/lol/characters/{champion_id}.png",
        },
        "NetWorth": {"value": net, "teamColor": None},
        "Kills": {"value": kills, "teamColor": None},
        "Deaths": {"value": deaths, "teamColor": None},
        "KillAssistsGiven": {"value": assists, "teamColor": None},
        "increaseLevel": {"value": level_ups, "teamColor": None},
    }


def make_row_before_level_up(
    team: str,
    nick: str,
    champion_id: str,
    net: int,
    kills: int,
    deaths: int,
    assists: int,
) -> TableRow:
    """Build one LoL player row as GRID sends it before the first level-up."""
    row = make_row(team, nick, champion_id, net, kills, deaths, assists, 0)
    del row["increaseLevel"]
    return row


def make_table(
    series_id: str, game_number: int, rows: list[TableRow], total: TableRow
) -> SeriesTablePayload:
    """Build a compact `series_table` with only the Game/Player columns the parser reads."""
    return {
        "Id": series_id,
        "stateGroups": [
            {"name": "Series", "states": [{"sequenceNumber": 1, "entityGroups": []}]},
            {
                "name": "Game",
                "states": [
                    {
                        "sequenceNumber": game_number,
                        "entityGroups": [
                            {
                                "name": "Player",
                                "tables": [{"tableRows": rows, "totalRow": total}],
                            },
                            {"name": "Teams", "tables": []},
                        ],
                    }
                ],
            },
        ],
    }


CBLOL_ROWS = [make_row(*record) for record in CBLOL_NET_WORTHS]
CBLOL_SERIES_TABLE = make_table(
    CBLOL_SERIES_ID,
    1,
    CBLOL_ROWS,
    make_row(FLUXO, "", "", 43896, 10, 10, 17, 76),
)

CBLOL_ROWS_BEFORE_LEVEL_UP = [
    make_row_before_level_up(*record) for record in CBLOL_NET_WORTHS_BEFORE_LEVEL_UP
]
CBLOL_SERIES_TABLE_BEFORE_LEVEL_UP = make_table(
    CBLOL_SERIES_ID,
    1,
    CBLOL_ROWS_BEFORE_LEVEL_UP,
    make_row_before_level_up(FLUXO, "", "", 4800, 0, 0, 0),
)

LEC_ROWS = [make_row(*record) for record in LEC_NET_WORTHS]
LEC_SERIES_TABLE = make_table(
    LEC_SERIES_ID,
    2,
    LEC_ROWS,
    make_row(NAVI, "", "", 8060, 0, 0, 0, 14),
)

CBLOL_SCOREBOARD: ScoreboardPayload = {
    "seriesId": CBLOL_SERIES_ID,
    "activeGameIndex": 0,
    "tournament": {"name": "CBLOL - Split 2 2026 (Regular Season: Regular Season)"},
    "series": {
        "startTimeDate": "2026-08-29T18:00:00Z",
        "endTimeDate": "",
        "status": "live",
        "format": "best-of-3",
        "title": "lol",
        "teams": [
            {
                "id": FLUXO,
                "name": "Fluxo W7M",
                "logoUrl": "https://cdn.grid.gg/assets/team-logos/generic",
                "teamColor": f"teams.{FLUXO}",
                "score": 0,
                "won": False,
            },
            {
                "id": LOS,
                "name": "LOS",
                "logoUrl": "https://cdn.grid.gg/assets/team-logos/generic",
                "teamColor": f"teams.{LOS}",
                "score": 0,
                "won": False,
            },
        ],
    },
    "games": [
        {
            "mapName": "Summoner's Rift",
            "status": "live",
            "centeredInfoText": "Summoner'S Rift",
            "teams": [
                {"id": FLUXO, "score": 4, "won": False, "infoText": BLUE_INFO},
                {"id": LOS, "score": 6, "won": False, "infoText": RED_INFO},
            ],
            "gameClock": {
                "isTicking": True,
                "tickingBackwards": False,
                "currentSeconds": 764,
                "color": "white",
                "occurredAt": "2026-08-29T18:17:46.158Z",
                "publishDelay": 3,
            },
        }
    ],
}

LEC_SCOREBOARD: ScoreboardPayload = {
    "seriesId": LEC_SERIES_ID,
    "activeGameIndex": 1,
    "tournament": {"name": "LEC - Summer 2026 (Regular Season: Regular Season)"},
    "series": {
        "startTimeDate": "2026-08-29T16:50:00Z",
        "endTimeDate": "",
        "status": "live",
        "format": "best-of-3",
        "title": "lol",
        "teams": [
            {
                "id": NAVI,
                "name": "Natus Vincere",
                "logoUrl": "https://cdn.grid.gg/assets/team-logos/generic",
                "teamColor": f"teams.{NAVI}",
                "score": 1,
                "won": False,
            },
            {
                "id": GIANTX,
                "name": "GIANTX",
                "logoUrl": "https://cdn.grid.gg/assets/team-logos/generic",
                "teamColor": f"teams.{GIANTX}",
                "score": 0,
                "won": False,
            },
        ],
    },
    "games": [
        {
            "mapName": "Summoner's Rift",
            "status": "finished",
            "centeredInfoText": "Summoner'S Rift",
            "teams": [
                {"id": NAVI, "score": 20, "won": True, "infoText": RED_INFO},
                {"id": GIANTX, "score": 7, "won": False, "infoText": BLUE_INFO},
            ],
            "gameClock": {
                "isTicking": False,
                "tickingBackwards": False,
                "currentSeconds": 1999,
                "color": "white",
                "occurredAt": "2026-08-29T18:17:45.474Z",
                "publishDelay": 1,
            },
        },
        {
            "mapName": "Summoner's Rift",
            "status": "live",
            "centeredInfoText": "Summoner'S Rift",
            "teams": [
                {"id": NAVI, "score": 0, "won": False, "infoText": RED_INFO},
                {"id": GIANTX, "score": 0, "won": False, "infoText": BLUE_INFO},
            ],
            "gameClock": {
                "isTicking": True,
                "tickingBackwards": False,
                "currentSeconds": 144,
                "color": "white",
                "occurredAt": "2026-08-29T18:17:45.474Z",
                "publishDelay": 1,
            },
        },
    ],
}


def wrap(service: str, delay: int, payload: object, series_id: str) -> str:
    """Wrap one payload in the socket envelope, uncompressed as we request it."""
    return json.dumps(
        {
            "service": f"integrity_safe_{service}",
            "delay": delay,
            "scope": {"id": series_id, "type": "series"},
            "data": [{"data": json.dumps(payload), "isCompressed": False}],
        }
    )


def player_rows(table: SeriesTablePayload) -> list[TableRow]:
    """Return the Game/Player tableRows of a compact series_table fixture."""
    game = next(group for group in table["stateGroups"] if group["name"] == "Game")
    players = next(
        group for group in game["states"][0]["entityGroups"] if group["name"] == "Player"
    )
    return players["tables"][0]["tableRows"]
