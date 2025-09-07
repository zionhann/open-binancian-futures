from model.constant import AppConfig
from utils import get_or_raise
from .position_list import PositionList


class PositionBook:
    def __init__(self, positions=None) -> None:
        self._positions = positions or {s: PositionList() for s in AppConfig.SYMBOLS}

    def __repr__(self) -> str:
        return str(self._positions)

    def get(self, symbol: str) -> PositionList:
        return get_or_raise(
            self._positions.get(symbol),
            lambda: KeyError(f"Position not found for symbol: {symbol}"),
        )
