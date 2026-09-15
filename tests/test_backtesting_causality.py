import pandas as pd
import pytest

from open_binancian_futures import (
    Backtesting,
    BacktestConfig,
    DataFrameDataSource,
    MarketExecutionPolicy,
)
from open_binancian_futures.models import Order
from open_binancian_futures.types import OrderType, PositionSide


def frame(symbol="ETH", periods=3, freq="h"):
    times = pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame(
        dict(
            Open_time=times,
            Symbol=symbol,
            Open=[100, 110, 120][:periods],
            High=130.0,
            Low=90.0,
            Close=105.0,
            Volume=1.0,
        )
    )


class Noop:
    def run_backtest(self, symbol, interval, index):
        pass


def test_default_next_open_and_end_reason():
    class Buy(Noop):
        def run_backtest(self, symbol, interval, index):
            if index == 0:
                self.orders[symbol].add(
                    Order(symbol, 1, OrderType.MARKET, PositionSide.BUY, 100.0, 0.5)
                )

    runner = Backtesting(
        Buy(),
        DataFrameDataSource(frame(), interval="1h"),
        BacktestConfig(warmup_bars=0),
    )
    trade = runner.run().summary.trades[0]
    assert trade.entry_price == 110
    assert trade.exit_reason == "end_of_backtest"
    assert BacktestConfig().market_execution == MarketExecutionPolicy.NEXT_OPEN


def test_prior_fill_cannot_be_cancelled_by_close_callback():
    class Cancel(Noop):
        def run_backtest(self, symbol, interval, index):
            if index == 0:
                self.orders[symbol].add(
                    Order(symbol, 1, OrderType.LIMIT, PositionSide.BUY, 95.0, 0.5)
                )
            else:
                self.orders[symbol].clear()

    result = Backtesting(
        Cancel(),
        DataFrameDataSource(frame(), interval="1h"),
        BacktestConfig(warmup_bars=0),
    ).run()
    assert result.summary.entry_count == 1


def test_all_views_completed_and_isolated():
    seen = []

    class Inspect(Noop):
        def run_backtest(self, symbol, interval, index):
            visible = self.indicators[symbol][interval]
            assert index == len(visible) - 1
            decision = (
                visible.iloc[index].Open_time
                + pd.Timedelta(minutes=5)
                - pd.Timedelta(milliseconds=1)
            )
            for intervals in self.indicators.values():
                for name, df in intervals.items():
                    delta = (
                        pd.Timedelta(hours=1)
                        if name == "1h"
                        else pd.Timedelta(minutes=5)
                    )
                    assert (
                        df.Open_time + delta - pd.Timedelta(milliseconds=1) <= decision
                    ).all()
            seen.append(visible.iloc[-1].Close)
            visible.loc[:, "Close"] = -999

    data = {s: {"5m": frame(s, freq="5min"), "1h": frame(s)} for s in ["ETH", "BTC"]}
    runner = Backtesting(
        Inspect(),
        DataFrameDataSource(data),
        BacktestConfig(warmup_bars=0, interval="5m"),
    )
    assert all(
        df.empty
        for intervals in runner.strategy.indicators.values()
        for df in intervals.values()
    )
    runner.run()
    assert seen == [105.0] * 6


def test_strategy_failure_propagates():
    class Fail(Noop):
        def run_backtest(self, symbol, interval, index):
            raise RuntimeError("strategy failed")

    with pytest.raises(RuntimeError, match="strategy failed"):
        Backtesting(
            Fail(),
            DataFrameDataSource(frame(), interval="1h"),
            BacktestConfig(warmup_bars=0),
        ).run()


def test_cli_strategy_failure_has_nonzero_exit(monkeypatch):
    from typer.testing import CliRunner
    from open_binancian_futures import cli

    class FailRunner:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self):
            raise RuntimeError("strategy failed")

    monkeypatch.setattr(cli, "Backtesting", FailRunner)
    result = CliRunner().invoke(cli.app, ["strategy.py", "--backtest"])
    assert result.exit_code != 0


def test_all_symbols_filled_before_callbacks_and_hook_orders_deferred():
    observations = []
    hook_lengths = []

    class Cross(Noop):
        def run_backtest(self, symbol, interval, index):
            if index == 0:
                self.orders[symbol].add(
                    Order(symbol, 1, OrderType.LIMIT, PositionSide.BUY, 95.0, 0.2)
                )
            if index == 1:
                observations.append(
                    tuple(bool(self.positions[s]) for s in ["BTC", "ETH"])
                )

        def on_backtest_entry_filled(self, symbol, timestamp):
            hook_lengths.append([len(self.indicators[s]["1h"]) for s in ["BTC", "ETH"]])
            self.orders["ETH"].add(
                Order(
                    "ETH",
                    10,
                    OrderType.STOP_MARKET,
                    PositionSide.SELL,
                    100.0,
                    0.2,
                    reduce_only=True,
                )
            )

    data = {s: frame(s) for s in ["ETH", "BTC"]}
    result = Backtesting(
        Cross(), DataFrameDataSource(data, interval="1h"), BacktestConfig(warmup_bars=0)
    ).run()
    assert observations == [(True, True), (True, True)]
    assert hook_lengths == [[1, 1], [1, 1]]
    assert (
        next(t for t in result.summary.trades if t.symbol == "ETH").exit_time
        == frame().Open_time.iloc[2]
    )


def test_last_next_open_is_cancelled_and_margin_released():
    class Last(Noop):
        def run_backtest(self, symbol, index):
            self.orders[symbol].add(
                Order(symbol, 1, OrderType.MARKET, PositionSide.BUY, 100.0, 0.5)
            )

    runner = Backtesting(
        Last(),
        DataFrameDataSource(frame(periods=1), interval="1h"),
        BacktestConfig(warmup_bars=0),
    )
    result = runner.run()
    assert result.summary.entry_count == 0
    assert result.final_balance == 100
    assert runner.pending_margin == 0
    assert not runner.orders["ETH"]


def test_custom_load_never_receives_future_and_raw_close_time_survives():
    from open_binancian_futures.strategy import Strategy

    loads = []
    observations = []

    class Custom(Strategy):
        def load(self, df):
            loads.append(tuple(df.Open_time))
            return df.assign(future_sensitive=df.Close.max())

        async def run(self, symbol, interval):
            pass

        async def run_backtest(self, symbol, interval, index):
            observations.append(
                (
                    self.indicators[symbol][interval].iloc[index].future_sensitive,
                    len(self.indicators[symbol]["1h"]),
                )
            )

    data = frame(freq="5min").assign(Close=[101.0, 102.0, 999.0])
    slow = frame().assign(Close_time=lambda df: df.Open_time + pd.Timedelta(minutes=7))
    strategy = Custom(None, None, None, None, None, None, None)
    runner = Backtesting(
        strategy,
        DataFrameDataSource({"ETH": {"5m": data, "1h": slow}}),
        BacktestConfig(warmup_bars=0, interval="5m"),
    )
    assert all(not times for times in loads)
    runner.run()
    assert observations == [(101.0, 0), (102.0, 1), (999.0, 1)]
    assert "Close_time" in runner.indicators["ETH"]["1h"]


def test_default_strategy_constructor_only_receives_warmup(monkeypatch):
    from open_binancian_futures.constants import settings
    from open_binancian_futures.strategy import Strategy

    observed = []
    data = frame()

    class Source:
        interval = "1h"

        def __init__(self, **kwargs):
            pass

        def load(self, symbols, intervals):
            return {"ETH": {"1h": data}}

    def construct(name, context):
        observed.extend(context.indicators["ETH"]["1h"].Open_time)
        return Noop()

    monkeypatch.setattr(
        "open_binancian_futures.runners.BinanceVisionDataSource", Source
    )
    monkeypatch.setattr(Strategy, "of", construct)
    monkeypatch.setattr(settings, "backtest_start_date", "2026-01-01")
    monkeypatch.setattr(settings, "backtest_end_date", "2026-01-02")
    monkeypatch.setattr(settings, "symbols", "ETH")
    monkeypatch.setattr(settings, "intervals", "1h")
    runner = Backtesting(config=BacktestConfig(warmup_bars=1, interval="1h"))
    assert observed == [data.Open_time.iloc[0]]
    assert len(runner.strategy.indicators["ETH"]["1h"]) == 1


def test_monthly_completion_uses_calendar_month():
    observed = []

    class Inspect(Noop):
        def run_backtest(self, symbol, interval, index):
            observed.append(len(self.indicators[symbol]["1M"]))

    monthly = frame(periods=1)
    daily = frame().assign(
        Open_time=pd.date_range("2026-01-30", periods=3, freq="D", tz="UTC")
    )
    Backtesting(
        Inspect(),
        DataFrameDataSource({"ETH": {"1d": daily, "1M": monthly}}),
        BacktestConfig(warmup_bars=0, interval="1d"),
    ).run()
    assert observed == [0, 1, 1]


def test_fresh_runs_are_deterministic():
    class Buy(Noop):
        def run_backtest(self, symbol, interval, index):
            if index == 0:
                self.orders[symbol].add(
                    Order(symbol, 1, OrderType.MARKET, PositionSide.BUY, 100.0, 0.5)
                )

    results = [
        Backtesting(
            Buy(),
            DataFrameDataSource(frame(), interval="1h"),
            BacktestConfig(warmup_bars=0),
        ).run()
        for _ in range(2)
    ]
    assert results[0].summary.trades == results[1].summary.trades
    assert results[0].equity_curve == results[1].equity_curve


@pytest.mark.parametrize("future_close", [20.0, 200.0])
@pytest.mark.parametrize("quantity, expected", [(1.0, (0, 0.0)), (0.25, (1, 25.0))])
def test_fill_hook_market_reservation_uses_known_open(future_close, quantity, expected):
    observed = []

    class HookOrder(Noop):
        def run_backtest(self, symbol, interval, index):
            if symbol == "ETH" and index == 0:
                self.orders[symbol].add(
                    Order(symbol, 1, OrderType.MARKET, PositionSide.BUY, 100.0, 0.5)
                )
            if symbol == "BTC" and index == 1:
                observed.append((len(self.orders["BTC"]), self.balance.reserved_margin))

        def on_backtest_entry_filled(self, symbol, timestamp):
            if symbol == "ETH":
                self.orders["BTC"].add(
                    Order("BTC", 2, OrderType.MARKET, PositionSide.BUY, 0.0, quantity)
                )

    data = {
        symbol: frame(symbol).assign(
            Open=100.0, Close=[100.0, future_close if symbol == "BTC" else 100.0, 100.0]
        )
        for symbol in ["ETH", "BTC"]
    }
    Backtesting(
        HookOrder(),
        DataFrameDataSource(data, interval="1h"),
        BacktestConfig(warmup_bars=0),
    ).run()
    # ETH consumed 50; BTC reserves using its known open regardless of future close.
    assert observed == [expected]
