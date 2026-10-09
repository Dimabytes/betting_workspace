"""Live wallet B per map from the engine journal: maker fills, shares, settled PnL."""
import json
import sys

import pandas as pd

OUR = "0xce44ec50818b97f0027cefccd33296161b33f6be"
journal, catalog_path = sys.argv[1], sys.argv[2]
ids = [9034957701, 9035220432, 9035256154, 9035318247, 9035434210]
catalog = pd.read_parquet(catalog_path).set_index("match_id").loc[ids]
fills = []
seen = set()
for line in open(journal):
    row = json.loads(line)
    if row["kind"] != "user_trade":
        continue
    data = row["data"]
    for maker in data.get("maker_orders") or []:
        if (maker.get("maker_address") or "").lower() != OUR:
            continue
        key = (data["id"], maker["order_id"])
        if key in seen:
            continue
        seen.add(key)
        fills.append({"asset": maker["asset_id"], "price": float(maker["price"]), "qty": float(maker["matched_amount"]), "t": float(data["match_time"])})
fills = pd.DataFrame(fills)
out = []
for match_id, row in catalog.iterrows():
    tokens = [str(row.token_id_0), str(row.token_id_1)]
    winner = row.radiant_token_index if row.radiant_win else 1 - row.radiant_token_index
    mine = fills[fills.asset.isin(tokens)].copy()
    mine["index"] = mine.asset.map({tokens[0]: 0, tokens[1]: 1})
    mine["pnl"] = mine.qty * ((mine["index"] == winner).astype(float) - mine.price)
    yes = mine[mine["index"] == 0]
    no = mine[mine["index"] == 1]
    out.append({"match_id": match_id, "fills": len(mine), "shares": round(mine.qty.sum(), 2),
                "yes_sh": round(yes.qty.sum(), 2), "no_sh": round(no.qty.sum(), 2),
                "avg_yes": round((yes.qty * yes.price).sum() / max(yes.qty.sum(), 1e-9), 4),
                "avg_no": round((no.qty * no.price).sum() / max(no.qty.sum(), 1e-9), 4),
                "pnl": round(mine.pnl.sum(), 2)})
table = pd.DataFrame(out)
print(table.to_string(index=False))
print("total pnl", round(table.pnl.sum(), 2), "fills", table.fills.sum(), "shares", round(table.shares.sum(), 2))
