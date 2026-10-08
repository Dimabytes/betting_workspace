"""Remove our live resting size from Telonex book snapshots for archive replays.

Recorded `book_snapshot_full` includes the live bot's own orders. Nautilus
`queue_position` then sees that size as queue ahead of the simulated twin, so
partials that happened live never trigger and later fills diverge.

Rebuild resting from `core_trace.jsonl` (places -> OrderAccepted -> Fill /
CancelAck / OrderRejected) and subtract from each snapshot before the book is
fed to the framework. Research matches without a live archive are untouched.
"""

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pandas as pd

from shared.utils.jsonl_io import open_maybe_gz, resolve_jsonl

_PRICE_TICK = 0.01


@dataclass(frozen=True)
class _PlaceMeta:
    token_index: int
    side: str
    price: float
    quantity: float


@dataclass
class _Resting:
    token_index: int
    side: str
    price: float
    quantity: float


def _as_side(value: object) -> str:
    return str(value).strip().upper()


def _price_key(price: float) -> int:
    """Integer tick index for 0.01 Polymarket prices."""
    return round(float(price) / _PRICE_TICK)


def _wall_us_from_now(*, now_ns: int, offset_ns: int) -> int:
    return (int(now_ns) + int(offset_ns)) // 1000


def _offset_from_header(header: Mapping[str, Any]) -> int | None:
    if header.get("kind") != "header":
        return None
    opened_wall_s = header.get("opened_wall_s")
    opened_now_ns = header.get("opened_now_ns")
    if opened_wall_s is None or opened_now_ns is None:
        return None
    return int(float(opened_wall_s) * 1e9) - int(opened_now_ns)


def _session_fill_wall_ns(archive_dir: Path) -> dict[str, int]:
    session_path = archive_dir / "session.jsonl"
    fill_ts: dict[str, int] = {}
    if not session_path.is_file():
        return fill_ts
    with session_path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("kind") != "fill":
                continue
            key = row.get("fill_key")
            ts_utc = row.get("ts_utc")
            if key and ts_utc:
                fill_ts[str(key)] = int(pd.Timestamp(ts_utc).value)
    return fill_ts


def _offset_from_first_fill(archive_dir: Path, fill_ts: Mapping[str, int]) -> int | None:
    path = archive_dir / "core_trace.jsonl"
    with open_maybe_gz(path) as handle:
        for line in handle:
            row = cast(dict[str, Any], json.loads(line))
            if row.get("kind") != "event":
                continue
            event = cast(dict[str, Any], row.get("event") or {})
            if event.get("type") != "Fill":
                continue
            fill_id = event.get("fill_id")
            now_ns = event.get("now_ns", row.get("now_ns"))
            if fill_id in fill_ts and now_ns is not None:
                return int(fill_ts[str(fill_id)]) - int(now_ns)
    return None


def clock_offset_ns(archive_dir: Path) -> int:
    """Map core_trace `now_ns` to wall epoch ns.

    Prefer header `opened_wall_s` / `opened_now_ns`. Fall back to the first
    Fill whose `fill_id` matches a session.jsonl `fill_key` with `ts_utc`.
    """
    header_path = resolve_jsonl(archive_dir / "core_trace.jsonl")
    if not header_path.is_file():
        raise FileNotFoundError(f"missing core_trace.jsonl: {archive_dir}")
    with open_maybe_gz(header_path) as handle:
        header = json.loads(handle.readline())
    offset = _offset_from_header(header)
    if offset is not None:
        return offset
    fill_ts = _session_fill_wall_ns(archive_dir)
    offset = _offset_from_first_fill(archive_dir, fill_ts)
    if offset is not None:
        return offset
    raise ValueError(f"cannot resolve core_trace clock offset: {archive_dir}")


def iter_resting_events(archive_dir: Path) -> Iterator[tuple[int, str, Any]]:
    """Yield (wall_us, op, payload).

    ops: ``accept`` (_Resting), ``fill`` (order_id, qty), ``remove`` (order_id).

    Telonex often prints our size a few dozen ms *before* OrderAccepted. Start
    resting at the place plan's wall time (once accept confirms), so the as-of
    book at accept already has us stripped. Caller should sort by wall_us.
    """
    offset_ns = clock_offset_ns(archive_dir)
    pending: dict[str, tuple[_PlaceMeta, int]] = {}
    path = archive_dir / "core_trace.jsonl"
    with open_maybe_gz(path) as handle:
        for line in handle:
            row = cast(dict[str, Any], json.loads(line))
            if row.get("kind") != "event":
                continue
            event = cast(dict[str, Any], row.get("event") or {})
            plan = cast(dict[str, Any], row.get("plan") or {})
            now_ns = int(event.get("now_ns") or row.get("now_ns") or 0)
            wall_us = _wall_us_from_now(now_ns=now_ns, offset_ns=offset_ns)

            for place_raw in cast(Sequence[Any], plan.get("places") or []):
                place = cast(dict[str, Any], place_raw)
                order_id = str(place["order_id"])
                meta = _PlaceMeta(
                    token_index=int(place["token_index"]),
                    side=_as_side(place["side"]),
                    price=float(place["price"]),
                    quantity=float(place["quantity"]),
                )
                pending[order_id] = (meta, wall_us)

            etype = event.get("type")
            if etype == "OrderAccepted":
                order_id = str(event["order_id"])
                held = pending.get(order_id)
                if held is None:
                    continue
                meta, place_wall_us = held
                yield (
                    place_wall_us,
                    "accept",
                    (
                        order_id,
                        _Resting(
                            token_index=meta.token_index,
                            side=meta.side,
                            price=meta.price,
                            quantity=meta.quantity,
                        ),
                    ),
                )
            elif etype == "Fill":
                yield wall_us, "fill", (str(event["order_id"]), float(event["qty"]))
            elif etype in ("CancelAck", "OrderRejected"):
                order_id = str(event["order_id"])
                pending.pop(order_id, None)
                yield wall_us, "remove", order_id


def _apply_resting_op(live: dict[str, _Resting], op: str, payload: Any) -> None:
    if op == "accept":
        order_id, resting = payload
        # Copy: the event payload is replayed once per token and per day, and a
        # later Fill mutates quantity in place.
        live[order_id] = replace(resting)
    elif op == "fill":
        order_id, qty = payload
        cur = live.get(order_id)
        if cur is None:
            return
        left = cur.quantity - qty
        if left <= 1e-9:
            live.pop(order_id, None)
        else:
            cur.quantity = left
    elif op == "remove":
        live.pop(payload, None)


def resting_by_token_at(
    events: Sequence[tuple[int, str, Any]], *, wall_us: int
) -> dict[int, dict[tuple[str, int], float]]:
    """token_index -> {(side, price_key): qty} for resting orders at wall_us."""
    live: dict[str, _Resting] = {}
    for event_us, op, payload in events:
        if event_us > wall_us:
            break
        _apply_resting_op(live, op, payload)

    out: dict[int, dict[tuple[str, int], float]] = {}
    for resting in live.values():
        bucket = out.setdefault(resting.token_index, {})
        key = (resting.side, _price_key(resting.price))
        bucket[key] = bucket.get(key, 0.0) + resting.quantity
    return out


def _strip_levels(
    raw: Sequence[Any] | None, *, side: str, subtract: Mapping[tuple[str, int], float]
) -> list[dict[str, str]]:
    if raw is None:
        return []
    side_u = _as_side(side)
    leftover = {
        price_key: qty for (s, price_key), qty in subtract.items() if s == side_u and qty > 0.0
    }
    out: list[dict[str, str]] = []
    for item_raw in raw:
        if not isinstance(item_raw, Mapping):
            continue
        item = cast(Mapping[Any, Any], item_raw)
        try:
            price = float(item["price"])
            size = float(item["size"])
        except (KeyError, TypeError, ValueError):
            continue
        key = _price_key(price)
        cut = leftover.pop(key, 0.0)
        new_size = size - cut
        if new_size > 1e-9:
            out.append({"price": str(item["price"]), "size": format(new_size, ".10g")})
    return out


def _subtract_map(
    live: Mapping[str, _Resting], *, token_index: int
) -> dict[tuple[str, int], float]:
    subtract: dict[tuple[str, int], float] = {}
    for resting in live.values():
        if resting.token_index != token_index:
            continue
        key = (resting.side, _price_key(resting.price))
        subtract[key] = subtract.get(key, 0.0) + resting.quantity
    return subtract


def strip_book_frame(
    book: pd.DataFrame, *, token_index: int, events: Sequence[tuple[int, str, Any]]
) -> pd.DataFrame:
    """Return a copy of book with this token's live resting size removed."""
    if book.empty or not events:
        return book.copy()

    rows: list[dict[Any, Any]] = []
    live: dict[str, _Resting] = {}
    cursor = 0
    n_events = len(events)

    for record in book.to_dict(orient="records"):
        wall_us = int(record["timestamp_us"])
        while cursor < n_events and events[cursor][0] <= wall_us:
            _apply_resting_op(live, events[cursor][1], events[cursor][2])
            cursor += 1

        subtract = _subtract_map(live, token_index=token_index)
        row = dict(record)
        if subtract:
            row["bids"] = _strip_levels(record.get("bids"), side="BUY", subtract=subtract)
            row["asks"] = _strip_levels(record.get("asks"), side="SELL", subtract=subtract)
        rows.append(row)
    return pd.DataFrame.from_records(rows, columns=list(book.columns))


def strip_day_book_file(
    *,
    book_path: Path,
    out_path: Path,
    token_index: int,
    events: Sequence[tuple[int, str, Any]],
) -> None:
    """Read one Telonex book day parquet, strip resting, write to out_path."""
    book = pd.read_parquet(book_path)
    stripped = strip_book_frame(book, token_index=token_index, events=events)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    stripped.to_parquet(out_path, index=False)


def load_resting_events(archive_dir: Path) -> tuple[tuple[int, str, Any], ...]:
    """Materialize resting events for one live archive, ordered by wall time."""
    return tuple(sorted(iter_resting_events(archive_dir), key=lambda item: item[0]))
