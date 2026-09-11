import asyncio
import logging
import math
import uuid
from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from typing import TypeVar

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponseSymbolsInner,
    ExchangeInformationResponseSymbolsInnerFiltersInner,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    AlgoUpdateO,
    OrderTradeUpdateO,
)
from pandas import DataFrame, Timestamp

from .execution import ExecutionConfig
from .types import (
    AlgoStatus,
    EventType,
    FilterType,
    OrderStatus,
    OrderType,
    PositionSide,
)
from .utils import decimal_places

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


# --- Balance ---


class Balance:
    """
    Account balance tracker with thread-safe access for concurrent order placement.

    Uses optimistic deduction to prevent race conditions when multiple symbols
    place orders simultaneously. The websocket balance update will correct any drift.
    """

    def __init__(
        self,
        balance: float,
        *,
        execution_config: ExecutionConfig | None = None,
    ):
        self._balance = math.floor(balance * 100) / 100
        self._execution_config = execution_config or ExecutionConfig()
        self._lock = asyncio.Lock()
        self._reserved_margins: dict[Hashable, float] = {}

    def __str__(self):
        return f"{self._balance:.2f}"

    @property
    def available(self) -> float:
        """Current available balance (may include optimistic deductions)."""
        return self._balance

    @property
    def reserved_margin(self) -> float:
        """Margin held for pending backtest orders."""
        return sum(self._reserved_margins.values())

    def reserved_margin_for(self, order_id: Hashable) -> float:
        return self._reserved_margins.get(order_id, 0.0)

    @property
    def lock(self) -> asyncio.Lock:
        """Lock for coordinating concurrent order placement."""
        return self._lock

    @property
    def execution_config(self) -> ExecutionConfig:
        return self._execution_config

    def configure_execution(self, execution_config: ExecutionConfig) -> None:
        """Update sizing inputs when a runner injects its execution policy."""
        self._execution_config = execution_config

    def calculate_quantity(self, entry_price: float) -> float:
        initial_margin = self._balance * self._execution_config.position_size
        return initial_margin * self._execution_config.leverage / entry_price

    def deduct(self, amount: float) -> None:
        """
        Optimistically deduct margin after order placement.

        Called immediately after placing an order to prevent subsequent orders
        from seeing stale balance. Websocket update will correct any drift.
        """
        self._balance = max(0, self._balance - amount)
        LOGGER.debug(f"Deducted {amount:.2f}, new balance: {self._balance:.2f}")

    def reserve_margin(self, order_id: Hashable, amount: float) -> None:
        """Reserve pending-order margin by reducing free balance once."""
        if amount < 0:
            raise ValueError("margin amount must not be negative")
        if order_id in self._reserved_margins:
            raise ValueError(f"margin is already reserved for order {order_id}")
        if amount > self._balance:
            raise ValueError("insufficient available balance for margin reservation")
        self._reserved_margins[order_id] = amount
        self._balance -= amount

    def release_margin(self, order_id: Hashable) -> float:
        """Release a pending reservation and return the released amount."""
        amount = self._reserved_margins.pop(order_id, 0.0)
        self._balance += amount
        return amount

    def consume_margin(self, order_id: Hashable, actual_margin: float) -> float:
        """Convert a pending reservation into actual position margin."""
        if actual_margin < 0:
            raise ValueError("actual margin must not be negative")
        reserved = self._reserved_margins.get(order_id, 0.0)
        if actual_margin > self._balance + reserved:
            raise ValueError("insufficient available balance for position margin")
        self._reserved_margins.pop(order_id, None)
        self._balance += reserved - actual_margin
        return reserved

    async def update(self, new_balance: float) -> None:
        """
        Update balance from websocket event (replaces with authoritative value).

        Called by on_balance_update to sync local state with exchange.
        """
        async with self._lock:
            self._balance = math.floor(new_balance * 100) / 100

    def increase_balance(self, amount: float) -> None:
        self._balance += amount


# --- Order ---


@dataclass(frozen=True)
class OrderIntent:
    """Framework-level order request independent of Binance SDK enums."""

    symbol: str
    side: PositionSide
    order_type: OrderType
    price: float | None = None
    quantity: float | None = None
    reduce_only: bool = False
    gtd: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "side", PositionSide(self.side))
        object.__setattr__(self, "order_type", OrderType(self.order_type))


@dataclass
class Order:
    symbol: str
    order_id: int
    type: OrderType
    side: PositionSide
    price: float
    quantity: float
    gtd: int | None = None
    created_at: Timestamp | None = None
    reduce_only: bool = False
    stop_triggered: bool = False

    def __repr__(self) -> str:
        return (
            f"Order({self.symbol}, {self.type.value}, {self.side.value}, {self.price})"
        )

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args

    def is_expired(self, time: Timestamp) -> bool:
        return self.gtd < int(time.timestamp() * 1000) if self.gtd else False

    def is_filled(
        self, high: float, low: float, open_price: float | None = None
    ) -> bool:
        """Return whether OHLC values cross this order's trigger.

        The two-argument form remains compatible with the live model.  The
        optional open price makes the gap condition explicit for callers that
        also need to calculate the fill price.  Use
        :class:`DeterministicFillPolicy` for the actual price selected by a
        backtest.
        """
        if self.is_type(OrderType.MARKET):
            return True
        if self.is_type(OrderType.LIMIT):
            return (
                (open_price is not None and open_price <= self.price) or self.price >= low
                if self.side == PositionSide.BUY
                else (open_price is not None and open_price >= self.price) or self.price <= high
            )
        if self.is_type(
            OrderType.TAKE_PROFIT_MARKET, OrderType.TAKE_PROFIT_LIMIT
        ):
            return (
                (open_price is not None and open_price >= self.price) or self.price <= high
                if self.side == PositionSide.SELL
                else (open_price is not None and open_price <= self.price) or self.price >= low
            )
        if self.is_type(OrderType.STOP_MARKET, OrderType.STOP_LIMIT):
            return (
                (open_price is not None and open_price <= self.price) or self.price >= low
                if self.side == PositionSide.SELL
                else (open_price is not None and open_price >= self.price) or self.price <= high
            )
        return False


class OrderList:
    """Wrapper around list[Order] with convenience methods."""

    def __init__(self, orders: list[Order] | None = None) -> None:
        self.orders = orders if orders is not None else []

    def __iter__(self):
        return iter(self.orders)

    def __len__(self) -> int:
        return len(self.orders)

    def __bool__(self) -> bool:
        return bool(self.orders)

    def __contains__(self, order: Order) -> bool:
        return order in self.orders

    def __repr__(self) -> str:
        return str(self.orders)

    def has_type(self, *args: OrderType) -> bool:
        return any(order.is_type(*args) for order in self.orders)

    def add(self, *args: Order) -> None:
        self.orders.extend(args)

    def remove_by_id(self, id: int) -> None:
        self.orders[:] = [order for order in self.orders if order.order_id != id]

    def clear(self) -> None:
        LOGGER.debug(f"{len(self.orders)} orders cleared")
        self.orders.clear()

    def find_all_by_type(self, *args: OrderType) -> "OrderList":
        orders = [order for order in self.orders if order.is_type(*args)]
        return OrderList(sorted(orders, key=lambda o: args.index(o.type)))

    def find_by_type(self, type: OrderType) -> "Order | None":
        return next((o for o in self.orders if o.type == type), None)

    def open_order(
        self,
        symbol: str,
        type: OrderType,
        side: PositionSide,
        entry_price: float,
        entry_quantity: float,
        time: Timestamp,
        gtd_time: int | None = None,
        reduce_only: bool = False,
    ) -> None:
        order_id = uuid.uuid4().int
        self.add(
            Order(
                symbol=symbol,
                order_id=order_id,
                type=type,
                side=side,
                price=entry_price,
                quantity=entry_quantity,
                gtd=gtd_time,
                created_at=time,
                reduce_only=reduce_only,
            )
        )
        LOGGER.debug(
            f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"OPEN {type.value} ORDER @ {entry_price}\n"
            f"ID: {order_id}, Symbol: {symbol}, Side: {side.value}, Quantity: {entry_quantity}"
        )


class OrderBook(dict[str, OrderList]):
    """Dict mapping symbol to OrderList with guaranteed non-null access."""

    def __init__(
        self,
        orders: dict[str, OrderList] | None = None,
        *,
        symbols: Iterable[str] | None = None,
    ) -> None:
        self._configured_symbols = frozenset(
            symbols if symbols is not None else (orders or {})
        )
        if orders is not None:
            super().__init__(orders)
        else:
            super().__init__({symbol: OrderList() for symbol in self._configured_symbols})

    def __getitem__(self, key: str) -> OrderList:
        """
        Get OrderList for symbol, auto-creating if missing.

        Logs a warning when a configured symbol is accessed for the first time.
        """
        if key not in self:
            if self._configured_symbols and key not in self._configured_symbols:
                LOGGER.warning(
                    f"OrderBook accessed with unknown symbol '{key}'. "
                    f"Configured symbols: {sorted(self._configured_symbols)}"
                )
            self[key] = OrderList()
        return super().__getitem__(key)


@dataclass
class OrderEvent:
    source: EventType
    symbol: str
    order_id: int
    status: OrderStatus | AlgoStatus
    order_type: OrderType | None = None
    side: PositionSide | None = None
    price: float | None = None
    stop_price: float | None = None
    quantity: float | None = None
    filled: float | None = None
    average_price: float | None = None
    realized_profit: float | None = None
    gtd: int | None = None
    is_reduce_only: bool | None = None

    @staticmethod
    def from_order_trade_update(data: OrderTradeUpdateO) -> "OrderEvent":
        return OrderEvent(
            source=EventType.ORDER_TRADE_UPDATE,
            symbol=data.s or "",
            order_id=int(data.i or 0),
            status=OrderStatus(data.X or ""),
            order_type=OrderType(data.ot) if data.ot else None,
            side=PositionSide(data.S) if data.S else None,
            price=float(data.p) if data.p else None,
            stop_price=float(data.sp) if data.sp else None,
            quantity=float(data.q) if data.q else None,
            filled=float(data.z) if data.z else None,
            average_price=float(data.ap) if data.ap else None,
            realized_profit=float(data.rp) if data.rp else None,
            gtd=int(data.gtd) if data.gtd else None,
            is_reduce_only=bool(data.R) if data.R is not None else None,
        )

    @staticmethod
    def from_algo_update(data: AlgoUpdateO) -> "OrderEvent":
        return OrderEvent(
            source=EventType.ALGO_UPDATE,
            symbol=data.s or "",
            order_id=int(data.aid or 0),
            status=AlgoStatus(data.X),
            order_type=OrderType(data.o) if data.o else None,
            side=PositionSide(data.S) if data.S else None,
            price=float(data.p) if data.p else None,
            stop_price=float(data.tp) if data.tp else None,
            quantity=float(data.q) if data.q else None,
            average_price=float(data.ap) if data.ap else None,
            gtd=int(data.gtd) if data.gtd else None,
            is_reduce_only=bool(data.R) if data.R is not None else None,
        )

    def to_order(self) -> "Order":
        if self.order_type is None:
            raise ValueError(
                f"Cannot convert {self.source.value} event to Order: order_type is required"
            )
        if self.side is None:
            raise ValueError(
                f"Cannot convert {self.source.value} event to Order: side is required"
            )
        return Order(
            symbol=self.symbol,
            order_id=self.order_id,
            type=self.order_type,
            side=self.side,
            price=self.price or self.stop_price or 0.0,
            quantity=self.quantity or 0.0,
            gtd=self.gtd or None,
            reduce_only=bool(self.is_reduce_only),
        )

    def can_convert_to_order(self) -> bool:
        return self.order_type is not None and self.side is not None

    @property
    def is_from_algo(self) -> bool:
        return self.source == EventType.ALGO_UPDATE

    @property
    def display_order_type(self) -> str:
        if self.order_type:
            return self.order_type.value
        return "ALGO" if self.is_from_algo else "UNKNOWN"


# --- Position ---


@dataclass
class Position:
    symbol: str
    price: float
    amount: float
    side: PositionSide
    leverage: int
    break_even_price: float | None = field(default=None, repr=True)

    def __post_init__(self) -> None:
        self.amount = abs(self.amount)
        if self.break_even_price is None:
            self.break_even_price = self.price

    def __repr__(self) -> str:
        return f"Position({self.symbol}, {self.side.value}, {self.amount}@{self.price})"

    def is_long(self) -> bool:
        return self.side == PositionSide.BUY

    def is_short(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / self.leverage

    def simple_pnl(self, target_price: float) -> float:
        price_diff = (
            target_price - self.price if self.is_long() else self.price - target_price
        )
        return price_diff * self.amount

    def roi(self, current_price: float) -> float:
        price_change = (
            current_price - self.price if self.is_long() else self.price - current_price
        )
        return (price_change / self.price) * self.leverage


class PositionList:
    """Wrapper around list[Position] with convenience methods."""

    def __init__(self, positions: list[Position] | None = None) -> None:
        self.positions: list[Position] = positions if positions is not None else []
        self.entry_count = 0 if not self.positions else 1

    def __iter__(self):
        return iter(self.positions)

    def __len__(self) -> int:
        return len(self.positions)

    def __bool__(self) -> bool:
        return bool(self.positions)

    def __contains__(self, position: Position) -> bool:
        return position in self.positions

    def __repr__(self) -> str:
        if not self.positions:
            return "[]"
        return (
            f"PositionList(positions={self.positions}, entry_count={self.entry_count})"
        )

    def clear(self) -> None:
        self.positions.clear()
        self.entry_count = 0

    def find_first(self) -> "Position | None":
        return next((position for position in self.positions), None)

    def is_long(self) -> bool:
        return any(position.is_long() for position in self.positions)

    def is_short(self) -> bool:
        return any(position.is_short() for position in self.positions)

    def update_positions(self, positions: list[Position]) -> None:
        self.positions[:] = positions


class PositionBook(dict[str, PositionList]):
    """Dict mapping symbol to PositionList with guaranteed non-null access."""

    def __init__(
        self,
        positions: dict[str, PositionList] | None = None,
        *,
        symbols: Iterable[str] | None = None,
    ) -> None:
        self._configured_symbols = frozenset(
            symbols if symbols is not None else (positions or {})
        )
        if positions is not None:
            super().__init__(positions)
        else:
            super().__init__(
                {symbol: PositionList() for symbol in self._configured_symbols}
            )

    def __getitem__(self, key: str) -> PositionList:
        """
        Get PositionList for symbol, auto-creating if missing.

        Logs a warning when a configured symbol is accessed for the first time.
        """
        if key not in self:
            if self._configured_symbols and key not in self._configured_symbols:
                LOGGER.warning(
                    f"PositionBook accessed with unknown symbol '{key}'. "
                    f"Configured symbols: {sorted(self._configured_symbols)}"
                )
            self[key] = PositionList()
        return super().__getitem__(key)


# --- ExchangeInfo & Filter ---


@dataclass(frozen=True)
class Filter:
    tick_size: float
    step_size: float
    min_notional: float

    @classmethod
    def from_binance(
        cls, filters: Iterable[ExchangeInformationResponseSymbolsInnerFiltersInner]
    ) -> "Filter":
        tick_size = 0.0
        step_size = 0.0
        min_notional = 0.0

        for f in filters:
            if f.filter_type == FilterType.PRICE_FILTER.value and f.tick_size:
                tick_size = float(f.tick_size)
            elif f.filter_type == FilterType.LOT_SIZE.value and f.step_size:
                step_size = float(f.step_size)
            elif f.filter_type == FilterType.MIN_NOTIONAL.value and f.notional:
                min_notional = float(f.notional)

        return cls(tick_size=tick_size, step_size=step_size, min_notional=min_notional)


class ExchangeInfo:
    def __init__(self, items: Iterable[ExchangeInformationResponseSymbolsInner]):
        self.filters: dict[str, Filter] = {
            item.symbol: Filter.from_binance(item.filters)
            for item in items
            if item.symbol and item.filters
        }

    def _get_filter(self, symbol: str) -> Filter:
        """Get filter for symbol with descriptive error on missing symbol."""
        try:
            return self.filters[symbol]
        except KeyError:
            raise KeyError(
                f"Symbol '{symbol}' not found in exchange info. "
                f"Available symbols: {list(self.filters.keys())}"
            )

    def _round_to_precision(self, value: float, reference: float) -> float:
        """Round value to match the decimal precision of the reference value."""
        decimals = decimal_places(reference)
        if decimals == decimal_places(value):
            return value
        return round(value, decimals)

    def to_entry_price(self, symbol: str, initial_price: float) -> float:
        tick_size = self._get_filter(symbol).tick_size
        return self._round_to_precision(initial_price, tick_size)

    def to_entry_quantity(
        self,
        symbol: str,
        entry_price: float,
        balance: Balance,
    ) -> float:
        f = self._get_filter(symbol)
        decimals = decimal_places(f.step_size)
        initial_quantity = balance.calculate_quantity(entry_price)
        entry_quantity = round(
            int(initial_quantity / f.step_size) * f.step_size, decimals
        )
        return (
            entry_quantity
            if self._is_notional_enough(symbol, entry_quantity, entry_price)
            else 0.0
        )

    def _is_notional_enough(
        self, symbol: str, entry_quantity: float, entry_price: float
    ) -> bool:
        return (
            self._calculate_notional(entry_quantity, entry_price)
            >= self._get_filter(symbol).min_notional
        )

    def _calculate_notional(self, entry_quantity: float, entry_price: float) -> float:
        return entry_quantity * entry_price


# --- Indicator ---


class Indicator(dict[str, dict[str, DataFrame]]):
    """Nested dict mapping symbol → interval → DataFrame with indicators."""

    def __getitem__(self, symbol: str) -> dict[str, DataFrame]:
        if symbol not in self:
            self[symbol] = {}
        return super().__getitem__(symbol)
