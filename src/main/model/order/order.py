from dataclasses import dataclass

from pandas import Timestamp

from model.constant import OrderType, PositionSide


@dataclass
class Order:
    """Represents a trading order."""
    symbol: str
    id: int
    type: OrderType
    side: PositionSide
    price: float
    quantity: float
    gtd: int = 0

    def __repr__(self) -> str:
        return f"\n\t{self.__class__.__name__}(symbol={self.symbol}, type={self.type}, side={self.side}, price={self.price})"

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args

    def is_expired(self, time: Timestamp) -> bool:
        return self.gtd < int(time.timestamp() * 1000) if self.gtd else False

    def is_filled(self, high: float, low: float) -> bool:
        if self.is_type(OrderType.MARKET):
            return True

        if self.is_type(OrderType.LIMIT):
            return (
                self.price >= low
                if self.side == PositionSide.BUY
                else self.price <= high
            )

        if self.is_type(OrderType.TAKE_PROFIT_MARKET):
            return (
                self.price <= high
                if self.side == PositionSide.SELL
                else self.price >= low
            )

        if self.is_type(OrderType.STOP_MARKET):
            return (
                self.price >= low
                if self.side == PositionSide.SELL
                else self.price <= high
            )

        return False
