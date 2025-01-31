import logging

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
    logger = logging.getLogger(__name__)
    _BASE_URL = "https://fapi.binance.com"

    def __init__(self) -> None:
        self.client = UMFutures(
            key=Required.API_KEY.value,
            secret=Required.API_SECRET.value,
            base_url=self._BASE_URL,
        )
        self.balance = Balance(BacktestConfig.BALANCE.value)
        self.strategy = Strategy.of(Required.STRATEGY.value)

        self.positions = {s: Positions() for s in AppConfig.SYMBOLS.value}
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

    def run(self) -> None:
        for symbol in AppConfig.SYMBOLS.value:
            self.logger.info(f"Running backtest for {symbol}...")

            self.logger.info(
                f"Backtesting {BacktestConfig.INDICATOR_INIT_SIZE.value} to {BacktestConfig.KLINES_LIMIT.value}..."
            )

            for i in range(
                int(BacktestConfig.INDICATOR_INIT_SIZE.value),
                int(BacktestConfig.KLINES_LIMIT.value),
            ):
                current_kline = self.indicators[symbol][: i + 1]
                close_price = current_kline["Close"].iloc[-1]

                self.test_results[symbol].check_filled_order_backtest(
                    positions=self.positions[symbol],
                    orders=self.orders[symbol],
                    entry_price=close_price,
                    balance=self.balance,
                )
                self.strategy.run_backtest(
                    indicators=current_kline,
                    positions=self.positions[symbol],
                    orders=self.orders[symbol],
                    exchange_info=self.exchange_info[symbol],
                    balance=self.balance,
                )

        for symbol in AppConfig.SYMBOLS.value:
            self.test_results[symbol].print()

    def close(self) -> None:
        return
