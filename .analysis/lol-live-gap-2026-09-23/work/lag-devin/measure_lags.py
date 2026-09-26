"""Measure GRID-vs-livestats lag per LoL map using death events.

For every linked map (trader tape + livestats archive):
- livestats: death counters per side at rfc460Timestamp wall + spawn-relative game time
- GRID scoreboard: kill counters per side at received_at + occurredAt + currentSeconds
- GRID table: death counters per side at received_at (no occurredAt in table)
Emits per-event rows for delta distributions.
"""

import gzip
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))

from lol.livestats_frames import (  # noqa: E402
    assign_game_times,
    dedup_sort_frames,
    find_spawn_index,
    parse_sides,
    read_archive_payloads,
)
from trader.grid_widgets import (  # noqa: E402
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    parse_frame,
    read_map_scoreboard,
    read_net_worth,
)

WINDOWS = E / "data/lol/raw/lolesports/windows"
LINKS = E / "data/lol/processed/lolesports_links/links.parquet"
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/lag-devin")
OTHER = {"blue": "RED", "red": "BLUE"}
SAME = {"blue": "BLUE", "red": "RED"}


def parse_iso(s: str) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass(frozen=True)
class LsEvent:
    side: str
    counter: int
    wall: float
    gt: float


def livestats_events(egid: str, anchor_ts: int) -> tuple[list[LsEvent], float, int] | None:
    """Death events from window frames plus spawn wall and frame count."""
    payloads = read_archive_payloads(WINDOWS, egid)
    if not payloads:
        return None
    frames = dedup_sort_frames(payloads)
    if not frames:
        return None
    spawn_idx = find_spawn_index(frames, anchor_ts)
    if spawn_idx is None:
        return None
    spawn_wall = frames[spawn_idx].wall_seconds
    clock = assign_game_times(frames[spawn_idx:])
    events: list[LsEvent] = []
    prev = {"blue": 0, "red": 0}
    for cf in clock.timed:
        sides = parse_sides(cf.payload)
        if isinstance(sides, int):
            continue
        for side, team in (("blue", sides.blue), ("red", sides.red)):
            deaths = sum(p.deaths for p in team.players)
            if deaths > prev[side]:
                for c in range(prev[side] + 1, deaths + 1):
                    events.append(LsEvent(side, c, cf.wall_seconds, cf.game_time))
                prev[side] = deaths
    return events, spawn_wall, len(clock.timed)


@dataclass(frozen=True)
class GridResult:
    sb_events: list[dict]
    tb_events: list[dict]
    wire_lags: list[float]
    clock_zero_offsets: list[float]
    tb_recv_count: int
    sb_recv_count: int


def grid_events(match_dir: Path, map_number: int) -> GridResult:
    """Scoreboard kill events, table death events, wire lags, clock-zero estimates."""
    sb_events: list[dict] = []
    tb_events: list[dict] = []
    wire_lags: list[float] = []
    clock_zero: list[float] = []
    team_side: dict[str, str] = {}
    prev_kills = {"BLUE": 0, "RED": 0}
    prev_deaths = {"BLUE": 0, "RED": 0}
    sb_n = tb_n = 0
    for name in ("grid_state.jsonl.gz", "grid_state.jsonl"):
        path = match_dir / name
        if path.is_file():
            break
    else:
        return GridResult(sb_events, tb_events, wire_lags, clock_zero, tb_n, sb_n)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
                recv = parse_iso(rec["received_at_utc"])
                frame = parse_frame(rec["frame"])
            except Exception:
                continue
            if recv is None or not frame.payload:
                continue
            if frame.service == SCOREBOARD_SERVICE:
                try:
                    board = read_map_scoreboard(frame.payload, map_number)
                except Exception:
                    continue
                if board is None:
                    continue
                sb_n += 1
                for team in board.teams:
                    team_side[team.team_id] = team.side
                occ = parse_iso(board.occurred_at)
                if occ is not None:
                    wire_lags.append(recv - occ)
                    if board.clock_ticking and board.game_status == "live":
                        clock_zero.append(occ - board.clock_seconds)
                for side in ("BLUE", "RED"):
                    kills = sum(t.kills for t in board.teams if t.side == side)
                    if kills > prev_kills[side]:
                        for c in range(prev_kills[side] + 1, kills + 1):
                            sb_events.append(
                                {
                                    "side": side,
                                    "counter": c,
                                    "sb_recv": recv,
                                    "sb_occ": occ,
                                    "sb_clock": board.clock_seconds,
                                }
                            )
                        prev_kills[side] = kills
            elif frame.service == TABLE_SERVICE:
                try:
                    table = read_net_worth(frame.payload, frame.delay)
                except Exception:
                    continue
                if table is None or table.game_number != map_number:
                    continue
                tb_n += 1
                for side in ("BLUE", "RED"):
                    deaths = sum(
                        p.deaths for p in table.players if team_side.get(p.team_id) == side
                    )
                    if deaths > prev_deaths[side]:
                        for c in range(prev_deaths[side] + 1, deaths + 1):
                            tb_events.append({"side": side, "counter": c, "tb_recv": recv})
                        prev_deaths[side] = deaths
    return GridResult(sb_events, tb_events, wire_lags, clock_zero, tb_n, sb_n)


def process_map(row) -> list[dict] | None:
    """All matched event deltas for one map, or None."""
    match_dir = E / "data/trader" / row["match_id"]
    links = pd.read_parquet(LINKS)
    link = links[links.condition_id == row["cond"]]
    if link.empty:
        return None
    link = link.iloc[0]
    egid = str(link["esports_game_id"])
    anchor_ts = int(link["loading_anchor_ts"])
    ls = livestats_events(egid, anchor_ts)
    if ls is None:
        return None
    ls_events, spawn_wall, n_frames = ls
    match = json.loads((match_dir / "match.json").read_text())
    g = grid_events(match_dir, int(match["map_number"]))
    ls_idx = {(e.counter, e.side): e for e in ls_events}
    sb_idx = {(e["counter"], e["side"]): e for e in g.sb_events}
    tb_idx = {(e["counter"], e["side"]): e for e in g.tb_events}
    import statistics

    rows = []
    for (c, ls_side), le in sorted(ls_idx.items()):
        se = sb_idx.get((c, OTHER[ls_side]))
        te = tb_idx.get((c, SAME[ls_side]))
        if se is None and te is None:
            continue
        rows.append(
            {
                "match_id": row["match_id"],
                "counter": c,
                "dead_side": ls_side,
                "ls_wall": le.wall,
                "ls_gt": le.gt,
                "sb_recv": se["sb_recv"] if se else None,
                "sb_occ": se["sb_occ"] if se else None,
                "sb_clock": se["sb_clock"] if se else None,
                "tb_recv": te["tb_recv"] if te else None,
            }
        )
    if not rows:
        return None
    meta = {
        "match_id": row["match_id"],
        "spawn_wall": spawn_wall,
        "n_ls_frames": n_frames,
        "n_events": len(rows),
        "sb_frames": g.sb_recv_count,
        "tb_frames": g.tb_recv_count,
        "wire_lag_median": statistics.median(g.wire_lags) if g.wire_lags else None,
        "clock_zero_offset": (
            statistics.median(g.clock_zero_offsets) - spawn_wall
            if g.clock_zero_offsets
            else None
        ),
        "grid_delay_s": (match.get("grid_delay_s")),
    }
    return {"events": rows, "meta": meta, "wire_lags": g.wire_lags[:500]}


def main() -> None:
    tapes = pd.read_csv(OUT / "tapes.csv")
    linked = tapes[tapes.has_grid & tapes.has_windows].reset_index(drop=True)
    print(f"linked maps: {len(linked)}")
    all_events: list[dict] = []
    all_meta: list[dict] = []
    wire_pool: list[float] = []
    done = 0
    with ProcessPoolExecutor(max_workers=8) as pool:
        for result in pool.map(process_map, linked.to_dict("records"), chunksize=4):
            done += 1
            if done % 40 == 0:
                print(f"  {done}/{len(linked)}")
            if result is None:
                continue
            all_events.extend(result["events"])
            all_meta.append(result["meta"])
            wire_pool.extend(result["wire_lags"])
    ev = pd.DataFrame(all_events)
    meta = pd.DataFrame(all_meta)
    ev.to_parquet(OUT / "events.parquet")
    meta.to_parquet(OUT / "map_meta.parquet")
    pd.Series(wire_pool).to_csv(OUT / "wire_lags.csv", index=False)
    print(f"events: {len(ev)}  maps: {len(meta)}")
    if len(ev):
        ev["sb_recv_minus_ls"] = ev.sb_recv - ev.ls_wall
        ev["sb_occ_minus_ls"] = ev.sb_occ - ev.ls_wall
        ev["tb_recv_minus_ls"] = ev.tb_recv - ev.ls_wall
        ev["tb_recv_minus_occ"] = ev.tb_recv - ev.sb_occ
        ev["sb_clock_minus_ls_gt"] = ev.sb_clock - ev.ls_gt
        for col in (
            "sb_recv_minus_ls",
            "sb_occ_minus_ls",
            "tb_recv_minus_ls",
            "tb_recv_minus_occ",
            "sb_clock_minus_ls_gt",
        ):
            s = ev[col].dropna()
            print(
                f"{col}: n={len(s)} p10={s.quantile(0.1):.2f} median={s.median():.2f} "
                f"p90={s.quantile(0.9):.2f}"
            )


if __name__ == "__main__":
    main()
