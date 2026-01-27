from model.constant import AppConfig
from model.container import SymbolContainer
from .order_list import OrderList


class OrderBook(SymbolContainer[OrderList]):
    """Container for managing orders per symbol with bracket notation access.

    Example:
        >>> orders = OrderBook()
        >>> orders["BTCUSDT"].add(order)
        >>> len(orders["BTCUSDT"])
    """

    def __init__(self, orders: dict[str, OrderList] | None = None) -> None:
        if orders is not None:
            # Initialize from existing dict
            super().__init__(OrderList, [])
            self._items = orders
        else:
            super().__init__(OrderList, AppConfig.SYMBOLS)
