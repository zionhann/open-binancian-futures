from enum import Enum
from dotenv import load_dotenv
import os

load_dotenv()

LISTEN_KEY = "listenKey"
USDT = "USDT"
CODE = "code"
POSITION_SIDE = "ps"  # BUY | SELL
ENTRY_PRICE = "ep"
POSITION_AMOUNT = "pa"

KEEPALIVE_INTERVAL = 3600 * 23 + 60 * 55
TO_MILLI = 1000
MIN_EXP = 660

LOG_FORMAT = "[%(asctime)s] %(levelname)s [%(name)s] %(module)s.%(funcName)s:%(lineno)d --- %(message)s"
LOG_BASEDIR = ".log"


class TradeConfig(Enum):
    SYMBOLS = [symbol.strip() for symbol in os.getenv("SYMBOLS", "BTCUSDT").split(",")]
    INTERVAL = os.getenv("INTERVAL", "1d")
    LEVERAGE = int(os.getenv("LEVERAGE", "1"))
    SIZE = float(os.getenv("SIZE", "0.05"))
    RSI_WINDOW = int(os.getenv("RSI_WINDOW", "14"))
    IS_TESTNET = True if os.getenv("IS_TESTNET", "false") == "true" else False


class RSI(Enum):
    OVERSOLD_THRESHOLD = int(os.getenv("RSI_OVERSOLD_THRESHOLD", "30"))
    OVERBOUGHT_THRESHOLD = int(os.getenv("RSI_OVERBOUGHT_THRESHOLD", "70"))


class TPSL(Enum):
    TAKE_PROFIT = float(os.getenv("TPSL_TAKE_PROFIT", "0.05"))
    STOP_LOSS = float(os.getenv("TPSL_STOP_LOSS", "0.05"))


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

    class TPSL(Enum):
        TAKE_PROFIT = "TAKE_PROFIT"
        STOP = "STOP"
        STOP_MARKET = "STOP_MARKET"
        TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class PositionSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


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
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
