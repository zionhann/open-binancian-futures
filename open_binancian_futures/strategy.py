import asyncio
import importlib
import logging
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

from open_binancian_futures.models import Balance
from open_binancian_futures.constants import settings
from open_binancian_futures.types import OrderType, PositionSide, TimeInForce
from open_binancian_futures.models import ExchangeInfo
from open_binancian_futures.models import Indicator
from open_binancian_futures.models import OrderBook, OrderEvent
from open_binancian_futures.models import Position, PositionBook
from open_binancian_futures import exchange as futures
from open_binancian_futures.utils import decimal_places, fetch, get_or_raise
from open_binancian_futures.webhook import Webhook

BASIC_COLUMNS = [
    "Open_time",
    "Symbol",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
]


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
            ValueError: If strategy name is None or invalid
        """
        if name is None:
            raise ValueError("Strategy is not specified")

        if (strategy := Strategy._import_strategy(name)) is None:
            raise ValueError(f"Invalid strategy: {name}")

        return strategy(
            client=context.client,
            exchange_info=context.exchange_info,
            balance=context.balance,
            orders=context.orders,
            positions=context.positions,
            webhook=context.webhook,
            indicators=context.indicators,
            lock=context.lock,
        )

    @staticmethod
    def _import_strategy(strategy_name: str) -> type["Strategy"] | None:
        """
        Import strategy by file name and return the Strategy subclass.
        Tries to import from package first, then falls back to direct import.
        """
        modules_to_try = [
            f"open_binancian_futures.preset.{strategy_name}",  # Try presets
            strategy_name,                                    # Try external/user module
        ]

        for module_path in modules_to_try:
            try:
                mod = importlib.import_module(module_path)
                subclasses = [
                    cls
                    for cls in mod.__dict__.values()
                    if isinstance(cls, type)
                    and issubclass(cls, Strategy)
                    and cls is not Strategy
                ]
                if subclasses:
                    return subclasses[0]
            except ImportError:
                continue
            except Exception as e:
                # Log other errors (syntax, etc) but keep trying/fail gracefully
                Strategy.LOGGER.error(f"Error loading strategy from {module_path}: {e}")
                Strategy.LOGGER.debug(traceback.format_exc())

        Strategy.LOGGER.error(f"Failed to find valid Strategy subclass in: {modules_to_try}")
        return None

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
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
    ) -> float:
        """
        Calculate stop price for TP/SL orders based on order type and position side.

        The price moves in opposite direction for stop-loss vs take-profit,
        and the direction depends on whether we're closing a long or short position.
        """
        # Normalize ratios by leverage
        sl_ratio = (sl_ratio or settings.stop_loss_ratio) / settings.leverage
        tp_ratio = (tp_ratio or settings.effective_take_profit_ratio) / settings.leverage

        # Determine if this is a stop-loss or take-profit order
        stop_order_types = {OrderType.STOP_MARKET, OrderType.STOP_LIMIT}
        take_profit_order_types = {OrderType.TAKE_PROFIT_MARKET, OrderType.TAKE_PROFIT_LIMIT}

        is_stop_loss = order_type in stop_order_types
        is_take_profit = order_type in take_profit_order_types

        # Select appropriate ratio
        ratio = sl_ratio if is_stop_loss else tp_ratio

        # Calculate price movement direction
        # SL SELL (close long) or TP BUY (close short) → price decreases (1 - ratio)
        # SL BUY (close short) or TP SELL (close long) → price increases (1 + ratio)
        should_decrease_price = (
            (is_stop_loss and stop_side == PositionSide.SELL) or
            (is_take_profit and stop_side == PositionSide.BUY)
        )

        factor = (1 - ratio) if should_decrease_price else (1 + ratio)

        return self.exchange_info.to_entry_price(symbol, entry_price * factor)

    def set_tpsl(
        self,
        symbol: str,
        position_side: PositionSide,
        price: float | None = None,
        stop_price: float | None = None,
        close_position: bool | None = None,
        order_types: list[OrderType] | None = None,
        quantity: float | None = None,
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
    ) -> None:
        _order_types = (
            order_types
            if order_types is not None
            else [
                OrderType.STOP_MARKET,
                OrderType.TAKE_PROFIT_MARKET,
            ]
        )

        for order_type in _order_types:
            reduce_only = None if close_position else True
            _stop_price = stop_price or self.calculate_stop_price(
                symbol=symbol,
                entry_price=get_or_raise(
                    price,
                    lambda: ValueError("Either price or stop_price must be provided"),
                ),
                order_type=order_type,
                stop_side=position_side,
                tp_ratio=tp_ratio,
                sl_ratio=sl_ratio,
            )
            _price = (_stop_price or price) if reduce_only else None
            _quantity = quantity if reduce_only else None

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
        activation_price: float | None = None,
        callback_ratio: float | None = None,
    ) -> None:
        _activation_price = (
            self.exchange_info.to_entry_price(symbol, activation_price)
            if activation_price
            else None
        )
        _callback_ratio = (
            (callback_ratio or settings.effective_callback_ratio) / settings.leverage * 100
        )
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
            self.positions.get(symbol).clear()
            return

        self.positions.get(symbol).update_positions(
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
        self.LOGGER.info(f"Updated positions: {self.positions.get(symbol)}")

    def on_balance_update(self, data: AccountUpdateABInner) -> None:
        if data.a == "USDT":
            self.balance = Balance(float(get_or_raise(data.cw)))
            self.LOGGER.info(f"Updated available USDT: {self.balance}")

    def accumulate_realized_profit(self, symbol: str, profit: float) -> None:
        """Accumulate realized profit for partial fills."""
        self._realized_profit[symbol] += profit

    def on_new_order(self, event: OrderEvent) -> None:
        if event.can_convert_to_order():
            self.orders.get(event.symbol).add(event.to_order())

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
        self.orders.get(event.symbol).remove_by_id(event.order_id)
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
        self.orders.get(event.symbol).remove_by_id(event.order_id)
        self._realized_profit[event.symbol] = 0.0

    def _handle_order_removal(self, event: OrderEvent, reason: str) -> None:
        """
        Common logic for removing orders (cancelled, expired, etc.).

        Args:
            event: OrderEvent containing order details
            reason: Human-readable reason (e.g., "cancelled", "expired")
        """
        self.orders.get(event.symbol).remove_by_id(event.order_id)
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
