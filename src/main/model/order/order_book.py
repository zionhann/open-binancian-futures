from model.constant import AppConfig
from utils import get_or_raise
from .order_list import OrderList


class OrderBook:
    def __init__(
            self,
            orders: dict[str, OrderList] = {
                s: OrderList() for s in AppConfig.SYMBOLS.value
            },
    ) -> None:
        self.orders = orders

    def get(self, symbol: str) -> OrderList:
        return get_or_raise(
            self.orders.get(symbol, None),
            lambda: KeyError(f"Order not found for symbol: {symbol}"),
        )
