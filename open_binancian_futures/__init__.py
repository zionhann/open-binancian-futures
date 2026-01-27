"""
Open Binancian Futures - Binance USDⓈ-M Futures Trading Framework

A robust framework for creating, backtesting, and deploying trading bots
for Binance USDⓈ-M Futures.
"""

__version__ = "4.0.0"
__author__ = "HAN Sion"
__email__ = "its.zionhan@gmail.com"
__license__ = "MIT"

# Direct imports - no lazy loading magic
from open_binancian_futures.constants import settings
from open_binancian_futures.types import (
    OrderType,
    PositionSide,
    OrderStatus,
    TimeInForce,
)
from open_binancian_futures.runners import Runner, LiveTrading, Backtesting
from open_binancian_futures.strategy import (
    Strategy,
    StrategyContext,
    StrategyLoadError,
    StrategyNotFoundError,
)
from open_binancian_futures.models import Order, Position, Balance
from open_binancian_futures.webhook import Webhook

__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "__license__",
    "settings",
    "OrderType",
    "PositionSide",
    "OrderStatus",
    "TimeInForce",
    "Runner",
    "LiveTrading",
    "Backtesting",
    "Strategy",
    "StrategyContext",
    "StrategyLoadError",
    "StrategyNotFoundError",
    "Order",
    "Position",
    "Balance",
    "Webhook",
]
