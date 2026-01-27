import logging
import math
import textwrap
import uuid
from collections.abc import MutableMapping, Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, Iterable, Optional, TypeVar

import pandas as pd
from pandas import DataFrame, Series, Timestamp

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponseSymbolsInner,
    ExchangeInformationResponseSymbolsInnerFiltersInner,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    OrderTradeUpdateO,
)
AlgoUpdateO = Any

from open_binancian_futures.types import (
    AlgoStatus,
    FilterType,
    OrderStatus,
    OrderType,
    PositionSide,
)
from open_binancian_futures.constants import settings
from open_binancian_futures.utils import decimal_places

LOGGER = logging.getLogger(__name__)
T = TypeVar("T")

# --- Base Container ---

class SymbolContainer(MutableMapping[str, T], Generic[T]):
    def __init__(self, default_factory: Callable[[], T], symbols: list[str]) -> None:
        self._items: dict[str, T] = {s: default_factory() for s in symbols}
        self._default_factory = default_factory

    def __getitem__(self, symbol: str) -> T:
        if symbol not in self._items:
            raise KeyError(f"Symbol not found: {symbol}")
        return self._items[symbol]

    def __setitem__(self, symbol: str, value: T) -> None:
        self._items[symbol] = value

    def __delitem__(self, symbol: str) -> None:
        del self._items[symbol]

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({list(self._items.keys())})"

    def get(self, symbol: str) -> T:
        return self[symbol]

    def update_item(self, symbol: str, value: T) -> None:
        self[symbol] = value


# --- Balance ---

@dataclass
class Balance:
    _balance: float

    def __init__(self, balance: float):
        self._balance = math.floor(balance * 100) / 100

    def __str__(self):
        return f"{self._balance:.2f}"

    @property
    def balance(self) -> float:
        return self._balance

    def calculate_quantity(self, entry_price: float, size: float, leverage: int) -> float:
        initial_margin = self._balance * size
        return initial_margin * leverage / entry_price

    def add_pnl(self, pnl: float) -> None:
        self._balance += pnl


# --- Order ---

@dataclass
class Order:
    symbol: str
    id: int
    type: OrderType
    side: PositionSide
    price: float
    quantity: float
    gtd: int = 0

    def __repr__(self) -> str:
        return f"\n\t{self.__class__.__name__}(symbol={self.symbol}, type={self.type}, side={self.side}, price={self.price})"

    def is_type(self, *args: OrderType) -> bool:
        return self.type in args

    def is_expired(self, time: Timestamp) -> bool:
        return self.gtd < int(time.timestamp() * 1000) if self.gtd else False

    def is_filled(self, high: float, low: float) -> bool:
        if self.is_type(OrderType.MARKET):
            return True
        if self.is_type(OrderType.LIMIT):
            return self.price >= low if self.side == PositionSide.BUY else self.price <= high
        if self.is_type(OrderType.TAKE_PROFIT_MARKET):
            return self.price <= high if self.side == PositionSide.SELL else self.price >= low
        if self.is_type(OrderType.STOP_MARKET):
            return self.price >= low if self.side == PositionSide.SELL else self.price <= high
        return False


class OrderList:
    def __init__(self, orders: list[Order] | None = None) -> None:
        self.orders = orders if orders is not None else []

    def __iter__(self) -> Iterator[Order]:
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
        self.orders[:] = [order for order in self.orders if order.id != id]

    def clear(self) -> None:
        LOGGER.debug(f"{len(self.orders)} ORDERS CLEARED")
        self.orders.clear()

    def find_all_by_type(self, *args: OrderType) -> "OrderList":
        orders = [order for order in self.orders if order.is_type(*args)]
        return OrderList(sorted(orders, key=lambda o: args.index(o.type)))

    def find_by_type(self, type: OrderType) -> "Order | None":
        return next((o for o in self.orders if o.type == type), None)

    def open_order(self, symbol: str, type: OrderType, side: PositionSide, entry_price: float, entry_quantity: float, time: Timestamp, gtd_time: int = 0) -> None:
        order_id = uuid.uuid4().int
        self.add(Order(symbol=symbol, id=order_id, type=type, side=side, price=entry_price, quantity=entry_quantity, gtd=gtd_time))
        LOGGER.debug(textwrap.dedent(f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                OPEN {type.value} ORDER @ {entry_price}
                ID: {order_id}
                Symbol: {symbol}
                Side: {side.value}
                Quantity: {entry_quantity}
                GTD: {time}
                """))


class OrderBook(SymbolContainer[OrderList]):
    def __init__(self, orders: dict[str, OrderList] | None = None) -> None:
        if orders is not None:
            super().__init__(OrderList, [])
            self._items = orders
        else:
            super().__init__(OrderList, settings.symbols_list)


class OrderEventSource(Enum):
    ORDER_TRADE_UPDATE = "ORDER_TRADE_UPDATE"
    ALGO_UPDATE = "ALGO_UPDATE"


@dataclass
class OrderEvent:
    source: OrderEventSource
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
            source=OrderEventSource.ORDER_TRADE_UPDATE,
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
            source=OrderEventSource.ALGO_UPDATE,
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
            raise ValueError(f"Cannot convert {self.source.value} event to Order: order_type is required")
        if self.side is None:
            raise ValueError(f"Cannot convert {self.source.value} event to Order: side is required")
        return Order(
            symbol=self.symbol,
            id=self.order_id,
            type=self.order_type,
            side=self.side,
            price=self.price or self.stop_price or 0.0,
            quantity=self.quantity or 0.0,
            gtd=self.gtd or 0,
        )

    def can_convert_to_order(self) -> bool:
        return self.order_type is not None and self.side is not None

    @property
    def is_from_algo(self) -> bool:
        return self.source == OrderEventSource.ALGO_UPDATE

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
        return (
            f"\n\t{self.__class__.__name__}(symbol={self.symbol}, side={self.side}, price={self.price}, "
            f"bep={self.break_even_price}, amount={self.amount}, leverage={self.leverage})"
        )

    def is_long(self) -> bool:
        return self.side == PositionSide.BUY

    def is_short(self) -> bool:
        return self.side == PositionSide.SELL

    def initial_margin(self) -> float:
        return self.amount * self.price / self.leverage

    def simple_pnl(self, target_price: float) -> float:
        price_diff = target_price - self.price if self.is_long() else self.price - target_price
        return price_diff * self.amount

    def roi(self, current_price: float) -> float:
        price_change = current_price - self.price if self.is_long() else self.price - current_price
        return (price_change / self.price) * self.leverage


class PositionList:
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
        return f"{__class__.__name__}(positions={self.positions}, entry_count={self.entry_count})"

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


class PositionBook(SymbolContainer[PositionList]):
    def __init__(self, positions: dict[str, PositionList] | None = None) -> None:
        if positions is not None:
            super().__init__(PositionList, [])
            self._items = positions
        else:
            super().__init__(PositionList, settings.symbols_list)


# --- ExchangeInfo & Filter ---

@dataclass(frozen=True)
class Filter:
    tick_size: float
    step_size: float
    min_notional: float

    @classmethod
    def from_binance(cls, filters: Iterable[ExchangeInformationResponseSymbolsInnerFiltersInner]) -> "Filter":
        filter_types: dict[str, ExchangeInformationResponseSymbolsInnerFiltersInner] = {
            f.filter_type: f for f in filters if f.filter_type is not None
        }
        tick_size = float(pf.tick_size) if (pf := filter_types.get(FilterType.PRICE_FILTER.value)) and pf.tick_size else 0.0
        step_size = float(ls.step_size) if (ls := filter_types.get(FilterType.LOT_SIZE.value)) and ls.step_size else 0.0
        min_notional = float(mn.notional) if (mn := filter_types.get(FilterType.MIN_NOTIONAL.value)) and mn.notional else 0.0
        return cls(tick_size=tick_size, step_size=step_size, min_notional=min_notional)


class ExchangeInfo:
    def __init__(self, items: Iterable[ExchangeInformationResponseSymbolsInner]):
        self.filters: dict[str, Filter] = {
            item.symbol: Filter.from_binance(item.filters)
            for item in items if item.symbol and item.filters
        }

    def to_entry_price(self, symbol: str, initial_price: float) -> float:
        tick_size = self.filters[symbol].tick_size
        decimals = decimal_places(tick_size)
        return initial_price if decimals == decimal_places(initial_price) else round(initial_price, decimals)

    def to_entry_quantity(self, symbol: str, entry_price: float, size: float, leverage: int, balance: Balance) -> float:
        step_size = self.filters[symbol].step_size
        initial_quantity = balance.calculate_quantity(entry_price, size, leverage)
        decimals = decimal_places(step_size)
        entry_quantity = round(int(initial_quantity / step_size) * step_size, decimals)
        return entry_quantity if self._is_notional_enough(symbol, entry_quantity, entry_price) else 0.0

    def trim_quantity_precision(self, symbol: str, quantity: float) -> float:
        step_size = self.filters[symbol].step_size
        decimals = decimal_places(step_size)
        return quantity if decimals == decimal_places(quantity) else round(quantity, decimals)

    def _is_notional_enough(self, symbol: str, entry_quantity: float, entry_price: float) -> bool:
        return self._calculate_notional(entry_quantity, entry_price) >= self.filters[symbol].min_notional

    def _calculate_notional(self, entry_quantity: float, entry_price: float) -> float:
        max_tpsl = max(settings.effective_take_profit_ratio, settings.stop_loss_ratio)
        factor = 1 - (max_tpsl / settings.leverage)
        return entry_quantity * (entry_price * factor)


# --- Indicator ---

class Indicator(SymbolContainer[DataFrame]):
    def __init__(self, indicators: dict[str, DataFrame]) -> None:
        super().__init__(DataFrame, [])
        self._items = indicators

    def update(self, symbol: str, df: DataFrame) -> None:
        self[symbol] = df


def vwap(high: Series, low: Series, close: Series, volume: Series, length: int) -> Series:
    tp = (high + low + close) / 3
    tpv = tp * volume
    wpv = tpv.rolling(window=length, min_periods=1).sum()
    cumvol = volume.rolling(window=length, min_periods=1).sum()
    res = wpv / cumvol
    res.name = f"VWAP_{length}"
    return res
