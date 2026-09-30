from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from analyze import PROJECT, REPORT, summarize_observations


def calculate_auc(scores: np.ndarray, outcomes: np.ndarray) -> float | None:
    positive = scores[outcomes]
    negative = scores[~outcomes]
    if not len(positive) or not len(negative):
        return None
    comparisons = positive[:, None] - negative[None, :]
    return float(np.mean((comparisons > 0) + 0.5 * (comparisons == 0)))


def bootstrap_series(rows: list[dict], metadata: dict) -> dict:
    series_ids = sorted({metadata[row["match_id"]]["market"]["event_slug"] for row in rows})
    clusters = [[row for row in rows if metadata[row["match_id"]]["market"]["event_slug"] == series_id] for series_id in series_ids]
    stats = []
    random = np.random.default_rng(20260930)
    for _ in range(2000):
        sampled = [row for index in random.integers(0, len(clusters), len(clusters)) for row in clusters[index]]
        future = [row["markout_300"] for row in sampled if row["complete_300"]]
        path = [row["first_60_drop3"] == "drop_first" for row in sampled if row["complete_60"]]
        stats.append([np.mean(future), np.mean(path)])
    intervals = np.quantile(np.asarray(stats), [0.025, 0.975], axis=0)
    return {"series": len(series_ids), "mean_markout_300_ci95": intervals[:, 0].tolist(),
            "drop_first_60_ci95": intervals[:, 1].tolist()}


def check_inventory(matches: list[dict]) -> dict:
    mismatches = []
    checked = 0
    for match in matches:
        end = match["summary"]["end"]
        if end is None or match["summary"]["late_fills"]:
            continue
        for index, token in enumerate([match["header"]["yes_token"], match["header"]["no_token"]]):
            expected = float(end["positions"].get(token, 0.0))
            observed = match["terminal_positions"][index]["quantity"]
            checked += 1
            if abs(expected - observed) > 0.05:
                mismatches.append({"match_id": match["match_id"], "index": index, "session": expected, "core": observed})
    return {"checked_tokens": checked, "mismatches": mismatches}


def inspect_backtest(metadata: dict) -> dict:
    import pyarrow.parquet as pq

    root = PROJECT / "data/backtests/dota_maker/LIVE"
    match_lookup = {int(meta["steam_match_id"] or match_id): meta for match_id, meta in metadata.items()}
    output = []
    for seed in sorted(root.glob("seed[0-9]*")):
        rows = pq.read_table(seed / "results.parquet").to_pylist()
        pgl = [row for row in rows if row["match_id"] in match_lookup and match_lookup[row["match_id"]]["league_id"] == 20279]
        blast = [row for row in rows if row["match_id"] in match_lookup and match_lookup[row["match_id"]]["league_id"] == 19102]
        ids = [row["match_id"] for row in pgl]
        fills = pq.read_table(seed / "fills.parquet", filters=[("match_id", "in", ids)]).to_pylist()
        all_buy = [fill for fill in fills if fill["side"] == "BUY" and fill["reference_300s"] is not None]
        all_sell = [fill for fill in fills if fill["side"] == "SELL" and fill["reference_300s"] is not None]
        result = {"seed": seed.name, "pgl_maps": len(pgl), "pgl_traded_maps": sum(row["buy_fills"] > 0 for row in pgl),
                  "blast_maps": len(blast), "pgl_engine_pnl": sum(row["engine_pnl"] or 0 for row in pgl),
                  "feed_sources": dict(Counter(row["feed_source"] for row in pgl)),
                  "models": dict(Counter(row["model_name"] for row in pgl))}
        for side, selected in [("buy", all_buy), ("sell", all_sell)]:
            quantity = sum(fill["quantity"] for fill in selected)
            result[f"{side}_quantity_with_300s"] = quantity
            result[f"{side}_mid300_minus_fill_price"] = sum(fill["quantity"] * (fill["reference_300s"] - fill["price"]) for fill in selected) / quantity if quantity else None
        result["manifest"] = json.loads((seed / "manifest.json").read_text())
        result["pgl_results"] = pgl
        output.append(result)
    return {"root": str(root), "seeds": output}


def main() -> None:
    analysis = json.loads((REPORT / "analysis.json").read_text())
    manifest = json.loads((REPORT / "vps_manifest.json").read_text())
    matches = analysis["matches"]
    metadata = {row["match_id"]: row["meta"] for row in manifest}
    output = {"inventory": check_inventory(matches), "tournaments": {}, "backtest": inspect_backtest(metadata)}
    all_rows = [row for match in matches for row in match["observations"]]
    for tournament in analysis["groups"]:
        rows = [row for row in all_rows if row["tournament"] == tournament and row["delta"] > 0]
        first = [row for row in rows if row["complete_60"] and row["first_60_drop3"] != "none"]
        future = [row for row in rows if row["complete_300"]]
        versions = {model: summarize_observations([row for row in rows if row["model"] == model]) for model in sorted({row["model"] for row in rows})}
        cases = Counter()
        for row in future:
            if row["markout_300"] > 0:
                cases["drop3_within60_then_positive300" if row["worst_60"] <= -0.03 + 1e-10 else "positive300_without_drop3_within60"] += 1
            else:
                cases["drop3_within60_then_nonpositive300" if row["worst_60"] <= -0.03 + 1e-10 else "nonpositive300_without_drop3_within60"] += 1
        output["tournaments"][tournament] = {
            "bootstrap": bootstrap_series(rows, metadata), "versions": versions, "cases": dict(cases),
            "auc_rise_first60_vs_drop_first60": calculate_auc(np.asarray([row["delta"] for row in first]), np.asarray([row["first_60_drop3"] == "rise_first" for row in first])),
            "auc_positive300": calculate_auc(np.asarray([row["delta"] for row in future]), np.asarray([row["markout_300"] > 0 for row in future])),
            "quantity50plus": summarize_observations([row for row in rows if row["quantity"] >= 50]),
            "delta2to3": summarize_observations([row for row in rows if 0.02 <= row["delta"] < 0.03]),
        }
    (REPORT / "checks.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
    compact = {**output, "backtest": {"root": output["backtest"]["root"], "seeds": [{k: v for k, v in seed.items() if k not in {"manifest", "pgl_results"}} for seed in output["backtest"]["seeds"]]}}
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
