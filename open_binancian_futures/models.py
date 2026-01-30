import logging
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional, TypeVar

from pandas import DataFrame, Timestamp

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponseSymbolsInner,
    ExchangeInformationResponseSymbolsInnerFiltersInner,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    OrderTradeUpdateO,
    AlgoUpdateO,
)

from .types import (
    AlgoStatus,
    FilterType,
    EventType,
    OrderStatus,
    OrderType,
    PositionSide,
)
from .constants import settings
from .utils import decimal_places

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


# --- Balance ---


class Balance:
    """Account balance tracker with precision rounding."""

    def __init__(self, balance: float):
        import math

        self._balance = math.floor(balance * 100) / 100

    def __str__(self):
        return f"{self._balance:.2f}"

    @property
    def balance(self) -> float:
        return self._balance

    def calculate_quantity(self, entry_price: float) -> float:
        initial_margin = self._balance * settings.size
        return initial_margin * settings.leverage / entry_price

    def add_pnl(self, pnl: float) -> None:
        self._balance += pnl


# --- Order ---


@dataclass
class Order:
    symbol: str
    order_id: int
    type: OrderType
    side: PositionSide
    price: float
    quantity: float
    gtd: Optional[int] = None

    def __repr__(self) -> str:
        return (
            f"Order({self.symbol}, {self.type.value}, {self.side.value}, {self.price})"
        )

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args

    def is_expired(self, time: Timestamp) -> bool:
        return self.gtd < int(time.timestamp() * 1000) if self.gtd else False

    def is_filled(self, high: float, low: float) -> bool:
        if self.is_type(OrderType.MARKET):
            return True
        if self.is_type(OrderType.LIMIT):
            return (
                self.price >= low
                if self.side == PositionSide.BUY
                else self.price <= high
            )
        if self.is_type(OrderType.TAKE_PROFIT_MARKET):
            return (
                self.price <= high
                if self.side == PositionSide.SELL
                else self.price >= low
            )
        if self.is_type(OrderType.STOP_MARKET):
            return (
                self.price >= low
                if self.side == PositionSide.SELL
                else self.price <= high
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
        gtd_time: Optional[int] = None,
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
            )
        )
        LOGGER.debug(
            f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"OPEN {type.value} ORDER @ {entry_price}\n"
            f"ID: {order_id}, Symbol: {symbol}, Side: {side.value}, Quantity: {entry_quantity}"
        )


class OrderBook(dict[str, OrderList]):
    """Dict mapping symbol to OrderList with guaranteed non-null access."""

    def __init__(self, orders: dict[str, OrderList] | None = None) -> None:
        if orders is not None:
            super().__init__(orders)
        else:
            super().__init__({symbol: OrderList() for symbol in settings.symbols_list})

    def __getitem__(self, key: str) -> OrderList:
        """
        Get OrderList for symbol, auto-creating if missing.

        Logs warning if symbol not in settings.symbols_list to aid debugging.
        """
        if key not in self:
            if key not in settings.symbols_list:
                LOGGER.warning(
                    f"OrderBook accessed with unknown symbol '{key}'. "
                    f"Configured symbols: {settings.symbols_list}"
                )
            self[key] = OrderList()
        return super().__getitem__(key)


@dataclass
class OrderEvent:
    source: EventType
    symbol: str
    order_id: int
    status: OrderStatus | AlgoStatus
    order_type: Optional[OrderType] = None
    side: Optional[PositionSide] = None
    price: Optional[float] = None
    stop_price: Optional[float] = None
    quantity: Optional[float] = None
    filled: Optional[float] = None
    average_price: Optional[float] = None
    realized_profit: Optional[float] = None
    gtd: Optional[int] = None
    is_reduce_only: Optional[bool] = None

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

    def __init__(self, positions: dict[str, PositionList] | None = None) -> None:
        if positions is not None:
            super().__init__(positions)
        else:
            super().__init__(
                {symbol: PositionList() for symbol in settings.symbols_list}
            )

    def __getitem__(self, key: str) -> PositionList:
        """
        Get PositionList for symbol, auto-creating if missing.

        Logs warning if symbol not in settings.symbols_list to aid debugging.
        """
        if key not in self:
            if key not in settings.symbols_list:
                LOGGER.warning(
                    f"PositionBook accessed with unknown symbol '{key}'. "
                    f"Configured symbols: {settings.symbols_list}"
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


class Indicator(dict[str, DataFrame]):
    """Simple dict mapping symbol to DataFrame with indicators."""

    def update(self, symbol: str, df: DataFrame) -> None:
        self[symbol] = df
