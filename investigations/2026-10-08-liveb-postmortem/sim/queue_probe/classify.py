"""Classify every simulated wallet B fill by the engine mechanism that produced it.

Inputs (work/queue-probe/): probe_{h3,957h6,h6}_{accepts,queue,fills}.jsonl from
run_probe.py, and work/sim-runs/diag_extra_bonly.csv (the 105 sim fills compared
with live). Writes fill_mechanisms.csv and diag_with_mechanism.csv next to the
probe files and prints the breakdown.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parents[2] / "work" / "queue-probe"
SEGMENTS = {"h3": "probe_h3", "957-h6": "probe_957h6", "h6": "probe_h6"}
# Heuristic: a print within 1 s of the DELETE belongs to the sweep that emptied the level.
SAME_SWEEP_MS = 1_000


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open()]


def hhmmss(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, UTC).strftime("%H:%M:%S.%f")[:12]


def mechanism(fill: dict, accept: dict | None, history: list[dict]) -> tuple[str, str]:
    """Return (mechanism, detail)."""
    if fill["trade_in_flight"] is None:
        return "book_cross", "no trade in flight: book update crossed the bid"
    start = history[0]["new"][1] if history and history[0]["old"] is None else None
    if accept is not None and accept["level_size"] > 0 and accept["engine_query"] == 0:
        return "price_raw_mismatch", f"level {accept['level_size']} at accept, engine saw 0"
    if accept is not None and accept["level_size"] == 0:
        return "alone_at_price", "no resting level at our price at accept"
    resets = [h for h in history if h["new"] is not None and h["old"] is not None
              and h["new"][1] == 0 and h["old"][1] > 0 and h["event"]["kind"] == "deltas"]
    if resets:
        last = resets[-1]
        gap_ms = (fill["ts"] - last["event"]["ts"]) / 1e6
        kind = "delete_then_sweep_print" if gap_ms <= SAME_SWEEP_MS else "delete_then_later_print"
        return kind, f"queue {last['old'][1]:.2f} -> 0 by DELETE, fill {gap_ms:.0f} ms later"
    if start is not None and start > 0:
        return "queue_drained_by_prints", f"start {start}"
    return "other", f"start {start}"


def classify_segment(seg: str, prefix: str) -> list[dict]:
    accepts = {row["cid"]: row for row in read(HERE / f"{prefix}_accepts.jsonl")}
    history: dict[str, list[dict]] = defaultdict(list)
    for row in read(HERE / f"{prefix}_queue.jsonl"):
        history[row["cid"]].append(row)
    out = []
    for fill in read(HERE / f"{prefix}_fills.jsonl"):
        cid = fill["cid"]
        kind, detail = mechanism(fill, accepts.get(cid), history[cid])
        accept = accepts.get(cid) or {}
        out.append({
            "seg": seg, "cid": cid, "match_id": cid.rsplit("-", 2)[-2], "px": fill["order_px"],
            "qty": round(fill["last_qty"], 2), "submit": hhmmss(fill["order_init_ts"]),
            "fill_s": round((fill["ts"] - fill["order_init_ts"]) / 1e9, 2),
            "fill_ts": hhmmss(fill["ts"]), "mechanism": kind, "detail": detail,
            "level_at_accept": accept.get("level_size"), "engine_queue_at_accept": accept.get("engine_query"),
            "trigger": fill["trigger"]["kind"],
            "trigger_px": fill["trigger"].get("px"), "trigger_size": fill["trigger"].get("size"),
        })
    return out


def sweep_check(prefix: str) -> list[dict]:
    """For DELETE-then-print fills: would an honest queue (no reset) still fill?

    Sweep = SELLER prints from 200 ms before the DELETE to 1 s after. A print
    below our price means the whole level went, so the fill stands. Otherwise
    the honest fill is the sweep's prints at our price minus the queue left.
    """
    trades: dict[str, list[dict]] = defaultdict(list)
    for row in read(HERE / f"{prefix}_trades.jsonl"):
        trades[row["iid"]].append(row)
    history: dict[str, list[dict]] = defaultdict(list)
    for row in read(HERE / f"{prefix}_queue.jsonl"):
        history[row["cid"]].append(row)
    fills: dict[str, list[dict]] = defaultdict(list)
    for row in read(HERE / f"{prefix}_fills.jsonl"):
        fills[row["cid"]].append(row)
    out = []
    for cid, order_fills in fills.items():
        first = order_fills[0]
        resets = [h for h in history[cid] if h["old"] and h["new"] and h["new"][1] == 0
                  and h["old"][1] > 0 and h["event"]["kind"] == "deltas"]
        if first["trade_in_flight"] is None or not resets:
            continue
        t0, queue_left, px = resets[-1]["event"]["ts"], resets[-1]["old"][1], first["order_px"]
        if (first["ts"] - t0) / 1e6 > SAME_SWEEP_MS:
            continue
        sweep = [t for t in trades[first["iid"]]
                 if t0 - 200e6 <= t["ts"] <= t0 + SAME_SWEEP_MS * 1e6 and t["aggressor"] == 2]
        at_px = sum(t["size"] for t in sweep if abs(t["px"] - px) < 1e-9)
        through = any(t["px"] < px - 1e-9 for t in sweep)
        sim = sum(f["last_qty"] for f in order_fills if f["ts"] >= t0)
        honest = sim if through else min(sim, max(0.0, at_px - queue_left))
        out.append({"cid": cid, "through": through, "sim": sim, "honest": honest})
    return out


def main() -> None:
    fills = pd.DataFrame([row for seg, prefix in SEGMENTS.items() for row in classify_segment(seg, prefix)])
    fills.to_csv(HERE / "fill_mechanisms.csv", index=False)
    print("all sim BUY fills in the three runs:", len(fills))
    print(fills.mechanism.value_counts().to_string(), "\n")
    diag = pd.read_csv(HERE.parent / "sim-runs" / "diag_extra_bonly.csv")
    diag["match_id"] = diag.match_id.astype(str)
    diag["qty"] = diag.qty.round(2)
    # One order can fill twice with the same size in the same ms: number repeats on both sides.
    left_key = ["seg", "match_id", "px", "qty", "sim_submit", "sim_fill_s"]
    right_key = ["seg", "match_id", "px", "qty", "submit", "fill_s"]
    diag["occ"] = diag.groupby(left_key).cumcount()
    fills["occ"] = fills.groupby(right_key).cumcount()
    joined = diag.merge(fills, left_on=[*left_key, "occ"], right_on=[*right_key, "occ"], how="left")
    print("diag rows", len(diag), "joined", joined.mechanism.notna().sum())
    joined["queue_through"] = joined.prints_at_px_sim < joined.sim_qa
    print(pd.crosstab(joined.status, joined.mechanism, margins=True).to_string(), "\n")
    print("diag rows with prints at our price < strategy queue_ahead (the '17 queue-through'):")
    print(joined[joined.queue_through].mechanism.value_counts().to_string())
    joined.to_csv(HERE / "diag_with_mechanism.csv", index=False)
    sweep = pd.DataFrame([row for prefix in SEGMENTS.values() for row in sweep_check(prefix)])
    print(f"\nDELETE-then-sweep orders: {len(sweep)}, sweep went below our price: {sweep.through.sum()}")
    print(f"shares: sim {sweep.sim.sum():.1f}, honest queue {sweep.honest.sum():.1f}")


if __name__ == "__main__":
    main()
