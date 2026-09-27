"""Live latency from core_trace archives.

- sig->place: SignalUpdate.received_ns -> now_ns of the trace record whose
  plan.places first becomes non-empty after that signal.
- place->accept: now_ns of placing record -> now_ns of next OrderAccepted event.
- place->cancel->accept for re-quotes.
- book->place: BookUpdate.now_ns -> placing now_ns (book-triggered re-quotes).
"""
import gzip
import json
import statistics
import sys
from pathlib import Path

ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader")


def quantiles(xs):
    xs = sorted(xs)
    if not xs:
        return "n=0"
    def q(p):
        return xs[min(len(xs) - 1, int(p * len(xs)))]
    return (
        f"n={len(xs)} p50={q(.5):.0f} p90={q(.9):.0f} "
        f"p99={q(.99):.0f} max={xs[-1]:.0f} mean={statistics.mean(xs):.0f} (ms)"
    )


def measure_one(path: Path):
    sig_to_place, book_to_place, place_to_accept, cancel_to_ack, fill_after_place = (
        [], [], [], [], [])
    pending_sig = None
    pending_book = None
    pending_places = 0
    pending_cancels = 0
    last_place_ns = None
    for line in gzip.open(path, "rt"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") != "event":
            continue
        now = rec.get("now_ns")
        ev = rec.get("event") or {}
        plan = rec.get("plan") or {}
        etype = ev.get("type", "")
        if etype == "SignalUpdate":
            inner = ev.get("signal") or ev
            pending_sig = inner.get("received_ns") or now
        elif etype == "BookUpdate":
            pending_book = now
        elif etype == "OrderAccepted":
            if pending_places > 0 and last_place_ns is not None:
                place_to_accept.append((now - last_place_ns) / 1e6)
                pending_places -= 1
        elif etype == "CancelAck":
            if pending_cancels > 0 and last_place_ns is not None:
                cancel_to_ack.append((now - last_place_ns) / 1e6)
                pending_cancels -= 1
        elif etype == "Fill":
            if last_place_ns is not None:
                fill_after_place.append((now - last_place_ns) / 1e6)
        places = plan.get("places") or []
        cancels = plan.get("cancels") or []
        if places:
            pending_places += len(places)
            last_place_ns = now
            if pending_sig is not None:
                sig_to_place.append((now - pending_sig) / 1e6)
                pending_sig = None
            if pending_book is not None:
                book_to_place.append((now - pending_book) / 1e6)
                pending_book = None
        if cancels:
            pending_cancels += len(cancels)
            last_place_ns = now
    return sig_to_place, book_to_place, place_to_accept, cancel_to_ack, fill_after_place


def main():
    dirs = sys.argv[1:]
    files = ([ROOT / d / "core_trace.jsonl.gz" for d in dirs]
             if dirs else sorted(ROOT.glob("*/core_trace.jsonl.gz"),
                                 key=lambda p: p.stat().st_mtime)[-8:])
    agg = [[] for _ in range(5)]
    for f in files:
        if not f.exists():
            continue
        r = measure_one(f)
        for a, x in zip(agg, r):
            a += x
        print(f"{f.parent.name}: places={len(r[2])} sig->place {quantiles(r[0])}")
    print("== aggregate ==")
    print("signal->place :", quantiles(agg[0]))
    print("book->place   :", quantiles(agg[1]))
    print("place->accept :", quantiles(agg[2]))
    print("cancel->ack   :", quantiles(agg[3]))
    print("place->fill   :", quantiles(agg[4]))


if __name__ == "__main__":
    main()
