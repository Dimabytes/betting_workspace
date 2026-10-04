"""Measure live frame->order-submit latency for the board-lead reaction constant.

For every September 2026 trader archive with a core_trace.jsonl(.gz):
for each SignalUpdate carrying a real signal, T0 = signal.received_ns
(the feed frame receipt). The first later event whose plan places orders
gives t_place; the OrderAccepted acks of that plan's order_ids give t_ack.
Latency samples are t_place - T0 (decision->wire) and t_ack - T0
(decision->resting on book). Read-only; prints quantiles per game.

    uv run python measure_reaction.py [trader_dir]
"""

import gzip
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

TRADER_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader"
)
SEP_START = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
SEP_END = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
MAX_GAP_S = 60.0


def trace_path(archive: Path) -> Path | None:
    for name in ("core_trace.jsonl", "core_trace.jsonl.gz"):
        path = archive / name
        if path.is_file():
            return path
    return None


def measure_archive(path: Path) -> tuple[str, list[tuple[float, float]]]:
    """(game, [(signal->place s, signal->ack s | nan)]) for one archive."""
    opener = gzip.open if path.suffix == ".gz" else open
    game = "?"
    pending_signal_ns: int | None = None
    # order_id -> (place_gap_ns, signal_received_ns)
    awaiting: dict[str, tuple[int, int]] = {}
    samples: list[tuple[float, float]] = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") == "header":
                game = row.get("game", "?")
                continue
            if row.get("kind") != "event":
                continue
            event = row["event"]
            now_ns = int(row["now_ns"])
            if event.get("type") == "OrderAccepted":
                order_id = event.get("order_id")
                if order_id in awaiting:
                    place_gap_ns, t0_ns = awaiting.pop(order_id)
                    samples.append((place_gap_ns / 1e9, (now_ns - t0_ns) / 1e9))
                continue
            if event.get("type") == "SignalUpdate":
                signal = event.get("signal")
                pending_signal_ns = int(signal["received_ns"]) if signal else None
            if pending_signal_ns is None:
                continue
            gap_s = (now_ns - pending_signal_ns) / 1e9
            if gap_s > MAX_GAP_S:
                pending_signal_ns = None
                continue
            places = row["plan"].get("places") or []
            if not places:
                continue
            place_gap_ns = now_ns - pending_signal_ns
            for place in places:
                awaiting[place["order_id"]] = (place_gap_ns, pending_signal_ns)
            pending_signal_ns = None
    for place_gap_ns, _t0_ns in awaiting.values():
        samples.append((place_gap_ns / 1e9, float("nan")))
    return game, samples


def main() -> None:
    per_game: dict[str, list[tuple[float, float]]] = defaultdict(list)
    scanned = 0
    for archive in sorted(TRADER_DIR.iterdir()):
        session = archive / "session.jsonl"
        if not session.is_file():
            continue
        if not (SEP_START <= session.stat().st_mtime < SEP_END):
            continue
        path = trace_path(archive)
        if path is None:
            continue
        scanned += 1
        game, rows = measure_archive(path)
        per_game[game].extend(rows)
    print(f"archives scanned: {scanned}")
    qs = [50, 75, 90, 95, 99]
    for game, rows in sorted(per_game.items()):
        place = np.asarray([r[0] for r in rows], dtype=np.float64)
        ack = np.asarray([r[1] for r in rows if r[1] == r[1]], dtype=np.float64)
        print(
            f"{game} signal->place: n={len(place)} "
            + " ".join(f"p{q}={v:.3f}s" for q, v in zip(qs, np.percentile(place, qs)))
        )
        if ack.size:
            print(
                f"{game} signal->ack:   n={len(ack)} "
                + " ".join(f"p{q}={v:.3f}s" for q, v in zip(qs, np.percentile(ack, qs)))
            )


if __name__ == "__main__":
    main()
