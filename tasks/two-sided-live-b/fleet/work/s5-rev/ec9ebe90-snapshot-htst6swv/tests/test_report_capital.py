"""Archived capital must preserve cash timing and exclude non-shared funding."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from test_backtest_postprocess import (
    GAME_END,
    build_context,
    build_enriched,
    build_reserve_event,
    build_result_row,
)

from backtest import report_capital
from backtest.postprocess import write_fills_parquet
from backtest.report_capital import calculate_report_capital, read_shared_capital
from backtest.results import write_quote_events_parquet, write_results_checkpoint
from backtest.wallet_path import calculate_reserve_path, calculate_wallet_path
from shared.utils.match_time import datetime_to_ns


def test_shared_capital_excludes_outside_settlement_funding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_buy = datetime_to_ns(GAME_END - timedelta(minutes=1))
    second_buy = datetime_to_ns(GAME_END + timedelta(minutes=1))
    results = [
        replace(build_result_row(match_id=1), cash_flow=-50.0, engine_pnl=50.0),
        replace(
            build_result_row(match_id=2),
            cash_flow=-100.0,
            engine_pnl=0.0,
            game_ended_at=(GAME_END + timedelta(minutes=2)).isoformat(),
        ),
    ]
    fills = [
        build_enriched(match_id=1, ts_ns=first_buy, price=0.5, quantity=100.0, order_id="a"),
        build_enriched(match_id=2, ts_ns=second_buy, price=1.0, quantity=100.0, order_id="b"),
    ]
    quotes = [
        build_reserve_event(
            match_id=1, kind="submitted", order_id="a", ts_ns=first_buy, price=0.5, quantity=100.0
        ),
        build_reserve_event(
            match_id=2, kind="submitted", order_id="b", ts_ns=second_buy, price=1.0, quantity=100.0
        ),
    ]
    contexts = {mid: build_context(match_id=mid) for mid in (1, 2)}
    capital = calculate_report_capital(results, fills, quotes, {1: 0, 2: 0})
    wallet = calculate_wallet_path(results, fills, {}, contexts)
    reserve = calculate_reserve_path(results, fills, quotes, contexts, settlement_at="game_end")
    assert capital.cash_fills == pytest.approx(wallet.required_cash)
    assert capital.deposit == pytest.approx(reserve.required_cash)
    assert capital.deposit == pytest.approx(50.0)
    write_results_checkpoint(report_dir=tmp_path, results=results)
    write_fills_parquet(report_dir=tmp_path, fills=fills)
    write_quote_events_parquet(report_dir=tmp_path, events=quotes)

    def fake_winning_tokens(_seed_dir: Path, match_ids: frozenset[int]) -> dict[int, int]:
        return {match_id: 0 for match_id in match_ids}

    monkeypatch.setattr(report_capital, "read_winning_tokens", fake_winning_tokens)
    shared = read_shared_capital(tmp_path, frozenset({2}))
    assert shared.cash_fills == pytest.approx(100.0)
    assert shared.deposit == pytest.approx(100.0)


def test_report_capital_keeps_reserve_until_cancel_ack() -> None:
    first = build_reserve_event(kind="submitted", order_id="a", ts_ns=1, price=0.5, quantity=100)
    replacement = build_reserve_event(
        kind="submitted", order_id="b", ts_ns=3, price=0.5, quantity=100.0
    )
    requested = replace(first, kind="cancel_request", ts_ns=2)
    ack = replace(first, kind="cancel_ack", ts_ns=3)
    before = calculate_report_capital([], [], [first, requested, replacement], {})
    after = calculate_report_capital([], [], [first, requested, ack, replacement], {})
    assert before.deposit == pytest.approx(100)
    assert after.deposit == pytest.approx(50)
    assert after.cash_fills == 0


def test_report_capital_settles_dust_using_outcome_not_engine_pnl() -> None:
    buy_ns = datetime_to_ns(GAME_END - timedelta(seconds=2))
    sell_ns = datetime_to_ns(GAME_END - timedelta(seconds=1))
    later_ns = datetime_to_ns(GAME_END + timedelta(seconds=1))
    results = [
        replace(build_result_row(match_id=1), cash_flow=-0.01, engine_pnl=-0.0068),
        replace(
            build_result_row(match_id=2),
            game_ended_at=(GAME_END + timedelta(seconds=2)).isoformat(),
        ),
    ]
    fills = [
        build_enriched(match_id=1, ts_ns=buy_ns, quantity=1.0, price=0.5),
        build_enriched(match_id=1, ts_ns=sell_ns, side="SELL", quantity=0.98, price=0.5),
        build_enriched(match_id=2, ts_ns=later_ns, quantity=2.0, price=0.5),
    ]
    capital = calculate_report_capital(results, fills, [], {1: 0, 2: 0})
    wallet = calculate_wallet_path(
        results, fills, {}, {mid: build_context(match_id=mid) for mid in (1, 2)}
    )
    assert capital.cash_fills == pytest.approx(0.99)
    assert capital.cash_fills == pytest.approx(wallet.required_cash)


def test_pair_settles_each_token_instead_of_the_last_fill() -> None:
    """100 @ 0.42 and 100 @ 0.55 with YES winning is +$3, not the loser's residual."""
    buy_ns = datetime_to_ns(GAME_END - timedelta(seconds=2))
    results = [build_result_row(match_id=1)]
    fills = [
        build_enriched(match_id=1, ts_ns=buy_ns, token_index=0, price=0.42, quantity=100.0),
        build_enriched(
            match_id=1,
            ts_ns=buy_ns + 1,
            token_index=1,
            price=0.55,
            quantity=100.0,
            order_id="O-2",
        ),
    ]
    contexts = {1: build_context(match_id=1, radiant_token_index=0, radiant_win=True)}
    wallet = calculate_wallet_path(results, fills, {}, contexts)
    capital = calculate_report_capital(results, fills, [], {1: 0})
    assert capital.cash_fills == pytest.approx(97.0)
    assert wallet.required_cash == pytest.approx(97.0)
    assert wallet.equity_spark[-1] == pytest.approx(100.0)
