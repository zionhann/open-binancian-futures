import logging

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
        self.trade_count = 0
        self.win_count = 0

        self.trade_frequency = 0.0
        self.win_rate = 0.0

        self.cumulative_pnl = 0.0
        self.return_per_day = 0.0

    def check_filled_order_backtest(
        self,
        positions: Positions,
        orders: Orders,
        entry_price: float,
        balance: Balance,
    ) -> None:
        self.sample_size += 1

        if (position := positions.first()) and (
            filled_order := orders.find_all_by_type(
                OrderType.TAKE_PROFIT, OrderType.STOP
            ).filled_order(entry_price)
        ):
            if pnl := position.realized_pnl(filled_order):
                initial_margin = position.calculate_initial_margin()
                roi = pnl / initial_margin * 100
                logger.info(f"Realized PNL: {round(pnl, 2)}USDT, ROI: {round(roi, 2)}%")

                self.trade_count += 1
                self.win_count += 1 if pnl > 0 else 0
                self.cumulative_pnl += pnl

                balance = balance.update(pnl + initial_margin)
                orders.clear()
                positions.clear()

    def print(self):
        self.trade_frequency = round(self.trade_count / self.sample_size * 100, 2)
        self.win_rate = round(self.win_count / self.trade_count * 100, 2)

        unit = self.strategy.interval[-1]
        base = INTERVAL_TO_SECONDS[unit] * int(self.strategy.interval[:-1])
        days = (base * self.sample_size) / (60 * 60 * 24)
        self.return_per_day = round(self.cumulative_pnl / days, 2)

        logger.info(
            f"""
===Backtest Result===
Symbol: {self.symbol}
Interval: {self.strategy.interval}
Leverage: {self.strategy.leverage}
Size: {self.strategy.size * 100}%
Strategy: {self.strategy.__class__.__name__}
TP/SL: {self.strategy.take_profit_ratio * 100}% / {self.strategy.stop_loss_ratio * 100}%
Initial Balance: {BacktestConfig.BALANCE.value}USDT

Sample Size: {self.sample_size}
Trade Frequency: {self.trade_frequency}%
Win Rate: {self.win_rate}%

Cumulative PNL: {self.cumulative_pnl:.2f}USDT
Return per Day: {self.return_per_day:.2f}USDT
"""
        )
