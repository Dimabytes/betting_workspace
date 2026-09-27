"""Replay GRID archives through the live reducer and compare to STRATZ exact-second
features at the same second, with a small shift scan to detect clock offset."""
import json
import sys
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))

from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.live_feed import FeedEvent

TRADER = E / "data" / "trader"
FEAT = E / "data/new_processed/dataset/game_features.parquet"

feats = pd.read_parquet(FEAT)
FEAT_IDS = set(feats["match_id"].unique())
truth_by_id = {mid: g.set_index("game_second") for mid, g in feats.groupby("match_id")}


def replay(d: Path, meta: dict) -> pd.DataFrame:
    market = meta["market"]
    reducer = GridFrameReducer(
        meta["map_number"],
        market["outcome_0_name"],
        market["outcome_1_name"],
        GAME_PROFILES[meta.get("game") or "dota"],
    )
    records = list(iter_grid_archive_records(d / "grid_state.jsonl.gz"))
    events = [e for e in replay_grid_records(records, reducer) if isinstance(e, FeedEvent)]
    rows = [
        dict(
            second=e.snapshot.second,
            radiant_nw_adv=e.snapshot.radiant_nw_adv,
            radiant_xp_adv=e.snapshot.radiant_xp_adv,
            deaths_radiant=e.snapshot.deaths_radiant,
            deaths_dire=e.snapshot.deaths_dire,
            top1_nw_adv=e.snapshot.top.top1_nw_adv,
            paused=e.snapshot.paused,
        )
        for e in events
        if e.snapshot.phase in ("in_progress", "pre_horn")
    ]
    if not rows:
        return pd.DataFrame()
    live = pd.DataFrame(rows)
    return live.drop_duplicates("second", keep="last").set_index("second").sort_index()


def compare(d: Path):
    meta = json.loads((d / "match.json").read_text())
    sid = int(meta["steam_match_id"])
    truth = truth_by_id.get(sid)
    if truth is None:
        return None
    try:
        live = replay(d, meta)
    except FileNotFoundError:
        return None
    if live.empty:
        return None
    # shift scan on nw_adv to find clock offset: rows where stratz has data
    best = None
    for shift in range(-4, 5):
        merged = live.join(truth.shift(0).rename(columns=lambda c: c + "_stratz"), how="inner")
        shifted = live.reset_index()
        shifted["second"] += shift
        m = shifted.set_index("second").join(
            truth.rename(columns=lambda c: c + "_stratz"), how="inner"
        )
        m = m[(m.index >= 0) & (m.index <= 600)]
        if len(m) < 30:
            continue
        mae = (m.radiant_nw_adv - m.radiant_nw_adv_stratz).abs().mean()
        if best is None or mae < best[0]:
            best = (mae, shift, m)
    if best is None:
        return dict(match_id=sid, n=0)
    mae0, shift, m = best
    return dict(
        match_id=sid,
        n=len(m),
        best_shift=shift,
        nw_adv_mae=round(mae0, 1),
        xp_mismatch=(m.radiant_xp_adv != m.radiant_xp_adv_stratz).mean(),
        xp_mae=(m.radiant_xp_adv - m.radiant_xp_adv_stratz).abs().mean(),
        deaths_mae=(
            (m.deaths_radiant - m.deaths_radiant_stratz).abs()
            + (m.deaths_dire - m.deaths_dire_stratz).abs()
        ).mean(),
        top1_mae=(m.top1_nw_adv - m.top1_nw_adv_stratz).abs().mean(),
        paused_pct=m.paused.mean(),
    )


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    rows = []
    seen = 0
    for d in sorted(TRADER.iterdir()):
        meta_p = d / "match.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text())
        except Exception:
            continue
        if meta.get("feed_source") != "grid":
            continue
        try:
            if int(meta.get("steam_match_id") or 0) not in FEAT_IDS:
                continue
        except (TypeError, ValueError):
            continue
        row = compare(d)
        if row:
            rows.append(row)
            seen += 1
            if seen >= limit:
                break
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(df.to_string())
    if len(df):
        print("\nshift histogram:", dict(df.best_shift.value_counts().sort_index()))
        print(df.drop(columns=["match_id", "best_shift"]).mean(numeric_only=True))


main()
