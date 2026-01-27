"""Generic container base class for symbol-keyed collections.

This module provides a Pythonic dict-like container with bracket notation access
for managing symbol-based data structures like OrderBook, PositionBook, and Indicator.
"""

from collections.abc import MutableMapping, Iterator
from typing import Generic, TypeVar, Callable

T = TypeVar('T')


class SymbolContainer(MutableMapping[str, T], Generic[T]):
    """Base container for symbol-keyed collections with bracket notation access.

    This class implements the MutableMapping protocol, allowing natural dict-like
    access patterns:

        container[symbol]          # Get item
        container[symbol] = value  # Set item
        del container[symbol]      # Delete item
        len(container)             # Get count
        for symbol in container:   # Iterate

    Args:
        default_factory: Callable that creates default values for each symbol.
        symbols: List of symbol strings to initialize the container with.

    Example:
        >>> orders = SymbolContainer(OrderList, ["BTCUSDT", "ETHUSDT"])
        >>> orders["BTCUSDT"].add(order)
        >>> len(orders)
        2
    """

    def __init__(self, default_factory: Callable[[], T], symbols: list[str]) -> None:
        self._items: dict[str, T] = {s: default_factory() for s in symbols}
        self._default_factory = default_factory

    def __getitem__(self, symbol: str) -> T:
        if symbol not in self._items:
            raise KeyError(f"Symbol not found: {symbol}")
        return self._items[symbol]

    def __setitem__(self, symbol: str, value: T) -> None:
        self._items[symbol] = value

    def __delitem__(self, symbol: str) -> None:
        del self._items[symbol]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({list(self._items.keys())})"

    # Backwards compatibility - deprecate in future
    def get(self, symbol: str) -> T:
        """Get item by symbol. Prefer bracket notation: container[symbol].

        This method is maintained for backwards compatibility but bracket
        notation should be preferred for new code.
        """
        return self[symbol]

    def update_item(self, symbol: str, value: T) -> None:
        """Update item by symbol. Prefer bracket notation: container[symbol] = value."""
        self[symbol] = value
