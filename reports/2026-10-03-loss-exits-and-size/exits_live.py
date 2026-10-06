import numpy as np, pandas as pd
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 400)
K = pd.read_parquet("live_known_maps.parquet"); F = pd.read_parquet("live_fills_enriched.parquet")
F = F.merge(K[["match_id", "series", "clip", "day", "week", "joined"]], on="match_id", how="inner")
S = F[F.side == "SELL"].copy()
S["won_f"] = S.won.astype(float)
S["hold_adv"] = S.qty * (S.won_f - S.price)
S["proceeds"] = S.qty * S.price
S["loss_exit"] = S.price < S.avg_cost_before - 0.005
S["gain_exit"] = S.price > S.avg_cost_before + 0.005
S["manual"] = ~S.maker
rng = np.random.default_rng(7)

def boot(df, col, n=4000):
    ser = df.groupby("series")[col].sum()
    if len(ser) < 3:
        return (np.nan, np.nan)
    vals = ser.values
    sims = [rng.choice(vals, size=len(vals), replace=True).sum() for _ in range(n)]
    return tuple(np.percentile(sims, [2.5, 97.5]).round(0))

def summ(df, label):
    if len(df) == 0:
        print(label, "none"); return
    w = df.qty
    print(f"{label:48s} sells={len(df):4d} maps={df.match_id.nunique():3d} series={df.series.nunique():3d} "
          f"shares={w.sum():9.0f} avg_px={np.average(df.price, weights=w):.3f} win_rate(w)={np.average(df.won_f, weights=w):.3f} "
          f"hold_adv=${df.hold_adv.sum():9.0f} CI95={boot(df, 'hold_adv')}")

for period, sub in [("ALL live Dota", S), ("since 2026-09-21", S[S.joined >= "2026-09-21"]),
                    ("since 2026-09-28 (big clips)", S[S.joined >= "2026-09-28"]), ("clip 400", S[S["clip"] == 400])]:
    print("\n####", period)
    summ(sub, "all sells")
    summ(sub[sub.manual], "manual (taker) sells")
    bot = sub[~sub.manual]
    summ(bot, "bot sells")
    summ(bot[bot.loss_exit], "bot LOSS exits (px < avg cost)")
    summ(bot[bot.loss_exit & (bot.won == True)], "  ...token later WON (comeback)")
    summ(bot[bot.loss_exit & (bot.won == False)], "  ...token later LOST")
    summ(bot[bot.gain_exit], "bot GAIN exits (px > avg cost)")
    fav = bot[bot.episode_first_buy >= 0.65]
    summ(fav[fav.loss_exit], "bot LOSS exits, entry >= 0.65 (favorite)")
    dog = bot[bot.episode_first_buy < 0.65]
    summ(dog[dog.loss_exit], "bot LOSS exits, entry < 0.65")
S.to_parquet("live_sells.parquet")
