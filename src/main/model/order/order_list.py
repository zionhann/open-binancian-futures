import logging
import textwrap
import uuid
from typing import Iterator

from pandas import Timestamp

from model.constant import OrderType, PositionSide
from .order import Order

LOGGER = logging.getLogger(__name__)


class OrderList:

    def __init__(self, orders: list[Order] | None = None) -> None:
        self.orders = orders if orders is not None else []

    def __iter__(self) -> Iterator[Order]:
        return iter(self.orders)

    def __len__(self) -> int:
        """Support len() - Pythonic replacement for size()."""
        return len(self.orders)

    def __bool__(self) -> bool:
        """Support boolean context - True if has orders, False if empty."""
        return bool(self.orders)

    def __contains__(self, order: Order) -> bool:
        """Support 'in' operator for membership testing."""
        return order in self.orders

    def __repr__(self) -> str:
        return str(self.orders)

    def has_type(self, *args: OrderType) -> bool:
        return any(order.is_type(*args) for order in self.orders)

    def add(self, *args: Order) -> None:
        """Add orders efficiently using extend."""
        self.orders.extend(args)

    def remove_by_id(self, id: int) -> None:
        """Remove order by ID using in-place filtering."""
        self.orders[:] = [order for order in self.orders if order.id != id]

    def clear(self) -> None:
        """Clear all orders using list.clear()."""
        LOGGER.debug(f"{len(self.orders)} ORDERS CLEARED")
        self.orders.clear()

    def find_all_by_type(self, *args: OrderType) -> "OrderList":
        orders = [order for order in self.orders if order.is_type(*args)]
        return OrderList(sorted(orders, key=lambda o: args.index(o.type)))

    def find_by_type(self, type: OrderType) -> "Order | None":
        """Find first order of given type using next()."""
        return next((o for o in self.orders if o.type == type), None)

    def open_order(
            self,
            symbol: str,
            type: OrderType,
            side: PositionSide,
            entry_price: float,
            entry_quantity: float,
            time: Timestamp,
            gtd_time: int = 0,
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
