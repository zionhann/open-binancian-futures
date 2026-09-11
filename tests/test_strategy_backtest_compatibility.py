from __future__ import annotations

import inspect
from types import SimpleNamespace

import pandas as pd
import pytest
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewAlgoOrderSideEnum,
    NewOrderSideEnum,
    NewOrderTimeInForceEnum,
)

from open_binancian_futures import BacktestConfig, Backtesting
from open_binancian_futures.models import (
    Balance,
    Indicator,
    OrderBook,
    OrderList,
    PositionBook,
    PositionList,
)
from open_binancian_futures.strategy import Strategy
from open_binancian_futures.types import OrderType, PositionSide
from open_binancian_futures.webhook import Webhook


class MinimalStrategy(Strategy):
    def load(self, df):
        return df

    async def run(self, symbol: str, interval: str) -> None:
        del symbol, interval

    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del symbol, interval, index


class RecordingGateway:
    def __init__(self) -> None:
        self.intents = []

    async def submit_order(self, intent) -> bool:
        self.intents.append(intent)
        return True


@pytest.mark.asyncio
async def test_domain_intent_uses_the_backtest_gateway() -> None:
    strategy = object.__new__(MinimalStrategy)
    strategy.orders = {"ETHUSDT": OrderList()}
    gateway = RecordingGateway()
    strategy._backtest_gateway = gateway

    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.BUY,
        OrderType.LIMIT,
        price=99.0,
        quantity=1.0,
    )

    assert await strategy.submit_order(intent) is True
    assert gateway.intents == [intent]


@pytest.mark.asyncio
async def test_domain_intent_maps_sdk_enums_only_at_live_boundary(monkeypatch) -> None:
    strategy = object.__new__(MinimalStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(
        rest_api=SimpleNamespace(new_order=object(), new_algo_order=object())
    )
    strategy.exchange_info = None
    strategy.balance = Balance(100.0)
    captured = {}

    def fake_fetch(method, **kwargs):
        captured["method"] = method
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("open_binancian_futures.strategy.fetch", fake_fetch)

    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.SELL,
        OrderType.LIMIT,
        price=99.0,
        quantity=0.5,
    )

    assert await strategy.submit_order(intent) is True
    assert captured["side"] is NewOrderSideEnum.SELL
    assert captured["time_in_force"] is NewOrderTimeInForceEnum.GTC
    assert captured["type"] == OrderType.LIMIT.value


@pytest.mark.asyncio
async def test_domain_intent_preserves_reduce_only_at_live_boundary(monkeypatch) -> None:
    strategy = object.__new__(MinimalStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(
        rest_api=SimpleNamespace(new_order=object(), new_algo_order=object())
    )
    strategy.exchange_info = None
    strategy.balance = Balance(100.0)
    captured = {}

    def fake_fetch(method, **kwargs):
        captured["method"] = method
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("open_binancian_futures.strategy.fetch", fake_fetch)

    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.SELL,
        OrderType.STOP_MARKET,
        price=95.0,
        quantity=0.5,
        reduce_only=True,
    )

    assert await strategy.submit_order(intent) is True
    assert captured["method"] is strategy.client.rest_api.new_algo_order
    assert captured["side"] is NewAlgoOrderSideEnum.SELL
    assert captured["trigger_price"] == 95.0
    assert captured["reduce_only"] == "true"


def test_existing_callback_signature_remains_public() -> None:
    assert list(inspect.signature(Strategy.run_backtest).parameters) == [
        "self",
        "symbol",
        "interval",
        "index",
    ]


def test_strategy_can_initialize_offline_with_injected_context() -> None:
    frame = pd.DataFrame(
        {
            "Open_time": [pd.Timestamp("2026-01-01", tz="UTC")],
            "Symbol": ["ETHUSDT"],
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.0],
            "Volume": [1.0],
        }
    )
    indicators = Indicator()
    indicators["ETHUSDT"]["1h"] = frame

    strategy = MinimalStrategy(
        client=None,
        exchange_info=object(),
        balance=Balance(100.0),
        orders=OrderBook({"ETHUSDT": OrderList()}),
        positions=PositionBook({"ETHUSDT": PositionList()}),
        webhook=Webhook.of(url=None),
        indicators=indicators,
    )

    assert strategy.client is None
    assert strategy.indicators["ETHUSDT"]["1h"].iloc[0]["Close"] == 100.0


def test_injected_strategy_receives_strategy_computed_indicators() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = pd.DataFrame(
        {
            "Open_time": timestamps,
            "Symbol": ["ETHUSDT", "ETHUSDT"],
            "Open": [100.0, 100.0],
            "High": [101.0, 101.0],
            "Low": [99.0, 99.0],
            "Close": [100.0, 100.0],
            "Volume": [1.0, 1.0],
        }
    )

    class IndicatorStrategy(Strategy):
        def __init__(self) -> None:
            super().__init__(
                client=None,
                exchange_info=None,
                balance=None,
                orders=None,
                positions=None,
                webhook=None,
                indicators=None,
            )
            self.seen: list[float] = []

        def load(self, df):
            return df.assign(TEST_INDICATOR=1.0)

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            self.seen.append(
                self.indicators[symbol][interval].iloc[index]["TEST_INDICATOR"]
            )

    strategy = IndicatorStrategy()
    Backtesting(
        strategy=strategy,
        data_source=frame,
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    ).run()

    assert strategy.seen == [1.0, 1.0]
