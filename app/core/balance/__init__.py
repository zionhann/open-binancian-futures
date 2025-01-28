import math

from app.core.constant import MIN_NOTIONAL, TPSL, AppConfig


class Balance:
    def __init__(self, balance: float):
        self.balance = math.floor(balance * 100) / 100

    def update(self, pnl: float) -> "Balance":
        return Balance(self.balance + pnl)

    def calculate_quantity(self, symbol: str, entry_price: float) -> float:
        initial_margin = self.balance * AppConfig.SIZE.value
        quantity = initial_margin * AppConfig.LEVERAGE.value / entry_price
        position_amount = math.floor(quantity * 1000) / 1000

        """
        Estimate the minimum notional value required for TP/SL orders.
        Since take profit ratio is usually higher than stop loss ratio, use take profit ratio on both positions.
        This guarantees that TP/SL orders can be placed without any nominal value issues.
        """
        factor = 1 - (TPSL.TAKE_PROFIT.value / AppConfig.LEVERAGE.value)
        notional = position_amount * (entry_price * factor)

        return position_amount if notional >= MIN_NOTIONAL[symbol] else 0.0
