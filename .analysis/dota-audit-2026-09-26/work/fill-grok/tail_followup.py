"""Follow-up: last SELL fate, buy-after-cutoff, both-leg overlap, loser shares."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from shared.utils.match_time import datetime_to_ns, parse_utc

OUT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/dota-audit-2026-09-26/work/fill-grok")
LIVE = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/backtests/dota_maker/LIVE")
CAT = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/new_processed/match_catalog/match_catalog.parquet")


def main() -> None:
    catalog = pd.read_parquet(CAT, columns=["match_id", "radiant_win", "radiant_token_index"])
    catalog["match_id"] = catalog["match_id"].astype(int)
    catalog = catalog.set_index("match_id")
    held = pd.read_csv(OUT / "held_positions.csv")
    lines: list[str] = []

    for seed in (0, 1, 2):
        results = pd.read_parquet(LIVE / f"seed{seed}" / "results.parquet")
        fills = pd.read_parquet(
            LIVE / f"seed{seed}" / "fills.parquet",
            columns=["match_id", "token_index", "side", "price", "quantity", "ts_ns", "position_after"],
        )
        quotes = pd.read_parquet(
            LIVE / f"seed{seed}" / "quote_events.parquet",
            columns=["match_id", "ts_ns", "kind", "token_index", "side", "price", "reason", "order_id"],
        )
        term = results[results["terminal_position"] > 1e-9][
            ["match_id", "terminal_position", "terminal_token_index", "terminal_side", "game_ended_at", "slug"]
        ].copy()
        lines.append(f"\n===== SEED {seed} reported terminal n={len(term)} =====")

        # join catalog win
        rows = []
        for row in term.itertuples(index=False):
            cat = catalog.loc[int(row.match_id)]
            rti = int(cat["radiant_token_index"])
            token = int(row.terminal_token_index)
            won = (token == rti and bool(cat["radiant_win"])) or (token != rti and not bool(cat["radiant_win"]))
            rows.append({**row._asdict(), "won": won, "rti": rti})
        term = pd.DataFrame(rows)
        lines.append(f"wins {int(term.won.sum())} losses {int((~term.won).sum())} qty {term.terminal_position.sum():.4f}")

        q = quotes[quotes["match_id"].isin(term["match_id"]) & (quotes["side"] == "SELL")]
        # last event per order, then the order whose last submit is the latest on that token
        fate_counts: dict[str, int] = {}
        above = 0
        below = 0
        missing = 0
        for row in term.itertuples(index=False):
            token_q = q[(q["match_id"] == row.match_id) & (q["token_index"] == row.terminal_token_index)]
            submits = token_q[token_q["kind"] == "submitted"]
            if submits.empty:
                missing += 1
                fate_counts["no_sell_submit"] = fate_counts.get("no_sell_submit", 0) + 1
                continue
            last = submits.sort_values("ts_ns").iloc[-1]
            oid = last["order_id"]
            life = token_q[token_q["order_id"] == oid].sort_values("ts_ns")
            final = life.iloc[-1]
            key = f"{final['kind']}:{final['reason']}"
            fate_counts[key] = fate_counts.get(key, 0) + 1
            h = held[(held.seed == seed) & (held.match_id == row.match_id) & (held.token == row.terminal_token_index)]
            if len(h) and pd.notna(h.iloc[0]["end_token_mid"]):
                if float(last["price"]) > float(h.iloc[0]["end_token_mid"]) + 1e-9:
                    above += 1
                else:
                    below += 1
        lines.append(f"last sell vs end mid: above {above} at-or-below {below} no-submit {missing}")
        lines.append("last sell order final event: " + json.dumps(fate_counts, sort_keys=True))

        # outlier end mids
        hmat = held[(held.seed == seed) & (held.qty >= 1)]
        odd = hmat[(hmat.end_token_mid < 0.9) | (hmat.duration_gap_s > 60) | (hmat.entry_second > 480) | (hmat.last_sell_wall_before_end_s < 0)]
        cols = ["match_id", "slug", "side", "qty", "avg_px", "entry_second", "last_sell_px", "last_sell_wall_before_end_s", "end_token_mid", "end_second", "duration", "duration_gap_s", "won"]
        lines.append("outliers:\n" + (odd[cols].to_string(index=False) if len(odd) else "none"))

        # loser inventory: shares bought on the losing token vs still open
        # losing token = 1-rti if radiant_win else rti
        bought_lose = 0.0
        sold_lose = 0.0
        open_lose = 0.0
        bought_win = 0.0
        sold_win = 0.0
        open_win = 0.0
        for match_id, mf in fills.groupby("match_id"):
            if int(match_id) not in catalog.index:
                continue
            cat = catalog.loc[int(match_id)]
            rti = int(cat["radiant_token_index"])
            win_token = rti if bool(cat["radiant_win"]) else 1 - rti
            for token in (0, 1):
                part = mf[mf["token_index"] == token]
                b = float(part.loc[part.side == "BUY", "quantity"].sum())
                s = float(part.loc[part.side == "SELL", "quantity"].sum())
                if token == win_token:
                    bought_win += b
                    sold_win += s
                    open_win += b - s
                else:
                    bought_lose += b
                    sold_lose += s
                    open_lose += b - s
        lines.append(
            f"winner token bought {bought_win:.2f} sold {sold_win:.2f} open {open_win:.2f} sold_frac {sold_win/bought_win if bought_win else 0:.4f}"
        )
        lines.append(
            f"loser token  bought {bought_lose:.2f} sold {sold_lose:.2f} open {open_lose:.2f} sold_frac {sold_lose/bought_lose if bought_lose else 0:.4f}"
        )

        # both-leg peak
        both_ids = {
            8922940920, 8938716198, 8937722788, 8964837315, 8910589445, 8940487898,
            8971664649, 8886728756, 8885183102,
        }
        for match_id, mf in fills.groupby("match_id"):
            if int(match_id) not in both_ids:
                continue
            qty = {0: 0.0, 1: 0.0}
            peak = 0.0
            for row in mf.sort_values("ts_ns").itertuples(index=False):
                token = int(row.token_index)
                if row.side == "BUY":
                    qty[token] += float(row.quantity)
                else:
                    qty[token] = max(0.0, qty[token] - float(row.quantity))
                if qty[0] > 0.01 and qty[1] > 0.01:
                    peak = max(peak, min(qty[0], qty[1]))
            if peak > 0:
                lines.append(f"both-leg match {match_id} peak_min_qty {peak:.4f} end {qty}")

    text = "\n".join(lines)
    (OUT / "tail_followup.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
