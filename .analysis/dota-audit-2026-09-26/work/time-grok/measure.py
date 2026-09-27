"""Timing measurements for the Dota audit. Read-only. Writes stats next to this file."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.dataset import VALIDATION_START_TIME
from shared.constants.paths import (
    MATCH_CATALOG_PATH,
    MARKET_SECONDS_DIR,
    RESEARCH_MODEL_SPLIT_PATH,
    TRADER_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.utils.jsonl_io import open_maybe_gz
from shared.utils.match_time import parse_utc
from trader.game_profile import GAME_PROFILES
from trader.grid_feed import GridFrameReducer, replay_grid_records
from trader.grid_widgets import TABLE_SERVICE, parse_frame
from trader.oddin_archive import iter_oddin_archive_records
from trader.oddin_feed import OddinSnapshotReducer, replay_oddin_records
from trader.paths import (
    GRID_STATE_ARCHIVE_FILENAME,
    ODDIN_STATE_ARCHIVE_FILENAME,
)

OUT = Path(__file__).resolve().parent / "measure_out.json"
E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")


def pct(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=np.float64)
    qs = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    out = {f"p{q}": float(np.percentile(arr, q)) for q in qs}
    out["n"] = float(len(arr))
    out["mean"] = float(arr.mean())
    return out


def dataset_facts() -> dict[str, object]:
    cutoff = datetime.fromtimestamp(VALIDATION_START_TIME, UTC).isoformat()
    split = pd.read_parquet(RESEARCH_MODEL_SPLIT_PATH)
    val = split[split["split"] == "validation"]
    gap = val[val["backtest_book_gap_excluded"].astype(bool)]
    val_n = int(len(val))
    excluded_ids = [int(x) for x in gap["match_id"].tolist()]

    cat = pd.read_parquet(
        MATCH_CATALOG_PATH,
        columns=["match_id", "spawn_at", "horn_at", "pauses_json", "duration", "radiant_prior"],
    )
    # start_time rule: spawn if present else horn
    spawn = cat["spawn_at"].to_list()
    horn = cat["horn_at"].to_list()
    near = 0
    flip = 0
    pause_maps = 0
    pause_secs: list[int] = []
    prehorn_pause_maps = 0
    prehorn_pause_secs: list[int] = []
    for spawn_s, horn_s, pauses_raw in zip(spawn, horn, cat["pauses_json"].to_list(), strict=True):
        horn_dt = parse_utc(str(horn_s))
        anchor = parse_utc(str(spawn_s)) if isinstance(spawn_s, str) else horn_dt
        horn_ts = int(horn_dt.timestamp())
        anchor_ts = int(anchor.timestamp())
        if abs(anchor_ts - VALIDATION_START_TIME) <= 120 or abs(horn_ts - VALIDATION_START_TIME) <= 120:
            near += 1
            spawn_side = anchor_ts >= VALIDATION_START_TIME
            horn_side = horn_ts >= VALIDATION_START_TIME
            if spawn_side != horn_side:
                flip += 1
        if not isinstance(pauses_raw, str):
            continue
        pauses = json.loads(pauses_raw)
        if not pauses:
            continue
        pause_maps += 1
        total = 0
        pre = 0
        for pause in pauses:
            total += int(pause["duration"])
            if int(pause["time"]) < 0:
                pre += int(pause["duration"])
        pause_secs.append(total)
        if pre:
            prehorn_pause_maps += 1
            prehorn_pause_secs.append(pre)

    # validation row second is the market second: compare one match to its market cache
    val_ids = val["match_id"].head(1).tolist()
    sample_id = int(val_ids[0]) if val_ids else None
    second_check = None
    if sample_id is not None:
        caches = list(MARKET_SECONDS_DIR.glob(f"v*/match_id={sample_id}.parquet"))
        if caches:
            market_seconds = set(pd.read_parquet(caches[0], columns=["second"])["second"].tolist())
            joined_frame = pd.read_parquet(
                VALIDATION_DATASET_PATH,
                columns=["second"],
                filters=[("match_id", "==", sample_id)],
            )
            joined = set(joined_frame["second"].tolist())
            second_check = {
                "match_id": sample_id,
                "market_n": len(market_seconds),
                "joined_n": len(joined),
                "joined_minus_market": len(joined - market_seconds),
                "joined_min": min(joined) if joined else None,
                "joined_max": max(joined) if joined else None,
                "market_min": min(market_seconds) if market_seconds else None,
            }

    # where do excluding gaps sit?
    gap_locs: list[dict[str, int]] = []
    for match_id in excluded_ids[:80]:
        caches = list(MARKET_SECONDS_DIR.glob(f"v*/match_id={match_id}.parquet"))
        if not caches:
            continue
        frame = pd.read_parquet(caches[0], columns=["second", "market_status"])
        longest = 0
        run = 0
        run_end = 0
        best_end = 0
        for second, status in zip(frame["second"].to_list(), frame["market_status"].to_list(), strict=True):
            second = int(second)
            if second < -60 or second >= 900:
                continue
            if status != "ok":
                run += 1
                run_end = second
                if run > longest:
                    longest = run
                    best_end = run_end
            else:
                run = 0
        if longest > 120:
            gap_locs.append({"match_id": match_id, "longest": longest, "end_second": best_end})

    after_480 = sum(1 for g in gap_locs if g["end_second"] >= 480)
    after_0 = sum(1 for g in gap_locs if g["end_second"] >= 0)
    return {
        "validation_start_iso": cutoff,
        "validation_maps": val_n,
        "book_gap_excluded": len(excluded_ids),
        "boundary_maps_within_120s": near,
        "spawn_vs_horn_side_flips_within_120s": flip,
        "catalog_maps": int(len(cat)),
        "maps_with_any_pause": pause_maps,
        "pause_duration_s": pct([float(x) for x in pause_secs]),
        "maps_with_prehorn_pause": prehorn_pause_maps,
        "prehorn_pause_s": pct([float(x) for x in prehorn_pause_secs]),
        "validation_second_is_market_second": second_check,
        "gap_sample_n": len(gap_locs),
        "gap_sample_end_ge_0": after_0,
        "gap_sample_end_ge_480": after_480,
        "gap_end_seconds_p50": pct([float(g["end_second"]) for g in gap_locs]),
    }


def _match_dirs() -> list[Path]:
    return sorted(p for p in TRADER_DIR.iterdir() if p.is_dir() and p.name.isdigit())


def steam_delays(limit: int) -> dict[str, object]:
    medians: list[float] = []
    all_lags: list[float] = []
    maps = 0
    for directory in _match_dirs():
        path = directory / "state.jsonl"
        if not path.exists() and not path.with_name("state.jsonl.gz").exists():
            continue
        lags: list[float] = []
        with open_maybe_gz(path) as handle:
            for line in handle:
                record = json.loads(line)
                match = record["payload"]["match"]
                if match.get("game_state") != 5:
                    continue
                game_time = int(match["game_time"])
                if game_time < 0 or game_time > 540:
                    continue
                received = parse_utc(record["received_at_utc"]).timestamp()
                # horn + game_time == start_timestamp + timestamp
                lags.append(received - (int(match["start_timestamp"]) + int(match["timestamp"])))
        if len(lags) < 30:
            continue
        maps += 1
        medians.append(float(statistics.median(lags)))
        # reservoir-ish: keep every 10th so the pooled sample stays small
        all_lags.extend(lags[::10])
        if maps >= limit:
            break
    return {"maps": maps, "per_map_median": pct(medians), "tick_sample": pct(all_lags)}


def grid_delays(limit: int) -> dict[str, object]:
    medians: list[float] = []
    table_delay_medians: list[float] = []
    tick_sample: list[float] = []
    maps = 0
    profile = GAME_PROFILES["dota"]
    for directory in _match_dirs():
        meta_path = directory / "match.json"
        archive = directory / GRID_STATE_ARCHIVE_FILENAME
        if not meta_path.exists():
            continue
        if not archive.exists() and not archive.with_name(archive.name + ".gz").exists():
            continue
        meta = json.loads(meta_path.read_text())
        if meta.get("game") not in (None, "dota"):
            continue
        market = meta["market"]
        reducer = GridFrameReducer(
            int(meta["map_number"]),
            market["outcome_0_name"],
            market["outcome_1_name"],
            profile,
        )
        delays: list[float] = []
        table_delays: list[int] = []
        try:
            records = list(__import__("trader.grid_archive", fromlist=["iter_grid_archive_records"]).iter_grid_archive_records(archive))
        except Exception:
            continue
        for record in records:
            frame = parse_frame(record["frame"])
            if frame.service == TABLE_SERVICE:
                table_delays.append(frame.delay)
        try:
            for event in replay_grid_records(records, reducer):
                snap = event.snapshot
                if snap.paused or snap.finished or snap.second < 0 or snap.second > 540:
                    continue
                if snap.phase.value != "in_progress":
                    continue
                received = parse_utc(event.received_at_utc).timestamp()
                delays.append(received - (event.horn_unix_seconds + snap.second))
        except Exception:
            continue
        if len(delays) < 30:
            continue
        maps += 1
        medians.append(float(statistics.median(delays)))
        tick_sample.extend(delays[::20])
        if table_delays:
            table_delay_medians.append(float(statistics.median(table_delays)))
        if maps >= limit:
            break
    return {
        "maps": maps,
        "feature_age_s_per_map_median": pct(medians),
        "feature_age_tick_sample": pct(tick_sample),
        "table_frame_delay_per_map_median": pct(table_delay_medians),
    }


def oddin_delays(limit: int) -> dict[str, object]:
    medians: list[float] = []
    transport: list[float] = []
    tick_sample: list[float] = []
    maps = 0
    for directory in _match_dirs():
        meta_path = directory / "match.json"
        archive = directory / ODDIN_STATE_ARCHIVE_FILENAME
        if not meta_path.exists():
            continue
        if not archive.exists() and not archive.with_name(archive.name + ".gz").exists():
            continue
        meta = json.loads(meta_path.read_text())
        market = meta.get("market") or {}
        yes_is_radiant = bool(market.get("yes_is_radiant", True))
        reducer = OddinSnapshotReducer(int(meta["map_number"]), yes_is_radiant)
        delays: list[float] = []
        transports: list[float] = []
        try:
            records = list(iter_oddin_archive_records(archive))
            for event in replay_oddin_records(records, reducer):
                snap = event.snapshot
                if snap.paused or snap.finished or snap.second <= 0 or snap.second > 540:
                    continue
                received = parse_utc(event.received_at_utc).timestamp()
                delays.append(received - (event.horn_unix_seconds + snap.second))
                transports.append(received - snap.server_timestamp)
        except Exception:
            continue
        if len(delays) < 30:
            continue
        maps += 1
        medians.append(float(statistics.median(delays)))
        transport.append(float(statistics.median(transports)))
        tick_sample.extend(delays[::20])
        if maps >= limit:
            break
    return {
        "maps": maps,
        "feature_age_s_per_map_median": pct(medians),
        "receive_minus_last_updated_per_map_median": pct(transport),
        "feature_age_tick_sample": pct(tick_sample),
    }


def index_delays() -> dict[str, object]:
    frame = pd.read_parquet(
        E / "data/archive_index/index.parquet",
        columns=["game", "feed_source", "admission", "delay_s", "delay_evidence", "paused_ticks"],
    )
    dota = frame[frame["game"] == "dota"]
    out: dict[str, object] = {"rows": int(len(dota))}
    by_source: dict[str, object] = {}
    for source, group in dota.groupby(dota["feed_source"].fillna("none")):
        admitted = group[group["admission"] == "admitted"]
        delays = [float(x) for x in admitted["delay_s"].dropna().tolist()]
        by_source[str(source)] = {
            "rows": int(len(group)),
            "admitted": int(len(admitted)),
            "delay_s": pct(delays),
            "evidence": {str(k): int(v) for k, v in admitted["delay_evidence"].fillna("none").value_counts().items()},
        }
    out["by_source"] = by_source
    return out


def main() -> None:
    facts: dict[str, object] = {"dataset": dataset_facts(), "index": index_delays()}
    facts["steam"] = steam_delays(40)
    facts["grid"] = grid_delays(25)
    facts["oddin"] = oddin_delays(25)
    OUT.write_text(json.dumps(facts, indent=2))
    print(OUT)


if __name__ == "__main__":
    main()
