from __future__ import annotations

import gzip
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26")
W = R / "work/parity-luna"
sys.path.insert(0, str(E / "src"))
from shared.utils.jsonl_io import open_maybe_gz, resolve_jsonl


def as_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None or pd.isna(value):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def utc_ns(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    try:
        return int(pd.Timestamp(value).value)
    except (TypeError, ValueError, OverflowError):
        return None


def ns_to_sec(value: Any) -> float | None:
    n = as_int(value)
    return None if n is None else n / 1e9


def open_jsonl(path: Path):
    return open_maybe_gz(resolve_jsonl(path))


def extract_cohorts() -> tuple[pd.DataFrame, pd.DataFrame]:
    j = pd.read_csv(W / "same_map_live_backtest.csv")
    main = j[
        (j.source == "grid")
        & (j.date >= "2026-09-01")
        & (j.date <= "2026-09-19")
        & (j.live_buy > 0)
        & (j.bt_buy > 0)
        & j.live_pnl.notna()
    ].copy()
    oddin = j[
        (j.source == "oddin")
        & (j.date >= "2026-09-20")
        & (j.date <= "2026-09-26")
        & (j.live_buy > 0)
        & (j.bt_buy > 0)
        & j.live_pnl.notna()
    ].copy()
    main["cohort"] = "GRID-49"
    oddin["cohort"] = "Oddin-5"
    return main, oddin


def read_live_match(row: pd.Series) -> dict[str, Any]:
    match_id = int(row.match_id_bt)
    archive = E / "data/trader" / str(row.dir)
    meta = json.loads((archive / "match.json").read_text())
    market = meta.get("market") or {}
    result: dict[str, Any] = {
        "match_id": match_id,
        "condition_id": str(row.condition_id).lower(),
        "archive": str(archive),
        "archive_dir": archive,
        "live_dir": str(row.dir),
        "event_slug": market.get("event_slug") or str(row.slug_live),
        "date": str(row.date),
        "source": str(row.source),
        "cohort": str(row.cohort),
        "tokens": [str(market.get("yes_token_id") or ""), str(market.get("no_token_id") or "")],
        "trace_present": False,
        "trace_orders": [],
        "clock_points": [],
        "session_fills": [],
        "live_mid_by_second": {},
        "trace_fill_link_count": 0,
        "trace_fill_count": 0,
        "trace_fill_unique_count": 0,
        "trace_last_wall_ns": None,
        "offset_ns": None,
        "trace_book_count": 0,
        "session_quote_count": 0,
    }
    session_path = archive / "session.jsonl"
    if session_path.exists():
        session_records = []
        with open_jsonl(session_path) as handle:
            for line in handle:
                try:
                    session_records.append(json.loads(line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        fills_by_key: dict[str, dict[str, Any]] = {}
        for record in session_records:
            kind = record.get("kind")
            if kind == "quote":
                result["session_quote_count"] += 1
            elif kind in {"fill", "late_fill"}:
                key = str(record.get("fill_key") or "")
                ts_ns = utc_ns(record.get("ts_utc"))
                fill = {**record, "_ts_ns": ts_ns}
                result["session_fills"].append(fill)
                if key:
                    fills_by_key[key] = fill
            elif kind == "signal":
                sec = as_int(record.get("second"))
                if sec is not None:
                    for token_index, key in enumerate(("yes_mid", "no_mid")):
                        value = record.get(key)
                        if value is not None:
                            result["live_mid_by_second"].setdefault(token_index, {})[sec] = float(value)
        result["session_fills_by_key"] = fills_by_key
    else:
        result["session_fills_by_key"] = {}

    trace_path = resolve_jsonl(archive / "core_trace.jsonl")
    if not trace_path.exists():
        result["trace_last_wall_ns"] = max(
            [x.get("_ts_ns") for x in result["session_fills"] if x.get("_ts_ns") is not None],
            default=None,
        )
        return result

    result["trace_present"] = True
    orders: dict[str, dict[str, Any]] = {}
    seen_fill_ids: set[str] = set()
    offset_ns: int | None = None
    latest_clock: dict[str, Any] | None = None
    last_book: dict[int, dict[str, Any]] = {}
    last_now_ns = 0
    with open_jsonl(trace_path) as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if record.get("kind") == "header":
                wall = record.get("opened_wall_s")
                monotonic = record.get("opened_now_ns")
                if wall is not None and monotonic is not None:
                    offset_ns = int(float(wall) * 1e9) - int(monotonic)
                continue
            if record.get("kind") != "event":
                continue
            event = record.get("event") or {}
            plan = record.get("plan") or {}
            now_ns = as_int(event.get("now_ns"), as_int(record.get("now_ns"), 0)) or 0
            last_now_ns = max(last_now_ns, now_ns)
            event_type = event.get("type")
            if event_type == "BookUpdate":
                books = (event.get("books") or {}).get("tokens") or []
                for token_book in books:
                    idx = as_int(token_book.get("token_index"))
                    if idx is not None:
                        last_book[idx] = dict(token_book)
                        result["trace_book_count"] += 1
            elif event_type == "ClockUpdate":
                clock = event.get("clock") or {}
                sec = as_int(clock.get("game_second"))
                if sec is not None:
                    latest_clock = {
                        "mono_ns": now_ns,
                        "wall_ns": (now_ns + offset_ns) if offset_ns is not None else None,
                        "game_second": sec,
                        "paused": bool(clock.get("paused", False)),
                        "game_ended": bool(clock.get("game_ended", False)),
                    }
                    result["clock_points"].append(latest_clock)

            order_id = str(event.get("order_id") or "")
            if event_type == "OrderAccepted" and order_id in orders:
                orders[order_id]["accepted_mono_ns"] = now_ns
                orders[order_id]["accepted_wall_ns"] = (
                    now_ns + offset_ns if offset_ns is not None else None
                )
            elif event_type == "Fill":
                result["trace_fill_count"] += 1
                fill_id = str(event.get("fill_id") or "")
                if fill_id not in seen_fill_ids:
                    seen_fill_ids.add(fill_id)
                    result["trace_fill_unique_count"] += 1
                    order = orders.get(order_id)
                    session_fill = result["session_fills_by_key"].get(fill_id)
                    if session_fill is not None:
                        result["trace_fill_link_count"] += 1
                    fill_wall_ns = (
                        session_fill.get("_ts_ns")
                        if session_fill is not None
                        else (now_ns + offset_ns if offset_ns is not None else None)
                    )
                    if order is not None:
                        fill = {
                            "fill_id": fill_id,
                            "ts_ns": fill_wall_ns,
                            "receipt_mono_ns": now_ns,
                            "qty": float(event.get("qty") or 0.0),
                            "price": float(event.get("price") or 0.0),
                            "second": as_int((session_fill or {}).get("second"), latest_clock.get("game_second") if latest_clock else None),
                            "is_maker": (session_fill or {}).get("is_maker"),
                            "session_fill": session_fill,
                        }
                        order["fills"].append(fill)
                        order["filled_qty"] += fill["qty"]
                        order["last_fill_receipt_wall_ns"] = fill["ts_ns"]
            elif event_type in {"CancelAck", "OrderRejected"} and order_id in orders:
                orders[order_id]["terminal_mono_ns"] = now_ns
                orders[order_id]["terminal_wall_ns"] = (
                    now_ns + offset_ns if offset_ns is not None else None
                )
                orders[order_id]["terminal_kind"] = event_type

            for raw in plan.get("places") or []:
                order_id = str(raw.get("order_id") or "")
                idx = as_int(raw.get("token_index"), -1)
                orders[order_id] = {
                    "source": "live",
                    "cohort": result["cohort"],
                    "match_id": match_id,
                    "condition_id": result["condition_id"],
                    "event_slug": result["event_slug"],
                    "live_dir": result["live_dir"],
                    "order_id": order_id,
                    "token_index": idx,
                    "side": str(raw.get("side") or ""),
                    "price": float(raw.get("price") or 0.0),
                    "quantity": float(raw.get("quantity") or 0.0),
                    "submit_mono_ns": now_ns,
                    "submit_wall_ns": now_ns + offset_ns if offset_ns is not None else None,
                    "submit_second": latest_clock.get("game_second") if latest_clock else None,
                    "submit_level_index": as_int(raw.get("level_index"), -1),
                    "reduce_only": bool(raw.get("reduce_only", False)),
                    "book_at_submit": last_book.get(idx, {}).copy() if idx in last_book else None,
                    "fills": [],
                    "filled_qty": 0.0,
                    "accepted_mono_ns": None,
                    "accepted_wall_ns": None,
                    "terminal_mono_ns": None,
                    "terminal_wall_ns": None,
                    "terminal_kind": "",
                }

    result["offset_ns"] = offset_ns
    result["trace_last_wall_ns"] = last_now_ns + offset_ns if offset_ns is not None else None
    result["trace_orders"] = list(orders.values())
    result["clock_points"].sort(key=lambda x: x["wall_ns"] or 0)
    for order in result["trace_orders"]:
        order["queue_source"] = "pending"
        order["queue_ahead"] = None
        order["queue_age_s"] = None
        order["queue_top_match"] = None
        order["game_second"] = order.pop("submit_second", None)
        accepted = order.get("accepted_wall_ns")
        if accepted is None:
            accepted = order.get("submit_wall_ns")
        fills = sorted(order.get("fills", []), key=lambda x: x.get("ts_ns") or 0)
        order["has_fill"] = bool(fills)
        order["total_filled_qty"] = sum(float(x["qty"]) for x in fills)
        order["first_fill_wall_ns"] = fills[0].get("ts_ns") if fills else None
        order["first_fill_second"] = fills[0].get("second") if fills else None
        if accepted is not None and fills and fills[0].get("ts_ns") is not None:
            order["time_to_first_fill_s"] = (fills[0]["ts_ns"] - accepted) / 1e9
        else:
            order["time_to_first_fill_s"] = None
        end = order.get("terminal_wall_ns")
        if end is None:
            end = order.get("last_fill_receipt_wall_ns")
        if end is None:
            end = result["trace_last_wall_ns"]
        order["rest_end_wall_ns"] = end
        order["resting_s"] = max(0.0, (end - accepted) / 1e9) if accepted is not None and end is not None else None
        risk_end = order.get("first_fill_wall_ns") if order.get("has_fill") else end
        order["exposure_min"] = max(0.0, (risk_end - accepted) / 60e9) if accepted is not None and risk_end is not None else None
        order["filled_fraction"] = min(1.0, order["total_filled_qty"] / order["quantity"]) if order["quantity"] > 0 else None
    return result


def map_game_second(clock_points: list[dict[str, Any]], wall_ns: int | None) -> float | None:
    if wall_ns is None or not clock_points:
        return None
    pts = [p for p in clock_points if p.get("wall_ns") is not None]
    if not pts:
        return None
    times = [int(p["wall_ns"]) for p in pts]
    i = int(np.searchsorted(times, wall_ns, side="right")) - 1
    if i < 0:
        return float(pts[0]["game_second"])
    if i >= len(pts) - 1:
        p = pts[i]
        if p.get("paused"):
            return float(p["game_second"])
        return float(p["game_second"]) + max(0.0, (wall_ns - times[i]) / 1e9)
    p, q = pts[i], pts[i + 1]
    span = times[i + 1] - times[i]
    if span <= 0:
        return float(p["game_second"])
    if p.get("paused") or q.get("paused") or span > 30e9:
        return float(p["game_second"])
    progress = max(0.0, min(1.0, (wall_ns - times[i]) / span))
    return float(p["game_second"]) + (float(q["game_second"]) - float(p["game_second"])) * progress


def read_backtest_orders(cohorts: pd.DataFrame) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, Any]]:
    match_ids = sorted(set(int(x) for x in cohorts.match_id_bt))
    bt_root = E / "data/backtests/dota_maker/LIVE"
    qcols = ["match_id", "ts_ns", "kind", "token_index", "side", "price", "reason", "order_id", "quantity", "level_index", "submit_level_index"]
    fcols = ["match_id", "token_index", "side", "price", "quantity", "submitted_quantity", "ts_ns", "queue_ahead", "order_id", "submit_level_index", "level_index", "markout_30s", "markout_300s", "is_maker", "reference_source_30s", "reference_source_300s"]
    quote = pd.read_parquet(bt_root / "seed0/quote_events.parquet", columns=qcols, filters=[("match_id", "in", match_ids)])
    fills = pd.read_parquet(bt_root / "seed0/fills.parquet", columns=fcols, filters=[("match_id", "in", match_ids)])
    results = pd.read_parquet(bt_root / "seed0/results.parquet", filters=[("match_id", "in", match_ids)])
    result_by_id = {int(r.match_id): r for _, r in results.iterrows()}
    cohort_by_id = {int(r.match_id_bt): r for _, r in cohorts.iterrows()}
    qgroup = {int(k): g for k, g in quote.groupby("match_id", sort=False)}
    fgroup = {int(k): g for k, g in fills.groupby("match_id", sort=False)}
    orders: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    for match_id in match_ids:
        q = qgroup.get(match_id, pd.DataFrame(columns=qcols))
        f = fgroup.get(match_id, pd.DataFrame(columns=fcols))
        info = cohort_by_id[match_id]
        result = result_by_id.get(match_id)
        by_order_fill = {str(k): g for k, g in f.groupby("order_id", sort=False)} if len(f) else {}
        last_ts = int(q.ts_ns.max()) if len(q) else None
        if result is not None:
            final_ns = utc_ns(result.game_ended_at)
            if final_ns is not None:
                last_ts = max(last_ts or final_ns, final_ns)
        for _, submitted in q[(q.kind == "submitted") & q.order_id.fillna("").ne("")].iterrows():
            order_id = str(submitted.order_id)
            ev = q[q.order_id == order_id].sort_values("ts_ns")
            accepted = ev[ev.kind == "accepted"]
            terminal = ev[ev.kind.isin(["cancel_ack", "rejected", "expired", "denied"])]
            accepted_ns = int(accepted.ts_ns.iloc[0]) if len(accepted) else None
            terminal_ns = int(terminal.ts_ns.iloc[-1]) if len(terminal) else None
            order_fills = by_order_fill.get(order_id, pd.DataFrame(columns=fcols))
            fs = []
            for _, frow in order_fills.sort_values("ts_ns").iterrows():
                fr = {k: (None if pd.isna(v) else v) for k, v in frow.to_dict().items()}
                fr["ts_ns"] = int(frow.ts_ns)
                fs.append(fr)
                fill_rows.append({
                    "source": "backtest", "cohort": info.cohort, "match_id": match_id,
                    "condition_id": str(info.condition_id).lower(), "event_slug": None,
                    "order_id": order_id, "token_index": int(frow.token_index),
                    "side": str(frow.side), "price": float(frow.price), "qty": float(frow.quantity),
                    "ts_ns": int(frow.ts_ns), "second": None, "submit_level_index": int(submitted.submit_level_index),
                    "queue_ahead": None if pd.isna(frow.queue_ahead) else float(frow.queue_ahead),
                    "markout_30s": None if pd.isna(frow.markout_30s) else float(frow.markout_30s),
                    "markout_300s": None if pd.isna(frow.markout_300s) else float(frow.markout_300s),
                    "is_maker": bool(frow.is_maker), "raw_fill": fr,
                })
            qty = float(submitted.quantity)
            sum_fill = sum(float(x["quantity"]) for x in fs)
            first_fill_ns = int(fs[0]["ts_ns"]) if fs else None
            terminal_kind = str(terminal.kind.iloc[-1]) if len(terminal) else ""
            end_ns = terminal_ns or (int(fs[-1]["ts_ns"]) if qty > 0 and sum_fill >= qty - 1e-7 and fs else None) or last_ts
            exposure_end = first_fill_ns if first_fill_ns is not None else end_ns
            queue_recorded = next((float(x["queue_ahead"]) for x in fs if x.get("queue_ahead") is not None and math.isfinite(float(x["queue_ahead"]))), None)
            orders.append({
                "source": "backtest", "cohort": str(info.cohort), "match_id": match_id,
                "condition_id": str(info.condition_id).lower(), "event_slug": None,
                "live_dir": str(info.dir), "order_id": order_id,
                "token_index": int(submitted.token_index), "side": str(submitted.side),
                "price": float(submitted.price), "quantity": qty,
                "submit_wall_ns": int(submitted.ts_ns), "submit_ns": int(submitted.ts_ns),
                "accepted_wall_ns": accepted_ns, "accepted_ns": accepted_ns,
                "terminal_wall_ns": terminal_ns, "terminal_kind": terminal_kind,
                "rest_end_wall_ns": end_ns,
                "submit_level_index": int(submitted.submit_level_index),
                "game_second": None,
                "fills": fs, "has_fill": bool(fs), "total_filled_qty": sum_fill,
                "first_fill_wall_ns": first_fill_ns,
                "time_to_first_fill_s": (first_fill_ns - accepted_ns) / 1e9 if first_fill_ns is not None and accepted_ns is not None else None,
                "resting_s": max(0.0, (end_ns - accepted_ns) / 1e9) if accepted_ns is not None and end_ns is not None else None,
                "exposure_min": max(0.0, (exposure_end - accepted_ns) / 60e9) if accepted_ns is not None and exposure_end is not None else None,
                "filled_fraction": min(1.0, sum_fill / qty) if qty > 0 else None,
                "queue_ahead": queue_recorded,
                "queue_source": "FillRecord" if queue_recorded is not None else "pending_reconstruct",
                "book_at_submit": None,
                "reason": "",
                "_match_result": result,
            })
    return orders, fill_rows, result_by_id


def book_depth(levels: Any, side: str, price: float) -> tuple[float | None, bool]:
    if levels is None:
        return None, False
    parsed = []
    try:
        for item in levels:
            if item is None:
                continue
            parsed.append((float(item["price"]), float(item["size"])))
    except (TypeError, KeyError, ValueError):
        return None, False
    if not parsed:
        return None, False
    target = round(float(price), 2)
    for p, size in parsed:
        if round(p, 2) == target:
            return size, True
    prices = [p for p, _ in parsed]
    lo, hi = min(prices), max(prices)
    within_displayed_range = lo - 1e-9 <= target <= hi + 1e-9
    return (0.0, True) if within_displayed_range else (None, False)


def load_raw_books(token: str, min_us: int, max_us: int) -> pd.DataFrame:
    folder = E / "data/raw/telonex/polymarket/book_snapshot_full" / f"asset_id={token}"
    if not folder.is_dir():
        return pd.DataFrame(columns=["timestamp_us", "bids", "asks"])
    start_date = datetime.fromtimestamp(max(0, min_us) / 1e6, tz=UTC).date() - timedelta(days=1)
    end_date = datetime.fromtimestamp(max(0, max_us) / 1e6, tz=UTC).date() + timedelta(days=1)
    frames = []
    for path in sorted(folder.glob("*.parquet")):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not start_date <= day <= end_date:
            continue
        try:
            frame = pd.read_parquet(
                path,
                columns=["timestamp_us", "bids", "asks"],
                filters=[("timestamp_us", ">=", min_us - 60_000_000), ("timestamp_us", "<=", max_us)],
            )
        except Exception:
            frame = pd.read_parquet(path, columns=["timestamp_us", "bids", "asks"])
            frame = frame[(frame.timestamp_us >= min_us - 60_000_000) & (frame.timestamp_us <= max_us)]
        if len(frame):
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["timestamp_us", "bids", "asks"])
    return pd.concat(frames, ignore_index=True).sort_values("timestamp_us").drop_duplicates("timestamp_us", keep="last").reset_index(drop=True)


def raw_asof(frame: pd.DataFrame, target_us: int, side: str, price: float) -> tuple[float | None, float | None, float | None, bool]:
    if frame.empty:
        return None, None, None, False
    times = frame.timestamp_us.to_numpy(dtype=np.int64)
    ix = int(np.searchsorted(times, target_us, side="right")) - 1
    if ix < 0:
        return None, None, None, False
    row = frame.iloc[ix]
    field = "bids" if side == "BUY" else "asks"
    depth, known = book_depth(row[field], side, price)
    bid = None
    ask = None
    if row["bids"] is not None and len(row["bids"]):
        bid = float(row["bids"][0]["price"])
    if row["asks"] is not None and len(row["asks"]):
        ask = float(row["asks"][0]["price"])
    age = max(0.0, (target_us - int(row.timestamp_us)) / 1e6)
    return depth, age, (bid, ask), known


def attach_depth(cohorts: pd.DataFrame, live_matches: dict[int, dict[str, Any]], live_orders: list[dict[str, Any]], bt_orders: list[dict[str, Any]]) -> dict[str, Any]:
    from backtest.strip_own_book import load_resting_events, resting_by_token_at

    summary = Counter()
    records_by_id = {int(r.match_id_bt): r for _, r in cohorts.iterrows()}
    by_match_live: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_match_bt: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for o in live_orders:
        by_match_live[int(o["match_id"])].append(o)
    for o in bt_orders:
        by_match_bt[int(o["match_id"])].append(o)

    for match_id, cohort_row in records_by_id.items():
        info = live_matches[match_id]
        tokens = info["tokens"]
        local_live = by_match_live.get(match_id, [])
        local_bt = by_match_bt.get(match_id, [])
        targets: dict[int, list[int]] = defaultdict(list)
        for o in local_live:
            b = o.get("book_at_submit") or {}
            target_ns = b.get("ts_ns")
            if target_ns is not None and info.get("offset_ns") is not None:
                targets[int(o.get("token_index") or 0)].append((int(target_ns) + int(info["offset_ns"])) // 1000)
            elif o.get("submit_wall_ns") is not None:
                targets[int(o.get("token_index") or 0)].append(int(o["submit_wall_ns"]) // 1000)
        for o in local_bt:
            targets[int(o["token_index"])].append(int(o["submit_wall_ns"]) // 1000)
        for f in info.get("session_fills", []):
            if f.get("_ts_ns") is not None:
                token = str(f.get("token_id") or "")
                idx = 0 if token == tokens[0] else (1 if token == tokens[1] else None)
                if idx is not None:
                    targets[idx].append(int(f["_ts_ns"]) // 1000)
        for o in local_bt:
            for f in o.get("fills", []):
                if f.get("ts_ns") is not None:
                    targets[int(o["token_index"])].append(int(f["ts_ns"]) // 1000)

        resting_events: tuple[Any, ...] = ()
        archive = info["archive_dir"]
        trace_file = resolve_jsonl(archive / "core_trace.jsonl")
        if trace_file.is_file():
            try:
                resting_events = load_resting_events(archive)
            except Exception:
                resting_events = ()

        for token_index, token in enumerate(tokens):
            if not token or not targets[token_index]:
                continue
            tmin, tmax = min(targets[token_index]), max(targets[token_index])
            frame = load_raw_books(token, tmin, tmax)
            summary["token_files_loaded"] += 1
            summary["raw_snapshot_rows"] += len(frame)
            for o in local_live:
                if int(o.get("token_index") or 0) != token_index:
                    continue
                b = o.get("book_at_submit") or {}
                book_mono_ns = b.get("ts_ns")
                if book_mono_ns is None or info.get("offset_ns") is None:
                    target_us = int(o["submit_wall_ns"] // 1000)
                else:
                    target_us = (int(book_mono_ns) + int(info["offset_ns"])) // 1000
                depth, age, top, known = raw_asof(frame, target_us, o["side"], o["price"])
                o["queue_ahead"] = depth if known else None
                o["queue_source"] = "Telonex timestamp_us as-of core BookUpdate"
                o["queue_age_s"] = age
                expected_bid = b.get("bid")
                expected_ask = b.get("ask")
                top_match = None
                if top is not None and expected_bid is not None and expected_ask is not None:
                    top_match = abs(top[0] - float(expected_bid)) < 0.0001 and abs(top[1] - float(expected_ask)) < 0.0001
                o["queue_top_match"] = top_match
                if known:
                    summary["live_queue_known"] += 1
                    summary["live_queue_top_match"] += int(top_match is True)
                    summary["live_queue_fresh_le_2s"] += int(age is not None and age <= 2.0)
            for o in local_bt:
                if int(o["token_index"]) != token_index:
                    continue
                target_us = int(o["submit_wall_ns"] // 1000)
                depth, age, top, known = raw_asof(frame, target_us, o["side"], o["price"])
                o["queue_raw_ahead"] = depth if known else None
                o["queue_age_s"] = age
                # Reproduce the product's same-token own-order subtraction only when
                # core_trace is present. Persisted fill telemetry remains the primary
                # value for filled orders and also validates this reconstruction.
                own = 0.0
                if known and resting_events and info.get("offset_ns") is not None:
                    try:
                        resting = resting_by_token_at(resting_events, wall_us=target_us)
                        bucket = resting.get(token_index, {})
                        own = float(bucket.get((o["side"], round(float(o["price"]) / 0.01)), 0.0))
                    except Exception:
                        own = 0.0
                o["queue_own_stripped"] = max(0.0, float(depth or 0.0) - own) if known else None
                if o.get("queue_source") == "FillRecord" and o.get("queue_ahead") is not None:
                    o["queue_ahead_recorded"] = o["queue_ahead"]
                    # Prefer stored execution telemetry for orders that filled.
                    o["queue_ahead"] = float(o["queue_ahead_recorded"])
                elif known:
                    o["queue_ahead"] = o["queue_own_stripped"]
                    o["queue_source"] = "Telonex timestamp_us as-of submit, live size stripped"
                    summary["bt_queue_reconstructed"] += 1
                if known:
                    summary["bt_queue_raw_known"] += 1
                    if o.get("queue_ahead_recorded") is not None:
                        raw_diff = abs(float(o["queue_raw_ahead"]) - float(o["queue_ahead_recorded"]))
                        stripped_diff = abs(float(o["queue_own_stripped"]) - float(o["queue_ahead_recorded"]))
                        summary["bt_fill_queue_validation_n"] += 1
                        summary["bt_fill_queue_raw_abs_diff_sum"] += raw_diff
                        summary["bt_fill_queue_stripped_abs_diff_sum"] += stripped_diff
                        summary["bt_fill_queue_raw_within_1"] += int(raw_diff <= 1.0)
                        summary["bt_fill_queue_stripped_within_1"] += int(stripped_diff <= 1.0)
        del frame
    return dict(summary)


def event_table(orders: list[dict[str, Any]], label: str) -> pd.DataFrame:
    rows = []
    for o in orders:
        row = {k: v for k, v in o.items() if not k.startswith("_") and k not in {"fills", "book_at_submit", "archive_dir"}}
        fills = o.get("fills", [])
        row["fill_event_count"] = len(fills)
        row["queue_ahead"] = o.get("queue_ahead")
        row["queue_bucket"] = queue_bucket(o.get("queue_ahead"))
        if label == "live" and row.get("game_second") is None:
            row["game_second"] = None
        rows.append(row)
    return pd.DataFrame(rows)


def queue_bucket(value: Any) -> str:
    try:
        q = float(value)
        if not math.isfinite(q):
            return "unknown"
    except (TypeError, ValueError):
        return "unknown"
    if q <= 1e-9:
        return "Q0"
    if q <= 25:
        return "Q1_0-25"
    if q <= 100:
        return "Q2_25-100"
    return "Q3_100+"


def summarize_orders(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if df.empty:
        return pd.DataFrame()
    for (cohort, source, side, rung), g in df.groupby(["cohort", "source", "side", "submit_level_index"], dropna=False):
        valid = g[g.accepted_wall_ns.notna()]
        fills = int(valid.has_fill.fillna(False).sum())
        exposure = float(valid.exposure_min.fillna(0).sum())
        rest_min = float(valid.resting_s.fillna(0).sum() / 60)
        firsts = valid[valid.time_to_first_fill_s.notna()]
        qty_sub = float(valid.quantity.fillna(0).sum())
        qty_fill = float(valid.total_filled_qty.fillna(0).sum())
        rows.append({
            "cohort": cohort, "source": source, "side": side, "rung": rung,
            "orders": len(g), "accepted": len(valid), "any_fill_orders": fills,
            "order_fill_probability": fills / len(valid) if len(valid) else None,
            "resting_minutes": rest_min,
            "first_fill_exposure_minutes": exposure,
            "first_fill_hazard_per_min": fills / exposure if exposure else None,
            "one_minute_hazard_probability": 1 - math.exp(-fills / exposure) if exposure else None,
            "median_resting_s": float(valid.resting_s.median()) if len(valid) else None,
            "median_time_to_first_fill_s": float(firsts.time_to_first_fill_s.median()) if len(firsts) else None,
            "mean_time_to_first_fill_s": float(firsts.time_to_first_fill_s.mean()) if len(firsts) else None,
            "submitted_qty": qty_sub, "filled_qty": qty_fill,
            "filled_fraction": qty_fill / qty_sub if qty_sub else None,
            "filled_fraction_of_filled_orders": float(valid[valid.has_fill].filled_fraction.mean()) if fills else None,
            "queue_known_orders": int(valid.queue_ahead.notna().sum()),
            "queue_median": float(valid.queue_ahead.median()) if valid.queue_ahead.notna().any() else None,
        })
    return pd.DataFrame(rows)


def live_fill_rows(live_matches: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    order_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for mid, match in live_matches.items():
        for order in match.get("trace_orders", []):
            order_by_key[(mid, order["order_id"])] = order
    for mid, match in live_matches.items():
        token_map = {token: idx for idx, token in enumerate(match.get("tokens", [])) if token}
        for session_fill in match.get("session_fills", []):
            if session_fill.get("kind") not in {"fill", "late_fill"}:
                continue
            order = None
            for candidate in match.get("trace_orders", []):
                if any(f.get("fill_id") == str(session_fill.get("fill_key") or "") for f in candidate.get("fills", [])):
                    order = candidate
                    break
            token = str(session_fill.get("token_id") or "")
            rows.append({
                "source": "live", "cohort": match["cohort"], "match_id": mid,
                "condition_id": match["condition_id"], "event_slug": match["event_slug"],
                "order_id": order.get("order_id") if order else None,
                "token_index": token_map.get(token), "side": str(session_fill.get("side") or ""),
                "price": float(session_fill.get("price") or 0.0), "qty": float(session_fill.get("size") or 0.0),
                "ts_ns": session_fill.get("_ts_ns"), "second": as_int(session_fill.get("second")),
                "submit_level_index": order.get("submit_level_index") if order else None,
                "queue_ahead": order.get("queue_ahead") if order else None,
                "markout_30s": None, "markout_300s": None,
                "is_maker": session_fill.get("is_maker"),
                "fill_order_joined": order is not None,
                "session_fill": session_fill,
            })
    return rows


def add_live_markouts(fills: list[dict[str, Any]], matches: dict[int, dict[str, Any]]) -> None:
    for fill in fills:
        if fill.get("source") != "live" or fill.get("token_index") is None or fill.get("second") is None:
            continue
        mids = matches[int(fill["match_id"])].get("live_mid_by_second", {}).get(int(fill["token_index"]), {})
        sec = int(fill["second"])
        for h in (30, 300):
            future = next((mids[t] for t in sorted(mids) if t >= sec + h and mids[t] is not None), None)
            if future is not None:
                fill[f"markout_{h}s"] = float(future) - float(fill["price"])


def summarize_markouts(fills: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(fills)
    if df.empty:
        return pd.DataFrame()
    df = df[(df.side == "BUY") & df.submit_level_index.notna()].copy()
    df["queue_bucket"] = df.queue_ahead.map(queue_bucket)
    rows = []
    for (cohort, source, rung, bucket), g in df.groupby(["cohort", "source", "submit_level_index", "queue_bucket"], dropna=False):
        row = {"cohort": cohort, "source": source, "rung": rung, "queue_bucket": bucket, "fills": len(g), "quantity": float(g.qty.sum())}
        for h in (30, 300):
            col = f"markout_{h}s"
            valid = g[g[col].notna()]
            row[col] = float(np.average(valid[col], weights=valid.qty)) if len(valid) and valid.qty.sum() else None
            row[f"{col}_fills"] = len(valid)
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    main_grid, oddin = extract_cohorts()
    cohorts = pd.concat([main_grid, oddin], ignore_index=True)
    print("cohort sizes", len(main_grid), len(oddin), flush=True)
    live_matches = {}
    live_orders = []
    for i, (_, row) in enumerate(cohorts.iterrows(), 1):
        parsed = read_live_match(row)
        live_matches[int(row.match_id_bt)] = parsed
        live_orders.extend(parsed["trace_orders"])
        print("live trace", i, len(cohorts), parsed["live_dir"], "trace", parsed["trace_present"], "orders", len(parsed["trace_orders"]), "fills", parsed["trace_fill_count"], "linked", parsed["trace_fill_link_count"], flush=True)
    bt_orders, bt_fill_rows, results = read_backtest_orders(cohorts)
    print("orders before depth", "live", len(live_orders), "bt", len(bt_orders), "bt fills", len(bt_fill_rows), flush=True)
    queue_validation = attach_depth(cohorts, live_matches, live_orders, bt_orders)
    print("queue validation", json.dumps(queue_validation, sort_keys=True), flush=True)
    for o in bt_orders:
        mid = int(o["match_id"])
        o["event_slug"] = live_matches[mid]["event_slug"]
        o["game_second"] = map_game_second(live_matches[mid]["clock_points"], o["submit_wall_ns"])
    for o in live_orders:
        o["game_second"] = o.get("game_second")
        o["event_slug"] = live_matches[int(o["match_id"])]["event_slug"]
    ldf, bdf = event_table(live_orders, "live"), event_table(bt_orders, "bt")
    ldf.to_csv(W / "live_order_lifecycle.csv", index=False)
    bdf.to_csv(W / "backtest_order_lifecycle.csv", index=False)
    summary = pd.concat([summarize_orders(ldf), summarize_orders(bdf)], ignore_index=True)
    summary.to_csv(W / "order_lifecycle_summary.csv", index=False)
    live_fills = live_fill_rows(live_matches)
    add_live_markouts(live_fills, live_matches)
    all_fills = live_fills + bt_fill_rows
    pd.DataFrame(all_fills).drop(columns=["session_fill", "raw_fill"], errors="ignore").to_csv(W / "order_fill_marks.csv", index=False)
    marks = summarize_markouts(all_fills)
    marks.to_csv(W / "order_markouts_rung_queue.csv", index=False)
    print("trace coverage", {
        "cohort_maps": len(cohorts),
        "trace_maps": sum(x["trace_present"] for x in live_matches.values()),
        "trace_orders": len(live_orders),
        "session_fills": sum(len(x["session_fills"]) for x in live_matches.values()),
        "trace_fill_events": sum(x["trace_fill_count"] for x in live_matches.values()),
        "trace_fill_unique_ids": sum(x["trace_fill_unique_count"] for x in live_matches.values()),
        "trace_fill_links": sum(x["trace_fill_link_count"] for x in live_matches.values()),
    }, flush=True)
    print("order summary\n", summary.to_string(index=False), flush=True)
    print("markouts\n", marks.to_string(index=False), flush=True)
    # Compact metadata for the next conditional and mechanism pass.
    (W / "order_lifecycle_meta.json").write_text(json.dumps({
        "cohorts": {"GRID-49": len(main_grid), "Oddin-5": len(oddin)},
        "queue_validation": queue_validation,
        "trace_coverage": {
            "trace_maps": sum(x["trace_present"] for x in live_matches.values()),
            "maps": len(live_matches),
            "orders": len(live_orders),
            "session_fills": sum(len(x["session_fills"]) for x in live_matches.values()),
            "trace_fill_events": sum(x["trace_fill_count"] for x in live_matches.values()),
            "trace_fill_unique_ids": sum(x["trace_fill_unique_count"] for x in live_matches.values()),
            "trace_fill_links": sum(x["trace_fill_link_count"] for x in live_matches.values()),
        },
        "clock_points": {str(mid): len(x["clock_points"]) for mid, x in live_matches.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
