import logging

from typing import Iterator
from app.core.constant import OrderType, PositionSide

logger = logging.getLogger(__name__)


class Orders:
    def __init__(self, orders: list["Order"] = []) -> None:
        self.orders = orders

    def __iter__(self) -> Iterator["Order"]:
        return iter(self.orders)

    def __str__(self) -> str:
        return str(self.orders)

    def has_type(self, *args: OrderType) -> bool:
        return any(
            order.is_type(order_type) for order in self.orders for order_type in args
        )

    def add(self, order: "Order") -> None:
        self.orders = [*self.orders, order]

    def remove_by_id(self, id: int) -> None:
        self.orders = [order for order in self.orders if order.id != id]

    def clear(self) -> None:
        self.orders = []

    def size(self) -> int:
        return len(self.orders)

    def find_all_by_type(self, *args: OrderType) -> "Orders":
        orders = [order for order in self.orders if order.is_type(*args)]
        return Orders(sorted(orders, key=lambda o: args.index(o.type)))

    def filled_orders_backtest(self, high: float, low: float) -> "Orders | None":
        return Orders(
            [
                order
                for order in self.find_all_by_type(
                    OrderType.LIMIT, OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET
                )
                if self._is_filled_backtest(order, high, low)
            ]
        )

    def _is_filled_backtest(self, order: "Order", high: float, low: float) -> bool:
        if order.is_type(OrderType.LIMIT, OrderType.TAKE_PROFIT_MARKET):
            return (
                order.price >= low
                if order.side == PositionSide.BUY
                else order.price <= high
            )
        if order.is_type(OrderType.STOP_MARKET):
            return (
                order.price >= low
                if order.side == PositionSide.SELL
                else order.price <= high
            )
        return False


class Order:
    def __init__(
        self,
        symbol: str,
        id: int,
        type: OrderType,
        side: PositionSide,
        price: float,
        quantity: float,
    ) -> None:
        self.symbol = symbol
        self.id = id
        self.type = type
        self.side = side
        self.price = price
        self.quantity = quantity

    def __repr__(self):
        return f"{__class__.__name__}(symbol={self.symbol}, type={self.type}, side={self.side}, price={self.price})"

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args
