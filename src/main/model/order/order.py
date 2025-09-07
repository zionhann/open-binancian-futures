from pandas import Timestamp

from model.constant import OrderType, PositionSide


class Order:
    def __init__(
            self,
            symbol: str,
            id: int,
            type: OrderType,
            side: PositionSide,
            price: float,
            quantity: float,
            gtd: int = 0,
    ) -> None:
        self.symbol = symbol
        self.id = id
        self.type = type
        self.side = side
        self.price = price
        self.quantity = quantity
        self.gtd = gtd

    def __repr__(self) -> str:
        return f"\n\t{__class__.__name__}(symbol={self.symbol}, type={self.type}, side={self.side}, price={self.price})"

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args

    def is_expired(self, time: Timestamp) -> bool:
        return self.gtd < int(time.timestamp() * 1000) if self.gtd else False

    def is_filled_(self, high, low) -> bool:
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
