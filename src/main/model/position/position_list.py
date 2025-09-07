import logging

from .position import Position

LOGGER = logging.getLogger(__name__)


class PositionList:
    def __init__(self, positions: list[Position] = []) -> None:
        self.positions: list[Position] = positions
        self.entry_count = 0 if not positions else 1

    def __iter__(self):
        return iter(self.positions)

    def __repr__(self) -> str:
        if not self.positions:
            return "[]"
        return f"{__class__.__name__}(positions={self.positions}, entry_count={self.entry_count})"

    def clear(self) -> None:
        self.positions = []
        self.entry_count = 0

    def find_first(self) -> "Position | None":
        return next((position for position in self.positions), None)

    def is_LONG(self) -> bool:
        return any(position.is_long() for position in self.positions)

    def is_SHORT(self) -> bool:
        return any(position.is_short() for position in self.positions)

    def is_empty(self) -> bool:
        return not self.positions

    def update_positions(self, positions: list[Position]) -> None:
        self.positions = positions
