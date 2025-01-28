import logging

from app.core.balance import Balance
from app.core.constant import OrderType
from app.core.order import Orders
from app.core.position import Positions


logger = logging.getLogger(__name__)


class TestResult:
    def __init__(self, symbol: str, sample_size: int):
        self.symbol = symbol
        self.sample_size = sample_size

        self.cumulative_pnl = 0.0
        self.trigger_count = 0
        self.winning_count = 0
        self.triggering_rate = 0.0
        self.winning_rate = 0.0

    def check_filled_order_backtest(
        self,
        positions: Positions,
        orders: Orders,
        entry_price: float,
        balance: Balance,
    ) -> None:
        if position := positions.first():
            filled_order = orders.find_all_by_type(
                OrderType.TAKE_PROFIT, OrderType.STOP
            ).filled_order(entry_price)

            if filled_order and (pnl := position.realized_pnl(filled_order)):
                initial_margin = position.calculate_initial_margin()
                roi = pnl / initial_margin * 100
                logger.info(f"Realized PNL: {round(pnl, 2)}USDT, ROI: {round(roi, 2)}%")

                self.update(
                    trigger_count=self.trigger_count + 1,
                    cumulative_pnl=self.cumulative_pnl + pnl,
                    winning_count=self.winning_count + (1 if pnl > 0 else 0),
                )
                balance = balance.update(pnl + initial_margin)
                orders.clear()
                positions.clear()

    def update(self, trigger_count: int, cumulative_pnl: float, winning_count: int):
        self.cumulative_pnl = cumulative_pnl
        self.trigger_count = trigger_count
        self.winning_count = winning_count
        self.triggering_rate = round(self.trigger_count / self.sample_size * 100, 2)
        self.winning_rate = round(self.winning_count / self.trigger_count * 100, 2)

    def __str__(self):
        return f"""
===Backtest Result===
Symbol: {self.symbol}
Sample Size: {self.sample_size}
Cumulative PNL: {self.cumulative_pnl:.2f}USDT
Trigger Count: {self.trigger_count}
Winning Count: {self.winning_count}
Triggering Rate: {self.triggering_rate}%
Winning Rate: {self.winning_rate}%
"""
