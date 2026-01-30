import asyncio
import importlib
import importlib.util
import inspect
import logging
import os
import sys
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass
import traceback

import pandas as pd
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    KlineCandlestickStreamsResponseK,
    AccountUpdateAPInner,
    AccountUpdateABInner,
)
from pandas import DataFrame

from .models import Balance
from .constants import settings
from .types import OrderType, PositionSide, TimeInForce
from .models import ExchangeInfo
from .models import Indicator
from .models import OrderBook, OrderEvent
from .models import Position, PositionBook
from . import exchange as futures
from .utils import decimal_places, fetch, get_or_raise
from .webhook import Webhook

BASIC_COLUMNS = [
    "Open_time",
    "Symbol",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
]


# Custom exceptions for strategy loading
class StrategyLoadError(Exception):
    """Base exception for strategy loading failures."""

    pass


class StrategyNotFoundError(StrategyLoadError):
    """Strategy file or module not found."""

    pass


@dataclass
class StrategyContext:
    """Context object containing all dependencies for a strategy."""

    client: DerivativesTradingUsdsFutures
    exchange_info: ExchangeInfo | None = None
    balance: Balance | None = None
    orders: OrderBook | None = None
    positions: PositionBook | None = None
    webhook: Webhook | None = None
    indicators: Indicator | None = None
    lock: asyncio.Lock | None = None


class Strategy(ABC):
    LOGGER = logging.getLogger(__name__)

    @staticmethod
    def of(name: str | None, context: StrategyContext) -> "Strategy":
        """
        Factory method to create a strategy instance.

        Args:
            name: Strategy name (file name without .py extension)
            context: StrategyContext containing all dependencies

        Returns:
            Strategy instance

        Raises:
            ValueError: If strategy name is None
            StrategyLoadError: If strategy cannot be loaded or instantiated
        """
        if name is None:
            raise ValueError("Strategy name is required")

        try:
            strategy_class = Strategy._import_strategy(name)
            return strategy_class(
                client=context.client,
                exchange_info=context.exchange_info,
                balance=context.balance,
                orders=context.orders,
                positions=context.positions,
                webhook=context.webhook,
                indicators=context.indicators,
                lock=context.lock,
            )
        except StrategyLoadError:
            Strategy.LOGGER.error(f"Failed to load strategy '{name}'")
            raise
        except TypeError as e:
            raise StrategyLoadError(
                f"Strategy '{name}' cannot be instantiated. "
                f"Check __init__ signature: {e}"
            ) from e
        except Exception as e:
            Strategy.LOGGER.error(f"Unexpected error loading strategy '{name}': {e}")
            Strategy.LOGGER.debug(traceback.format_exc())
            raise StrategyLoadError(f"Failed to load strategy '{name}': {e}") from e

    @staticmethod
    def _import_strategy(strategy_name: str) -> type["Strategy"]:
        """
        Import strategy from a file path and return the Strategy subclass.

        Args:
            strategy_name: File path (with or without .py extension)

        Returns:
            Strategy subclass ready for instantiation

        Raises:
            StrategyNotFoundError: Strategy file not found
            StrategyLoadError: Strategy cannot be loaded or has no valid subclass
        """
        # Ensure .py extension
        if not strategy_name.endswith(".py"):
            strategy_name += ".py"

        abs_path = os.path.abspath(strategy_name)
        if not os.path.exists(abs_path):
            raise StrategyNotFoundError(f"Strategy file not found: {abs_path}")

        module_name = os.path.basename(abs_path)[:-3]
        dir_name = os.path.dirname(abs_path)

        # Load module from file
        try:
            sys.path.insert(0, dir_name)
            spec = importlib.util.spec_from_file_location(module_name, abs_path)
            if not spec or not spec.loader:
                raise StrategyLoadError(f"Cannot load strategy from {abs_path}")

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except StrategyLoadError:
            raise
        except Exception as e:
            raise StrategyLoadError(
                f"Error loading strategy '{strategy_name}': {e}"
            ) from e
        finally:
            try:
                sys.path.remove(dir_name)
            except ValueError:
                pass

        # Find non-abstract Strategy subclass
        subclasses = [
            cls
            for cls in mod.__dict__.values()
            if isinstance(cls, type)
            and issubclass(cls, Strategy)
            and cls is not Strategy
            and not inspect.isabstract(cls)
        ]

        if not subclasses:
            raise StrategyLoadError(f"No valid Strategy subclass found in '{strategy_name}'")

        if len(subclasses) > 1:
            Strategy.LOGGER.warning(
                f"Multiple Strategy subclasses found in {strategy_name}. "
                f"Using '{subclasses[0].__name__}'"
            )

        return subclasses[0]

    def __init__(
        self,
        client: DerivativesTradingUsdsFutures,
        exchange_info: ExchangeInfo | None,
        balance: Balance | None,
        orders: OrderBook | None,
        positions: PositionBook | None,
        webhook: Webhook | None,
        indicators: Indicator | None,
        lock: asyncio.Lock | None = None,
    ) -> None:
        self.client = client
        self.exchange_info = exchange_info or futures.init_exchange_info()
        self.balance = balance or futures.init_balance()
        self.orders = orders or futures.init_orders()
        self.positions = positions or futures.init_positions()
        self.webhook = webhook or Webhook.of(url=None)
        self.indicators = indicators or futures.init_indicators()
        self.add_indicators(self.indicators)
        self.lock = lock
        self._realized_profit = {s: 0.0 for s in settings.symbols_list}

    def add_indicators(self, indicator: Indicator) -> None:
        """Load custom indicators for all symbols."""
        for symbol in settings.symbols_list:
            # Use bracket notation for cleaner, more Pythonic access
            indicator[symbol] = self.load(indicator[symbol][BASIC_COLUMNS])

            self.LOGGER.info(
                f"Loaded indicators for {symbol}:\n{indicator[symbol].tail().to_string(index=False)}"
            )

    def calculate_stop_price(
        self,
        symbol: str,
        entry_price: float,
        order_type: OrderType,
        stop_side: PositionSide,
        ratio: float,
    ) -> float:
        """
        Calculate stop price for TP/SL orders based on order type and position side.

        The price moves in opposite direction for stop-loss vs take-profit,
        and the direction depends on whether we're closing a long or short position.
        """
        sl_orders = {OrderType.STOP_MARKET, OrderType.STOP_LIMIT}
        tp_orders = {OrderType.TAKE_PROFIT_MARKET, OrderType.TAKE_PROFIT_LIMIT}

        # Normalize ratios by leverage
        _ratio = ratio / settings.leverage

        # Calculate price movement direction
        # SL SELL (close long) or TP BUY (close short) → price decreases (1 - ratio)
        # SL BUY (close short) or TP SELL (close long) → price increases (1 + ratio)
        should_decrease_price = (
            order_type in sl_orders and stop_side == PositionSide.SELL
        ) or (order_type in tp_orders and stop_side == PositionSide.BUY)

        factor = (1 - _ratio) if should_decrease_price else (1 + _ratio)

        return self.exchange_info.to_entry_price(symbol, entry_price * factor)

    def set_tpsl(
        self,
        symbol: str,
        position_side: PositionSide,
        order_type: OrderType,
        ratio: float,
        price: float | None = None,
        stop_price: float | None = None,
        close_position: bool | None = None,
        quantity: float | None = None,
    ) -> None:
        reduce_only = None if close_position else True
        _quantity = quantity if reduce_only else None
        _stop_price = stop_price or self.calculate_stop_price(
            symbol=symbol,
            entry_price=get_or_raise(
                price,
                lambda: ValueError("Either price or stop_price must be provided"),
            ),
            order_type=order_type,
            stop_side=position_side,
            ratio=ratio,
        )
        _price = _stop_price if reduce_only else None

        fetch(
            self.client.rest_api.new_algo_order,
            algo_type="CONDITIONAL",
            symbol=symbol,
            side=position_side.value,
            type=order_type.value,
            price=_price,
            trigger_price=self.exchange_info.to_entry_price(symbol, _stop_price),
            quantity=_quantity,
            close_position=close_position,
            time_in_force=TimeInForce.GTE_GTC.value,
            reduce_only=reduce_only,
        )

    def set_trailing_stop(
        self,
        symbol: str,
        position_side: PositionSide,
        quantity: float,
        callback_ratio: float,
        activation_price: float | None = None,
    ) -> None:
        _activation_price = (
            self.exchange_info.to_entry_price(symbol, activation_price)
            if activation_price
            else None
        )
        _callback_ratio = callback_ratio / settings.leverage * 100
        max_cb_ratio = min(100 // settings.leverage, 10)
        safe_cb_ratio = round(min(max(0.1, _callback_ratio), max_cb_ratio), 2)

        fetch(
            self.client.rest_api.new_algo_order,
            algo_type="CONDITIONAL",
            symbol=symbol,
            side=position_side.value,
            type=OrderType.TRAILING_STOP_MARKET.value,
            quantity=quantity,
            time_in_force=TimeInForce.GTE_GTC.value,
            reduce_only=True,
            activate_price=_activation_price,
            callback_rate=safe_cb_ratio,
        )

    async def on_new_candlestick(self, data: KlineCandlestickStreamsResponseK) -> None:
        if not get_or_raise(data.x):
            return

        open_time = (
            pd.to_datetime(get_or_raise(data.t), unit="ms")
            .tz_localize("UTC")
            .tz_convert(settings.timezone)
        )
        symbol = get_or_raise(data.s)
        new_data = {
            "Open_time": open_time,
            "Symbol": symbol,
            "Open": float(get_or_raise(data.o)),
            "High": float(get_or_raise(data.h)),
            "Low": float(get_or_raise(data.l)),
            "Close": float(get_or_raise(data.c)),
            "Volume": float(get_or_raise(data.v)),
        }
        new_df = pd.DataFrame([new_data], index=[open_time])
        df = pd.concat([self.indicators[symbol], new_df])
        self.indicators[symbol] = self.load(df)

        self.LOGGER.info(
            f"Updated indicators for {symbol}:\n{self.indicators[symbol].tail().to_string(index=False)}"
        )
        await self.run(symbol)

    def on_position_update(self, data: AccountUpdateAPInner) -> None:
        symbol, price, amount, bep = (
            get_or_raise(data.s),
            float(get_or_raise(data.ep)),
            float(get_or_raise(data.pa)),
            float(get_or_raise(data.bep)),
        )

        if price == 0.0 or amount == 0.0:
            self.positions[symbol].clear()
            return

        self.positions[symbol].update_positions(
            [
                Position(
                    symbol=symbol,
                    price=price,
                    amount=amount,
                    side=(PositionSide.BUY if amount > 0 else PositionSide.SELL),
                    leverage=settings.leverage,
                    break_even_price=bep,
                )
            ]
        )
        self.LOGGER.info(f"Updated positions: {self.positions[symbol]}")

    def on_balance_update(self, data: AccountUpdateABInner) -> None:
        if data.a == "USDT":
            self.balance = Balance(float(get_or_raise(data.cw)))
            self.LOGGER.info(f"Updated available USDT: {self.balance}")

    def accumulate_realized_profit(self, symbol: str, profit: float) -> None:
        """Accumulate realized profit for partial fills."""
        self._realized_profit[symbol] += profit

    def on_new_order(self, event: OrderEvent) -> None:
        if event.can_convert_to_order():
            self.orders[event.symbol].add(event.to_order())

        valid_price = event.price or event.stop_price

        self.LOGGER.info(
            textwrap.dedent(
                f"""
                Opened {event.display_order_type} order @ {valid_price}
                Symbol: {event.symbol}
                Side: {event.side.value if event.side else "N/A"}
                Quantity: {event.quantity or "N/A"}
                Reduce-only: {event.is_reduce_only}
                """
            )
        )

    def on_triggered_algo(self, event: OrderEvent) -> None:
        self.orders[event.symbol].remove_by_id(event.order_id)
        self.LOGGER.info(
            f"{event.display_order_type} order for {event.symbol} has been triggered."
        )

    def on_filled_order(self, event: OrderEvent) -> None:
        price = event.price or 0.0
        filled = event.filled or 0.0
        quantity = event.quantity or 0.0
        average_price = round(event.average_price or 0.0, decimal_places(price))
        filled_percentage = (filled / quantity * 100) if quantity else 0.0
        side_str = event.side.value if event.side else "N/A"
        realized_profit = self._realized_profit[event.symbol]

        self.LOGGER.info(
            textwrap.dedent(
                f"""
                {event.display_order_type} order has been filled @ {average_price}
                Symbol: {event.symbol}
                Side: {side_str}
                Quantity: {filled}/{quantity} {event.symbol[:-4]} ({filled_percentage:.2f}%)
                Realized Profit: {realized_profit} USDT
                """
            )
        )

        self.webhook.send_message(
            message=textwrap.dedent(
                f"""
                [{event.status.value}] {event.symbol[:-4]} {side_str} {filled} @ {average_price}
                Order: {event.display_order_type}
                PNL: {realized_profit} USDT
                """
            )
        )
        self.orders[event.symbol].remove_by_id(event.order_id)
        self._realized_profit[event.symbol] = 0.0

    def _handle_order_removal(self, event: OrderEvent, reason: str) -> None:
        """
        Common logic for removing orders (cancelled, expired, etc.).

        Args:
            event: OrderEvent containing order details
            reason: Human-readable reason (e.g., "cancelled", "expired")
        """
        self.orders[event.symbol].remove_by_id(event.order_id)
        side_str = event.side.value if event.side else "N/A"
        self.LOGGER.info(
            f"{event.display_order_type} order for {event.symbol} has been {reason}. (Side: {side_str})"
        )

    def on_cancelled_order(self, event: OrderEvent) -> None:
        self._handle_order_removal(event, "cancelled")

    def on_expired_order(self, event: OrderEvent) -> None:
        self._handle_order_removal(event, "expired")

    @abstractmethod
    def load(self, df: DataFrame) -> DataFrame: ...

    @abstractmethod
    async def run(self, symbol: str) -> None: ...

    @abstractmethod
    async def run_backtest(self, symbol: str, index: int) -> None: ...
