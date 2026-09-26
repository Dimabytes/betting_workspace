"""List LoL live tapes from 2026-09-14..09-20 that also have a local livestats window."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from lol.constants import LOL_LINKS_PATH, LOL_WINDOWS_DIR
from lol.livestats_frames import FETCH

TRADER = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader")
OUT = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/clock-grok/candidates.json"
)
START = datetime(2026, 9, 14, tzinfo=timezone.utc)
END = datetime(2026, 9, 21, tzinfo=timezone.utc)


def parse_utc(text: object) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def in_window(stamp: datetime | None) -> bool:
    return stamp is not None and START <= stamp < END


def session_mode(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open() as handle:
        line = handle.readline()
    if not line:
        return None
    record = json.loads(line)
    if record.get("kind") != "session_start":
        return None
    mode = record.get("execution_mode")
    return mode if isinstance(mode, str) else None


def main() -> None:
    links = pd.read_parquet(
        LOL_LINKS_PATH,
        columns=["condition_id", "esports_game_id", "game_number", "loading_anchor_ts", "event_id"],
    )
    by_condition: dict[str, dict[str, object]] = {}
    for row in links.itertuples(index=False):
        by_condition[str(row.condition_id).lower()] = {
            "esports_game_id": str(row.esports_game_id),
            "game_number": int(row.game_number),
            "loading_anchor_ts": int(row.loading_anchor_ts),
            "event_id": str(row.event_id),
        }
    print(f"links {len(by_condition)}")

    rows: list[dict[str, object]] = []
    lol_seen = 0
    for child in sorted(TRADER.iterdir()):
        match_path = child / "match.json"
        if not match_path.is_file():
            continue
        meta = json.loads(match_path.read_text())
        if meta.get("game") != "lol":
            continue
        lol_seen += 1
        horn = parse_utc(meta.get("horn_at_utc"))
        joined = parse_utc(meta.get("joined_at_utc"))
        anchor = horn or joined
        if not in_window(anchor):
            continue
        market = meta.get("market") or {}
        condition = str(market.get("condition_id") or "").lower()
        link = by_condition.get(condition)
        game_id = None if link is None else str(link["esports_game_id"])
        window = None if game_id is None else FETCH.archive_path(LOL_WINDOWS_DIR, game_id)
        has_window = window is not None and window.is_file()
        session = child / "session.jsonl"
        grid = child / "grid_state.jsonl"
        final = meta.get("final") or {}
        rows.append(
            {
                "archive_id": child.name,
                "horn_at_utc": meta.get("horn_at_utc"),
                "joined_at_utc": meta.get("joined_at_utc"),
                "map_number": meta.get("map_number"),
                "grid_delay_s": meta.get("grid_delay_s"),
                "yes_is_radiant": market.get("yes_is_radiant"),
                "condition_id": market.get("condition_id"),
                "yes_token_id": market.get("yes_token_id"),
                "no_token_id": market.get("no_token_id"),
                "outcome_0_name": market.get("outcome_0_name"),
                "outcome_1_name": market.get("outcome_1_name"),
                "duration_seconds": final.get("duration_seconds"),
                "execution_mode": session_mode(session),
                "esports_game_id": game_id,
                "loading_anchor_ts": None if link is None else link["loading_anchor_ts"],
                "event_id": None if link is None else link["event_id"],
                "has_livestats": has_window,
                "session_bytes": session.stat().st_size if session.is_file() else 0,
                "grid_bytes": grid.stat().st_size if grid.is_file() else 0,
            }
        )
    usable = [
        row
        for row in rows
        if row["has_livestats"] and row["session_bytes"] and row["grid_bytes"]
    ]
    usable.sort(key=lambda row: int(row["session_bytes"]), reverse=True)
    print(f"lol tapes {lol_seen} in window {len(rows)} with livestats+session+grid {len(usable)}")
    modes: dict[str, int] = {}
    for row in rows:
        key = str(row["execution_mode"])
        modes[key] = modes.get(key, 0) + 1
    print("modes", modes)
    linked = sum(1 for row in rows if row["esports_game_id"])
    print(f"condition joined to links {linked}/{len(rows)}")
    for row in usable[:12]:
        print(
            row["archive_id"],
            row["horn_at_utc"],
            "mode",
            row["execution_mode"],
            "game",
            row["esports_game_id"],
            "dur",
            row["duration_seconds"],
            "session",
            row["session_bytes"],
            "grid",
            row["grid_bytes"],
            "delay",
            row["grid_delay_s"],
        )
    OUT.write_text(json.dumps({"in_window": rows, "usable": usable}, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
