import common
import logging

from app import App
from core.constants import AppConfig, ApiKey, BaseUrl
from core.order import Orders
from core.position import Positions
from binance.um_futures import UMFutures
from core.balance import Balance
from backtest.test_result import TestResult


class Backtest(App):
    LIMIT = 500
    TRIALS = 300
    logger = logging.getLogger(__name__)

    def __init__(self) -> None:
        self.client = UMFutures(
            key=ApiKey.CLIENT.value,
            secret=ApiKey.SECRET.value,
            base_url=BaseUrl.REST.value,
        )
        self.positions = {s: Positions() for s in AppConfig.SYMBOLS.value}
        self.orders = {s: Orders() for s in AppConfig.SYMBOLS.value}
        self.balance = Balance(10000)

        self.strategy = common.init_strategy()
        self.indicators = {
            s: common.init_indicators(s, self.client, self.strategy)
            for s in AppConfig.SYMBOLS.value
        }
        self.test_results = {
            s: TestResult(s, self.TRIALS) for s in AppConfig.SYMBOLS.value
        }

    def run(self) -> None:
        for symbol in AppConfig.SYMBOLS.value:
            self.logger.info(f"Running backtest for {symbol}...")

            for i in range(self.LIMIT - self.TRIALS, self.LIMIT):
                current_kline = self.indicators[symbol][: i + 1]
                self.strategy.run(
                    indicators=current_kline,
                    positions=self.positions[symbol],
                    orders=self.orders[symbol],
                    balance=self.balance,
                    client=self.client,
                    test_result=self.test_results[symbol],
                    is_backtest=True,
                )

        for symbol in AppConfig.SYMBOLS.value:
            print(self.test_results[symbol])

    def close(self) -> None:
        return
