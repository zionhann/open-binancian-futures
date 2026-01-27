from typing import Iterable

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponseSymbolsInner,
)

from model.balance import Balance
from model.constant import Bracket, AppConfig
from model.exchange_info.filter import Filter
from utils import decimal_places


class ExchangeInfo:
    """
    Exchange trading rules and filters for symbols.

    Handles price/quantity precision, minimum notional validation,
    and ensures orders comply with exchange requirements.
    """

    def __init__(self, items: Iterable[ExchangeInformationResponseSymbolsInner]):
        """
        Initialize ExchangeInfo from Binance API response.

        Args:
            items: Exchange information for each symbol
        """
        self.filters: dict[str, Filter] = {
            item.symbol: Filter.from_binance(item.filters)
            for item in items
            if item.symbol and item.filters
        }

    def to_entry_price(self, symbol: str, initial_price: float) -> float:
        """
        Round price to match exchange tick size precision.

        The exchange requires prices to be multiples of tick_size.
        This method ensures the price has the correct decimal precision.

        Args:
            symbol: Trading pair symbol
            initial_price: Desired entry price

        Returns:
            Price rounded to tick_size precision

        Example:
            >>> # If tick_size is 0.01, price must have 2 decimals
            >>> exchange_info.to_entry_price("BTCUSDT", 50123.456)
            50123.46
        """
        tick_size = self.filters[symbol].tick_size
        decimals = decimal_places(tick_size)
        return (
            initial_price
            if decimals == decimal_places(initial_price)
            else round(initial_price, decimals)
        )

    def to_entry_quantity(
            self,
            symbol: str,
            entry_price: float,
            size: float,
            leverage: int,
            balance: Balance,
    ) -> float:
        """
        Calculate order quantity that meets exchange requirements.

        Ensures quantity:
        1. Is a multiple of step_size (precision requirement)
        2. Meets minimum notional value (prevents dust orders)
        3. Accounts for balance, size, and leverage

        Args:
            symbol: Trading pair symbol
            entry_price: Order entry price
            size: Position size as fraction of balance (e.g., 0.1 = 10%)
            leverage: Trading leverage
            balance: Available balance

        Returns:
            Valid order quantity, or 0.0 if notional requirement not met

        Example:
            >>> # With $100 balance, 0.1 size, 10x leverage at $50k BTC
            >>> exchange_info.to_entry_quantity("BTCUSDT", 50000, 0.1, 10, balance)
            0.002  # $10 * 10x / $50k = 0.002 BTC
        """
        step_size = self.filters[symbol].step_size
        initial_quantity = balance.calculate_quantity(entry_price, size, leverage)
        decimals = decimal_places(step_size)

        # Round down to nearest step_size multiple
        entry_quantity = round(int(initial_quantity / step_size) * step_size, decimals)

        # Validate minimum notional value
        return (
            entry_quantity
            if self._is_notional_enough(symbol, entry_quantity, entry_price)
            else 0.0
        )

    def trim_quantity_precision(self, symbol: str, quantity: float) -> float:
        """
        Round quantity to match exchange step_size precision.

        Similar to to_entry_price(), but for quantity values.
        The exchange requires quantities to be multiples of step_size.

        Args:
            symbol: Trading pair symbol
            quantity: Desired quantity

        Returns:
            Quantity rounded to step_size precision

        Example:
            >>> # If step_size is 0.001, quantity must have 3 decimals
            >>> exchange_info.trim_quantity_precision("BTCUSDT", 0.12345)
            0.123
        """
        step_size = self.filters[symbol].step_size
        decimals = decimal_places(step_size)
        return (
            quantity
            if decimals == decimal_places(quantity)
            else round(quantity, decimals)
        )

    def _is_notional_enough(
            self, symbol: str, entry_quantity: float, entry_price: float
    ) -> bool:
        """
        Check if order meets minimum notional value requirement.

        Args:
            symbol: Trading pair symbol
            entry_quantity: Order quantity
            entry_price: Order price

        Returns:
            True if notional value meets exchange minimum, False otherwise
        """
        return (
                self._calculate_notional(entry_quantity, entry_price)
                >= self.filters[symbol].min_notional
        )

    def _calculate_notional(self, entry_quantity: float, entry_price: float) -> float:
        """
        Calculate conservative notional value accounting for TP/SL orders.

        When opening a position, TP/SL orders are placed at different prices.
        This method calculates a conservative notional value that ensures
        even the TP/SL orders meet the minimum notional requirement.

        The calculation accounts for the worst-case scenario where:
        - Stop-loss reduces position value by max(TAKE_PROFIT_RATIO, STOP_LOSS_RATIO)
        - Leverage affects the actual notional value

        Args:
            entry_quantity: Order quantity
            entry_price: Order entry price

        Returns:
            Conservative notional value that accounts for TP/SL

        Example:
            >>> # With 10% SL, 20% TP, 10x leverage
            >>> # Factor = 1 - (20% / 10) = 0.98
            >>> # Notional = quantity * (price * 0.98)
            >>> exchange_info._calculate_notional(0.1, 50000)
            4900.0  # Conservative estimate
        """
        max_tpsl = max(Bracket.TAKE_PROFIT_RATIO, Bracket.STOP_LOSS_RATIO)
        factor = 1 - (max_tpsl / AppConfig.LEVERAGE)
        return entry_quantity * (entry_price * factor)
