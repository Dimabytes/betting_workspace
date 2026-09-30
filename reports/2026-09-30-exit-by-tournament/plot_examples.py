import json

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from analyze import PROJECT, REPORT, read_records


def main() -> None:
    data = json.loads((REPORT / "analysis.json").read_text())
    matches = {match["match_id"]: match for match in data["matches"]}
    examples = [
        {"id": "9018837779", "token": 1, "title": "PGL: Na'Vi-Yandex, map 2 / held Na'Vi", "start": 4, "end": 11},
        {"id": "9015175653", "token": 0, "title": "PGL: GamerLegion-1win, map 1 / held GamerLegion", "start": 3, "end": 10},
        {"id": "grid-3011801-m1", "token": 0, "title": "BLAST: GamerLegion-Aurora, map 1 / held Aurora", "start": 5, "end": 13},
        {"id": "grid-3011800-m1", "token": 0, "title": "BLAST: Liquid-Yakult, map 1 / held Liquid", "start": 1, "end": 8},
    ]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), constrained_layout=True)
    for axis, example in zip(axes.flat, examples):
        match = matches[example["id"]]
        token = example["token"]
        times = []
        mids = []
        fairs = []
        for row in read_records(PROJECT / "data/trader" / example["id"] / "session.jsonl"):
            if row.get("kind") != "signal" or row.get("reason") != "model":
                continue
            minute = row["second"] / 60
            if not example["start"] <= minute <= example["end"]:
                continue
            times.append(minute)
            radiant_index = match["header"]["limits"]["radiant_token_index"]
            mids.append(row["market_p_radiant"] if token == radiant_index else 1 - row["market_p_radiant"])
            fairs.append(row["yes_fair"] if token == 0 else 1 - row["yes_fair"])
        axis.plot(times, mids, color="#334155", linewidth=1.5, label="Observed midpoint")
        axis.plot(times, fairs, color="#f59e0b", linewidth=1, alpha=0.85, label="Model price in 300s")
        observed = [row for row in match["observations"] if row["token_index"] == token and row["sell_price"] is not None and example["start"] <= row["second"] / 60 <= example["end"]]
        axis.scatter([row["second"] / 60 for row in observed], [row["sell_price"] for row in observed], s=14, color="#a855f7", label="Resting SELL (30s samples)")
        for side, color, marker in [("BUY", "#2563eb", "^"), ("SELL", "#16a34a", "v")]:
            fills = [fill for fill in match["fills"] if fill["side"] == side and fill["token_index"] == token and example["start"] <= fill["second"] / 60 <= example["end"]]
            axis.scatter([fill["second"] / 60 for fill in fills], [fill["price"] for fill in fills], s=35, marker=marker, color=color, zorder=5, label=f"Actual {side} fills")
        axis.set(title=example["title"], xlabel="Received game clock, minutes", ylabel="Held token price, USD", xlim=(example["start"], example["end"]))
        axis.grid(alpha=0.2)
        axis.legend(fontsize=8, loc="best")
    fig.suptitle("A positive 5-minute forecast can precede a drawdown or support continued holding", fontsize=14)
    fig.savefig(REPORT / "examples.png", dpi=160)


if __name__ == "__main__":
    main()
