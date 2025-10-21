import os

from enum import Enum

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class Required:
    """
    Either (API_KEY, API_SECRET) or (API_KEY_TEST, API_SECRET_TEST) is required.
    STRATEGY: The class name of the strategy to use. It must be located in the `runner.core.strategy` module.
    """

    API_KEY = os.getenv("API_KEY")
    API_SECRET = os.getenv("API_SECRET")

    API_KEY_TEST = os.getenv("API_KEY_TEST")
    API_SECRET_TEST = os.getenv("API_SECRET_TEST")

    STRATEGY = os.getenv("STRATEGY")


class AppConfig:
    SYMBOLS = [s.strip() for s in os.getenv("SYMBOLS", "BTCUSDT").split(",")]
    INTERVAL = os.getenv("INTERVAL", "1d")
    LEVERAGE = int(os.getenv("LEVERAGE", 1))
    SIZE = float(os.getenv("SIZE", 0.05))
    AVERAGING = [float(ratio.strip()) for ratio in os.getenv("AVERAGING", "1").split(",")]
    IS_TESTNET = os.getenv("IS_TESTNET", "false") == "true"
    GTD_NLINES = int(os.getenv("GTD_NLINES", 3))
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")
    TIMEZONE = os.getenv("TIMEZONE", "UTC")


class BacktestConfig:
    IS_BACKTEST = os.getenv("IS_BACKTEST", "false") == "true"
    BALANCE = float(os.getenv("BACKTEST_BALANCE", 100))
    KLINES_LIMIT = min(int(os.getenv("BACKTEST_KLINES_LIMIT", 1000)), 1000)
    INDICATOR_INIT_SIZE = int(
        os.getenv("BACKTEST_INDICATOR_INIT_SIZE", KLINES_LIMIT * 0.2)
    )
    SAMPLE_SIZE = int(KLINES_LIMIT - INDICATOR_INIT_SIZE)


class Bracket:
    STOP_LOSS_RATIO = float(os.getenv("TPSL_STOP_LOSS_RATIO", 0.05))
    TAKE_PROFIT_RATIO = float(os.getenv("TPSL_TAKE_PROFIT_RATIO", STOP_LOSS_RATIO * 2))


class TrailingStop:
    ACTIVATION_RATIO = float(
        os.getenv(
            "TS_ACTIVATION_RATIO",
            Bracket.TAKE_PROFIT_RATIO,
        )
    )
    CALLBACK_RATIO = float(
        os.getenv(
            "TS_CALLBACK_RATIO",
            abs(ACTIVATION_RATIO - Bracket.STOP_LOSS_RATIO),
        )
    )


class OrderType(Enum):
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT"
    MARKET = "MARKET"
    STOP_MARKET = "STOP_MARKET"
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


class BaseURL(Enum):
    MAINNET_REST = "https://fapi.binance.com"
    MAINNET_WS = "wss://fstream.binance.com"

    TESTNET_REST = "https://testnet.binancefuture.com"
    TESTNET_WS = "wss://stream.binancefuture.com"
