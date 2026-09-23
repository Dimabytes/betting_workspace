"""Per-map feature extraction: GRID live path vs livestats train path.

For every LoL trader archive with a links.parquet row and raw livestats:
  - replay grid_state.jsonl through GridFrameReducer (the live code path)
    -> grid snapshots with the labels the model saw
  - prepare_map_livestats_until (the training code path)
    -> integer-second grid rows 0..900
  - session.jsonl signal rows (what the model predicted live)
Writes one parquet per map under work/featemp-devin/maps/.
"""

import json
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
W = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/"
    ".analysis/lol-live-gap-2026-09-23/work/featemp-devin"
)
MAPS_DIR = W / "maps"
END_SECOND = 900  # covers the 0..540 training window with buffer for clock fit


def load_links() -> pd.DataFrame:
    return pd.read_parquet(E / "data/lol/processed/lolesports_links/links.parquet")


def candidate_maps() -> pd.DataFrame:
    links = load_links()
    by_cid = {r.condition_id: r for r in links.itertuples()}
    rows = []
    for d in sorted((E / "data/trader").glob("grid-*")):
        mj = d / "match.json"
        if not mj.exists() or not (d / "session.jsonl").exists():
            continue
        try:
            m = json.loads(mj.read_text())
        except Exception:
            continue
        if m.get("game") != "lol":
            continue
        cid = m["market"]["condition_id"]
        link = by_cid.get(cid)
        if link is None:
            continue
        gid = str(link.esports_game_id)
        if not (E / "data/lol/raw/lolesports/windows" / f"{gid}.jsonl.gz").exists():
            continue
        if not (E / "data/lol/raw/lolesports/details" / f"{gid}.jsonl.gz").exists():
            continue
        rows.append(
            dict(
                match=d.name,
                esports_game_id=gid,
                map_number=m["map_number"],
                outcome_0=m["market"]["outcome_0_name"],
                outcome_1=m["market"]["outcome_1_name"],
                yes_is_radiant=bool(m["market"]["yes_is_radiant"]),
                model=m.get("model", {}).get("name"),
                joined_at_utc=m.get("joined_at_utc"),
                horn_at_utc=m.get("horn_at_utc"),
                grid_delay_s=m.get("grid_delay_s"),
            )
        )
    return pd.DataFrame(rows)


def read_signals(match_dir: Path) -> pd.DataFrame:
    rows = []
    for i, line in enumerate((match_dir / "session.jsonl").read_text().splitlines()):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") == "signal":
            rows.append(
                dict(
                    row_index=i,
                    second=r.get("second"),
                    market_p_radiant=r.get("market_p_radiant"),
                    market_radiant_prior=r.get("market_radiant_prior"),
                    radiant_fair=r.get("radiant_fair"),
                    yes_fair=r.get("yes_fair"),
                    reason=r.get("reason"),
                )
            )
        elif r.get("kind") == "session_start":
            rows.append(dict(row_index=i, kind_marker="session_start",
                             model=r.get("model", {}).get("name")))
    return pd.DataFrame(rows)


def build_map(row: dict) -> dict:
    class R:
        pass
    r = R()
    for k, v in row.items():
        setattr(r, k, v)
    row = r
    from lol.livestats_frames import (
        LivestatsDrop,
        prepare_map_livestats_until,
    )
    from lol.networth import DEFAULT_ITEM_CATALOG_DIR, load_item_catalog
    from lol.constants import LOL_WINDOWS_DIR, LOL_DETAILS_DIR
    from trader.game_profile import GAME_PROFILES
    from trader.grid_archive import iter_grid_archive_records
    from trader.grid_feed import GridFrameReducer, replay_grid_records

    match_dir = E / "data/trader" / row.match
    out = MAPS_DIR / f"{row.match}.parquet"
    signals = read_signals(match_dir)

    # --- GRID replay (live path) ---
    reducer = GridFrameReducer(
        int(row.map_number), row.outcome_0, row.outcome_1, GAME_PROFILES["lol"]
    )
    grid_state = match_dir / "grid_state.jsonl"
    if not grid_state.exists():
        grid_state = match_dir / "grid_state.jsonl.gz"
    records = list(iter_grid_archive_records(grid_state))
    events = list(replay_grid_records(records, reducer))
    grid_rows = []
    for i, ev in enumerate(events):
        s = ev.snapshot
        grid_rows.append(
            dict(
                event_index=i,
                second=s.second,
                server_timestamp=s.server_timestamp,
                received_at_utc=ev.received_at_utc,
                phase=str(s.phase),
                horn_unix_seconds=ev.horn_unix_seconds,
                radiant_nw_adv=s.radiant_nw_adv,
                radiant_nw=s.radiant_nw,
                dire_nw=s.dire_nw,
                radiant_xp_adv=s.radiant_xp_adv,
                deaths_radiant=s.deaths_radiant,
                deaths_dire=s.deaths_dire,
                top1_nw_adv=s.top.top1_nw_adv,
                radiant_top1_nw_ratio=s.top.radiant_top1_nw_ratio,
                dire_top1_nw_ratio=s.top.dire_top1_nw_ratio,
                paused=s.paused,
            )
        )
    grid = pd.DataFrame(grid_rows)

    # --- livestats (train path) ---
    links = load_links()
    link = links[links.esports_game_id.astype(str) == str(row.esports_game_id)].iloc[0]
    catalog = load_item_catalog(DEFAULT_ITEM_CATALOG_DIR)
    res = prepare_map_livestats_until(
        dict(link), LOL_WINDOWS_DIR, LOL_DETAILS_DIR, catalog, END_SECOND
    )
    status = "ok"
    ls = pd.DataFrame()
    drop_reason = None
    if isinstance(res, LivestatsDrop):
        status = "drop"
        drop_reason = res.reason
    else:
        ls_rows = []
        for slot in res.grid_rows:
            f = slot.features
            ls_rows.append(
                dict(
                    second=slot.second,
                    state_wall_us=slot.state_wall_us,
                    radiant_nw_adv=f.radiant_nw_adv,
                    radiant_nw=f.radiant_nw,
                    dire_nw=f.dire_nw,
                    radiant_xp_adv=f.radiant_xp_adv,
                    deaths_radiant=f.deaths_radiant,
                    deaths_dire=f.deaths_dire,
                    top1_nw_adv=f.top1_nw_adv,
                    radiant_top1_nw_ratio=f.radiant_top1_nw_ratio,
                    dire_top1_nw_ratio=f.dire_top1_nw_ratio,
                )
            )
        ls = pd.DataFrame(ls_rows)
        meta = dict(
            spawn_wall_seconds=res.spawn_wall_seconds,
            pause_count=res.pause_count,
            pause_seconds=res.pause_seconds,
            frame_count=res.frame_count,
            skipped_age_rows=res.skipped_age_rows,
            skipped_invariant_rows=res.skipped_invariant_rows,
            skipped_stamp_rows=res.skipped_stamp_rows,
            last_game_time=res.last_game_time,
        )
    if status != "ok":
        meta = {}
    # store as a dict of frames in parquet: use one file with a 'src' column
    grid = grid.assign(src="grid")
    if not ls.empty:
        ls = ls.assign(src="ls", state_wall_us=ls.state_wall_us)
    signals = signals.assign(src="signal")
    allcols = sorted(set(grid.columns) | set(ls.columns) | set(signals.columns))
    combined = pd.concat(
        [grid.reindex(columns=allcols), ls.reindex(columns=allcols),
         signals.reindex(columns=allcols)],
        ignore_index=True,
    )
    combined["match"] = row.match
    combined["esports_game_id"] = str(row.esports_game_id)
    for k, v in meta.items():
        combined.attrs[k] = v
    combined.to_parquet(out)
    # attrs don't survive parquet; write meta json sidecar
    (MAPS_DIR / f"{row.match}.meta.json").write_text(json.dumps({
        **meta, "status": status, "drop_reason": drop_reason,
        "match": row.match, "esports_game_id": str(row.esports_game_id),
        "model": row.model, "yes_is_radiant": row.yes_is_radiant,
        "horn_at_utc": row.horn_at_utc, "grid_delay_s": row.grid_delay_s,
    }))
    return dict(match=row.match, status=status, drop=drop_reason,
                n_grid=len(grid), n_ls=len(ls), n_sig=len(signals))


def main() -> None:
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    cands = candidate_maps()
    print(f"candidates: {len(cands)}")
    cands.to_csv(W / "candidate_maps.csv", index=False)
    only = set(sys.argv[1:])
    if only:
        cands = cands[cands.match.isin(only)]
        print(f"filtered to: {len(cands)}")
    results = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        futs = {
            pool.submit(build_map, dict(row._asdict())): row.match
            for row in cands.itertuples()
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                traceback.print_exc()
                results.append(dict(match=name, status="error", drop=str(exc)[:200],
                                    n_grid=0, n_ls=0, n_sig=0))
            done = len(results)
            if done % 25 == 0:
                print(f"{done}/{len(cands)}")
    res = pd.DataFrame(results)
    res.to_csv(W / "build_results.csv", index=False)
    print(res.status.value_counts())
    print(res[res.status != "ok"].head(30).to_string())


if __name__ == "__main__":
    main()
