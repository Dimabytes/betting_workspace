"""Per 5-minute bucket: live vs sim resting bid price per token (median), orders placed, fills."""
import json
import sys

import pandas as pd

journal, run_dir, match_id = sys.argv[1], sys.argv[2], int(sys.argv[3])
cat = pd.read_parquet("data/new_processed/match_catalog/match_catalog.parquet").set_index("match_id").loc[match_id]
tokens = {str(cat.token_id_0): 0, str(cat.token_id_1): 1}
live = []
for line in open(journal):
    row = json.loads(line)
    if row["kind"] == "user_order" and row["data"]["asset_id"] in tokens and row["data"]["type"] == "PLACEMENT":
        live.append({"t": int(row["data"]["timestamp"]) * 1_000_000, "token_index": tokens[row["data"]["asset_id"]], "price": float(row["data"]["price"])})
live = pd.DataFrame(live)
q = pd.read_parquet(f"{run_dir}/quote_events.parquet")
print(q.kind.value_counts().to_dict())
sim = q[q.kind == "submitted"].rename(columns={"ts_ns": "t"})
fills_sim = pd.read_parquet(f"{run_dir}/fills.parquet")
fills_sim = fills_sim[fills_sim.match_id == match_id].rename(columns={"ts_ns": "t"})
OUR = "0xce44ec50818b97f0027cefccd33296161b33f6be"
fills_live = []
for line in open(journal):
    row = json.loads(line)
    if row["kind"] != "user_trade":
        continue
    for m in row["data"].get("maker_orders") or []:
        if (m.get("maker_address") or "").lower() == OUR and m["asset_id"] in tokens:
            fills_live.append({"t": int(float(row["data"]["match_time"]) * 1e9), "key": (row["data"]["id"], m["order_id"]), "quantity": float(m["matched_amount"])})
fills_live = pd.DataFrame(fills_live).drop_duplicates("key")
horn = pd.Timestamp(cat.horn_at).value
for frame in (live, sim, fills_sim, fills_live):
    frame["bucket"] = ((frame.t - horn) // (300 * 10**9)).astype(int) * 5
rows = []
for b in sorted(set(live.bucket) | set(sim.bucket)):
    r = {"min": b}
    for k in (0, 1):
        lv = live[(live.bucket == b) & (live.token_index == k)]
        sm = sim[(sim.bucket == b) & (sim.token_index == k)]
        r[f"live_n{k}"] = len(lv)
        r[f"sim_n{k}"] = len(sm)
        r[f"live_px{k}"] = round(lv.price.median(), 3) if len(lv) else None
        r[f"sim_px{k}"] = round(sm.price.median(), 3) if len(sm) else None
    r["live_fill_sh"] = round(fills_live[fills_live.bucket == b].quantity.sum(), 1)
    r["sim_fill_sh"] = round(fills_sim[fills_sim.bucket == b].quantity.sum(), 1)
    rows.append(r)
print(pd.DataFrame(rows).to_string(index=False))
