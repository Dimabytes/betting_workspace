"""Live wallet B vs backtest per map: fills, shares, avg bid per side, PnL, 30 s markout (c/share)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUR = "0xce44ec50818b97f0027cefccd33296161b33f6be"
RAW = Path("data/raw/telonex/polymarket")
DAY = "2026-10-08"
journal = sys.argv[1]
variant = sys.argv[2]
catalog = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet").set_index("match_id")


class Mids:
    def __init__(self, token: str):
        book = pd.read_parquet(RAW / "book_snapshot_full" / f"asset_id={token}" / f"{DAY}.parquet", columns=["timestamp_us", "bids", "asks"])
        self.ts = book.timestamp_us.to_numpy()
        self.bids = book.bids.to_numpy()
        self.asks = book.asks.to_numpy()

    def at(self, t_us: int) -> float:
        i = max(int(np.searchsorted(self.ts, t_us, side="right")) - 1, 0)
        bid = max((float(x["price"]) for x in self.bids[i]), default=0.0)
        ask = min((float(x["price"]) for x in self.asks[i]), default=1.0)
        return (bid + ask) / 2


def load_live_fills() -> pd.DataFrame:
    rows, seen = [], set()
    for line in open(journal):
        row = json.loads(line)
        if row["kind"] != "user_trade":
            continue
        data = row["data"]
        for maker in data.get("maker_orders") or []:
            if (maker.get("maker_address") or "").lower() != OUR or (data["id"], maker["order_id"]) in seen:
                continue
            seen.add((data["id"], maker["order_id"]))
            rows.append({"asset": maker["asset_id"], "price": float(maker["price"]), "quantity": float(maker["matched_amount"]),
                         "tx": (data.get("transaction_hash") or "").lower(), "t_us": int(float(data["match_time"]) * 1e6)})
    return pd.DataFrame(rows)


def print_times(token: str) -> dict[str, int]:
    path = RAW / "trades" / f"asset_id={token}" / f"{DAY}.parquet"
    if not path.is_file():
        return {}
    trades = pd.read_parquet(path, columns=["trade_id", "timestamp_us"])
    return dict(zip(trades.trade_id.str.lower(), trades.timestamp_us))


def summarize(fills: pd.DataFrame, winner: int, mids: list[Mids]) -> dict[str, float]:
    if fills.empty:
        return {"fills": 0, "shares": 0.0, "yes_sh": 0.0, "no_sh": 0.0, "avg_yes": np.nan, "avg_no": np.nan, "pnl": 0.0, "mk30_c": np.nan}
    marks = np.array([mids[i].at(t + 30_000_000) for i, t in zip(fills.token_index, fills.t_us)])
    yes, no = fills[fills.token_index == 0], fills[fills.token_index == 1]
    payout = (fills.token_index == winner).astype(float)
    return {
        "fills": len(fills),
        "shares": round(fills.quantity.sum(), 1),
        "yes_sh": round(yes.quantity.sum(), 1),
        "no_sh": round(no.quantity.sum(), 1),
        "avg_yes": round((yes.price * yes.quantity).sum() / yes.quantity.sum(), 3) if len(yes) else np.nan,
        "avg_no": round((no.price * no.quantity).sum() / no.quantity.sum(), 3) if len(no) else np.nan,
        "pnl": round((fills.quantity * (payout - fills.price)).sum(), 2),
        "mk30_c": round(100 * (fills.quantity * (marks - fills.price)).sum() / fills.quantity.sum(), 2),
    }


SWITCH_H3_END = pd.Timestamp("2026-10-08T14:47:23Z").value // 1000
SWITCH_H6_START = pd.Timestamp("2026-10-08T14:48:39Z").value // 1000
FAR = 2**62


def build_segments(variant: str) -> list[tuple[int, str, int, int, Path]]:
    """(match_id, label, from_us, to_us, run_dir). 9034957701 switched h3 -> h6 at 14:47-14:48 UTC."""
    root = Path("data/backtests/dota_maker")
    run = lambda name: root / f"validation_join_delta02_x015_cut480_p45_liveb1008-{name}-{variant}" / "seed0"
    segments = [(9034957701, "h3 to 14:47", 0, SWITCH_H3_END, run("h3")),
                (9034957701, "h6 from 14:48", SWITCH_H6_START, FAR, run("957-h6"))]
    for match_id in (9035220432, 9035256154, 9035318247, 9035434210):
        segments.append((match_id, "h6", 0, FAR, run("h6")))
    return segments


live = load_live_fills()
out = []
for match_id, label, t_from, t_to, run_dir in build_segments(variant):
    row = catalog.loc[match_id]
    tokens = [str(row.token_id_0), str(row.token_id_1)]
    winner = int(row.radiant_token_index if row.radiant_win else 1 - row.radiant_token_index)
    mids = [Mids(token) for token in tokens]
    real = live[live.asset.isin(tokens)].copy()
    real["token_index"] = real.asset.map({tokens[0]: 0, tokens[1]: 1})
    clocks = {**print_times(tokens[0]), **print_times(tokens[1])}
    real["t_us"] = [clocks.get(tx, t) for tx, t in zip(real.tx, real.t_us)]
    sim = pd.read_parquet(run_dir / "fills.parquet")
    sim = sim[sim.match_id == match_id].copy()
    sim["t_us"] = sim.ts_ns // 1000
    for source, frame in (("live", real), ("sim", sim)):
        frame = frame[(frame.t_us >= t_from) & (frame.t_us < t_to)]
        out.append({"match_id": match_id, "seg": label, "src": source, **summarize(frame, winner, mids)})
table = pd.DataFrame(out)
print(table.to_string(index=False))
for source in ("live", "sim"):
    part = table[table.src == source]
    mk = (part.mk30_c * part.shares).sum() / part.shares.sum()
    print(f"{source}: fills {part.fills.sum()}, shares {part.shares.sum():.1f}, pnl {part.pnl.sum():.2f}, mk30 {mk:.2f} c/share")
