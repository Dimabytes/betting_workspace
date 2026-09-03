#!/usr/bin/env python3
"""Summarize live_paper match archives on this VPS. Stdlib only."""

from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DOTA_2_MODEL = Path("/root/work/dota_2_model")
HOST_TREES: tuple[tuple[str, Path], ...] = (
    ("live", DOTA_2_MODEL / "data" / "live_paper_live"),
    ("paper", DOTA_2_MODEL / "data" / "live_paper_paper"),
    ("legacy", DOTA_2_MODEL / "data" / "live_paper"),
)
LIVE_WALLET_CANDIDATES = (
    DOTA_2_MODEL / "data" / "live_paper_live" / "wallet" / "live.db",
    DOTA_2_MODEL / "data" / "live_paper" / "wallet" / "live.db",
)
BERLIN = ZoneInfo("Europe/Berlin")
FEE_RATE = 0.05
REBATE_RATE = 0.15
POLYMARKET_DATA_API = "https://data-api.polymarket.com"


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


def game_from_meta(meta: dict) -> str:
    """Return match.json game; missing or empty is dota."""
    game = meta.get("game")
    if not game:
        return "dota"
    return str(game)


def live_wallet_db() -> Path | None:
    """First existing live.db among live then legacy. paper.db is not the funder."""
    for path in LIVE_WALLET_CANDIDATES:
        if path.is_file():
            return path
    return None


def match_dirs() -> list[tuple[str, Path]]:
    """Host trees in order, skip missing/empty. Each row is (tree, archive)."""
    found: list[tuple[str, Path]] = []
    for tree, root in HOST_TREES:
        if not root.is_dir():
            continue
        children = [
            child
            for child in root.iterdir()
            if child.is_dir() and child.name != "wallet" and (child / "match.json").is_file()
        ]
        if not children:
            continue
        found.extend((tree, child) for child in children)
    return sorted(found, key=lambda item: (item[1] / "match.json").stat().st_mtime)


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
        try:
            net = float(realized) + float(imv) + rebate
        except (TypeError, ValueError):
            net = None
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


def is_open_session(tree: str, archive: Path, sess: dict, meta: dict) -> bool:
    """True when this archive might still be a running worker, not leftover tape.

    `--live` used to mean "no session_end". Crash/exhaust leaves that gap forever,
    and the pre-rollout `legacy` tree is full of them. Skip those so --live is
    the current maps, not August leftovers.
    """
    if not sess["live"]:
        return False
    if tree == "legacy":
        return False
    if meta.get("final") is not None:
        return False
    if (archive / "execution_cleanup.json").is_file():
        return False
    return True


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{number:+.4f}"


def print_match_row(archive: Path, sess: dict, meta: dict, tree: str) -> None:
    teams = meta.get("teams") or {}
    radiant = teams.get("radiant", "?")
    dire = teams.get("dire", "?")
    map_no = meta.get("map_number", "?")
    joined = meta.get("joined_at_utc", "")
    final = meta.get("final")
    winner = (final or {}).get("winner")
    mode = (sess["start"] or {}).get("execution_mode", "?") if sess["start"] else "?"
    game = game_from_meta(meta)
    if winner:
        status = winner
    elif final is not None:
        status = "done"
    elif sess["live"]:
        status = "LIVE"
    else:
        status = "done"
    cleanup = "cleanup" if (archive / "execution_cleanup.json").is_file() else "no-cleanup"
    print(
        f"{archive.name}  [{tree}]  game={game}  {radiant} vs {dire}  map {map_no}  {status}  "
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


def cmd_list(today: bool, live_only: bool, game: str | None) -> None:
    day = datetime.now(BERLIN).date()
    printed = 0
    day_net = 0.0
    day_rebate = 0.0
    day_count = 0
    live_count = 0
    for tree, archive in match_dirs():
        meta = load_json(archive / "match.json") or {}
        if game is not None and game_from_meta(meta) != game:
            continue
        if today and not in_berlin_day(meta.get("joined_at_utc"), day):
            continue
        sess = summarize_session(archive)
        if live_only and not is_open_session(tree, archive, sess, meta):
            continue
        print_match_row(archive, sess, meta, tree)
        printed += 1
        winner = (meta.get("final") or {}).get("winner")
        if sess["live"] and not winner:
            live_count += 1
        elif sess["net"] is not None:
            day_net += sess["net"]
            day_rebate += sess["rebate"]
            day_count += 1
    if printed == 0:
        print("no matches")
    elif today or live_only:
        print()
        print(
            f"berlin_day={day}  finished={day_count}  live={live_count}  "
            f"sum_net={fmt_money(day_net if day_count else None)}  "
            f"sum_rebate={fmt_money(day_rebate if day_count else None)}"
        )
        print("telegram sum_net is maps with session_end only. not the day.")
    if today:
        print_polymarket_today(day)


def cmd_one(match_id: str) -> None:
    hits = [(tree, archive) for tree, archive in match_dirs() if archive.name == match_id]
    if not hits:
        raise SystemExit(f"no match.json for {match_id} in live/paper/legacy trees")
    for tree, archive in hits:
        _print_one(match_id, tree, archive)


def _print_one(match_id: str, tree: str, archive: Path) -> None:
    meta = load_json(archive / "match.json")
    if meta is None:
        raise SystemExit(f"no match.json at {archive}")
    sess = summarize_session(archive)
    teams = meta.get("teams") or {}
    market = meta.get("market") or {}
    model = meta.get("model") or {}
    final = meta.get("final") or {}
    print(f"match {match_id}  [{tree}]  game={game_from_meta(meta)}")
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
        token = str(fill.get("token_id") or "")
        if yes_id and token == yes_id:
            leg = "yes"
        elif no_id and token == no_id:
            leg = "no"
        else:
            leg = "?"
        print(
            f"  FILL {fill.get('ts_utc')} t={fill.get('second')} {fill.get('side')} "
            f"{leg} {fill.get('size')} @ {fill.get('price')} maker={fill.get('is_maker')} "
            f"pos={fill.get('position_after')} cash={fill.get('net_cash')}"
        )
    if not sess["fills"]:
        print("  no fills")


def cmd_wallet() -> None:
    wallet = live_wallet_db()
    if wallet is None:
        print("no live.db in live_paper_live or live_paper")
        return
    conn = sqlite3.connect(f"file:{wallet}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cash = conn.execute(
        "SELECT COALESCE(SUM(cash_delta), 0) AS s FROM fill_ledger WHERE status IN ('MATCHED', 'CONFIRMED')"
    ).fetchone()["s"]
    positions = list(conn.execute("SELECT token_id, size, avg_price FROM positions WHERE size != 0"))
    n_fills = conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()["n"]
    print(f"wallet {wallet}")
    print(f"  ledger_net_cash={cash:+.4f}  fill_rows={n_fills}  nonzero_positions={len(positions)}")
    print("  this is inventory cash, not day PnL")
    for row in positions[:20]:
        print(f"  pos {row['token_id'][-8:]} size={row['size']} avg={row['avg_price']}")
    conn.close()


def read_funder() -> str | None:
    """Return the pinned Safe / funder from live.db, or None."""
    wallet = live_wallet_db()
    if wallet is None:
        return None
    conn = sqlite3.connect(f"file:{wallet}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT v FROM wallet_identity WHERE k='funder' AND v IS NOT NULL AND v != ''"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    funder = row[0]
    if not isinstance(funder, str) or not funder:
        return None
    return funder


def unix_in_berlin_day(stamp: object, day) -> bool:
    """True when a unix timestamp falls on this Europe/Berlin calendar day."""
    try:
        unix = float(stamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    parsed = datetime.fromtimestamp(unix, timezone.utc)
    return parsed.astimezone(BERLIN).date() == day


def fold_polymarket_day(activity: list, positions: list, day) -> dict:
    """Fold data-api activity + open marks into Berlin-day cash and pnl."""
    buy = 0.0
    sell = 0.0
    redeem = 0.0
    rebate = 0.0
    n_buy = 0
    n_sell = 0
    n_redeem = 0
    n_rebate = 0
    for row in activity:
        if not unix_in_berlin_day(row.get("timestamp"), day):
            continue
        kind = str(row.get("type") or "")
        side = str(row.get("side") or "").upper()
        try:
            usdc = float(row.get("usdcSize") or 0.0)
        except (TypeError, ValueError):
            usdc = 0.0
        if kind == "TRADE" and side == "BUY":
            buy += usdc
            n_buy += 1
        elif kind == "TRADE" and side == "SELL":
            sell += usdc
            n_sell += 1
        elif kind == "REDEEM":
            redeem += usdc
            n_redeem += 1
        elif kind == "MAKER_REBATE":
            rebate += usdc
            n_rebate += 1
    open_mark = 0.0
    n_open = 0
    for pos in positions:
        try:
            size = float(pos.get("size") or 0.0)
            price = float(pos.get("curPrice") or 0.0)
        except (TypeError, ValueError):
            continue
        if size == 0.0:
            continue
        open_mark += size * price
        n_open += 1
    cash = -buy + sell + redeem + rebate
    return {
        "buy": buy,
        "sell": sell,
        "redeem": redeem,
        "rebate": rebate,
        "cash": cash,
        "open": open_mark,
        "pnl": cash + open_mark,
        "n_buy": n_buy,
        "n_sell": n_sell,
        "n_redeem": n_redeem,
        "n_rebate": n_rebate,
        "n_open": n_open,
    }


def fetch_json(url: str) -> object:
    """GET JSON from Polymarket data-api."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode())


def fetch_activity(funder: str) -> list:
    """Page data-api activity for this funder."""
    rows: list = []
    offset = 0
    while offset <= 2000:
        chunk = fetch_json(
            f"{POLYMARKET_DATA_API}/activity?user={funder}&limit=100&offset={offset}"
        )
        if not isinstance(chunk, list) or not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < 100:
            break
        offset += 100
    return rows


def fetch_positions(funder: str) -> list:
    """Open positions for this funder."""
    payload = fetch_json(f"{POLYMARKET_DATA_API}/positions?user={funder}")
    if isinstance(payload, list):
        return payload
    return []


def print_polymarket_today(day) -> None:
    """Print settled Berlin-day PnL from Polymarket activity + open marks."""
    funder = read_funder()
    if funder is None:
        print("polymarket_today n/a  no funder in live.db")
        return
    try:
        folded = fold_polymarket_day(fetch_activity(funder), fetch_positions(funder), day)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        print(f"polymarket_today n/a  {type(exc).__name__}")
        return
    print(
        f"polymarket_today  buy={folded['buy']:.2f} sell={folded['sell']:.2f} "
        f"redeem={folded['redeem']:.2f} rebate={folded['rebate']:.2f} "
        f"cash={folded['cash']:+.2f} open={folded['open']:+.2f} "
        f"pnl={folded['pnl']:+.2f}  "
        f"n_buy={folded['n_buy']} n_sell={folded['n_sell']} "
        f"n_redeem={folded['n_redeem']} n_rebate={folded['n_rebate']} n_open={folded['n_open']}"
    )
    print("day number is polymarket_today pnl (cash+open). leftover BUY is not a loss if REDEEM paid.")


def check_game_default() -> None:
    """Fail if missing game is not dota."""
    if game_from_meta({}) != "dota":
        raise SystemExit("missing game must default to dota")
    if game_from_meta({"game": "lol"}) != "lol":
        raise SystemExit("lol game must stay lol")
    if HOST_TREES[0][0] != "live" or HOST_TREES[1][0] != "paper" or HOST_TREES[2][0] != "legacy":
        raise SystemExit("host tree order must be live, paper, legacy")
    dummy_sess = {"live": True}
    dummy_meta: dict = {}
    dummy_archive = Path("/nonexistent")
    if is_open_session("legacy", dummy_archive, dummy_sess, dummy_meta):
        raise SystemExit("legacy tree must not count as an open session")
    if not is_open_session("live", dummy_archive, dummy_sess, dummy_meta):
        raise SystemExit("live tree without end/final/cleanup must count as open")
    if is_open_session("live", dummy_archive, dummy_sess, {"final": {"winner": None}}):
        raise SystemExit("GRID final with null winner must not count as open")


def check_fold() -> None:
    """Fail if redeem is omitted from the day number."""
    day = datetime(2026, 8, 29, tzinfo=BERLIN).date()
    noon = datetime(2026, 8, 29, 12, 0, tzinfo=BERLIN)
    ts = noon.timestamp()
    rows = [
        {"type": "TRADE", "side": "BUY", "usdcSize": 50.0, "timestamp": ts},
        {"type": "REDEEM", "usdcSize": 52.08, "timestamp": ts},
        {"type": "MAKER_REBATE", "usdcSize": 0.2, "timestamp": ts},
    ]
    folded = fold_polymarket_day(rows, [{"size": 4.0, "curPrice": 0.3}], day)
    cash = round(folded["cash"], 2)
    pnl = round(folded["pnl"], 2)
    if cash != 2.28 or pnl != 3.48:
        raise SystemExit(f"fold check failed cash={cash} pnl={pnl}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", action="store_true", help="Berlin calendar day")
    parser.add_argument(
        "--live",
        action="store_true",
        help="open live/paper sessions (no session_end, no final, no cleanup; not legacy)",
    )
    parser.add_argument("--game", choices=("dota", "lol"), help="filter match.json game")
    parser.add_argument("--match", help="one Steam match id")
    parser.add_argument("--wallet", action="store_true", help="sqlite inventory snapshot")
    parser.add_argument("--self-check", action="store_true", help="assert redeem is in the day fold")
    args = parser.parse_args()
    if args.self_check:
        check_fold()
        check_game_default()
        print("fold ok")
        return
    if args.match:
        cmd_one(args.match)
        return
    if args.wallet:
        cmd_wallet()
        return
    cmd_list(today=args.today, live_only=args.live, game=args.game)


if __name__ == "__main__":
    main()
