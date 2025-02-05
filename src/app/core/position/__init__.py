import logging
import textwrap
from app.core.balance import Balance
from app.core.constant import AppConfig, OrderType, PositionSide
from app.core.order import Order


class Positions:
    _LOGGER = logging.getLogger(__name__)

    def __init__(self, positions: list["Position"] = []) -> None:
        self.positions = positions

    def clear(self) -> None:
        self.positions = []

    def first(self) -> "Position | None":
        return next((position for position in self.positions), None)

    def is_LONG(self) -> bool:
        return any(position.is_LONG() for position in self.positions)

    def is_SHORT(self) -> bool:
        return any(position.is_SHORT() for position in self.positions)

    def is_empty(self) -> bool:
        return not self.positions

    def open_position_backtest(
        self,
        balance: Balance,
        order: Order,
        time: str,
    ) -> Balance:
        position = Position(
            symbol=order.symbol,
            amount=order.quantity,
            price=order.price,
            side=order.side,
        )
        self.positions = [*self.positions, position]
        initial_margin = position.initial_margin()

        self._LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time}
                {order.side.value} {position.symbol}@{position.price}
                Type: {order.type.value}
                Size: {order.quantity}{position.symbol[:-4]}
                Margin: {initial_margin:.2f}USDT
                """
            )
        )
        return balance.update_backtest(-initial_margin)


class Position:
    def __init__(
        self, symbol: str, price: float, amount: float, side: PositionSide
    ) -> None:
        self.symbol = symbol
        self.price = price
        self.amount = abs(amount)
        self.side = side

    def is_LONG(self) -> bool:
        return self.side == PositionSide.BUY

    def is_SHORT(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / AppConfig.LEVERAGE.value

    def pnl_backtest(self, filled: Order) -> float:
        pnl = abs(self.price - filled.price) * self.amount
        return pnl if filled.type == OrderType.TAKE_PROFIT_MARKET else -pnl
