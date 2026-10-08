"""Print every KashRock live tick for one Dota 2 / LoL match, polled over REST.

KashRock's push socket is Builder+ only, so on the sandbox plan this polls
GET /v6/esports/<sport>/live/<game_id>/boxscore every POLL_SECONDS. The log
timestamp is the local receive time; each line also prints the frame's own
`ts` and its age. Run it next to watch_grid_live.py on the same map to read
the real feed delay.

There is no xp field in the payload; `level` is the proxy. Board carries
kills, towers, team gold and per-player K/D/A, creep_score, total_gold, level.

Invocation:
  make run F=scripts/watch_kashrock_live.py                 # list live dota2 games
  make run F=scripts/watch_kashrock_live.py ARGS="kalmy"    # substring of game_id
  make run F=scripts/watch_kashrock_live.py ARGS="kr_dota2_kalmychata-vs-team-lynx-23-09-2026"
  make run F=scripts/watch_kashrock_live.py ARGS="--sport lol kwk"
"""

# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

from shared.utils.http import get_json, http_client
from shared.utils.log import get_logger, setup_logging
from shared.utils.match_time import parse_utc

logger = get_logger(__name__)

KASHROCK_API = "https://kashrock.up.railway.app"
CREDENTIALS_PATH = Path("~/.kashrock/credentials").expanduser()
POLL_SECONDS = 15.0
MAX_CONSECUTIVE_FAILURES = 8
FINISHED_STATES = {"finished", "completed", "ended"}


@dataclass(frozen=True)
class LiveGame:
    """One live game row from /live/games."""

    game_id: str
    match_id: str
    number: int
    blue_code: str
    red_code: str


@dataclass(frozen=True)
class PlayerRow:
    """One player's live line: K/D/A plus farm."""

    side: str
    nickname: str
    kills: int
    deaths: int
    assists: int
    creep_score: int
    total_gold: int
    level: int


@dataclass(frozen=True)
class SideBoard:
    """Team-level counters for one side."""

    kills: int
    towers: int
    series_score: int


@dataclass(frozen=True)
class Board:
    """The comparable content of one boxscore (everything but timestamps)."""

    state: str
    round_clock: str
    map_number: int
    blue: SideBoard
    red: SideBoard
    gold_blue: int
    gold_red: int
    players: tuple[PlayerRow, ...]


@dataclass(frozen=True)
class Tick:
    """One polled boxscore plus the server timestamps used for lag math."""

    board: Board
    frame_ts: datetime | None
    ingested_at: datetime | None


def read_api_key() -> str:
    """Return the KashRock key from KASHROCK_API_KEY or ~/.kashrock/credentials."""
    from_env = os.environ.get("KASHROCK_API_KEY", "")
    if from_env:
        return from_env
    credentials = json.loads(CREDENTIALS_PATH.read_text())
    return cast(str, credentials["api_key"])


def parse_player(raw: dict[str, Any]) -> PlayerRow:
    """Project one KashRock player object onto the printed fields."""
    return PlayerRow(
        side=raw.get("side", ""),
        nickname=raw.get("nickname") or raw.get("summoner_name") or "?",
        kills=raw.get("kills") or 0,
        deaths=raw.get("deaths") or 0,
        assists=raw.get("assists") or 0,
        creep_score=raw.get("creep_score") or 0,
        total_gold=raw.get("total_gold") or 0,
        level=raw.get("level") or 0,
    )


def parse_side(raw: dict[str, Any]) -> SideBoard:
    """Project one side block (kills/towers/series score)."""
    return SideBoard(
        kills=raw.get("kills") or 0,
        towers=raw.get("towers") or 0,
        series_score=raw.get("series_score") or 0,
    )


def parse_ts(value: Any) -> datetime | None:
    """Parse a KashRock ISO timestamp; None when absent or unparsable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_utc(value)
    except ValueError:
        return None


def parse_boxscore(payload: dict[str, Any]) -> Tick | None:
    """Build a Tick from the /live/<game>/boxscore response; None on empty."""
    box = payload.get("boxscore") or {}
    if not box:
        return None
    gold = (box.get("board") or {}).get("gold") or {}
    players = tuple(parse_player(p) for p in box.get("players") or [])
    board = Board(
        state=box.get("state", ""),
        round_clock=str(box.get("round_clock") or box.get("clock") or "-"),
        map_number=box.get("map_number") or 0,
        blue=parse_side(box.get("blue") or {}),
        red=parse_side(box.get("red") or {}),
        gold_blue=gold.get("blue") or 0,
        gold_red=gold.get("red") or 0,
        players=players,
    )
    return Tick(
        board=board,
        frame_ts=parse_ts(box.get("ts")),
        ingested_at=parse_ts(box.get("ingested_at")),
    )


def list_live_games(client: httpx.Client, sport: str) -> tuple[LiveGame, ...]:
    """List all games with live telemetry for one sport."""
    payload = get_json(client, f"{KASHROCK_API}/v6/esports/{sport}/live/games")
    rows = payload.get("games") or []
    return tuple(
        LiveGame(
            game_id=row["game_id"],
            match_id=row.get("kr_match_id") or row.get("match_id") or row["game_id"],
            number=row.get("number") or 0,
            blue_code=row.get("blue_code") or "?",
            red_code=row.get("red_code") or "?",
        )
        for row in rows
    )


def log_games(games: tuple[LiveGame, ...]) -> None:
    """Print the live-game table used to pick a game_id."""
    logger.info("live games: %s", len(games))
    for game in games:
        logger.info(
            "  map=%-3s %-24s vs %-24s %s",
            game.number,
            game.blue_code,
            game.red_code,
            game.game_id,
        )


def resolve_game(client: httpx.Client, sport: str, selector: str) -> LiveGame:
    """Resolve a substring of a game_id/match_id to one live game."""
    games = list_live_games(client, sport)
    matches = [game for game in games if selector in game.game_id or selector in game.match_id]
    if not matches:
        log_games(games)
        raise SystemExit(f"no live {sport} game matching {selector!r}")
    return max(matches, key=lambda game: game.number)


def format_players(board: Board, side: str) -> str:
    """Render one side as `nick L12 5230g cs41 3/1/4 ...`, richest first."""
    players = [player for player in board.players if player.side == side]
    players.sort(key=lambda player: -player.total_gold)
    return "  ".join(
        f"{p.nickname} L{p.level} {p.total_gold}g cs{p.creep_score} "
        f"{p.kills}/{p.deaths}/{p.assists}"
        for p in players
    )


def log_tick(tick: Tick, game: LiveGame, received_at: datetime) -> None:
    """Print one tick line plus one player line per side."""
    board = tick.board
    frame_age = (
        "-" if tick.frame_ts is None else f"{(received_at - tick.frame_ts).total_seconds():.1f}s"
    )
    logger.info(
        "t=%-6s %-8s map=%-2s kills=%-7s twr=%-6s gold=%-11s ts=%s age=%s",
        board.round_clock,
        board.state,
        board.map_number,
        f"{board.blue.kills}-{board.red.kills}",
        f"{board.blue.towers}-{board.red.towers}",
        f"{board.gold_blue}-{board.gold_red}",
        tick.frame_ts.strftime("%H:%M:%S") if tick.frame_ts else "-",
        frame_age,
    )
    logger.info("     blue %-18s %s", game.blue_code, format_players(board, "blue"))
    logger.info("     red  %-18s %s", game.red_code, format_players(board, "red"))


def fetch_tick(client: httpx.Client, sport: str, game_id: str) -> Tick | None:
    """Poll one boxscore; None when the response carries no board."""
    payload = get_json(client, f"{KASHROCK_API}/v6/esports/{sport}/live/{game_id}/boxscore")
    return parse_boxscore(payload)


def next_map_game(client: httpx.Client, sport: str, game: LiveGame) -> LiveGame | None:
    """Find the next live game of the same match after the current map ends."""
    candidates = [
        row
        for row in list_live_games(client, sport)
        if row.match_id == game.match_id and row.game_id != game.game_id
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.number)


def follow_game(client: httpx.Client, sport: str, game: LiveGame) -> int:
    """Poll the boxscore every POLL_SECONDS, printing each tick that changed."""
    logger.info(
        "following %s vs %s (%s) poll=%.0fs",
        game.blue_code,
        game.red_code,
        game.game_id,
        POLL_SECONDS,
    )
    current = game
    last_board: Board | None = None
    failures = 0
    while True:
        try:
            tick = fetch_tick(client, sport, current.game_id)
            failures = 0
        except httpx.HTTPError as exc:
            failures += 1
            logger.warning("poll failed (%s/%s): %s", failures, MAX_CONSECUTIVE_FAILURES, exc)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                return 1
            time.sleep(POLL_SECONDS)
            continue
        received_at = datetime.now(UTC)
        if tick is not None and tick.board != last_board:
            log_tick(tick, current, received_at)
            last_board = tick.board
        if tick is not None and tick.board.state in FINISHED_STATES:
            following = next_map_game(client, sport, current)
            if following is None:
                logger.info("map %s finished, no next live game; stopping", current.number)
                return 0
            logger.info("map %s finished, hopping to %s", current.number, following.game_id)
            current = following
            last_board = None
        time.sleep(POLL_SECONDS)


def main() -> int:
    """List live games, or follow the one matched by the selector."""
    setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("selector", nargs="?", default="", help="game_id or substring")
    parser.add_argument("--sport", default="dota2", help="kashrock sport key")
    args = parser.parse_args()
    client = http_client()
    client.headers["x-api-key"] = read_api_key()
    with client:
        if not args.selector:
            log_games(list_live_games(client, args.sport))
            logger.info("pass a game_id (or substring) to follow one game")
            return 0
        game = resolve_game(client, args.sport, args.selector)
        return follow_game(client, args.sport, game)


if __name__ == "__main__":
    raise SystemExit(main())
