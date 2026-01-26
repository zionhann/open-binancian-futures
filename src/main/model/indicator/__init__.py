from pandas import DataFrame

from model.container import SymbolContainer


class Indicator(SymbolContainer[DataFrame]):
    """Container for managing indicator DataFrames per symbol with bracket notation.

    Example:
        >>> indicators = Indicator({"BTCUSDT": df})
        >>> indicators["BTCUSDT"].iloc[-1]
        >>> indicators["BTCUSDT"] = new_df
    """

    def __init__(self, indicators: dict[str, DataFrame]) -> None:
        # Initialize with empty factory, then replace with provided dict
        super().__init__(DataFrame, [])
        self._items = indicators

    def update(self, symbol: str, df: DataFrame) -> None:
        """Update indicator DataFrame for a symbol. Equivalent to container[symbol] = df."""
        self[symbol] = df
