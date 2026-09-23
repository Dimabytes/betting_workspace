"""Classify live fills by time since the scoreboard first showed the latest kill.

Scoreboard kills: series_scoreboard_v2 kill-count increments on the pinned map.
Table deaths: series_table death-sum increments for the victim side.
Mids: session signal yes_mid/no_mid at the aligned GRID receipt.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import numpy as np

E = Path("/Users/dimabytes/work/polymarket/dota_2_bot/esports-trader")
R = Path(
    "/Users/dimabytes/work/polymarket/dota_2_bot/betting_workspace"
    "/.analysis/lol-live-gap-2026-09-23"
)
OUT = R / "work" / "clock-grok" / "kill_fills.json"
TRADER = E / "data" / "trader"
WORKERS = 6
MARKOUT_SLACK_S = 15.0
CANCEL_S = 12.0
HORIZONS = (10, 30, 60)


def _find_window(hay: np.ndarray, needle: np.ndarray) -> int | None:
    n = int(needle.shape[0])
    m = int(hay.shape[0])
    if n == 0 or n > m:
        return None
    candidates = np.flatnonzero(hay[: m - n + 1] == needle[0])
    for start in candidates.tolist():
        if np.array_equal(hay[start : start + n], needle):
            return int(start)
    return None


def _greedy_pairs(ev: np.ndarray, sg: np.ndarray) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    ei = 0
    n = int(ev.shape[0])
    for si, sec in enumerate(sg.tolist()):
        while ei < n and int(ev[ei]) < sec:
            ei += 1
        if ei < n and int(ev[ei]) == sec:
            pairs.append((ei, si))
            ei += 1
    return pairs


def _kills(archive: Path, map_number: int, radiant_side: str, other_side: str) -> list[dict]:
    """One dict per scoreboard kill. `side` on the scoreboard is the killer."""
    from trader.grid_archive import iter_grid_archive_records
    from trader.grid_widgets import SCOREBOARD_SERVICE, TABLE_SERVICE, parse_frame, read_map_scoreboard, read_net_worth
    from shared.utils.match_time import parse_utc

    sides = (radiant_side, other_side)
    other = {radiant_side: other_side, other_side: radiant_side}
    team_side: dict[str, str] = {}
    prev_kills = {radiant_side: 0, other_side: 0}
    prev_deaths = {radiant_side: 0, other_side: 0}
    sb: list[dict] = []
    tb: dict[str, list[float]] = {radiant_side: [], other_side: []}
    for record in iter_grid_archive_records(archive):
        recv = parse_utc(record["received_at_utc"]).timestamp()
        frame = parse_frame(record["frame"])
        if not frame.payload:
            continue
        if frame.service == SCOREBOARD_SERVICE:
            board = read_map_scoreboard(frame.payload, map_number)
            if board is None:
                continue
            for team in board.teams:
                team_side[team.team_id] = team.side
            occ = None
            if board.occurred_at:
                try:
                    occ = parse_utc(board.occurred_at).timestamp()
                except ValueError:
                    occ = None
            for side in sides:
                kills = sum(team.kills for team in board.teams if team.side == side)
                if kills <= prev_kills[side]:
                    continue
                for _ in range(prev_kills[side] + 1, kills + 1):
                    sb.append(
                        {
                            "victim": other[side],
                            "sb_recv": recv,
                            "sb_occ": occ,
                        }
                    )
                prev_kills[side] = kills
        elif frame.service == TABLE_SERVICE:
            table = read_net_worth(frame.payload, frame.delay)
            if table is None or table.game_number != map_number:
                continue
            for side in sides:
                deaths = sum(
                    player.deaths
                    for player in table.players
                    if team_side.get(player.team_id) == side
                )
                if deaths <= prev_deaths[side]:
                    continue
                for _ in range(prev_deaths[side] + 1, deaths + 1):
                    tb[side].append(recv)
                prev_deaths[side] = deaths
    cursor = {radiant_side: 0, other_side: 0}
    matched = 0
    lags: list[float] = []
    for kill in sb:
        victim = kill["victim"]
        deaths = tb[victim]
        i = cursor[victim]
        while i < len(deaths) and deaths[i] < kill["sb_recv"] - 1.0:
            i += 1
        if i < len(deaths) and deaths[i] - kill["sb_recv"] <= 40.0:
            kill["tb_recv"] = deaths[i]
            lags.append(deaths[i] - kill["sb_recv"])
            matched += 1
            i += 1
        else:
            kill["tb_recv"] = None
        cursor[victim] = i
    return sb, matched, lags


def extract_map(archive_dir: str) -> dict:
    from shared.utils.match_time import parse_utc
    from trader.game_profile import GAME_PROFILES
    from trader.grid_archive import iter_grid_archive_records
    from trader.grid_feed import GridFrameReducer, GridOrientationError, replay_grid_records
    from trader.paths import GRID_STATE_ARCHIVE_FILENAME

    path = Path(archive_dir)
    try:
        meta = json.loads((path / "match.json").read_text())
        game = meta.get("game") or "dota"
        if game not in ("lol", "dota"):
            return {"skip": "game"}
        if game == "dota" and meta.get("feed_source") != "grid":
            return {"skip": "not_grid"}
        session_path = path / "session.jsonl"
        if not session_path.is_file():
            return {"skip": "no_session"}
        mode = None
        signals: list[dict] = []
        fills: list[dict] = []
        for line in session_path.open():
            row = json.loads(line)
            kind = row.get("kind")
            if kind == "session_start":
                mode = row.get("execution_mode")
            elif kind == "signal":
                signals.append(row)
            elif kind in ("fill", "late_fill"):
                fills.append(row)
        if mode != "live" or not fills:
            return {"skip": "no_live_fills" if mode == "live" else f"mode_{mode}"}
        market = meta["market"]
        yes = market["yes_token_id"]
        no = market["no_token_id"]
        yes_is_radiant = bool(market["yes_is_radiant"])
        profile = GAME_PROFILES[game]
        radiant_side = profile.side_0_text
        other_side = profile.side_1_text
        radiant_token = yes if yes_is_radiant else no
        archive = path / GRID_STATE_ARCHIVE_FILENAME
        kills, n_matched, lags = _kills(archive, int(meta["map_number"]), radiant_side, other_side)
        reducer = GridFrameReducer(
            int(meta["map_number"]),
            market["outcome_0_name"],
            market["outcome_1_name"],
            profile,
        )
        events = list(replay_grid_records(iter_grid_archive_records(archive), reducer))
        ev_sec = np.asarray([event.snapshot.second for event in events], dtype=np.int32)
        sg_sec = np.asarray([int(row["second"]) for row in signals], dtype=np.int32) if signals else np.asarray([], dtype=np.int32)
        start = _find_window(ev_sec, sg_sec) if signals else None
        if start is not None:
            pairs = [(start + i, i) for i in range(len(signals))]
            align = "exact_signal_window"
        elif signals and (start := _find_window(sg_sec, ev_sec)) is not None:
            pairs = [(i, start + i) for i in range(len(events))]
            align = "exact_event_window"
        elif signals:
            pairs = _greedy_pairs(ev_sec, sg_sec)
            align = "greedy"
        else:
            pairs = []
            align = "none"
        recv_list: list[float] = []
        yes_mid: list[float] = []
        no_mid: list[float] = []
        for ei, si in pairs:
            sig = signals[si]
            ym = sig.get("yes_mid")
            nm = sig.get("no_mid")
            if ym is None or nm is None:
                continue
            recv_list.append(parse_utc(events[ei].received_at_utc).timestamp())
            yes_mid.append(float(ym))
            no_mid.append(float(nm))
        recv = np.asarray(recv_list, dtype=np.float64)
        yes_a = np.asarray(yes_mid, dtype=np.float64)
        no_a = np.asarray(no_mid, dtype=np.float64)
        order = np.argsort(recv, kind="mergesort") if recv.size else recv
        recv, yes_a, no_a = recv[order], yes_a[order], no_a[order]

        def mid_at(ts: float, token_is_yes: bool, horizon: int) -> float:
            if recv.size == 0:
                return float("nan")
            target = ts + horizon
            j = int(np.searchsorted(recv, target, side="left"))
            if j >= recv.size or recv[j] - target > MARKOUT_SLACK_S:
                return float("nan")
            return float(yes_a[j] if token_is_yes else no_a[j])

        pos = {yes: 0.0, no: 0.0}
        pos_mismatch = 0
        seen_keys: set[str] = set()
        skipped_dup = skipped_venue = skipped_bad = 0
        out_fills = []
        kill_recv = np.asarray([k["sb_recv"] for k in kills], dtype=np.float64)
        fills.sort(key=lambda row: row.get("ts_utc") or "")
        for fill in fills:
            if fill.get("venue") not in (None, "polymarket"):
                skipped_venue += 1
                continue
            if "token_id" not in fill or not fill.get("ts_utc"):
                skipped_bad += 1
                continue
            key = fill.get("fill_key")
            if key:
                if key in seen_keys:
                    skipped_dup += 1
                    continue
                seen_keys.add(key)
            token = fill["token_id"]
            if token not in pos:
                skipped_bad += 1
                continue
            side = fill["side"]
            size = float(fill["size"])
            price = float(fill["price"])
            before = pos[token]
            if side == "BUY":
                pos[token] = before + size
            else:
                pos[token] = before - size
            after = fill.get("position_after")
            if after is not None and abs(pos[token] - float(after)) > 1e-3:
                pos_mismatch += 1
                pos[token] = float(after)
            ts = parse_utc(fill["ts_utc"]).timestamp()
            token_is_yes = token == yes
            seen_i = int(np.searchsorted(kill_recv, ts, side="right")) - 1 if kill_recv.size else -1
            latest = kills[seen_i] if seen_i >= 0 else None
            age = None
            bucket = "none_60"
            table_seen = None
            victim_token = None
            if latest is not None:
                age = ts - latest["sb_recv"]
                victim_token = radiant_token if latest["victim"] == radiant_side else (
                    no if radiant_token == yes else yes
                )
                # dire token is the other one
                if latest["victim"] != radiant_side:
                    victim_token = no if radiant_token == yes else yes
                else:
                    victim_token = radiant_token
                tb = latest["tb_recv"]
                table_seen = tb is not None and tb <= ts
                if age < 3:
                    bucket = "0_3"
                elif age < 11:
                    bucket = "3_11"
                elif age < 30:
                    bucket = "11_30"
                elif age < 60:
                    bucket = "30_60"
                else:
                    bucket = "none_60"
                    victim_token = None
                    table_seen = None
            if bucket == "none_60":
                unseen = None
                for kill in reversed(kills):
                    if kill["sb_recv"] <= ts:
                        break
                    occ = kill["sb_occ"]
                    if occ is not None and occ <= ts and ts - occ < 60:
                        unseen = kill
                        break
                if unseen is not None and (latest is None or ts - latest["sb_recv"] >= 60):
                    bucket = "before_sb"
                    age = ts - unseen["sb_recv"]
                    victim_token = radiant_token if unseen["victim"] == radiant_side else (
                        no if radiant_token == yes else yes
                    )
                    tb = unseen["tb_recv"]
                    table_seen = tb is not None and tb <= ts
                    latest = unseen
            held_victim = False
            buying_victim = False
            on_victim = False
            if victim_token is not None and bucket not in ("none_60",):
                # position_after is the engine size after this fill, so the
                # pre-fill size does not depend on journal order.
                if token == victim_token and after is not None:
                    held_before = float(after) - size if side == "BUY" else float(after) + size
                elif token == victim_token:
                    held_before = before
                else:
                    held_before = pos[victim_token]
                held_victim = held_before > 1e-6
                buying_victim = side == "BUY" and token == victim_token
                on_victim = token == victim_token
            hurt = held_victim or buying_victim
            in_cancel = False
            if kill_recv.size:
                for kill in kills:
                    vt = radiant_token if kill["victim"] == radiant_side else (
                        no if radiant_token == yes else yes
                    )
                    if vt == token and 0.0 <= ts - kill["sb_recv"] < CANCEL_S:
                        in_cancel = True
                        break
            mids = {}
            for horizon in HORIZONS:
                mid = mid_at(ts, token_is_yes, horizon)
                if mid != mid:
                    mids[horizon] = None
                elif side == "BUY":
                    mids[horizon] = mid - price
                else:
                    mids[horizon] = price - mid
            out_fills.append(
                {
                    "side": side,
                    "price": price,
                    "size": size,
                    "notional": price * size,
                    "is_maker": bool(fill.get("is_maker")),
                    "late": fill.get("kind") == "late_fill",
                    "bucket": bucket,
                    "age": age,
                    "table_seen": table_seen,
                    "hurt": hurt if bucket != "none_60" else False,
                    "buying_victim": buying_victim if bucket != "none_60" else False,
                    "holding_victim": held_victim if bucket != "none_60" else False,
                    "on_victim": on_victim if bucket != "none_60" else False,
                    "in_cancel_12": in_cancel,
                    "mk10": mids[10],
                    "mk30": mids[30],
                    "mk60": mids[60],
                    "align_exact": align == "exact_signal_window",
                }
            )
        return {
            "id": path.name,
            "game": game,
            "align": align,
            "n_events": len(events),
            "n_signals": len(signals),
            "n_pairs": len(pairs),
            "n_mids": int(recv.size),
            "n_kills": len(kills),
            "n_kills_table": n_matched,
            "table_lag_s": lags,
            "pos_mismatch": pos_mismatch,
            "skipped_dup": skipped_dup,
            "skipped_venue": skipped_venue,
            "skipped_bad": skipped_bad,
            "fills": out_fills,
        }
    except GridOrientationError as exc:
        return {"id": path.name, "error": f"orientation: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {
            "id": path.name,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc()[-500:],
        }


def _accumulate(fills: list[dict]) -> dict:
    def blank():
        return {
            "n": 0,
            "buy_n": 0,
            "sell_n": 0,
            "buy_notional": 0.0,
            "sell_notional": 0.0,
            "qty": 0.0,
            "hurt_n": 0,
            "buying_n": 0,
            "holding_n": 0,
            "on_victim_n": 0,
            "table_unseen_n": 0,
            "table_known_n": 0,
            "maker_n": 0,
            "mk": {
                h: {"qty": 0.0, "px": 0.0, "usdc": 0.0, "loss": 0.0, "gain": 0.0, "n": 0, "n_loss": 0, "n_gain": 0}
                for h in HORIZONS
            },
        }

    buckets = {name: blank() for name in ("0_3", "3_11", "11_30", "30_60", "none_60", "before_sb")}
    cancel = blank()
    for fill in fills:
        bucket = buckets[fill["bucket"]]
        targets = [bucket]
        if fill["in_cancel_12"]:
            targets.append(cancel)
        for acc in targets:
            acc["n"] += 1
            acc["qty"] += fill["size"]
            acc["maker_n"] += int(fill["is_maker"])
            if fill["side"] == "BUY":
                acc["buy_n"] += 1
                acc["buy_notional"] += fill["notional"]
            else:
                acc["sell_n"] += 1
                acc["sell_notional"] += fill["notional"]
            if fill["hurt"]:
                acc["hurt_n"] += 1
            if fill["buying_victim"]:
                acc["buying_n"] += 1
            if fill["holding_victim"]:
                acc["holding_n"] += 1
            if fill["on_victim"]:
                acc["on_victim_n"] += 1
            if fill["table_seen"] is True:
                acc["table_known_n"] += 1
            elif fill["table_seen"] is False:
                acc["table_unseen_n"] += 1
            for horizon, key in ((10, "mk10"), (30, "mk30"), (60, "mk60")):
                mk = fill[key]
                if mk is None:
                    continue
                cell = acc["mk"][horizon]
                cell["n"] += 1
                cell["qty"] += fill["size"]
                cell["px"] += mk * fill["size"]
                usdc = mk * fill["size"]
                cell["usdc"] += usdc
                if usdc < 0:
                    cell["loss"] += usdc
                    cell["n_loss"] += 1
                else:
                    cell["gain"] += usdc
                    cell["n_gain"] += 1
    return {"buckets": buckets, "cancel_12": cancel}


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    dirs = []
    for path in sorted(TRADER.iterdir()):
        match = path / "match.json"
        if not match.is_file() or not (path / "session.jsonl").is_file():
            continue
        meta = json.loads(match.read_text())
        game = meta.get("game") or "dota"
        if game == "lol":
            dirs.append(path)
        elif game == "dota" and meta.get("feed_source") == "grid":
            dirs.append(path)
    if limit is not None:
        dirs = dirs[:limit]
    print(f"maps_queued {len(dirs)}", flush=True)
    from concurrent.futures import ProcessPoolExecutor
    from multiprocessing import get_context

    rows = []
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=get_context("spawn")) as pool:
        for i, result in enumerate(pool.map(extract_map, [str(p) for p in dirs], chunksize=1)):
            rows.append(result)
            if (i + 1) % 40 == 0 or i + 1 == len(dirs):
                print(f"extracted {i + 1}/{len(dirs)}", flush=True)

    by_game: dict[str, list[dict]] = {"lol": [], "dota": []}
    skips: dict[str, int] = {}
    errors = []
    lag = {"lol": [], "dota": []}
    meta = {"lol": [], "dota": []}
    for result in rows:
        if "skip" in result:
            skips[result["skip"]] = skips.get(result["skip"], 0) + 1
            continue
        if "error" in result:
            errors.append({"id": result.get("id"), "error": result["error"]})
            continue
        game = result["game"]
        by_game[game].extend(result["fills"])
        lag[game].extend(result["table_lag_s"])
        meta[game].append(
            {
                "id": result["id"],
                "align": result["align"],
                "n_kills": result["n_kills"],
                "n_kills_table": result["n_kills_table"],
                "n_fills": len(result["fills"]),
                "n_mids": result["n_mids"],
                "pos_mismatch": result["pos_mismatch"],
                "skipped_dup": result["skipped_dup"],
                "skipped_venue": result["skipped_venue"],
                "skipped_bad": result["skipped_bad"],
            }
        )

    payload = {"queued": len(dirs), "skips": skips, "n_errors": len(errors), "errors_head": errors[:12]}
    for game, fills in by_game.items():
        def picked(pred):
            return _accumulate([fill for fill in fills if pred(fill)])

        stats = _accumulate(fills)
        stats["slices"] = {
            "blind": picked(
                lambda fill: fill["bucket"] in ("0_3", "3_11") and fill["table_seen"] is False
            ),
            "buying_victim": picked(lambda fill: fill["buying_victim"]),
            "exact_align": picked(lambda fill: fill["align_exact"]),
        }
        lags = np.asarray(lag[game], dtype=np.float64)
        maps = meta[game]
        payload[game] = {
            "n_maps_with_fills": len(maps),
            "n_fills": len(fills),
            "n_kills": int(sum(m["n_kills"] for m in maps)),
            "n_kills_with_table": int(sum(m["n_kills_table"] for m in maps)),
            "pos_mismatch": int(sum(m["pos_mismatch"] for m in maps)),
            "skipped_dup": int(sum(m["skipped_dup"] for m in maps)),
            "skipped_venue": int(sum(m["skipped_venue"] for m in maps)),
            "skipped_bad": int(sum(m["skipped_bad"] for m in maps)),
            "align": {name: sum(1 for m in maps if m["align"] == name) for name in ("exact_signal_window", "exact_event_window", "greedy", "none")},
            "table_lag": {
                "n": int(lags.size),
                "p10": float(np.quantile(lags, 0.1)) if lags.size else None,
                "median": float(np.median(lags)) if lags.size else None,
                "p90": float(np.quantile(lags, 0.9)) if lags.size else None,
            },
            **stats,
        }
        block = payload[game]
        print(
            f"{game} maps {block['n_maps_with_fills']} fills {block['n_fills']} "
            f"kills {block['n_kills']} table {block['n_kills_with_table']} "
            f"lag_med {block['table_lag']['median']}",
            flush=True,
        )
        total_buy = sum(block["buckets"][b]["buy_notional"] for b in block["buckets"])
        total_sell = sum(block["buckets"][b]["sell_notional"] for b in block["buckets"])
        for name, acc in block["buckets"].items():
            mk = acc["mk"][30]
            mean = 100 * mk["px"] / mk["qty"] if mk["qty"] else float("nan")
            print(
                f"  {name:10} n {acc['n']:5} buy$ {acc['buy_notional']:.0f} "
                f"({acc['buy_notional'] / total_buy if total_buy else 0:.3f}) "
                f"sell$ {acc['sell_notional']:.0f} "
                f"({acc['sell_notional'] / total_sell if total_sell else 0:.3f}) "
                f"mk30 {mean:.3f}c usdc {mk['usdc']:.2f} loss {mk['loss']:.2f} "
                f"hurt {acc['hurt_n']} table_unseen {acc['table_unseen_n']}",
                flush=True,
            )
        c = block["cancel_12"]
        mk = c["mk"][30]
        print(
            f"  cancel12 n {c['n']} mk30_usdc {mk['usdc']:.2f} loss {mk['loss']:.2f} gain {mk['gain']:.2f}",
            flush=True,
        )
        for slice_name, slice_stats in block["slices"].items():
            acc = slice_stats["cancel_12"] if slice_name == "cancel_unused" else None
            buckets = slice_stats["buckets"]
            n = sum(b["n"] for b in buckets.values())
            loss30 = sum(b["mk"][30]["loss"] for b in buckets.values())
            usdc30 = sum(b["mk"][30]["usdc"] for b in buckets.values())
            print(f"  slice {slice_name} n {n} mk30_usdc {usdc30:.2f} loss {loss30:.2f}", flush=True)
    OUT.write_text(json.dumps(payload, indent=2))
    print(f"wrote {OUT}", flush=True)


if __name__ == "__main__":
    main()
