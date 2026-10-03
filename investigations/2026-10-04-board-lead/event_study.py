"""Board-lead step 0: event study on receipt-time clocks over archive schedules.

Board receipt = schedule kill_gates[].received_ns; table receipt = first tick
with deaths_side >= the gate's awaited_deaths. For each pending victim side we
sample the normalized Telonex pair mid on wall-clock offsets from board_rx and
report the signed move in the killer's favor: share done by board_rx/table_rx,
remainder to +120/+300s, monthly splits, and maker feasibility (winner-token
spread plus best-bid lift in the first 3s).

Go criterion (plan): mean remainder board_rx->+120 >= 2c LoL / >= 1c Dota with
<30% of the +300s move done by board_rx. If LoL fails, continue Dota only.

Read-only on esports-trader data; run from the esports-trader repo root:

    PYTHONPATH=src nice -n 10 uv run python \
        ../betting_workspace/investigations/2026-10-04-board-lead/event_study.py
"""

from __future__ import annotations

import argparse
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from lol.constants import LOL_GRID_START_SECOND, LOL_TRAIN_END_SECOND
from shared.constants.dataset import MODEL_START_SECOND, TRAIN_LAG_SECONDS
from shared.constants.lol import (
    LOL_GAME_FEATURES_PATH,
    LOL_MODELS_DIR,
    LOL_RAW_TELONEX_DIR,
    LOL_VALIDATION_PATH,
)
from shared.constants.paths import (
    DATA_DIR,
    GAME_FEATURES_DATASET_PATH,
    PRODUCTION_MODEL_DIR,
    RAW_TELONEX_POLYMARKET_DIR,
    VALIDATION_DATASET_PATH,
)
from shared.constants.strategy import BUY_CUTOFF_SECOND
from shared.utils.dota_features import (
    DOTA_HISTORY_FIELDS,
    GRID_HISTORY_POLICY,
    attach_catalog_features,
)
from shared.utils.gbm import load_predictor
from shared.utils.telonex_book import (
    PAIR_SUM_TOLERANCE,
    TokenBook,
    find_asof_quote,
    load_token_book,
    normalize_pair_mids,
)

SCHEDULES_DIR = DATA_DIR / "archive_index" / "schedules" / "trader"
NS_PER_SECOND = 1_000_000_000
BASELINE_OFFSET_S = 10.0
CURVE_OFFSETS_S = (
    0.0,
    0.5,
    1.0,
    2.0,
    3.0,
    4.0,
    5.0,
    6.0,
    8.0,
    10.0,
    12.0,
    15.0,
    20.0,
    30.0,
    45.0,
    60.0,
    90.0,
    120.0,
    180.0,
    240.0,
    300.0,
)
MAKER_OFFSETS_S = (1.0, 2.0, 3.0)
SHARE_MIN_TOTAL_C = 0.5
SENSITIVITY_MATCHES = 60

SIDES = ("radiant", "dire")


@dataclass(frozen=True)
class KillEvent:
    """One pending-victim record: board saw kills the table has not shown yet."""

    match_id: str
    archive_id: str
    victim: str
    awaited: int
    pending: int
    board_rx_ns: int
    table_rx_ns: int | None


@dataclass(frozen=True)
class PairBooks:
    """Radiant/dire token books for one schedule's measurement window."""

    radiant: TokenBook | None
    dire: TokenBook | None


def offset_column(offset: float) -> str:
    return f"mv_{offset}".replace(".", "p").rstrip("0").rstrip("p")


def extract_kill_events(payload: dict[str, Any]) -> list[KillEvent]:
    """Pending victim sides from kill_gates; one event per (side, awaited) total."""
    ticks = payload["ticks"]
    tick_ns = [int(tick["received_ns"]) for tick in ticks]
    deaths = {side: [int(tick[f"deaths_{side}"]) for tick in ticks] for side in SIDES}
    seen: set[tuple[str, int]] = set()
    events: list[KillEvent] = []
    match_id = str(payload["identity"]["match_id"])
    archive_id = str(payload["identity"]["archive_id"])
    for gate in payload["kill_gates"]:
        board_rx = int(gate["received_ns"])
        index = bisect_right(tick_ns, board_rx) - 1
        for side in SIDES:
            awaited = int(gate[side]["awaited_deaths"])
            current = deaths[side][index] if index >= 0 else 0
            pending = awaited - current
            if pending <= 0 or (side, awaited) in seen:
                continue
            seen.add((side, awaited))
            table_rx = first_death_reach(tick_ns, deaths[side], board_rx, awaited)
            events.append(
                KillEvent(
                    match_id=match_id,
                    archive_id=archive_id,
                    victim=side,
                    awaited=awaited,
                    pending=pending,
                    board_rx_ns=board_rx,
                    table_rx_ns=table_rx,
                )
            )
    return events


def first_death_reach(
    tick_ns: list[int], deaths_side: list[int], after_ns: int, awaited: int
) -> int | None:
    """First tick received after `after_ns` whose table deaths cover `awaited`."""
    index = bisect_right(tick_ns, after_ns)
    while index < len(tick_ns):
        if deaths_side[index] >= awaited:
            return tick_ns[index]
        index += 1
    return None


def load_pair_books(
    events: list[KillEvent], identity: dict[str, Any], telonex_root: Path
) -> PairBooks:
    """Both tokens' books covering every event's baseline..+300s window."""
    start_ns = min(event.board_rx_ns for event in events) - 15 * NS_PER_SECOND
    end_ns = (
        max(
            max(event.board_rx_ns + 300 * NS_PER_SECOND, event.table_rx_ns or 0)
            for event in events
        )
        + 15 * NS_PER_SECOND
    )
    yes_radiant = bool(identity["yes_is_radiant"])
    radiant_token = identity["yes_token_id"] if yes_radiant else identity["no_token_id"]
    dire_token = identity["no_token_id"] if yes_radiant else identity["yes_token_id"]
    return PairBooks(
        radiant=load_token_book(
            token_id=radiant_token,
            start_us=start_ns // 1000,
            end_us=end_ns // 1000,
            telonex_root=telonex_root,
        ),
        dire=load_token_book(
            token_id=dire_token,
            start_us=start_ns // 1000,
            end_us=end_ns // 1000,
            telonex_root=telonex_root,
        ),
    )


def pair_mid_at(books: PairBooks, target_ns: int) -> float | None:
    """Age-gated two-sided normalized market_p_radiant at one wall instant."""
    if books.radiant is None or books.dire is None:
        return None
    target_us = target_ns // 1000
    radiant = find_asof_quote(books.radiant, target_us)
    dire = find_asof_quote(books.dire, target_us)
    if radiant.quote is None or dire.quote is None:
        return None
    return normalize_pair_mids(
        radiant_mid=radiant.quote.mid,
        dire_mid=dire.quote.mid,
        tolerance=PAIR_SUM_TOLERANCE,
    )


def side_quote(book: TokenBook | None, target_ns: int):
    if book is None:
        return None
    return find_asof_quote(book, target_ns // 1000).quote


def measure_event(event: KillEvent, books: PairBooks) -> dict[str, Any]:
    """All per-event measurements: curve, shares, remainders, maker window."""
    direction = -1.0 if event.victim == "radiant" else 1.0
    row: dict[str, Any] = {
        "match_id": event.match_id,
        "archive_id": event.archive_id,
        "victim": event.victim,
        "awaited": event.awaited,
        "pending": event.pending,
        "board_rx_ns": event.board_rx_ns,
        "month": datetime.fromtimestamp(event.board_rx_ns / NS_PER_SECOND, UTC).strftime(
            "%Y-%m"
        ),
        "table_lag_s": (
            (event.table_rx_ns - event.board_rx_ns) / NS_PER_SECOND
            if event.table_rx_ns is not None
            else np.nan
        ),
        "table_missing": event.table_rx_ns is None,
    }
    base_ns = event.board_rx_ns - int(BASELINE_OFFSET_S * NS_PER_SECOND)
    base = pair_mid_at(books, base_ns)
    row["base_mid"] = base
    mids = {
        offset: pair_mid_at(books, event.board_rx_ns + int(offset * NS_PER_SECOND))
        for offset in CURVE_OFFSETS_S
    }
    for offset, mid in mids.items():
        row[offset_column(offset)] = (
            direction * (mid - base) * 100.0 if base is not None and mid is not None else np.nan
        )
    table_mid = pair_mid_at(books, event.table_rx_ns) if event.table_rx_ns else None
    row["mv_table"] = (
        direction * (table_mid - base) * 100.0
        if base is not None and table_mid is not None
        else np.nan
    )
    row["n_mids"] = sum(mid is not None for mid in mids.values())
    winner = books.dire if event.victim == "radiant" else books.radiant
    loser = books.radiant if event.victim == "radiant" else books.dire
    winner0 = side_quote(winner, event.board_rx_ns)
    loser0 = side_quote(loser, event.board_rx_ns)
    row["winner_spread_0"] = (
        (winner0.ask - winner0.bid) * 100.0 if winner0 is not None else np.nan
    )
    for offset in MAKER_OFFSETS_S:
        target = event.board_rx_ns + int(offset * NS_PER_SECOND)
        winner_now = side_quote(winner, target)
        loser_now = side_quote(loser, target)
        key = str(offset).rstrip("0").rstrip(".")
        row[f"winner_bid_lift_{key}"] = (
            (winner_now.bid - winner0.bid) * 100.0
            if winner_now is not None and winner0 is not None
            else np.nan
        )
        row[f"winner_ask_lift_{key}"] = (
            (winner_now.ask - winner0.ask) * 100.0
            if winner_now is not None and winner0 is not None
            else np.nan
        )
        row[f"loser_ask_drop_{key}"] = (
            (loser0.ask - loser_now.ask) * 100.0
            if loser_now is not None and loser0 is not None
            else np.nan
        )
    return row


def aggregate_share(frame: pd.DataFrame, at_column: str) -> float:
    """Sum of signed move at `at_column` over sum of signed move to +300s."""
    both = frame[[at_column, "mv_300"]].dropna()
    total = both["mv_300"].sum()
    if not len(both) or abs(total) < 1e-9:
        return np.nan
    return float(both[at_column].sum() / total)


def median_share(frame: pd.DataFrame, at_column: str) -> tuple[float, int]:
    """Median per-event share among events whose +300s move reaches 0.5c."""
    both = frame[[at_column, "mv_300"]].dropna()
    both = both[both["mv_300"].abs() >= SHARE_MIN_TOTAL_C]
    if both.empty:
        return np.nan, 0
    return float((both[at_column] / both["mv_300"]).median()), len(both)


def fmt(value: float, digits: int = 2) -> str:
    return "n/a" if value is None or (isinstance(value, float) and np.isnan(value)) else f"{value:.{digits}f}"


def game_report(game: str, frame: pd.DataFrame, lines: list[str]) -> None:
    lines.append(f"### {game} — {len(frame)} events, {frame['match_id'].nunique()} matches")
    lines.append("")
    lines.append("Event curve, signed move in killer direction (c):")
    lines.append("")
    lines.append("| offset | mean | median | p25 | p75 | n |")
    lines.append("|---|---|---|---|---|---|")
    for offset in CURVE_OFFSETS_S:
        column = offset_column(offset)
        series = frame[column].dropna()
        lines.append(
            f"| +{offset:g}s | {fmt(series.mean())} | {fmt(series.median())} | "
            f"{fmt(series.quantile(0.25))} | {fmt(series.quantile(0.75))} | {len(series)} |"
        )
    for label, column in (("table_rx", "mv_table"),):
        series = frame[column].dropna()
        lines.append(
            f"| {label} | {fmt(series.mean())} | {fmt(series.median())} | "
            f"{fmt(series.quantile(0.25))} | {fmt(series.quantile(0.75))} | {len(series)} |"
        )
    lines.append("")
    agg_board = aggregate_share(frame, "mv_0")
    med_board, n_board = median_share(frame, "mv_0")
    agg_table = aggregate_share(frame, "mv_table")
    med_table, n_table = median_share(frame, "mv_table")
    lines.append(
        f"Share of +300s move: board_rx agg {fmt(agg_board*100,1)}% / median "
        f"{fmt(med_board*100,1)}% (n={n_board}); table_rx agg {fmt(agg_table*100,1)}% / "
        f"median {fmt(med_table*100,1)}% (n={n_table})"
    )
    rem120 = frame["mv_120"] - frame["mv_0"]
    rem300 = frame["mv_300"] - frame["mv_0"]
    lines.append(
        f"Remainder board_rx->+120s: mean {fmt(rem120.mean())}c median "
        f"{fmt(rem120.median())}c p25 {fmt(rem120.quantile(0.25))} p75 "
        f"{fmt(rem120.quantile(0.75))} | ->+300s: mean {fmt(rem300.mean())}c "
        f"median {fmt(rem300.median())}c"
    )
    lines.append("")
    lines.append("| month | n | mean rem +120c | agg share@board | med table lag s |")
    lines.append("|---|---|---|---|---|")
    monthly = frame.assign(rem120=rem120).groupby("month")
    for month, part in monthly:
        lines.append(
            f"| {month} | {len(part)} | {fmt(part['rem120'].mean())} | "
            f"{fmt(aggregate_share(part, 'mv_0')*100,1)}% | {fmt(part['table_lag_s'].median(),1)} |"
        )
    lines.append("")
    spread = frame["winner_spread_0"].dropna()
    lines.append(
        f"Winner token @board_rx: median spread {fmt(spread.median())}c, "
        f"<=2c in {fmt((spread <= 2.0).mean()*100,1)}% of {len(spread)}"
    )
    for offset in MAKER_OFFSETS_S:
        key = str(offset).rstrip("0").rstrip(".")
        lift = frame[f"winner_bid_lift_{key}"].dropna()
        ask_lift = frame[f"winner_ask_lift_{key}"].dropna()
        drop = frame[f"loser_ask_drop_{key}"].dropna()
        lines.append(
            f"+{offset:g}s: winner bid lift median {fmt(lift.median())}c, "
            f"<=0.5c in {fmt((lift <= 0.5).mean()*100,1)}% | winner ask lift "
            f"{fmt(ask_lift.median())}c | loser ask drop {fmt(drop.median())}c"
        )
    lag = frame["table_lag_s"].dropna()
    lines.append(
        f"Board->table lag: median {fmt(lag.median(),1)}s p90 "
        f"{fmt(lag.quantile(0.9),1)}s | table never reached in "
        f"{fmt(frame['table_missing'].mean()*100,1)}% of events"
    )
    lines.append("")


def run_event_study(out: Path, limit: int | None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for game in ("lol", "dota"):
        telonex_root = LOL_RAW_TELONEX_DIR if game == "lol" else RAW_TELONEX_POLYMARKET_DIR
        paths = sorted((SCHEDULES_DIR / game).glob("*.json"))
        if limit is not None:
            paths = paths[:limit]
        for index, path in enumerate(paths, start=1):
            payload = json.loads(path.read_text())
            if payload["identity"]["feed_source"] != "grid":
                skipped[game] = skipped.get(game, 0) + 1
                continue
            events = extract_kill_events(payload)
            if not events:
                continue
            books = load_pair_books(events, payload["identity"], telonex_root)
            for event in events:
                row = measure_event(event, books)
                row["game"] = game
                rows.append(row)
            if index % 50 == 0:
                print(f"{game}: {index}/{len(paths)} schedules, {len(rows)} events", flush=True)
    frame = pd.DataFrame(rows)
    frame.to_parquet(out / "events.parquet")
    curve_rows = []
    for (game, month), part in frame.groupby(["game", "month"]):
        for offset in CURVE_OFFSETS_S:
            series = part[offset_column(offset)].dropna()
            curve_rows.append(
                {
                    "game": game,
                    "month": month,
                    "offset_s": offset,
                    "mean_c": series.mean(),
                    "median_c": series.median(),
                    "n": len(series),
                }
            )
    pd.DataFrame(curve_rows).to_csv(out / "curves.csv", index=False)
    if skipped:
        print("non-grid schedules skipped:", skipped)
    return frame


def sensitivity_check(game: str) -> dict[str, float]:
    """Mean |delta shift| of the frozen production model under one naked death.

    Validation rows are re-attached exactly like the trainer does; then
    deaths_<side> += 1 on every row mirrors a board tick's board-implied deaths.
    """
    rng = np.random.default_rng(0)
    if game == "dota":
        frame = pd.read_parquet(VALIDATION_DATASET_PATH)
        usable = frame.loc[
            (frame["second"] >= MODEL_START_SECOND)
            & (frame["second"] < BUY_CUTOFF_SECOND)
            & (frame["market_status"] == "ok")
        ]
        tape_path, model_dir, lag = (
            GAME_FEATURES_DATASET_PATH,
            PRODUCTION_MODEL_DIR,
            TRAIN_LAG_SECONDS,
        )
    else:
        frame = pd.read_parquet(LOL_VALIDATION_PATH)
        usable = frame.loc[
            (frame["second"] >= LOL_GRID_START_SECOND)
            & (frame["second"] <= LOL_TRAIN_END_SECOND)
            & frame["signal_market_p_radiant_300s"].notna()
        ]
        tape_path, model_dir, lag = (
            LOL_GAME_FEATURES_PATH,
            LOL_MODELS_DIR / "production",
            0,
        )
    match_ids = np.sort(usable["match_id"].unique())
    sampled = rng.choice(match_ids, size=min(SENSITIVITY_MATCHES, len(match_ids)), replace=False)
    part = usable.loc[usable["match_id"].isin(sampled)]
    tape = pd.read_parquet(
        tape_path,
        columns=["match_id", "game_second", *DOTA_HISTORY_FIELDS],
        filters=[("match_id", "in", sampled.tolist())],
    )
    enriched = attach_catalog_features(
        part,
        tape,
        key_seconds=part["second"] - lag,
        start_second=GRID_HISTORY_POLICY.start_second,
    )
    predictor = load_predictor(model_dir)
    features = enriched[list(predictor.feature_names)].assign(
        second=enriched["second"] - lag
    )
    base = np.asarray(predictor.predict(features))
    result: dict[str, float] = {"rows": float(len(features)), "matches": float(len(sampled))}
    for side in SIDES:
        bumped = features.assign(**{f"deaths_{side}": features[f"deaths_{side}"] + 1.0})
        delta = np.asarray(predictor.predict(bumped)) - base
        result[f"mean_delta_{side}_c"] = float(delta.mean() * 100.0)
        result[f"mean_abs_delta_{side}_c"] = float(np.abs(delta).mean() * 100.0)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sensitivity", action="store_true")
    parser.add_argument("--events", type=Path, default=None, help="reuse events.parquet")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.events is not None:
        frame = pd.read_parquet(args.events)
    else:
        frame = run_event_study(args.out, args.limit)
    lines: list[str] = ["# Board-lead step 0: mid move from board receipt", ""]
    for game in ("lol", "dota"):
        game_report(game, frame.loc[frame["game"] == game], lines)
    lines.append("## Verdict")
    for game, threshold in (("lol", 2.0), ("dota", 1.0)):
        part = frame.loc[frame["game"] == game]
        remainder = (part["mv_120"] - part["mv_0"]).mean()
        share = aggregate_share(part, "mv_0")
        verdict = (
            "GO"
            if pd.notna(remainder) and remainder >= threshold and share < 0.30
            else "FAIL"
        )
        lines.append(
            f"- {game}: remainder +120s mean {fmt(remainder)}c (needs >= {threshold:g}c), "
            f"agg share@board {fmt(share*100,1)}% (needs <30%) -> {verdict}"
        )
    if args.sensitivity:
        lines.append("")
        lines.append("## Frozen-model sensitivity to one naked death (validation subsample)")
        for game in ("lol", "dota"):
            stats = sensitivity_check(game)
            lines.append(
                f"- {game}: n={int(stats['rows'])} rows / {int(stats['matches'])} matches | "
                f"deaths_radiant+1: mean {fmt(stats['mean_delta_radiant_c'])}c "
                f"|delta| {fmt(stats['mean_abs_delta_radiant_c'])}c | "
                f"deaths_dire+1: mean {fmt(stats['mean_delta_dire_c'])}c "
                f"|delta| {fmt(stats['mean_abs_delta_dire_c'])}c"
            )
    report = "\n".join(lines)
    (args.out / "report.md").write_text(report + "\n")
    print("\n" + report)


if __name__ == "__main__":
    main()
