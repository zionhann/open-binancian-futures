from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from open_binancian_futures import (
    BacktestConfig,
    Backtesting,
    DataFrameDataSource,
    ExecutionConfig,
)
from open_binancian_futures.constants import settings
from open_binancian_futures.models import Balance, OrderIntent
from open_binancian_futures.strategy import Strategy, StrategyContext
from open_binancian_futures.types import OrderType, PositionSide


def _frame() -> pd.DataFrame:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    return pd.DataFrame(
        {
            "Open_time": timestamps,
            "Symbol": ["ETHUSDT", "ETHUSDT"],
            "Open": [100.0, 100.0],
            "High": [100.0, 100.0],
            "Low": [100.0, 100.0],
            "Close": [100.0, 100.0],
            "Volume": [1.0, 1.0],
        }
    )


def test_balance_quantity_uses_injected_execution_config(monkeypatch) -> None:
    monkeypatch.setattr(settings, "leverage", 20)
    monkeypatch.setattr(settings, "size", 0.9)

    balance = Balance(
        100.0,
        execution_config=ExecutionConfig(leverage=2, position_size=0.1),
    )

    assert balance.calculate_quantity(100.0) == pytest.approx(0.2)


def test_backtest_order_size_uses_config_instead_of_global_settings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "leverage", 20)
    monkeypatch.setattr(settings, "size", 0.9)

    class IntentStrategy:
        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del interval
            if index == 0:
                await self._backtest_gateway.submit_order(
                    self.order_intent(
                        symbol,
                        PositionSide.BUY,
                        OrderType.MARKET,
                        price=100.0,
                    )
                )

        def order_intent(self, symbol, side, order_type, *, price=None):
            return OrderIntent(symbol, side, order_type, price=price)

    result = Backtesting(
        strategy=IntentStrategy(),
        data_source=DataFrameDataSource(_frame(), interval="1h"),
        config=BacktestConfig(
            initial_balance=100.0,
            leverage=2,
            position_size=0.1,
            interval="1h",
        ),
    ).run()

    assert result.summary.trades[0].quantity == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_live_margin_uses_injected_execution_config(monkeypatch) -> None:
    monkeypatch.setattr(settings, "leverage", 20)

    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del symbol, interval, index

    strategy = object.__new__(MinimalStrategy)
    strategy._backtest_gateway = None
    strategy.execution_config = ExecutionConfig(leverage=2, position_size=0.1)
    strategy.client = SimpleNamespace(rest_api=SimpleNamespace(new_order=object()))
    strategy.exchange_info = None
    strategy.balance = Balance(
        100.0,
        execution_config=strategy.execution_config,
    )

    monkeypatch.setattr(
        "open_binancian_futures.strategy.fetch", lambda method, **kwargs: object()
    )
    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.BUY,
        OrderType.LIMIT,
        price=100.0,
        quantity=1.0,
    )

    assert await strategy.submit_order(intent) is True
    assert strategy.balance.available == pytest.approx(50.0)


def test_strategy_factory_injects_execution_config(monkeypatch) -> None:
    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del symbol, interval, index

    monkeypatch.setattr(
        Strategy,
        "_import_strategy",
        staticmethod(lambda name: MinimalStrategy),
    )
    execution_config = ExecutionConfig(leverage=3, position_size=0.2)

    strategy = Strategy.of(
        "strategy.py",
        context=StrategyContext(execution_config=execution_config),
    )

    assert strategy.execution_config == execution_config
    assert strategy.balance.execution_config == execution_config
