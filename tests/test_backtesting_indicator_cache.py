import logging

import pandas as pd
import pytest

from open_binancian_futures import Backtesting, BacktestConfig, DataFrameDataSource
from open_binancian_futures.models import Order
from open_binancian_futures.strategy import Strategy
from open_binancian_futures.types import OrderType, PositionSide


def data():
    def frame(symbol, frequency, offset=0):
        times = pd.date_range('2026-01-01', periods=4, freq=frequency, tz='UTC')
        return pd.DataFrame(dict(Open_time=times, Symbol=symbol, Open=100., High=110.,
                                 Low=90., Close=[100+offset,101+offset,102+offset,103+offset], Volume=1.))
    return {s: {'5m': frame(s, '5min', i), '1h': frame(s, 'h', i)}
            for i, s in enumerate(('BTC', 'ETH', 'SOL', 'XRP'))}


class CachedStrategy(Strategy):
    def __init__(self, enabled):
        self.enabled, self.period, self.loads = enabled, 2, 0
        self.observations = []
        super().__init__(None, None, None, None, None, None, None)
    def backtest_indicator_cache_key(self):
        return self.period if self.enabled else None
    def load(self, frame):
        self.loads += 1
        return frame.assign(mean=frame.Close.rolling(self.period, min_periods=1).mean())
    async def run(self, *args): pass
    async def run_backtest(self, symbol, interval, index):
        view = self.indicators[symbol][interval]
        self.observations.append((symbol, index, float(view.iloc[-1]['mean'])))
        if index == 0:
            self.orders[symbol].add(Order(symbol, 1, OrderType.MARKET, PositionSide.BUY, 100., .01))
        # Mutating the exposed view must never poison a later cached result.
        view.loc[:, 'mean'] = -999
    def on_backtest_entry_filled(self, symbol, timestamp):
        assert all((df.Open_time < timestamp).all()
                   for intervals in self.indicators.values() for df in intervals.values())


def runner(strategy, source=None):
    return Backtesting(strategy, DataFrameDataSource(source or data()),
                       BacktestConfig(interval='5m'))


def test_opt_in_reduces_loads_without_changing_trades_equity_or_callback_views():
    ordinary, cached = CachedStrategy(False), CachedStrategy(True)
    expected, actual = runner(ordinary).run(), runner(cached).run()
    assert actual.summary.trades == expected.summary.trades
    assert actual.equity_curve == expected.equity_curve
    assert actual.final_balance == expected.final_balance
    assert ordinary.observations == cached.observations
    assert cached.loads < ordinary.loads / 2
    assert len(cached._backtest_indicator_cache) == 8


def test_equal_length_data_changes_settings_and_disabled_cache_are_not_reused():
    strategy = CachedStrategy(True)
    instance = runner(strategy)
    cutoff = pd.Timestamp('2026-01-01 00:10', tz='UTC')
    instance._set_strategy_view(cutoff)
    before = strategy.loads
    instance._set_strategy_view(cutoff)
    assert strategy.loads == before
    instance.indicators['BTC']['5m'].loc[:, 'Close'] = 200.
    instance._set_strategy_view(cutoff)
    assert strategy.loads == before + 1
    assert strategy.indicators['BTC']['5m']['mean'].iloc[-1] == 200.
    strategy.period = 3
    instance._set_strategy_view(cutoff)
    assert strategy.loads == before + 9
    strategy.enabled = False
    instance._set_strategy_view(cutoff)
    assert not strategy._backtest_indicator_cache
    assert strategy.loads == before + 17


def test_new_runner_clears_previous_run_cache():
    strategy = CachedStrategy(True)
    first = runner(strategy)
    first._set_strategy_view(pd.Timestamp('2026-01-01 00:10', tz='UTC'))
    previous = strategy._backtest_indicator_cache
    second = runner(strategy)
    assert strategy._backtest_indicator_cache is not previous
    assert all(frame.empty for intervals in second.strategy.indicators.values()
               for frame in intervals.values())


def test_disabled_log_does_not_render_table(monkeypatch):
    strategy = CachedStrategy(False)
    monkeypatch.setattr(strategy.LOGGER, 'isEnabledFor', lambda level: False)
    def forbidden(*args, **kwargs): raise AssertionError('disabled log rendered a table')
    monkeypatch.setattr(pd.DataFrame, 'to_string', forbidden)
    runner(strategy).run()


def test_close_time_cache_detects_same_index_updates_and_nonmonotonic_closes():
    strategy = CachedStrategy(False)
    instance = runner(strategy)
    frame = instance.indicators['BTC']['5m']
    frame['Close_time'] = frame.Open_time + pd.Timedelta(minutes=5)
    cutoff = pd.Timestamp('2026-01-01 00:10', tz='UTC')
    assert len(instance._visible_indicators(cutoff)['BTC']['5m']) == 2
    frame.loc[frame.index[0], 'Close_time'] = cutoff + pd.Timedelta(minutes=1)
    visible = instance._visible_indicators(cutoff)['BTC']['5m']
    assert list(visible.index) == [frame.index[1]]


def test_stateful_load_remains_uncached_by_default():
    strategy = CachedStrategy(False)
    instance = runner(strategy)
    cutoff = pd.Timestamp('2026-01-01 00:10', tz='UTC')
    before = strategy.loads
    instance._set_strategy_view(cutoff)
    instance._set_strategy_view(cutoff)
    assert strategy.loads == before + 16


def test_close_times_are_computed_once_per_frame_in_each_run(monkeypatch):
    strategy = CachedStrategy(False)
    instance = runner(strategy)
    calls = []
    original = instance._close_times
    def counted(frame, interval):
        calls.append(interval)
        return original(frame, interval)
    monkeypatch.setattr(instance, '_close_times', counted)
    instance.run()
    assert len(calls) == 8


def test_different_close_times_preserve_default_and_cached_views():
    source = data()
    for intervals in source.values():
        frame = intervals['5m']
        frame['Close_time'] = frame.Open_time + pd.Timedelta(minutes=4)
    source['ETH']['5m']['Close_time'] += pd.Timedelta(seconds=30)
    ordinary, cached = CachedStrategy(False), CachedStrategy(True)
    expected, actual = runner(ordinary, source).run(), runner(cached, source).run()
    assert actual.summary.trades == expected.summary.trades
    assert actual.equity_curve == expected.equity_curve
    assert actual.final_balance == expected.final_balance
    assert ordinary.observations == cached.observations


def test_close_time_source_mode_changes_invalidate_equal_values():
    instance = runner(CachedStrategy(False))
    frame = instance.indicators['BTC']['5m']
    cutoff = frame.index[0]
    assert instance._visible_indicators(cutoff)['BTC']['5m'].empty
    frame['Close_time'] = frame.index
    assert len(instance._visible_indicators(cutoff)['BTC']['5m']) == 1
    frame.drop(columns='Close_time', inplace=True)
    assert instance._visible_indicators(cutoff)['BTC']['5m'].empty


def test_nat_cutoff_never_exposes_future_rows():
    instance = runner(CachedStrategy(False))
    visible = instance._visible_indicators(pd.NaT)
    assert all(frame.empty for intervals in visible.values() for frame in intervals.values())
