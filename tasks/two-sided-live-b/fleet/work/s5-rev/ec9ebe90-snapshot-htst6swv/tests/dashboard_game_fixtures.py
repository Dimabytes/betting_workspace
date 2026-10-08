import json
from collections.abc import Iterable
from pathlib import Path

from dashboard.catalog import ArchiveEntry
from trader.paths import (
    GRID_STATE_ARCHIVE_FILENAME,
    MATCH_META_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
    SESSION_JOURNAL_FILENAME,
)

DOTA_RADIANT = "Team Lynx"
DOTA_DIRE = "Klim Sani4"
LOL_BLUE = "GIANTX"
LOL_RED = "Natus Vincere"
ODDIN_HOME = "LGD Gaming"
ODDIN_AWAY = "Yakult's Brothers"

GRID_BOARD_AT = "2026-08-24T11:07:52.653Z"
GRID_TABLE_AT = "2026-08-24T11:07:55.000Z"
GRID_NEXT_AT = "2026-08-24T11:08:00.000Z"
ODDIN_WS_AT = "2026-09-19T16:57:50.000000Z"
ODDIN_UPDATED = "2026-09-19 16:57:34.676000000 +0000 UTC"
ODDIN_UPDATED_NEXT = "2026-09-19 16:57:35.141000000 +0000 UTC"
ODDIN_TEAM_GOLD_PAD = 97


def write_meta(
    archive_dir: Path,
    *,
    match_id: str,
    game: str = "dota",
    map_number: int = 1,
    feed_source: str = "grid",
    yes_is_radiant: bool = True,
    radiant: str = DOTA_RADIANT,
    dire: str = DOTA_DIRE,
) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": 9,
        "match_id": match_id,
        "game": game,
        "map_number": map_number,
        "feed_source": feed_source,
        "teams": {"radiant": radiant, "dire": dire},
        "market": {
            "condition_id": f"cond-{match_id}",
            "yes_is_radiant": yes_is_radiant,
            "outcome_0_name": radiant,
            "outcome_1_name": dire,
        },
        "model": {"name": "m", "trained_at": "t"},
        "final": None,
    }
    (archive_dir / MATCH_META_FILENAME).write_text(json.dumps(doc), encoding="utf-8")


def write_lines(path: Path, records: Iterable[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def grid_record(frame: str, received: str) -> dict[str, object]:
    return {"received_at_utc": received, "frame": frame}


def oddin_record(payload: object, received: str, event: str = "ws") -> dict[str, object]:
    return {"received_at_utc": received, "event": event, "payload": payload}


def signal_record(
    *,
    second: int,
    feed_source: str = "grid",
    feed_received_at_utc: str = GRID_TABLE_AT,
    recorded_at_utc: str = "2026-08-24T11:07:56.000Z",
    server_timestamp: int = 1_787_240_875,
    phase: str = "in_progress",
    paused: bool = False,
    radiant_nw: int,
    dire_nw: int,
    radiant_xp_adv: int = 1200,
    deaths_radiant: int = 0,
    deaths_dire: int = 0,
    model_evaluated: bool | None = True,
    raw_delta: float | None = 0.05,
    reason: str = "model",
    entry_block: str = "none",
    market_radiant_prior: float | None = None,
    with_snapshot: bool = True,
) -> dict[str, object]:
    record: dict[str, object] = {
        "kind": "signal",
        "second": second,
        "yes_best_bid": 0.4,
        "yes_best_ask": 0.45,
        "yes_mid": 0.42,
        "reason": reason,
        "entry_block": entry_block,
        "feed_source": feed_source,
        "feed_received_at_utc": feed_received_at_utc,
        "recorded_at_utc": recorded_at_utc,
    }
    if market_radiant_prior is not None:
        record["market_radiant_prior"] = market_radiant_prior
    if model_evaluated is not None:
        record["model_evaluated"] = model_evaluated
    if raw_delta is not None or model_evaluated is not None:
        record["raw_delta"] = raw_delta
    if with_snapshot:
        record["game_snapshot"] = {
            "second": second,
            "server_timestamp": server_timestamp,
            "phase": phase,
            "paused": paused,
            "radiant_nw_adv": radiant_nw - dire_nw,
            "radiant_nw": radiant_nw,
            "dire_nw": dire_nw,
            "radiant_xp_adv": radiant_xp_adv,
            "deaths_radiant": deaths_radiant,
            "deaths_dire": deaths_dire,
            "top": {
                "top1_nw_adv": 500,
                "radiant_top1_nw_ratio": 0.4,
                "dire_top1_nw_ratio": 0.3,
                "top3_nw_adv": 900,
                "radiant_top3_nw_ratio": 0.6,
                "dire_top3_nw_ratio": 0.5,
            },
        }
    return record


def oddin_player(
    nickname: str,
    net_worth: int,
    deaths: int = 0,
    *,
    hero: str | None = "Kez",
    kills: int | None = 0,
    assists: int | None = 0,
    alive: bool | None = True,
    respawn: int | None = None,
    aegis: bool | None = False,
) -> dict[str, object]:
    row: dict[str, object] = {
        "player": {"nickname": nickname},
        "netWorth": net_worth,
        "deaths": deaths,
    }
    if hero is not None:
        row["hero"] = {"name": hero}
    if kills is not None:
        row["kills"] = kills
    if assists is not None:
        row["assists"] = assists
    if alive is not None:
        row["alive"] = alive
    if respawn is not None:
        row["respawnTimer"] = respawn
    if aegis is not None:
        row["hasAegis"] = aegis
    return row


def oddin_five(prefix: str, base_nw: int, deaths_base: int = 0) -> list[dict[str, object]]:
    return [
        oddin_player(f"{prefix}{index}", base_nw + index, deaths_base + index) for index in range(5)
    ]


def oddin_side(name: str, faction: str, players: list[dict[str, object]]) -> dict[str, object]:
    team_nw = sum(
        int(player["netWorth"]) for player in players if isinstance(player["netWorth"], int)
    )
    return {
        "team": {"name": name},
        "faction": faction,
        "kills": 3,
        "netWorth": 0,
        "netWorthNullable": team_nw + ODDIN_TEAM_GOLD_PAD,
        "towers": 4,
        "barracks": 1,
        "barracksNullable": 1,
        "roshans": 1,
        "players": players,
    }


def oddin_payload(
    *,
    map_order: int = 1,
    game_time: int = 998,
    status: str = "LIVE",
    data_status: str = "VALID_DATA",
    paused: bool = False,
    updated: str = ODDIN_UPDATED,
    map_id: str = "map-1",
    home_faction: str = "DIRE",
    radiant_players: list[dict[str, object]] | None = None,
    dire_players: list[dict[str, object]] | None = None,
    home_score: int = 1,
    away_score: int = 1,
    previous: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    radiant = radiant_players if radiant_players is not None else oddin_five("R", 5000, 0)
    dire = dire_players if dire_players is not None else oddin_five("D", 7000, 1)
    away_faction = "RADIANT" if home_faction == "DIRE" else "DIRE"
    payload: dict[str, object] = {
        "matchStatus": status,
        "dataStatus": data_status,
        "lastUpdatedAt": updated,
        "mapPaused": paused,
        "homeScore": home_score,
        "awayScore": away_score,
        "homeTeam": {"name": ODDIN_HOME},
        "awayTeam": {"name": ODDIN_AWAY},
        "currentMap": {
            "id": map_id,
            "mapOrder": map_order,
            "gameTime": game_time,
            "homeTeam": oddin_side(
                ODDIN_HOME, home_faction, dire if home_faction == "DIRE" else radiant
            ),
            "awayTeam": oddin_side(
                ODDIN_AWAY, away_faction, radiant if away_faction == "RADIANT" else dire
            ),
        },
    }
    if previous is not None:
        payload["previousMaps"] = previous
    return payload


def make_entry(
    archive_dir: Path,
    *,
    match_id: str,
    game: str | None = "dota",
    map_number: int | None = 1,
    yes_is_radiant: bool | None = True,
    radiant: str | None = DOTA_RADIANT,
    dire: str | None = DOTA_DIRE,
    outcome_0_name: str | None = None,
    outcome_1_name: str | None = None,
    record_only: bool = False,
) -> ArchiveEntry:
    return ArchiveEntry(
        match_id=match_id,
        archive_dir=archive_dir,
        tree="test",
        game=game,
        slug=None,
        event_slug=None,
        joined_at_utc=None,
        condition_id=f"cond-{match_id}",
        yes_token=None,
        no_token=None,
        yes_is_radiant=yes_is_radiant,
        radiant=radiant,
        dire=dire,
        map_number=map_number,
        outcome_0_name=outcome_0_name if outcome_0_name is not None else radiant,
        outcome_1_name=outcome_1_name if outcome_1_name is not None else dire,
        finished=False,
        cleanup_proven=False,
        session_ended=False,
        has_journal=True,
        record_only=record_only,
        last_write=None,
        realized=None,
        imv=None,
        rebate=None,
        net=None,
        fill_count=None,
        closed_observed_at=None,
        last_decision=None,
        params=None,
    )


def journal_path(archive_dir: Path) -> Path:
    return archive_dir / SESSION_JOURNAL_FILENAME


def grid_state_path(archive_dir: Path) -> Path:
    return archive_dir / GRID_STATE_ARCHIVE_FILENAME


def oddin_state_path(archive_dir: Path) -> Path:
    return archive_dir / ODDIN_STATE_ARCHIVE_FILENAME
