import logging

import textwrap
from typing import override

from binance.um_futures import UMFutures
from app import App
from app.core.constant import AppConfig, BacktestConfig, Required
from app.core.exchange_info import ExchangeInfo
from app.core.order import Orders
from app.core.position import Positions
from app.core.balance import Balance
from app.core.strategy import Strategy
from app.backtest.test_result import TestResult
from app.utils import fetch


class Backtest(App):
    _LOGGER = logging.getLogger(__name__)
    _BASE_URL = "https://fapi.binance.com"

    def __init__(self) -> None:
        self.client = UMFutures(
            key=Required.API_KEY.value,
            secret=Required.API_SECRET.value,
            base_url=self._BASE_URL,
        )
        self.balance = Balance(BacktestConfig.BALANCE.value)
        self.strategy = Strategy.of(Required.STRATEGY.value)

        self.positions = {
            s: Positions(self.strategy.leverage) for s in AppConfig.SYMBOLS.value
        }
        self.orders = {s: Orders() for s in AppConfig.SYMBOLS.value}
        self.test_results = {
            s: TestResult(s, self.strategy) for s in AppConfig.SYMBOLS.value
        }
        self.indicators = {
            s: self.strategy.init_indicators(
                s, self.client, limit=BacktestConfig.KLINES_LIMIT.value
            )
            for s in AppConfig.SYMBOLS.value
        }

        exchange_info = fetch(self.client.exchange_info)["symbols"]
        self.exchange_info = {
            s: ExchangeInfo(s, exchange_info) for s in AppConfig.SYMBOLS.value
        }

    @override
    def run(self) -> None:
        for i in range(
            int(BacktestConfig.INDICATOR_INIT_SIZE.value),
            int(BacktestConfig.KLINES_LIMIT.value),
        ):
            for symbol in AppConfig.SYMBOLS.value:
                klines = self.indicators[symbol][: i + 1]
                current_kline = klines.iloc[-1]

                self.orders[symbol] = self.orders[symbol].rm_expired_orders_backtest(
                    current_kline["Open_time"]
                )

                self.test_results[symbol].check_filled_order_backtest(
                    positions=self.positions[symbol],
                    orders=self.orders[symbol],
                    kline=current_kline,
                    balance=self.balance,
                )

                self.strategy.run_backtest(
                    indicators=klines,
                    positions=self.positions[symbol],
                    orders=self.orders[symbol],
                    exchange_info=self.exchange_info[symbol],
                    balance=self.balance,
                )

        self._LOGGER.info("Backtesting finished. Closing remaining positions...")

        for symbol in AppConfig.SYMBOLS.value:
            self.positions[symbol].reset_backtest(self.balance)
            self.orders[symbol].clear()

        [self.test_results[symbol].print() for symbol in AppConfig.SYMBOLS.value]

        self._LOGGER.info(
            textwrap.dedent(
                f"""
                ===Overall Result===
                PNL: {sum(r.cumulative_pnl for r in self.test_results.values()):.2f} USDT
                Balance: {BacktestConfig.BALANCE.value} -> {self.balance} USDT
                """
            )
        )

    @override
    def close(self) -> None:
        return
