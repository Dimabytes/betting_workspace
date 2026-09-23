"""Scan trader tapes: LoL maps with grid_state + livestats availability."""

import json
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
TRADER = E / "data/trader"
WINDOWS = E / "data/lol/raw/lolesports/windows"
DETAILS = E / "data/lol/raw/lolesports/details"
LINKS = E / "data/lol/processed/lolesports_links/links.parquet"
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/lag-devin")


def main() -> None:
    links = pd.read_parquet(LINKS)
    by_cond = links.set_index("condition_id")
    rows = []
    for d in sorted(TRADER.iterdir()):
        match_path = d / "match.json"
        if not match_path.is_file() or not d.name.startswith("grid-"):
            continue
        try:
            m = json.loads(match_path.read_text())
        except Exception as exc:
            print(f"{d.name}: bad match.json {exc}")
            continue
        if m.get("game") != "lol":
            continue
        cond = m.get("market", {}).get("condition_id")
        grid_state = d / "grid_state.jsonl"
        grid_state_gz = d / "grid_state.jsonl.gz"
        has_grid = grid_state.is_file() or grid_state_gz.is_file()
        session = (d / "session.jsonl").is_file()
        link = by_cond.loc[cond] if cond in by_cond.index else None
        esports_game_id = str(link["esports_game_id"]) if link is not None else None
        has_windows = esports_game_id is not None and (WINDOWS / f"{esports_game_id}.jsonl.gz").is_file()
        has_details = esports_game_id is not None and (DETAILS / f"{esports_game_id}.jsonl.gz").is_file()
        rows.append(
            {
                "match_id": d.name,
                "cond": cond,
                "series": m.get("market", {}).get("grid_series_id"),
                "map": m.get("map_number"),
                "joined": m.get("joined_at_utc"),
                "slug": m.get("market", {}).get("market_slug"),
                "radiant": m.get("teams", {}).get("radiant"),
                "dire": m.get("teams", {}).get("dire"),
                "has_grid": has_grid,
                "has_session": session,
                "egid": esports_game_id,
                "has_windows": has_windows,
                "has_details": has_details,
                "dur": (m.get("final") or {}).get("duration_seconds"),
            }
        )
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "tapes.csv", index=False)
    print(f"lol tapes: {len(df)}")
    print(f"  with grid_state: {df.has_grid.sum()}")
    print(f"  with session: {df.has_session.sum()}")
    print(f"  linked to esports_game_id: {df.egid.notna().sum()}")
    print(f"  with windows: {df.has_windows.sum()}")
    print(f"  with details: {df.has_details.sum()}")
    both = df[df.has_grid & df.has_windows]
    print(f"  BOTH grid_state + windows: {len(both)}")
    print(both[["match_id", "slug", "joined", "egid", "dur"]].to_string())


if __name__ == "__main__":
    main()
