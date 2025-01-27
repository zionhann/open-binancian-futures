import math


class Balance:
    def __init__(self, balance: float):
        self.balance = math.floor(balance * 100) / 100

    def update(self, pnl: float):
        self.balance += pnl
        self.balance = math.floor(self.balance * 100) / 100

    def get(self) -> float:
        return self.balance
