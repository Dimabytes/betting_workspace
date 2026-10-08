"""Recorded GRID widget payloads from series 2995964, shared by every GRID test."""

import json
from typing import cast

from trader.grid_widget_types import ScoreboardPayload, SeriesTablePayload, TableRow

KLIM = "53000"
LYNX = "54613"

SCOREBOARD: ScoreboardPayload = {
    "seriesId": "2995964",
    "activeGameIndex": 0,
    "tournament": {"name": "EPL Masters II (Play-Ins)"},
    "series": {
        "startTimeDate": "2026-08-24T10:00:00Z",
        "endTimeDate": "",
        "status": "live",
        "format": "best-of-3",
        "title": "dota",
        "teams": [
            {
                "id": KLIM,
                "name": "Klim Sani4",
                "logoUrl": "https://cdn.grid.gg/assets/team-logos/generic",
                "teamColor": f"teams.{KLIM}",
                "score": 1,
                "won": False,
            },
            {
                "id": LYNX,
                "name": "Team Lynx",
                "logoUrl": "https://cdn.grid.gg/assets/team-logos/e977e4",
                "teamColor": f"teams.{LYNX}",
                "score": 0,
                "won": False,
            },
        ],
    },
    "games": [
        {
            "mapName": "Defense of the Ancients",
            "status": "live",
            "centeredInfoText": "Game 1",
            "teams": [
                {
                    "id": KLIM,
                    "score": 24,
                    "won": True,
                    "infoText": {"text": "DIRE", "color": "#D14E3D"},
                },
                {
                    "id": LYNX,
                    "score": 22,
                    "won": False,
                    "infoText": {"text": "RADIANT", "color": "#9FCD3D"},
                },
            ],
            "gameClock": {
                "isTicking": True,
                "tickingBackwards": False,
                "currentSeconds": 2647,
                "color": "white",
                "occurredAt": "2026-08-24T11:07:52.653Z",
                "publishDelay": 1,
            },
        }
    ],
}

# team, nick, hero, net worth, kills, deaths, assists, level-ups
NET_WORTHS = (
    (KLIM, "423", "life-stealer", 30784, 8, 3, 6, 22),
    (KLIM, "mangekyou", "beastmaster", 26369, 3, 4, 9, 19),
    (KLIM, "swedenstrong", "rubick", 14776, 4, 5, 15, 19),
    (KLIM, "Rein", "dazzle", 13263, 2, 7, 12, 19),
    (KLIM, "squad1x", "monkey-king", 26438, 7, 3, 10, 24),
    (LYNX, "naive-", "juggernaut", 23074, 7, 4, 8, 19),
    (LYNX, "mellojul", "puck", 22479, 8, 4, 11, 18),
    (LYNX, "htiviy_vladik", "tidehunter", 22279, 2, 5, 14, 17),
    (LYNX, "QBFY", "witch-doctor", 10884, 3, 6, 13, 13),
    (LYNX, "kreker", "crystal-maiden", 8641, 2, 5, 16, 12),
)


def make_row(
    team: str,
    nick: str,
    hero: str,
    net: int,
    kills: int,
    deaths: int,
    assists: int,
    level_ups: int,
) -> TableRow:
    """Build one `series_table` player row in the shape GRID sends."""
    return {
        "entity": {
            "value": nick,
            "hexColor": "#5b6f7e",
            "teamColor": f"teams.{team}",
            "iconUrl": f"https://cdn.grid.gg/assets/dota/characters/{hero}.png",
        },
        "NetWorth": {"value": net, "teamColor": None},
        "Kills": {"value": kills, "teamColor": None},
        "Deaths": {"value": deaths, "teamColor": None},
        "KillAssistsGiven": {"value": assists, "teamColor": None},
        "increaseLevel": {"value": level_ups, "teamColor": None},
    }


def make_portraitless_row(team: str, nick: str, net: int) -> TableRow:
    """A row like the roster substitute's in grid-3007267-m3: a nick, NW, no iconUrl."""
    row = make_row(team, nick, "", net, 0, 0, 0, 0)
    del row["entity"]["iconUrl"]
    return row


ROWS = [make_row(*record) for record in NET_WORTHS]

SERIES_TABLE: SeriesTablePayload = {
    "Id": "2995964",
    "stateGroups": [
        {"name": "Series", "states": [{"sequenceNumber": 1, "entityGroups": []}]},
        {
            "name": "Game",
            "states": [
                {
                    "sequenceNumber": 1,
                    "entityGroups": [
                        {
                            "name": "Player",
                            "tables": [
                                {
                                    "tableRows": ROWS,
                                    "totalRow": make_row(KLIM, "", "", 198987, 46, 46, 114, 182),
                                }
                            ],
                        },
                        {"name": "Teams", "tables": []},
                    ],
                }
            ],
        },
    ],
}


def table_rows(table: SeriesTablePayload) -> list[TableRow]:
    """The Game/Player tableRows list of a series_table payload."""
    return table["stateGroups"][1]["states"][0]["entityGroups"][0]["tables"][0]["tableRows"]


def copy_series_table() -> SeriesTablePayload:
    """A SERIES_TABLE deep copy safe to mutate."""
    return cast(SeriesTablePayload, json.loads(json.dumps(SERIES_TABLE)))


def table_with_extra_row(row: TableRow) -> SeriesTablePayload:
    """A SERIES_TABLE deep copy with `row` appended to the player table."""
    payload = copy_series_table()
    table_rows(payload).append(row)
    return payload


def foreign_table() -> SeriesTablePayload:
    """The degenerate table GRID sometimes sends: one NW-0 row under a foreign team id."""
    payload = copy_series_table()
    table_rows(payload)[:] = [make_portraitless_row("99999", "ghost", 0)]
    return payload


def wrap(service: str, delay: int, payload: object) -> str:
    """Wrap one payload in the socket envelope, uncompressed as we request it."""
    return json.dumps(
        {
            "service": f"integrity_safe_{service}",
            "delay": delay,
            "scope": {"id": "2995964", "type": "series"},
            "data": [{"data": json.dumps(payload), "isCompressed": False}],
        }
    )
