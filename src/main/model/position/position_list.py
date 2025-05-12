import logging
import textwrap

from pandas import Timestamp

from model.balance import Balance
from model.order import Order
from .position import Position

LOGGER = logging.getLogger(__name__)


class PositionList:
    def __init__(self, positions: list[Position] = []) -> None:
        self.positions: list[Position] = positions
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

    def update(self, positions: list[Position]) -> None:
        self.positions = positions

    def remove(self, position: Position) -> None:
        if position in self.positions:
            self.positions.remove(position)

    def open_position_backtesting(
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
        balance.update_backtesting(-position.initial_margin())

        LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {position.symbol} @ {position.price}
                Type: {order.type.value}
                Size: {order.quantity} {position.symbol[:-4]}
                """
            )
        )

    def reset_backtesting(self, balance: Balance) -> None:
        for position in self.positions:
            margin = position.initial_margin()
            balance.update_backtesting(margin)

            LOGGER.info(
                textwrap.dedent(
                    f"""
                    CLOSED {position.symbol}
                    Balance: {balance}
                    """
                )
            )
            self.remove(position)
