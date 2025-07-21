from model.constant import AppConfig
from utils import get_or_raise
from .position_list import PositionList


class PositionBook:
    def __init__(
            self,
            positions: dict[str, PositionList] = {
                s: PositionList() for s in AppConfig.SYMBOLS.value
            },
    ) -> None:
        self.positions = positions

    def __repr__(self) -> str:
        return str(self.positions)

    def get(self, symbol: str) -> PositionList:
        return get_or_raise(
            self.positions.get(symbol),
            lambda: KeyError(f"Position not found for symbol: {symbol}"),
        )
