#!/usr/bin/env python3
"""Summarize trader match archives on this VPS. Stdlib only."""

import argparse
import gzip
import json
import math
import os
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TextIO, cast
from zoneinfo import ZoneInfo

UTC = timezone.utc  # noqa: UP017 — datetime.UTC needs 3.11+; this file also runs standalone

ESPORTS_TRADER = Path(__file__).resolve().parents[2]
HOST_TREES: tuple[tuple[str, Path], ...] = (
    ("live", ESPORTS_TRADER / "data" / "trader_live"),
    ("paper", ESPORTS_TRADER / "data" / "trader_paper"),
    ("legacy", ESPORTS_TRADER / "data" / "live_paper"),
)
LIVE_WALLET_CANDIDATES = (
    ESPORTS_TRADER / "data" / "trader_live" / "wallet" / "live.db",
    ESPORTS_TRADER / "data" / "live_paper" / "wallet" / "live.db",
)
BERLIN = ZoneInfo("Europe/Berlin")
FEE_RATE = 0.05
REBATE_RATE = 0.15
POLYMARKET_DATA_API = "https://data-api.polymarket.com"
STALE_OPEN_SECONDS = 900.0
# Mirrors BUY_CUTOFF_SECOND / MIN_ORDER_SIZE in src/shared/constants/strategy.py — stdlib-only file, values copied not imported.
RESTART_CUTOFF_SECOND = 480.0
RESTART_POSITION_SHARES = 5.0
PAGE_LIMIT = 500
ACTIVITY_MAX_OFFSET = 5000
POSITIONS_MAX_OFFSET = 10000
REQUEST_TIMEOUT_S = 10.0
DAY_DEADLINE_S = 60.0
ACTIVITY_START = 1

FetchJson = Callable[[str, float], object]
WallClock = Callable[[], float]

_FOLD_KINDS = frozenset({"TRADE", "REDEEM", "MAKER_REBATE"})


@dataclass(frozen=True)
class ActivityEntry:
    timestamp: float
    kind: str
    side: str
    usdc_size: float | None


@dataclass(frozen=True)
class PositionEntry:
    asset: str
    condition_id: str | None
    title: str | None
    size: float
    cur_price: float
    redeemable: bool | None


@dataclass(frozen=True)
class ActivityResult:
    entries: tuple[ActivityEntry, ...]
    end: float
    day_start: float
    day_end: float
    started_at: float
    completed_at: float
    pages: int
    day_complete: bool
    payout_search_complete: bool
    newest_payout: float | None
    stop_reason: str
    error: str | None


@dataclass(frozen=True)
class PositionsResult:
    positions: tuple[PositionEntry, ...]
    as_of: float
    started_at: float
    completed_at: float
    pages: int
    traversal_complete: bool
    stop_reason: str
    error: str | None


@dataclass(frozen=True)
class DayFold:
    buy: float
    sell: float
    redeem: float
    rebate: float
    cash: float
    open_mark: float
    pnl: float
    n_buy: int
    n_sell: int
    n_redeem: int
    n_rebate: int
    n_open: int


@dataclass(frozen=True)
class DayResult:
    day: str
    fold: DayFold | None
    fetched_at: float | None
    complete: bool
    error: str | None
    activity: ActivityResult | None
    positions: PositionsResult | None


@dataclass(frozen=True)
class SessionSummary:
    start: dict[str, object] | None
    end: dict[str, object] | None
    fills: tuple[dict[str, object], ...]
    fill_count: int
    quotes: int
    reasons: Mapping[str, int]
    blocks: Mapping[str, int]
    errors: tuple[str, ...]
    last_signal: dict[str, object] | None
    last_quote: dict[str, object] | None
    last_model: dict[str, object] | None
    last_fill: dict[str, object] | None
    rebate: float
    realized: float | None
    imv: float | None
    net: float | None
    late_fills: int
    positions: Mapping[str, float]
    leftover: float
    live: bool


@dataclass(frozen=True)
class RebateAccrual:
    total: float
    per_game: dict[str, float]
    fills: int
    matches: int


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
        value = value.replace(tzinfo=UTC)
    return value


def load_json(path: Path) -> dict[str, object] | None:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, object], document) if isinstance(document, dict) else None


def _open_records(path: Path) -> TextIO | None:
    if path.is_file():
        return path.open("r", encoding="utf-8", newline="\n")
    gz = path.with_name(path.name + ".gz")
    if gz.is_file():
        return gzip.open(gz, "rt", encoding="utf-8", newline="\n")
    return None


def iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    handle = _open_records(path)
    if handle is None:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield cast(dict[str, object], record)


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    return None


def _record_money(record: dict[str, object], key: str) -> float:
    value = _num(record.get(key))
    return value if value is not None else 0.0


def game_from_meta(meta: dict[str, object]) -> str:
    game = meta.get("game")
    if not game or not isinstance(game, str):
        return "dota"
    return game


def live_wallet_db() -> Path | None:
    for path in LIVE_WALLET_CANDIDATES:
        if path.is_file():
            return path
    return None


def match_dirs(
    trees: tuple[tuple[str, Path], ...] = HOST_TREES,
) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    for tree, root in trees:
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


@dataclass
class _SessionFold:
    fills: list[dict[str, object]] = field(default_factory=list)
    late: list[dict[str, object]] = field(default_factory=list)
    reasons: Counter[str] = field(default_factory=Counter)
    blocks: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    start: dict[str, object] | None = None
    end: dict[str, object] | None = None
    last_signal: dict[str, object] | None = None
    last_quote: dict[str, object] | None = None
    last_model: dict[str, object] | None = None
    quotes: int = 0
    rebate: float = 0.0

    def add(self, rec: dict[str, object]) -> None:
        kind = rec.get("kind")
        if kind == "session_start":
            self.start = rec
        elif kind == "session_end":
            self.end = rec
        elif kind == "signal":
            self.reasons[str(rec.get("reason", "?"))] += 1
            block = rec.get("entry_block")
            if block and block != "none":
                self.blocks[str(block)] += 1
            self.last_signal = rec
            if rec.get("reason") == "model":
                self.last_model = rec
        elif kind == "quote":
            self.quotes += 1
            self.last_quote = rec
        elif kind in {"fill", "late_fill"}:
            self.fills.append(rec)
            if kind == "late_fill":
                self.late.append(rec)
            self.rebate += maker_rebate(
                _record_money(rec, "price"),
                _record_money(rec, "size"),
                bool(rec.get("is_maker")),
            )
        elif kind == "trading_error":
            self.errors.append(f"{rec.get('phase')}:{rec.get('error_type')}")


def _end_positions(end: dict[str, object]) -> dict[str, float]:
    positions: dict[str, float] = {}
    raw_positions = end.get("positions")
    if isinstance(raw_positions, dict):
        for token, raw_size in cast(dict[str, object], raw_positions).items():
            size = _num(raw_size)
            if size is not None:
                positions[str(token)] = size
    return positions


def summarize_session(archive: Path) -> SessionSummary:
    fold = _SessionFold()
    for rec in iter_jsonl(archive / "session.jsonl"):
        fold.add(rec)
    fills = fold.fills
    late = fold.late
    realized: float | None = None
    imv: float | None = None
    positions: dict[str, float] = {}
    if fold.end is not None:
        realized = _num(fold.end.get("net_cash"))
        imv = _num(fold.end.get("inventory_value"))
        positions = _end_positions(fold.end)
    elif fills:
        realized = _num(fills[-1].get("net_cash"))
    if late:
        last_late_cash = _num(late[-1].get("net_cash"))
        if last_late_cash is not None:
            realized = last_late_cash
        before = dict(positions)
        for rec in late:
            token = rec.get("token_id")
            after = _num(rec.get("position_after"))
            if token is not None and after is not None:
                positions[str(token)] = after
        if positions != before:
            imv = 0.0 if all(abs(v) < 1e-9 for v in positions.values()) else None
    leftover_sum = sum(positions.values())
    last_fill = fills[-1] if fills else None
    net = None
    if realized is not None and imv is not None:
        net = realized + imv + fold.rebate
    return SessionSummary(
        start=fold.start,
        end=fold.end,
        fills=tuple(fills),
        fill_count=len(fills),
        quotes=fold.quotes,
        reasons=MappingProxyType(dict(fold.reasons)),
        blocks=MappingProxyType(dict(fold.blocks)),
        errors=tuple(fold.errors),
        last_signal=fold.last_signal,
        last_quote=fold.last_quote,
        last_model=fold.last_model,
        last_fill=last_fill,
        rebate=fold.rebate,
        realized=realized,
        imv=imv,
        net=net,
        late_fills=len(late),
        positions=MappingProxyType(positions),
        leftover=leftover_sum,
        live=fold.end is None,
    )


def archive_last_write(archive: Path) -> float | None:
    stamps = [entry.stat().st_mtime for entry in archive.glob("*") if entry.is_file()]
    if not stamps:
        return None
    return max(stamps)


def is_stale_archive(archive: Path, now: float | None = None) -> bool:
    last_write = archive_last_write(archive)
    if last_write is None:
        return False
    if now is None:
        now = datetime.now(UTC).timestamp()
    return now - last_write > STALE_OPEN_SECONDS


def is_open_session(
    tree: str,
    archive: Path,
    sess: SessionSummary,
    meta: dict[str, object],
    now: float | None = None,
) -> bool:
    if not sess.live:
        return False
    if sess.start is None:
        return False
    if tree == "legacy":
        return False
    if meta.get("final") is not None:
        return False
    if (archive / "execution_cleanup.json").is_file():
        return False
    return not is_stale_archive(archive, now)


def restart_block(second: float, pos_yes: float, pos_no: float) -> str | None:
    if pos_yes >= RESTART_POSITION_SHARES or pos_no >= RESTART_POSITION_SHARES:
        return "position"
    if second < RESTART_CUTOFF_SECOND:
        return "in_window"
    return None


def cmd_restart_check() -> None:
    blocked: list[str] = []
    for tree, archive in match_dirs():
        if tree != "live":
            continue
        meta = load_json(archive / "match.json") or {}
        sess = summarize_session(archive)
        if not is_open_session(tree, archive, sess, meta):
            continue
        last = sess.last_signal
        if last is None:
            blocked.append(f"no_signal {archive.name}")
            continue
        second = _record_money(last, "second")
        pos_yes = _record_money(last, "pos_yes")
        pos_no = _record_money(last, "pos_no")
        reason = restart_block(second, pos_yes, pos_no)
        if reason is not None:
            blocked.append(
                f"{reason} {archive.name} second={second:g} pos_yes={pos_yes:g} pos_no={pos_no:g}"
            )
    if not blocked:
        print("restart_check SAFE")
        return
    for line in blocked:
        print(f"restart_check UNSAFE {line}")
    raise SystemExit(1)


def fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(number):
        return "n/a"
    return f"{number:+.4f}"


def print_match_row(
    archive: Path, sess: SessionSummary, meta: dict[str, object], tree: str
) -> None:
    teams_raw = meta.get("teams")
    teams = cast(dict[str, object], teams_raw) if isinstance(teams_raw, dict) else {}
    radiant = teams.get("radiant", "?")
    dire = teams.get("dire", "?")
    map_no = meta.get("map_number", "?")
    joined = meta.get("joined_at_utc", "")
    final_raw = meta.get("final")
    final = cast(dict[str, object], final_raw) if isinstance(final_raw, dict) else None
    winner = final.get("winner") if final is not None else None
    mode = sess.start.get("execution_mode", "?") if sess.start else "?"
    game = game_from_meta(meta)
    if winner:
        status = str(winner)
    elif final is not None:
        status = "done"
    elif sess.live and is_stale_archive(archive):
        status = "ORPHAN"
    elif sess.live:
        status = "LIVE"
    else:
        status = "done"
    cleanup = "cleanup" if (archive / "execution_cleanup.json").is_file() else "no-cleanup"
    print(
        f"{archive.name}  [{tree}]  game={game}  {radiant} vs {dire}  map {map_no}  {status}  "
        f"mode={mode}  fills={sess.fill_count}  "
        f"realized={fmt_money(sess.realized)} imv={fmt_money(sess.imv)} "
        f"rebate={fmt_money(sess.rebate)} net={fmt_money(sess.net)}  "
        f"{cleanup}  joined={joined}"
    )


def in_berlin_day(stamp: str | None, day: date) -> bool:
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
        joined = meta.get("joined_at_utc")
        if today and not in_berlin_day(joined if isinstance(joined, str) else None, day):
            continue
        sess = summarize_session(archive)
        if live_only and not is_open_session(tree, archive, sess, meta):
            continue
        print_match_row(archive, sess, meta, tree)
        printed += 1
        final_raw = meta.get("final")
        winner = (
            cast(dict[str, object], final_raw).get("winner")
            if isinstance(final_raw, dict)
            else None
        )
        if sess.live and not winner and not is_stale_archive(archive):
            live_count += 1
        elif sess.net is not None:
            day_net += sess.net
            day_rebate += sess.rebate
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


@dataclass(frozen=True)
class _MetaBlocks:
    teams: dict[str, object]
    market: dict[str, object]
    model: dict[str, object]
    final: dict[str, object]


def _meta_blocks(meta: dict[str, object]) -> _MetaBlocks:
    def block(key: str) -> dict[str, object]:
        value = meta.get(key)
        return cast(dict[str, object], value) if isinstance(value, dict) else {}

    return _MetaBlocks(
        teams=block("teams"), market=block("market"), model=block("model"), final=block("final")
    )


def _print_meta(
    match_id: str, tree: str, meta: dict[str, object], blocks: _MetaBlocks, sess: SessionSummary
) -> None:
    print(f"match {match_id}  [{tree}]  game={game_from_meta(meta)}")
    print(
        f"  {blocks.teams.get('radiant')} (radiant) vs {blocks.teams.get('dire')} (dire)  "
        f"map {meta.get('map_number')}"
    )
    print(f"  joined {meta.get('joined_at_utc')}  horn {meta.get('horn_at_utc')}")
    print(
        f"  market {blocks.market.get('market_slug')}  "
        f"yes_is_radiant={blocks.market.get('yes_is_radiant')}"
    )
    print(
        f"  model {blocks.model.get('name')}  "
        f"mode={sess.start.get('execution_mode') if sess.start else None}"
    )
    print(
        f"  winner={blocks.final.get('winner')}  pause_s={blocks.final.get('pause_seconds')}  "
        f"missing_s={blocks.final.get('missing_seconds')}"
    )
    print(f"  match.json pnl={blocks.final.get('pnl')}  (ignore if flattened to zero)")


def _fill_leg(token: str, yes_id: object, no_id: object) -> str:
    if isinstance(yes_id, str) and token == yes_id:
        return "yes"
    if isinstance(no_id, str) and token == no_id:
        return "no"
    return "?"


def _print_session(sess: SessionSummary, blocks: _MetaBlocks) -> None:
    print(
        f"  session  fills={sess.fill_count} quotes={sess.quotes}  "
        f"realized={fmt_money(sess.realized)} imv={fmt_money(sess.imv)} "
        f"rebate={fmt_money(sess.rebate)} net={fmt_money(sess.net)}  "
        f"leftover_sum={sess.leftover:.4f}  live={sess.live}"
    )
    if sess.late_fills:
        print(
            f"  late_fill  {sess.late_fills} recovered after session_end; "
            "realized and leftover are corrected, imv has no post-close mark"
        )
    yes_id = blocks.market.get("yes_token_id")
    no_id = blocks.market.get("no_token_id")
    end_pos = sess.positions
    if isinstance(yes_id, str) or isinstance(no_id, str):
        yes_left = end_pos.get(yes_id, 0.0) if isinstance(yes_id, str) else 0.0
        no_left = end_pos.get(no_id, 0.0) if isinstance(no_id, str) else 0.0
        print(f"  leftover yes={yes_left:.4f} no={no_left:.4f}")
    if sess.reasons:
        reason_order = sorted(sess.reasons.items(), key=lambda item: (-item[1], item[0]))
        print("  signal.reason " + " ".join(f"{k}={v}" for k, v in reason_order))
    if sess.blocks:
        block_order = sorted(sess.blocks.items(), key=lambda item: (-item[1], item[0]))
        print("  entry_block " + " ".join(f"{k}={v}" for k, v in block_order))
    if sess.errors:
        print("  trading_error " + ", ".join(sess.errors))
    last = sess.last_signal
    if last:
        print(
            f"  last signal second={last.get('second')} reason={last.get('reason')} "
            f"entry_block={last.get('entry_block')} yes_mid={last.get('yes_mid')} "
            f"yes_fair={last.get('yes_fair')}"
        )
    quote = sess.last_quote
    if quote:
        placed_raw = quote.get("placed")
        placed = cast(list[object], placed_raw) if isinstance(placed_raw, list) else []
        print(
            f"  last quote second={quote.get('second')} decision={quote.get('decision')} "
            f"fv={quote.get('fv_source')} placed={len(placed)}"
        )
    for fill in sess.fills:
        leg = _fill_leg(str(fill.get("token_id") or ""), yes_id, no_id)
        print(
            f"  FILL {fill.get('ts_utc')} t={fill.get('second')} {fill.get('side')} "
            f"{leg} {fill.get('size')} @ {fill.get('price')} maker={fill.get('is_maker')} "
            f"pos={fill.get('position_after')} cash={fill.get('net_cash')}"
        )
    if not sess.fills:
        print("  no fills")


def _print_one(match_id: str, tree: str, archive: Path) -> None:
    meta = load_json(archive / "match.json")
    if meta is None:
        raise SystemExit(f"no match.json at {archive}")
    sess = summarize_session(archive)
    blocks = _meta_blocks(meta)
    _print_meta(match_id, tree, meta, blocks, sess)
    _print_session(sess, blocks)


def cmd_wallet() -> None:
    wallet = live_wallet_db()
    if wallet is None:
        print("no live.db in trader_live or live_paper")
        return
    conn = sqlite3.connect(f"file:{wallet}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cash = conn.execute(
        "SELECT COALESCE(SUM(cash_delta), 0) AS s FROM fill_ledger"
        " WHERE status IN ('MATCHED', 'CONFIRMED', 'MERGED')"
    ).fetchone()["s"]
    positions = list(
        conn.execute("SELECT token_id, size, avg_price FROM positions WHERE size != 0")
    )
    n_fills = conn.execute(
        "SELECT COUNT(*) AS n FROM fill_ledger WHERE status != 'MERGED'"
    ).fetchone()["n"]
    print(f"wallet {wallet}")
    print(f"  ledger_net_cash={cash:+.4f}  fill_rows={n_fills}  nonzero_positions={len(positions)}")
    print("  this is inventory cash, not day PnL")
    for row in positions[:20]:
        print(f"  pos {row['token_id'][-8:]} size={row['size']} avg={row['avg_price']}")
    conn.close()


def read_funder() -> str | None:
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


def berlin_day_bounds(day: date) -> tuple[float, float]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=BERLIN).timestamp()
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=BERLIN).timestamp()
    return start, end


def unix_in_berlin_day(stamp: object, day: date) -> bool:
    unix = _num(stamp)
    if unix is None:
        return False
    parsed = datetime.fromtimestamp(unix, UTC)
    return parsed.astimezone(BERLIN).date() == day


def _in_fold_window(row: ActivityEntry, day: date, after: float | None) -> bool:
    if after is not None:
        return row.timestamp > after
    return unix_in_berlin_day(row.timestamp, day)


def fold_after_payout(
    activity: tuple[ActivityEntry, ...],
    positions: tuple[PositionEntry, ...],
    payout_ts: float,
) -> DayFold:
    return fold_polymarket_day(activity, positions, date.min, after=payout_ts)


def fold_polymarket_day(
    activity: tuple[ActivityEntry, ...],
    positions: tuple[PositionEntry, ...],
    day: date,
    *,
    after: float | None = None,
) -> DayFold:
    buy = 0.0
    sell = 0.0
    redeem = 0.0
    rebate = 0.0
    n_buy = 0
    n_sell = 0
    n_redeem = 0
    n_rebate = 0
    for row in activity:
        if not _in_fold_window(row, day, after):
            continue
        if row.kind == "TRADE" and row.side == "BUY":
            buy += row.usdc_size or 0.0
            n_buy += 1
        elif row.kind == "TRADE" and row.side == "SELL":
            sell += row.usdc_size or 0.0
            n_sell += 1
        elif row.kind == "REDEEM":
            redeem += row.usdc_size or 0.0
            n_redeem += 1
        elif row.kind == "MAKER_REBATE":
            rebate += row.usdc_size or 0.0
            n_rebate += 1
    open_mark = 0.0
    n_open = 0
    for pos in positions:
        if pos.size == 0.0:
            continue
        open_mark += pos.size * pos.cur_price
        n_open += 1
    cash = -buy + sell + redeem + rebate
    return DayFold(
        buy=buy,
        sell=sell,
        redeem=redeem,
        rebate=rebate,
        cash=cash,
        open_mark=open_mark,
        pnl=cash + open_mark,
        n_buy=n_buy,
        n_sell=n_sell,
        n_redeem=n_redeem,
        n_rebate=n_rebate,
        n_open=n_open,
    )


def accrued_rebate_since(
    cut_unix: float, matches: list[tuple[str, Path]] | None = None
) -> RebateAccrual:
    if matches is None:
        matches = match_dirs()
    per_game: dict[str, float] = {}
    n_fills = 0
    n_matches = 0
    for tree, archive in matches:
        if tree == "paper":
            continue
        journal = archive / "session.jsonl"
        try:
            journal_mtime = journal.stat().st_mtime
        except OSError:
            continue
        if journal_mtime < cut_unix:
            continue
        meta = load_json(archive / "match.json") or {}
        game = game_from_meta(meta)
        match_sum = 0.0
        for rec in iter_jsonl(journal):
            if rec.get("kind") not in {"fill", "late_fill"}:
                continue
            ts_utc = rec.get("ts_utc")
            stamp = parse_utc(ts_utc if isinstance(ts_utc, str) else None)
            if stamp is None or stamp.timestamp() < cut_unix:
                continue
            match_sum += maker_rebate(
                _record_money(rec, "price"),
                _record_money(rec, "size"),
                bool(rec.get("is_maker")),
            )
            n_fills += 1
        if match_sum:
            per_game[game] = per_game.get(game, 0.0) + match_sum
            n_matches += 1
    return RebateAccrual(
        total=sum(per_game.values()),
        per_game=dict(per_game),
        fills=n_fills,
        matches=n_matches,
    )


def last_rebate_payout(activity: tuple[ActivityEntry, ...]) -> float | None:
    stamps = [row.timestamp for row in activity if row.kind == "MAKER_REBATE"]
    return max(stamps) if stamps else None


def print_rebate_accrued(result: ActivityResult) -> None:
    if not result.payout_search_complete:
        print("rebate_accrued n/a  payout search incomplete")
        return
    payout = result.newest_payout
    if payout is None:
        print("rebate_accrued n/a  no paid MAKER_REBATE in account history")
        return
    accrued = accrued_rebate_since(payout)
    since = datetime.fromtimestamp(payout, UTC).astimezone(BERLIN)
    games = " ".join(f"{game}={value:+.2f}" for game, value in sorted(accrued.per_game.items()))
    print(
        f"rebate_accrued  since_payout={since:%m-%d %H:%M %Z}  "
        f"total={accrued.total:+.2f}  {games}  "
        f"fills={accrued.fills} matches={accrued.matches}"
    )
    print("rebate_accrued is the session-tape estimate from maker fills, not the paid number.")


def cmd_rebate() -> None:
    funder = read_funder()
    if funder is None:
        print("rebate_accrued n/a  no funder in live.db")
        return
    now = time.time()
    day, _day_start, _day_end = _today_window(now)
    del day
    result = fetch_activity(funder, end=now, day_start=_day_start, day_end=_day_end)
    if result.error is not None:
        print(f"rebate_accrued n/a  {result.error}")
        return
    print_rebate_accrued(result)


def fetch_json(url: str, timeout: float) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _activity_url(funder: str, *, limit: int, offset: int, end: float) -> str:
    query = urllib.parse.urlencode(
        {
            "user": funder,
            "limit": limit,
            "offset": offset,
            "sortBy": "TIMESTAMP",
            "sortDirection": "DESC",
            "start": ACTIVITY_START,
            "end": int(end),
        }
    )
    return f"{POLYMARKET_DATA_API}/activity?{query}"


def _positions_url(funder: str, *, limit: int, offset: int) -> str:
    query = urllib.parse.urlencode(
        {
            "user": funder,
            "sizeThreshold": 0,
            "includeArchived": "true",
            "limit": limit,
            "offset": offset,
            "sortBy": "TITLE",
            "sortDirection": "ASC",
        }
    )
    return f"{POLYMARKET_DATA_API}/positions?{query}"


def _activity_page(payload: object) -> tuple[ActivityEntry, ...] | None:
    if not isinstance(payload, list):
        return None
    out: list[ActivityEntry] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            return None
        row = cast(dict[str, object], item)
        stamp = _num(row.get("timestamp"))
        if stamp is None:
            return None
        kind = row.get("type")
        kind = kind if isinstance(kind, str) else ""
        side = row.get("side")
        side = side.upper() if isinstance(side, str) else ""
        usdc: float | None = None
        if kind in _FOLD_KINDS:
            usdc = _num(row.get("usdcSize"))
            if usdc is None:
                return None
        out.append(ActivityEntry(timestamp=stamp, kind=kind, side=side, usdc_size=usdc))
    return tuple(out)


def _position_page(payload: object) -> tuple[PositionEntry, ...] | None:
    if not isinstance(payload, list):
        return None
    out: list[PositionEntry] = []
    for item in cast(list[object], payload):
        if not isinstance(item, dict):
            return None
        row = cast(dict[str, object], item)
        asset = row.get("asset")
        if not isinstance(asset, str) or not asset:
            return None
        size = _num(row.get("size"))
        cur_price = _num(row.get("curPrice"))
        if size is None or cur_price is None:
            return None
        condition_id = row.get("conditionId")
        title = row.get("title")
        redeemable = row.get("redeemable")
        out.append(
            PositionEntry(
                asset=asset,
                condition_id=condition_id if isinstance(condition_id, str) else None,
                title=title if isinstance(title, str) else None,
                size=size,
                cur_price=cur_price,
                redeemable=redeemable if isinstance(redeemable, bool) else None,
            )
        )
    return tuple(out)


def _descends(page: tuple[ActivityEntry, ...], previous_last: float | None, end: float) -> bool:
    last = previous_last
    for row in page:
        if row.timestamp > end:
            return False
        if last is not None and row.timestamp > last:
            return False
        last = row.timestamp
    return True


@dataclass(frozen=True)
class _PageOutcome:
    done: bool
    stop_reason: str
    error: str | None


@dataclass
class _ActivityWalk:
    end: float
    day_start: float
    page_limit: int
    entries: list[ActivityEntry] = field(default_factory=list)
    pages: int = 0
    day_complete: bool = False
    payout_complete: bool = False
    newest_payout: float | None = None
    previous_last: float | None = None
    previous_sig: tuple[tuple[float, str, str], ...] | None = None

    def absorb(self, page: tuple[ActivityEntry, ...]) -> _PageOutcome:
        self.pages += 1
        if not page:
            self.day_complete = True
            self.payout_complete = True
            return _PageOutcome(True, "exhausted", None)
        if not _descends(page, self.previous_last, self.end):
            return _PageOutcome(True, "invalid", "activity not descending")
        sig = tuple((row.timestamp, row.kind, row.side) for row in page)
        if sig == self.previous_sig:
            return _PageOutcome(True, "invalid", "activity page repeated")
        self.previous_sig = sig
        self.entries.extend(page)
        self.previous_last = page[-1].timestamp
        if self.previous_last < self.day_start:
            self.day_complete = True
        if not self.payout_complete:
            for row in page:
                if row.kind == "MAKER_REBATE":
                    self.newest_payout = row.timestamp
                    self.payout_complete = True
                    break
        if self.day_complete and self.payout_complete:
            return _PageOutcome(True, "proven", None)
        if len(page) < self.page_limit:
            self.day_complete = True
            self.payout_complete = True
            return _PageOutcome(True, "exhausted", None)
        return _PageOutcome(False, "", None)


def fetch_activity(
    funder: str,
    *,
    end: float,
    day_start: float,
    day_end: float,
    fetch: FetchJson = fetch_json,
    wall: WallClock = time.time,
    monotonic: WallClock = time.monotonic,
    deadline_s: float = DAY_DEADLINE_S,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
    page_limit: int = PAGE_LIMIT,
    max_offset: int = ACTIVITY_MAX_OFFSET,
) -> ActivityResult:
    started_wall = wall()
    started_monotonic = monotonic()
    walk = _ActivityWalk(end=end, day_start=day_start, page_limit=page_limit)
    stop_reason = "exhausted"
    error: str | None = None
    offset = 0
    while True:
        if offset > max_offset:
            stop_reason = "offset_cap"
            break
        remaining = deadline_s - (monotonic() - started_monotonic)
        if remaining <= 0.0:
            stop_reason = "deadline"
            break
        url = _activity_url(funder, limit=page_limit, offset=offset, end=end)
        try:
            payload = fetch(url, min(request_timeout_s, remaining))
        except (
            OSError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            stop_reason = "http_error"
            error = type(exc).__name__
            break
        page = _activity_page(payload)
        if page is None:
            stop_reason = "invalid"
            error = "malformed page"
            break
        outcome = walk.absorb(page)
        if outcome.done:
            stop_reason = outcome.stop_reason
            error = outcome.error
            break
        offset += page_limit
    return ActivityResult(
        entries=tuple(walk.entries),
        end=end,
        day_start=day_start,
        day_end=day_end,
        started_at=started_wall,
        completed_at=wall(),
        pages=walk.pages,
        day_complete=walk.day_complete,
        payout_search_complete=walk.payout_complete,
        newest_payout=walk.newest_payout,
        stop_reason=stop_reason,
        error=error,
    )


def fetch_positions(
    funder: str,
    *,
    fetch: FetchJson = fetch_json,
    wall: WallClock = time.time,
    monotonic: WallClock = time.monotonic,
    deadline_s: float = DAY_DEADLINE_S,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
    page_limit: int = PAGE_LIMIT,
    max_offset: int = POSITIONS_MAX_OFFSET,
) -> PositionsResult:
    started_wall = wall()
    started_monotonic = monotonic()
    as_of = started_wall
    positions: list[PositionEntry] = []
    seen_assets: set[str] = set()
    pages = 0
    traversal_complete = False
    stop_reason = "exhausted"
    error: str | None = None
    offset = 0
    while True:
        if offset > max_offset:
            stop_reason = "offset_cap"
            break
        remaining = deadline_s - (monotonic() - started_monotonic)
        if remaining <= 0.0:
            stop_reason = "deadline"
            break
        url = _positions_url(funder, limit=page_limit, offset=offset)
        try:
            payload = fetch(url, min(request_timeout_s, remaining))
        except (
            OSError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            stop_reason = "http_error"
            error = type(exc).__name__
            break
        page = _position_page(payload)
        if page is None:
            stop_reason = "invalid"
            error = "malformed page"
            break
        pages += 1
        conflict = False
        for row in page:
            if row.asset in seen_assets:
                conflict = True
                break
            seen_assets.add(row.asset)
            positions.append(row)
        if conflict:
            stop_reason = "invalid"
            error = "position asset repeated"
            break
        if len(page) < page_limit:
            traversal_complete = True
            stop_reason = "exhausted"
            break
        offset += page_limit
    return PositionsResult(
        positions=tuple(positions),
        as_of=as_of,
        started_at=started_wall,
        completed_at=wall(),
        pages=pages,
        traversal_complete=traversal_complete,
        stop_reason=stop_reason,
        error=error,
    )


def _today_window(now: float) -> tuple[date, float, float]:
    day = datetime.fromtimestamp(now, BERLIN).date()
    day_start, day_end = berlin_day_bounds(day)
    return day, day_start, day_end


def fetch_day(
    funder: str,
    *,
    now: float | None = None,
    fetch: FetchJson = fetch_json,
    wall: WallClock = time.time,
    monotonic: WallClock = time.monotonic,
    deadline_s: float = DAY_DEADLINE_S,
    request_timeout_s: float = REQUEST_TIMEOUT_S,
    page_limit: int = PAGE_LIMIT,
) -> DayResult:
    if now is None:
        now = wall()
    day, day_start, day_end = _today_window(now)
    started = monotonic()
    activity = fetch_activity(
        funder,
        end=now,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        wall=wall,
        monotonic=monotonic,
        deadline_s=deadline_s,
        request_timeout_s=request_timeout_s,
        page_limit=page_limit,
    )
    positions = fetch_positions(
        funder,
        fetch=fetch,
        wall=wall,
        monotonic=monotonic,
        deadline_s=max(0.0, deadline_s - (monotonic() - started)),
        request_timeout_s=request_timeout_s,
        page_limit=page_limit,
    )
    problems: list[str] = []
    if not activity.day_complete:
        problems.append(f"activity:{activity.stop_reason}")
    if not positions.traversal_complete:
        problems.append(f"positions:{positions.stop_reason}")
    for result in (activity, positions):
        if result.error is not None:
            problems.append(f"{result.error}")
    if problems:
        return DayResult(
            day=day.isoformat(),
            fold=None,
            fetched_at=None,
            complete=False,
            error=" ".join(problems),
            activity=activity,
            positions=positions,
        )
    fold = fold_polymarket_day(activity.entries, positions.positions, day)
    return DayResult(
        day=day.isoformat(),
        fold=fold,
        fetched_at=now,
        complete=True,
        error=None,
        activity=activity,
        positions=positions,
    )


def publish_day(previous: DayResult | None, candidate: DayResult) -> DayResult:
    if candidate.complete:
        return candidate
    if previous is not None and previous.fold is not None and previous.day == candidate.day:
        return DayResult(
            day=previous.day,
            fold=previous.fold,
            fetched_at=previous.fetched_at,
            complete=False,
            error=candidate.error,
            activity=candidate.activity or previous.activity,
            positions=candidate.positions or previous.positions,
        )
    return candidate


def print_polymarket_today(day: date) -> None:
    funder = read_funder()
    if funder is None:
        print("polymarket_today n/a  no funder in live.db")
        return
    result = fetch_day(funder)
    if not result.complete or result.fold is None:
        reason = result.error or "incomplete"
        print(f"polymarket_today n/a  {reason}")
        if result.activity is not None:
            print_rebate_accrued(result.activity)
        return
    folded = result.fold
    print(
        f"polymarket_today  buy={folded.buy:.2f} sell={folded.sell:.2f} "
        f"redeem={folded.redeem:.2f} rebate={folded.rebate:.2f} "
        f"cash={folded.cash:+.2f} open={folded.open_mark:+.2f} "
        f"pnl={folded.pnl:+.2f}  "
        f"n_buy={folded.n_buy} n_sell={folded.n_sell} "
        f"n_redeem={folded.n_redeem} n_rebate={folded.n_rebate} n_open={folded.n_open}"
    )
    print(
        "day number is polymarket_today pnl (cash+open). leftover BUY is not a loss if REDEEM paid."
    )
    if result.activity is not None:
        print_rebate_accrued(result.activity)


def check_game_default() -> None:
    if game_from_meta({}) != "dota":
        raise SystemExit("missing game must default to dota")
    if game_from_meta({"game": "lol"}) != "lol":
        raise SystemExit("lol game must stay lol")
    if HOST_TREES[0][0] != "live" or HOST_TREES[1][0] != "paper" or HOST_TREES[2][0] != "legacy":
        raise SystemExit("host tree order must be live, paper, legacy")
    dummy_sess = _open_summary({"kind": "session_start"})
    no_start = _open_summary(None)
    dummy_meta: dict[str, object] = {}
    dummy_archive = Path("/nonexistent")
    if is_open_session("legacy", dummy_archive, dummy_sess, dummy_meta):
        raise SystemExit("legacy tree must not count as an open session")
    if not is_open_session("live", dummy_archive, dummy_sess, dummy_meta):
        raise SystemExit("live tree without end/final/cleanup must count as open")
    if is_open_session("live", dummy_archive, dummy_sess, {"final": {"winner": None}}):
        raise SystemExit("GRID final with null winner must not count as open")
    if is_open_session("live", dummy_archive, no_start, {"record_only": True}):
        raise SystemExit("record-only has no session_start: must not count as open")
    if not is_open_session("live", dummy_archive, dummy_sess, {"record_only": True}):
        raise SystemExit("a real session_start outranks a stale record_only marker")
    check_stale_open()


def _open_summary(start: dict[str, object] | None) -> SessionSummary:
    return SessionSummary(
        start=start,
        end=None,
        fills=(),
        fill_count=0,
        quotes=0,
        reasons=MappingProxyType({}),
        blocks=MappingProxyType({}),
        errors=(),
        last_signal=None,
        last_quote=None,
        last_model=None,
        last_fill=None,
        rebate=0.0,
        realized=None,
        imv=None,
        net=None,
        late_fills=0,
        positions=MappingProxyType({}),
        leftover=0.0,
        live=True,
    )


def check_stale_open() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "grid-1-m1"
        archive.mkdir()
        tape = archive / "grid_state.jsonl"
        tape.write_text("{}\n")
        sess = _open_summary({"kind": "session_start"})
        old = datetime.now(UTC).timestamp() - 4.0 * STALE_OPEN_SECONDS
        os.utime(tape, (old, old))
        if is_open_session("live", archive, sess, {}):
            raise SystemExit("stale archive must not count as an open session")
        fresh = datetime.now(UTC).timestamp()
        os.utime(tape, (fresh, fresh))
        if not is_open_session("live", archive, sess, {}):
            raise SystemExit("freshly written archive must count as an open session")


def check_fold() -> None:
    day = datetime(2026, 8, 29, tzinfo=BERLIN).date()
    noon = datetime(2026, 8, 29, 12, 0, tzinfo=BERLIN)
    ts = noon.timestamp()
    rows = (
        ActivityEntry(timestamp=ts, kind="TRADE", side="BUY", usdc_size=50.0),
        ActivityEntry(timestamp=ts, kind="REDEEM", side="", usdc_size=52.08),
        ActivityEntry(timestamp=ts, kind="MAKER_REBATE", side="", usdc_size=0.2),
    )
    folded = fold_polymarket_day(rows, (PositionEntry("a", "0xc", None, 4.0, 0.3, None),), day)
    cash = round(folded.cash, 2)
    pnl = round(folded.pnl, 2)
    if cash != 2.28 or pnl != 3.48:
        raise SystemExit(f"fold check failed cash={cash} pnl={pnl}")


def check_rebate_cut() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "trader_live"
        archive = root / "m1"
        archive.mkdir(parents=True)
        (archive / "match.json").write_text(json.dumps({"match_id": "m1"}))
        past = datetime.now(UTC).timestamp() - 3600.0
        ts = datetime.fromtimestamp(past, UTC).isoformat().replace("+00:00", "Z")
        (archive / "session.jsonl").write_text(
            json.dumps(
                {
                    "kind": "fill",
                    "token_id": "t",
                    "side": "BUY",
                    "price": 0.5,
                    "size": 10.0,
                    "is_maker": True,
                    "position_after": 10.0,
                    "net_cash": -5.0,
                    "second": 0,
                    "ts_utc": ts,
                }
            )
            + "\n"
        )
        matches = match_dirs((("live", root),))
        future = datetime.now(UTC).timestamp() + 3600.0
        accrued = accrued_rebate_since(future, matches)
        if accrued.fills != 0 or accrued.total != 0.0:
            raise SystemExit("future rebate cut must accrue nothing")
        past_cut = past - 60.0
        accrued = accrued_rebate_since(past_cut, matches)
        if accrued.fills != 1:
            raise SystemExit("past rebate cut must see the one fixture fill")
        expected = maker_rebate(0.5, 10.0, True)
        if abs(accrued.total - expected) > 1e-9:
            raise SystemExit("rebate accrual must equal the maker estimate")


def _fake_fetch(pages: Sequence[object]) -> FetchJson:
    calls = 0

    def fetch(url: str, timeout: float) -> object:
        nonlocal calls
        del url, timeout
        page: object = pages[calls] if calls < len(pages) else []
        calls += 1
        return page

    return fetch


def check_paging() -> None:
    day = date(2026, 8, 29)
    day_start, day_end = berlin_day_bounds(day)
    end = day_end + 3600.0
    in_day = day_start + 43200.0
    _check_activity_boundaries(day, day_start, day_end, end, in_day)
    _check_activity_failures(day_start, day_end, end, in_day)
    _check_positions(day)
    _check_day_result(day, in_day, end)


def _check_activity_boundaries(
    day: date, day_start: float, day_end: float, end: float, in_day: float
) -> None:
    pages: list[object] = [
        [{"type": "TRADE", "side": "BUY", "usdcSize": 1.0, "timestamp": in_day}],
        [{"type": "REDEEM", "usdcSize": 2.0, "timestamp": day_start - 60.0}],
    ]
    fetch = _fake_fetch(pages)
    result = fetch_activity(
        "0xabc",
        end=end,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        page_limit=1,
        deadline_s=60.0,
    )
    if not result.day_complete or not result.payout_search_complete:
        raise SystemExit("boundary row before day start must prove the day complete")
    if result.pages != 3:
        raise SystemExit("payout search without a rebate row must page to exhaustion")

    midnight_rows = [
        [{"type": "TRADE", "side": "SELL", "usdcSize": 3.0, "timestamp": day_start}],
        [{"type": "TRADE", "side": "BUY", "usdcSize": 4.0, "timestamp": day_start}],
        [{"type": "TRADE", "side": "BUY", "usdcSize": 9.0, "timestamp": day_start - 1.0}],
    ]
    fetch = _fake_fetch(midnight_rows)
    result = fetch_activity(
        "0xabc",
        end=end,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        page_limit=1,
        deadline_s=60.0,
    )
    if not result.day_complete:
        raise SystemExit("equal-second rows split across pages must keep the scan going")
    fold = fold_polymarket_day(result.entries, (), day)
    if abs(fold.cash - (-4.0 + 3.0)) > 1e-9:
        raise SystemExit("rows exactly at midnight belong to the day")

    dst_day = date(2026, 10, 25)
    dst_start, dst_end = berlin_day_bounds(dst_day)
    if dst_end - dst_start != 25.0 * 3600.0:
        raise SystemExit("Berlin DST day must be 25 hours, not 86400 seconds")

    payout = day_start - 7200.0
    fetch = _fake_fetch(
        [
            [{"type": "TRADE", "side": "BUY", "usdcSize": 1.0, "timestamp": in_day}],
            [{"type": "TRADE", "side": "BUY", "usdcSize": 1.0, "timestamp": day_start - 60.0}],
            [{"type": "MAKER_REBATE", "usdcSize": 0.01, "timestamp": payout}],
            [],
        ]
    )
    result = fetch_activity(
        "0xabc",
        end=end,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        page_limit=1,
        deadline_s=60.0,
    )
    if result.newest_payout != payout or not result.payout_search_complete:
        raise SystemExit("the newest MAKER_REBATE must prove the payout cut")
    if not result.day_complete:
        raise SystemExit("the payout search must not invalidate a proven day")


def _check_activity_failures(day_start: float, day_end: float, end: float, in_day: float) -> None:
    fetch = _fake_fetch([[{"type": "REDEEM", "usdcSize": 1.0, "timestamp": end + 100.0}]])
    result = fetch_activity(
        "0xabc",
        end=end,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        page_limit=1,
        deadline_s=60.0,
    )
    if result.day_complete or result.stop_reason != "invalid":
        raise SystemExit("a row newer than the fixed end must fail the traversal")

    fetch = _fake_fetch([{"error": "nope"}])
    result = fetch_activity(
        "0xabc", end=end, day_start=day_start, day_end=day_end, fetch=fetch, page_limit=1
    )
    if result.day_complete or result.stop_reason != "invalid":
        raise SystemExit("a non-list page must fail the traversal")

    fetch = _fake_fetch([[{"type": "TRADE", "side": "BUY", "usdcSize": "x", "timestamp": in_day}]])
    result = fetch_activity(
        "0xabc", end=end, day_start=day_start, day_end=day_end, fetch=fetch, page_limit=1
    )
    if result.day_complete:
        raise SystemExit("malformed money must fail the traversal")

    fetch = _fake_fetch([[{"type": "TRADE", "side": "BUY", "usdcSize": 1.0, "timestamp": in_day}]])
    result = fetch_activity(
        "0xabc",
        end=end,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        page_limit=1,
        max_offset=0,
    )
    if result.day_complete or result.stop_reason != "offset_cap":
        raise SystemExit("a full page at the offset cap is not exhaustion")

    fetch = _fake_fetch(
        [
            [{"type": "TRADE", "side": "BUY", "usdcSize": 1.0, "timestamp": in_day}],
            [{"type": "TRADE", "side": "BUY", "usdcSize": 1.0, "timestamp": day_start - 60.0}],
        ]
    )
    result = fetch_activity(
        "0xabc",
        end=end,
        day_start=day_start,
        day_end=day_end,
        fetch=fetch,
        page_limit=1,
        max_offset=1,
    )
    if result.payout_search_complete:
        raise SystemExit("a capped search without a payout row is unknown, not absent")
    if not result.day_complete:
        raise SystemExit("a capped payout search must not invalidate a proven day")


def _check_positions(day: date) -> None:
    fetch = _fake_fetch([[]])
    positions = fetch_positions("0xabc", fetch=fetch, page_limit=1)
    if not positions.traversal_complete or positions.positions:
        raise SystemExit("a valid empty positions page is legitimate zero inventory")

    dust = [
        [
            {
                "asset": "t1",
                "conditionId": "0xc1",
                "size": 0.3,
                "curPrice": 0.5,
                "redeemable": True,
            },
            {"asset": "t2", "conditionId": "0xc1", "size": 5.0, "curPrice": 0.2},
        ],
        [{"asset": "t3", "size": 0.0, "curPrice": 1.0}],
    ]
    fetch = _fake_fetch(dust)
    positions = fetch_positions("0xabc", fetch=fetch, page_limit=2)
    if not positions.traversal_complete or len(positions.positions) != 3:
        raise SystemExit("sub-share dust and redeemable residues must stay in the result")
    fold = fold_polymarket_day((), positions.positions, day)
    if abs(fold.open_mark - (0.3 * 0.5 + 5.0 * 0.2 + 0.0)) > 1e-9:
        raise SystemExit("open mark must include dust positions")

    fetch = _fake_fetch(
        [
            [{"asset": "t1", "size": 1.0, "curPrice": 0.5}],
            [{"asset": "t1", "size": 2.0, "curPrice": 0.5}],
        ]
    )
    positions = fetch_positions("0xabc", fetch=fetch, page_limit=1)
    if positions.traversal_complete:
        raise SystemExit("a repeated asset must not be folded twice or declared complete")

    fetch = _fake_fetch([[{"asset": "t1", "size": 1.0, "curPrice": 0.5}]])
    positions = fetch_positions("0xabc", fetch=fetch, page_limit=1, max_offset=0)
    if positions.traversal_complete or positions.stop_reason != "offset_cap":
        raise SystemExit("a full page at the positions cap is not exhaustion")


def _check_day_result(day: date, in_day: float, end: float) -> None:
    def failing_fetch(url: str, timeout: float) -> object:
        raise TimeoutError("boom")

    day_result = fetch_day("0xabc", now=in_day, fetch=failing_fetch)
    if day_result.complete or day_result.fold is not None:
        raise SystemExit("a failed page must not publish a partial fold")

    retained = publish_day(
        DayResult(
            day=day.isoformat(),
            fold=DayFold(1.0, 0.0, 2.0, 0.0, 1.0, 0.5, 1.5, 1, 0, 1, 0, 1),
            fetched_at=end,
            complete=True,
            error=None,
            activity=None,
            positions=None,
        ),
        day_result,
    )
    if retained.fold is None or retained.fetched_at != end or retained.complete:
        raise SystemExit("an incomplete candidate must retain the last complete fold")


def check_restart_block() -> None:
    if restart_block(100.0, 0.0, 0.0) != "in_window":
        raise SystemExit("flat map at second 100 must be in_window")
    if restart_block(480.0, 0.0, 0.0) is not None:
        raise SystemExit("flat map at second 480 must be safe")
    if restart_block(600.0, 10.0, 0.0) != "position":
        raise SystemExit("10 shares at second 600 must be position")
    if restart_block(600.0, 0.5, 0.0) is not None:
        raise SystemExit("dust below min size must be safe")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", action="store_true", help="Berlin calendar day")
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "open live/paper sessions (no session_end, no final, no cleanup, "
            "written within 15 min; not legacy)"
        ),
    )
    parser.add_argument("--game", choices=("dota", "lol"), help="filter match.json game")
    parser.add_argument("--match", help="one Steam match id")
    parser.add_argument("--wallet", action="store_true", help="sqlite inventory snapshot")
    parser.add_argument(
        "--rebate",
        action="store_true",
        help="maker rebate accrued since the last paid MAKER_REBATE",
    )
    parser.add_argument(
        "--restart-check",
        action="store_true",
        help="restart_check verdict; exit 1 when an open live map blocks a restart",
    )
    parser.add_argument(
        "--self-check", action="store_true", help="assert paging and fold contracts"
    )
    args = parser.parse_args()
    if args.self_check:
        check_fold()
        check_rebate_cut()
        check_paging()
        check_game_default()
        check_restart_block()
        print("self-check ok")
        return
    if args.match:
        cmd_one(args.match)
        return
    if args.wallet:
        cmd_wallet()
        return
    if args.rebate:
        cmd_rebate()
        return
    if args.restart_check:
        cmd_restart_check()
        return
    cmd_list(today=args.today, live_only=args.live, game=args.game)


if __name__ == "__main__":
    main()
