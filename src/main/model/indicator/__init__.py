from pandas import DataFrame

from utils import get_or_raise


class Indicator:
    def __init__(self, indicators: dict[str, DataFrame]) -> None:
        self.indicators = indicators

    def get(self, symbol: str) -> DataFrame:
        return get_or_raise(
            self.indicators.get(symbol),
            lambda: KeyError(f"Indicator not found for symbol: {symbol}"),
        )

    def update(self, symbol: str, df: DataFrame) -> None:
        self.indicators[symbol] = df
