"""Market reaction around kills: p_radiant path from core_trace BookUpdates.

For each kill event (livestats-stamped wall E == GRID occurredAt):
- signed move = move toward the killer's token of the radiant mid
- fraction of the 60s move done at +5/+11/+30s and at sb_recv / tb_recv
- time to 50% of the 60s move
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


def mid_series(match_dir: Path) -> tuple[list[float], list[float]] | None:
    """(wall_s, p_radiant) from BookUpdate rows of core_trace."""
    path = None
    for name in ("core_trace.jsonl.gz", "core_trace.jsonl"):
        cand = match_dir / name
        if cand.is_file():
            path = cand
            break
    if path is None:
        return None
    opener = gzip.open if path.suffix == ".gz" else open
    anchor_wall = None
    anchor_ns = None
    radiant_index = 1  # default overwritten by LimitsUpdate
    ts: list[float] = []
    ps: list[float] = []
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
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
            etype = ev.get("type")
            if etype == "LimitsUpdate":
                radiant_index = int(ev["limits"]["radiant_token_index"])
                continue
            if etype != "BookUpdate":
                continue
            books = (ev.get("books") or {}).get("tokens") or []
            if len(books) != 2:
                continue
            try:
                b = books[radiant_index]
                bid = float(b["bid"])
                ask = float(b["ask"])
            except (TypeError, KeyError, ValueError):
                continue
            wall = anchor_wall + (int(rec["now_ns"]) - anchor_ns) / 1e9
            ts.append(wall)
            ps.append((bid + ask) / 2.0)
    if len(ts) < 30:
        return None
    return ts, ps


def mid_at(ts: list[float], ps: list[float], wall: float) -> float | None:
    """Last mid at or before wall; None if outside coverage."""
    if wall < ts[0] or wall > ts[-1] + 1.0:
        return None
    i = bisect_left(ts, wall)
    if i < len(ts) and ts[i] == wall:
        return ps[i]
    if i == 0:
        return None
    return ps[i - 1]


def process_map(row) -> list[dict]:
    match_dir = E / "data/trader" / row["match_id"]
    series = mid_series(match_dir)
    if series is None:
        return []
    ts, ps = series
    out = []
    events = pd.DataFrame(row["events"])
    for ev in events.itertuples():
        if ev.sb_occ is None or pd.isna(ev.sb_occ):
            continue
        E0 = float(ev.sb_occ)
        sign = 1.0 if ev.dead_side == "red" else -1.0
        p0 = mid_at(ts, ps, E0)
        p60 = mid_at(ts, ps, E0 + 60.0)
        if p0 is None or p60 is None:
            continue
        move60 = sign * (p60 - p0)
        p_at = {d: mid_at(ts, ps, E0 + d) for d in (3, 5, 8, 11, 20, 30)}
        # time to 50% of the 60s signed move
        t50 = None
        if move60 > 0.002:
            target = p0 + sign * move60 * 0.5
            lo, hi = E0, E0 + 60.0
            i = bisect_left(ts, E0)
            for j in range(i, len(ts)):
                if ts[j] > hi:
                    break
                if sign * (ps[j] - p0) >= move60 * 0.5:
                    t50 = ts[j] - E0
                    break
        out.append(
            {
                "match_id": row["match_id"],
                "counter": ev.counter,
                "dead_side": ev.dead_side,
                "ls_gt": ev.ls_gt,
                "E": E0,
                "sb_recv": ev.sb_recv,
                "tb_recv": ev.tb_recv,
                "p0": p0,
                "move60_signed": move60,
                "m3": sign * (p_at[3] - p0) if p_at[3] is not None else None,
                "m5": sign * (p_at[5] - p0) if p_at[5] is not None else None,
                "m8": sign * (p_at[8] - p0) if p_at[8] is not None else None,
                "m11": sign * (p_at[11] - p0) if p_at[11] is not None else None,
                "m20": sign * (p_at[20] - p0) if p_at[20] is not None else None,
                "m30": sign * (p_at[30] - p0) if p_at[30] is not None else None,
                "t50": t50,
            }
        )
    return out


def main() -> None:
    events = pd.read_parquet(OUT / "events.parquet")
    tapes = pd.read_csv(OUT / "tapes.csv")
    tapes = tapes[tapes.match_id.isin(events.match_id.unique())]
    grouped = {m: g.to_dict("records") for m, g in events.groupby("match_id")}
    tapes["events"] = tapes.match_id.map(grouped)
    all_rows: list[dict] = []
    done = 0
    with ProcessPoolExecutor(max_workers=8) as pool:
        for res in pool.map(process_map, tapes.to_dict("records"), chunksize=4):
            done += 1
            if done % 40 == 0:
                print(f"{done}/{len(tapes)}")
            all_rows.extend(res)
    df = pd.DataFrame(all_rows)
    df.to_parquet(OUT / "reactions.parquet")
    print("reaction rows:", len(df))
    if len(df):
        for col in ("m3", "m5", "m8", "m11", "m20", "m30", "move60_signed", "t50"):
            s = df[col].dropna()
            print(
                f"{col}: n={len(s)} p10={s.quantile(0.1):.3f} med={s.median():.3f} "
                f"mean={s.mean():.3f} p90={s.quantile(0.9):.3f}"
            )


if __name__ == "__main__":
    main()
