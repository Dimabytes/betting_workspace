"""Honest-queue replay of LIVE seeds 0-2. Reads books and trade tapes; writes jsonl."""

import json
import sys
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
ONCHAIN = E / "data/raw/telonex/polymarket/onchain_fills"
TRADES = E / "data/raw/telonex/polymarket/trades"
OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok")
HALF = 0.005
KEEP_VALID = {"valid_empty", "valid_cleared"}
KEEP_PLUS = KEEP_VALID | {"improve_touch"}


def log(msg: str) -> None:
    print(msg, flush=True)


def us_of(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1_000_000)


def ymd(us: int) -> str:
    return datetime.fromtimestamp(us / 1e6, tz=timezone.utc).strftime("%Y-%m-%d")


def days_of(t0: int, t1: int) -> list[str]:
    start = datetime.fromtimestamp(t0 / 1e6, tz=timezone.utc).date()
    end = datetime.fromtimestamp(t1 / 1e6, tz=timezone.utc).date()
    out = []
    cur = start
    while cur <= end:
        out.append(cur.isoformat())
        cur = cur.fromordinal(cur.toordinal() + 1)
    return out


def era_of(us: int) -> str:
    day = ymd(us)
    if day <= "2026-08-07":
        return "telonex"
    if day >= "2026-08-09":
        return "collector"
    return "gap"


def cent(price: float) -> float:
    return round(price, 2)


class Hist:
    def __init__(self) -> None:
        self.buckets = [0] * 10001
        self.n = 0
        self.sum_s = 0.0
        self.zeros = 0

    def add_gap(self, gap_us: int) -> None:
        if gap_us <= 0:
            self.zeros += 1
            return
        self.buckets[min(gap_us // 10_000, 10000)] += 1
        self.n += 1
        self.sum_s += gap_us / 1e6

    def quantile_s(self, q: float) -> float | None:
        if not self.n:
            return None
        need = max(1, int(q * self.n + 0.999))
        acc = 0
        for i, count in enumerate(self.buckets):
            acc += count
            if acc >= need:
                return 100.0 if i == 10000 else (i + 0.5) * 0.01
        return None

    def as_dict(self) -> dict:
        return {
            "gaps": self.n,
            "zero_gaps": self.zeros,
            "median_s": self.quantile_s(0.5),
            "p10_s": self.quantile_s(0.1),
            "p90_s": self.quantile_s(0.9),
            "mean_s": (self.sum_s / self.n) if self.n else None,
        }


def touch_of(levels: list | None, prices: set[float], *, best_is_max: bool) -> tuple[float | None, dict[float, float]]:
    best = None
    found = {p: 0.0 for p in prices}
    for level in levels or []:
        px = float(level["price"])
        sz = float(level["size"])
        if sz <= 0 or px != px:
            continue
        if best is None or (px > best if best_is_max else px < best):
            best = px
        key = cent(px)
        if key in found:
            found[key] += sz
    return best, found


def load_catalog() -> dict[int, dict]:
    table = pq.read_table(
        CAT,
        columns=["match_id", "token_id_0", "token_id_1", "radiant_win", "radiant_token_index"],
    )
    out = {}
    for mid, t0, t1, win, idx in zip(
        table.column("match_id").to_pylist(),
        table.column("token_id_0").to_pylist(),
        table.column("token_id_1").to_pylist(),
        table.column("radiant_win").to_pylist(),
        table.column("radiant_token_index").to_pylist(),
        strict=False,
    ):
        out[int(mid)] = {
            "tokens": (str(t0), str(t1)),
            "radiant_win": bool(win),
            "radiant_token_index": int(idx),
        }
    return out


def load_seed(seed: int, catalog: dict[int, dict]) -> tuple[dict, list[dict], dict]:
    root = LIVE / f"seed{seed}"
    results = pq.read_table(
        root / "results.parquet",
        columns=["match_id", "horn_at", "game_ended_at", "engine_pnl", "cash_flow", "signal_mode", "live_order_seconds"],
    )
    matches = {}
    for mid, horn, ended, pnl, cash, mode, live_s in zip(
        results.column("match_id").to_pylist(),
        results.column("horn_at").to_pylist(),
        results.column("game_ended_at").to_pylist(),
        results.column("engine_pnl").to_pylist(),
        results.column("cash_flow").to_pylist(),
        results.column("signal_mode").to_pylist(),
        results.column("live_order_seconds").to_pylist(),
        strict=False,
    ):
        mid = int(mid)
        info = catalog[mid]
        matches[mid] = {
            "horn_us": us_of(horn),
            "end_us": us_of(ended),
            "engine_pnl": float(pnl),
            "cash_flow": float(cash),
            "mode": mode,
            "live_s": int(live_s),
            "tokens": info["tokens"],
            "radiant_win": info["radiant_win"],
            "radiant_token_index": info["radiant_token_index"],
            "era": era_of(us_of(horn)),
        }
    quotes = pq.read_table(
        root / "quote_events.parquet",
        columns=["order_id", "kind", "ts_ns", "price", "level_index"],
        filters=[("kind", "in", ["submitted", "accepted", "cancel_ack"])],
    )
    submit_ts, submit_px, accept_ts, ack_ts, level = {}, {}, {}, {}, {}
    for oid, kind, ts, px, lvl in zip(
        quotes.column("order_id").to_pylist(),
        quotes.column("kind").to_pylist(),
        quotes.column("ts_ns").to_pylist(),
        quotes.column("price").to_pylist(),
        quotes.column("level_index").to_pylist(),
        strict=False,
    ):
        if not oid:
            continue
        if kind == "submitted":
            submit_ts[oid] = int(ts)
            submit_px[oid] = float(px)
            level[oid] = int(lvl)
        elif kind == "accepted":
            accept_ts[oid] = int(ts)
        elif kind == "cancel_ack":
            ack_ts[oid] = int(ts)
    fills_table = pq.read_table(
        root / "fills.parquet",
        columns=[
            "match_id", "token_index", "side", "price", "quantity", "ts_ns", "queue_ahead",
            "order_id", "level_index", "maker_rebate", "markout_30s", "markout_300s",
        ],
    )
    fills = []
    for row in zip(
        fills_table.column("match_id").to_pylist(),
        fills_table.column("token_index").to_pylist(),
        fills_table.column("side").to_pylist(),
        fills_table.column("price").to_pylist(),
        fills_table.column("quantity").to_pylist(),
        fills_table.column("ts_ns").to_pylist(),
        fills_table.column("queue_ahead").to_pylist(),
        fills_table.column("order_id").to_pylist(),
        fills_table.column("level_index").to_pylist(),
        fills_table.column("maker_rebate").to_pylist(),
        fills_table.column("markout_30s").to_pylist(),
        fills_table.column("markout_300s").to_pylist(),
        strict=False,
    ):
        mid, tok, side, px, qty, ts, ahead, oid, lvl, rebate, m30, m300 = row
        mid = int(mid)
        match = matches[mid]
        token = match["tokens"][int(tok)]
        won = (int(tok) == match["radiant_token_index"]) == match["radiant_win"]
        fills.append({
            "seed": seed,
            "match": mid,
            "token_index": int(tok),
            "token": token,
            "side": side,
            "price": float(px),
            "qty": float(qty),
            "ts_ns": int(ts),
            "queue_ahead": float(ahead),
            "order_id": oid,
            "level": int(lvl),
            "rebate": float(rebate or 0.0),
            "m30": None if m30 is None else float(m30),
            "m300": None if m300 is None else float(m300),
            "submit_ns": submit_ts.get(oid),
            "submit_px": submit_px.get(oid),
            "accept_ns": accept_ts.get(oid),
            "mode": match["mode"],
            "era": match["era"],
            "won": won,
        })
    quoted = defaultdict(float)
    for oid, ts in accept_ts.items():
        end = ack_ts.get(oid)
        if end is None:
            continue
        quoted[level.get(oid, -1)] += max(0, end - ts) / 1e9
    meta = {
        "engine": sum(m["engine_pnl"] for m in matches.values()),
        "live_s": sum(m["live_s"] for m in matches.values()),
        "quoted_s_by_level": {str(k): v for k, v in sorted(quoted.items())},
        "n_accept_without_ack": sum(1 for oid in accept_ts if oid not in ack_ts),
    }
    return matches, fills, meta


def iter_book(token: str, t0: int, t1: int, want_levels: bool):
    cols = ["timestamp_us", "bids", "asks"] if want_levels else ["timestamp_us"]
    filt = (pc.field("timestamp_us") >= t0) & (pc.field("timestamp_us") <= t1)
    for day in days_of(t0, t1):
        path = BOOK / f"asset_id={token}" / f"{day}.parquet"
        if not path.exists():
            continue
        dataset = ds.dataset(path, format="parquet")
        for batch in dataset.scanner(columns=cols, filter=filt, batch_size=4096).to_batches():
            ts = batch.column("timestamp_us").to_pylist()
            if not want_levels:
                for t in ts:
                    yield t, None, None
                continue
            bids = batch.column("bids").to_pylist()
            asks = batch.column("asks").to_pylist()
            for i, t in enumerate(ts):
                yield int(t), bids[i], asks[i]


def load_trades(token: str, t0_us: int, t1_us: int) -> tuple[list[tuple], str]:
    """(ts_ns, price, size, hits_bid, hits_ask) and the tape name."""
    onchain = _onchain_rows(token, t0_us, t1_us)
    if onchain:
        return onchain, "onchain"
    public = _trade_rows(token, t0_us, t1_us)
    if public:
        return public, "trades"
    return [], "none"


def _rows(path: Path, t0_us: int, t1_us: int, columns: list[str]):
    if not path.exists():
        return []
    table = pq.read_table(
        path,
        columns=columns,
        filters=[("timestamp_us" if "timestamp_us" in columns else "block_timestamp_us", ">=", t0_us),
                  ("timestamp_us" if "timestamp_us" in columns else "block_timestamp_us", "<=", t1_us)],
    )
    return table


def _side_flags(side: str) -> tuple[bool, bool]:
    """(hits resting bids, hits resting asks) for an aggressor side on this file's book."""
    return side == "sell", side == "buy"


def _onchain_rows(token: str, t0_us: int, t1_us: int) -> list[tuple]:
    """Trades as (ts_ns, price, size, true_hit_bid, true_hit_ask, verb_hit_bid, verb_hit_ask).

    True aggressor is taker_side when taker_asset_id == this file's asset_id, else the flip.
    The engine reads taker_side verbatim and never looks at taker_asset_id.
    """
    rows = []
    seen = set()
    cols = [
        "block_timestamp_us", "tx_hash", "log_index", "price", "amount",
        "taker_side", "taker_asset_id", "asset_id",
    ]
    for day in days_of(t0_us, t1_us):
        path = ONCHAIN / f"asset_id={token}" / f"{day}.parquet"
        if not path.exists():
            continue
        table = pq.read_table(
            path,
            columns=cols,
            filters=[("block_timestamp_us", ">=", t0_us), ("block_timestamp_us", "<=", t1_us)],
        )
        for ts, tx, log_index, price, amount, taker, taker_asset, asset in zip(
            table.column("block_timestamp_us").to_pylist(),
            table.column("tx_hash").to_pylist(),
            table.column("log_index").to_pylist(),
            table.column("price").to_pylist(),
            table.column("amount").to_pylist(),
            table.column("taker_side").to_pylist(),
            table.column("taker_asset_id").to_pylist(),
            table.column("asset_id").to_pylist(),
            strict=False,
        ):
            key = (tx, log_index)
            if key in seen:
                continue
            seen.add(key)
            raw = (taker or "").lower()
            if raw not in ("buy", "sell"):
                continue
            true = raw if str(taker_asset) == str(asset) else ("sell" if raw == "buy" else "buy")
            true_bid, true_ask = _side_flags(true)
            verb_bid, verb_ask = _side_flags(raw)
            rows.append((int(ts) * 1000, float(price), float(amount), true_bid, true_ask, verb_bid, verb_ask))
    rows.sort()
    return rows


def _trade_rows(token: str, t0_us: int, t1_us: int) -> list[tuple]:
    rows = []
    for day in days_of(t0_us, t1_us):
        path = TRADES / f"asset_id={token}" / f"{day}.parquet"
        if not path.exists():
            continue
        table = pq.read_table(
            path,
            columns=["timestamp_us", "price", "size", "side"],
            filters=[("timestamp_us", ">=", t0_us), ("timestamp_us", "<=", t1_us)],
        )
        for ts, price, size, side in zip(
            table.column("timestamp_us").to_pylist(),
            table.column("price").to_pylist(),
            table.column("size").to_pylist(),
            table.column("side").to_pylist(),
            strict=False,
        ):
            label = (side or "").lower()
            if label not in ("buy", "sell"):
                continue
            hit_bid, hit_ask = _side_flags(label)
            rows.append((int(ts) * 1000, float(price), float(size), hit_bid, hit_ask, hit_bid, hit_ask))
    rows.sort()
    return rows


def last_state(states: list[dict], ts_ns: int) -> dict | None:
    lo, hi = 0, len(states)
    while lo < hi:
        mid = (lo + hi) // 2
        if states[mid]["ts"] <= ts_ns:
            lo = mid + 1
        else:
            hi = mid
    return states[lo - 1] if lo else None


def classify_fill(fill: dict, states: list[dict], trades: list[tuple], ts_us: list[int], ts_set: set[int]) -> dict:
    accept = fill["accept_ns"]
    submit = fill["submit_ns"]
    fill_ns = fill["ts_ns"]
    price = cent(fill["price"])
    side = fill["side"]
    book = "bid" if side == "BUY" else "ask"
    submit_px = fill["submit_px"]
    repriced = submit_px is None or abs(submit_px - fill["price"]) >= HALF
    at_accept = last_state(states, accept)
    if repriced and at_accept is not None:
        ahead0 = at_accept[book].get(price, 0.0)
        ahead_src = "book_at_accept"
    else:
        ahead0 = fill["queue_ahead"]
        ahead_src = "submit_latched"
    improving = False
    if at_accept is not None:
        if side == "BUY" and at_accept["best_bid"] is not None:
            improving = fill["price"] > at_accept["best_bid"] + HALF
        elif side == "SELL" and at_accept["best_ask"] is not None:
            improving = fill["price"] < at_accept["best_ask"] - HALF
    ahead = 0.0 if ahead0 != ahead0 else ahead0
    trade_at_fill = False
    trade_vol = 0.0
    missed_vol = 0.0
    phantom_vol = 0.0
    missed_n = 0
    phantom_n = 0
    events: list[tuple] = []
    for ts, px, sz, hit_bid, hit_ask, verb_bid, verb_ask in trades:
        if ts <= accept or ts > fill_ns or abs(px - price) >= HALF:
            continue
        hits = hit_bid if side == "BUY" else hit_ask
        verb = verb_bid if side == "BUY" else verb_ask
        if hits and not verb:
            missed_vol += sz
            missed_n += 1
        elif verb and not hits:
            phantom_vol += sz
            phantom_n += 1
        if hits:
            events.append((ts, 0, sz))
    for state in states:
        if accept < state["ts"] < fill_ns:
            events.append((state["ts"], 1, state[book].get(price, 0.0)))
    for ts, kind, val in sorted(events):
        if kind == 0:
            ahead = max(0.0, ahead - val)
            trade_vol += val
            if ts // 1000 == fill_ns // 1000:
                trade_at_fill = True
        elif val < ahead:
            ahead = val
    fill_us = fill_ns // 1000
    book_timed = fill_us in ts_set
    snap = last_state(states, fill_ns)
    touch = False
    if snap is not None and abs(snap["ts"] // 1000 - fill_us) <= 1:
        if side == "BUY" and snap["best_ask"] is not None and snap["best_ask"] <= fill["price"] + HALF:
            touch = True
        elif side == "SELL" and snap["best_bid"] is not None and snap["best_bid"] >= fill["price"] - HALF:
            touch = True
    if ahead0 != ahead0:
        klass = "unknown_depth"
    elif ahead <= 1e-6:
        klass = "valid_empty" if ahead0 <= 1e-6 else "valid_cleared"
    elif trade_at_fill:
        klass = "early"
    elif touch:
        klass = "improve_touch" if improving else "touch_only"
    else:
        klass = "unexplained"
    clear_submit = _lag(ts_us, (submit // 1000) if submit else None)
    clear_accept = _lag(ts_us, accept // 1000)
    return {
        "class": klass,
        "ahead0": ahead0,
        "ahead": ahead,
        "ahead_src": ahead_src,
        "improving": improving,
        "repriced": repriced,
        "book_timed": book_timed,
        "touch": touch,
        "trade_at_fill": trade_at_fill,
        "trade_vol": trade_vol,
        "missed_n": missed_n,
        "missed_vol": missed_vol,
        "phantom_n": phantom_n,
        "phantom_vol": phantom_vol,
        "clear_submit_s": clear_submit,
        "clear_accept_s": clear_accept,
    }


def _as_set(ts_us: list[int]) -> set[int]:
    return set(ts_us)


def _lag(ts_us: list[int], after_us: int | None) -> float | None:
    if after_us is None:
        return None
    lo, hi = 0, len(ts_us)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts_us[mid] <= after_us:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(ts_us):
        return None
    return (ts_us[lo] - after_us) / 1e6


def scan_token(token: str, window: tuple[int, int], prices: set[float], hist: Hist) -> tuple[list[int], list[dict]]:
    t0, t1 = window
    ts_us: list[int] = []
    states: list[dict] = []
    prev = None
    for ts, bids, asks in iter_book(token, t0, t1, bool(prices)):
        ts_us.append(ts)
        if not prices:
            continue
        bid, bid_sz = touch_of(bids, prices, best_is_max=True)
        ask, ask_sz = touch_of(asks, prices, best_is_max=False)
        cur = (bid, ask, tuple(sorted(bid_sz.items())), tuple(sorted(ask_sz.items())))
        if cur == prev:
            continue
        prev = cur
        states.append({"ts": ts * 1000, "bid": bid, "ask": ask, "bid_sz": bid_sz, "ask_sz": ask_sz})
    ts_us.sort()
    states.sort(key=lambda s: s["ts"])
    if ts_us:
        last = ts_us[0]
        for ts in ts_us[1:]:
            hist.add_gap(ts - last)
            last = ts
    return ts_us, states


def money(rows: list[dict], keep: set[str]) -> dict:
    inv = defaultdict(float)
    cash = defaultdict(float)
    rebate = defaultdict(float)
    for row in rows:
        if row["class"] not in keep:
            continue
        key = (row["seed"], row["mode"], row["match"], row["token_index"])
        qty = row["qty"]
        if row["side"] == "BUY":
            inv[key] += qty
            cash[key] -= row["price"] * qty
            rebate[key] += row["rebate"]
        else:
            take = min(qty, inv[key])
            if take <= 0:
                continue
            inv[key] -= take
            cash[key] += row["price"] * take
            rebate[key] += row["rebate"] * (take / qty)
    out = defaultdict(lambda: {"cash": 0.0, "settle": 0.0, "rebate": 0.0})
    seen = set()
    for key, qty in inv.items():
        seed, mode, match, _tok = key
        bucket = out[(seed, mode)]
        bucket["cash"] += cash[key]
        bucket["rebate"] += rebate[key]
        if qty > 1e-9:
            won = next(r["won"] for r in rows if r["seed"] == seed and r["match"] == match and r["token_index"] == key[3])
            bucket["settle"] += qty * (1.0 if won else 0.0)
        seen.add((seed, mode))
    for key in cash:
        seed, mode = key[0], key[1]
        if (seed, mode) not in out or out[(seed, mode)]["cash"] == 0 and cash[key]:
            out[(seed, mode)]["cash"] += 0.0
    # cash for keys whose inventory hit exactly 0 still needs adding: handled because inv key exists
    return {f"{seed}|{mode}": {k: round(v, 4) for k, v in bucket.items()} | {"pnl": round(bucket["cash"] + bucket["settle"], 4)} for (seed, mode), bucket in sorted(out.items())}


def _mis_side(rows: list[dict]) -> dict:
    out = {"fills": len(rows), "missed_fills": 0, "phantom_fills": 0, "both_fills": 0, "either_fills": 0,
           "missed_prints": 0, "phantom_prints": 0, "missed_vol": 0.0, "phantom_vol": 0.0}
    by_seed = defaultdict(lambda: {"missed_fills": 0, "phantom_fills": 0, "both_fills": 0, "either_fills": 0})
    for row in rows:
        missed = row["missed_n"] > 0
        phantom = row["phantom_n"] > 0
        out["missed_prints"] += row["missed_n"]
        out["phantom_prints"] += row["phantom_n"]
        out["missed_vol"] += row["missed_vol"]
        out["phantom_vol"] += row["phantom_vol"]
        if missed:
            out["missed_fills"] += 1
            by_seed[row["seed"]]["missed_fills"] += 1
        if phantom:
            out["phantom_fills"] += 1
            by_seed[row["seed"]]["phantom_fills"] += 1
        if missed and phantom:
            out["both_fills"] += 1
            by_seed[row["seed"]]["both_fills"] += 1
        if missed or phantom:
            out["either_fills"] += 1
            by_seed[row["seed"]]["either_fills"] += 1
    out["missed_vol"] = round(out["missed_vol"], 4)
    out["phantom_vol"] = round(out["phantom_vol"], 4)
    out["by_seed"] = {str(k): v for k, v in sorted(by_seed.items())}
    return out


def _classes(rows: list[dict]) -> dict:
    buckets = defaultdict(lambda: {"n": 0, "qty": 0.0, "notional": 0.0, "rebate": 0.0, "m30": 0.0, "m30w": 0.0, "m300": 0.0, "m300w": 0.0})
    for row in rows:
        key = f"{row['seed']}|{row['mode']}|{row['class']}|{row['side']}"
        bucket = buckets[key]
        qty = row["qty"]
        bucket["n"] += 1
        bucket["qty"] += qty
        bucket["notional"] += row["price"] * qty
        bucket["rebate"] += row["rebate"]
        if row["m30"] is not None:
            bucket["m30"] += row["m30"] * qty
            bucket["m30w"] += qty
        if row["m300"] is not None:
            bucket["m300"] += row["m300"] * qty
            bucket["m300w"] += qty
    cooked = {}
    for key, bucket in buckets.items():
        cooked[key] = {
            "n": bucket["n"],
            "qty": round(bucket["qty"], 4),
            "notional": round(bucket["notional"], 4),
            "rebate": round(bucket["rebate"], 4),
            "markout_30": round(bucket["m30"] / bucket["m30w"], 6) if bucket["m30w"] else None,
            "markout_300": round(bucket["m300"] / bucket["m300w"], 6) if bucket["m300w"] else None,
        }
    return cooked


def _clear_lag(rows: list[dict]) -> dict:
    def pack(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0}
        ordered = sorted(vals)
        return {"n": len(ordered), "median_s": ordered[len(ordered) // 2], "p90_s": ordered[int(len(ordered) * 0.9)]}
    out = {}
    for era in ("telonex", "collector", "gap", "all"):
        subset = rows if era == "all" else [r for r in rows if r["era"] == era]
        out[era] = {
            "submit": pack([r["clear_submit_s"] for r in subset if r["clear_submit_s"] is not None]),
            "accept": pack([r["clear_accept_s"] for r in subset if r["clear_accept_s"] is not None]),
        }
    return out


def main() -> None:
    catalog = load_catalog()
    log(f"catalog {len(catalog)}")
    matches: dict[int, dict] = {}
    fills: list[dict] = []
    seed_meta = {}
    for seed in (0, 1, 2):
        seed_matches, seed_fills, meta = load_seed(seed, catalog)
        matches.update(seed_matches)
        fills.extend(seed_fills)
        seed_meta[seed] = meta
        missing = sum(1 for f in seed_fills if f["accept_ns"] is None or f["submit_ns"] is None)
        log(f"seed {seed} fills {len(seed_fills)} missing_join {missing} engine {meta['engine']:.2f}")
    by_token = defaultdict(list)
    for fill in fills:
        by_token[fill["token"]].append(fill)
    windows = {}
    for mid, match in matches.items():
        for token in match["tokens"]:
            windows[token] = (match["horn_us"], match["end_us"], match["era"])
    hists = {name: Hist() for name in ("telonex", "collector", "gap")}
    token_medians = defaultdict(list)
    records = []
    n_tokens = len(windows)
    missing_book = 0
    for i, (token, (t0, t1, era)) in enumerate(windows.items()):
        group = by_token.get(token, [])
        prices = {cent(f["price"]) for f in group}
        token_hist = Hist()
        ts_us, states = scan_token(token, (t0, t1), prices, token_hist)
        _merge(hists[era], token_hist)
        if token_hist.n:
            token_medians[era].append(token_hist.quantile_s(0.5))
        if not ts_us:
            missing_book += 1
        if group:
            t_min = min(f["accept_ns"] for f in group) // 1000 - 1_000_000
            t_max = max(f["ts_ns"] for f in group) // 1000 + 1_000_000
            trades, tape = load_trades(token, t_min, t_max)
            packed = [
                {"ts": s["ts"], "bid": s["bid_sz"], "ask": s["ask_sz"], "best_bid": s["bid"], "best_ask": s["ask"]}
                for s in states
            ]
            ts_set = set(ts_us)
            for fill in group:
                got = classify_fill(fill, packed, trades, ts_us, ts_set)
                got["tape"] = tape
                records.append({**{k: fill[k] for k in (
                    "seed", "match", "token_index", "side", "price", "qty", "rebate", "m30", "m300",
                    "mode", "era", "won", "level", "queue_ahead",
                )}, **got})
        if (i + 1) % 50 == 0:
            log(f"tokens {i + 1}/{n_tokens} fills {len(records)} missing_book {missing_book}")
    path = OUT / "queue_fills.jsonl"
    with path.open("w") as fh:
        for row in records:
            fh.write(json.dumps(row) + "\n")
    summary = {
        "fills": len(records),
        "tokens": n_tokens,
        "missing_book_tokens": missing_book,
        "cadence": {name: hist.as_dict() for name, hist in hists.items()},
        "token_median_of_medians": {
            name: _median(vals) for name, vals in token_medians.items()
        },
        "seed_meta": seed_meta,
        "pnl_valid": money(records, KEEP_VALID),
        "pnl_valid_plus_improve": money(records, KEEP_PLUS),
        "mis_side": _mis_side(records),
        "classes": _classes(records),
        "clear_lag": _clear_lag(records),
    }
    (OUT / "queue_summary.json").write_text(json.dumps(summary, indent=2))
    log(f"wrote {path} rows {len(records)}")


def _merge(dst: Hist, src: Hist) -> None:
    for i, count in enumerate(src.buckets):
        dst.buckets[i] += count
    dst.n += src.n
    dst.sum_s += src.sum_s
    dst.zeros += src.zeros


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    ordered = sorted(vals)
    return ordered[len(ordered) // 2]


if __name__ == "__main__":
    main()
