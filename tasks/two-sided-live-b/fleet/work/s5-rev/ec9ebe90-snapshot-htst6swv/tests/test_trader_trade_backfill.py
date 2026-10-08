"""REST /data/trades backfill shares the WS fill_key and confirms MATCHED once."""

# pyright: reportPrivateUsage=false

from pathlib import Path

import pytest
from polymaker.domain import Side, TradeState
from polymaker.state.tracker import TradeEvent

from trader.fill_parsing import fill_key, normalize_maker_trades
from trader.trade_backfill import _rest_payload, backfill_trades
from trader.wallet_store import WalletFillProcessor, WalletStateStore

OUR_ADDRESS = "0x941aa5589961e33c54365a27a3223c916e6a24d9"
YES_TOKEN = "yes-token"
NO_TOKEN = "no-token"
ORDER_ID = "0xorder"
SELL_CASH = 0.57 * 17.86


def _other_token(token_id: str) -> str | None:
    if token_id == YES_TOKEN:
        return NO_TOKEN
    if token_id == NO_TOKEN:
        return YES_TOKEN
    return None


def _ws_payload() -> dict[str, object]:
    return {
        "id": "clob-trade-1",
        "status": "CONFIRMED",
        "asset_id": YES_TOKEN,
        "side": "BUY",
        "outcome": "Yes",
        "timestamp": 1_800_000_000,
        "maker_orders": [
            {
                "maker_address": OUR_ADDRESS,
                "order_id": ORDER_ID,
                "matched_amount": "17.86",
                "price": "0.57",
                "outcome": "Yes",
            }
        ],
    }


def _clob_row() -> dict[str, object]:
    """CLOB GET /data/trades shape: match_time instead of timestamp."""
    return {
        "id": "clob-trade-1",
        "status": "CONFIRMED",
        "asset_id": YES_TOKEN,
        "side": "BUY",
        "outcome": "Yes",
        "match_time": "1800000000",
        "maker_orders": [
            {
                "maker_address": OUR_ADDRESS,
                "order_id": ORDER_ID,
                "matched_amount": "17.86",
                "price": "0.57",
                "outcome": "Yes",
            }
        ],
    }


def test_rest_trade_shares_the_ws_fill_key() -> None:
    """REST backfill and the user-WS parser must produce the same key."""
    ws = normalize_maker_trades(_ws_payload(), OUR_ADDRESS, _other_token)
    rest = normalize_maker_trades(_rest_payload(_clob_row()), OUR_ADDRESS, _other_token)
    assert len(ws) == 1
    assert len(rest) == 1
    assert ws[0].trade_id == rest[0].trade_id
    assert ws[0].trade_id == fill_key("clob-trade-1", ORDER_ID)


def test_backfill_trades_confirms_matched_without_double_cash(tmp_path: Path) -> None:
    """A MATCHED ledger row becomes CONFIRMED; cash is not credited again."""
    store = WalletStateStore(tmp_path / "wallet.db")
    store.set_position(YES_TOKEN, 17.86, 0.57)
    processor = WalletFillProcessor(store)
    key = fill_key("clob-trade-1", ORDER_ID)
    processor.on_trade(
        TradeEvent(YES_TOKEN, Side.SELL, 0.57, 17.86, key, TradeState.MATCHED, 1.0),
        "cid-1",
    )
    cash_before = store.ledger_net_cash()
    result = backfill_trades(store, [_clob_row()], OUR_ADDRESS, _other_token)
    assert result.applied == 0
    assert result.confirmed == 1
    assert result.cash == 0.0
    assert result.keys == (key,)
    assert store.ledger_net_cash() == cash_before
    status = store._conn.execute(
        "SELECT status FROM fill_ledger WHERE fill_key=?", (key,)
    ).fetchone()
    assert status["status"] == TradeState.CONFIRMED.value
    store.close()


def test_backfill_trades_inserts_a_missing_fill(tmp_path: Path) -> None:
    """A REST row with no ledger key credits cash and writes CONFIRMED."""
    store = WalletStateStore(tmp_path / "wallet.db")
    result = backfill_trades(store, [_clob_row()], OUR_ADDRESS, _other_token)
    key = fill_key("clob-trade-1", ORDER_ID)
    assert result.applied == 1
    assert result.confirmed == 0
    assert result.cash == pytest.approx(SELL_CASH)
    assert result.keys == (key,)


def test_backfill_trades_inserts_a_taker_sell(tmp_path: Path) -> None:
    """A REST TAKER row is not in maker_orders; it still credits the top-level fill."""
    store = WalletStateStore(tmp_path / "wallet.db")
    row = {
        "id": "taker-trade-1",
        "taker_order_id": "0xtaker",
        "status": "CONFIRMED",
        "asset_id": YES_TOKEN,
        "side": "SELL",
        "size": "96.84",
        "price": "0.71",
        "trader_side": "TAKER",
        "match_time": "1787317357",
        "maker_orders": [
            {
                "maker_address": "0xother",
                "order_id": "0xother-order",
                "matched_amount": "96.84",
                "price": "0.71",
                "outcome": "Yes",
            }
        ],
    }
    result = backfill_trades(store, [row], OUR_ADDRESS, _other_token)
    key = fill_key("taker-trade-1", "0xtaker")
    assert result.applied == 1
    assert result.confirmed == 0
    assert result.cash == pytest.approx(96.84 * 0.71)
    assert result.keys == (key,)
    fill = store.fill_for_key(key)
    assert fill is not None
    assert fill.side is Side.SELL
    assert fill.size == pytest.approx(96.84)
    store.close()


def test_backfill_trades_skips_a_failed_trade(tmp_path: Path) -> None:
    """A FAILED CLOB trade moved no cash; it must not become a CONFIRMED ledger row."""
    store = WalletStateStore(tmp_path / "wallet.db")
    row = {**_clob_row(), "status": "FAILED"}
    result = backfill_trades(store, [row], OUR_ADDRESS, _other_token)
    assert result.applied == 0
    assert result.confirmed == 0
    assert result.keys == ()
    assert store.ledger_net_cash() == 0.0
    store.close()


def test_backfill_trades_skips_a_retrying_trade(tmp_path: Path) -> None:
    """RETRYING is not settled yet; a later pass books it once it says CONFIRMED."""
    store = WalletStateStore(tmp_path / "wallet.db")
    result = backfill_trades(
        store, [{**_clob_row(), "status": "RETRYING"}], OUR_ADDRESS, _other_token
    )
    assert result.applied == 0
    assert store.ledger_net_cash() == 0.0
    store.close()


def test_backfilled_taker_fill_is_not_a_maker_fill(tmp_path: Path) -> None:
    """A recovered taker sell must read back as taker, or the rebate report inflates PnL."""
    store = WalletStateStore(tmp_path / "wallet.db")
    row: dict[str, object] = {
        "id": "taker-trade-1",
        "taker_order_id": "0xtaker",
        "status": "CONFIRMED",
        "asset_id": YES_TOKEN,
        "side": "SELL",
        "size": "96.84",
        "price": "0.71",
        "trader_side": "TAKER",
        "match_time": "1787317357",
        "maker_orders": [],
    }
    backfill_trades(store, [row], OUR_ADDRESS, _other_token)
    fill = store.fill_for_key(fill_key("taker-trade-1", "0xtaker"))
    assert fill is not None
    assert fill.is_maker is False
    store.close()


def test_backfilled_maker_fill_stays_a_maker_fill(tmp_path: Path) -> None:
    """A recovered maker fill keeps is_maker so it still earns the rebate."""
    store = WalletStateStore(tmp_path / "wallet.db")
    backfill_trades(store, [_clob_row()], OUR_ADDRESS, _other_token)
    fill = store.fill_for_key(fill_key("clob-trade-1", ORDER_ID))
    assert fill is not None
    assert fill.is_maker is True
    store.close()
