from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from open_binancian_futures import (
    BacktestConfig,
    Backtesting,
    DataFrameDataSource,
    ExecutionConfig,
    LiveTrading,
)
from open_binancian_futures import runners as runners_module
from open_binancian_futures.constants import settings
from open_binancian_futures.models import (
    Balance,
    Indicator,
    OrderBook,
    OrderEvent,
    OrderIntent,
    Position,
    PositionBook,
    PositionList,
)
from open_binancian_futures.strategy import Strategy, StrategyContext
from open_binancian_futures.types import EventType, OrderStatus, OrderType, PositionSide
from open_binancian_futures.webhook import Webhook


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


@pytest.mark.parametrize("timezone", ["", "Not/AZone"])
def test_execution_config_rejects_invalid_timezone(timezone: str) -> None:
    with pytest.raises(ValueError, match="timezone"):
        ExecutionConfig(timezone=timezone)


@pytest.mark.parametrize("entry_price", [0.0, -1.0, float("nan")])
def test_balance_quantity_rejects_non_positive_or_non_finite_price(
    entry_price: float,
) -> None:
    balance = Balance(100.0)

    with pytest.raises(ValueError, match="entry_price"):
        balance.calculate_quantity(entry_price)


def test_backtest_config_preserves_existing_positional_field_order() -> None:
    config = BacktestConfig(100.0, 2, 3, "1h", "union")

    assert config.warmup_bars == 3
    assert config.interval == "1h"
    assert config.timeline_mode == "union"
    assert config.position_size == pytest.approx(0.05)


def test_live_trading_injects_explicit_domain_objects(monkeypatch) -> None:
    fake_client = object()
    domain_objects = {
        "exchange_info": object(),
        "balance": object(),
        "orders": object(),
        "positions": object(),
        "indicators": object(),
    }
    calls = {}

    monkeypatch.setattr(settings, "strategy", "strategy.py")
    monkeypatch.setattr(settings, "symbols", "ETHUSDT,SOLUSDT")
    monkeypatch.setattr(settings, "intervals", "1m,5m")
    monkeypatch.setattr(settings, "leverage", 7)
    monkeypatch.setattr(settings, "size", 0.2)
    monkeypatch.setattr(settings, "timezone", "Asia/Seoul")
    monkeypatch.setattr(runners_module, "client", lambda: fake_client)

    for name, value in domain_objects.items():

        def initialize(*args, _name=name, _value=value, **kwargs):
            del args
            calls[_name] = kwargs
            return _value

        monkeypatch.setattr(
            runners_module.futures,
            f"init_{name}",
            initialize,
        )

    captured = {}

    def build_strategy(name, context):
        captured["name"] = name
        captured["context"] = context
        return object()

    monkeypatch.setattr(
        runners_module.Strategy,
        "of",
        staticmethod(build_strategy),
    )

    runner = LiveTrading()

    assert runner.client is fake_client
    assert calls["exchange_info"] == {"symbols": ("ETHUSDT", "SOLUSDT")}
    assert calls["balance"]["execution_config"].leverage == 7
    assert calls["orders"] == {"symbols": ("ETHUSDT", "SOLUSDT")}
    assert calls["positions"] == {
        "symbols": ("ETHUSDT", "SOLUSDT"),
        "leverage": 7,
    }
    assert calls["indicators"] == {
        "symbols": ("ETHUSDT", "SOLUSDT"),
        "intervals": ("1m", "5m"),
        "timezone": "Asia/Seoul",
    }
    context = captured["context"]
    assert captured["name"] == "strategy.py"
    assert context.client is fake_client
    assert context.exchange_info is domain_objects["exchange_info"]
    assert context.balance is domain_objects["balance"]
    assert context.orders is domain_objects["orders"]
    assert context.positions is domain_objects["positions"]
    assert context.indicators is domain_objects["indicators"]


def test_strategy_initial_indicators_use_injected_timezone(monkeypatch) -> None:
    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del symbol, interval, index

    calls = {}

    def init_indicators(**kwargs):
        calls.update(kwargs)
        return Indicator()

    monkeypatch.setattr(
        "open_binancian_futures.strategy.futures.init_indicators",
        init_indicators,
    )
    config = ExecutionConfig(timezone="Asia/Seoul")

    MinimalStrategy(
        client=object(),
        exchange_info=object(),
        balance=Balance(100.0, execution_config=config),
        orders=OrderBook(),
        positions=PositionBook(),
        webhook=Webhook.of(None),
        indicators=None,
        execution_config=config,
    )

    assert calls["timezone"] == "Asia/Seoul"


def test_strategy_factory_updates_positions_for_legacy_initializers(
    monkeypatch,
) -> None:
    class LegacyStrategy(Strategy):
        def __init__(
            self,
            client,
            exchange_info,
            balance,
            orders,
            positions,
            webhook,
            indicators,
        ) -> None:
            super().__init__(
                client,
                exchange_info,
                balance,
                orders,
                positions,
                webhook,
                indicators,
            )

        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del symbol, interval, index

    execution_config = ExecutionConfig(leverage=5)
    positions = PositionBook(
        {
            "ETHUSDT": PositionList(
                [
                    Position(
                        symbol="ETHUSDT",
                        price=100.0,
                        amount=1.0,
                        side=PositionSide.BUY,
                        leverage=1,
                    )
                ]
            )
        }
    )
    monkeypatch.setattr(
        Strategy,
        "_import_strategy",
        staticmethod(lambda name: LegacyStrategy),
    )

    strategy = Strategy.of(
        "strategy.py",
        StrategyContext(
            exchange_info=object(),
            balance=Balance(100.0, execution_config=execution_config),
            orders=OrderBook(),
            positions=positions,
            webhook=Webhook.of(None),
            indicators=Indicator(),
            execution_config=execution_config,
        ),
    )

    assert strategy.positions["ETHUSDT"].find_first().leverage == 5


def test_realized_profit_is_initialized_for_unknown_filled_symbol() -> None:
    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del symbol, interval, index

    strategy = MinimalStrategy(
        client=None,
        exchange_info=None,
        balance=Balance(100.0),
        orders=OrderBook(),
        positions=PositionBook(),
        webhook=Webhook.of(None),
        indicators=Indicator(),
    )
    strategy.accumulate_realized_profit("SOLUSDT", 2.5)
    strategy.on_filled_order(
        OrderEvent(
            source=EventType.ORDER_TRADE_UPDATE,
            symbol="SOLUSDT",
            order_id=1,
            status=OrderStatus.FILLED,
            order_type=OrderType.MARKET,
            side=PositionSide.BUY,
            price=100.0,
            quantity=1.0,
            filled=1.0,
            average_price=100.0,
        )
    )

    assert strategy._realized_profit["SOLUSDT"] == 0.0
