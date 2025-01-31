import logging

from app.core.constant import OrderType, PositionSide

logger = logging.getLogger(__name__)


class Orders:
    def __init__(self, orders: list["Order"] = []) -> None:
        self.orders = orders

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

    def find_all_by_type(self, *args: OrderType) -> "Orders":
        return Orders([order for order in self.orders if order.is_type(*args)])

    def find_first_by_type(self, *args: OrderType) -> "Order | None":
        return next((order for order in self.orders if order.is_type(*args)), None)

    def filled_order(self, current_price: float) -> "Order | None":
        for order in self.orders:
            if order.is_type(OrderType.TAKE_PROFIT) and (
                (order.side == PositionSide.SELL and order.price <= current_price)
                or (order.side == PositionSide.BUY and order.price >= current_price)
            ):
                return order
            elif order.is_type(OrderType.STOP) and (
                (order.side == PositionSide.SELL and order.price >= current_price)
                or (order.side == PositionSide.BUY and order.price <= current_price)
            ):
                return order
        return None

    def is_empty(self) -> bool:
        return not self.orders

    def is_not_empty(self) -> bool:
        return not self.is_empty()

    def size(self) -> int:
        return len(self.orders)

    def to_ids(self) -> list[int]:
        return [order.id for order in self.orders]


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

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args
