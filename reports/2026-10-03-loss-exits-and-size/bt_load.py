import json, numpy as np, pandas as pd
from pathlib import Path
RES = json.loads(Path("resolutions.json").read_text())

def load_seed(base: Path, seed: str):
    f = pd.read_parquet(base / seed / "fills.parquet")
    r = pd.read_parquet(base / seed / "results.parquet")
    r = r[~r.terminated_early].copy()
    f = f[f.match_id.isin(r.match_id)].copy()
    slug = r.set_index("match_id").slug
    f["slug"] = f.match_id.map(slug)
    def val(row_slug, tok):
        d = RES.get(row_slug)
        if not d or not d["outcomePrices"]:
            return np.nan
        return float(d["outcomePrices"][tok])
    f["value"] = [val(s, t) for s, t in zip(f.slug, f.token_index)]
    f["seed"] = seed
    r["seed"] = seed
    return f.sort_values(["match_id", "ts_ns"]).reset_index(drop=True), r

def check_mapping(f, r):
    t = r[(r.terminal_position > 1) & r.settlement_applied].copy()
    t["implied"] = (t.engine_pnl - t.cash_flow) / t.terminal_position
    t["res"] = [float(RES[s]["outcomePrices"][i]) if RES.get(s) else np.nan for s, i in zip(t.slug, t.terminal_token_index)]
    ok = (abs(t.implied - t.res) < 0.02).mean()
    return len(t), ok

if __name__ == "__main__":
    base = Path("/root/work/esports-trader/data/backtests/dota_maker/LIVE")
    for s in ("seed0", "seed1", "seed2"):
        f, r = load_seed(base, s)
        print(s, "maps", len(r), "fills", len(f), "mapping check (n, share ok):", check_mapping(f, r), "nan values", f.value.isna().sum())
