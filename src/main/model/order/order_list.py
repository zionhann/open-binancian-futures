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
        self.orders = []

    def size(self) -> int:
        return len(self.orders)

    def find_all_by_type(self, *args: OrderType) -> "OrderList":
        orders = [order for order in self.orders if order.is_type(*args)]
        return OrderList(sorted(orders, key=lambda o: args.index(o.type)))

    def find_by_type(self, type: OrderType) -> "Order | None":
        return next(filter(lambda o: o.type == type, self.orders), None)

    def remove_expired_orders_backtesting(self, time: Timestamp) -> None:
        for order in self.orders:
            if order.type == OrderType.LIMIT and order.is_expired(time):
                LOGGER.debug(
                    textwrap.dedent(
                        f"""
                        {order.type.value} ORDER EXPIRED @ {time}
                        ID: {order.id}
                        Symbol: {order.symbol}
                        Side: {order.side.value}
                        Price: {order.price}
                        Quantity: {order.quantity}
                        """
                    )
                )
                self.clear()

    def filled_orders_backtesting(self, high: float, low: float) -> "OrderList":
        return OrderList(
            [
                order
                for order in self.find_all_by_type(
                OrderType.LIMIT, OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET
            )
                if self._is_filled_backtesting(order, high, low)
            ],
        )

    def _is_filled_backtesting(self, order: "Order", high: float, low: float) -> bool:
        if order.is_type(OrderType.LIMIT):
            return (
                order.price >= low
                if order.side == PositionSide.BUY
                else order.price <= high
            )

        if order.is_type(OrderType.TAKE_PROFIT_MARKET):
            return (
                order.price <= high
                if order.side == PositionSide.SELL
                else order.price >= low
            )

        if order.is_type(OrderType.STOP_MARKET):
            return (
                order.price >= low
                if order.side == PositionSide.SELL
                else order.price <= high
            )
        return False

    def open_order_backtest(
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
