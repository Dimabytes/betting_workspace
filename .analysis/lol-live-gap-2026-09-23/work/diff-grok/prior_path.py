"""Minute path around the horn for the 15 worst LoL priors. Public prices-history only."""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from shared.constants.api import POLYMARKET_CLOB_API
from shared.utils.match_time import HORN_OFFSET_SECONDS
from shared.utils.price_history import parse_history

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23")
TRADER = E / "data" / "trader"
OUT = R / "work" / "diff-grok"


def iso_to_unix(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


def fetch_history(token_id: str, start_ts: int, end_ts: int) -> list[dict]:
    params = urllib.parse.urlencode(
        {"market": token_id, "fidelity": 1, "startTs": start_ts, "endTs": end_ts}
    )
    url = f"{POLYMARKET_CLOB_API}/prices-history?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "diff-grok-prior"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    return parse_history(body)


def last_change_age(points: list[dict], anchor_ts: int) -> tuple[int | None, float | None, int]:
    """Seconds since the YES price last changed, among points strictly before anchor."""
    before = [point for point in points if int(point["t"]) < anchor_ts]
    before.sort(key=lambda point: int(point["t"]))
    if not before:
        return None, None, 0
    price = float(before[-1]["p"])
    changed_at = int(before[0]["t"])
    for point in before:
        if float(point["p"]) != price:
            changed_at = int(point["t"])
    # walk back while equal to the final price
    changed_at = int(before[0]["t"])
    for point in reversed(before):
        if abs(float(point["p"]) - price) > 1e-9:
            break
        changed_at = int(point["t"])
    return anchor_ts - changed_at, price, len(before)


def price_at(points: list[dict], target: int) -> float | None:
    chosen = None
    for point in points:
        if int(point["t"]) <= target:
            chosen = float(point["p"])
        else:
            break
    return chosen


def main() -> None:
    worst = pd.read_csv(OUT / "worst15_repro.csv")
    same = pd.read_csv(R / "work" / "orchestrator" / "same_map_lol.csv")
    val = pd.read_parquet(
        E / "data/lol/processed/datasets/validation.parquet",
        columns=["match_id", "second", "state_ts_us", "market_radiant_prior", "market_p_radiant"],
    )
    rows = []
    for record in worst.itertuples(index=False):
        meta = json.loads((TRADER / record.dir / "match.json").read_text())
        market = meta["market"]
        horn = iso_to_unix(meta["horn_at_utc"])
        anchor = horn - HORN_OFFSET_SECONDS
        start = anchor - 6 * 3600
        end = horn + 600
        yes = fetch_history(market["yes_token_id"], start, end)
        no = fetch_history(market["no_token_id"], start, end)
        yes.sort(key=lambda point: int(point["t"]))
        age_s, last_px, n_before = last_change_age(yes, anchor)
        link = same.loc[same.dir == record.dir]
        match_id = int(link.match_id.iloc[0]) if len(link) else None
        spawn_us = None
        train_at_0 = None
        if match_id is not None:
            part = val[(val.match_id == match_id) & (val.second == 0)]
            if len(part):
                spawn_us = int(part.state_ts_us.iloc[0])
                train_at_0 = float(part.market_p_radiant.iloc[0])
        spawn_s = None if spawn_us is None else spawn_us / 1_000_000
        horn_minus_spawn = None if spawn_s is None else horn - spawn_s
        window = [
            (int(point["t"]) - anchor, round(float(point["p"]), 3))
            for point in yes
            if abs(int(point["t"]) - anchor) <= 300
        ]
        rows.append(
            {
                "dir": record.dir,
                "train_prior": record.train_prior,
                "live_prior": record.live_prior,
                "live_mid": record.live_first_mid,
                "yes_is_radiant": record.yes_is_radiant,
                "last_change_age_s": age_s,
                "yes_at_anchor": last_px,
                "yes_at_horn": price_at(yes, horn),
                "yes_at_horn_plus_80": price_at(yes, horn + 80),
                "no_at_anchor": price_at(no, anchor - 1),
                "horn_minus_spawn_s": horn_minus_spawn,
                "train_mid_second0": train_at_0,
                "path_yes_rel_anchor": window,
            }
        )
        print(
            record.dir,
            "change_age_s",
            age_s,
            "yes@anchor",
            last_px,
            "yes@horn",
            price_at(yes, horn),
            "yes@+80",
            price_at(yes, horn + 80),
            "horn-spawn",
            None if horn_minus_spawn is None else round(horn_minus_spawn, 1),
            "train0",
            train_at_0,
            "path",
            window,
        )
    pd.DataFrame(rows).to_csv(OUT / "worst15_path.csv", index=False)


if __name__ == "__main__":
    main()
