"""Split live anchor-check failures into own-order strip gaps and real book moves.

Run from esports-trader root: python3 <this> data/trader/*/
Reads core_trace.jsonl(.gz) of live sessions only.

For each SignalUpdate with a signal, the next Wake rebuilds the latch:
  anchor = signal.anchor_p (mid read in match_worker at signal time)
  eval   = stripped book_p of the BookUpdate the Wake sees
  before = stripped book_p of the last BookUpdate at or before signal time
A failure is |eval - anchor| > 1c. Classes:
  own          eval ~ before (<=1c), before != anchor (>1c): raw vs stripped gap, book did not move
  clean_moved  before ~ anchor (<=0.1c), eval != before (>1c): real book move after the signal
  mixed        everything else
"""

import bisect
import gzip
import json
import sys
from pathlib import Path

TOL = 0.01 + 1e-12
SAME = 0.001 + 1e-12


def read_rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        for line in f:
            yield json.loads(line)


def compute_book_p(books, radiant_index, tolerance):
    by_index = {b["token_index"]: b for b in books["tokens"]}
    radiant = by_index[radiant_index]
    dire = by_index[1 - radiant_index]
    radiant_mid = (radiant["bid"] + radiant["ask"]) / 2
    dire_mid = (dire["bid"] + dire["ask"]) / 2
    pair_sum = radiant_mid + dire_mid
    if abs(pair_sum - 1) > tolerance:
        return None
    return radiant_mid / pair_sum


def split_session(path):
    counts = dict(signals=0, fails=0, own=0, clean_moved=0, mixed=0)
    book_ns = []
    book_values = []
    current = None
    pending = None
    for row in read_rows(path):
        if row["kind"] == "header":
            if row["execution_mode"] != "live":
                return None
            radiant_index = row["limits"]["radiant_token_index"]
            tolerance = row["limits"]["pair_sum_tolerance"]
            continue
        if row["kind"] != "event":
            continue
        event = row["event"]
        kind = event["type"]
        if kind == "BookUpdate":
            books = event["books"]
            current = None if books is None else compute_book_p(books, radiant_index, tolerance)
            book_ns.append(event["now_ns"])
            book_values.append(current)
        elif kind == "SignalUpdate" and event["signal"] is not None:
            pending = event["signal"]
        elif kind == "Wake" and pending is not None:
            signal = pending
            pending = None
            counts["signals"] += 1
            anchor = signal["anchor_p"]
            if current is None or abs(current - anchor) <= TOL:
                continue
            counts["fails"] += 1
            index = bisect.bisect_right(book_ns, signal["received_ns"]) - 1
            before = book_values[index] if index >= 0 else None
            if before is not None and abs(current - before) <= TOL and abs(before - anchor) > TOL:
                counts["own"] += 1
            elif before is not None and abs(before - anchor) <= SAME and abs(current - before) > TOL:
                counts["clean_moved"] += 1
            else:
                counts["mixed"] += 1
    return counts


def main():
    total = dict(sessions=0, signals=0, fails=0, own=0, clean_moved=0, mixed=0)
    for arg in sys.argv[1:]:
        path = next(Path(arg).glob("core_trace.jsonl*"), None)
        if path is None:
            continue
        counts = split_session(path)
        if counts is None or counts["signals"] == 0:
            continue
        total["sessions"] += 1
        for key, value in counts.items():
            total[key] += value
        if len(sys.argv) <= 4:
            print(Path(arg).name, counts)
    print("TOTAL", total)


if __name__ == "__main__":
    main()
