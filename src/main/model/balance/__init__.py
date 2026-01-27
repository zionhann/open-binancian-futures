import math
from dataclasses import dataclass


@dataclass
class Balance:
    _balance: float

    def __init__(self, balance: float):
        # Floor to 2 decimal places for precision
        self._balance = math.floor(balance * 100) / 100

    def __str__(self):
        return f"{self._balance:.2f}"

    @property
    def balance(self) -> float:
        """Get current balance value."""
        return self._balance

    def calculate_quantity(
            self, entry_price: float, size: float, leverage: int
    ) -> float:
        initial_margin = self._balance * size
        return initial_margin * leverage / entry_price

    def add_pnl(self, pnl: float) -> None:
        self._balance += pnl
