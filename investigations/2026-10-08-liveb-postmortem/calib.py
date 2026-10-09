#!/usr/bin/env python3
"""Order-level fill-model calibration on wallet B's real orders (2026-10-08).

Each real order (placement, price, size, cancel) is replayed against the
collector book and the on-chain fill tape under a Nautilus-like queue model:
queue ahead = displayed bid size at our price when we decided to place,
minus our own resting size; later bid-side executions at that price eat the
queue; excess fills us; a level that empties zeroes the queue (Nautilus
DELETE rule). Predicted fills are compared with what really happened.

Tape timing modes:
  block  on-chain block_timestamp_us as is (what the Nautilus backtest sees)
  shift  block time minus SHIFT_S seconds
  ws     WS last_trade_price time joined by tx hash; else shift

Usage: python3 -I calib.py [--maps a,b,...]
"""

import bisect
import glob
import json
import os
import sys
from collections import defaultdict

import pyarrow.parquet as pq

R = "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/investigations/2026-10-08-liveb-postmortem"
BOOKS = f"{R}/work/sim-devin/books"  # journal split per condition id
JOURNAL = f"{R}/work/data/trader_live_b/wallet/engine_journal/live.jsonl"
ONCHAIN = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket/onchain_fills"
OUR = "0xce44ec50818b97f0027cefccd33296161b33f6be"
DAY = "2026-10-08"
SHIFT_S = 2.5  # p50 of block time minus WS print time over 108,538 fills on 2026-10-08
MODES = ("block", "shift", "ws")
HORIZONS_S = (30, 120)

US = 1_000_000


def ticks(px):
    return int(round(float(px) * 100))


def load_maps(only=None):
    maps = []
    for mj in sorted(glob.glob(f"{R}/work/data/trader_live_b/*/match.json")):
        name = os.path.basename(os.path.dirname(mj))
        if only and name not in only:
            continue
        m = json.load(open(mj))
        toks = {}

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("yes_token_id", "no_token_id") and isinstance(v, str):
                        toks.setdefault(k, v)
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(m)
        cid = None

        def find_cid(o):
            nonlocal cid
            if isinstance(o, dict):
                for k, v in o.items():
                    if cid is None and k in ("condition_id", "conditionId") and isinstance(v, str):
                        cid = v
                    find_cid(v)
            elif isinstance(o, list):
                for v in o:
                    find_cid(v)

        find_cid(m)
        if cid is None or not os.path.isfile(f"{BOOKS}/{cid}.jsonl"):
            continue
        maps.append((name, cid, toks["yes_token_id"], toks["no_token_id"]))
    return maps


class Book:
    """Bid levels per token over time, plus the mid series."""

    def __init__(self, tokens):
        self.tok2i = {t: i for i, t in enumerate(tokens)}
        self.bids = [{} for _ in tokens]
        self.asks = [{} for _ in tokens]
        self.level_hist = defaultdict(list)  # (tok, px) -> [(t_us, size)]
        self.mid_t = [[] for _ in tokens]
        self.mid_v = [[] for _ in tokens]
        self.prints = {}  # tx -> t_us (earliest)
        self.t_end = 0

    def _note_mid(self, i, t):
        b = max(self.bids[i]) if self.bids[i] else None
        a = min(self.asks[i]) if self.asks[i] else None
        if b is None or a is None:
            return
        m = (b + a) / 200.0
        if self.mid_v[i] and self.mid_v[i][-1] == m:
            return
        self.mid_t[i].append(t)
        self.mid_v[i].append(m)

    def _set(self, i, side, px, sz, t):
        d = self.bids[i] if side == 0 else self.asks[i]
        if sz > 0:
            d[px] = sz
        else:
            d.pop(px, None)
        if side == 0:
            self.level_hist[(i, px)].append((t, sz))

    def load(self, path):
        with open(path) as f:
            for line in f:
                r = json.loads(line)
                p = r["payload"]
                t = r["receivedAtUs"]
                ty = p.get("type")
                if ty == "book":
                    i = self.tok2i.get(p["tokenId"])
                    if i is None:
                        continue
                    old_b = set(self.bids[i])
                    self.bids[i].clear()
                    self.asks[i].clear()
                    for x in p["bids"]:
                        self._set(i, 0, ticks(x["price"]), float(x["size"]), t)
                    for x in p["asks"]:
                        self._set(i, 1, ticks(x["price"]), float(x["size"]), t)
                    for px in old_b - set(self.bids[i]):
                        self.level_hist[(i, px)].append((t, 0.0))
                    self._note_mid(i, t)
                elif ty == "price_change":
                    for c in p["priceChanges"]:
                        i = self.tok2i.get(c["tokenId"])
                        if i is None:
                            continue
                        self._set(i, 0 if c["side"] == "BUY" else 1, ticks(c["price"]), float(c["size"]), t)
                        self._note_mid(i, t)
                elif ty == "last_trade_price":
                    tx = (p.get("transactionHash") or "").lower()
                    if tx:
                        ts = int(p["timestamp"]) * 1000
                        self.prints[tx] = min(ts, self.prints.get(tx, ts))
                self.t_end = max(self.t_end, t)

    def level_at(self, i, px, t):
        h = self.level_hist.get((i, px))
        if not h:
            return 0.0
        k = bisect.bisect_right(h, (t, float("inf"))) - 1
        return h[k][1] if k >= 0 else 0.0

    def level_changes(self, i, px, t0, t1):
        h = self.level_hist.get((i, px))
        if not h:
            return []
        a = bisect.bisect_right(h, (t0, float("inf")))
        b = bisect.bisect_right(h, (t1, float("inf")))
        return h[a:b]

    def mid_at(self, i, t):
        k = bisect.bisect_right(self.mid_t[i], t) - 1
        return self.mid_v[i][k] if k >= 0 else None


def load_orders(cid, tokens, book):
    """Real orders of this market: id -> dict."""
    tok2i = {t: i for i, t in enumerate(tokens)}
    orders = {}
    decisions = defaultdict(list)  # (tok, px, size) -> [t_us]
    by_tok = defaultdict(list)  # tok -> [(t_us, px)] in journal order
    with open(JOURNAL) as f:
        for line in f:
            r = json.loads(line)
            k = r["kind"]
            if k == "orders_out":
                for o in r["data"]:
                    i = tok2i.get(o["token_id"])
                    if i is not None and o["side"] == "BUY":
                        t_us = int(r["ts"] * US)
                        decisions[(i, ticks(o["price"]), round(float(o["size"]), 2))].append(t_us)
                        by_tok[i].append((t_us, ticks(o["price"])))
                continue
            d = r["data"]
            if isinstance(d, list):
                d = d[0] if d else {}
            if k == "user_order":
                if d.get("market") != cid or d.get("side") != "BUY":
                    continue
                i = tok2i.get(d["asset_id"])
                if i is None:
                    continue
                oid = d["id"]
                t = int(d["timestamp"]) * 1000
                ty = d["type"]
                o = orders.get(oid)
                if ty == "PLACEMENT":
                    if o is None:
                        orders[oid] = dict(
                            tok=i, px=ticks(d["price"]), size=float(d["original_size"]),
                            t_place=t, t_cancel=None, fills=[], matched=float(d["size_matched"]),
                        )
                elif o is not None:
                    o["matched"] = max(o["matched"], float(d["size_matched"]))
                    if ty == "CANCELLATION" or d.get("status") == "CANCELED":
                        o["t_cancel"] = t if o["t_cancel"] is None else min(o["t_cancel"], t)
            elif k == "user_trade":
                if d.get("market") != cid:
                    continue
                tx = (d.get("transaction_hash") or "").lower()
                for mo in d.get("maker_orders") or []:
                    if (mo.get("maker_address") or "").lower() != OUR:
                        continue
                    o = orders.get(mo["order_id"])
                    if o is None:
                        continue
                    key = (d["id"], mo["order_id"])
                    if key in o.setdefault("_seen", set()):
                        continue
                    o["_seen"].add(key)
                    t_fill = book.prints.get(tx) or int(float(d["match_time"]) * US)
                    o["fills"].append((t_fill, float(mo["matched_amount"]), tx))
    for o in orders.values():
        o["fills"].sort()
        o["real_qty"] = sum(q for _, q, _ in o["fills"])
        full = o["real_qty"] >= o["size"] - 0.01
        if o["t_cancel"] is not None:
            o["t_end"] = o["t_cancel"]
        elif full and o["fills"]:
            # The real order ended with its last fill. The counterfactual order
            # rests until the strategy next wants a different price on this token
            # (the next orders_out decision whose price differs), at most 60 s.
            t_f = o["fills"][-1][0]
            o["t_end"] = t_f + 60 * US
            for t, px in by_tok.get(o["tok"], ()):
                if t > t_f and px != o["px"]:
                    o["t_end"] = min(o["t_end"], t + 200_000)
                    break
        else:
            o["t_end"] = book.t_end
        # decision time: last orders_out with same (tok, px, size) within 3 s before placement
        cands = decisions.get((o["tok"], o["px"], round(o["size"], 2)), [])
        k = bisect.bisect_left(cands, o["t_place"]) - 1
        if k >= 0 and o["t_place"] - cands[k] <= 3 * US:
            o["t_dec"] = cands[k]
        else:
            o["t_dec"] = o["t_place"] - 200_000
    return orders


def load_tape(tokens, book):
    """Bid-side executions per (tok, px): [(t_block, amount, tx, ours)]."""
    tape = defaultdict(list)
    for i, tok in enumerate(tokens):
        path = f"{ONCHAIN}/asset_id={tok}/{DAY}.parquet"
        for r in pq.read_table(path, columns=["block_timestamp_us", "maker_asset_id", "asset_id", "mirrored", "maker_side", "price", "amount", "tx_hash", "maker"]).to_pylist():
            if r["mirrored"] or r["maker_side"] != "buy":
                continue
            tape[(i, ticks(r["price"]))].append((int(r["block_timestamp_us"]), float(r["amount"]), r["tx_hash"].lower(), r["maker"].lower() == OUR))
    return tape


def retime(rows, mode, book):
    out = []
    for t, amt, tx, ours in rows:
        if mode == "block":
            tt = t
        elif mode == "shift":
            tt = t - int(SHIFT_S * US)
        else:
            tt = book.prints.get(tx)
            if tt is None:
                tt = t - int(SHIFT_S * US)
        out.append((tt, amt, tx, ours))
    out.sort()
    return out


class OwnSize:
    """Our resting size per (tok, px) at time t, from the real order intervals."""

    def __init__(self, orders):
        self.iv = defaultdict(list)
        for oid, o in orders.items():
            self.iv[(o["tok"], o["px"])].append((o["t_place"], o["t_end"], o["size"], oid))

    def at(self, tok, px, t, exclude=None):
        s = 0.0
        for a, b, sz, oid in self.iv.get((tok, px), ()):
            if oid != exclude and a <= t < b:
                s += sz
        return s


def predict(o, tape_rows, book, own):
    """Nautilus-like queue replay of one order. Returns [(t, qty, tx)]."""
    tok, px = o["tok"], o["px"]
    q = max(0.0, book.level_at(tok, px, o["t_dec"]) - own.at(tok, px, o["t_dec"], exclude=None))
    rem = o["size"]
    fills = []
    last_t = o["t_place"]
    a = bisect.bisect_left(tape_rows, (o["t_place"], -1.0, "", False))
    for k in range(a, len(tape_rows)):
        t, amt, tx, ours = tape_rows[k]
        if t > o["t_end"]:
            break
        if q > 0:
            for ct, sz in book.level_changes(tok, px, last_t, t):
                if sz - own.at(tok, px, ct) <= 1e-9:
                    q = 0.0
                    break
        last_t = t
        q -= amt
        if q < 0:
            f = min(-q, rem)
            if f > 1e-9:
                fills.append((t, f, tx))
                rem -= f
            q = 0.0
        if rem <= 1e-9:
            break
    return fills


def markout(fills, book, tok, px, h_s):
    num = den = 0.0
    for t, qty, *_ in fills:
        m = book.mid_at(tok, t + h_s * US)
        if m is None:
            continue
        num += (m - px / 100.0) * qty
        den += qty
    return (100.0 * num / den) if den else float("nan"), den


def main():
    only = None
    if "--maps" in sys.argv:
        only = set(sys.argv[sys.argv.index("--maps") + 1].split(","))
    maps = load_maps(only)
    tot = {m: defaultdict(float) for m in MODES}
    real_tot = defaultdict(float)
    print(f"{'map':16s} {'orders':>6s} {'real_n':>6s} {'real_sh':>8s} | mode  {'pred_n':>6s} {'pred_sh':>8s} {'both':>5s} {'predonly':>8s} {'realonly':>8s} {'sametx':>6s} {'mo30r':>6s} {'mo30p':>6s} {'mo120r':>6s} {'mo120p':>6s}")
    for name, cid, yes, no in maps:
        tokens = (yes, no)
        book = Book(tokens)
        book.load(f"{BOOKS}/{cid}.jsonl")
        orders = load_orders(cid, tokens, book)
        tape = load_tape(tokens, book)
        own = OwnSize(orders)
        real_n = sum(1 for o in orders.values() if o["real_qty"] > 0)
        real_sh = sum(o["real_qty"] for o in orders.values())
        real_mo = {}
        for h in HORIZONS_S:
            num = den = 0.0
            for o in orders.values():
                v, d = markout(o["fills"], book, o["tok"], o["px"], h)
                if d:
                    num += v * d
                    den += d
            real_mo[h] = num / den if den else float("nan")
        real_tot["n"] += real_n
        real_tot["sh"] += real_sh
        for h in HORIZONS_S:
            real_tot[f"mo{h}"] += real_mo[h] * real_sh
        for mode in MODES:
            rt = {k: retime(v, mode, book) for k, v in tape.items()}
            pred_n = pred_sh = both = ponly = ronly = sametx = 0
            mo = {h: [0.0, 0.0] for h in HORIZONS_S}
            for o in orders.values():
                pf = predict(o, rt.get((o["tok"], o["px"]), []), book, own)
                pq_ = sum(q for _, q, _ in pf)
                if pq_ > 0:
                    pred_n += 1
                    pred_sh += pq_
                if pq_ > 0 and o["real_qty"] > 0:
                    both += 1
                    real_tx = {tx for _, _, tx in o["fills"]}
                    if any(tx in real_tx for _, _, tx in pf):
                        sametx += 1
                elif pq_ > 0:
                    ponly += 1
                elif o["real_qty"] > 0:
                    ronly += 1
                for h in HORIZONS_S:
                    v, d = markout(pf, book, o["tok"], o["px"], h)
                    if d:
                        mo[h][0] += v * d
                        mo[h][1] += d
            mo_v = {h: (mo[h][0] / mo[h][1] if mo[h][1] else float("nan")) for h in HORIZONS_S}
            T = tot[mode]
            T["pred_n"] += pred_n
            T["pred_sh"] += pred_sh
            T["both"] += both
            T["ponly"] += ponly
            T["ronly"] += ronly
            T["sametx"] += sametx
            for h in HORIZONS_S:
                T[f"mo{h}"] += mo[h][0]
                T[f"mo{h}d"] += mo[h][1]
            print(f"{name:16s} {len(orders):6d} {real_n:6d} {real_sh:8.0f} | {mode:5s} {pred_n:6d} {pred_sh:8.0f} {both:5d} {ponly:8d} {ronly:8d} {sametx:6d} {real_mo[30]:6.2f} {mo_v[30]:6.2f} {real_mo[120]:6.2f} {mo_v[120]:6.2f}")
    print("\nTOTAL (10 maps)")
    print(f"real: filled orders {real_tot['n']:.0f}, shares {real_tot['sh']:.0f}, markout30 {real_tot['mo30'] / real_tot['sh']:.2f} c/sh, markout120 {real_tot['mo120'] / real_tot['sh']:.2f} c/sh")
    for mode in MODES:
        T = tot[mode]
        print(f"{mode:5s}: filled orders {T['pred_n']:.0f}, shares {T['pred_sh']:.0f}, both {T['both']:.0f}, pred-only {T['ponly']:.0f}, real-only {T['ronly']:.0f}, same-tx {T['sametx']:.0f}, markout30 {T['mo30'] / T['mo30d']:.2f}, markout120 {T['mo120'] / T['mo120d']:.2f}")


if __name__ == "__main__":
    main()
