from app.core.balance import Balance
from app.core.constant import TPSL, AppConfig, FilterType
from app.utils import decimal_places


class ExchangeInfo:
    def __init__(self, symbol: str, data: dict):
        try:
            filters = next(filter(lambda el: el["symbol"] == symbol, data))["filters"]
            filter_types = {f["filterType"]: f for f in filters}

            self.symbol = symbol
            self.tick_size = float(
                filter_types[FilterType.PRICE_FILTER.value]["tickSize"]
            )
            self.step_size = float(filter_types[FilterType.LOT_SIZE.value]["stepSize"])
            self.min_notional = int(
                filter_types[FilterType.MIN_NOTIONAL.value]["notional"]
            )
        except StopIteration:
            raise ValueError(f"Symbol {symbol} not found in exchange info")

    def to_entry_price(self, initial_price: float) -> float:
        decimals = decimal_places(self.tick_size)
        return round(int(initial_price / self.tick_size) * self.tick_size, decimals)

    def to_entry_quantity(
        self, entry_price: float, size: float, leverage: int, balance: Balance
    ) -> float:
        initial_quantity = balance.calculate_quantity(entry_price, size, leverage)
        decimals = decimal_places(self.step_size)
        entry_quantity = round(
            int(initial_quantity / self.step_size) * self.step_size, decimals
        )
        return (
            entry_quantity
            if self._is_notional_enough(entry_quantity, entry_price)
            else 0.0
        )

    def _is_notional_enough(self, entry_quantity: float, entry_price: float) -> bool:
        return self._notional(entry_quantity, entry_price) >= self.min_notional

    def _notional(self, entry_quantity: float, entry_price: float) -> float:
        """
        Estimate the notional value required for TP/SL orders.
        Since take profit ratio is usually higher than stop loss ratio, use take profit ratio on both positions.
        This guarantees that TP/SL orders can be placed without any nominal value issues.
        """
        factor = 1 - (TPSL.TAKE_PROFIT_RATIO.value / AppConfig.LEVERAGE.value)
        return entry_quantity * (entry_price * factor)
