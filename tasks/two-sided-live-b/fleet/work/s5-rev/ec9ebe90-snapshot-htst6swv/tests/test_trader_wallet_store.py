"""Durable fill ledger: index-free keys, MATCHED pending, FAILED rebuild."""

# pyright: reportPrivateUsage=false

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polymaker.domain import Fill, OpenOrder, Side, TradeState
from polymaker.state.store import StateStore
from polymaker.state.tracker import TradeEvent

from dashboard import summarize
from trader import engine_seams, wallet_store
from trader.chain_balances import ChainSnapshot
from trader.fill_parsing import (
    fill_key,
    normalize_maker_trades,
    split_fill_key,
    trade_time_or_receipt,
)
from trader.wallet_store import (
    WalletFillProcessor,
    WalletIdentity,
    WalletStateStore,
    share_qty_matches,
)

OUR_ADDRESS = "0xabc"
YES_TOKEN = "yes-token"
NO_TOKEN = "no-token"


def test_share_qty_matches_floors_to_the_same_tick() -> None:
    assert share_qty_matches(198.35, 198.3567)
    assert share_qty_matches(0.008926, 0.0)
    assert not share_qty_matches(3.18, 0.0)


def _other_token(token_id: str) -> str | None:
    if token_id == YES_TOKEN:
        return NO_TOKEN
    if token_id == NO_TOKEN:
        return YES_TOKEN
    return None


def _trade_payload(makers: list[dict[str, object]], status: str = "MATCHED") -> dict[str, object]:
    return {
        "id": "clob-trade-1",
        "status": status,
        "asset_id": YES_TOKEN,
        "side": "BUY",
        "outcome": "Yes",
        "maker_orders": makers,
        "timestamp": 1.0,
    }


def _our_maker(order_id: str) -> dict[str, object]:
    return {
        "maker_address": OUR_ADDRESS,
        "order_id": order_id,
        "matched_amount": "10",
        "price": "0.40",
        "outcome": "Yes",
    }


def _other_maker(order_id: str) -> dict[str, object]:
    return {
        "maker_address": "0xother",
        "order_id": order_id,
        "matched_amount": "3",
        "price": "0.40",
        "outcome": "Yes",
    }


SIGNER_ADDRESS = "0xsigner"
FUNDER_ADDRESS = "0x941aa5589961e33c54365a27a3223c916e6a24d9"


def test_safe_maker_row_matches_funder_not_signer() -> None:
    """Gnosis maker_address is the Safe. The signer EOA must not steal the fill."""
    ours = _our_maker("order-ours")
    ours["maker_address"] = FUNDER_ADDRESS
    payload = _trade_payload([ours])
    by_signer = normalize_maker_trades(payload, SIGNER_ADDRESS, _other_token)
    by_funder = normalize_maker_trades(payload, FUNDER_ADDRESS, _other_token)
    assert by_signer == []
    assert len(by_funder) == 1
    assert by_funder[0].size == 10.0


def test_empty_funder_pin_upgrades_to_the_connected_address(tmp_path: Path) -> None:
    """Pin before gateway.connect stores ''. Connect must be allowed to fill it in."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.pin_static(2, 137)
    store.pin_funder("0xabc")
    assert store.read_identity() == WalletIdentity("0xabc", 2, 137)
    store.close()


def test_funder_upgrade_still_refuses_a_chain_mismatch(tmp_path: Path) -> None:
    """Empty funder is not a free pass to change chain or signature type."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.pin_static(2, 137)
    with pytest.raises(RuntimeError, match="wallet identity"):
        store.pin_static(2, 1)
    store.close()


def test_pre_connect_empty_funder_keeps_the_stored_address(tmp_path: Path) -> None:
    """Restart pins before connect; an empty funder must not clash with the stored Safe."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.pin_static(2, 137)
    store.pin_funder("0xabc")
    store.pin_static(2, 137)
    assert store.read_identity() == WalletIdentity("0xabc", 2, 137)
    store.close()


def test_maker_order_permutation_does_not_duplicate_the_fill_key() -> None:
    """WS and REST must key by trade id + our order id, never maker_orders index."""
    ours = _our_maker("order-ours")
    first = normalize_maker_trades(
        _trade_payload([_other_maker("x"), ours, _other_maker("y")]),
        OUR_ADDRESS,
        _other_token,
    )
    second = normalize_maker_trades(
        _trade_payload([ours, _other_maker("y"), _other_maker("x")]),
        OUR_ADDRESS,
        _other_token,
    )
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].trade_id == second[0].trade_id
    assert first[0].trade_id == fill_key("clob-trade-1", "order-ours")


def test_matched_is_not_irreversible(tmp_path: Path) -> None:
    """FAILED after MATCHED unwinds size and cash. Core sell-only is not involved."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 1.0),
        "cid-1",
    )
    assert store.position(YES_TOKEN).size == 10.0
    assert store.ledger_net_cash() == -4.0
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.FAILED, 2.0),
        "cid-1",
    )
    assert store.position(YES_TOKEN).size == 0.0
    assert store.position(YES_TOKEN).avg_price == 0.0
    assert store.ledger_net_cash() == 0.0
    store.close()


def test_matched_then_new_store_failed_restores_avg_price(tmp_path: Path) -> None:
    """MATCHED is pending on disk; FAILED with no remaining fills zeros the token."""
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    matched = TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 1.0)
    processor.on_trade(matched, "cid-1")
    pos = store.position(YES_TOKEN)
    assert pos.size == 10.0
    assert pos.avg_price == 0.40
    assert store.ledger_net_cash() == -4.0
    store.close()

    restarted = WalletStateStore(db_path)
    replay = WalletFillProcessor(restarted)
    failed = TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.FAILED, 2.0)
    replay.on_trade(failed, "cid-1")
    restored = restarted.position(YES_TOKEN)
    assert restored.size == 0.0
    assert restored.avg_price == 0.0
    assert restarted.ledger_net_cash() == 0.0
    restarted.close()


def test_confirmed_without_matched_is_one_transition(tmp_path: Path) -> None:
    """A CONFIRMED with no WS MATCHED applies the fill once."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    fills: list[Fill] = []
    processor._on_fill = fills.append
    key = fill_key("clob-trade-1", "order-ours")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.CONFIRMED, 1.0),
        "cid-1",
    )
    assert len(fills) == 1
    assert store.position(YES_TOKEN).size == 10.0
    assert store.ledger_net_cash() == -4.0
    store.close()


def test_matched_then_confirmed_does_not_double_cash(tmp_path: Path) -> None:
    """MATCHED already in the ledger keeps net_cash when CONFIRMED arrives."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    event = TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 1.0)
    processor.on_trade(event, "cid-1")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.CONFIRMED, 2.0),
        "cid-1",
    )
    assert store.ledger_net_cash() == -4.0
    assert store.position(YES_TOKEN).size == 10.0
    store.close()


def test_outbox_survives_restart_until_ack(tmp_path: Path) -> None:
    """Unacked outbox rows replay after a new store opens the same file."""
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 1.0),
        "cid-1",
    )
    pending = store.pending_outbox()
    assert len(pending) == 1
    assert pending[0].fill_key == key
    store.close()

    restarted = WalletStateStore(db_path)
    replayed = restarted.pending_outbox()
    assert len(replayed) == 1
    restarted.ack_outbox(replayed[0].seq)
    assert restarted.pending_outbox() == []
    restarted.close()


def test_legacy_positions_without_ledger_fail_loud(tmp_path: Path) -> None:
    """Old per-match paper_state.db files are not merged into the wallet ledger."""
    db_path = tmp_path / "paper_state.db"
    legacy = StateStore(db_path)
    legacy.set_position(YES_TOKEN, 5.0, 0.5)
    legacy.close()
    try:
        WalletStateStore(db_path)
    except RuntimeError as exc:
        assert "cannot be merged" in str(exc)
    else:
        raise AssertionError("legacy positions must fail loud")


def test_zero_token_sizes_writes_size_zero_without_payout(tmp_path: Path) -> None:
    """Steam-final leftover drop is size 0 / avg 0 and adds no fill_ledger row."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 12.0, 0.4)
    store.set_position(NO_TOKEN, 3.0, 0.6)
    before = store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()
    store.zero_token_sizes({YES_TOKEN, NO_TOKEN})
    assert store.position(YES_TOKEN).size == 0.0
    assert store.position(YES_TOKEN).avg_price == 0.0
    assert store.position(NO_TOKEN).size == 0.0
    after = store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()
    assert int(after["n"]) == int(before["n"]) == 0
    store.close()


def test_drop_untracked_positions_is_noop(tmp_path: Path) -> None:
    """Boot with empty _token_cid must not wipe persisted sizes."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 8.0, 0.5)
    dropped = store.drop_untracked_positions(set())
    assert dropped == []
    assert store.position(YES_TOKEN).size == 8.0
    store.close()


def test_has_unacked_matched_for_token(tmp_path: Path) -> None:
    """Fence waits on unacked MATCHED rows for the market's tokens only."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 1.0),
        "cid-1",
    )
    assert store.has_unacked_matched({YES_TOKEN}) is True
    assert store.has_unacked_matched({NO_TOKEN}) is False
    store.ack_outbox(store.pending_outbox()[0].seq)
    assert store.has_unacked_matched({YES_TOKEN}) is False
    store.close()


def test_interleaved_failed_rebuilds_from_remaining_fills(tmp_path: Path) -> None:
    """FAILED A after MATCHED A then MATCHED B leaves B's position, not A's pre-image."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key_a = fill_key("clob-a", "order-a")
    key_b = fill_key("clob-b", "order-b")
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key_a, TradeState.MATCHED, 1.0),
        "cid-1",
    )
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.50, 5.0, key_b, TradeState.MATCHED, 2.0),
        "cid-1",
    )
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key_a, TradeState.FAILED, 3.0),
        "cid-1",
    )
    pos = store.position(YES_TOKEN)
    assert pos.size == 5.0
    assert pos.avg_price == 0.50
    assert store.running_net_cash == -2.5
    assert store.ledger_net_cash() == -2.5
    store.close()


def test_mint_without_other_token_skips_the_maker_row() -> None:
    """Mint branch with no complementary token must not substitute the taker asset."""
    payload = _trade_payload(
        [
            {
                "maker_address": OUR_ADDRESS,
                "order_id": "order-ours",
                "matched_amount": "10",
                "price": "0.40",
                "outcome": "No",
            }
        ]
    )
    events = normalize_maker_trades(payload, OUR_ADDRESS, lambda _token: None)
    assert events == []


def test_duplicate_maker_key_skips_the_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two maker_orders collapsing onto one fill_key must not silently drop the second size."""
    alerts: list[str] = []
    monkeypatch.setattr(
        "trader.fill_parsing.notify_in_background",
        alerts.append,
    )
    first = _our_maker("order-ours")
    second = dict(first)
    second["matched_amount"] = "7"
    events = normalize_maker_trades(
        _trade_payload([first, second]),
        OUR_ADDRESS,
        _other_token,
    )
    assert events == []
    assert alerts == ["trader duplicate maker fill key"]


def test_failed_and_confirmed_key_positions_by_ledger_token(tmp_path: Path) -> None:
    """CONFIRMED/FAILED must write and clear inflight under the ledger token, not the event."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    processor.on_trade(
        TradeEvent(NO_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 1.0),
        "cid-1",
    )
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.CONFIRMED, 2.0),
        "cid-1",
    )
    assert store.position(NO_TOKEN).size == 10.0
    assert store.position(YES_TOKEN).size == 0.0
    assert store.inflight(NO_TOKEN) == 0
    key_fail = fill_key("clob-trade-2", "order-ours")
    processor.on_trade(
        TradeEvent(NO_TOKEN, Side.BUY, 0.30, 4.0, key_fail, TradeState.MATCHED, 3.0),
        "cid-1",
    )
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.30, 4.0, key_fail, TradeState.FAILED, 4.0),
        "cid-1",
    )
    assert store.position(NO_TOKEN).size == 10.0
    assert store.position(YES_TOKEN).size == 0.0
    store.close()


def test_oversized_sell_halts_and_does_not_write_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SELL bigger than the held size must not clamp cash and position into a wrong pair."""
    alerts: list[str] = []
    halted: list[str] = []
    monkeypatch.setattr("trader.wallet_store.notify_in_background", alerts.append)
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 5.0, 0.40)
    store.set_oversized_sell_handler(halted.append)
    fill = Fill(YES_TOKEN, Side.SELL, 0.50, 10.0, "clob:order", 1.0, is_maker=True)
    assert store.apply_matched_fill(fill, "clob:order") is False
    assert store.position(YES_TOKEN).size == 5.0
    assert store.running_net_cash == 0.0
    assert store.ledger_net_cash() == 0.0
    count = store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()
    assert int(count["n"]) == 0
    assert halted == [YES_TOKEN]
    assert alerts == ["trader oversized sell"]
    store.close()


def test_split_sell_remainder_does_not_halt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """44.12 then 40 + 4.12 is a full exit; float leftover must not look oversized."""
    alerts: list[str] = []
    halted: list[str] = []
    monkeypatch.setattr("trader.wallet_store.notify_in_background", alerts.append)
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_oversized_sell_handler(halted.append)
    buy = Fill(YES_TOKEN, Side.BUY, 0.68, 44.12, "clob:buy", 1.0, is_maker=True)
    sell_bulk = Fill(YES_TOKEN, Side.SELL, 0.68, 40.0, "clob:sell-40", 2.0, is_maker=True)
    sell_rest = Fill(YES_TOKEN, Side.SELL, 0.68, 4.12, "clob:sell-412", 3.0, is_maker=True)
    assert store.apply_matched_fill(buy, "clob:buy") is True
    assert store.apply_matched_fill(sell_bulk, "clob:sell-40") is True
    assert store.position(YES_TOKEN).size == pytest.approx(4.12)
    assert store.apply_matched_fill(sell_rest, "clob:sell-412") is True
    assert store.position(YES_TOKEN).size == 0.0
    assert store.ledger_net_cash() == pytest.approx(0.0)
    assert halted == []
    assert alerts == []
    store.close()


def test_sell_one_tick_above_held_still_halts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SELL past the half-tick tolerance is still oversized."""
    alerts: list[str] = []
    halted: list[str] = []
    monkeypatch.setattr("trader.wallet_store.notify_in_background", alerts.append)
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 4.12, 0.68)
    store.set_oversized_sell_handler(halted.append)
    fill = Fill(YES_TOKEN, Side.SELL, 0.68, 4.13, "clob:over", 1.0, is_maker=True)
    assert store.apply_matched_fill(fill, "clob:over") is False
    assert store.position(YES_TOKEN).size == pytest.approx(4.12)
    assert store.ledger_net_cash() == 0.0
    assert halted == [YES_TOKEN]
    store.close()


def test_split_fill_key_uses_rpartition() -> None:
    """Informational columns split on the last colon; the fill_key PK is unchanged."""
    clob_trade_id, maker_order_id = split_fill_key("paper-ns-1:extra:fill")
    assert clob_trade_id == "paper-ns-1:extra"
    assert maker_order_id == "fill"


def test_rest_size_up_is_ignored(tmp_path: Path) -> None:
    """Lagged REST extra size must not become sqlite inventory."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.reconcile_positions({YES_TOKEN: (28.99, 0.69)})
    assert store.position(YES_TOKEN).size == 0.0
    assert YES_TOKEN not in store._last_fill_ts
    store.close()


def test_rest_size_down_is_ignored(tmp_path: Path) -> None:
    """A REST shrink larger than half a share tick is not inventory. Chain does that."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 13.0, 0.60)
    store.reconcile_positions({YES_TOKEN: (8.09, 0.60)})
    assert store.position(YES_TOKEN).size == pytest.approx(13.0)
    assert store.fill_precedes_writedown(YES_TOKEN, time.time()) is False
    store.close()


def test_rest_half_tick_down_still_applies(tmp_path: Path) -> None:
    """Rounding inside half a share tick is still written."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 13.0, 0.60)
    store.reconcile_positions({YES_TOKEN: (12.996, 0.60)})
    assert store.position(YES_TOKEN).size == pytest.approx(12.996)
    store.close()


def test_rest_size_down_inside_tolerance_is_ignored(tmp_path: Path) -> None:
    """Two percent is still too much for REST to take off sqlite."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 100.0, 0.50)
    store.reconcile_positions({YES_TOKEN: (99.0, 0.50)})
    assert store.position(YES_TOKEN).size == pytest.approx(100.0)
    store.close()


def test_rest_size_down_skips_while_live_sell(tmp_path: Path) -> None:
    """A live SELL fill that REST already sees must wait for MATCHED, not size-down."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 13.0, 0.60)
    store.upsert_order(OpenOrder("oid-s", YES_TOKEN, Side.SELL, 0.58, 13.0))
    store.reconcile_positions({YES_TOKEN: (8.09, 0.60)})
    assert store.position(YES_TOKEN).size == pytest.approx(13.0)
    store.close()


def test_chain_floor_moves_on_confirm_backfill_and_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MATCHED does not move the floor. CONFIRMED, backfill, and FAILED do."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(wallet_store.time, "time", lambda: clock["now"])
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    assert store.chain_read_floor(YES_TOKEN) == 1_000.0
    clock["now"] = 1_100.0
    matched = Fill(YES_TOKEN, Side.BUY, 0.40, 10.0, "clob:buy", 1_050.0, is_maker=True)
    assert store.apply_matched_fill(matched, "clob:buy") is True
    assert store.chain_read_floor(YES_TOKEN) == 1_000.0
    assert store.apply_confirmed_fill(matched, "clob:buy").confirmed is True
    assert store.chain_read_floor(YES_TOKEN) == 1_100.0
    clock["now"] = 1_200.0
    backfill = Fill(NO_TOKEN, Side.BUY, 0.40, 4.0, "clob:back", 1_150.0, is_maker=True)
    assert store.apply_backfilled_fill(backfill, "clob:back") is True
    assert store.chain_read_floor(NO_TOKEN) == 1_200.0
    clock["now"] = 1_300.0
    failing = Fill(YES_TOKEN, Side.BUY, 0.40, 2.0, "clob:fail", 1_250.0, is_maker=True)
    assert store.apply_matched_fill(failing, "clob:fail") is True
    assert store.apply_failed_fill(failing, "clob:fail") is True
    assert store.chain_read_floor(YES_TOKEN) == 1_300.0
    store.close()
    clock["now"] = 2_000.0
    restarted = WalletStateStore(db_path)
    assert restarted._settled_wall == {}
    assert restarted.chain_read_floor(YES_TOKEN) == restarted._boot_wall
    assert restarted.chain_read_floor(YES_TOKEN) == 2_000.0
    restarted.close()


def test_restore_ledger_position_clears_the_write_down(tmp_path: Path) -> None:
    """A restore puts the ledger size back and drops the watermark, freeze, and buy block."""
    store = WalletStateStore(tmp_path / "wallet.db")
    fill = Fill(YES_TOKEN, Side.BUY, 0.55, 10.0, "clob:buy", 50.0, is_maker=True)
    assert store.apply_matched_fill(fill, "clob:buy") is True
    assert store.apply_confirmed_fill(fill, "clob:buy").confirmed is True
    store.force_set_position(YES_TOKEN, 0.0, 0.55, source="onchain")
    store.note_writedown_snapshot(YES_TOKEN, 80.0)
    store.freeze_sell(YES_TOKEN, 30.0)
    store.block_buy(YES_TOKEN)
    block_ts = store.chain_read_floor(YES_TOKEN) + 10.0
    store.restore_ledger_position(YES_TOKEN, block_ts)
    assert store.position(YES_TOKEN).size == pytest.approx(10.0)
    assert store.chain_read_floor(YES_TOKEN) == pytest.approx(block_ts)
    assert store.fill_precedes_writedown(YES_TOKEN, 79.0) is False
    assert store.is_sell_frozen(YES_TOKEN) is False
    assert store.is_buy_blocked(YES_TOKEN) is False
    store.close()


def test_rest_size_up_leaves_no_write_down_watermark(tmp_path: Path) -> None:
    """An ignored size-up dates nothing, so a later fill still applies."""
    store = WalletStateStore(tmp_path / "wallet.db")
    sent_at = time.time()
    store.note_rest_positions_sent(sent_at)
    store.reconcile_positions({YES_TOKEN: (28.99, 0.69)})
    assert store.fill_precedes_writedown(YES_TOKEN, sent_at - 5.0) is False
    store.close()


def test_buy_block_flags_one_token(tmp_path: Path) -> None:
    """The on-chain excess block is per token and clears on request."""
    store = WalletStateStore(tmp_path / "wallet.db")
    assert store.is_buy_blocked(YES_TOKEN) is False
    store.block_buy(YES_TOKEN)
    assert store.is_buy_blocked(YES_TOKEN) is True
    assert store.is_buy_blocked(NO_TOKEN) is False
    store.unblock_buy(YES_TOKEN)
    assert store.is_buy_blocked(YES_TOKEN) is False
    store.close()


def test_sell_fill_stamps_settle(tmp_path: Path) -> None:
    """A SELL fill starts the 10s divergence skip, including after restart rebuild."""
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    store.set_position(YES_TOKEN, 10.0, 0.40)
    now = time.time()
    fill = Fill(YES_TOKEN, Side.SELL, 0.50, 5.0, "clob:sell", now, is_maker=True)
    assert store.apply_matched_fill(fill, "clob:sell") is True
    assert store._last_fill_ts[YES_TOKEN] == now
    assert store.is_settling(YES_TOKEN, now) is True
    store.close()
    restarted = WalletStateStore(db_path)
    assert restarted.is_settling(YES_TOKEN, now) is True
    restarted.close()


def test_fill_clears_sell_freeze(tmp_path: Path) -> None:
    """A later fill on the frozen token unblocks SELL."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.freeze_sell(YES_TOKEN, 12.0)
    fill = Fill(YES_TOKEN, Side.BUY, 0.40, 10.0, "clob:buy", time.time(), is_maker=True)
    assert store.apply_matched_fill(fill, "clob:buy") is True
    assert store.is_sell_frozen(YES_TOKEN) is False
    store.close()


def test_sell_freeze_expires_without_a_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SELL freeze releases on its own once its window ends."""
    clock = 100.0
    monkeypatch.setattr(wallet_store.time, "monotonic", lambda: clock)
    store = WalletStateStore(tmp_path / "wallet.db")
    store.freeze_sell(YES_TOKEN, 12.0)
    assert store.is_sell_frozen(YES_TOKEN) is True
    clock = 111.9
    assert store.is_sell_frozen(YES_TOKEN) is True
    clock = 112.1
    assert store.is_sell_frozen(YES_TOKEN) is False
    store.close()


def test_reopen_restores_last_fill_timestamp(tmp_path: Path) -> None:
    """A new store instance reloads the last fill timestamp from fill_ledger."""
    db_path = tmp_path / "wallet.db"
    store = WalletStateStore(db_path)
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    fill_ts = 1_700_000_123.5
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.CONFIRMED, fill_ts),
        "cid-1",
    )
    assert store._last_fill_ts[YES_TOKEN] == fill_ts
    store.close()
    restarted = WalletStateStore(db_path)
    assert restarted._last_fill_ts[YES_TOKEN] == fill_ts
    restarted.close()


def test_trade_time_or_receipt_keeps_a_real_trade_time() -> None:
    """A plausible exchange stamp is used as-is, including a replay from hours ago."""
    now = 1_800_000_000.0
    assert trade_time_or_receipt(now - 3.0, now) == pytest.approx(now - 3.0)
    assert trade_time_or_receipt(now - 7200.0, now) == pytest.approx(now - 7200.0)


def test_trade_time_or_receipt_falls_back_on_a_missing_stamp() -> None:
    """The user stream reports 0.0 with no timestamp; that must not read as 1970."""
    now = 1_800_000_000.0
    assert trade_time_or_receipt(0.0, now) == pytest.approx(now)
    assert trade_time_or_receipt(-1.0, now) == pytest.approx(now)
    assert trade_time_or_receipt(1.0, now) == pytest.approx(now)


def test_trade_time_or_receipt_falls_back_on_a_far_future_stamp() -> None:
    """A stamp well ahead of the receipt clock is a unit bug, not a trade time."""
    now = 1_800_000_000.0
    assert trade_time_or_receipt(now + 600.0, now) == pytest.approx(now)


def test_missing_trade_stamp_still_holds_the_exit_settle(tmp_path: Path) -> None:
    """A BUY with no usable stamp keeps the full 10s settle instead of skipping it."""
    store = WalletStateStore(tmp_path / "wallet.db")
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", "order-ours")
    matched = TradeEvent(YES_TOKEN, Side.BUY, 0.40, 10.0, key, TradeState.MATCHED, 0.0)
    processor.on_trade(matched, "cid-1")
    assert store.is_settling(YES_TOKEN, time.time())
    store.close()


def test_apply_backfilled_fill_is_idempotent_on_key(tmp_path: Path) -> None:
    """A second apply of the same fill_key does not insert or credit cash again."""
    store = WalletStateStore(tmp_path / "wallet.db")
    key = fill_key("clob-trade-1", "order-ours")
    fill = Fill(YES_TOKEN, Side.SELL, 0.57, 17.86, key, 1_800_000_000.0, is_maker=True)
    assert store.apply_backfilled_fill(fill, key) is True
    assert store.apply_backfilled_fill(fill, key) is False
    assert store.ledger_net_cash() == pytest.approx(0.57 * 17.86)
    rows = store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()
    assert int(rows["n"]) == 1
    store.close()


def test_apply_backfilled_fill_credits_cash_after_write_down(tmp_path: Path) -> None:
    """A SELL recovered after REST wrote size 0 still pays out and does not raise."""
    store = WalletStateStore(tmp_path / "wallet.db")
    halted: list[str] = []
    store.set_oversized_sell_handler(halted.append)
    store.set_position(YES_TOKEN, 0.0, 0.0)
    store.note_writedown_snapshot(YES_TOKEN, time.time())
    key = fill_key("clob-trade-1", "order-ours")
    fill = Fill(YES_TOKEN, Side.SELL, 0.57, 17.86, key, time.time() - 60.0, is_maker=True)
    assert store.apply_backfilled_fill(fill, key) is True
    assert store.position(YES_TOKEN).size == 0.0
    assert store.ledger_net_cash() == pytest.approx(0.57 * 17.86)
    assert halted == []
    status = store._conn.execute(
        "SELECT status FROM fill_ledger WHERE fill_key=?", (key,)
    ).fetchone()
    assert status["status"] == TradeState.CONFIRMED.value
    outbox = store._conn.execute(
        "SELECT event FROM fill_outbox WHERE fill_key=?", (key,)
    ).fetchone()
    assert outbox["event"] == "confirmed"
    store.close()


def test_apply_backfilled_fill_skips_a_superseded_key(tmp_path: Path) -> None:
    """A key already recorded SUPERSEDED is not inserted a second time."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 13.0, 0.60)
    sent_at = time.time()
    store.note_writedown_snapshot(YES_TOKEN, sent_at)
    key = "trade-late"
    late = Fill(YES_TOKEN, Side.SELL, 0.58, 4.91, key, sent_at - 5.0, is_maker=True)
    assert store.apply_matched_fill(late, key) is False
    cash_before = store.ledger_net_cash()
    assert store.apply_backfilled_fill(late, key) is False
    assert store.ledger_net_cash() == cash_before
    rows = store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()
    assert int(rows["n"]) == 1
    store.close()


MERGE_TX = "0xmerge1"


def _open_store_with_pair(db_path: Path) -> WalletStateStore:
    store = WalletStateStore(db_path)
    yes_key = fill_key("clob-y", "order-y")
    no_key = fill_key("clob-n", "order-n")
    yes_buy = Fill(YES_TOKEN, Side.BUY, 0.40, 30.0, yes_key, 10.0, is_maker=True)
    no_buy = Fill(NO_TOKEN, Side.BUY, 0.55, 20.0, no_key, 11.0, is_maker=True)
    assert store.apply_confirmed_fill(yes_buy, yes_key).applied_new is True
    assert store.apply_confirmed_fill(no_buy, no_key).applied_new is True
    return store


def _merge_key(token_id: str) -> str:
    return f"merge:{MERGE_TX}:{token_id}"


def test_apply_merge_moves_sizes_and_cash_once(tmp_path: Path) -> None:
    store = _open_store_with_pair(tmp_path / "wallet.db")
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True
    yes = store.position(YES_TOKEN)
    no = store.position(NO_TOKEN)
    assert (yes.size, yes.avg_price) == (pytest.approx(10.0), pytest.approx(0.40))
    assert (no.size, no.avg_price) == (pytest.approx(0.0), pytest.approx(0.0))
    ledger_yes = store.ledger_position(YES_TOKEN)
    ledger_no = store.ledger_position(NO_TOKEN)
    assert (ledger_yes.size, ledger_yes.avg_price) == (pytest.approx(10.0), pytest.approx(0.40))
    assert (ledger_no.size, ledger_no.avg_price) == (pytest.approx(0.0), pytest.approx(0.0))
    assert store.running_net_cash == pytest.approx(-3.0)
    assert store.ledger_net_cash() == pytest.approx(-3.0)
    assert store.ledger_net_cash_for_tokens({YES_TOKEN}) == pytest.approx(-2.0)
    assert store.ledger_net_cash_for_tokens({NO_TOKEN}) == pytest.approx(-1.0)
    row = store._conn.execute(
        "SELECT side, price, size, status, cash_delta, pre_size, pre_avg, is_maker,"
        " clob_trade_id, maker_order_id FROM fill_ledger WHERE fill_key=?",
        (_merge_key(YES_TOKEN),),
    ).fetchone()
    assert (
        row["side"],
        row["price"],
        row["size"],
        row["status"],
        row["cash_delta"],
        row["pre_size"],
        row["pre_avg"],
        row["is_maker"],
        row["clob_trade_id"],
        row["maker_order_id"],
    ) == ("MERGE", 0.5, 20.0, "MERGED", 10.0, 30.0, 0.40, 0, MERGE_TX, "")
    outbox = store._conn.execute(
        "SELECT event, acked FROM fill_outbox WHERE fill_key LIKE 'merge:%' ORDER BY seq"
    ).fetchall()
    assert [(item["event"], item["acked"]) for item in outbox] == [("merged", 1), ("merged", 1)]
    ledger_n = int(store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()["n"])
    outbox_n = int(store._conn.execute("SELECT COUNT(*) AS n FROM fill_outbox").fetchone()["n"])
    cash = store.running_net_cash
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is False
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(NO_TOKEN, YES_TOKEN), qty=20.0) is False
    ledger_after = store._conn.execute("SELECT COUNT(*) AS n FROM fill_ledger").fetchone()
    outbox_after = store._conn.execute("SELECT COUNT(*) AS n FROM fill_outbox").fetchone()
    assert int(ledger_after["n"]) == ledger_n
    assert int(outbox_after["n"]) == outbox_n
    yes_again = store.position(YES_TOKEN)
    no_again = store.position(NO_TOKEN)
    assert yes_again.size == pytest.approx(yes.size)
    assert yes_again.avg_price == pytest.approx(yes.avg_price)
    assert no_again.size == pytest.approx(no.size)
    assert no_again.avg_price == pytest.approx(no.avg_price)
    assert store.running_net_cash == cash
    store.close()


def test_merge_rows_stay_out_of_fill_readers(tmp_path: Path) -> None:
    store = _open_store_with_pair(tmp_path / "wallet.db")
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True
    assert store.fill_for_key(_merge_key(YES_TOKEN)) is None
    assert store.ledger_status(_merge_key(YES_TOKEN)) == "MERGED"
    assert store.matched_keys_for_tokens({YES_TOKEN, NO_TOKEN}) == ()
    assert store.has_unacked_matched({YES_TOKEN, NO_TOKEN}) is False
    assert [item.event for item in store.pending_outbox()] == ["confirmed", "confirmed"]
    tail = store.core_outbox_after(after_seq=0, tokens=frozenset({YES_TOKEN, NO_TOKEN}))[-2:]
    assert [item.event for item in tail] == ["merged", "merged"]
    assert [item.fill_key for item in tail] == [_merge_key(YES_TOKEN), _merge_key(NO_TOKEN)]
    store.close()


def test_reopen_restores_positions_and_cash_after_merge(tmp_path: Path) -> None:
    db_path = tmp_path / "wallet.db"
    store = _open_store_with_pair(db_path)
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True
    store.close()
    reopened = WalletStateStore(db_path)
    assert reopened.position(YES_TOKEN).size == pytest.approx(10.0)
    assert reopened.position(NO_TOKEN).size == pytest.approx(0.0)
    assert reopened.running_net_cash == pytest.approx(-3.0)
    assert reopened.inflight(YES_TOKEN) == 0
    assert reopened._last_fill_ts[YES_TOKEN] == 10.0
    reopened.close()


def test_failed_fill_after_merge_replays_the_merge(tmp_path: Path) -> None:
    store = WalletStateStore(tmp_path / "wallet.db")
    yes_key = fill_key("clob-y", "order-y")
    no_key = fill_key("clob-n", "order-n")
    late_key = fill_key("clob-late", "order-late")
    yes_buy = Fill(YES_TOKEN, Side.BUY, 0.40, 20.0, yes_key, 10.0, is_maker=True)
    late_buy = Fill(YES_TOKEN, Side.BUY, 0.45, 10.0, late_key, 12.0, is_maker=True)
    no_buy = Fill(NO_TOKEN, Side.BUY, 0.55, 20.0, no_key, 11.0, is_maker=True)
    assert store.apply_confirmed_fill(yes_buy, yes_key).applied_new is True
    assert store.apply_matched_fill(late_buy, late_key) is True
    assert store.apply_confirmed_fill(no_buy, no_key).applied_new is True
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True
    assert store.apply_failed_fill(late_buy, late_key) is True
    assert store.position(YES_TOKEN).size == pytest.approx(0.0)
    assert store.running_net_cash == pytest.approx(1.0)
    assert store.ledger_net_cash() == pytest.approx(1.0)
    store.close()


def test_merge_moves_the_chain_floor_for_both_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(wallet_store.time, "time", lambda: clock["now"])
    store = _open_store_with_pair(tmp_path / "wallet.db")
    clock["now"] = 1_100.0
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True
    assert store.chain_read_floor(YES_TOKEN) == 1_100.0
    assert store.chain_read_floor(NO_TOKEN) == 1_100.0
    store.close()


def test_summarize_wallet_counts_merge_cash_not_fill_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "wallet.db"
    store = _open_store_with_pair(db_path)
    assert store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=20.0) is True
    store.close()
    monkeypatch.setattr(summarize, "LIVE_WALLET_CANDIDATES", (db_path,))
    summarize.cmd_wallet()
    output = capsys.readouterr().out
    assert "ledger_net_cash=-3.0000" in output
    assert "fill_rows=2" in output
    assert "nonzero_positions=1" in output


def test_held_merge_blocks_chain_write_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(wallet_store.time, "time", lambda: clock["now"])

    def _ignore(_message: str) -> None:
        return None

    monkeypatch.setattr(engine_seams, "notify_in_background", _ignore)
    store = WalletStateStore(tmp_path / "wallet.db")
    yes_key = fill_key("clob-y", "order-y")
    no_key = fill_key("clob-n", "order-n")
    yes_buy = Fill(YES_TOKEN, Side.BUY, 0.40, 200.0, yes_key, 1_000.0, is_maker=True)
    no_buy = Fill(NO_TOKEN, Side.BUY, 0.55, 150.0, no_key, 1_000.0, is_maker=True)
    assert store.apply_confirmed_fill(yes_buy, yes_key).applied_new is True
    assert store.apply_confirmed_fill(no_buy, no_key).applied_new is True
    store.hold_merge(token_ids=(YES_TOKEN, NO_TOKEN))
    clock["now"] = 1_100.0
    snapshot = ChainSnapshot(123, 1_100.0, {YES_TOKEN: 50.0, NO_TOKEN: 0.0})

    def _alert(*_args: object, **_kwargs: object) -> None:
        return None

    engine = SimpleNamespace(alerter=SimpleNamespace(alert=_alert), _token_cid={})
    for token_id in (YES_TOKEN, NO_TOKEN):
        engine_seams._apply_chain_balance(
            cast(Any, engine),
            store,
            {},
            token_id,
            snapshot=snapshot,
            sent_at=1_100.0,
            now=1_100.0,
        )
    assert store.position(YES_TOKEN).size == pytest.approx(200.0)
    assert store.position(NO_TOKEN).size == pytest.approx(150.0)
    clock["now"] = 1_101.0
    applied = store.apply_merge(tx_hash=MERGE_TX, token_ids=(YES_TOKEN, NO_TOKEN), qty=150.0)
    assert applied is True
    assert store.position(YES_TOKEN).size == pytest.approx(50.0)
    assert store.ledger_position(YES_TOKEN).size == pytest.approx(50.0)
    assert store.position(NO_TOKEN).size == pytest.approx(0.0)
    assert store.ledger_position(NO_TOKEN).size == pytest.approx(0.0)
    store.release_merge(token_ids=(YES_TOKEN, NO_TOKEN))
    assert store.merge_is_held(YES_TOKEN) is False
    assert store.merge_is_held(NO_TOKEN) is False
    store.close()
