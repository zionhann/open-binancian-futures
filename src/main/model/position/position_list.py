import logging

from .position import Position

LOGGER = logging.getLogger(__name__)


class PositionList:
    def __init__(self, positions: list[Position] | None = None) -> None:
        self.positions: list[Position] = positions if positions is not None else []
        self.entry_count = 0 if not self.positions else 1

    def __iter__(self):
        return iter(self.positions)

    def __len__(self) -> int:
        """Support len() - Pythonic way to get position count."""
        return len(self.positions)

    def __bool__(self) -> bool:
        """Support boolean context - True if has positions, False if empty."""
        return bool(self.positions)

    def __contains__(self, position: Position) -> bool:
        """Support 'in' operator for membership testing."""
        return position in self.positions

    def __repr__(self) -> str:
        if not self.positions:
            return "[]"
        return f"{__class__.__name__}(positions={self.positions}, entry_count={self.entry_count})"

    def clear(self) -> None:
        """Clear all positions using list.clear()."""
        self.positions.clear()
        self.entry_count = 0

    def find_first(self) -> "Position | None":
        return next((position for position in self.positions), None)

    def is_long(self) -> bool:
        return any(position.is_long() for position in self.positions)

    def is_short(self) -> bool:
        return any(position.is_short() for position in self.positions)

    def update_positions(self, positions: list[Position]) -> None:
        """Update positions using in-place slice assignment."""
        self.positions[:] = positions
