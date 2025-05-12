import logging
import textwrap
from typing import override

from model.balance import Balance
from model.constant import AppConfig, BacktestConfig, Required
from model.order import OrderBook
from model.position import PositionBook
from openapi import binance_futures as futures
from runner import Runner
from strategy import Strategy
from .test_result import TestResult

LOGGER = logging.getLogger(__name__)


class Backtesting(Runner):
    def __init__(self) -> None:
        self.client = futures.client
        self.balance = Balance(BacktestConfig.BALANCE.value)
        self.orders = OrderBook()
        self.positions = PositionBook()
        self.indicators = futures.init_indicators(
            limit=BacktestConfig.KLINES_LIMIT.value
        )
        self.strategy = Strategy.of(
            name=Required.STRATEGY.value,
            client=self.client,
            balance=self.balance,
            orders=self.orders,
            positions=self.positions,
            indicators=self.indicators,
        )
        self.test_results = {
            s: TestResult(s, self.strategy) for s in AppConfig.SYMBOLS.value
        }

    @override
    def run(self) -> None:
        LOGGER.info("Starting backtesting...")
        sample_size = (
                BacktestConfig.KLINES_LIMIT.value - BacktestConfig.INDICATOR_INIT_SIZE.value
        )

        for i in range(BacktestConfig.INDICATOR_INIT_SIZE.value, sample_size):
            for symbol in AppConfig.SYMBOLS.value:
                klines = self.indicators.get(symbol)[: i + 1]
                current_kline = klines.iloc[-1]
                open_time = current_kline["Open_time"]

                self.orders.get(symbol).remove_expired_orders_backtesting(open_time)
                self.strategy.run_backtest(symbol, i)
                self.test_results[symbol].evaluate_filled_orders(
                    positions=self.positions.get(symbol),
                    orders=self.orders.get(symbol),
                    kline=current_kline,
                    balance=self.balance,
                    leverage=AppConfig.LEVERAGE.value,
                )

        LOGGER.info("Backtesting finished. Closing remaining positions...")

        for symbol in AppConfig.SYMBOLS.value:
            self.positions.get(symbol).reset_backtesting(self.balance)
            self.orders.get(symbol).clear()

        [self.test_results[symbol].print() for symbol in AppConfig.SYMBOLS.value]

        overall_trade_count = sum(
            r.trade_count_buy + r.trade_count_sell for r in self.test_results.values()
        )
        overall_win_count = sum(
            r.win_count_buy + r.win_count_sell for r in self.test_results.values()
        )
        overall_win_rate = (
            overall_win_count / overall_trade_count * 100
            if overall_trade_count > 0
            else 0
        )

        LOGGER.info(
            textwrap.dedent(
                f"""
                ===Overall Result===
                Win Rate: {overall_win_rate:.2f}%
                PNL: {sum(r.cumulative_pnl for r in self.test_results.values()):.2f} USDT
                Balance: {BacktestConfig.BALANCE.value} -> {self.balance} USDT
                """
            )
        )

    @override
    def close(self) -> None:
        return
