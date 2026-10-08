"""Durable place/cancel attempts and signed CLOB identity."""

# pyright: reportMissingTypeStubs=false, reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from types import CoroutineType
from typing import Any, Protocol

from polymaker.domain import OpenOrder, Quote, Side
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.order_builder.builder import ExchangeOrderBuilderV2
from py_clob_client_v2.order_utils.model.order_data_v2 import OrderV2
from py_clob_client_v2.signer import Signer

from trader.core_persistence import (
    CoreCommand,
    DispatchState,
    upsert_command,
)

_HASH_SIGNER = Signer("0x" + "11" * 32, 137)


@dataclass(frozen=True)
class PreparedPlace:
    core_order_id: str
    quote: Quote
    order_hash: str
    signed: object | None


def exchange_address(*, chain_id: int, neg_risk: bool) -> str:
    config = get_contract_config(chain_id)
    if neg_risk:
        return config.neg_risk_exchange_v2
    return config.exchange_v2


def hash_signed_order(*, chain_id: int, neg_risk: bool, order: OrderV2) -> str:
    builder = ExchangeOrderBuilderV2(
        exchange_address(chain_id=chain_id, neg_risk=neg_risk),
        chain_id,
        _HASH_SIGNER,
    )
    typed = builder.build_order_typed_data(order)
    return builder.build_order_hash(typed)


def paper_order_hash(*, quote: Quote, core_order_id: str) -> str:
    return (
        f"paper:{core_order_id}:{quote.token_id}:{quote.side.value}:"
        f"{quote.price:.4f}:{quote.size:.4f}"
    )


def prepare_dispatch(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    revision: int,
    batch_id: str,
    places: Sequence[PreparedPlace],
) -> tuple[CoreCommand, ...]:
    commands = persist_prepared_places(
        conn,
        session_id=session_id,
        revision=revision,
        batch_id=batch_id,
        places=places,
    )
    conn.commit()
    started = tuple(replace(command, dispatch_state="dispatch_started") for command in commands)
    mark_dispatch_started(conn, commands)
    conn.commit()
    return started


def record_dispatch_results(
    conn: sqlite3.Connection,
    *,
    commands: Sequence[CoreCommand],
    places: Sequence[PreparedPlace],
    placed: Sequence[OpenOrder],
) -> None:
    pairs = bind_place_results(planned=places, placed=placed)
    by_id = {command.core_order_id: command for command in commands}
    for item, order in pairs:
        persist_place_outcome(
            conn,
            command=by_id[item.core_order_id],
            venue_id=None if order is None else order.order_id,
            outcome="accepted" if order is not None else "unknown",
        )
    conn.commit()


def persist_then_dispatch(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    revision: int,
    batch_id: str,
    places: Sequence[PreparedPlace],
    post: Callable[[], list[OpenOrder]],
) -> list[OpenOrder]:
    commands = prepare_dispatch(
        conn,
        session_id=session_id,
        revision=revision,
        batch_id=batch_id,
        places=places,
    )
    placed = post()
    record_dispatch_results(conn, commands=commands, places=places, placed=placed)
    return placed


def persist_prepared_places(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    revision: int,
    batch_id: str,
    places: Sequence[PreparedPlace],
) -> tuple[CoreCommand, ...]:
    commands: list[CoreCommand] = []
    for item in places:
        command = CoreCommand(
            command_id=f"{session_id}:{batch_id}:{item.core_order_id}",
            session_id=session_id,
            revision=revision,
            batch_id=batch_id,
            kind="place",
            core_order_id=item.core_order_id,
            token_index=None,
            side="BUY" if item.quote.side is Side.BUY else "SELL",
            price=item.quote.price,
            quantity=item.quote.size,
            venue_id=None,
            order_hash=item.order_hash,
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        )
        upsert_command(conn, command)
        commands.append(command)
    return tuple(commands)


def persist_prepared_cancels(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    revision: int,
    batch_id: str,
    venue_ids: Sequence[str],
    core_ids: Sequence[str],
) -> tuple[CoreCommand, ...]:
    commands: list[CoreCommand] = []
    for venue_id, core_id in zip(venue_ids, core_ids, strict=True):
        command = CoreCommand(
            command_id=f"{session_id}:{batch_id}:cancel:{core_id}",
            session_id=session_id,
            revision=revision,
            batch_id=batch_id,
            kind="cancel",
            core_order_id=core_id,
            token_index=None,
            side=None,
            price=None,
            quantity=None,
            venue_id=venue_id,
            order_hash=None,
            dispatch_state="prepared",
            outcome="",
            consumed=False,
        )
        upsert_command(conn, command)
        commands.append(command)
    return tuple(commands)


def mark_dispatch_started(conn: sqlite3.Connection, commands: Sequence[CoreCommand]) -> None:
    for command in commands:
        upsert_command(conn, replace(command, dispatch_state="dispatch_started"))


def mark_known_not_sent(conn: sqlite3.Connection, commands: Sequence[CoreCommand]) -> None:
    for command in commands:
        if command.dispatch_state != "prepared":
            continue
        upsert_command(
            conn,
            replace(command, dispatch_state="known_not_sent", outcome="canceled", consumed=True),
        )


def persist_place_outcome(
    conn: sqlite3.Connection,
    *,
    command: CoreCommand,
    venue_id: str | None,
    outcome: str,
) -> CoreCommand:
    dispatch: DispatchState = "dispatched"
    updated = replace(command, venue_id=venue_id, dispatch_state=dispatch, outcome=outcome)
    upsert_command(conn, updated)
    return updated


def persist_cancel_outcome(
    conn: sqlite3.Connection, *, command: CoreCommand, ok: bool
) -> CoreCommand:
    updated = replace(
        command,
        dispatch_state="dispatched",
        outcome="canceled" if ok else "timeout",
    )
    upsert_command(conn, updated)
    return updated


def consume_command(conn: sqlite3.Connection, command: CoreCommand) -> None:
    upsert_command(conn, replace(command, consumed=True))


def retire_unsent(conn: sqlite3.Connection, commands: Sequence[CoreCommand]) -> tuple[str, ...]:
    retired: list[str] = []
    for command in commands:
        if command.dispatch_state != "prepared" or command.consumed:
            continue
        mark_known_not_sent(conn, (command,))
        retired.append(command.core_order_id)
    return tuple(retired)


def bind_place_results(
    *, planned: Sequence[PreparedPlace], placed: Sequence[OpenOrder]
) -> tuple[tuple[PreparedPlace, OpenOrder | None], ...]:
    leftover = list(placed)
    pairs: list[tuple[PreparedPlace, OpenOrder | None]] = []
    for item in planned:
        match_at = next(
            (
                index
                for index, order in enumerate(leftover)
                if order.token_id == item.quote.token_id
                and order.side is item.quote.side
                and abs(order.price - item.quote.price) < 1e-12
                and abs(order.size - item.quote.size) < 1e-12
            ),
            None,
        )
        if match_at is None:
            pairs.append((item, None))
            continue
        pairs.append((item, leftover.pop(match_at)))
    return tuple(pairs)


class RecomputeLocked(Protocol):
    def __call__(self, cid: str) -> CoroutineType[Any, Any, None]: ...


def wrap_recompute_retire(
    *,
    original: RecomputeLocked,
    load_prepared: Callable[[str], tuple[CoreCommand, ...]],
    retire: Callable[[str, tuple[CoreCommand, ...]], None],
) -> RecomputeLocked:
    async def wrapped(cid: str) -> None:
        try:
            await original(cid)
        finally:
            retire(cid, load_prepared(cid))

    return wrapped
