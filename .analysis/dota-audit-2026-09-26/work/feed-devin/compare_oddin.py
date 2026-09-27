"""Replay Oddin archives through the live reducer and compare to STRATZ exact-second
features at the same second, with a shift scan to detect clock offset."""
import json
import sys
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
sys.path.insert(0, str(E / "src"))

from trader.live_feed import FeedEvent
from trader.oddin_archive import iter_oddin_archive_records
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records

TRADER = E / "data" / "trader"
FEAT = E / "data/new_processed/dataset/game_features.parquet"

feats = pd.read_parquet(FEAT)
FEAT_IDS = set(feats["match_id"].unique())
truth_by_id = {mid: g.set_index("game_second") for mid, g in feats.groupby("match_id")}


def replay(d: Path, meta: dict) -> pd.DataFrame:
    reducer = OddinSnapshotReducer(meta["map_number"], meta["market"]["yes_is_radiant"])
    events = [
        e
        for e in replay_oddin_records(
            iter_oddin_archive_records(d / "oddin_state.jsonl.gz"), reducer
        )
        if isinstance(e, FeedEvent)
    ]
    rows = [
        dict(
            second=e.snapshot.second,
            radiant_nw_adv=e.snapshot.radiant_nw_adv,
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
    try:
        sid = int(meta["steam_match_id"])
    except (TypeError, ValueError):
        return None
    truth = truth_by_id.get(sid)
    if truth is None:
        return None
    try:
        live = replay(d, meta)
    except FileNotFoundError:
        return None
    if live.empty:
        return None
    best = None
    for shift in range(-8, 9):
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
        return dict(match_id=sid, archive=d.name, n=0)
    mae0, shift, m = best
    return dict(
        match_id=sid,
        archive=d.name,
        n=len(m),
        best_shift=shift,
        nw_adv_mae=round(mae0, 1),
        deaths_mae=(
            (m.deaths_radiant - m.deaths_radiant_stratz).abs()
            + (m.deaths_dire - m.deaths_dire_stratz).abs()
        ).mean(),
        top1_mae=(m.top1_nw_adv - m.top1_nw_adv_stratz).abs().mean(),
        paused_pct=m.paused.mean(),
    )


def main():
    rows = []
    for d in sorted(TRADER.iterdir()):
        meta_p = d / "match.json"
        if not meta_p.exists():
            continue
        try:
            meta = json.loads(meta_p.read_text())
        except Exception:
            continue
        if meta.get("feed_source") != "oddin":
            continue
        row = compare(d)
        if row:
            rows.append(row)
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(df.to_string())
    if len(df):
        print("\nshift histogram:", dict(df.best_shift.value_counts().sort_index()))
        print(df.drop(columns=["match_id", "archive", "best_shift"]).mean(numeric_only=True))


main()
