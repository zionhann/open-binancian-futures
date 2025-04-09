import logging

import textwrap
from typing import Iterator
import uuid

from pandas import Timestamp

from app.core.constant import OrderType, PositionSide


class Orders:
    _LOGGER = logging.getLogger(__name__)

    def __init__(self, orders: list["Order"] = []) -> None:
        self.orders = orders

    def __iter__(self) -> Iterator["Order"]:
        return iter(self.orders)

    def __repr__(self) -> str:
        return str(self.orders)

    def has_type(self, *args: OrderType) -> bool:
        return any(order.is_type(*args) for order in self.orders)

    def add(self, *args: "Order") -> None:
        self.orders = [*self.orders, *args]

    def remove_by_id(self, id: int) -> None:
        self.orders = [order for order in self.orders if order.id != id]

    def clear(self) -> None:
        self.orders = []

    def size(self) -> int:
        return len(self.orders)

    def find_all_by_type(self, *args: OrderType) -> "Orders":
        orders = [order for order in self.orders if order.is_type(*args)]
        return Orders(sorted(orders, key=lambda o: args.index(o.type)))

    def find_by_type(self, type: OrderType) -> "Order | None":
        return next(filter(lambda o: o.type == type, self.orders), None)

    def rm_expired_orders_backtest(self, time: Timestamp) -> "Orders":
        for order in self.orders:
            if order.type == OrderType.LIMIT and order.is_expired(time):
                self._LOGGER.debug(
                    textwrap.dedent(
                        f"""
                        {order.type.value} ORDER EXPIRED @ {time}
                        Symbol: {order.symbol}
                        Side: {order.side.value}
                        Price: {order.price}
                        Quantity: {order.quantity}
                        """
                    )
                )
                return Orders()
        return self

    def filled_orders_backtest(self, high: float, low: float) -> "Orders | None":
        return Orders(
            [
                order
                for order in self.find_all_by_type(
                    OrderType.LIMIT, OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET
                )
                if self._is_filled_backtest(order, high, low)
            ],
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

    def open_order_backtest(
        self,
        symbol: str,
        type: OrderType,
        side: PositionSide,
        entry_price: float,
        entry_quantity: float,
        gtd_time=0,
    ) -> None:
        self.add(
            Order(
                symbol=symbol,
                id=uuid.uuid4().int,
                type=type,
                side=side,
                price=entry_price,
                quantity=entry_quantity,
                gtd=gtd_time,
            )
        )

        self._LOGGER.debug(
            textwrap.dedent(
                f"""
                OPEN {type.value} ORDER @ {entry_price}
                Symbol: {symbol}
                Side: {side.value}
                Quantity: {entry_quantity}
                GTD: {gtd_time}
                """
            )
        )


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
        return f"\n{__class__.__name__}(symbol={self.symbol}, type={self.type}, side={self.side}, price={self.price})"

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args

    def is_expired(self, time: Timestamp) -> bool:
        return self.gtd < int(time.timestamp() * 1000) if self.gtd else False
