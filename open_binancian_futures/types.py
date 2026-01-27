from enum import Enum

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
    ALGO_UPDATE = "ALGO_UPDATE"

class OrderStatus(Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    EXPIRED_IN_MATCH = "EXPIRED_IN_MATCH"

class AlgoStatus(Enum):
    NEW = "NEW"
    CANCELED = "CANCELED"
    TRIGGERING = "TRIGGERING"
    TRIGGERED = "TRIGGERED"
    FINISHED = "FINISHED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class PositionSide(Enum):
    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"
    BUY = "BUY"
    SELL = "SELL"

class PositionMarginType(Enum):
    ISOLATED = "isolated"
    CROSSED = "crossed"

class FilterType(Enum):
    PRICE_FILTER = "PRICE_FILTER"
    LOT_SIZE = "LOT_SIZE"
    MIN_NOTIONAL = "MIN_NOTIONAL"

class BaseURL(Enum):
    MAINNET_REST = "https://fapi.binance.com"
    MAINNET_WS = "wss://fstream.binance.com"
    TESTNET_REST = "https://testnet.binancefuture.com"
    TESTNET_WS = "wss://stream.binancefuture.com"
