from enum import Enum
import os

LISTEN_KEY = "listenKey"
USDT = "USDT"

KEEPALIVE_INTERVAL = 3600 * 23 + 60 * 55
TO_MILLI = 1000
MIN_EXP = 660
MAX_CHILL_COUNTER = 3

MIN_NOTIONAL = {
    "BTCUSDT": 100,
    "ETHUSDT": 20,
}


class AppConfig(Enum):
    SYMBOLS = [symbol.strip() for symbol in os.getenv("SYMBOLS", "BTCUSDT").split(",")]
    INTERVAL = os.getenv("INTERVAL", "1d")
    LEVERAGE = int(os.getenv("LEVERAGE", 1))
    SIZE = float(os.getenv("SIZE", 0.05))
    IS_TESTNET = os.getenv("IS_TESTNET", "false") == "true"


class RSI(Enum):
    WINDOW = int(os.getenv("RSI_WINDOW", 14))
    OVERSOLD_THRESHOLD = int(os.getenv("RSI_OVERSOLD_THRESHOLD", 30))
    OVERBOUGHT_THRESHOLD = int(os.getenv("RSI_OVERBOUGHT_THRESHOLD", 70))


class TPSL(Enum):
    TAKE_PROFIT = float(os.getenv("TPSL_TAKE_PROFIT", 0.01 * AppConfig.LEVERAGE.value))
    STOP_LOSS = float(os.getenv("TPSL_STOP_LOSS", 0.005 * AppConfig.LEVERAGE.value))
    TRAILING_STOP = float(os.getenv("TPSL_TRAILING_STOP", 0.5))


class ApiKey(Enum):
    CLIENT = os.getenv("API_KEY")
    SECRET = os.getenv("API_SECRET")
    CLIENT_TEST = os.getenv("API_KEY_TEST")
    SECRET_TEST = os.getenv("API_SECRET_TEST")


class BaseUrl(Enum):
    REST = os.getenv("BASEURL_REST")
    WS = os.getenv("BASEURL_WS")
    REST_TEST = os.getenv("BASEURL_REST_TEST")
    WS_TEST = os.getenv("BASEURL_WS_TEST")


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


class EventType(Enum):
    KLINE = "kline"
    ORDER_TRADE_UPDATE = "ORDER_TRADE_UPDATE"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"
    LISTEN_KEY_EXPIRED = "listenKeyExpired"


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


class OpenOrder(Enum):
    ID = "id"
    TYPE = "type"
    SIDE = "side"
    PRICE = "price"
    QUANTITY = "quantity"


class Position(Enum):
    SIDE = "side"
    ENTRY_PRICE = "entry_price"
    AMOUNT = "amount"
