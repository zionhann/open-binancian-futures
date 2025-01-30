import math

from app.core.constant import AppConfig


class Balance:
    def __init__(self, balance: float):
        self.balance = math.floor(balance * 100) / 100

    def __str__(self):
        return f"{self.balance:.2f}"

    def update(self, pnl: float) -> "Balance":
        return Balance(self.balance + pnl)

    def calculate_quantity(self, entry_price: float) -> float:
        initial_margin = self.balance * AppConfig.SIZE.value
        return initial_margin * AppConfig.LEVERAGE.value / entry_price
