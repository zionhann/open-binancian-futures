import math


class Balance:
    def __init__(self, balance: float):
        self._balance = math.floor(balance * 100) / 100

    def __str__(self):
        return f"{self._balance:.2f}"

    def calculate_quantity(
            self, entry_price: float, size: float, leverage: int
    ) -> float:
        initial_margin = self._balance * size
        return initial_margin * leverage / entry_price

    def update_backtesting(self, pnl: float) -> None:
        self._balance += pnl
