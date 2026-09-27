from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from shared.constants.dataset import MODEL_TARGET_HORIZON_SECONDS, TRAIN_LAG_SECONDS
from shared.constants.paths import RAW_TELONEX_POLYMARKET_DIR, TRAINING_DATASET_PATH, VALIDATION_DATASET_PATH
from shared.utils.match_catalog import load_match_catalog
from shared.utils.match_time import datetime_to_ns, get_state_available_ts
from shared.utils.telonex_book import (
    NS_PER_US,
    US_PER_SECOND,
    find_asof_quote,
    load_token_book,
    normalize_pair_mids,
    resolve_market_pair,
)
from market_data.build_market_data import market_seconds_cache_path


ROOT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
WORK = ROOT / "work" / "data-luna"
SAMPLE_PATH = WORK / "crossed_book_sample.csv"
MAP_OUTPUT = WORK / "crossed_sample_per_map.csv"
SUMMARY_OUTPUT = WORK / "crossed_sample_summary.txt"
ERROR_OUTPUT = WORK / "crossed_sample_mid_errors.csv"
LABEL_HORIZON_US = MODEL_TARGET_HORIZON_SECONDS * US_PER_SECOND


def _quote(book, target_us: int):
    result = find_asof_quote(book, target_us)
    return result.quote


def _flags(radiant_book, dire_book, target_us: int) -> dict[str, object]:
    radiant = _quote(radiant_book, target_us)
    dire = _quote(dire_book, target_us)
    if radiant is None or dire is None:
        return {
            "pair_quotes": False,
            "radiant": radiant,
            "dire": dire,
            "cross_any": False,
            "locked_any": False,
            "both_cross": False,
            "pair_sum_pass": False,
            "p_radiant": None,
        }
    radiant_cross = radiant.bid > radiant.ask
    dire_cross = dire.bid > dire.ask
    radiant_locked = radiant.bid == radiant.ask
    dire_locked = dire.bid == dire.ask
    pair_sum = radiant.mid + dire.mid
    return {
        "pair_quotes": True,
        "radiant": radiant,
        "dire": dire,
        "radiant_cross": radiant_cross,
        "dire_cross": dire_cross,
        "radiant_locked": radiant_locked,
        "dire_locked": dire_locked,
        "cross_any": radiant_cross or dire_cross,
        "locked_any": radiant_locked or dire_locked,
        "both_cross": radiant_cross and dire_cross,
        "pair_sum_pass": pair_sum > 0.0 and abs(pair_sum - 1.0) <= 0.05,
        "p_radiant": normalize_pair_mids(radiant_mid=radiant.mid, dire_mid=dire.mid, tolerance=0.05),
    }


def _nearest_uncrossed(book, target_us: int):
    stamps = np.asarray(book.timestamps_us, dtype=np.int64)
    bids = np.asarray([np.nan if value is None else value for value in book.bids], dtype=np.float64)
    asks = np.asarray([np.nan if value is None else value for value in book.asks], dtype=np.float64)
    good = np.isfinite(bids) & np.isfinite(asks) & (bids < asks)
    good_indexes = np.flatnonzero(good)
    if not len(good_indexes):
        return None
    good_stamps = stamps[good_indexes]
    insert = int(np.searchsorted(good_stamps, target_us, side="left"))
    candidate_positions = [pos for pos in (insert - 1, insert) if 0 <= pos < len(good_indexes)]
    chosen = min(candidate_positions, key=lambda pos: (abs(int(good_stamps[pos]) - target_us), int(good_stamps[pos])))
    index = int(good_indexes[chosen])
    mid = (float(bids[index]) + float(asks[index])) / 2.0
    return int(stamps[index]), mid


def _row_metric(rows, books, cache_by_second, row_type: str) -> Counter:
    out = Counter()
    for row in rows:
        second = int(row["second"])
        if row_type == "train":
            market_second = second + TRAIN_LAG_SECONDS
            cache_row = cache_by_second.get(market_second)
            if cache_row is None:
                out["missing_cache_rows"] += 1
                continue
            anchor_us = int(cache_row["state_ts_us"])
        else:
            market_second = second
            anchor_us = int(row["state_ts_us"])
            cache_row = cache_by_second.get(market_second)

        flags = _flags(*books, anchor_us)
        out["rows"] += 1
        out["pair_quotes"] += int(bool(flags["pair_quotes"]))
        out["cross_any"] += int(bool(flags["cross_any"]))
        out["locked_any"] += int(bool(flags["locked_any"]))
        out["cross_or_locked"] += int(bool(flags["cross_any"] or flags["locked_any"]))
        out["both_cross"] += int(bool(flags["both_cross"]))
        if row_type == "val":
            is_ok = bool(cache_row is not None and cache_row["market_status"] == "ok")
            out["ok_rows"] += int(is_ok)
            if is_ok:
                out["ok_cross_or_locked"] += int(bool(flags["cross_any"] or flags["locked_any"]))
            if second <= 489:
                out["entry_rows"] += 1
                out["entry_pair_quotes"] += int(bool(flags["pair_quotes"]))
                out["entry_cross_any"] += int(bool(flags["cross_any"]))
                out["entry_locked_any"] += int(bool(flags["locked_any"]))
                out["entry_cross_or_locked"] += int(bool(flags["cross_any"] or flags["locked_any"]))
                out["entry_ok_rows"] += int(is_ok)
                if is_ok:
                    out["entry_ok_cross_or_locked"] += int(bool(flags["cross_any"] or flags["locked_any"]))

        label = row.get("signal_market_p_radiant_300s")
        if pd.notna(label):
            out["label_rows"] += 1
            label_flags = _flags(*books, anchor_us + LABEL_HORIZON_US)
            out["label_pair_quotes"] += int(bool(label_flags["pair_quotes"]))
            out["label_cross_any"] += int(bool(label_flags["cross_any"]))
            out["label_locked_any"] += int(bool(label_flags["locked_any"]))
            out["label_cross_or_locked"] += int(bool(label_flags["cross_any"] or label_flags["locked_any"]))
            out["label_both_cross"] += int(bool(label_flags["both_cross"]))
    return out


def main() -> None:
    sample = pd.read_csv(SAMPLE_PATH)
    catalog = load_match_catalog(Path("data/new_processed/match_catalog/match_catalog.parquet"))
    ids = set(sample.match_id.astype("int64"))

    training = pd.read_parquet(
        TRAINING_DATASET_PATH,
        columns=["match_id", "second", "signal_market_p_radiant_300s"],
    )
    training = training[training.match_id.astype("int64").isin(ids)]
    validation = pd.read_parquet(
        VALIDATION_DATASET_PATH,
        columns=["match_id", "second", "state_ts_us", "market_status", "signal_market_p_radiant_300s"],
    )
    validation = validation[validation.match_id.astype("int64").isin(ids)]
    train_by_id = {int(match_id): frame.to_dict("records") for match_id, frame in training.groupby("match_id", sort=False)}
    val_by_id = {int(match_id): frame.to_dict("records") for match_id, frame in validation.groupby("match_id", sort=False)}

    map_rows: list[dict[str, object]] = []
    monthly = defaultdict(Counter)
    errors: list[dict[str, object]] = []
    rebuild_diffs = Counter()

    fields = [
        "match_id", "month", "era", "token0_snapshots", "token0_two_sided", "token0_crossed", "token0_locked",
        "token1_snapshots", "token1_two_sided", "token1_crossed", "token1_locked",
        "market_total", "market_ok", "market_pair_quotes", "market_ok_cross_any", "market_ok_locked_any",
        "market_ok_cross_or_locked", "market_both_cross", "market_both_cross_pair_sum_pass",
        "train_rows", "train_current_cross_or_locked", "train_current_cross_any", "train_current_locked_any",
        "train_label_rows", "train_label_cross_or_locked", "train_label_cross_any", "train_label_locked_any",
        "val_rows", "val_pair_quotes", "val_cross_or_locked", "val_ok", "val_ok_cross_or_locked",
        "val_label_rows", "val_label_cross_or_locked", "val_label_cross_any", "val_label_locked_any",
        "entry_rows_le489", "entry_pair_quotes_le489", "entry_cross_or_locked_le489", "entry_ok_le489",
        "entry_ok_cross_or_locked_le489",
    ]
    MAP_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with MAP_OUTPUT.open("w", newline="") as map_file:
        writer = csv.DictWriter(map_file, fieldnames=fields)
        writer.writeheader()
        for ordinal, sample_row in enumerate(sample.itertuples(index=False), start=1):
            match_id = int(sample_row.match_id)
            entry = catalog[match_id]
            state_start = datetime_to_ns(get_state_available_ts(horn=entry.horn_at, second=-60, pauses=entry.pauses)) // NS_PER_US
            state_end = datetime_to_ns(get_state_available_ts(horn=entry.horn_at, second=entry.duration, pauses=entry.pauses)) // NS_PER_US
            load_start = state_start - 5 * US_PER_SECOND
            load_end = state_end + MODEL_TARGET_HORIZON_SECONDS * US_PER_SECOND
            books = [
                load_token_book(token_id=token_id, start_us=load_start, end_us=load_end, telonex_root=RAW_TELONEX_POLYMARKET_DIR)
                for token_id in entry.gamma.token_ids
            ]
            if books[0] is None or books[1] is None:
                raise RuntimeError(f"sample map {match_id} missing a token book")

            token_raw = []
            for book in books:
                bids = np.asarray([np.nan if value is None else value for value in book.bids], dtype=np.float64)
                asks = np.asarray([np.nan if value is None else value for value in book.asks], dtype=np.float64)
                paired = np.isfinite(bids) & np.isfinite(asks)
                token_raw.append({
                    "snapshots": len(book.timestamps_us),
                    "two_sided": int(paired.sum()),
                    "crossed": int((paired & (bids > asks)).sum()),
                    "locked": int((paired & (bids == asks)).sum()),
                })

            cache = pd.read_parquet(market_seconds_cache_path(match_id))
            cache_by_second = {int(row.second): row._asdict() for row in cache.itertuples(index=False)}
            market = Counter()
            for cache_row in cache_by_second.values():
                market["total"] += 1
                market["ok"] += int(cache_row["market_status"] == "ok")
                anchor_us = int(cache_row["state_ts_us"])
                flags = _flags(books[entry.radiant_token_index], books[1 - entry.radiant_token_index], anchor_us)
                market["pair_quotes"] += int(bool(flags["pair_quotes"]))
                market["ok_cross_any"] += int(cache_row["market_status"] == "ok" and bool(flags["cross_any"]))
                market["ok_locked_any"] += int(cache_row["market_status"] == "ok" and bool(flags["locked_any"]))
                market["ok_cross_or_locked"] += int(cache_row["market_status"] == "ok" and bool(flags["cross_any"] or flags["locked_any"]))
                market["both_cross"] += int(bool(flags["both_cross"]))
                market["both_cross_pair_sum_pass"] += int(bool(flags["both_cross"] and flags["pair_sum_pass"]))
                market_result = resolve_market_pair(
                    find_asof_quote(books[entry.radiant_token_index], anchor_us),
                    find_asof_quote(books[1 - entry.radiant_token_index], anchor_us),
                )
                if market_result.status != cache_row["market_status"]:
                    rebuild_diffs["market_status"] += 1
                old_price = cache_row["market_p_radiant"]
                new_price = market_result.market_p_radiant
                if pd.isna(old_price) != (new_price is None) or (
                    old_price is not None and pd.notna(old_price) and new_price is not None and abs(float(old_price) - new_price) > 1e-9
                ):
                    rebuild_diffs["market_mid"] += 1

                if cache_row["market_status"] == "ok" and (flags["cross_any"] or flags["locked_any"]):
                    radiant = flags["radiant"]
                    dire = flags["dire"]
                    uncrossed_r = _nearest_uncrossed(books[entry.radiant_token_index], anchor_us)
                    uncrossed_d = _nearest_uncrossed(books[1 - entry.radiant_token_index], anchor_us)
                    p_uncrossed = None
                    if uncrossed_r is not None and uncrossed_d is not None:
                        p_uncrossed = normalize_pair_mids(radiant_mid=uncrossed_r[1], dire_mid=uncrossed_d[1], tolerance=0.05)
                    p_current = float(old_price)
                    p_error_cents = None if p_uncrossed is None else abs(p_current - p_uncrossed) * 100.0
                    for token_index, quote, nearest in (
                        (entry.radiant_token_index, radiant, uncrossed_r),
                        (1 - entry.radiant_token_index, dire, uncrossed_d),
                    ):
                        if quote.bid > quote.ask or quote.bid == quote.ask:
                            token_error = None if nearest is None else abs(quote.mid - nearest[1]) * 100.0
                            errors.append({
                                "match_id": match_id,
                                "month": sample_row.month,
                                "era": sample_row.era,
                                "market_second": int(cache_row["second"]),
                                "token_index": token_index,
                                "condition": "crossed" if quote.bid > quote.ask else "locked",
                                "mid": quote.mid,
                                "nearest_uncrossed_mid": None if nearest is None else nearest[1],
                                "nearest_uncrossed_distance_s": None if nearest is None else abs(nearest[0] - anchor_us) / US_PER_SECOND,
                                "token_mid_error_cents": token_error,
                                "market_p": p_current,
                                "nearest_uncrossed_pair_p": p_uncrossed,
                                "pair_p_error_cents": p_error_cents,
                            })

            train_metrics = _row_metric(train_by_id.get(match_id, []), (books[entry.radiant_token_index], books[1 - entry.radiant_token_index]), cache_by_second, "train")
            val_metrics = _row_metric(val_by_id.get(match_id, []), (books[entry.radiant_token_index], books[1 - entry.radiant_token_index]), cache_by_second, "val")
            result = {
                "match_id": match_id, "month": sample_row.month, "era": sample_row.era,
                "token0_snapshots": token_raw[0]["snapshots"], "token0_two_sided": token_raw[0]["two_sided"],
                "token0_crossed": token_raw[0]["crossed"], "token0_locked": token_raw[0]["locked"],
                "token1_snapshots": token_raw[1]["snapshots"], "token1_two_sided": token_raw[1]["two_sided"],
                "token1_crossed": token_raw[1]["crossed"], "token1_locked": token_raw[1]["locked"],
                "market_total": market["total"], "market_ok": market["ok"], "market_pair_quotes": market["pair_quotes"],
                "market_ok_cross_any": market["ok_cross_any"], "market_ok_locked_any": market["ok_locked_any"],
                "market_ok_cross_or_locked": market["ok_cross_or_locked"], "market_both_cross": market["both_cross"],
                "market_both_cross_pair_sum_pass": market["both_cross_pair_sum_pass"],
                "train_rows": train_metrics["rows"], "train_current_cross_or_locked": train_metrics["cross_or_locked"],
                "train_current_cross_any": train_metrics["cross_any"], "train_current_locked_any": train_metrics["locked_any"],
                "train_label_rows": train_metrics["label_rows"], "train_label_cross_or_locked": train_metrics["label_cross_or_locked"],
                "train_label_cross_any": train_metrics["label_cross_any"], "train_label_locked_any": train_metrics["label_locked_any"],
                "val_rows": val_metrics["rows"], "val_pair_quotes": val_metrics["pair_quotes"],
                "val_cross_or_locked": val_metrics["cross_or_locked"], "val_ok": val_metrics["ok_rows"],
                "val_ok_cross_or_locked": val_metrics["ok_cross_or_locked"], "val_label_rows": val_metrics["label_rows"],
                "val_label_cross_or_locked": val_metrics["label_cross_or_locked"], "val_label_cross_any": val_metrics["label_cross_any"],
                "val_label_locked_any": val_metrics["label_locked_any"], "entry_rows_le489": val_metrics["entry_rows"],
                "entry_pair_quotes_le489": val_metrics["entry_pair_quotes"], "entry_cross_or_locked_le489": val_metrics["entry_cross_or_locked"],
                "entry_ok_le489": val_metrics["entry_ok_rows"], "entry_ok_cross_or_locked_le489": val_metrics["entry_ok_cross_or_locked"],
            }
            writer.writerow(result)
            map_rows.append(result)
            key = (sample_row.month, sample_row.era)
            for name, value in result.items():
                if isinstance(value, (int, np.integer)) and name not in ("match_id",):
                    monthly[key][name] += int(value)
            if ordinal % 10 == 0 or ordinal == len(sample):
                map_file.flush()
                print(f"processed {ordinal}/{len(sample)} maps", flush=True)

    pd.DataFrame(errors).to_csv(ERROR_OUTPUT, index=False)
    summary_lines = []
    summary_lines.append(f"sample_maps={len(sample)} training_rows={len(training)} validation_rows={len(validation)}")
    summary_lines.append(f"cache_rebuild_diffs={dict(rebuild_diffs)}")
    summary_lines.append("MONTH_ERA raw snapshots token crossed/locked; rates cross/locked over two-sided; cache and row counts")
    for key in sorted(monthly):
        m, era = key
        c = monthly[key]
        raw_total = c["token0_snapshots"] + c["token1_snapshots"]
        paired_total = c["token0_two_sided"] + c["token1_two_sided"]
        cross_total = c["token0_crossed"] + c["token1_crossed"]
        locked_total = c["token0_locked"] + c["token1_locked"]
        summary_lines.append(
            f"{m} {era} maps={int(sample[(sample.month==m)&(sample.era==era)].shape[0])} raw={raw_total} paired={paired_total} "
            f"cross={cross_total} ({cross_total/max(paired_total,1):.9g}/paired,{cross_total/max(raw_total,1):.9g}/all) "
            f"locked={locked_total} ({locked_total/max(paired_total,1):.9g}/paired,{locked_total/max(raw_total,1):.9g}/all) "
            f"ok_s={c['market_ok']} flagged={c['market_ok_cross_or_locked']} cross={c['market_ok_cross_any']} locked={c['market_ok_locked_any']} "
            f"both_cross={c['market_both_cross']} pair_pass={c['market_both_cross_pair_sum_pass']} "
            f"train={c['train_rows']} tcrosslock={c['train_current_cross_or_locked']} tlab={c['train_label_rows']} tlabflag={c['train_label_cross_or_locked']} "
            f"val={c['val_rows']} valok={c['val_ok']} vflag={c['val_cross_or_locked']} vokflag={c['val_ok_cross_or_locked']} "
            f"vlab={c['val_label_rows']} vlabflag={c['val_label_cross_or_locked']} entry={c['entry_rows_le489']} entryok={c['entry_ok_le489']} entryflag={c['entry_cross_or_locked_le489']} entryokflag={c['entry_ok_cross_or_locked_le489']}"
        )
    df = pd.DataFrame(map_rows)
    total = {col: int(df[col].sum()) for col in fields if col not in ("match_id", "month", "era")}
    raw_snapshots = total["token0_snapshots"] + total["token1_snapshots"]
    raw_paired = total["token0_two_sided"] + total["token1_two_sided"]
    raw_cross = total["token0_crossed"] + total["token1_crossed"]
    raw_lock = total["token0_locked"] + total["token1_locked"]
    summary_lines.append(
        f"TOTAL maps={len(sample)} raw_snapshots={raw_snapshots} two_sided={raw_paired} crossed={raw_cross} crossed_rate={raw_cross/max(raw_paired,1):.9g} locked={raw_lock} locked_rate={raw_lock/max(raw_paired,1):.9g} "
        f"market_ok={total['market_ok']} current_flagged={total['market_ok_cross_or_locked']} current_flag_rate={total['market_ok_cross_or_locked']/max(total['market_ok'],1):.9g} "
        f"both_cross={total['market_both_cross']} pair_sum_pass={total['market_both_cross_pair_sum_pass']} "
        f"train_rows={total['train_rows']} train_current_flag={total['train_current_cross_or_locked']} train_label_rows={total['train_label_rows']} train_label_flag={total['train_label_cross_or_locked']} "
        f"val_rows={total['val_rows']} val_ok={total['val_ok']} val_ok_flag={total['val_ok_cross_or_locked']} val_labels={total['val_label_rows']} val_label_flag={total['val_label_cross_or_locked']} "
        f"entry_rows={total['entry_rows_le489']} entry_ok={total['entry_ok_le489']} entry_ok_flag={total['entry_ok_cross_or_locked_le489']}"
    )
    if errors:
        err = pd.DataFrame(errors)
        for label, frame in [("all", err), ("crossed", err[err.condition.eq("crossed")]), ("locked", err[err.condition.eq("locked")])]:
            token = frame.token_mid_error_cents.dropna()
            pair = frame.pair_p_error_cents.dropna()
            dist = frame.nearest_uncrossed_distance_s.dropna()
            summary_lines.append(
                f"MID_ERROR_{label} flagged_token_rows={len(frame)} token_mid_error_count={len(token)} token_mid_abs_cents_median={token.median() if len(token) else None} "
                f"p95={token.quantile(.95) if len(token) else None} max={token.max() if len(token) else None} "
                f"pair_p_error_count={len(pair)} pair_p_abs_cents_median={pair.median() if len(pair) else None} p95={pair.quantile(.95) if len(pair) else None} max={pair.max() if len(pair) else None} "
                f"nearest_uncrossed_distance_median_s={dist.median() if len(dist) else None} p95_s={dist.quantile(.95) if len(dist) else None}"
            )
    else:
        summary_lines.append("MID_ERROR no flagged ok market seconds")
    SUMMARY_OUTPUT.write_text("\n".join(summary_lines) + "\n")
    print("summary", SUMMARY_OUTPUT)
    print("errors", ERROR_OUTPUT, "rows", len(errors))
    print("\n".join(summary_lines))


if __name__ == "__main__":
    main()
