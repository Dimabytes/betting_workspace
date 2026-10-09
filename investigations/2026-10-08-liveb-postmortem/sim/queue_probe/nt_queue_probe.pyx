# cython: language_level=3
"""Read private state of a Nautilus backtest (read only): venues and queue-position maps."""
from nautilus_trader.backtest.engine cimport BacktestEngine
from nautilus_trader.backtest.engine cimport OrderMatchingEngine


def exchange(BacktestEngine engine, venue):
    """The SimulatedExchange for a venue."""
    return engine._venues[venue]


def queue_state(OrderMatchingEngine engine):
    """Copies of _queue_ahead, _queue_excess, _queue_pending keyed by client order id, plus the trade in flight."""
    ahead = {str(k): (int(v[0]), int(v[1])) for k, v in engine._queue_ahead.items()}
    excess = {str(k): int(v) for k, v in engine._queue_excess.items()}
    pending = {str(k): int(v) for k, v in engine._queue_pending.items()}
    trade = None if engine._last_trade_size is None else float(engine._last_trade_size)
    return ahead, excess, pending, trade
