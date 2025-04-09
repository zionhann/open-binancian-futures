import logging
import textwrap

from pandas import Series

from app.core.balance import Balance
from app.core.constant import (
    OrderType,
    INTERVAL_TO_SECONDS,
    PositionSide,
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

        self.hit_count_buy = 0
        self.hit_count_sell = 0

        self.win_count_buy = 0
        self.win_count_sell = 0

        self.trade_count_buy = 0
        self.trade_count_sell = 0

        self.cumulative_pnl = 0.0

    def check_filled_order_backtest(
        self,
        positions: Positions,
        orders: Orders,
        kline: Series,
        balance: Balance,
        leverage: int,
    ) -> None:
        self.sample_size += 1
        time, high, low = kline["Open_time"], kline["High"], kline["Low"]

        if filled_orders := orders.filled_orders_backtest(high, low):
            for order in filled_orders:
                if order.is_type(OrderType.LIMIT) and positions.is_empty():
                    positions.open_position_backtest(balance, order, time, leverage)
                    orders.remove_by_id(order.id)

                    self.hit_count_buy += 1 if order.side == PositionSide.BUY else 0
                    self.hit_count_sell += 1 if order.side == PositionSide.SELL else 0

                elif order.is_type(
                    OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET
                ) and (position := positions.find_first()):
                    pnl = position.realized_pnl_backtest(balance, order, time)
                    positions.clear()
                    orders.clear()

                    self.cumulative_pnl += pnl

                    if position.side == PositionSide.BUY:
                        self.trade_count_buy += 1
                        self.win_count_buy += 1 if pnl > 0 else 0
                    else:
                        self.trade_count_sell += 1
                        self.win_count_sell += 1 if pnl > 0 else 0

    def print(self):
        trade_count = self.trade_count_buy + self.trade_count_sell
        hit_count = self.hit_count_buy + self.hit_count_sell
        win_count = self.win_count_buy + self.win_count_sell

        hit_rate = round(hit_count / self.sample_size * 100, 2)
        win_rate = round(win_count / trade_count * 100 if trade_count else 0, 2)

        buy_rate = round(self.hit_count_buy / hit_count * 100 if hit_count else 0, 2)
        sell_rate = round(self.hit_count_sell / hit_count * 100 if hit_count else 0, 2)

        buy_win_rate = round(
            (
                self.win_count_buy / self.trade_count_buy * 100
                if self.trade_count_buy
                else 0
            ),
            2,
        )
        sell_win_rate = round(
            (
                self.win_count_sell / self.trade_count_sell * 100
                if self.trade_count_sell
                else 0
            ),
            2,
        )

        unit = self.strategy.interval[-1]
        base = INTERVAL_TO_SECONDS[unit] * int(self.strategy.interval[:-1])
        days = (base * self.sample_size) / (60 * 60 * 24)
        return_per_day = round(self.cumulative_pnl / days, 2)

        logger.info(
            textwrap.dedent(
                f"""
                ===Backtest Result===
                Symbol: {self.symbol}
                Hit Rate: {hit_rate}% ({hit_count}/{self.sample_size})
                - BUY Hit Rate: {buy_rate}% ({self.hit_count_buy}/{hit_count})
                - SELL Hit Rate: {sell_rate}% ({self.hit_count_sell}/{hit_count})
                
                Win Rate: {win_rate}% ({win_count}/{trade_count})
                - BUY Win Rate: {buy_win_rate}% ({self.win_count_buy}/{self.trade_count_buy})
                - SELL Win Rate: {sell_win_rate}% ({self.win_count_sell}/{self.trade_count_sell})

                Cumulative PNL: {self.cumulative_pnl:.2f} USDT
                Return per Day: {return_per_day:.2f} USDT
                """
            )
        )
