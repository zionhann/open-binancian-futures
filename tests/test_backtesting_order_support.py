import asyncio

import pandas as pd
import pytest

from open_binancian_futures import Backtesting, DataFrameDataSource, OrderIntent
from open_binancian_futures.types import OrderType, PositionSide


@pytest.mark.parametrize('fields', [
    {'order_type': OrderType.STOP_MARKET, 'close_position': True},
    {'order_type': OrderType.TRAILING_STOP_MARKET, 'callback_rate': 1},
    {'order_type': OrderType.LIQUIDATION},
    {'time_in_force': 'IOC'},
    {'time_in_force': 'FOK'},
    {'time_in_force': 'GTD'},
    {'time_in_force': 'GTC', 'gtd': 1000},
    {'activation_price': 100},
    {'callback_rate': 1},
    {'order_type': OrderType.MARKET, 'time_in_force': 'GTC'},
    {'order_type': OrderType.MARKET, 'gtd': 1000},
])
def test_unsupported_intent_is_rejected_without_orders_or_reservations(fields):
    class Strategy:
        def run_backtest(self, *args): pass
    frame = pd.DataFrame({'Open_time': [pd.Timestamp('2026-01-01', tz='UTC')],
                          'Symbol': ['BTC'], 'Open': [100], 'High': [101],
                          'Low': [99], 'Close': [100]})
    runner = Backtesting(Strategy(), DataFrameDataSource(frame))
    kwargs = dict(symbol='BTC', side=PositionSide.SELL, order_type=OrderType.LIMIT,
                  price=90, quantity=1)
    kwargs.update(fields)
    with pytest.raises(ValueError):
        asyncio.run(runner.submit_order(OrderIntent(**kwargs)))
    assert not runner.orders['BTC']
    assert runner.pending_margin == 0 and runner.balance.available == 100


def test_strategy_submit_propagates_unsupported_backtest_intent():
    from open_binancian_futures.strategy import Strategy
    class RealStrategy(Strategy):
        def load(self, frame): return frame
        async def run(self, *args): pass
        async def run_backtest(self, *args): pass
    strategy = RealStrategy(None, None, None, None, None, None, None)
    frame = pd.DataFrame({'Open_time': [pd.Timestamp('2026-01-01', tz='UTC')],
                          'Symbol': ['BTC'], 'Open': [100], 'High': [101],
                          'Low': [99], 'Close': [100]})
    runner = Backtesting(strategy, DataFrameDataSource(frame))
    with pytest.raises(ValueError, match='TRAILING_STOP_MARKET'):
        asyncio.run(strategy.submit_order(OrderIntent('BTC', PositionSide.SELL,
                    OrderType.TRAILING_STOP_MARKET, 100, 1, callback_rate=1)))
    assert not runner.orders['BTC'] and runner.pending_margin == 0
