from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections import OrderedDict
from bisect import bisect_right
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from market_data.build_market_data import market_seconds_cache_path
from prepare_dataset.stratz_seconds import MatchDataError, build_minute_states
from shared.constants.dataset import (
    MODEL_START_SECOND,
    TRAIN_END_SECOND_EXCLUSIVE,
    TRAIN_LAG_SECONDS,
    VALIDATION_START_TIME,
)
from shared.constants.paths import (
    MATCH_CATALOG_PATH,
    RAW_TELONEX_POLYMARKET_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.utils.match_catalog import CatalogEntry, load_match_catalog
from shared.utils.stratz import get_stratz_match_reach_data
from shared.utils.telonex_book import (
    US_PER_SECOND,
    find_asof_quote,
    load_token_book,
    resolve_market_pair,
)


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "train-luna"
OUT_JSON = WORK / "future_book_selection.json"
OUT_DROPS = WORK / "future_book_dropped_labels.parquet"
OUT_MINUTE = WORK / "minute_candidate_rows.parquet"
OUT_VAL = WORK / "validation_fixed_candidate_rows.parquet"
ASSET_ROOT = RAW_TELONEX_POLYMARKET_DIR / "book_snapshot_full"
_TIMESTAMP_INDEXES: dict[str, list[tuple[int, int, Path, int]]] = {}
_TIMESTAMP_INDEX_STARTS: dict[str, list[int]] = {}
_ROWGROUP_TIMESTAMPS: OrderedDict[tuple[Path, int], np.ndarray] = OrderedDict()


def map_end_for(entry: CatalogEntry) -> float:
    return entry.ended_at.timestamp()


def as_record(
    *,
    split: str,
    entry: CatalogEntry,
    game_second: int,
    market_second: int,
    state_ts_us: int,
    market_status: str,
    market_p: float | None,
    label: float | None,
    radiant_nw_adv: int,
    radiant_win: bool,
    features: dict[str, float | int],
) -> dict[str, object]:
    target_us = int(state_ts_us) + 300 * US_PER_SECOND
    end_ts = map_end_for(entry)
    return {
        "split": split,
        "match_id": int(entry.match_id),
        "event_id": str(entry.event_id),
        "game_second": int(game_second),
        "market_second": int(market_second),
        "state_ts_us": int(state_ts_us),
        "target_ts_us": target_us,
        "market_status": str(market_status),
        "market_p_radiant": None if market_p is None else float(market_p),
        "signal_market_p_radiant_300s": None if label is None or pd.isna(label) else float(label),
        "radiant_nw_adv": int(radiant_nw_adv),
        "radiant_win": bool(radiant_win),
        "market_favorite_correct": (
            None
            if market_p is None
            else bool((float(market_p) >= 0.5) == bool(radiant_win))
        ),
        "map_end_ts": float(end_ts),
        "target_after_map_end": bool(target_us / 1_000_000 >= end_ts),
        **features,
    }


def minute_rows(entries: list[CatalogEntry], label: str) -> tuple[pd.DataFrame, Counter[str]]:
    output: list[dict[str, object]] = []
    errors: Counter[str] = Counter()
    for n, entry in enumerate(entries, start=1):
        cache_path = market_seconds_cache_path(entry.match_id)
        try:
            match = get_stratz_match_reach_data(entry.match_id)
            states = build_minute_states(match, TRAIN_END_SECOND_EXCLUSIVE)
        except MatchDataError as exc:
            errors[f"match_data:{exc.reason}"] += 1
            continue
        except Exception as exc:
            errors[f"{type(exc).__name__}:{str(exc)[:120]}"] += 1
            continue
        if not states:
            errors["empty_minute_states"] += 1
            continue
        market = pd.read_parquet(
            cache_path,
            columns=[
                "second",
                "state_ts_us",
                "market_status",
                "market_p_radiant",
                "signal_market_p_radiant_300s",
            ],
        )
        by_second = {int(row.second): row for row in market.itertuples(index=False)}
        for state in states:
            market_second = int(state.second + TRAIN_LAG_SECONDS)
            market_row = by_second.get(market_second)
            if market_row is None:
                continue
            feat = {
                "second": int(state.second),
                "radiant_nw_adv": int(state.radiant_nw_adv),
                "radiant_nw": int(state.radiant_nw),
                "dire_nw": int(state.dire_nw),
                "radiant_xp_adv": int(state.radiant_xp_adv),
                "deaths_radiant": int(state.deaths_radiant),
                "deaths_dire": int(state.deaths_dire),
                "top1_nw_adv": int(state.top.top1_nw_adv),
                "radiant_top1_nw_ratio": float(state.top.radiant_top1_nw_ratio),
                "dire_top1_nw_ratio": float(state.top.dire_top1_nw_ratio),
                "market_radiant_prior": float(entry.radiant_prior),
            }
            output.append(
                as_record(
                    split=label,
                    entry=entry,
                    game_second=int(state.second),
                    market_second=market_second,
                    state_ts_us=int(market_row.state_ts_us),
                    market_status=str(market_row.market_status),
                    market_p=market_row.market_p_radiant,
                    label=market_row.signal_market_p_radiant_300s,
                    radiant_nw_adv=int(state.radiant_nw_adv),
                    radiant_win=bool(state.radiant_win),
                    features=feat,
                )
            )
        if n % 250 == 0:
            print("MINUTE_PROGRESS", label, n, "/", len(entries), "candidate_rows", len(output), flush=True)
    return pd.DataFrame(output), errors


def validation_rows(entries: list[CatalogEntry]) -> pd.DataFrame:
    frame = pd.read_parquet(
        VALIDATION_DATASET_PATH,
        columns=[
            "match_id",
            "event_id",
            "second",
            "state_ts_us",
            "market_status",
            "market_p_radiant",
            "signal_market_p_radiant_300s",
            "start_time",
            "radiant_win",
            "radiant_nw_adv",
            "radiant_nw",
            "dire_nw",
            "radiant_xp_adv",
            "deaths_radiant",
            "deaths_dire",
            "top1_nw_adv",
            "radiant_top1_nw_ratio",
            "dire_top1_nw_ratio",
            "market_radiant_prior",
        ],
    )
    selected = frame.loc[
        frame.match_id.isin([entry.match_id for entry in entries])
        & (frame.second >= MODEL_START_SECOND)
        & (frame.second < TRAIN_END_SECOND_EXCLUSIVE)
    ].copy()
    catalog = {entry.match_id: entry for entry in entries}
    selected["game_second"] = selected.second - TRAIN_LAG_SECONDS
    selected["market_second"] = selected.second
    selected["target_ts_us"] = selected.state_ts_us + 300 * US_PER_SECOND
    selected["map_end_ts"] = selected.match_id.map(lambda match_id: map_end_for(catalog[int(match_id)]))
    selected["target_after_map_end"] = (
        selected.target_ts_us / 1_000_000 >= selected.map_end_ts
    )
    selected["market_favorite_correct"] = (
        (selected.market_p_radiant >= 0.5) == selected.radiant_win
    )
    selected["split"] = "validation"
    return selected


def relaxed_price_at_pair(radiant, dire) -> float | None:
    if radiant.quote is None or dire.quote is None:
        return None
    total = radiant.quote.mid + dire.quote.mid
    if total <= 0.0:
        return None
    return radiant.quote.mid / total


def asset_timestamp_index(token_id: str) -> list[tuple[int, int, Path, int]]:
    """Read Parquet statistics only; retain one (min,max,path,row-group) entry per row group."""
    cached = _TIMESTAMP_INDEXES.get(token_id)
    if cached is not None:
        return cached
    result: list[tuple[int, int, Path, int]] = []
    asset_dir = ASSET_ROOT / f"asset_id={token_id}"
    if asset_dir.is_dir():
        for path in sorted(asset_dir.glob("*.parquet")):
            parquet = pq.ParquetFile(path)
            ts_column = parquet.schema_arrow.get_field_index("timestamp_us")
            for rg in range(parquet.metadata.num_row_groups):
                stats = parquet.metadata.row_group(rg).column(ts_column).statistics
                if stats is None or not stats.has_min_max:
                    continue
                result.append((int(stats.min), int(stats.max), path, rg))
    result.sort(key=lambda item: (item[0], item[1]))
    _TIMESTAMP_INDEXES[token_id] = result
    _TIMESTAMP_INDEX_STARTS[token_id] = [item[0] for item in result]
    return result


def latest_raw_timestamp_before(token_id: str, target_us: int) -> int | None:
    """Find the most recent raw snapshot <= target using row-group timestamp metadata."""
    groups = asset_timestamp_index(token_id)
    if not groups:
        return None
    index = bisect_right(_TIMESTAMP_INDEX_STARTS[token_id], target_us) - 1
    while index >= 0:
        minimum, maximum, path, rg = groups[index]
        key = (path, rg)
        timestamps = _ROWGROUP_TIMESTAMPS.get(key)
        if timestamps is None:
            timestamps = pq.ParquetFile(path).read_row_group(rg, columns=["timestamp_us"])["timestamp_us"].to_numpy()
            _ROWGROUP_TIMESTAMPS[key] = timestamps
            if len(_ROWGROUP_TIMESTAMPS) > 64:
                _ROWGROUP_TIMESTAMPS.popitem(last=False)
        else:
            _ROWGROUP_TIMESTAMPS.move_to_end(key)
        eligible = timestamps[timestamps <= target_us]
        if eligible.size:
            return int(eligible.max())
        index -= 1
    return None


def future_status_and_relaxation(group: pd.DataFrame) -> pd.DataFrame:
    match_id = int(group.match_id.iloc[0])
    entry = catalog[match_id]
    start_us = int(group.target_ts_us.min()) - 5 * US_PER_SECOND
    end_us = int(group.target_ts_us.max()) + 30 * US_PER_SECOND
    books = [
        load_token_book(
            token_id=token,
            start_us=start_us,
            end_us=end_us,
            telonex_root=RAW_TELONEX_POLYMARKET_DIR,
        )
        for token in entry.gamma.token_ids
    ]
    radiant_book = None if books[entry.radiant_token_index] is None else books[entry.radiant_token_index]
    dire_book = None if books[1 - entry.radiant_token_index] is None else books[1 - entry.radiant_token_index]
    rows: list[dict[str, object]] = []
    for row in group.itertuples(index=False):
        exact_status = "missing_quote"
        exact_label = None
        relaxed_label = None
        relaxed_offset: int | None = None
        if radiant_book is not None and dire_book is not None:
            target = int(row.target_ts_us)
            radiant = find_asof_quote(radiant_book, target)
            dire = find_asof_quote(dire_book, target)
            pair = resolve_market_pair(radiant, dire)
            exact_status = pair.status
            exact_label = pair.market_p_radiant
            if pair.status == "missing_quote":
                stale_side = False
                for token, result in zip(entry.gamma.token_ids, books, strict=True):
                    asof_status = "missing" if result is None else find_asof_quote(result, target).status
                    if asof_status == "missing":
                        prior_us = latest_raw_timestamp_before(token, target)
                        if prior_us is not None and target - prior_us > 5 * US_PER_SECOND:
                            stale_side = True
                if stale_side:
                    exact_status = "stale_quote"
            if exact_status in {"wide_spread", "inconsistent_pair"}:
                relaxed_label = relaxed_price_at_pair(radiant, dire)
                relaxed_offset = 0 if relaxed_label is not None else None
            else:
                for offset in range(31):
                    later = target + offset * US_PER_SECOND
                    later_pair = resolve_market_pair(
                        find_asof_quote(radiant_book, later),
                        find_asof_quote(dire_book, later),
                    )
                    if later_pair.status == "ok":
                        relaxed_label = later_pair.market_p_radiant
                        relaxed_offset = offset
                        break
        rows.append(
            {
                "split": str(row.split),
                "match_id": match_id,
                "game_second": int(row.game_second),
                "market_second": int(row.market_second),
                "target_ts_us": int(row.target_ts_us),
                "future_status": exact_status,
                "exact_label": exact_label,
                "relaxed_label": relaxed_label,
                "relaxed_offset_seconds": relaxed_offset,
                "market_p_radiant": float(row.market_p_radiant),
                "radiant_nw_adv": int(row.radiant_nw_adv),
                "radiant_win": bool(row.radiant_win),
                "market_favorite_correct": bool(row.market_favorite_correct),
                "target_after_map_end": bool(row.target_after_map_end),
            }
        )
    return pd.DataFrame(rows)


def second_bucket(second: int) -> str:
    if second < 0:
        return "prehorn [-60,0)"
    if second < 120:
        return "0-119"
    if second < 300:
        return "120-299"
    if second < 480:
        return "300-479"
    return "480-599"


def nw_bucket(nw_adv: int) -> str:
    value = abs(int(nw_adv))
    if value < 3_000:
        return "<3k"
    if value < 10_000:
        return "3k-<10k"
    return ">=10k"


def price_bucket(price: float) -> str:
    if price < 0.15:
        return "<0.15"
    if price < 0.50:
        return "0.15-<0.50"
    if price < 0.85:
        return "0.50-<0.85"
    return ">=0.85"


def summarize_group(name: str, frame: pd.DataFrame, drops: pd.DataFrame) -> dict[str, object]:
    current_ok = frame.loc[frame.market_status.eq("ok")].copy()
    is_kept = current_ok.signal_market_p_radiant_300s.notna()
    dropped = current_ok.loc[~is_kept].merge(
        drops[["match_id", "market_second", "future_status", "relaxed_label", "relaxed_offset_seconds"]],
        on=["match_id", "market_second"],
        how="left",
        validate="one_to_one",
    )
    labeled_dropped = dropped.loc[dropped.relaxed_label.notna()].copy()
    dropped["market_p_radiant"] = dropped.market_p_radiant.astype(float)
    status_counts = Counter(dropped.future_status.fillna("not_reconstructed").tolist())
    result: dict[str, object] = {
        "rows_any_current_status": int(len(frame)),
        "current_ok_rows": int(len(current_ok)),
        "current_not_ok_rows": int(len(frame) - len(current_ok)),
        "kept_labeled_rows": int(is_kept.sum()),
        "dropped_current_ok_label_rows": int((~is_kept).sum()),
        "drop_share_of_current_ok": float((~is_kept).mean()) if len(current_ok) else None,
        "maps_with_candidate_rows": int(frame.match_id.nunique()),
        "maps_with_current_ok_rows": int(current_ok.match_id.nunique()),
        "maps_with_no_kept_rows_but_current_ok": int(
            sum(
                int(match_id) not in set(current_ok.loc[is_kept, "match_id"].astype(int))
                for match_id in current_ok.match_id.unique()
            )
        ),
        "future_status_counts_among_dropped": dict(status_counts),
        "relaxed_label_coverage": int(len(labeled_dropped)),
        "relaxed_label_coverage_share": float(len(labeled_dropped) / len(dropped)) if len(dropped) else None,
        "relaxed_offset_seconds_counts": {
            str(k): int(v)
            for k, v in Counter(labeled_dropped.relaxed_offset_seconds.astype(int).tolist()).items()
        },
    }
    # Keep the model clock as the shared time-regime key in both minute and exact-second rows.
    current_ok["second_bucket"] = current_ok.game_second.map(second_bucket)
    current_ok["nw_bucket"] = current_ok.radiant_nw_adv.map(nw_bucket)
    current_ok["market_p_bucket"] = current_ok.market_p_radiant.map(price_bucket)
    current_ok["favorite_result"] = np.where(
        current_ok.market_favorite_correct.astype(bool), "favorite_correct", "favorite_wrong"
    )
    current_ok["is_dropped"] = current_ok.signal_market_p_radiant_300s.isna()
    regimes: dict[str, object] = {}
    for field in ["second_bucket", "nw_bucket", "market_p_bucket", "favorite_result", "target_after_map_end"]:
        table: dict[str, object] = {}
        for key, group in current_ok.groupby(field, dropna=False, sort=True):
            n = int(group.is_dropped.sum())
            table[str(key)] = {
                "current_ok_rows": int(len(group)),
                "dropped": n,
                "drop_share": float(n / len(group)) if len(group) else None,
            }
        regimes[field] = table
    result["drop_share_by_regime"] = regimes

    kept = current_ok.loc[~current_ok.is_dropped].copy()
    kept["move"] = (
        kept.signal_market_p_radiant_300s.astype(float)
        - kept.market_p_radiant.astype(float)
    )
    labeled_dropped["move"] = (
        labeled_dropped.relaxed_label.astype(float)
        - labeled_dropped.market_p_radiant.astype(float)
    )
    move_groups = {"kept_exact_label": kept, "dropped_relaxed_label": labeled_dropped}
    move_stats: dict[str, object] = {}
    for group_label, group in move_groups.items():
        move = group.move.to_numpy(dtype=np.float64)
        move_stats[group_label] = {
            "rows": int(len(group)),
            "maps": int(group.match_id.nunique()),
            "mean_move_cents": float(np.mean(move) * 100) if len(move) else None,
            "median_abs_move_cents": float(np.median(np.abs(move)) * 100) if len(move) else None,
            "p90_abs_move_cents": float(np.quantile(np.abs(move), 0.90) * 100) if len(move) else None,
            "p95_abs_move_cents": float(np.quantile(np.abs(move), 0.95) * 100) if len(move) else None,
            "share_abs_move_ge_5c": float(np.mean(np.abs(move) >= 0.05)) if len(move) else None,
            "share_abs_move_ge_10c": float(np.mean(np.abs(move) >= 0.10)) if len(move) else None,
            "share_target_after_map_end": float(group.target_after_map_end.astype(bool).mean()) if len(group) else None,
        }
    result["move_comparison"] = move_stats
    return result


catalog = load_match_catalog(MATCH_CATALOG_PATH)
all_entries = list(catalog.values())
train_entries = [
    entry
    for entry in all_entries
    if entry.start_time < VALIDATION_START_TIME and market_seconds_cache_path(entry.match_id).is_file()
]
validation_entries = [
    entry
    for entry in all_entries
    if entry.start_time >= VALIDATION_START_TIME and market_seconds_cache_path(entry.match_id).is_file()
]
train_minute, train_errors = minute_rows(train_entries, "research_train")
val_minute, val_errors = minute_rows(validation_entries, "validation_minute")
val_exact = validation_rows(validation_entries)
minute = pd.concat([train_minute, val_minute], ignore_index=True)
minute.to_parquet(OUT_MINUTE, index=False)
val_exact.to_parquet(OUT_VAL, index=False)
print("CANDIDATE_COUNTS", {
    "train_maps_with_cache": len(train_entries),
    "validation_maps_with_cache": len(validation_entries),
    "train_minute_candidate_rows": len(train_minute),
    "validation_minute_candidate_rows": len(val_minute),
    "validation_exact_rows_fixed_window": len(val_exact),
    "train_builder_errors": dict(train_errors),
    "validation_builder_errors": dict(val_errors),
}, flush=True)

# Raw future-book lookup is limited to candidate rows that are current-OK and have
# no normal +300 label. One map is loaded at a time, below the audit's memory cap.
drop_rows = pd.concat(
    [
        minute.loc[minute.market_status.eq("ok") & minute.signal_market_p_radiant_300s.isna()],
        val_exact.loc[val_exact.market_status.eq("ok") & val_exact.signal_market_p_radiant_300s.isna()],
    ],
    ignore_index=True,
)
drop_rows = drop_rows.drop_duplicates(["split", "match_id", "market_second"])
drop_frames: list[pd.DataFrame] = []
for n, (match_id, group) in enumerate(drop_rows.groupby("match_id", sort=False), start=1):
    drop_frames.append(future_status_and_relaxation(group))
    if n % 100 == 0:
        print("RAW_BOOK_PROGRESS", n, "/", drop_rows.match_id.nunique(), "drop_rows", len(drop_rows), flush=True)
drops = pd.concat(drop_frames, ignore_index=True) if drop_frames else pd.DataFrame()
drops.to_parquet(OUT_DROPS, index=False)

train_stats = summarize_group("research_train", train_minute, drops.loc[drops.split.eq("research_train")])
val_stats = summarize_group("validation", val_exact, drops.loc[drops.split.eq("validation")])
production_rows = minute.copy()
production_rows["split"] = "production"
production_drop_frames = drops.loc[drops.split.isin(["research_train", "validation_minute"])].copy()
production_drop_frames["split"] = "production"
production_stats = summarize_group("production", production_rows, production_drop_frames)

status_mismatch = int((drops.exact_label.notna()).sum()) if "exact_label" in drops.columns else 0
result = {
    "source": {
        "catalog_train_maps_with_market_cache": len(train_entries),
        "catalog_validation_maps_with_market_cache": len(validation_entries),
        "fixed_validation_window": [MODEL_START_SECOND, TRAIN_END_SECOND_EXCLUSIVE],
        "target_horizon_seconds": 300,
        "train_lag_seconds": TRAIN_LAG_SECONDS,
        "status_basis": "exact raw-book as-of at each row state timestamp + 300 s",
        "relaxation": "wide/inconsistent uses same-target two-sided mid normalized without spread/pair gate; stale/missing uses first OK exact-book second in [target,target+30s]",
        "builder_errors": {"train": dict(train_errors), "validation": dict(val_errors)},
    },
    "research_train": train_stats,
    "validation": val_stats,
    "production": production_stats,
    "dropped_label_rows": int(len(drops)),
    "exact_future_status_counts": {str(k): int(v) for k, v in Counter(drops.future_status.tolist()).items()},
    "raw_future_status_ok_but_label_missing": status_mismatch,
}
OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True))
print("RESULT_JSON", OUT_JSON)
print(json.dumps({"research_train": train_stats, "validation": val_stats, "production": production_stats, "exact_future_status_counts": result["exact_future_status_counts"]}, indent=2, sort_keys=True))
