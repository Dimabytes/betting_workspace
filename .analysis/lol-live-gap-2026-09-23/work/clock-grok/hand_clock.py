"""Hand timing of two LoL live maps against local livestats and the book."""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from lol.constants import LOL_WINDOWS_DIR
from lol.livestats_frames import (
    FETCH,
    assign_game_times,
    dedup_sort_frames,
    find_spawn_index,
    parse_sides,
    read_archive_payloads,
    wall_us_for_second,
)
from shared.constants.lol import LOL_RAW_TELONEX_DIR
from shared.utils.match_time import parse_utc
from shared.utils.telonex_book import US_PER_SECOND, load_token_book, lookup_market_p_after
from trader.game_profile import GAME_PROFILES
from trader.grid_archive import iter_grid_archive_records
from trader.grid_feed import GridFrameReducer
from trader.grid_widgets import (
    SCOREBOARD_SERVICE,
    TABLE_SERVICE,
    clock_age_seconds,
    parse_frame,
    read_map_scoreboard,
)
from trader.live_feed import MatchPhase
from trader.paths import GRID_STATE_ARCHIVE_FILENAME

WORK = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace/.analysis/lol-live-gap-2026-09-23/work/clock-grok"
)
TRADER = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader/data/trader")
MAPS = ("grid-3000375-m4", "grid-3002603-m1")
WINDOW_END = 540
MID_OFFSETS = (-30, -5, 0, 4, 5, 8, 10, 30)


def pct(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0}
    arr = np.asarray(values, dtype=np.float64)
    qs = np.percentile(arr, [5, 25, 50, 75, 95])
    return {
        "n": int(arr.size),
        "mean": float(arr.mean()),
        "p05": float(qs[0]),
        "p25": float(qs[1]),
        "p50": float(qs[2]),
        "p75": float(qs[3]),
        "p95": float(qs[4]),
    }


def load_candidate(archive_id: str) -> dict[str, object]:
    payload = json.loads((WORK / "candidates.json").read_text())
    for row in payload["in_window"]:
        if row["archive_id"] == archive_id:
            return row
    raise SystemExit(f"missing candidate {archive_id}")


def livestats_kills(game_id: str, loading_anchor_ts: int) -> dict[str, object]:
    payloads = read_archive_payloads(LOL_WINDOWS_DIR, game_id)
    if not payloads:
        raise SystemExit(f"no livestats payloads for {game_id}")
    frames = dedup_sort_frames(payloads)
    spawn_index = find_spawn_index(frames, loading_anchor_ts)
    if spawn_index is None:
        raise SystemExit(f"no spawn frame for {game_id}")
    spawn = frames[spawn_index]
    clock = assign_game_times(frames[spawn_index:])
    sample_keys = sorted(spawn.payload.keys())
    kills: list[dict[str, object]] = []
    prev_r: int | None = None
    prev_d: int | None = None
    for frame in clock.timed:
        if frame.game_time > WINDOW_END:
            break
        parsed = parse_sides(frame.payload)
        if isinstance(parsed, int):
            continue
        deaths_r = sum(player.deaths for player in parsed.blue.players)
        deaths_d = sum(player.deaths for player in parsed.red.players)
        if prev_r is None:
            prev_r = deaths_r
            prev_d = deaths_d
            continue
        dr = deaths_r - prev_r
        dd = deaths_d - prev_d
        prev_r = deaths_r
        prev_d = deaths_d
        if dr <= 0 and dd <= 0:
            continue
        kills.append(
            {
                "total": deaths_r + deaths_d,
                "deaths_blue": deaths_r,
                "deaths_red": deaths_d,
                "delta_blue": dr,
                "delta_red": dd,
                "game_time": frame.game_time,
                "wall": frame.wall_seconds,
                "stamp": frame.payload.get("rfc460Timestamp"),
            }
        )
    return {
        "spawn_wall": spawn.wall_seconds,
        "spawn_stamp": spawn.stamp,
        "frame_keys": sample_keys,
        "pause_count": clock.pause_count,
        "pause_seconds": clock.pause_seconds,
        "pauses": clock.pauses,
        "kills": kills,
        "frames": len(clock.timed),
    }


def grid_pass(archive_id: str, meta: dict[str, object]) -> dict[str, object]:
    market = {
        "outcome_0_name": meta["outcome_0_name"],
        "outcome_1_name": meta["outcome_1_name"],
    }
    reducer = GridFrameReducer(
        int(meta["map_number"]),
        str(market["outcome_0_name"]),
        str(market["outcome_1_name"]),
        GAME_PROFILES["lol"],
    )
    ages: list[float] = []
    publish_delays: list[float] = []
    table_delays: list[float] = []
    clock_gaps: list[float] = []
    events: list[dict[str, object]] = []
    path = TRADER / archive_id / GRID_STATE_ARCHIVE_FILENAME
    for record in iter_grid_archive_records(path):
        received = parse_utc(record["received_at_utc"])
        frame = parse_frame(record["frame"])
        if frame.service == TABLE_SERVICE:
            table_delays.append(float(frame.delay))
        if frame.service == SCOREBOARD_SERVICE and frame.payload:
            board = read_map_scoreboard(frame.payload, int(meta["map_number"]))
            if board is not None and board.game_status == "live":
                age = clock_age_seconds(board.occurred_at, received)
                ages.append(age)
                publish_delays.append(float(board.publish_delay))
                if board.clock_ticking and 0 <= board.clock_seconds <= WINDOW_END:
                    clock_gaps.append(age)
        event = reducer.reduce_frame(frame, received)
        if event is None:
            continue
        snap = event.snapshot
        if snap.phase is not MatchPhase.IN_PROGRESS:
            continue
        events.append(
            {
                "received": received.timestamp(),
                "received_at_utc": event.received_at_utc,
                "second": snap.second,
                "paused": snap.paused,
                "deaths_blue": snap.deaths_radiant,
                "deaths_red": snap.deaths_dire,
                "server_ts": snap.server_timestamp,
                "horn": event.horn_unix_seconds,
            }
        )
    kills: list[dict[str, object]] = []
    prev_r: int | None = None
    prev_d: int | None = None
    for event in events:
        if event["paused"] or not (0 <= int(event["second"]) <= WINDOW_END):
            if prev_r is None and int(event["second"]) >= 0:
                prev_r = int(event["deaths_blue"])
                prev_d = int(event["deaths_red"])
            continue
        deaths_r = int(event["deaths_blue"])
        deaths_d = int(event["deaths_red"])
        if prev_r is None:
            prev_r = deaths_r
            prev_d = deaths_d
            continue
        dr = deaths_r - prev_r
        dd = deaths_d - prev_d
        prev_r = deaths_r
        prev_d = deaths_d
        if dr <= 0 and dd <= 0:
            continue
        kills.append(
            {
                **event,
                "total": deaths_r + deaths_d,
                "delta_blue": dr,
                "delta_red": dd,
            }
        )
    return {
        "events": events,
        "kills": kills,
        "scoreboard_age_s": pct(ages),
        "scoreboard_age_while_clock_0_540": pct(clock_gaps),
        "publish_delay": pct(publish_delays),
        "table_delay": pct(table_delays),
    }


def load_signals(archive_id: str) -> list[dict[str, object]]:
    signals: list[dict[str, object]] = []
    path = TRADER / archive_id / "session.jsonl"
    with path.open() as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("kind") != "signal":
                continue
            signals.append(
                {
                    "second": record.get("second"),
                    "market_p_radiant": record.get("market_p_radiant"),
                    "market_radiant_prior": record.get("market_radiant_prior"),
                    "yes_mid": record.get("yes_mid"),
                    "no_mid": record.get("no_mid"),
                    "reason": record.get("reason"),
                }
            )
    return signals


def align_signals(
    events: list[dict[str, object]], signals: list[dict[str, object]]
) -> tuple[list[dict[str, object]], float]:
    """Greedy in-order match of signal.second to feed-event.second."""
    aligned: list[dict[str, object]] = []
    index = 0
    matched = 0
    for signal in signals:
        second = signal["second"]
        while index < len(events) and events[index]["second"] != second:
            if int(events[index]["second"]) < int(second):
                index += 1
                continue
            break
        if index < len(events) and events[index]["second"] == second:
            aligned.append({**signal, "received": events[index]["received"]})
            matched += 1
            index += 1
        else:
            aligned.append({**signal, "received": None})
    rate = matched / len(signals) if signals else 0.0
    return aligned, rate


def nearest_signal(signals: list[dict[str, object]], wall: float) -> dict[str, object] | None:
    best: dict[str, object] | None = None
    best_gap = 1e18
    for signal in signals:
        received = signal.get("received")
        if not isinstance(received, float):
            continue
        gap = abs(received - wall)
        if gap < best_gap:
            best = signal
            best_gap = gap
    if best is None or best_gap > 3:
        return None
    return best


def mid_at(books: tuple[object, object] | None, wall: float, offset: int) -> float | None:
    if books is None:
        return None
    radiant, dire = books
    target_us = round(wall * US_PER_SECOND) + offset * US_PER_SECOND
    return lookup_market_p_after(radiant, dire, target_us, 0)


def analyze(archive_id: str) -> dict[str, object]:
    meta = load_candidate(archive_id)
    game_id = str(meta["esports_game_id"])
    live = livestats_kills(game_id, int(meta["loading_anchor_ts"]))
    grid = grid_pass(archive_id, meta)
    signals = load_signals(archive_id)
    aligned, align_rate = align_signals(grid["events"], signals)
    horn = parse_utc(str(meta["horn_at_utc"])).timestamp()
    spawn_wall = float(live["spawn_wall"])
    pauses = live["pauses"]

    offsets: list[float] = []
    internal: list[float] = []
    for event in grid["events"]:
        if event["paused"]:
            continue
        second = int(event["second"])
        if second < 0 or second > WINDOW_END:
            continue
        wall_us = wall_us_for_second(spawn_wall, pauses, second)
        offsets.append(float(event["received"]) - wall_us / US_PER_SECOND)
        internal.append(float(event["received"]) - (float(event["horn"]) + second))

    grid_by_total = {int(kill["total"]): kill for kill in grid["kills"]}
    rows: list[dict[str, object]] = []
    end_us = round((spawn_wall + WINDOW_END + 40) * US_PER_SECOND)
    start_us = round((spawn_wall - 120) * US_PER_SECOND)
    if meta["yes_is_radiant"]:
        radiant_token, dire_token = str(meta["yes_token_id"]), str(meta["no_token_id"])
    else:
        radiant_token, dire_token = str(meta["no_token_id"]), str(meta["yes_token_id"])
    radiant_book = load_token_book(
        token_id=radiant_token, start_us=start_us, end_us=end_us, telonex_root=LOL_RAW_TELONEX_DIR
    )
    dire_book = load_token_book(
        token_id=dire_token, start_us=start_us, end_us=end_us, telonex_root=LOL_RAW_TELONEX_DIR
    )
    books = None if radiant_book is None or dire_book is None else (radiant_book, dire_book)

    signed_by_offset: dict[int, list[float]] = {offset: [] for offset in MID_OFFSETS if offset != 0}
    for kill in live["kills"]:
        total = int(kill["total"])
        grid_kill = grid_by_total.get(total)
        direction = 0.0
        if int(kill["delta_red"]) > 0 and int(kill["delta_blue"]) <= 0:
            direction = 1.0
        elif int(kill["delta_blue"]) > 0 and int(kill["delta_red"]) <= 0:
            direction = -1.0
        wall = float(kill["wall"])
        mids = {str(offset): mid_at(books, wall, offset) for offset in MID_OFFSETS}
        base = mids["0"]
        if base is not None and direction != 0.0:
            for offset in signed_by_offset:
                later = mids[str(offset)]
                if later is not None:
                    signed_by_offset[offset].append(direction * (later - base))
        signal_at_grid = None
        if grid_kill is not None:
            signal_at_grid = nearest_signal(aligned, float(grid_kill["received"]))
        signal_at_stamp = nearest_signal(aligned, wall)
        rows.append(
            {
                "total": total,
                "game_time": round(float(kill["game_time"]), 3),
                "rfc460": kill["stamp"],
                "livestats_wall": wall,
                "delta_blue": kill["delta_blue"],
                "delta_red": kill["delta_red"],
                "grid_received": None if grid_kill is None else grid_kill["received"],
                "grid_second": None if grid_kill is None else grid_kill["second"],
                "grid_minus_livestats_s": (
                    None if grid_kill is None else float(grid_kill["received"]) - wall
                ),
                "grid_second_minus_game_time": (
                    None if grid_kill is None else int(grid_kill["second"]) - float(kill["game_time"])
                ),
                "occurred_at_minus_rfc460": (
                    None if grid_kill is None else int(grid_kill["server_ts"]) - wall
                ),
                "side_match": (
                    None
                    if grid_kill is None
                    else (
                        int(grid_kill["delta_blue"]) > 0
                        and int(kill["delta_blue"]) > 0
                        or int(grid_kill["delta_red"]) > 0
                        and int(kill["delta_red"]) > 0
                    )
                ),
                "mids_from_rfc460": mids,
                "signal_p_at_grid": None if signal_at_grid is None else signal_at_grid["market_p_radiant"],
                "signal_second_at_grid": None if signal_at_grid is None else signal_at_grid["second"],
                "signal_p_near_rfc460": None if signal_at_stamp is None else signal_at_stamp["market_p_radiant"],
            }
        )

    priors = [
        signal["market_radiant_prior"]
        for signal in signals
        if isinstance(signal["market_radiant_prior"], float)
    ]
    prior_at_spawn = mid_at(books, spawn_wall, -1) if books else None
    prior_at_horn_minus_90 = mid_at(books, horn, -90) if books else None
    paired = [row for row in rows if row["grid_received"] is not None]
    receive_lags = [float(row["grid_minus_livestats_s"]) for row in paired]
    second_gaps = [float(row["grid_second_minus_game_time"]) for row in paired]
    side_ok = [row for row in paired if row["side_match"]]
    summary = {
        "archive_id": archive_id,
        "horn_at_utc": meta["horn_at_utc"],
        "esports_game_id": game_id,
        "execution_mode": meta["execution_mode"],
        "grid_delay_s_meta": meta["grid_delay_s"],
        "yes_is_radiant": meta["yes_is_radiant"],
        "spawn_stamp": live["spawn_stamp"],
        "spawn_wall": spawn_wall,
        "horn_minus_spawn_s": horn - spawn_wall,
        "frame_keys": live["frame_keys"],
        "pause_count": live["pause_count"],
        "pause_seconds": live["pause_seconds"],
        "livestats_frames_through_end": live["frames"],
        "livestats_kills_0_540": len(live["kills"]),
        "grid_events": len(grid["events"]),
        "grid_kills_0_540": len(grid["kills"]),
        "paired_kills": len(paired),
        "side_matched_kills": len(side_ok),
        "signals": len(signals),
        "signal_align_rate": align_rate,
        "scoreboard_age_s": grid["scoreboard_age_s"],
        "scoreboard_age_while_clock_0_540": grid["scoreboard_age_while_clock_0_540"],
        "publish_delay": grid["publish_delay"],
        "table_delay": grid["table_delay"],
        "tick_offset_received_minus_livestats_wall": pct(offsets),
        "tick_internal_lag": pct(internal),
        "kill_receive_minus_rfc460": pct(receive_lags),
        "kill_grid_second_minus_livestats_game_time": pct(second_gaps),
        "signed_mid_move_after_rfc460": {str(k): pct(v) for k, v in signed_by_offset.items()},
        "books_loaded": books is not None,
        "live_prior_from_session": None if not priors else float(priors[0]),
        "book_prior_at_spawn_minus_1s": prior_at_spawn,
        "book_prior_at_horn_minus_90s": prior_at_horn_minus_90,
        "window_file": str(FETCH.archive_path(LOL_WINDOWS_DIR, game_id)),
    }
    csv_path = WORK / f"kills_{archive_id}.csv"
    fieldnames = [
        "total",
        "game_time",
        "rfc460",
        "livestats_wall",
        "delta_blue",
        "delta_red",
        "grid_received",
        "grid_second",
        "grid_minus_livestats_s",
        "grid_second_minus_game_time",
        "occurred_at_minus_rfc460",
        "side_match",
        "signal_p_at_grid",
        "signal_second_at_grid",
        "signal_p_near_rfc460",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames + [f"mid_{o}" for o in MID_OFFSETS])
        writer.writeheader()
        for row in rows:
            flat = {key: row[key] for key in fieldnames}
            mids = row["mids_from_rfc460"]
            for offset in MID_OFFSETS:
                flat[f"mid_{offset}"] = mids[str(offset)]
            writer.writerow(flat)
    summary["kills_csv"] = str(csv_path)
    summary["kill_rows_head"] = rows[:8]
    return summary


def main() -> None:
    summaries = [analyze(archive_id) for archive_id in MAPS]
    path = WORK / "hand_clock.json"
    path.write_text(json.dumps(summaries, indent=2, default=str))
    print("wrote", path)
    for summary in summaries:
        print("---", summary["archive_id"], summary["horn_at_utc"])
        for key in (
            "horn_minus_spawn_s",
            "paired_kills",
            "side_matched_kills",
            "signal_align_rate",
            "books_loaded",
            "scoreboard_age_s",
            "table_delay",
            "tick_offset_received_minus_livestats_wall",
            "tick_internal_lag",
            "kill_receive_minus_rfc460",
            "kill_grid_second_minus_livestats_game_time",
            "signed_mid_move_after_rfc460",
            "live_prior_from_session",
            "book_prior_at_spawn_minus_1s",
            "book_prior_at_horn_minus_90s",
            "frame_keys",
        ):
            print(key, summary[key])


if __name__ == "__main__":
    main()
