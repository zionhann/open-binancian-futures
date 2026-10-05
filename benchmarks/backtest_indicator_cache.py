"""Offline repeated benchmark: python benchmarks/backtest_indicator_cache.py."""
import json
import logging
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from open_binancian_futures import BacktestConfig, Backtesting, DataFrameDataSource
from open_binancian_futures.models import Order
from open_binancian_futures.strategy import Strategy
from open_binancian_futures.types import OrderType, PositionSide


class BenchmarkStrategy(Strategy):
    def __init__(self, cached):
        self.cached, self.loads = cached, 0
        super().__init__(None, None, None, None, None, None, None)
    def backtest_indicator_cache_key(self): return 20 if self.cached else None
    def load(self, frame):
        self.loads += 1
        result = frame.copy()
        for length in range(2, 22):
            result[f'mean_{length}'] = frame.Close.rolling(length, min_periods=1).mean()
        return result
    async def run(self, *args): pass
    async def run_backtest(self, symbol, interval, index):
        if index == 0:
            self.orders[symbol].add(Order(symbol, 1, OrderType.MARKET, PositionSide.BUY, 100., .01))


def main():
    logging.disable(logging.CRITICAL)
    index = pd.date_range('2026-01-01', periods=120, freq='5min', tz='UTC')
    data = {s: pd.DataFrame(dict(Open_time=index, Symbol=s, Open=100., High=110.,
                                 Low=90., Close=101., Volume=1.))
            for s in ('BTC', 'ETH', 'SOL', 'XRP')}
    report, reference = {}, None
    for cached in (False, True):
        durations, peaks, calls = [], [], []
        for _ in range(3):
            strategy = BenchmarkStrategy(cached)
            instance = Backtesting(strategy, DataFrameDataSource(data, interval='5m'),
                                   BacktestConfig(interval='5m'))
            tracemalloc.start()
            started = time.perf_counter()
            result = instance.run()
            durations.append(time.perf_counter() - started)
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            calls.append(strategy.loads)
            signature = (result.summary.trades, result.equity_curve, result.final_balance)
            if reference is None: reference = signature
            assert signature == reference, 'cache changed ledger, equity or final balance'
        report['cached' if cached else 'default'] = {
            'seconds': durations, 'median_seconds': statistics.median(durations),
            'peak_bytes': peaks, 'load_calls': calls,
        }
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
