from app.core.balance import Balance
from app.core.constant import AppConfig, OrderType, PositionSide
from app.core.order import Order


class Positions:
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
        symbol: str,
        quantity: float,
        price: float,
        side: PositionSide,
    ) -> None:
        position = Position(
            symbol=symbol,
            amount=quantity if side == PositionSide.BUY else -quantity,
            entry_price=price,
        )
        self.positions = [*self.positions, position]

        initial_margin = position.calculate_initial_margin()
        balance = balance.update(-initial_margin)


class Position:
    def __init__(self, symbol: str, entry_price: float, amount: float) -> None:
        self.side = PositionSide.BUY if amount > 0 else PositionSide.SELL
        self.symbol = symbol
        self.entry_price = entry_price
        self.amount = abs(amount)

    def is_LONG(self) -> bool:
        return self.side == PositionSide.BUY

    def is_SHORT(self) -> bool:
        return self.side == PositionSide.SELL

    def realized_pnl(self, filled: Order) -> float | None:
        pnl = abs(self.entry_price - filled.price) * self.amount
        return pnl if filled.type == OrderType.TAKE_PROFIT else -pnl

    def calculate_initial_margin(self) -> float:
        return self.amount * self.entry_price / AppConfig.LEVERAGE.value
