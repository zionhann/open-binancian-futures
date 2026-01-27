"""
Open Binancian Futures - Binance USDⓈ-M Futures Trading Framework

A robust framework for creating, backtesting, and deploying trading bots
for Binance USDⓈ-M Futures.
"""

__version__ = "3.1.1"
__author__ = "HAN Sion"
__email__ = "its.zionhan@gmail.com"
__license__ = "MIT"

# Public API exports - lazy imports to avoid dependency issues at package level
__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "__license__",
]

def __getattr__(name):
    """Lazy import public API components to avoid loading heavy dependencies at package init."""
    if name == "AppConfig":
        from open_binancian_futures.constants import AppConfig
        return AppConfig
    elif name == "BacktestConfig":
        from open_binancian_futures.constants import BacktestConfig
        return BacktestConfig
    elif name == "Bracket":
        from open_binancian_futures.constants import Bracket
        return Bracket
    elif name == "TrailingStop":
        from open_binancian_futures.constants import TrailingStop
        return TrailingStop
    elif name == "OrderType":
        from open_binancian_futures.constants import OrderType
        return OrderType
    elif name == "PositionSide":
        from open_binancian_futures.constants import PositionSide
        return PositionSide
    elif name == "OrderStatus":
        from open_binancian_futures.constants import OrderStatus
        return OrderStatus
    elif name == "TimeInForce":
        from open_binancian_futures.constants import TimeInForce
        return TimeInForce
    elif name == "Runner":
        from open_binancian_futures.runners import Runner
        return Runner
    elif name == "LiveTrading":
        from open_binancian_futures.runners import LiveTrading
        return LiveTrading
    elif name == "Backtesting":
        from open_binancian_futures.runners import Backtesting
        return Backtesting
    elif name == "Strategy":
        from open_binancian_futures.strategy import Strategy
        return Strategy
    elif name == "StrategyContext":
        from open_binancian_futures.strategy import StrategyContext
        return StrategyContext
    elif name == "Webhook":
        from open_binancian_futures.webhook import Webhook
        return Webhook
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
