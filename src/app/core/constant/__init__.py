import os

from enum import Enum

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class Required(Enum):
    """
    Either (API_KEY, API_SECRET) or (API_KEY_TEST, API_SECRET_TEST) is required.
    STRATEGY: The class name of the strategy to use. It must be located in the `app.core.strategy` module.
    """

    API_KEY = os.getenv("API_KEY")
    API_SECRET = os.getenv("API_SECRET")

    API_KEY_TEST = os.getenv("API_KEY_TEST")
    API_SECRET_TEST = os.getenv("API_SECRET_TEST")

    STRATEGY = os.getenv("STRATEGY")


class AppConfig(Enum):
    SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "BTCUSDT").split(",")]
    INTERVAL = os.getenv("INTERVAL", "1d")
    LEVERAGE = int(os.getenv("LEVERAGE", 1))
    SIZE = float(os.getenv("SIZE", 0.05))
    IS_TESTNET = os.getenv("IS_TESTNET", "false") == "true"
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")


class BacktestConfig(Enum):
    IS_BACKTEST = os.getenv("IS_BACKTEST", "false") == "true"
    BALANCE = float(os.getenv("BACKTEST_BALANCE", 10000))
    KLINES_LIMIT = min(int(os.getenv("BACKTEST_KLINES_LIMIT", 500)), 1000)
    INDICATOR_INIT_SIZE = int(
        os.getenv("BACKTEST_INDICATOR_INIT_SIZE", KLINES_LIMIT * 0.2)
    )


class Indicator(Enum):
    RSI_WINDOW = int(os.getenv("RSI_WINDOW", 14))
    RSI_RECENT_WINDOW = int(os.getenv("RSI_RECENT_WINDOW", 7))
    RSI_OVERSOLD_THRESHOLD = int(os.getenv("RSI_OVERSOLD_THRESHOLD", 30))
    RSI_OVERBOUGHT_THRESHOLD = int(os.getenv("RSI_OVERBOUGHT_THRESHOLD", 70))
    OBV_SIGNAL = int(os.getenv("OBV_SIGNAL", 9))
    MACD_FAST = int(os.getenv("MACD_FAST", 12))
    MACD_SLOW = int(os.getenv("MACD_SLOW", 26))
    MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", 9))
    VMA_WINDOW = int(os.getenv("VMA_WINDOW", 10))
    VWAP_LENGTH = int(os.getenv("VWAP_LENGTH", 14))


class TPSL(Enum):
    STOP_LOSS_RATIO = float(os.getenv("TPSL_STOP_LOSS_RATIO", 0.05))
    TAKE_PROFIT_RATIO = float(os.getenv("TPSL_TAKE_PROFIT_RATIO", STOP_LOSS_RATIO * 2))


class TS(Enum):
    ACTIVATION_RATIO = float(
        os.getenv(
            "TS_ACTIVATION_RATIO",
            TPSL.TAKE_PROFIT_RATIO.value,
        )
    )
    CALLBACK_RATIO = float(
        os.getenv(
            "TS_CALLBACK_RATIO",
            abs(ACTIVATION_RATIO - TPSL.STOP_LOSS_RATIO.value),
        )
    )


class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"
    LIQUIDATION = "LIQUIDATION"


class TimeInForce(Enum):
    GTC = "GTC"
    GTD = "GTD"
    GTE_GTC = "GTE_GTC"


class EventType(Enum):
    KLINE = "kline"
    ORDER_TRADE_UPDATE = "ORDER_TRADE_UPDATE"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    LISTEN_KEY_EXPIRED = "listenKeyExpired"
    TRADE_LITE = "TRADE_LITE"


class OrderStatus(Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"


class PositionSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class FilterType(Enum):
    PRICE_FILTER = "PRICE_FILTER"
    LOT_SIZE = "LOT_SIZE"
    MIN_NOTIONAL = "MIN_NOTIONAL"
