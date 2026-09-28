"""Live order latency per hop from core_trace, matched by core order_id.

Run from esports-trader root:
  uv run python ../betting_workspace/.analysis/dota-audit-2026-09-26/fixes/scripts/latency_hops.py

Hops (all core monotonic clock, one process, so no clock skew):
- place: plan.places[order_id] now_ns -> OrderAccepted(order_id) now_ns (POST round trip + dispatch)
- cancel: plan.cancels[order_id] now_ns -> CancelAck(order_id) now_ns (DELETE round trip + dispatch)
- book_in: BookUpdate now_ns - newest token ts_ns (WS local receipt -> core event)
- signal_in: SignalUpdate wall - newest feed frame received_at_utc before it (inference + queue; lower bound)
Venue check (host wall clock vs venue ts_utc, NTP skew included):
- fill_after_place: session.jsonl fill ts_utc - place wall; share under 1000 ms tests secondsDelay on makers.
"""

import gzip
import json
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path("data/trader")
DEPLOY_UTC = pd.Timestamp("2026-09-24T19:00:00Z")


def open_trace(archive: Path):
    gz = archive / "core_trace.jsonl.gz"
    if gz.is_file():
        return gzip.open(gz, "rt")
    plain = archive / "core_trace.jsonl"
    return plain.open() if plain.is_file() else None


def quantiles(values: list[float]) -> str:
    if not values:
        return "n=0"
    series = pd.Series(values)
    parts = [f"p{int(q * 100)}={series.quantile(q):.0f}" for q in (0.1, 0.5, 0.9, 0.99)]
    return f"n={len(values)} " + " ".join(parts) + f" mean={series.mean():.0f}"


def load_fill_wall_ns(archive: Path) -> dict[str, int]:
    path = archive / "session.jsonl"
    fills: dict[str, int] = {}
    if not path.is_file():
        return fills
    for line in path.open():
        row = json.loads(line)
        if row.get("kind") == "fill" and row.get("fill_key") and row.get("ts_utc"):
            fills[row["fill_key"]] = pd.Timestamp(row["ts_utc"]).value
    return fills


def load_frame_wall_ns(archive: Path) -> list[int]:
    frames: list[int] = []
    for name in ("grid_state.jsonl", "oddin_state.jsonl"):
        for path in (archive / f"{name}.gz", archive / name):
            if not path.is_file():
                continue
            opener = gzip.open(path, "rt") if path.suffix == ".gz" else path.open()
            for line in opener:
                start = line.find('"received_at_utc":"')
                if start < 0:
                    continue
                stamp = line[start + 19 : line.index('"', start + 19)]
                frames.append(pd.Timestamp(stamp).value)
    return sorted(frames)


def measure(archive: Path, out: dict[str, list[float]]) -> str | None:
    handle = open_trace(archive)
    if handle is None:
        return None
    match = json.loads((archive / "match.json").read_text())
    if match.get("game") != "dota":
        return None
    joined = pd.Timestamp(match["joined_at_utc"])
    epoch = "after_deploy" if joined >= DEPLOY_UTC else "before_deploy"
    feed = match.get("feed_source") or "unknown"
    fills_wall = load_fill_wall_ns(archive)
    frames_wall = load_frame_wall_ns(archive)
    offset_ns = None
    placed: dict[str, int] = {}
    cancel_req: dict[str, int] = {}
    live = False
    for line in handle:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "header":
            live = rec.get("execution_mode") == "live"
            if rec.get("opened_wall_s") is not None and rec.get("opened_now_ns") is not None:
                offset_ns = int(float(rec["opened_wall_s"]) * 1e9) - int(rec["opened_now_ns"])
            placed.clear()
            cancel_req.clear()
            continue
        if rec.get("kind") != "event" or not live:
            continue
        now_ns = rec["now_ns"]
        event = rec.get("event") or {}
        plan = rec.get("plan") or {}
        kind = event.get("type")
        order_id = event.get("order_id")
        if kind == "OrderAccepted" and order_id in placed:
            out[f"{epoch}/place"].append((now_ns - placed[order_id]) / 1e6)
            out[f"{epoch}/{feed}/place"].append((now_ns - placed[order_id]) / 1e6)
        elif kind == "CancelAck" and order_id in cancel_req:
            out[f"{epoch}/cancel"].append((now_ns - cancel_req.pop(order_id)) / 1e6)
        elif kind == "BookUpdate":
            tokens = (event.get("books") or {}).get("tokens") or []
            newest = max((token.get("ts_ns") or 0 for token in tokens), default=0)
            if newest:
                out[f"{epoch}/book_in"].append((now_ns - newest) / 1e6)
        elif kind == "SignalUpdate" and event.get("signal") and offset_ns is not None:
            signal_wall = now_ns + offset_ns
            index = bisect_right(frames_wall, signal_wall) - 1
            if index >= 0:
                out[f"{epoch}/{feed}/signal_in"].append((signal_wall - frames_wall[index]) / 1e6)
        elif kind == "Fill" and offset_ns is not None and order_id in placed:
            venue_ns = fills_wall.get(event.get("fill_id", ""))
            if venue_ns is not None:
                out[f"{epoch}/fill_after_place"].append(
                    (venue_ns - (placed[order_id] + offset_ns)) / 1e6
                )
        for place in plan.get("places") or []:
            placed.setdefault(place["order_id"], now_ns)
        for cancel in plan.get("cancels") or []:
            cancel_req.setdefault(cancel["order_id"], now_ns)
    return epoch


def main() -> None:
    out: dict[str, list[float]] = defaultdict(list)
    sessions: dict[str, int] = defaultdict(int)
    for archive in sorted(ROOT.iterdir()):
        if not (archive / "match.json").is_file():
            continue
        epoch = measure(archive, out)
        if epoch is not None:
            sessions[epoch] += 1
    print("sessions:", dict(sessions))
    for key in sorted(out):
        print(f"{key:36s} {quantiles(out[key])}")
    for epoch in ("before_deploy", "after_deploy"):
        fills = out.get(f"{epoch}/fill_after_place", [])
        if fills:
            bins = (0, 100, 200, 300, 500, 1000)
            counts = [sum(1 for value in fills if value < edge) for edge in bins]
            labels = " ".join(f"<{edge}ms={count}" for edge, count in zip(bins, counts))
            print(f"{epoch}: fills after place of {len(fills)}: {labels}")


if __name__ == "__main__":
    main()
