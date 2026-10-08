"""Durable place/cancel commands and signed CLOB identity."""

# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportMissingTypeStubs=false

import multiprocessing
import os
from contextlib import suppress
from pathlib import Path

from polymaker.domain import OpenOrder, Quote, Side
from py_clob_client_v2.config import get_contract_config
from py_clob_client_v2.order_builder.builder import ExchangeOrderBuilderV2, OrderDataV2
from py_clob_client_v2.order_utils.model.order_data_v2 import Side as ClobSide
from py_clob_client_v2.order_utils.model.signature_type_v2 import SignatureTypeV2
from py_clob_client_v2.signer import Signer

from trader.core_execution import (
    PreparedPlace,
    bind_place_results,
    exchange_address,
    hash_signed_order,
    mark_dispatch_started,
    paper_order_hash,
    persist_place_outcome,
    persist_prepared_places,
    persist_then_dispatch,
    retire_unsent,
)
from trader.core_persistence import list_commands
from trader.wallet_store import WalletStateStore

YES = "yes-token"
_TEST_KEY = "0x" + "11" * 32
_SALT = 123456789


def test_paper_hash_is_stable() -> None:
    quote = Quote(YES, Side.BUY, 0.50, 40.0)
    assert paper_order_hash(quote=quote, core_order_id="c0") == paper_order_hash(
        quote=quote, core_order_id="c0"
    )


def test_db_failure_before_dispatch_does_not_post(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    store._conn.close()
    posted = False

    def post() -> list[OpenOrder]:
        nonlocal posted
        posted = True
        return []

    with suppress(Exception):
        persist_then_dispatch(
            store._conn,
            session_id="s",
            revision=1,
            batch_id="b",
            places=(
                PreparedPlace(
                    core_order_id="c0",
                    quote=Quote(YES, Side.BUY, 0.50, 40.0),
                    order_hash="h",
                    signed=None,
                ),
            ),
            post=post,
        )
    assert posted is False


def test_prepared_crash_before_post_leaves_no_venue_order(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    persist_prepared_places(
        store._conn,
        session_id="s",
        revision=1,
        batch_id="b",
        places=(
            PreparedPlace(
                core_order_id="c0",
                quote=Quote(YES, Side.BUY, 0.50, 40.0),
                order_hash="hash-1",
                signed=None,
            ),
        ),
    )
    store._conn.commit()
    commands = list_commands(store._conn, "s")
    assert commands[0].dispatch_state == "prepared"
    assert commands[0].order_hash == "hash-1"
    store.close()


def test_load_shed_retires_only_prepared(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    persist_prepared_places(
        store._conn,
        session_id="s",
        revision=1,
        batch_id="b",
        places=(
            PreparedPlace(
                core_order_id="c0",
                quote=Quote(YES, Side.BUY, 0.50, 40.0),
                order_hash="h0",
                signed=None,
            ),
            PreparedPlace(
                core_order_id="c1",
                quote=Quote(YES, Side.BUY, 0.49, 40.0),
                order_hash="h1",
                signed=None,
            ),
        ),
    )
    commands = list_commands(store._conn, "s")
    mark_dispatch_started(store._conn, (commands[0],))
    retired = retire_unsent(store._conn, list_commands(store._conn, "s"))
    assert retired == ("c1",)
    leftover = {item.core_order_id: item for item in list_commands(store._conn, "s")}
    assert leftover["c0"].dispatch_state == "dispatch_started"
    assert leftover["c1"].dispatch_state == "known_not_sent"
    store.close()


def test_unknown_result_keeps_hash(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "w.db")
    persist_prepared_places(
        store._conn,
        session_id="s",
        revision=1,
        batch_id="b",
        places=(
            PreparedPlace(
                core_order_id="c0",
                quote=Quote(YES, Side.BUY, 0.50, 40.0),
                order_hash="lost-hash",
                signed=None,
            ),
        ),
    )
    command = list_commands(store._conn, "s")[0]
    persist_place_outcome(store._conn, command=command, venue_id=None, outcome="unknown")
    store._conn.commit()
    loaded = list_commands(store._conn, "s")[0]
    assert loaded.order_hash == "lost-hash"
    assert loaded.outcome == "unknown"
    store.close()


def test_partial_place_response_binds_by_quote() -> None:
    planned = (
        PreparedPlace("c0", Quote(YES, Side.BUY, 0.50, 40.0), "h0", None),
        PreparedPlace("c1", Quote(YES, Side.BUY, 0.49, 40.0), "h1", None),
    )
    placed = [OpenOrder("v-mid", YES, Side.BUY, 0.49, 40.0)]
    pairs = bind_place_results(planned=planned, placed=placed)
    assert pairs[0][1] is None
    assert pairs[1][1] is not None
    assert pairs[1][1].order_id == "v-mid"


def test_sdk_hash_matches_builder_and_selects_neg_risk() -> None:
    chain_id = 137
    signer = Signer(_TEST_KEY, chain_id)
    normal = exchange_address(chain_id=chain_id, neg_risk=False)
    risky = exchange_address(chain_id=chain_id, neg_risk=True)
    config = get_contract_config(chain_id)
    assert normal == config.exchange_v2
    assert risky == config.neg_risk_exchange_v2
    builder = ExchangeOrderBuilderV2(normal, chain_id, signer, generate_salt=lambda: _SALT)
    order = builder.build_order(
        OrderDataV2(
            maker=signer.address(),
            tokenId="1",
            makerAmount="500000",
            takerAmount="1000000",
            side=ClobSide.BUY,
            signer=signer.address(),
            signatureType=SignatureTypeV2.EOA,
            timestamp="1",
        )
    )
    digest = hash_signed_order(chain_id=chain_id, neg_risk=False, order=order)
    assert digest == builder.build_order_hash(builder.build_order_typed_data(order))
    assert digest.startswith("0x")
    assert len(digest) == 66


def _crash_after_post(db: str, flag: str) -> None:
    store = WalletStateStore(Path(db))

    def post() -> list[OpenOrder]:
        Path(flag).write_text("posted")
        os._exit(1)

    persist_then_dispatch(
        store._conn,
        session_id="s",
        revision=1,
        batch_id="b",
        places=(
            PreparedPlace(
                core_order_id="c0",
                quote=Quote(YES, Side.BUY, 0.50, 40.0),
                order_hash="venue-hash",
                signed=None,
            ),
        ),
        post=post,
    )


def test_subprocess_crash_keeps_persisted_hash(tmp_path: Path) -> None:
    db = tmp_path / "crash.db"
    flag = tmp_path / "posted"
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_crash_after_post, args=(str(db), str(flag)))
    child.start()
    child.join(timeout=15)
    assert child.exitcode not in (0, None)
    assert flag.read_text() == "posted"
    reopened = WalletStateStore(db)
    loaded = list_commands(reopened._conn, "s")[0]
    assert loaded.order_hash == "venue-hash"
    assert loaded.dispatch_state == "dispatch_started"
    reopened.close()
