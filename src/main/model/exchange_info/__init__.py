from typing import Iterable

from model.balance import Balance
from model.constant import TPSL, AppConfig
from model.exchange_info.filter import Filter
from utils import decimal_places


class ExchangeInfo:
    def __init__(self, items: Iterable[dict]):
        self.filters = {
            item["symbol"]: Filter(item["filters"])
            for item in items
        }

    def to_entry_price(self, symbol: str, initial_price: float) -> float:
        tick_size = self.filters[symbol].tick_size
        decimals = decimal_places(tick_size)
        return round(int(initial_price / tick_size) * tick_size, decimals)

    def to_entry_quantity(
            self,
            symbol: str,
            entry_price: float,
            size: float,
            leverage: int,
            balance: Balance,
    ) -> float:
        step_size = self.filters[symbol].step_size
        initial_quantity = balance.calculate_quantity(entry_price, size, leverage)
        decimals = decimal_places(step_size)
        entry_quantity = round(int(initial_quantity / step_size) * step_size, decimals)
        return (
            entry_quantity
            if self._is_notional_enough(symbol, entry_quantity, entry_price)
            else 0.0
        )

    def trim_quantity_precision(self, symbol: str, quantity: float) -> float:
        step_size = self.filters[symbol].step_size
        decimals = decimal_places(step_size)
        return round(int(quantity / step_size) * step_size, decimals)

    def _is_notional_enough(
            self, symbol: str, entry_quantity: float, entry_price: float
    ) -> bool:
        return (
                self._notional(entry_quantity, entry_price)
                >= self.filters[symbol].min_notional
        )

    def _notional(self, entry_quantity: float, entry_price: float) -> float:
        """
        Estimate the notional value required for TP/SL orders.
        This guarantees that TP/SL orders can be placed without any nominal value issues.
        """
        max_tpsl = max(TPSL.TAKE_PROFIT_RATIO.value, TPSL.STOP_LOSS_RATIO.value)
        factor = 1 - (max_tpsl / AppConfig.LEVERAGE.value)
        return entry_quantity * (entry_price * factor)
