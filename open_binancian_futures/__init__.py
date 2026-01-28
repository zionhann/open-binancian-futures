"""
Open Binancian Futures - Binance USDⓈ-M Futures Trading Framework

A robust framework for creating, backtesting, and deploying trading bots
for Binance USDⓈ-M Futures.
"""

__version__ = "1.0.0b1"
__author__ = "HAN Sion"
__email__ = "its.zionhan@gmail.com"
__license__ = "MIT"

# Direct imports - no lazy loading magic
from .constants import settings
from .types import (
    OrderType,
    PositionSide,
    OrderStatus,
    TimeInForce,
    EventType,
    AlgoStatus,
    FilterType,
    BaseURL,
)
from .runners import Runner, LiveTrading, Backtesting
from .strategy import (
    Strategy,
    StrategyContext,
    StrategyLoadError,
    StrategyNotFoundError,
)
from .models import (
    Order,
    Position,
    Balance,
    OrderList,
    OrderBook,
    PositionList,
    PositionBook,
    OrderEvent,
    OrderEventSource,
    ExchangeInfo,
    Filter,
    Indicator,
    vwap,
)
from .webhook import Webhook

__all__ = [
    # Package metadata
    "__version__",
    "__author__",
    "__email__",
    "__license__",
    # Configuration
    "settings",
    # Core models
    "Order",
    "Position",
    "Balance",
    # Collection types
    "OrderList",
    "OrderBook",
    "PositionList",
    "PositionBook",
    # Exchange information
    "ExchangeInfo",
    "Filter",
    # Event handling
    "OrderEvent",
    "OrderEventSource",
    # Technical analysis
    "Indicator",
    "vwap",
    # Enums - Order types
    "OrderType",
    "PositionSide",
    "OrderStatus",
    "TimeInForce",
    # Enums - Additional
    "EventType",
    "AlgoStatus",
    "FilterType",
    "BaseURL",
    # Runners
    "Runner",
    "LiveTrading",
    "Backtesting",
    # Strategy framework
    "Strategy",
    "StrategyContext",
    "StrategyLoadError",
    "StrategyNotFoundError",
    # Infrastructure
    "Webhook",
]
