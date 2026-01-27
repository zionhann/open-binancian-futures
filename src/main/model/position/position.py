from dataclasses import dataclass, field

from model.constant import PositionSide, AppConfig


@dataclass
class Position:
    """Represents a trading position."""

    symbol: str
    price: float
    amount: float
    side: PositionSide
    leverage: int
    break_even_price: float | None = field(default=None, repr=True)

    def __post_init__(self) -> None:
        # Ensure amount is always positive
        self.amount = abs(self.amount)

        # Default break_even_price to entry price
        if self.break_even_price is None:
            self.break_even_price = self.price

    def __repr__(self) -> str:
        return (
            f"\n\t{self.__class__.__name__}("
            f"symbol={self.symbol}, side={self.side}, price={self.price}, "
            f"bep={self.break_even_price}, amount={self.amount}, leverage={self.leverage})"
        )

    def is_long(self) -> bool:
        return self.side == PositionSide.BUY

    def is_short(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / self.leverage

    def simple_pnl(self, target_price: float) -> float:
        price_diff = (
            target_price - self.price if self.is_long() else self.price - target_price
        )
        return price_diff * self.amount

    def roi(self, current_price: float) -> float:
        """Calculate return on investment for this position."""
        price_change = (
            current_price - self.price if self.is_long() else self.price - current_price
        )
        return (price_change / self.price) * self.leverage
