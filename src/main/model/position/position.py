import logging
import textwrap

from pandas import Timestamp

from model.balance import Balance
from model.constant import PositionSide
from model.order import Order

LOGGER = logging.getLogger(__name__)


class Position:
    def __init__(
            self,
            symbol: str,
            price: float,
            amount: float,
            side: PositionSide,
            leverage: int,
            bep: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.price = price
        self.break_even_price = bep or price
        self.amount = abs(amount)
        self.side = side
        self.leverage = leverage

    def __repr__(self) -> str:
        return f"\n\t{__class__.__name__}(symbol={self.symbol}, side={self.side}, leverage={self.leverage}, price={self.price}, amount={self.amount})"

    def is_LONG(self) -> bool:
        return self.side == PositionSide.BUY

    def is_SHORT(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / self.leverage

    def pnl_backtesting(self, filled: Order) -> float:
        price_diff = filled.price - self.price if self.is_LONG() else self.price - filled.price
        return price_diff * self.amount

    def realized_pnl_backtesting(
            self, balance: Balance, order: Order, time: Timestamp
    ) -> float:
        pnl, margin = self.pnl_backtesting(order), self.initial_margin()
        balance.update_backtesting(pnl + margin)

        LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {self.symbol} @ {order.price}
                ID: {order.id}
                Type: {order.type.value}
                Realized PNL: {pnl:.2f} USDT ({pnl / margin * 100:.2f}%)
                Balance: {balance}
                """
            )
        )
        return pnl
