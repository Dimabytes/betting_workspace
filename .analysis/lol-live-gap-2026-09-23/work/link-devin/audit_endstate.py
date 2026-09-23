"""End-state checks: market end mid vs radiant_win; final NW sign vs radiant_win;
market_end_time vs game end; PM question number vs linked game number."""

import json
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23")

links = pq.read_table(E / "data/lol/processed/lolesports_links/links.parquet").to_pylist()
universe = pq.read_table(E / "data/lol/processed/universe/markets.parquet").to_pylist()
games = pq.read_table(E / "data/lol/processed/lolesports/games.parquet").to_pylist()
split = pq.read_table(E / "data/lol/processed/datasets/split.parquet").to_pylist()

uni_by_cid = {u["condition_id"]: u for u in universe if u["condition_id"]}
link_by_gid = {l["esports_game_id"]: l for l in links}
games_by_gid = {g["esports_game_id"]: g for g in games}
split_gids = {s["esports_game_id"]: s["split"] for s in split}

# ---------- market_seconds: last ok mid per match ----------
ms = pq.read_table(
    E / "data/lol/processed/datasets/market_seconds.parquet",
    columns=["match_id", "second", "market_status", "market_p_radiant"],
)
import pandas as pd

df = ms.to_pandas()
ok = df[df["market_status"] == "ok"]
last = ok.groupby("match_id").agg(last_second=("second", "max"))
last = last.join(
    ok.set_index(["match_id", "second"]), on=["match_id", "last_second"], how="inner"
)
# mean of last up-to-30 ok seconds for robustness
ok_sorted = ok.sort_values(["match_id", "second"])
tail_mean = ok_sorted.groupby("match_id").tail(30).groupby("match_id")["market_p_radiant"].mean()

print("== 2a: end-of-map market mid vs radiant_win ==")
contradictions = []
weird = []
for match_id, row in last.iterrows():
    gid = str(match_id)
    l = link_by_gid.get(gid)
    if l is None:
        continue
    radiant_win = int(l["resolved_outcome_index"]) == int(l["radiant_token_index"])
    end_mid = row["market_p_radiant"]
    tmean = tail_mean.get(match_id)
    u = uni_by_cid.get(l["condition_id"], {})
    rec = (gid, l["game_number"], radiant_win, round(end_mid, 4), round(tmean, 4),
           u.get("question"), l["assignment"], split_gids.get(gid))
    expected = 1.0 if radiant_win else 0.0
    if abs(end_mid - expected) > 0.5 and abs(tmean - expected) > 0.4:
        contradictions.append(rec)
    elif abs(end_mid - expected) > 0.2:
        weird.append(rec)
print(f"market_seconds matches={len(last)} hard contradictions={len(contradictions)} "
      f"soft (>0.2 off)={len(weird)}")
for r in contradictions[:40]:
    print("  CONTRA", r)
for r in weird[:40]:
    print("  soft", r)

# ---------- final NW sign vs radiant_win (orientation/wrong-game detector) ----------
print("\n== end-game radiant_nw_adv sign vs radiant_win ==")
gf = pq.read_table(
    E / "data/lol/processed/datasets/game_features.parquet",
    columns=["match_id", "game_second", "radiant_nw_adv", "deaths_radiant", "deaths_dire"],
).to_pandas()
gf_sorted = gf.sort_values(["match_id", "game_second"])
tail_feat = gf_sorted.groupby("match_id").tail(10).groupby("match_id").agg(
    nw=("radiant_nw_adv", "mean"), dr=("deaths_radiant", "mean"), dd=("deaths_dire", "mean"),
    last_second=("game_second", "max"),
)
sign_contra = []
for match_id, row in tail_feat.iterrows():
    gid = str(match_id)
    l = link_by_gid.get(gid)
    if l is None:
        continue
    radiant_win = int(l["resolved_outcome_index"]) == int(l["radiant_token_index"])
    nw = row["nw"]
    death_diff = row["dd"] - row["dr"]  # positive => radiant died less => radiant favored
    disagree_nw = (radiant_win and nw < 0) or (not radiant_win and nw > 0)
    disagree_deaths = (radiant_win and death_diff < 0) or (not radiant_win and death_diff > 0)
    if disagree_nw and disagree_deaths:
        u = uni_by_cid.get(l["condition_id"], {})
        sign_contra.append(
            (gid, l["game_number"], radiant_win, round(nw), round(death_diff, 1),
             int(row["last_second"]), u.get("question"), l["assignment"], split_gids.get(gid))
        )
print(f"maps where BOTH final nw_adv and deaths disagree with radiant_win: {len(sign_contra)}")
for r in sign_contra[:50]:
    print("  ", r)

# ---------- market_end_time vs game end ----------
print("\n== market_end_time vs last frame time ==")
import math
from shared.utils.parsing import parse_ts
from lol.livestats_frames import wall_us_for_second  # noqa

# last frame wall per match: max state_ts_us in market_seconds
last_state = df.groupby("match_id")["state_ts_us"].max()
end_deltas = []
for match_id, last_us in last_state.items():
    gid = str(match_id)
    l = link_by_gid.get(gid)
    u = uni_by_cid.get(l["condition_id"]) if l else None
    if u is None or not u["market_end_time"]:
        continue
    me = parse_ts(u["market_end_time"])
    if me is None:
        continue
    end_deltas.append((me - last_us / 1e6, l, u))
dvals = sorted(d for d, _, _ in end_deltas)
n = len(dvals)
print("market_end - last_frame seconds percentiles:",
      {p: round(dvals[min(n - 1, int(n * p))]) for p in (0, .01, .05, .25, .5, .75, .95, .99, 1)})
