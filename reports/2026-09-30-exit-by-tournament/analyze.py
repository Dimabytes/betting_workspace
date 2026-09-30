from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np


REPORT = Path(__file__).resolve().parent
PROJECT = REPORT.parents[2] / "esports-trader"
SAMPLE_SECONDS = 30
MIN_POSITION = 5.0


@dataclass(frozen=True)
class Observation:
    match_id: str
    tournament: str
    model: str
    time: float
    second: int
    token_index: int
    quantity: float
    cost: float
    mid: float
    delta: float
    clipped_delta: float
    bid: float
    ask: float
    bid_size: float
    sell_price: float | None
    exit_status: str
    episode: int


@dataclass
class Position:
    quantity: float
    basis: float
    episode: int


def read_records(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as handle:
        for line in handle:
            yield json.loads(line)


def select_trace(archive: Path) -> Path:
    path = archive / "core_trace.jsonl"
    return path if path.exists() else archive / "core_trace.jsonl.gz"


def extract_match(manifest_row: dict) -> dict:
    match_id = manifest_row["match_id"]
    archive = PROJECT / "data/trader" / match_id
    session_hash = hashlib.sha256((archive / "session.jsonl").read_bytes()).hexdigest()
    assert session_hash == manifest_row["session_sha256"], match_id
    meta = manifest_row["meta"]
    tournament = "PGL Wallachia S9 / Oddin" if meta["league_id"] == 20279 else "BLAST Slam VIII / GRID"
    records = read_records(select_trace(archive))
    header = next(records)
    assert header["kind"] == "header"
    radiant_index = header["limits"]["radiant_token_index"]
    origin = header["opened_now_ns"]
    positions = [Position(0.0, 0.0, 0), Position(0.0, 0.0, 0)]
    books = None
    second = -100
    paused = False
    orders = {}
    samples = []
    sampled_bins = set()
    fill_ids = set()
    fills = []
    book_rows = []
    event_types = Counter()
    signal_count = 0
    held_signal_count = 0
    for record in records:
        event = record.get("event")
        if not event:
            continue
        kind = event["type"]
        event_types[kind] += 1
        time = (event["now_ns"] - origin) / 1e9
        if kind == "ClockUpdate":
            second = event["clock"]["game_second"]
            paused = event["clock"]["paused"]
        elif kind == "BookUpdate":
            books = event["books"]["tokens"]
            values = [book.get(key) for book in books for key in ["bid", "ask"]]
            fresh = all(0 <= event["now_ns"] - book["ts_ns"] <= 5e9 for book in books)
            valid = all(value is not None and 0 < value < 1 for value in values)
            if valid and fresh:
                mids = [(book["bid"] + book["ask"]) / 2 for book in books]
                pair_sum = sum(mids)
                if abs(pair_sum - 1) <= 0.05 and all(book["bid"] <= book["ask"] for book in books):
                    book_rows.append([time, mids[0] / pair_sum, books[0]["bid"], books[1]["bid"]])
        elif kind == "Fill" and event["fill_id"] not in fill_ids:
            fill_ids.add(event["fill_id"])
            index = event["token_index"]
            position = positions[index]
            quantity = event["qty"]
            price = event["price"]
            if event["side"] == "BUY":
                if position.quantity < MIN_POSITION:
                    position.episode += 1
                position.quantity += quantity
                position.basis += quantity * price
            else:
                assert quantity <= position.quantity + 0.05, (match_id, event)
                cost = position.basis / position.quantity if position.quantity else 0
                position.quantity = max(0.0, position.quantity - quantity)
                position.basis = position.quantity * cost
            fills.append({**event, "time": time, "second": second, "episode": position.episode})
            order = orders.get(event["order_id"])
            if order:
                order["quantity"] -= quantity
                if order["quantity"] <= 1e-6:
                    orders.pop(event["order_id"])
        elif kind == "OrderAccepted":
            if event["order_id"] in orders:
                orders[event["order_id"]]["status"] = "live"
        elif kind in {"CancelAck", "OrderRejected"}:
            orders.pop(event["order_id"], None)
        elif kind == "SignalUpdate" and event["signal"] is not None:
            signal_count += 1
            signal = event["signal"]
            if books is not None and second >= 0 and not paused:
                for index, position in enumerate(positions):
                    if position.quantity < MIN_POSITION:
                        continue
                    held_signal_count += 1
                    bin_key = f"{index}:{int(time // SAMPLE_SECONDS)}"
                    if bin_key in sampled_bins:
                        continue
                    book = books[index]
                    if book["bid"] is None or book["ask"] is None:
                        continue
                    sampled_bins.add(bin_key)
                    sign = 1 if index == radiant_index else -1
                    mid = signal["anchor_p"] if sign == 1 else 1 - signal["anchor_p"]
                    delta = sign * signal["predicted_delta"]
                    sells = [order for order in orders.values() if order["side"] == "SELL" and order["token_index"] == index]
                    live_sells = [order for order in sells if order["status"] == "live"]
                    sell_price = min(order["price"] for order in live_sells) if live_sells else None
                    status = "live" if live_sells else (sells[0]["status"] if sells else "none")
                    samples.append(Observation(match_id, tournament, meta["model"]["name"], time, second, index,
                                               position.quantity, position.basis / position.quantity, mid, delta,
                                               min(1.0, max(0.0, mid + delta)) - mid, book["bid"], book["ask"],
                                               book["bid_size"], sell_price, status, position.episode))
        plan = record.get("plan", {})
        for cancel in plan.get("cancels", []):
            order_id = cancel["order_id"]
            if order_id in orders:
                orders[order_id]["status"] = "canceling"
        for order in plan.get("places", []):
            orders[order["order_id"]] = {**order, "status": "pending"}
    book_array = np.asarray(book_rows, dtype=float)
    assert len(book_array) and np.all(np.diff(book_array[:, 0]) >= 0), match_id
    observations = [evaluate_observation(sample, book_array) for sample in samples]
    return {"match_id": match_id, "tournament": tournament, "meta": meta, "header": header,
            "summary": manifest_row["summary"], "signal_count": signal_count, "held_signal_count": held_signal_count,
            "event_types": dict(event_types), "core_fill_count": len(fills), "fills": fills,
            "observations": observations, "terminal_positions": [asdict(position) for position in positions]}


def evaluate_observation(sample: Observation, books: np.ndarray) -> dict:
    row = asdict(sample)
    times = books[:, 0]
    start = np.searchsorted(times, sample.time, side="right")
    for horizon in [60, 300]:
        target = sample.time + horizon
        end = np.searchsorted(times, target, side="right")
        path = books[start:end]
        complete = bool(len(path) and 0 <= target - path[-1, 0] <= 5)
        if complete:
            gaps = np.diff(np.concatenate(([sample.time], path[:, 0], [target])))
            complete = bool(np.max(gaps) <= 10)
        row[f"complete_{horizon}"] = complete
        if not complete:
            continue
        mids = path[:, 1] if sample.token_index == 0 else 1 - path[:, 1]
        changes = mids - sample.mid
        row[f"markout_{horizon}"] = float(changes[-1])
        row[f"worst_{horizon}"] = float(min(0.0, np.min(changes)))
        row[f"best_{horizon}"] = float(max(0.0, np.max(changes)))
        row[f"bid_change_{horizon}"] = float(path[-1, 2 + sample.token_index] - sample.bid)
        for drop in [0.02, 0.03, 0.05]:
            down = np.flatnonzero(changes <= -drop + 1e-10)
            up = np.flatnonzero(changes >= 0.02 - 1e-10)
            outcome = "none"
            if len(down) and (not len(up) or down[0] < up[0]):
                outcome = "drop_first"
            elif len(up):
                outcome = "rise_first"
            row[f"first_{horizon}_drop{int(drop * 100)}"] = outcome
    return row


def summarize_observations(rows: list[dict]) -> dict:
    summary = {"n": len(rows), "maps": len({row["match_id"] for row in rows})}
    if not rows:
        return summary
    summary["positive_share"] = float(np.mean([row["delta"] > 0 for row in rows]))
    summary["negative_share"] = float(np.mean([row["delta"] < 0 for row in rows]))
    summary["median_delta"] = float(np.median([row["delta"] for row in rows]))
    live = [row for row in rows if row["sell_price"] is not None]
    summary["live_sell_share"] = len(live) / len(rows)
    summary["sell_above_ask_share"] = float(np.mean([row["sell_price"] > np.ceil((row["ask"] - 1e-10) * 100) / 100 + 1e-10 for row in live])) if live else None
    for horizon in [60, 300]:
        valid = [row for row in rows if row[f"complete_{horizon}"]]
        summary[f"n_{horizon}"] = len(valid)
        if not valid:
            continue
        summary[f"mean_markout_{horizon}"] = float(np.mean([row[f"markout_{horizon}"] for row in valid]))
        summary[f"median_markout_{horizon}"] = float(np.median([row[f"markout_{horizon}"] for row in valid]))
        summary[f"rise_share_{horizon}"] = float(np.mean([row[f"markout_{horizon}"] > 0 for row in valid]))
        summary[f"drop3_share_{horizon}"] = float(np.mean([row[f"worst_{horizon}"] <= -0.03 + 1e-10 for row in valid]))
        summary[f"drop3_recovery_share_{horizon}"] = float(np.mean([row[f"worst_{horizon}"] <= -0.03 + 1e-10 and row[f"markout_{horizon}"] > 0 for row in valid]))
        summary[f"mean_bid_change_{horizon}"] = float(np.mean([row[f"bid_change_{horizon}"] for row in valid]))
        if horizon == 300:
            summary["mean_prediction_error_300"] = float(np.mean([row["clipped_delta"] - row["markout_300"] for row in valid]))
        for drop in [2, 3, 5]:
            outcomes = Counter(row[f"first_{horizon}_drop{drop}"] for row in valid)
            summary[f"first_{horizon}_drop{drop}"] = dict(outcomes)
        per_map = []
        for match_id in sorted({row["match_id"] for row in valid}):
            selected = [row for row in valid if row["match_id"] == match_id]
            per_map.append(float(np.mean([row[f"markout_{horizon}"] for row in selected])))
        summary[f"map_equal_mean_markout_{horizon}"] = float(np.mean(per_map))
        summary[f"map_positive_mean_share_{horizon}"] = float(np.mean(np.asarray(per_map) > 0))
    return summary


def write_analysis(matches: list[dict]) -> None:
    rows = [row for match in matches for row in match["observations"]]
    groups = {}
    for tournament in sorted({match["tournament"] for match in matches}):
        selected = [row for row in rows if row["tournament"] == tournament]
        selections = {
            "all": selected,
            "positive": [row for row in selected if row["delta"] > 0],
            "negative": [row for row in selected if row["delta"] < 0],
            "early_positive": [row for row in selected if row["delta"] > 0 and row["second"] < 600],
            "late_positive": [row for row in selected if row["delta"] > 0 and row["second"] >= 600],
            "profit5_positive": [row for row in selected if row["delta"] > 0 and row["bid"] - row["cost"] >= 0.05],
            "positive1to3": [row for row in selected if 0.01 <= row["delta"] < 0.03],
            "positive3plus": [row for row in selected if row["delta"] >= 0.03],
        }
        groups[tournament] = {name: summarize_observations(selection) for name, selection in selections.items()}
    output = {"sample_seconds": SAMPLE_SECONDS, "matches": matches, "groups": groups}
    (REPORT / "analysis.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(groups, ensure_ascii=False, indent=2), flush=True)


def main() -> None:
    manifest = json.loads((REPORT / "vps_manifest.json").read_text())
    matches = []
    traded = [row for row in manifest if row["summary"]["fills"]]
    for index, row in enumerate(sorted(traded, key=lambda row: row["meta"]["joined_at_utc"])):
        matches.append(extract_match(row))
        if (index + 1) % 10 == 0:
            print(f"Processed {index + 1}/{len(traded)} matches", flush=True)
    write_analysis(matches)


if __name__ == "__main__":
    main()
