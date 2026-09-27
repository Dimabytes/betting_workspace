from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pandas as pd

from shared.constants.dataset import MODEL_TARGET_HORIZON_SECONDS, TRAIN_LAG_SECONDS
from shared.constants.paths import RAW_TELONEX_POLYMARKET_DIR, TRAINING_DATASET_PATH, VALIDATION_DATASET_PATH
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import datetime_to_ns, get_state_available_ts
from shared.utils.telonex_book import NS_PER_US, US_PER_SECOND, find_asof_quote, load_token_book, lookup_market_p_after, resolve_market_pair
from market_data.build_market_data import market_seconds_cache_path


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "data-luna"


def filtered(book, mode: str):
    bids = []
    asks = []
    for bid, ask in zip(book.bids, book.asks, strict=True):
        invalid = bid is not None and ask is not None and (bid > ask or (mode == "ge" and bid == ask))
        bids.append(None if invalid else bid)
        asks.append(None if invalid else ask)
    return replace(book, bids=tuple(bids), asks=tuple(asks))


def price_changed(old, new) -> bool:
    old_present = pd.notna(old)
    new_present = new is not None and pd.notna(new)
    if old_present != new_present:
        return True
    if not old_present:
        return False
    return abs(float(old) - float(new)) > 1e-9


def summarize_change(out: Counter, prefix: str, old, new, *, status: str | None = None, old_status=None, new_status=None) -> None:
    if status is not None:
        changed = old_status != new_status
        out[f"{prefix}_status_changed"] += int(changed)
        out[f"{prefix}_ok_to_nonok"] += int(old_status == "ok" and new_status != "ok")
        out[f"{prefix}_nonok_to_ok"] += int(old_status != "ok" and new_status == "ok")
    changed = price_changed(old, new)
    out[f"{prefix}_price_changed"] += int(changed)
    if pd.notna(old) and new is not None and pd.notna(new):
        delta_cents = abs(float(old) - float(new)) * 100.0
        out[f"{prefix}_price_delta_sum_cents"] += delta_cents
        out[f"{prefix}_price_delta_ge1c"] += int(delta_cents >= 1.0 - 1e-9)
        out[f"{prefix}_price_delta_max_cents"] = max(out[f"{prefix}_price_delta_max_cents"], delta_cents)


def main() -> None:
    sample = pd.read_csv(WORK / "crossed_book_sample.csv")
    per_map = pd.read_csv(WORK / "crossed_sample_per_map.csv")
    possible = per_map[
        per_map[["market_ok_cross_any", "market_ok_locked_any", "val_label_cross_any", "val_label_locked_any"]].gt(0).any(axis=1)
    ]
    ids = set(possible.match_id.astype("int64"))
    catalog = load_match_catalog(Path("data/new_processed/match_catalog/match_catalog.parquet"))
    train = pd.read_parquet(TRAINING_DATASET_PATH, columns=["match_id", "second", "signal_market_p_radiant_300s"])
    train = train[train.match_id.astype("int64").isin(ids)]
    validation = pd.read_parquet(
        VALIDATION_DATASET_PATH,
        columns=["match_id", "second", "state_ts_us", "market_status", "market_p_radiant", "signal_market_p_radiant_300s"],
    )
    validation = validation[validation.match_id.astype("int64").isin(ids)]
    train_by_id = {int(key): frame.to_dict("records") for key, frame in train.groupby("match_id", sort=False)}
    val_by_id = {int(key): frame.to_dict("records") for key, frame in validation.groupby("match_id", sort=False)}
    modes = ("strict", "ge")
    totals = {mode: Counter() for mode in modes}
    per_map_rows = []
    horizon_us = MODEL_TARGET_HORIZON_SECONDS * US_PER_SECOND

    for ordinal, sample_row in enumerate(sample[sample.match_id.astype("int64").isin(ids)].itertuples(index=False), start=1):
        match_id = int(sample_row.match_id)
        entry = catalog[match_id]
        state_start = datetime_to_ns(get_state_available_ts(horn=entry.horn_at, second=-60, pauses=entry.pauses)) // NS_PER_US
        state_end = datetime_to_ns(get_state_available_ts(horn=entry.horn_at, second=entry.duration, pauses=entry.pauses)) // NS_PER_US
        books = [
            load_token_book(
                token_id=token_id,
                start_us=state_start - 5 * US_PER_SECOND,
                end_us=state_end + horizon_us,
                telonex_root=RAW_TELONEX_POLYMARKET_DIR,
            )
            for token_id in entry.gamma.token_ids
        ]
        if books[0] is None or books[1] is None:
            raise RuntimeError(f"sample map {match_id} missing a book")
        cache = pd.read_parquet(market_seconds_cache_path(match_id))
        cache_by_second = {int(row.second): row._asdict() for row in cache.itertuples(index=False)}
        map_counts = {mode: Counter() for mode in modes}
        for mode in modes:
            alt_books = [filtered(book, mode) for book in books]
            radiant_book = alt_books[entry.radiant_token_index]
            dire_book = alt_books[1 - entry.radiant_token_index]
            for row in cache_by_second.values():
                anchor_us = int(row["state_ts_us"])
                alt = resolve_market_pair(find_asof_quote(radiant_book, anchor_us), find_asof_quote(dire_book, anchor_us))
                summarize_change(
                    map_counts[mode], "cache_current", row["market_p_radiant"], alt.market_p_radiant,
                    status="x", old_status=row["market_status"], new_status=alt.status,
                )
                alt_label = lookup_market_p_after(radiant_book, dire_book, anchor_us, MODEL_TARGET_HORIZON_SECONDS)
                summarize_change(map_counts[mode], "cache_label", row["signal_market_p_radiant_300s"], alt_label)

            for row in train_by_id.get(match_id, []):
                market_second = int(row["second"]) + TRAIN_LAG_SECONDS
                cache_row = cache_by_second[market_second]
                anchor_us = int(cache_row["state_ts_us"])
                alt = resolve_market_pair(find_asof_quote(radiant_book, anchor_us), find_asof_quote(dire_book, anchor_us))
                summarize_change(
                    map_counts[mode], "training_current", cache_row["market_p_radiant"], alt.market_p_radiant,
                    status="x", old_status=cache_row["market_status"], new_status=alt.status,
                )
                alt_label = lookup_market_p_after(radiant_book, dire_book, anchor_us, MODEL_TARGET_HORIZON_SECONDS)
                summarize_change(map_counts[mode], "training_label", row["signal_market_p_radiant_300s"], alt_label)

            for row in val_by_id.get(match_id, []):
                anchor_us = int(row["state_ts_us"])
                alt = resolve_market_pair(find_asof_quote(radiant_book, anchor_us), find_asof_quote(dire_book, anchor_us))
                prefix = "validation_entry_current" if int(row["second"]) <= 489 else "validation_current"
                summarize_change(
                    map_counts[mode], prefix, row["market_p_radiant"], alt.market_p_radiant,
                    status="x", old_status=row["market_status"], new_status=alt.status,
                )
                alt_label = lookup_market_p_after(radiant_book, dire_book, anchor_us, MODEL_TARGET_HORIZON_SECONDS)
                prefix_label = "validation_entry_label" if int(row["second"]) <= 489 else "validation_label"
                summarize_change(map_counts[mode], prefix_label, row["signal_market_p_radiant_300s"], alt_label)
            totals[mode].update(map_counts[mode])
            row_out = {"match_id": match_id, "month": sample_row.month, "era": sample_row.era, "mode": mode, **map_counts[mode]}
            per_map_rows.append(row_out)
        if ordinal % 10 == 0 or ordinal == len(ids):
            print(f"counterfactual maps {ordinal}/{len(ids)}", flush=True)

    out_path = WORK / "crossed_filter_counterfactual.csv"
    pd.DataFrame(per_map_rows).to_csv(out_path, index=False)
    summary = [f"maps_with_flagged_baseline_asof={len(ids)} train_rows_in_candidates={len(train)} val_rows_in_candidates={len(validation)}"]
    for mode in modes:
        summary.append(f"MODE {mode} (strict filters bid>ask; ge filters bid>=ask)")
        for name, value in sorted(totals[mode].items()):
            if name.endswith("price_delta_sum_cents"):
                continue
            summary.append(f"{name}={value}")
    summary.append(f"details={out_path}")
    summary_path = WORK / "crossed_filter_counterfactual_summary.txt"
    summary_path.write_text("\n".join(summary) + "\n")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
