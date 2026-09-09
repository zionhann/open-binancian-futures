from __future__ import annotations

import pandas as pd

from open_binancian_futures import (
    BacktestConfig,
    Backtesting,
    DataFrameDataSource,
    DeterministicFillPolicy,
    MarketExecutionPolicy,
)
from open_binancian_futures.strategy import Strategy
from open_binancian_futures.types import OrderType, PositionSide


def ohlcv(
    timestamps: list[pd.Timestamp],
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open_time": timestamps,
            "Symbol": ["ETHUSDT"] * len(timestamps),
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [1.0] * len(timestamps),
        }
    )


class NoOpStrategy:
    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del symbol, interval, index


class EntryThenStopStrategy:
    def __init__(self) -> None:
        self.entry_times: list[pd.Timestamp] = []
        self.reservations: list[float] = []

    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del interval
        if index == 2:
            self.reservations.append(self.balance.reserved_margin)
        if index == 0:
            self.orders[symbol].open_order(
                symbol=symbol,
                type=OrderType.LIMIT,
                side=PositionSide.BUY,
                entry_price=99.0,
                entry_quantity=1.0,
                time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
            )

    def on_backtest_entry_filled(
        self, symbol: str, timestamp: pd.Timestamp
    ) -> None:
        self.entry_times.append(timestamp)
        self.orders[symbol].open_order(
            symbol=symbol,
            type=OrderType.STOP_MARKET,
            side=PositionSide.SELL,
            entry_price=95.0,
            entry_quantity=1.0,
            time=timestamp,
        )


class LeaveLongOpenStrategy:
    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del interval
        if index == 0:
            self.orders[symbol].open_order(
                symbol=symbol,
                type=OrderType.MARKET,
                side=PositionSide.BUY,
                entry_price=100.0,
                entry_quantity=1.0,
                time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
            )


def test_injected_dataframe_backtest_does_not_create_a_client(monkeypatch) -> None:
    def fail_if_called():
        raise AssertionError("REST client must not be created for injected data")

    monkeypatch.setattr("open_binancian_futures.runners.client", fail_if_called)
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    runner = Backtesting(
        strategy=NoOpStrategy(),
        data_source=DataFrameDataSource(ohlcv(timestamps, [100, 100, 100], [101] * 3, [99] * 3, [100] * 3), interval="1h"),
        config=BacktestConfig(initial_balance=100.0, warmup_bars=0, interval="1h"),
    )

    result = runner.run()

    assert result.final_balance == 100.0
    assert result.summary.evaluated_bars == 3


def test_injected_raw_dataframe_uses_the_single_configured_symbol() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = pd.DataFrame(
        {
            "Open_time": timestamps,
            "Open": [100.0, 100.0],
            "High": [101.0, 101.0],
            "Low": [99.0, 99.0],
            "Close": [100.0, 100.0],
        }
    )

    result = Backtesting(
        strategy=NoOpStrategy(),
        data_source=frame,
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    ).run()

    assert result.summary.evaluated_bars == 2
    assert set(result.by_symbol) == {"BTCUSDT"}


def test_custom_source_can_return_a_raw_symbolized_dataframe() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = ohlcv(
        timestamps,
        [100.0, 100.0],
        [101.0, 101.0],
        [99.0, 99.0],
        [100.0, 100.0],
    )

    class RawFrameSource:
        def load(self, symbols, intervals):
            del symbols, intervals
            return frame

    result = Backtesting(
        strategy=NoOpStrategy(),
        data_source=RawFrameSource(),
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    ).run()

    assert set(result.by_symbol) == {"ETHUSDT"}


def test_fill_policy_market_execution_is_respected_by_runner() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = ohlcv(
        timestamps,
        [100.0, 111.0],
        [101.0, 112.0],
        [99.0, 110.0],
        [105.0, 111.0],
    )

    class MarketStrategy:
        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del interval
            if index == 0:
                self.orders[symbol].open_order(
                    symbol=symbol,
                    type=OrderType.MARKET,
                    side=PositionSide.BUY,
                    entry_price=0.0,
                    entry_quantity=0.5,
                    time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
                )

    result = Backtesting(
        strategy=MarketStrategy(),
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(
            warmup_bars=0,
            interval="1h",
            fill_policy=DeterministicFillPolicy(MarketExecutionPolicy.NEXT_OPEN),
        ),
    ).run()

    assert result.summary.trades[0].entry_time == timestamps[1]
    assert result.summary.trades[0].entry_price == 111.0


def test_same_order_id_on_different_symbols_has_independent_reservations() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frames = {
        symbol: ohlcv(
            timestamps,
            [100.0, 100.0],
            [101.0, 101.0],
            [99.0, 99.0],
            [100.0, 100.0],
        ).assign(Symbol=symbol)
        for symbol in ("ETHUSDT", "SOLUSDT")
    }

    class SameIdStrategy:
        def __init__(self) -> None:
            self.observed: list[tuple[float, float]] = []

        async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
            del interval
            if index == 0:
                self.orders[symbol].add(
                    Order(
                        symbol=symbol,
                        order_id=1,
                        type=OrderType.LIMIT,
                        side=PositionSide.BUY,
                        price=50.0,
                        quantity=1.0,
                    )
                )
            elif index == 1 and symbol == "SOLUSDT":
                self.observed.append(
                    (self.balance.available, self.balance.reserved_margin)
                )

    from open_binancian_futures.models import Order

    strategy = SameIdStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(frames, interval="1h"),
        config=BacktestConfig(initial_balance=100.0, warmup_bars=0, interval="1h"),
    )
    runner.run()

    assert runner.balance.available == 100.0
    assert runner.pending_margin == 0.0
    assert strategy.observed == [(0.0, 100.0)]


def test_new_limit_and_protective_stop_wait_until_a_later_candle() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    frame = ohlcv(
        timestamps,
        [100.0, 100.0, 100.0],
        [101.0, 102.0, 101.0],
        [99.0, 98.0, 94.0],
        [100.0, 101.0, 96.0],
    )
    strategy = EntryThenStopStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(initial_balance=100.0, warmup_bars=0, interval="1h"),
    )

    result = runner.run()

    assert result.summary.trade_count == 1
    trade = result.summary.trades[0]
    assert trade.entry_time == timestamps[1]
    assert trade.exit_time == timestamps[2]
    assert trade.entry_price == 99.0
    assert trade.exit_price == 95.0
    assert result.summary.pnl == -4.0
    assert strategy.reservations == [0.0]


def test_strategy_order_intent_is_executed_by_the_runner_gateway() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = ohlcv(
        timestamps,
        [100.0, 100.0],
        [101.0, 101.0],
        [99.0, 98.0],
        [100.0, 99.0],
    )

    class IntentStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del interval
            if symbol == "ETHUSDT" and index == 0:
                await self.submit_order(
                    self.order_intent(
                        symbol,
                        PositionSide.BUY,
                        OrderType.LIMIT,
                        price=99.0,
                        quantity=1.0,
                    )
                )

    strategy = IntentStrategy(
        client=None,
        exchange_info=object(),
        balance=None,
        orders=None,
        positions=None,
        webhook=None,
        indicators=None,
    )
    result = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    ).run()

    assert result.summary.entry_count == 1
    assert result.summary.trades[0].entry_price == 99.0


def test_strategy_cancellation_releases_pending_margin_before_next_callback() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))

    class CancellingStrategy:
        def __init__(self) -> None:
            self.observed: list[tuple[float, float]] = []

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del interval
            if index == 0:
                self.orders[symbol].open_order(
                    symbol=symbol,
                    type=OrderType.LIMIT,
                    side=PositionSide.BUY,
                    entry_price=20.0,
                    entry_quantity=1.0,
                    time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
                )
            elif index == 1:
                self.orders[symbol].clear()
            elif index == 2:
                self.observed.append((self.balance.available, self.balance.reserved_margin))

    strategy = CancellingStrategy()
    Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(
            ohlcv(timestamps, [100.0] * 3, [101.0] * 3, [99.0] * 3, [100.0] * 3),
            interval="1h",
        ),
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    ).run()

    assert strategy.observed == [(100.0, 0.0)]


def test_last_close_realizes_open_position_and_equity_curve() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    frame = ohlcv(
        timestamps,
        [100.0, 102.0, 104.0],
        [101.0, 103.0, 106.0],
        [99.0, 101.0, 103.0],
        [100.0, 102.0, 105.0],
    )
    runner = Backtesting(
        strategy=LeaveLongOpenStrategy(),
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(initial_balance=100.0, warmup_bars=0, interval="1h"),
    )

    result = runner.run()

    assert result.summary.trade_count == 1
    assert result.summary.pnl == 5.0
    assert len(result.equity_curve) == 3
    assert result.equity_curve[-1].equity == result.final_balance
    assert not runner.positions["ETHUSDT"]
