"""Apply the fill-snapshot size as a cancel and recompute kept PnL."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
LIVE = E / "data/backtests/dota_maker/validation_join_delta02_x015_cut480_p4_archive-s3-20260924"
CAT = E / "data/new_processed/match_catalog/match_catalog.parquet"
BOOK = E / "data/raw/telonex/polymarket/book_snapshot_full"
ROWS = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok/queue_fills.jsonl")
HALF = 0.005


def ymd(us: int) -> str:
    return datetime.fromtimestamp(us / 1e6, tz=timezone.utc).strftime("%Y-%m-%d")


def size_at(levels, price: float) -> float:
    total = 0.0
    for level in levels or []:
        px = float(level["price"])
        sz = float(level["size"])
        if sz > 0 and abs(px - price) < HALF:
            total += sz
    return total


def main() -> None:
    rows = [json.loads(line) for line in ROWS.open()]
    cat = pq.read_table(CAT, columns=["match_id", "token_id_0", "token_id_1"]).to_pydict()
    tokens = {}
    for mid, t0, t1 in zip(cat["match_id"], cat["token_id_0"], cat["token_id_1"]):
        tokens[int(mid)] = (str(t0), str(t1))

    by_key = defaultdict(list)
    for seed in (0, 1, 2):
        table = pq.read_table(
            LIVE / f"seed{seed}/fills.parquet",
            columns=["match_id", "token_index", "side", "price", "quantity", "queue_ahead", "level_index", "ts_ns", "markout_30s"],
        )
        cols = [table.column(name).to_pylist() for name in table.column_names]
        for mid, tok, side, px, qty, qa, lvl, ts, m30 in zip(*cols, strict=False):
            key = (
                seed, int(mid), int(tok), side, round(float(px), 4), round(float(qty), 4),
                round(float(qa), 4), int(lvl), None if m30 is None else round(float(m30), 6),
            )
            by_key[key].append(int(ts))

    need = []
    for i, row in enumerate(rows):
        if row["class"] in ("valid_empty", "valid_cleared"):
            continue
        key = (
            row["seed"], row["match"], row["token_index"], row["side"], round(row["price"], 4),
            round(row["qty"], 4), round(row["queue_ahead"], 4), row["level"],
            None if row["m30"] is None else round(row["m30"], 6),
        )
        token = tokens[row["match"]][row["token_index"]]
        for ts in by_key[key]:
            need.append((i, token, ts // 1000, row["side"], row["price"], row["ahead"]))

    groups = defaultdict(list)
    for item in need:
        groups[(item[1], ymd(item[2]))].append(item)
    print(f"lookups {len(need)} files {len(groups)}", flush=True)

    found = {}
    for n, ((token, day), items) in enumerate(groups.items()):
        path = BOOK / f"asset_id={token}" / f"{day}.parquet"
        wanted = {item[2] for item in items}
        if not path.exists():
            continue
        dataset = ds.dataset(path, format="parquet")
        filt = pc.field("timestamp_us").isin(list(wanted))
        for batch in dataset.scanner(columns=["timestamp_us", "bids", "asks"], filter=filt, batch_size=1024).to_batches():
            for ts, bids, asks in zip(
                batch.column("timestamp_us").to_pylist(),
                batch.column("bids").to_pylist(),
                batch.column("asks").to_pylist(),
            ):
                found[(token, int(ts))] = (bids, asks)
        if (n + 1) % 50 == 0:
            print(f"files {n + 1}/{len(groups)} snaps {len(found)}", flush=True)

    # Per jsonl row, any matched snapshot that drops our level to 0 counts.
    # Ambiguous keys: a row clears if every candidate snapshot clears, and we also count partials.
    by_row = defaultdict(list)
    for i, token, us, side, price, ahead in need:
        book = found.get((token, us))
        if book is None:
            by_row[i].append(None)
            continue
        bids, asks = book
        shown = size_at(bids if side == "BUY" else asks, price)
        other = size_at(asks if side == "BUY" else bids, price)
        by_row[i].append((shown, other, shown < ahead - 1e-6, shown <= 1e-6))

    cleared_rows = set()
    stats = defaultdict(lambda: {"n": 0, "miss": 0, "cleared": 0, "dropped_not_zero": 0, "still": 0, "locked": 0})
    for i, row in enumerate(rows):
        if row["class"] in ("valid_empty", "valid_cleared"):
            continue
        bucket = stats[row["class"]]
        bucket["n"] += 1
        snaps = by_row.get(i, [])
        if not snaps or any(snap is None for snap in snaps):
            bucket["miss"] += 1
            continue
        if all(snap[3] for snap in snaps):
            bucket["cleared"] += 1
            cleared_rows.add(i)
        elif any(snap[2] for snap in snaps):
            bucket["dropped_not_zero"] += 1
        else:
            bucket["still"] += 1
        if any(snap[1] > 0 and snap[0] > 0 for snap in snaps):
            bucket["locked"] += 1
    print("STATS", {k: dict(v) for k, v in stats.items()}, flush=True)

    def pnl(pred):
        inv = defaultdict(float)
        cash = defaultdict(float)
        mode = {}
        for row in rows:
            mode[(row["seed"], row["match"])] = row["mode"]
        for i, row in enumerate(rows):
            if not pred(i, row):
                continue
            key = (row["seed"], row["match"], row["token_index"])
            if row["side"] == "BUY":
                inv[key] += row["qty"]
                cash[key] -= row["price"] * row["qty"]
            else:
                take = min(row["qty"], inv[key])
                if take <= 0:
                    continue
                inv[key] -= take
                cash[key] += row["price"] * take
        out = defaultdict(float)
        bymode = defaultdict(float)
        for key, qty in inv.items():
            seed, match, tok = key
            settle = 0.0
            if qty > 1e-9:
                won = next(r["won"] for r in rows if r["seed"] == seed and r["match"] == match and r["token_index"] == tok)
                settle = qty * (1.0 if won else 0.0)
            out[seed] += cash[key] + settle
            bymode[(seed, mode[(seed, match)])] += cash[key] + settle
        print("SEED", {k: round(v, 2) for k, v in sorted(out.items())})
        print("MODE", {f"{a}|{b}": round(v, 2) for (a, b), v in sorted(bymode.items())})

    print("VARIANT snap-cleared added to valid")
    pnl(lambda i, r: r["class"] in ("valid_empty", "valid_cleared") or i in cleared_rows)
    print("VARIANT plus improve_touch")
    pnl(lambda i, r: r["class"] in ("valid_empty", "valid_cleared", "improve_touch") or i in cleared_rows)


if __name__ == "__main__":
    main()
