"""Verify aggressor-side correctness: which book level shrank around each fill (fill-devin)."""

import pandas as pd
import pyarrow.parquet as pq

ROOT = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/raw/telonex/polymarket"
DAY = "2026-09-04"
TOKENS = {
    0: "36946747883824898023808254946925983326269817251004902998289698829747635065694",
    1: "82086840383991785924234653677813734460597199808125936662356188867404162091207",
}
START_US, END_US = 1788527342000000, 1788530092000000


def load(channel, token):
    return pq.read_table(f"{ROOT}/{channel}/asset_id={token}/{DAY}.parquet").to_pandas()


def level_series(bk):
    out = {}
    for side in ("bids", "asks"):
        rows = []
        for ts, lv in zip(bk.timestamp_us.values, bk[side]):
            for l in lv:
                rows.append((ts, float(l["price"]), float(l["size"])))
        out[side] = pd.DataFrame(rows, columns=["ts_us", "price", "size"]).sort_values("ts_us")
    return out


def level_at(lv, ts_us):
    rows = lv[lv.ts_us <= ts_us]
    if rows.empty:
        return None, None
    last = rows.iloc[-1]
    return float(last["size"]), int(last.ts_us)


stats = {0: dict(tot=0, direct=0, mirror=0, direct_consumed_emitted=0, direct_consumed_opposite=0,
                 mirror_consumed_emitted=0, mirror_consumed_opposite=0, ambiguous=0),
         1: dict(tot=0, direct=0, mirror=0, direct_consumed_emitted=0, direct_consumed_opposite=0,
                 mirror_consumed_emitted=0, mirror_consumed_opposite=0, ambiguous=0)}

for i in (0, 1):
    tid = TOKENS[i]
    b = load("book_snapshot_full", tid)
    b = b[(b.timestamp_us >= START_US) & (b.timestamp_us <= END_US)].sort_values("timestamp_us")
    f = load("onchain_fills", tid)
    f = f[(f.block_timestamp_us >= START_US) & (f.block_timestamp_us <= END_US)].sort_values(
        "block_timestamp_us"
    )
    lv = level_series(b)
    bids_by_px = {px: g for px, g in lv["bids"].groupby("price")}
    asks_by_px = {px: g for px, g in lv["asks"].groupby("price")}
    st = stats[i]
    for _, fr in f.iterrows():
        fts = int(fr.block_timestamp_us)
        px = round(float(fr.price), 2)
        st["tot"] += 1
        emitted = str(fr.taker_side).lower()
        maker_is_me = str(fr.maker_asset_id) == tid
        # size change across the fill stamp on each side at this price
        for side_name, book in (("bids", bids_by_px), ("asks", asks_by_px)):
            g = book.get(px)
            if g is None:
                continue
            before = g[g.ts_us <= fts]
            after = g[g.ts_us > fts]
            if before.empty or after.empty:
                continue
            delta_b = float(after.iloc[0]["size"]) - float(before.iloc[-1]["size"])
            st.setdefault(f"delta_{side_name}", []).append(delta_b)
        if maker_is_me:
            st["direct"] += 1
        else:
            st["mirror"] += 1
        # measure: which side shrank the most around the stamp (±5s window look-back too)
        deltas = {}
        for side_name, book in (("bids", bids_by_px), ("asks", asks_by_px)):
            g = book.get(px)
            if g is None:
                continue
            w = g[(g.ts_us > fts - 3_000_000) & (g.ts_us <= fts + 3_000_000)]
            pre = g[g.ts_us <= fts - 3_000_000]
            if w.empty or pre.empty:
                continue
            deltas[side_name] = float(w.iloc[-1]["size"]) - float(pre.iloc[-1]["size"])
        if not deltas:
            st["ambiguous"] += 1
            continue
        # emitted aggressor: 'buy' => consumes asks; 'sell' => consumes bids
        emitted_side = "asks" if emitted == "buy" else "bids"
        opposite_side = "bids" if emitted_side == "asks" else "asks"
        d_emit = deltas.get(emitted_side, 0.0)
        d_opp = deltas.get(opposite_side, 0.0)
        if d_emit < -1e-9 and d_emit <= d_opp:
            key = "direct_consumed_emitted" if maker_is_me else "mirror_consumed_emitted"
            st[key] += 1
        elif d_opp < -1e-9 and d_opp < d_emit:
            key = "direct_consumed_opposite" if maker_is_me else "mirror_consumed_opposite"
            st[key] += 1
        else:
            st["ambiguous"] += 1

for i in (0, 1):
    st = stats[i]
    print(f"token{i}: fills={st['tot']} direct(maker=this)={st['direct']} mirror={st['mirror']}")
    print(
        f"  consumed emitted side: direct={st['direct_consumed_emitted']} mirror={st['mirror_consumed_emitted']}"
    )
    print(
        f"  consumed OPPOSITE side: direct={st['direct_consumed_opposite']} mirror={st['mirror_consumed_opposite']}"
    )
    print(f"  ambiguous/no-change: {st['ambiguous']}")
    for k in ("delta_bids", "delta_asks"):
        v = st.get(k)
        if v:
            s = pd.Series(v)
            print(f"  {k} delta across stamp: p10={s.quantile(.1):.1f} p50={s.quantile(.5):.1f} p90={s.quantile(.9):.1f}")
