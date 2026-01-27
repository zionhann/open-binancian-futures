from model.constant import AppConfig
from model.container import SymbolContainer
from .position_list import PositionList


class PositionBook(SymbolContainer[PositionList]):
    """Container for managing positions per symbol with bracket notation access.

    Example:
        >>> positions = PositionBook()
        >>> positions["BTCUSDT"].is_long()
        >>> len(positions)
    """

    def __init__(self, positions: dict[str, PositionList] | None = None) -> None:
        if positions is not None:
            # Initialize from existing dict
            super().__init__(PositionList, [])
            self._items = positions
        else:
            super().__init__(PositionList, AppConfig.SYMBOLS)
