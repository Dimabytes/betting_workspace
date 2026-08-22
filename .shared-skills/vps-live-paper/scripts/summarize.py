#!/usr/bin/env python3
"""Summarize live_paper match archives on this VPS. Stdlib only."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

LIVE_PAPER = Path("/root/work/dota_2_model/data/live_paper")
WALLET_DB = LIVE_PAPER / "wallet" / "live.db"
BERLIN = ZoneInfo("Europe/Berlin")
FEE_RATE = 0.05
REBATE_RATE = 0.15


def maker_rebate(price: float, size: float, is_maker: bool) -> float:
    if not is_maker:
        return 0.0
    if not (0.0 < price < 1.0) or size <= 0.0:
        return 0.0
    return REBATE_RATE * FEE_RATE * size * price * (1.0 - price)


def parse_utc(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    text = stamp.replace("Z", "+00:00")
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def match_dirs() -> list[Path]:
    if not LIVE_PAPER.is_dir():
        return []
    dirs = [
        child
        for child in LIVE_PAPER.iterdir()
        if child.is_dir() and child.name != "wallet" and (child / "match.json").is_file()
    ]
    return sorted(dirs, key=lambda p: (p / "match.json").stat().st_mtime)


def summarize_session(archive: Path) -> dict:
    fills: list[dict] = []
    reasons: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    quotes = 0
    errors: list[str] = []
    start: dict | None = None
    end: dict | None = None
    last_signal: dict | None = None
    last_quote: dict | None = None
    last_model: dict | None = None
    rebate = 0.0
    for rec in iter_jsonl(archive / "session.jsonl"):
        kind = rec.get("kind")
        if kind == "session_start":
            start = rec
        elif kind == "session_end":
            end = rec
        elif kind == "signal":
            reasons[str(rec.get("reason", "?"))] += 1
            block = rec.get("entry_block")
            if block and block != "none":
                blocks[str(block)] += 1
            last_signal = rec
            if rec.get("reason") == "model":
                last_model = rec
        elif kind == "quote":
            quotes += 1
            last_quote = rec
        elif kind == "fill":
            fills.append(rec)
            rebate += maker_rebate(
                float(rec.get("price") or 0.0),
                float(rec.get("size") or 0.0),
                bool(rec.get("is_maker")),
            )
        elif kind == "trading_error":
            errors.append(f"{rec.get('phase')}:{rec.get('error_type')}")
    realized = None
    imv = None
    leftover_sum = 0.0
    if end is not None:
        realized = end.get("net_cash")
        imv = end.get("inventory_value")
        leftover_sum = sum(float(v) for v in (end.get("positions") or {}).values())
    last_fill = fills[-1] if fills else None
    net = None
    if realized is not None and imv is not None:
        net = float(realized) + float(imv) + rebate
    return {
        "start": start,
        "end": end,
        "fills": fills,
        "fill_count": len(fills),
        "quotes": quotes,
        "reasons": reasons,
        "blocks": blocks,
        "errors": errors,
        "last_signal": last_signal,
        "last_quote": last_quote,
        "last_model": last_model,
        "last_fill": last_fill,
        "rebate": rebate,
        "realized": realized,
        "imv": imv,
        "net": net,
        "leftover": leftover_sum,
        "live": end is None,
    }


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.4f}"


def print_match_row(archive: Path, sess: dict, meta: dict) -> None:
    teams = meta.get("teams") or {}
    radiant = teams.get("radiant", "?")
    dire = teams.get("dire", "?")
    map_no = meta.get("map_number", "?")
    joined = meta.get("joined_at_utc", "")
    winner = (meta.get("final") or {}).get("winner")
    mode = (sess["start"] or {}).get("execution_mode", "?") if sess["start"] else "?"
    status = "LIVE" if sess["live"] else (winner or "done")
    cleanup = "cleanup" if (archive / "execution_cleanup.json").is_file() else "no-cleanup"
    print(
        f"{archive.name}  {radiant} vs {dire}  map {map_no}  {status}  "
        f"mode={mode}  fills={sess['fill_count']}  "
        f"realized={fmt_money(sess['realized'])} imv={fmt_money(sess['imv'])} "
        f"rebate={fmt_money(sess['rebate'])} net={fmt_money(sess['net'])}  "
        f"{cleanup}  joined={joined}"
    )


def in_berlin_day(stamp: str | None, day) -> bool:
    parsed = parse_utc(stamp)
    if parsed is None:
        return False
    return parsed.astimezone(BERLIN).date() == day


def cmd_list(today: bool, live_only: bool) -> None:
    day = datetime.now(BERLIN).date()
    printed = 0
    day_net = 0.0
    day_rebate = 0.0
    day_count = 0
    live_count = 0
    for archive in match_dirs():
        meta = load_json(archive / "match.json") or {}
        if today and not in_berlin_day(meta.get("joined_at_utc"), day):
            continue
        sess = summarize_session(archive)
        if live_only and not sess["live"]:
            continue
        print_match_row(archive, sess, meta)
        printed += 1
        if sess["live"]:
            live_count += 1
        elif sess["net"] is not None:
            day_net += sess["net"]
            day_rebate += sess["rebate"]
            day_count += 1
    if printed == 0:
        print("no matches")
        return
    if today or live_only:
        print()
        print(
            f"berlin_day={day}  finished={day_count}  live={live_count}  "
            f"sum_net={fmt_money(day_net if day_count else None)}  "
            f"sum_rebate={fmt_money(day_rebate if day_count else None)}"
        )
        print("net = realized + imv + rebate. sqlite cash is not this number.")


def cmd_one(match_id: str) -> None:
    archive = LIVE_PAPER / match_id
    meta = load_json(archive / "match.json")
    if meta is None:
        raise SystemExit(f"no match.json at {archive}")
    sess = summarize_session(archive)
    teams = meta.get("teams") or {}
    market = meta.get("market") or {}
    model = meta.get("model") or {}
    final = meta.get("final") or {}
    print(f"match {match_id}")
    print(f"  {teams.get('radiant')} (radiant) vs {teams.get('dire')} (dire)  map {meta.get('map_number')}")
    print(f"  joined {meta.get('joined_at_utc')}  horn {meta.get('horn_at_utc')}")
    print(f"  market {market.get('market_slug')}  yes_is_radiant={market.get('yes_is_radiant')}")
    print(f"  model {model.get('name')}  mode={(sess['start'] or {}).get('execution_mode')}")
    print(f"  winner={final.get('winner')}  pause_s={final.get('pause_seconds')}  missing_s={final.get('missing_seconds')}")
    print(f"  match.json pnl={final.get('pnl')}  (ignore if flattened to zero)")
    print(
        f"  session  fills={sess['fill_count']} quotes={sess['quotes']}  "
        f"realized={fmt_money(sess['realized'])} imv={fmt_money(sess['imv'])} "
        f"rebate={fmt_money(sess['rebate'])} net={fmt_money(sess['net'])}  "
        f"leftover_sum={sess['leftover']:.4f}  live={sess['live']}"
    )
    yes_id = market.get("yes_token_id")
    no_id = market.get("no_token_id")
    end_pos = (sess["end"] or {}).get("positions") or {}
    if yes_id or no_id:
        print(
            f"  leftover yes={float(end_pos.get(yes_id, 0.0) if yes_id else 0.0):.4f} "
            f"no={float(end_pos.get(no_id, 0.0) if no_id else 0.0):.4f}"
        )
    if sess["reasons"]:
        print("  signal.reason " + " ".join(f"{k}={v}" for k, v in sess["reasons"].most_common()))
    if sess["blocks"]:
        print("  entry_block " + " ".join(f"{k}={v}" for k, v in sess["blocks"].most_common()))
    if sess["errors"]:
        print("  trading_error " + ", ".join(sess["errors"]))
    last = sess["last_signal"]
    if last:
        print(
            f"  last signal second={last.get('second')} reason={last.get('reason')} "
            f"entry_block={last.get('entry_block')} yes_mid={last.get('yes_mid')} "
            f"yes_fair={last.get('yes_fair')}"
        )
    quote = sess["last_quote"]
    if quote:
        placed = quote.get("placed") or []
        print(
            f"  last quote second={quote.get('second')} decision={quote.get('decision')} "
            f"fv={quote.get('fv_source')} placed={len(placed)}"
        )
    for fill in sess["fills"]:
        print(
            f"  FILL {fill.get('ts_utc')} t={fill.get('second')} {fill.get('side')} "
            f"{fill.get('size')} @ {fill.get('price')} maker={fill.get('is_maker')} "
            f"pos={fill.get('position_after')} cash={fill.get('net_cash')}"
        )
    if not sess["fills"]:
        print("  no fills")


def cmd_wallet() -> None:
    if not WALLET_DB.is_file():
        print(f"no {WALLET_DB}")
        return
    conn = sqlite3.connect(f"file:{WALLET_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cash = conn.execute(
        "SELECT COALESCE(SUM(cash_delta), 0) AS s FROM fill_ledger WHERE status IN ('MATCHED', 'CONFIRMED')"
    ).fetchone()["s"]
    positions = list(conn.execute("SELECT token_id, size, avg_price FROM positions WHERE size != 0"))
    n_fills = conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()["n"]
    print(f"wallet {WALLET_DB}")
    print(f"  ledger_net_cash={cash:+.4f}  fill_rows={n_fills}  nonzero_positions={len(positions)}")
    print("  this is inventory cash, not day PnL")
    for row in positions[:20]:
        print(f"  pos {row['token_id'][-8:]} size={row['size']} avg={row['avg_price']}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", action="store_true", help="Berlin calendar day")
    parser.add_argument("--live", action="store_true", help="sessions without session_end")
    parser.add_argument("--match", help="one Steam match id")
    parser.add_argument("--wallet", action="store_true", help="sqlite inventory snapshot")
    args = parser.parse_args()
    if args.match:
        cmd_one(args.match)
        return
    if args.wallet:
        cmd_wallet()
        return
    cmd_list(today=args.today, live_only=args.live)


if __name__ == "__main__":
    main()
