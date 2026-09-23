"""Live model delta vs realized 300 s radiant-mid move, split by production model name.

Rows: signal reason=model with radiant_fair and market_p_radiant. Realized: market_p_radiant of the
first model row at game second >= s+300 (within 10 s) minus the current market_p_radiant.
"""
import glob, json, os, collections
import numpy as np

E = "/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader"
by = collections.defaultdict(lambda: {"p": [], "r": [], "maps": set()})
for d in glob.glob(f"{E}/data/trader/*/"):
    mj, sj = os.path.join(d, "match.json"), os.path.join(d, "session.jsonl")
    if not (os.path.exists(mj) and os.path.exists(sj)):
        continue
    game = json.load(open(mj)).get("game", "dota")
    model, mode, rows = None, None, []
    for line in open(sj):
        try:
            r = json.loads(line)
        except Exception:
            continue
        k = r.get("kind")
        if k == "session_start":
            model = (r.get("model") or {}).get("name"); mode = r.get("execution_mode")
        elif k == "signal" and r.get("reason") == "model" and r.get("radiant_fair") is not None and r.get("market_p_radiant") is not None:
            rows.append((int(r["second"]), float(r["radiant_fair"]) - float(r["market_p_radiant"]), float(r["market_p_radiant"])))
    if mode != "live" or not rows:
        continue
    secs = np.array([x[0] for x in rows]); mids = np.array([x[2] for x in rows])
    for s, pred, mid in rows:
        if s < 0 or s > 480:
            continue
        i = np.searchsorted(secs, s + 300)
        if i >= len(secs) or secs[i] - (s + 300) > 10:
            continue
        key = (game, model)
        by[key]["p"].append(pred); by[key]["r"].append(mids[i] - mid); by[key]["maps"].add(os.path.basename(d.rstrip("/")))
print(f"{'game':5} {'model':18} {'maps':>4} {'rows':>7} {'corr':>7} {'edge|p|>=2c (c)':>16} {'n':>6}")
for (game, model), v in sorted(by.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
    p, r = np.array(v["p"]), np.array(v["r"])
    if len(p) < 200:
        continue
    m = np.abs(p) >= 0.02
    edge = 100 * float(np.mean(np.sign(p[m]) * r[m])) if m.any() else float("nan")
    print(f"{game:5} {str(model):18} {len(v['maps']):4d} {len(p):7d} {np.corrcoef(p, r)[0,1]:7.3f} {edge:16.3f} {int(m.sum()):6d}")
