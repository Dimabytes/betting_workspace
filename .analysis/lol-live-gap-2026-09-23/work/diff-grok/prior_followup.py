"""Reproduce live priors for the worst LoL maps and scan every local tape.

Read-only on esports-trader. GET https://clob.polymarket.com/prices-history only.
"""

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from shared.constants.api import POLYMARKET_CLOB_API, QUOTE_TRAILING_SECONDS
from shared.utils.match_time import HORN_OFFSET_SECONDS
from shared.utils.price_history import last_aligned_pre_anchor_pair, parse_history
from shared.utils.telonex_book import PAIR_SUM_TOLERANCE, normalize_pair_mids

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path("/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23")
TRADER = E / "data" / "trader"
OUT = R / "work" / "diff-grok"


def iso_to_unix(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


def fetch_history(token_id: str, start_ts: int, end_ts: int) -> list[dict[str, float | int]]:
    params = urllib.parse.urlencode(
        {"market": token_id, "fidelity": 1, "startTs": start_ts, "endTs": end_ts}
    )
    url = f"{POLYMARKET_CLOB_API}/prices-history?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": "diff-grok-prior"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    return parse_history(body)


def prior_from_histories(
    yes_history: list, no_history: list, anchor_ts: int, yes_is_radiant: bool
) -> tuple[float | None, object]:
    pair = last_aligned_pre_anchor_pair(yes_history, no_history, anchor_ts)
    if pair is None:
        return None, None
    if yes_is_radiant:
        radiant_mid, dire_mid = pair.left.price, pair.right.price
    else:
        radiant_mid, dire_mid = pair.right.price, pair.left.price
    prior = normalize_pair_mids(
        radiant_mid=radiant_mid, dire_mid=dire_mid, tolerance=PAIR_SUM_TOLERANCE
    )
    return prior, pair


def first_signal(session_path: Path) -> dict | None:
    """First signal that has a prior, plus the first model mid after it."""
    prior_row = None
    with session_path.open() as handle:
        for line in handle:
            if '"kind": "signal"' not in line and '"kind":"signal"' not in line:
                continue
            row = json.loads(line)
            if row.get("kind") != "signal":
                continue
            if prior_row is None and row.get("market_radiant_prior") is not None:
                prior_row = row
            if (
                prior_row is not None
                and row.get("reason") == "model"
                and row.get("market_p_radiant") is not None
            ):
                return {
                    "prior": prior_row["market_radiant_prior"],
                    "prior_second": prior_row.get("second"),
                    "prior_reason": prior_row.get("reason"),
                    "mid": row["market_p_radiant"],
                    "mid_second": row.get("second"),
                    "yes_mid": row.get("yes_mid"),
                    "no_mid": row.get("no_mid"),
                }
    return None


def scan_tapes() -> pd.DataFrame:
    rows = []
    for match_path in sorted(TRADER.glob("*/match.json")):
        session = match_path.parent / "session.jsonl"
        if not session.is_file():
            continue
        meta = json.loads(match_path.read_text())
        hit = first_signal(session)
        if hit is None:
            rows.append(
                {
                    "dir": match_path.parent.name,
                    "game": meta.get("game"),
                    "feed": meta.get("feed_source"),
                    "status": "no_model_prior",
                }
            )
            continue
        gap_c = 100.0 * (float(hit["prior"]) - float(hit["mid"]))
        rows.append(
            {
                "dir": match_path.parent.name,
                "game": meta.get("game"),
                "feed": meta.get("feed_source"),
                "status": "ok",
                "yes_is_radiant": meta["market"]["yes_is_radiant"],
                "prior": hit["prior"],
                "mid": hit["mid"],
                "abs_gap_c": abs(gap_c),
                "gap_c": gap_c,
                "prior_second": hit["prior_second"],
                "mid_second": hit["mid_second"],
                "yes_mid": hit["yes_mid"],
                "no_mid": hit["no_mid"],
            }
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT / "prior_vs_first_mid.csv", index=False)
    return frame


def summarize_scan(frame: pd.DataFrame) -> None:
    print("tapes", len(frame))
    print(frame.groupby(["game", "status"]).size().to_string())
    ok = frame[frame.status == "ok"]
    for game, group in ok.groupby("game"):
        n = len(group)
        print(
            f"{game} n={n} "
            f">5c {(group.abs_gap_c > 5).mean():.3f} ({(group.abs_gap_c > 5).sum()}) "
            f">10c {(group.abs_gap_c > 10).mean():.3f} ({(group.abs_gap_c > 10).sum()}) "
            f"median {group.abs_gap_c.median():.2f} "
            f"mean {group.abs_gap_c.mean():.2f} "
            f"p90 {group.abs_gap_c.quantile(0.9):.2f}"
        )
        for feed, sub in group.groupby("feed"):
            print(
                f"  {feed} n={len(sub)} "
                f">5c {(sub.abs_gap_c > 5).mean():.3f} ({(sub.abs_gap_c > 5).sum()}) "
                f">10c {(sub.abs_gap_c > 10).mean():.3f} ({(sub.abs_gap_c > 10).sum()})"
            )


def reproduce_worst() -> None:
    parity = pd.read_csv(R / "work" / "orchestrator" / "prior_parity_lol.csv")
    worst = parity.reindex(parity.diff_c.abs().sort_values(ascending=False).index).head(15)
    rows = []
    for record in worst.itertuples(index=False):
        meta = json.loads((TRADER / record.dir / "match.json").read_text())
        market = meta["market"]
        horn = iso_to_unix(meta["horn_at_utc"])
        anchor = horn - HORN_OFFSET_SECONDS
        start = anchor - QUOTE_TRAILING_SECONDS
        yes_hist = fetch_history(market["yes_token_id"], start, anchor)
        no_hist = fetch_history(market["no_token_id"], start, anchor)
        yes_is = bool(market["yes_is_radiant"])
        prior, pair = prior_from_histories(yes_hist, no_hist, anchor, yes_is)
        flipped, _ = prior_from_histories(yes_hist, no_hist, anchor, not yes_is)
        session = first_signal(TRADER / record.dir / "session.jsonl")
        yes_age = no_age = None
        yes_px = no_px = None
        yes_ts = no_ts = None
        if pair is not None:
            yes_px, yes_ts = pair.left.price, pair.left.quote_ts
            no_px, no_ts = pair.right.price, pair.right.quote_ts
            yes_age = anchor - yes_ts
            no_age = anchor - no_ts
        mid = None if session is None else session["mid"]
        yes_mid = None if session is None else session["yes_mid"]
        no_mid = None if session is None else session["no_mid"]
        rows.append(
            {
                "dir": record.dir,
                "train_prior": record.train_prior,
                "live_prior": record.live_prior,
                "live_first_mid": record.live_first_mid,
                "diff_c": record.diff_c,
                "yes_is_radiant": yes_is,
                "horn_at_utc": meta["horn_at_utc"],
                "anchor_ts": anchor,
                "map_number": meta.get("map_number"),
                "n_yes": len(yes_hist),
                "n_no": len(no_hist),
                "yes_px": yes_px,
                "no_px": no_px,
                "yes_ts": yes_ts,
                "no_ts": no_ts,
                "yes_age_s": yes_age,
                "no_age_s": no_age,
                "reproduced": prior,
                "flipped": flipped,
                "session_mid": mid,
                "session_yes_mid": yes_mid,
                "session_no_mid": no_mid,
                "repro_minus_live_c": None if prior is None else 100 * (prior - record.live_prior),
                "flip_minus_train_c": None
                if flipped is None
                else 100 * (flipped - record.train_prior),
            }
        )
        print(
            record.dir,
            "live",
            round(record.live_prior, 3),
            "repro",
            None if prior is None else round(prior, 3),
            "flip",
            None if flipped is None else round(flipped, 3),
            "train",
            round(record.train_prior, 3),
            "yes_age_h",
            None if yes_age is None else round(yes_age / 3600, 2),
            "px",
            yes_px,
            no_px,
            "yes_is",
            yes_is,
        )
    pd.DataFrame(rows).to_csv(OUT / "worst15_repro.csv", index=False)


def main() -> None:
    frame = scan_tapes()
    summarize_scan(frame)
    reproduce_worst()


if __name__ == "__main__":
    main()
