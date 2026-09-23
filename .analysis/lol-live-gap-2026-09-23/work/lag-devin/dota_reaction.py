"""Dota market reaction around kills: GRID scoreboard kills + core_trace mids.

Same measurement as market_reaction.py but self-contained per tape:
kill events come from the GRID scoreboard itself (occurredAt = event time),
p_radiant from BookUpdate mids at radiant_token_index.
"""

import gzip
import json
import sys
from bisect import bisect_left
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/lag-devin")

from trader.grid_widgets import SCOREBOARD_SERVICE, parse_frame, read_map_scoreboard  # noqa: E402


def parse_iso(s):
    if not s:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def open_jsonl(match_dir: Path, stem: str):
    for name in (f"{stem}.jsonl.gz", f"{stem}.jsonl"):
        p = match_dir / name
        if p.is_file():
            return (gzip.open if p.suffix == ".gz" else open)(p, "rt", encoding="utf-8")
    return None


def tape_reaction(match_dir: Path, map_number: int):
    """Kill events (killer side, occ, recv) and the radiant mid series."""
    fh = open_jsonl(match_dir, "grid_state")
    if fh is None:
        return None
    kills = {"RADIANT": 0, "DIRE": 0}
    events = []
    wire = []
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
                recv = parse_iso(rec["received_at_utc"])
                frame = parse_frame(rec["frame"])
            except Exception:
                continue
            if recv is None or not frame.payload or frame.service != SCOREBOARD_SERVICE:
                continue
            try:
                board = read_map_scoreboard(frame.payload, map_number)
            except Exception:
                continue
            if board is None:
                continue
            occ = parse_iso(board.occurred_at)
            if occ is not None:
                wire.append(recv - occ)
            for side in ("RADIANT", "DIRE"):
                k = sum(t.kills for t in board.teams if t.side == side)
                if k > kills[side]:
                    for c in range(kills[side] + 1, k + 1):
                        events.append({"killer": side, "counter": c, "occ": occ, "recv": recv})
                    kills[side] = k
    fh2 = open_jsonl(match_dir, "core_trace")
    if fh2 is None:
        return None
    anchor_wall = anchor_ns = None
    radiant_index = None
    ts, ps = [], []
    with fh2:
        for line in fh2:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("kind") == "header":
                anchor_wall = float(rec["opened_wall_s"])
                anchor_ns = int(rec["opened_now_ns"])
                continue
            if rec.get("kind") != "event" or anchor_wall is None:
                continue
            ev = rec.get("event") or {}
            if ev.get("type") == "LimitsUpdate":
                radiant_index = int(ev["limits"]["radiant_token_index"])
                continue
            if ev.get("type") != "BookUpdate":
                continue
            books = (ev.get("books") or {}).get("tokens") or []
            if len(books) != 2 or radiant_index is None:
                continue
            try:
                b = books[radiant_index]
                mid = (float(b["bid"]) + float(b["ask"])) / 2
            except (TypeError, KeyError, ValueError):
                continue
            ts.append(anchor_wall + (int(rec["now_ns"]) - anchor_ns) / 1e9)
            ps.append(mid)
    return events, wire, ts, ps


def mid_at(ts, ps, wall):
    if not ts or wall < ts[0] or wall > ts[-1] + 1.0:
        return None
    i = bisect_left(ts, wall)
    if i < len(ts) and ts[i] == wall:
        return ps[i]
    if i == 0:
        return None
    return ps[i - 1]


def process_map(match_id: str) -> list[dict]:
    match_dir = E / "data/trader" / match_id
    try:
        meta = json.loads((match_dir / "match.json").read_text())
    except Exception:
        return []
    res = tape_reaction(match_dir, int(meta["map_number"]))
    if res is None:
        return []
    events, wire, ts, ps = res
    if len(ts) < 30:
        return []
    out = []
    for ev in events:
        E0 = ev["occ"]
        if E0 is None:
            continue
        sign = 1.0 if ev["killer"] == "RADIANT" else -1.0
        p0 = mid_at(ts, ps, E0)
        p60 = mid_at(ts, ps, E0 + 60.0)
        if p0 is None or p60 is None:
            continue
        move60 = sign * (p60 - p0)
        p_at = {d: mid_at(ts, ps, E0 + d) for d in (3, 5, 8, 11, 20, 30)}
        t50 = None
        if move60 > 0.002:
            i = bisect_left(ts, E0)
            for j in range(i, len(ts)):
                if ts[j] > E0 + 60.0:
                    break
                if sign * (ps[j] - p0) >= move60 * 0.5:
                    t50 = ts[j] - E0
                    break
        out.append(
            {
                "match_id": match_id,
                "killer": ev["killer"],
                "E": E0,
                "sb_recv": ev["recv"],
                "wire": (ev["recv"] - E0) if ev["recv"] else None,
                "move60_signed": move60,
                "m3": sign * (p_at[3] - p0) if p_at[3] is not None else None,
                "m5": sign * (p_at[5] - p0) if p_at[5] is not None else None,
                "m11": sign * (p_at[11] - p0) if p_at[11] is not None else None,
                "m30": sign * (p_at[30] - p0) if p_at[30] is not None else None,
                "t50": t50,
            }
        )
    return out


def main() -> None:
    rows = []
    ids = []
    for d in sorted((E / "data/trader").iterdir()):
        mp = d / "match.json"
        if not mp.is_file():
            continue
        try:
            m = json.loads(mp.read_text())
        except Exception:
            continue
        if m.get("game") == "dota" and (
            (d / "core_trace.jsonl.gz").is_file() or (d / "core_trace.jsonl").is_file()
        ) and ((d / "grid_state.jsonl.gz").is_file() or (d / "grid_state.jsonl").is_file()):
            ids.append(d.name)
    print("dota maps with ct+grid:", len(ids))
    with ProcessPoolExecutor(max_workers=8) as pool:
        for i, res in enumerate(pool.map(process_map, ids, chunksize=4)):
            if i % 40 == 0:
                print(i, "/", len(ids))
            rows.extend(res)
    df = pd.DataFrame(rows)
    df.to_parquet(OUT / "dota_reactions.parquet")
    print("dota reaction rows:", len(df))
    for col in ("move60_signed", "m3", "m5", "m11", "m30", "t50", "wire"):
        s = df[col].dropna()
        print(
            f"{col}: n={len(s)} p10={s.quantile(0.1):.3f} med={s.median():.3f} "
            f"mean={s.mean():.3f} p90={s.quantile(0.9):.3f}"
        )


if __name__ == "__main__":
    main()
