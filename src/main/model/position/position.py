import logging

from model.constant import PositionSide, AppConfig

LOGGER = logging.getLogger(__name__)


class Position:
    def __init__(
            self,
            symbol: str,
            price: float,
            amount: float,
            side: PositionSide,
            leverage: int | None = None,
            bep: float | None = None,
    ) -> None:
        self.symbol = symbol
        self.price = price
        self.break_even_price = bep or price
        self.amount = abs(amount)
        self.side = side
        self.leverage = leverage or AppConfig.LEVERAGE

    def __repr__(self) -> str:
        return f"\n\t{__class__.__name__}(symbol={self.symbol}, side={self.side}, price={self.price}, bep={self.break_even_price}, amount={self.amount}, leverage={self.leverage})"

    def is_long(self) -> bool:
        return self.side == PositionSide.BUY

    def is_short(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / self.leverage

    def simple_pnl(self, target_price: float) -> float:
        price_diff = target_price - self.price if self.is_long() else self.price - target_price
        return price_diff * self.amount
