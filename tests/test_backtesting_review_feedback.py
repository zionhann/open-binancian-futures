from __future__ import annotations

import inspect
import io
import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewAlgoOrderSideEnum,
)

from open_binancian_futures import (
    BacktestConfig,
    Backtesting,
    BacktestResult,
    BacktestSummary,
    BinanceVisionDataSource,
    Candle,
    DataFrameDataSource,
    DeterministicFillPolicy,
    ExecutionConfig,
    MarketExecutionPolicy,
)
from open_binancian_futures.models import Balance, Order, OrderEvent
from open_binancian_futures.strategy import Strategy
from open_binancian_futures.types import EventType, OrderStatus, OrderType, PositionSide


def make_frame(
    symbol: str,
    timestamps: list[pd.Timestamp],
    *,
    opens: list[float] | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    values = closes or [100.0] * len(timestamps)
    return pd.DataFrame(
        {
            "Open_time": timestamps,
            "Symbol": [symbol] * len(timestamps),
            "Open": opens or values,
            "High": highs or [value + 1 for value in values],
            "Low": lows or [value - 1 for value in values],
            "Close": values,
            "Volume": [1.0] * len(timestamps),
        }
    )


class NoOpStrategy:
    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del symbol, interval, index


class LiveIntentStrategy(Strategy):
    def load(self, df):
        return df

    async def run(self, symbol: str, interval: str) -> None:
        del symbol, interval

    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        del symbol, interval, index


def test_explicit_interval_is_not_aliased_to_a_different_loaded_interval() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    source = DataFrameDataSource(
        {"ETHUSDT": {"1h": make_frame("ETHUSDT", timestamps)}},
        interval="1h",
    )

    with pytest.raises(ValueError, match="Configured interval '5m'.*ETHUSDT"):
        Backtesting(
            strategy=NoOpStrategy(),
            data_source=source,
            config=BacktestConfig(warmup_bars=0, interval="5m"),
        )


def test_implicit_interval_mismatch_is_rejected_across_symbols() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    source = DataFrameDataSource(
        {
            "ETHUSDT": {"1h": make_frame("ETHUSDT", timestamps)},
            "BTCUSDT": {"4h": make_frame("BTCUSDT", timestamps)},
        }
    )

    with pytest.raises(ValueError, match="Selected interval '1h'.*BTCUSDT"):
        Backtesting(
            strategy=NoOpStrategy(),
            data_source=source,
            config=BacktestConfig(warmup_bars=0),
        )


def test_reduce_only_limit_is_a_supported_exit() -> None:
    candle = Candle(
        pd.Timestamp("2026-01-01", tz="UTC"),
        open=100.0,
        high=105.0,
        low=99.0,
        close=104.0,
    )
    order = Order(
        symbol="ETHUSDT",
        order_id=1,
        type=OrderType.LIMIT,
        side=PositionSide.SELL,
        price=104.0,
        quantity=1.0,
        reduce_only=True,
    )

    selected = DeterministicFillPolicy().select_exit(
        [order],
        candle,
        PositionSide.BUY,
    )

    assert selected == (order, 104.0)


def test_partial_exit_records_only_the_closed_quantity() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"))
    frame = make_frame(
        "ETHUSDT",
        timestamps,
        opens=[100.0, 100.0, 104.0, 110.0],
        highs=[100.0, 103.0, 105.0, 110.0],
        lows=[100.0, 99.0, 103.0, 109.0],
        closes=[100.0, 100.0, 104.0, 110.0],
    )

    class PartialExitStrategy:
        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
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
            elif index == 1:
                self.orders[symbol].open_order(
                    symbol=symbol,
                    type=OrderType.LIMIT,
                    side=PositionSide.SELL,
                    entry_price=104.0,
                    entry_quantity=0.4,
                    time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
                    reduce_only=True,
                )

    result = Backtesting(
        strategy=PartialExitStrategy(),
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(initial_balance=100.0, interval="1h"),
    ).run()

    assert [trade.quantity for trade in result.summary.trades] == [0.4, 0.6]
    assert result.summary.pnl == pytest.approx(7.6)
    assert result.final_balance == pytest.approx(107.6)


def test_all_crossed_partial_exits_are_processed_on_one_candle() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"))
    frame = make_frame(
        "ETHUSDT",
        timestamps,
        opens=[100.0, 100.0, 100.0, 90.0],
        highs=[100.0, 100.0, 106.0, 90.0],
        lows=[100.0, 100.0, 99.0, 90.0],
        closes=[100.0, 100.0, 105.0, 90.0],
    )

    class MultiplePartialExitStrategy:
        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
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
            elif index == 1:
                for price in (104.0, 105.0):
                    self.orders[symbol].open_order(
                        symbol=symbol,
                        type=OrderType.LIMIT,
                        side=PositionSide.SELL,
                        entry_price=price,
                        entry_quantity=0.4,
                        time=self.indicators[symbol]["1h"].iloc[index]["Open_time"],
                        reduce_only=True,
                    )

    result = Backtesting(
        strategy=MultiplePartialExitStrategy(),
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(initial_balance=100.0, interval="1h"),
    ).run()

    assert [trade.quantity for trade in result.summary.trades] == pytest.approx(
        [0.4, 0.4, 0.2]
    )
    assert [trade.exit_price for trade in result.summary.trades] == [104.0, 105.0, 90.0]
    assert result.summary.pnl == pytest.approx(1.6)


def test_deferred_market_entry_reserves_margin_before_the_next_symbol_callback() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frames = {
        symbol: make_frame(symbol, timestamps, opens=[60.0, 61.0], closes=[60.0, 61.0])
        for symbol in ("ETHUSDT", "SOLUSDT")
    }

    class DeferredMarketStrategy(Strategy):
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
            self.observed: list[tuple[str, float, float]] = []

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
                        OrderType.MARKET,
                        quantity=1.0,
                    )
                )
            if index == 0:
                self.observed.append(
                    (symbol, self.balance.available, self.balance.reserved_margin)
                )

    strategy = DeferredMarketStrategy()
    result = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(frames, interval="1h"),
        config=BacktestConfig(
            initial_balance=100.0,
            interval="1h",
            market_execution=MarketExecutionPolicy.NEXT_OPEN,
        ),
    ).run()

    assert strategy.observed == [("ETHUSDT", 40.0, 60.0), ("SOLUSDT", 40.0, 60.0)]
    assert result.final_balance == pytest.approx(100.0)


def test_cross_symbol_market_order_uses_target_candle_and_waits_for_next_open() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frames = {
        "ETHUSDT": make_frame(
            "ETHUSDT",
            timestamps,
            opens=[100.0, 100.0],
            highs=[100.0, 100.0],
            lows=[100.0, 100.0],
            closes=[100.0, 100.0],
        ),
        "SOLUSDT": make_frame(
            "SOLUSDT",
            timestamps,
            opens=[11.0, 12.0],
            highs=[11.0, 12.0],
            lows=[10.0, 12.0],
            closes=[10.0, 12.0],
        ),
    }

    class CrossSymbolMarketStrategy(Strategy):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.accepted: list[bool] = []

        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del interval
            if symbol == "ETHUSDT" and index == 0:
                self.accepted.append(
                    await self.submit_order(
                        self.order_intent(
                            "SOLUSDT",
                            PositionSide.BUY,
                            OrderType.MARKET,
                            quantity=1.0,
                        )
                    )
                )

    strategy = CrossSymbolMarketStrategy(
        client=None,
        exchange_info=None,
        balance=None,
        orders=None,
        positions=None,
        webhook=None,
        indicators=None,
    )
    result = Backtesting(
        strategy=strategy,
        data_source=DataFrameDataSource(frames, interval="1h"),
        config=BacktestConfig(
            initial_balance=15.0,
            leverage=1,
            interval="1h",
            market_execution=MarketExecutionPolicy.NEXT_OPEN,
        ),
    ).run()

    assert strategy.accepted == [True]
    assert result.summary.trade_count == 1
    trade = result.by_symbol["SOLUSDT"].trades[0]
    assert trade.entry_time == timestamps[1]
    assert trade.entry_price == 12.0


def test_profit_factor_is_infinite_when_all_completed_trades_win() -> None:
    result = BacktestResult("ETHUSDT", evaluated_bars=1)
    result.record_trade(PositionSide.BUY, 2.0)
    summary = BacktestSummary.from_results([result])

    assert result.profit_factor == float("inf")
    assert summary.profit_factor == float("inf")
    assert BacktestResult("ETHUSDT").profit_factor == 0.0


def vision_archive(url: str) -> bytes:
    member = url.rsplit("/", 1)[-1][:-4] + ".csv"
    open_time = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000)
    row = [
        open_time,
        100.0,
        101.0,
        99.0,
        100.5,
        1.0,
        open_time + 3599999,
        100.0,
        1,
        1.0,
        1.0,
        0,
    ]
    csv = pd.DataFrame([row]).to_csv(index=False, header=False).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive_file:
        archive_file.writestr(member, csv)
    return output.getvalue()


def test_invalid_download_is_not_written_to_the_vision_cache(tmp_path) -> None:
    source = BinanceVisionDataSource(
        start_date="2026-01-01",
        end_date="2026-01-02",
        data_dir=tmp_path,
        downloader=lambda url: b"not a zip archive",
    )
    timestamp = pd.Timestamp("2026-01-01", tz="UTC")
    path = source._archive_path("monthly", "ETHUSDT", "1h", timestamp)

    with pytest.raises(ValueError, match="Invalid Binance Vision archive"):
        source._read_archive("monthly", "ETHUSDT", "1h", timestamp)

    assert not path.exists()


def test_corrupt_cached_vision_archive_is_removed_and_refreshed(tmp_path) -> None:
    timestamp = pd.Timestamp("2026-01-01", tz="UTC")
    calls: list[str] = []

    def download(url: str) -> bytes:
        calls.append(url)
        return vision_archive(url)

    source = BinanceVisionDataSource(
        start_date="2026-01-01",
        end_date="2026-01-02",
        data_dir=tmp_path,
        downloader=download,
    )
    path = source._archive_path("monthly", "ETHUSDT", "1h", timestamp)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupt cache")

    loaded = source._read_archive("monthly", "ETHUSDT", "1h", timestamp)

    assert len(loaded) == 1
    assert calls == [source._archive_url("monthly", "ETHUSDT", "1h", timestamp)]
    assert path.read_bytes() == vision_archive(calls[0])


def test_order_event_prefers_stop_price_when_order_price_is_zero() -> None:
    event = OrderEvent(
        source=EventType.ORDER_TRADE_UPDATE,
        symbol="ETHUSDT",
        order_id=1,
        status=OrderStatus.NEW,
        order_type=OrderType.STOP_MARKET,
        side=PositionSide.SELL,
        price=0.0,
        stop_price=95.0,
        quantity=1.0,
    )

    assert event.to_order().price == 95.0


def test_source_start_time_anchors_evaluation_after_warmup_context() -> None:
    timestamps = list(
        pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    )
    frame = make_frame("ETHUSDT", [timestamps[0], timestamps[2], timestamps[3]])
    source = DataFrameDataSource(frame, interval="1h", symbol="ETHUSDT")
    source.start_time = timestamps[2]

    class TimestampStrategy:
        def __init__(self) -> None:
            self.seen: list[pd.Timestamp] = []

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del interval
            self.seen.append(self.indicators[symbol]["1h"].iloc[index]["Open_time"])

    strategy = TimestampStrategy()
    Backtesting(
        strategy=strategy,
        data_source=source,
        config=BacktestConfig(warmup_bars=2, interval="1h"),
    ).run()

    assert strategy.seen == [timestamps[2], timestamps[3]]


def test_cost_model_is_applied_to_entry_and_exit() -> None:
    timestamps = list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC"))
    frame = make_frame(
        "ETHUSDT",
        timestamps,
        opens=[100.0, 110.0],
        highs=[100.0, 110.0],
        lows=[100.0, 110.0],
        closes=[100.0, 110.0],
    )

    class OneUnitCost:
        def __init__(self) -> None:
            self.calls = 0

        def cost(self, order, fill_price, quantity, timestamp) -> float:
            del order, fill_price, quantity, timestamp
            self.calls += 1
            return 1.0

    class EntryStrategy:
        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
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

    cost_model = OneUnitCost()
    result = Backtesting(
        strategy=EntryStrategy(),
        data_source=DataFrameDataSource(frame, interval="1h"),
        config=BacktestConfig(
            initial_balance=100.0,
            leverage=10,
            interval="1h",
            cost_model=cost_model,
        ),
    ).run()

    assert cost_model.calls == 2
    assert result.summary.trades[0].pnl == pytest.approx(8.0)
    assert result.final_balance == pytest.approx(108.0)


@pytest.mark.asyncio
async def test_live_intent_deducts_entry_margin_and_rolls_it_back_on_failure(
    monkeypatch,
) -> None:
    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del symbol, interval, index

    strategy = object.__new__(MinimalStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(rest_api=SimpleNamespace(new_order=object()))
    strategy.exchange_info = None
    strategy.execution_config = ExecutionConfig(leverage=10)
    strategy.balance = Balance(100.0, execution_config=strategy.execution_config)

    monkeypatch.setattr(
        "open_binancian_futures.strategy.fetch", lambda method, **kwargs: object()
    )
    intent = strategy.order_intent(
        "ETHUSDT", PositionSide.BUY, OrderType.LIMIT, price=100.0, quantity=1.0
    )

    assert await strategy.submit_order(intent) is True
    assert strategy.balance.available == 90.0

    strategy.balance = Balance(100.0, execution_config=strategy.execution_config)

    def fail_fetch(method, **kwargs):
        del method, kwargs
        raise RuntimeError("exchange failure")

    monkeypatch.setattr("open_binancian_futures.strategy.fetch", fail_fetch)

    assert await strategy.submit_order(intent) is False
    assert strategy.balance.available == 100.0


@pytest.mark.asyncio
async def test_live_market_intent_reserves_reference_margin(monkeypatch) -> None:
    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del symbol, interval, index

    strategy = object.__new__(MinimalStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(rest_api=SimpleNamespace(new_order=object()))
    strategy.exchange_info = None
    strategy.execution_config = ExecutionConfig(leverage=10)
    strategy.balance = Balance(100.0, execution_config=strategy.execution_config)

    monkeypatch.setattr(
        "open_binancian_futures.strategy.fetch", lambda method, **kwargs: object()
    )
    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.BUY,
        OrderType.MARKET,
        price=100.0,
        quantity=1.0,
    )

    assert await strategy.submit_order(intent) is True
    assert strategy.balance.available == 90.0


@pytest.mark.asyncio
async def test_live_unpriced_market_entry_is_rejected(monkeypatch) -> None:
    strategy = object.__new__(LiveIntentStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(rest_api=SimpleNamespace(new_order=object()))
    strategy.exchange_info = None
    strategy.balance = Balance(100.0)
    calls: list[object] = []

    def fake_fetch(method, **kwargs):
        calls.append((method, kwargs))
        return object()

    monkeypatch.setattr("open_binancian_futures.strategy.fetch", fake_fetch)
    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.BUY,
        OrderType.MARKET,
        quantity=1.0,
    )

    assert await strategy.submit_order(intent) is False
    assert calls == []
    assert strategy.balance.available == 100.0


@pytest.mark.asyncio
async def test_live_conditional_intent_uses_the_algo_order_adapter(monkeypatch) -> None:
    class MinimalStrategy(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol: str, interval: str) -> None:
            del symbol, interval

        async def run_backtest(
            self, symbol: str, interval: str, index: int
        ) -> None:
            del symbol, interval, index

    strategy = object.__new__(MinimalStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(
        rest_api=SimpleNamespace(new_order=object(), new_algo_order=object())
    )
    strategy.exchange_info = None
    strategy.balance = Balance(100.0)
    captured: dict[str, object] = {}

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


@pytest.mark.asyncio
async def test_live_trailing_stop_intent_is_rejected_without_trailing_parameters(
    monkeypatch,
) -> None:
    strategy = object.__new__(LiveIntentStrategy)
    strategy._backtest_gateway = None
    strategy.client = SimpleNamespace(
        rest_api=SimpleNamespace(new_order=object(), new_algo_order=object())
    )
    strategy.exchange_info = None
    strategy.balance = Balance(100.0)
    calls: list[object] = []

    def fake_fetch(method, **kwargs):
        calls.append((method, kwargs))
        return object()

    monkeypatch.setattr("open_binancian_futures.strategy.fetch", fake_fetch)
    intent = strategy.order_intent(
        "ETHUSDT",
        PositionSide.SELL,
        OrderType.TRAILING_STOP_MARKET,
        quantity=1.0,
        reduce_only=True,
    )

    assert await strategy.submit_order(intent) is False
    assert calls == []


def test_backtest_run_strategy_inspects_signature_once(monkeypatch) -> None:
    from open_binancian_futures import runners

    timestamps = list(pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC"))
    calls = 0
    original_signature = inspect.signature

    def counting_signature(callable_object):
        nonlocal calls
        calls += 1
        return original_signature(callable_object)

    monkeypatch.setattr(runners.inspect, "signature", counting_signature)
    Backtesting(
        strategy=NoOpStrategy(),
        data_source=DataFrameDataSource(
            make_frame("ETHUSDT", timestamps), interval="1h"
        ),
        config=BacktestConfig(interval="1h"),
    ).run()

    assert calls == 1
