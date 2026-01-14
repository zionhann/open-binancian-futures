from dataclasses import dataclass
from enum import Enum
from typing import Optional

from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    OrderTradeUpdateO,
    AlgoUpdateO,
)

from main.model.order.order import Order
from model.constant import OrderStatus, OrderType, PositionSide, AlgoStatus


class OrderEventSource(Enum):
    ORDER_TRADE_UPDATE = "ORDER_TRADE_UPDATE"
    ALGO_UPDATE = "ALGO_UPDATE"


@dataclass
class OrderEvent:
    """
    Unified order event that normalizes ORDER_TRADE_UPDATE and ALGO_UPDATE events.
    Enables common handling of NEW, CANCELED, and EXPIRED events across both event types.
    """

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
        """Create OrderEvent from ORDER_TRADE_UPDATE event data."""
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
        """
        Create OrderEvent from ALGO_UPDATE event data.
        Maps AlgoStatus to OrderStatus for common statuses (NEW, CANCELED, EXPIRED).
        """
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
        """
        Convert to Order object for OrderBook compatibility.
        Raises ValueError if required fields are missing.
        """
        from .order import Order

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
            id=self.order_id,
            type=self.order_type,
            side=self.side,
            price=self.price or self.stop_price or 0.0,
            quantity=self.quantity or 0.0,
            gtd=self.gtd or 0,
        )

    def can_convert_to_order(self) -> bool:
        """Check if this event has enough information to convert to Order."""
        return self.order_type is not None and self.side is not None

    @property
    def is_from_algo(self) -> bool:
        """Check if this event originated from ALGO_UPDATE."""
        return self.source == OrderEventSource.ALGO_UPDATE

    @property
    def display_order_type(self) -> str:
        """Get display string for order type."""
        if self.order_type:
            return self.order_type.value
        return "ALGO" if self.is_from_algo else "UNKNOWN"
