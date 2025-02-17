import logging
import textwrap

from pandas import Series

from app.core.balance import Balance
from app.core.constant import (
    BacktestConfig,
    OrderType,
    INTERVAL_TO_SECONDS,
)
from app.core.order import Orders
from app.core.position import Positions
from app.core.strategy import Strategy


logger = logging.getLogger(__name__)


class TestResult:
    def __init__(self, symbol: str, strategy: Strategy):
        self.symbol = symbol
        self.strategy = strategy

        self.sample_size = 0
        self.hit_count = 0
        self.trade_count = 0
        self.win_count = 0

        self.hit_rate = 0.0
        self.win_rate = 0.0

        self.cumulative_pnl = 0.0
        self.return_per_day = 0.0

    def check_filled_order_backtest(
        self,
        positions: Positions,
        orders: Orders,
        kline: Series,
        balance: Balance,
    ) -> None:
        self.sample_size += 1
        time, high, low = kline["Open_time"], kline["High"], kline["Low"]

        if filled_orders := orders.filled_orders_backtest(high, low):
            for order in filled_orders:
                if order.is_type(OrderType.LIMIT) and positions.is_empty():
                    positions.open_position_backtest(balance, order, time)
                    orders.remove_by_id(order.id)

                    self.hit_count += 1

                elif order.is_type(
                    OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET
                ) and (position := positions.first()):
                    pnl = position.realized_pnl_backtest(balance, order, time)
                    positions.clear()
                    orders.clear()

                    self.trade_count += 1
                    self.win_count += 1 if pnl > 0 else 0
                    self.cumulative_pnl += pnl

    def print(self):
        self.hit_rate = round(self.hit_count / self.sample_size * 100, 2)
        self.win_rate = round(
            self.win_count / self.trade_count * 100 if self.trade_count else 0, 2
        )

        unit = self.strategy.interval[-1]
        base = INTERVAL_TO_SECONDS[unit] * int(self.strategy.interval[:-1])
        days = (base * self.sample_size) / (60 * 60 * 24)
        self.return_per_day = round(self.cumulative_pnl / days, 2)

        logger.info(
            textwrap.dedent(
                f"""
                ===Backtest Result===
                Symbol: {self.symbol}
                Hit Rate: {self.hit_rate}% ({self.hit_count}/{self.sample_size})
                Win Rate: {self.win_rate}% ({self.win_count}/{self.trade_count})

                Cumulative PNL: {self.cumulative_pnl:.2f} USDT
                Return per Day: {self.return_per_day:.2f} USDT
                """
            )
        )
