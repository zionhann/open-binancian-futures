import logging
import textwrap

from pandas import Timestamp
from app.core.balance import Balance
from app.core.constant import OrderType, PositionSide
from app.core.order import Order


class Positions:
    _LOGGER = logging.getLogger(__name__)

    def __init__(self, positions: list["Position"] = []) -> None:
        self.positions: list["Position"] = positions
        self.averaging_count = 0 if not positions else 1

    def __repr__(self) -> str:
        return str(self.positions)

    def clear(self) -> None:
        self.positions = []
        self.averaging_count = 0

    def find_first(self) -> "Position | None":
        return next((position for position in self.positions), None)

    def is_LONG(self) -> bool:
        return any(position.is_LONG() for position in self.positions)

    def is_SHORT(self) -> bool:
        return any(position.is_SHORT() for position in self.positions)

    def is_empty(self) -> bool:
        return not self.positions

    def update(self, positions: list["Position"]) -> None:
        self.positions = positions

    def remove(self, position: "Position") -> None:
        if position in self.positions:
            self.positions.remove(position)

    def open_position_backtest(
        self, balance: Balance, order: Order, time: Timestamp, leverage: int
    ) -> None:
        position = Position(
            symbol=order.symbol,
            amount=order.quantity,
            price=order.price,
            side=order.side,
            leverage=leverage,
        )
        self.positions = [position]
        balance.update_backtest(-position.initial_margin())

        self._LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {position.symbol} @ {position.price}
                Type: {order.type.value}
                Size: {order.quantity} {position.symbol[:-4]}
                """
            )
        )

    def reset_backtest(self, balance: Balance) -> None:
        for position in self.positions:
            margin = position.initial_margin()
            balance.update_backtest(margin)

            self._LOGGER.info(
                textwrap.dedent(
                    f"""
                    CLOSED {position.symbol}
                    Balance: {balance}
                    """
                )
            )
            self.remove(position)


class Position:
    _LOGGER = logging.getLogger(__name__)

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
        return f"\n{__class__.__name__}(symbol={self.symbol}, side={self.side}, leverage={self.leverage}, price={self.price}, amount={self.amount})"

    def is_LONG(self) -> bool:
        return self.side == PositionSide.BUY

    def is_SHORT(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / self.leverage

    def pnl_backtest(self, filled: Order) -> float:
        pnl = abs(self.price - filled.price) * self.amount
        return pnl if filled.type == OrderType.TAKE_PROFIT_MARKET else -pnl

    def realized_pnl_backtest(
        self, balance: Balance, order: Order, time: Timestamp
    ) -> float:
        pnl, margin = self.pnl_backtest(order), self.initial_margin()
        balance.update_backtest(pnl + margin)

        self._LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {self.symbol} @ {order.price}
                Type: {order.type.value}
                Realized PNL: {pnl:.2f} USDT ({pnl / margin * 100:.2f}%)
                Balance: {balance}
                """
            )
        )
        return pnl
