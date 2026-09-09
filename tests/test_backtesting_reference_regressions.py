from __future__ import annotations

import pandas as pd

from open_binancian_futures import (
    BacktestConfig,
    Backtesting,
    DataFrameDataSource,
    MarketExecutionPolicy,
)
from open_binancian_futures.types import OrderType, PositionSide


def make_frame(symbol: str, timestamps: list[pd.Timestamp], closes: list[float]):
    return pd.DataFrame(
        {
            "Open_time": timestamps,
            "Symbol": [symbol] * len(timestamps),
            "Open": closes,
            "High": [value + 1 for value in closes],
            "Low": [value - 1 for value in closes],
            "Close": closes,
            "Volume": [1.0] * len(timestamps),
        }
    )


class TimestampRecordingStrategy:
    def __init__(self) -> None:
        self.seen: list[tuple[str, int, pd.Timestamp]] = []

    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del interval
        timestamp = self.indicators[symbol]["1h"].iloc[index]["Open_time"]
        self.seen.append((symbol, index, timestamp))


class OneEntryStrategy:
    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del interval
        if symbol == "ETHUSDT" and index == 0:
            self.orders[symbol].open_order(
                symbol=symbol,
                type=OrderType.MARKET,
                side=PositionSide.BUY,
                entry_price=100.0,
                entry_quantity=1.0,
                time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
            )


class PendingEntryStrategy:
    def __init__(self) -> None:
        self.observed: list[tuple[float, float]] = []

    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
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
            self.observed.append((self.balance.available, self.balance.reserved_margin))


class NextOpenMarketStrategy:
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


def test_runner_processes_unsorted_candles_chronologically() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    strategy = TimestampRecordingStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(
            make_frame("ETHUSDT", [timestamps[2], timestamps[0], timestamps[1]], [102, 100, 101]),
            interval="1h",
        ),
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    )

    runner.run()

    assert [item[2] for item in strategy.seen] == timestamps
    assert [item[1] for item in strategy.seen] == [0, 1, 2]


def test_hit_rate_uses_actual_evaluated_bars_for_each_symbol() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    strategy = OneEntryStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(
            {
                "ETHUSDT": make_frame("ETHUSDT", timestamps, [100, 101, 102]),
                "SOLUSDT": make_frame("SOLUSDT", [timestamps[0], timestamps[2]], [50, 52]),
            },
            interval="1h",
        ),
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    )

    result = runner.run()

    assert result.summary.evaluated_bars == 4
    assert result.summary.entry_count == 1
    assert result.summary.hit_rate == 0.25


def test_pending_margin_is_visible_to_the_next_strategy_callback() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    strategy = PendingEntryStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(
            make_frame("ETHUSDT", timestamps, [100, 100]), interval="1h"
        ),
        config=BacktestConfig(warmup_bars=0, interval="1h"),
    )

    result = runner.run()

    assert strategy.observed == [(80.0, 20.0)]
    assert result.final_balance == 100.0


def test_next_open_market_execution_is_explicit() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = make_frame("ETHUSDT", timestamps, [100, 110])
    frame.loc[1, "Open"] = 111.0
    strategy = NextOpenMarketStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(
            warmup_bars=0,
            interval="1h",
            market_execution=MarketExecutionPolicy.NEXT_OPEN,
        ),
    )

    result = runner.run()

    assert result.summary.trades[0].entry_time == timestamps[1]
    assert result.summary.trades[0].entry_price == 111.0


def test_union_timeline_does_not_evaluate_a_symbol_before_its_warmup() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    earlier = list(
        pd.date_range("2025-12-31 22:00", periods=5, freq="h", tz="UTC")
    )
    strategy = TimestampRecordingStrategy()
    runner = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(
            {
                "ETHUSDT": make_frame("ETHUSDT", timestamps, [100, 101, 102]),
                "SOLUSDT": make_frame("SOLUSDT", earlier, [50, 51, 52, 53, 54]),
            },
            interval="1h",
        ),
        config=BacktestConfig(
            warmup_bars=1,
            interval="1h",
            timeline_mode="union",
        ),
    )

    runner.run()

    assert [
        timestamp
        for symbol, _, timestamp in strategy.seen
        if symbol == "ETHUSDT"
    ] == timestamps[1:]
    assert [
        timestamp
        for symbol, _, timestamp in strategy.seen
        if symbol == "SOLUSDT"
    ] == earlier[1:]
