import logging
import textwrap
import uuid
from typing import Iterator

from pandas import Timestamp

from model.constant import OrderType, PositionSide
from .order import Order

LOGGER = logging.getLogger(__name__)


class OrderList:

    def __init__(self, orders: list[Order] = []) -> None:
        self.orders = orders

    def __iter__(self) -> Iterator[Order]:
        return iter(self.orders)

    def __repr__(self) -> str:
        return str(self.orders)

    def has_type(self, *args: OrderType) -> bool:
        return any(order.is_type(*args) for order in self.orders)

    def add(self, *args: Order) -> None:
        self.orders = [*self.orders, *args]

    def remove_by_id(self, id: int) -> None:
        self.orders = [order for order in self.orders if order.id != id]

    def clear(self) -> None:
        LOGGER.debug(
            textwrap.dedent(f"""{len(self.orders)} ORDERS CLEARED""")
        )
        self.orders = []

    def size(self) -> int:
        return len(self.orders)

    def find_all_by_type(self, *args: OrderType) -> "OrderList":
        orders = [order for order in self.orders if order.is_type(*args)]
        return OrderList(sorted(orders, key=lambda o: args.index(o.type)))

    def find_by_type(self, type: OrderType) -> "Order | None":
        return next(filter(lambda o: o.type == type, self.orders), None)

    def open_order_(
            self,
            symbol: str,
            type: OrderType,
            side: PositionSide,
            entry_price: float,
            entry_quantity: float,
            time: Timestamp,
            gtd_time=0,
    ) -> None:
        order_id = uuid.uuid4().int
        self.add(
            Order(
                symbol=symbol,
                id=order_id,
                type=type,
                side=side,
                price=entry_price,
                quantity=entry_quantity,
                gtd=gtd_time,
            )
        )

        LOGGER.debug(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                OPEN {type.value} ORDER @ {entry_price}
                ID: {order_id}
                Symbol: {symbol}
                Side: {side.value}
                Quantity: {entry_quantity}
                GTD: {time}
                """
            )
        )
