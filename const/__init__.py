from enum import Enum
from dotenv import load_dotenv
import os

load_dotenv()

LISTEN_KEY = "listenKey"
USDT = "USDT"
CODE = "code"

KEEPALIVE_INTERVAL = 3600 * 23 + 60 * 55
LISTEN_KEY_RENEW_INTERVAL = 60 * 55


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
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP = "STOP"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(Enum):
    GTC = "GTC"
    GTD = "GTD"


class EventType(Enum):
    KLINE = "kline"
    ORDER_TRADE_UPDATE = "ORDER_TRADE_UPDATE"
    ACCOUNT_UPDATE = "ACCOUNT_UPDATE"


class OrderStatus(Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
