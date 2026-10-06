import numpy as np, pandas as pd
from live_caps_lib import sim
pd.set_option("display.width", 250)
K = pd.read_parquet("live_known_maps.parquet"); F = pd.read_parquet("live_fills_enriched.parquet")
F["ts"] = pd.to_datetime(F.ts, utc=True); F = F.sort_values(["match_id", "ts"])
T = K[(K.day.astype(str) == "2026-10-03") & (K.buy_usd > 0)].sort_values("joined")
rows = []
for m in T.itertuples():
    g = F[F.match_id == m.match_id]; b = g[g.maker]
    tok_bought = b[b.side == "BUY"].groupby("is_yes").qty.sum()
    side_yes = tok_bought.idxmax() if len(tok_bought) else None
    team = (m.radiant if (side_yes == (m.yes_is_radiant == "True")) else m.dire) if side_yes is not None else "-"
    won_team = m.radiant if m.winner == "radiant" else m.dire
    actual = m.pnl
    bot_only, _ = sim(b, 1e12)
    cap4, _ = sim(b, 1600)
    buys = b[b.side == "BUY"]
    hold = (buys.qty * (buys.won.astype(float) - buys.price)).sum() + buys.rebate.sum()
    rows.append(dict(map=f"{m.radiant} vs {m.dire} m{int(m.map_number)}", our_team=team, winner=won_team,
                     actual=round(actual), bot_only=round(bot_only), cap4=round(cap4), never_sell=round(hold), peak=round(m.peak_cost)))
R = pd.DataFrame(rows)
print(R.to_string(index=False))
print(R[["actual", "bot_only", "cap4", "never_sell"]].sum())
