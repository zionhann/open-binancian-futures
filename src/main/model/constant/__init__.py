from enum import Enum
from typing import Optional

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class Required(BaseSettings):
    """
    Either (API_KEY, API_SECRET) or (API_KEY_TEST, API_SECRET_TEST) is required.
    STRATEGY: The class name of the strategy to use. It must be located in the `runner.core.strategy` module.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    API_KEY: Optional[str] = None
    API_SECRET: Optional[str] = None
    API_KEY_TEST: Optional[str] = None
    API_SECRET_TEST: Optional[str] = None
    STRATEGY: Optional[str] = None


class _AppConfigRaw(BaseSettings):
    """Internal model for raw environment values."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SYMBOLS: str = "BTCUSDT"
    INTERVAL: str = "1d"
    LEVERAGE: int = Field(default=1, ge=1)
    SIZE: float = Field(default=0.05, gt=0.0, le=1.0)
    AVERAGING: str = "1"
    IS_TESTNET: bool = False
    GTD_NLINES: int = Field(default=3, ge=1)
    WEBHOOK_URL: Optional[str] = None
    TIMEZONE: str = "UTC"


class AppConfig:
    """Application configuration with parsed values."""
    def __init__(self):
        raw = _AppConfigRaw()
        self.SYMBOLS = [s.strip() for s in raw.SYMBOLS.split(",")]
        self.INTERVAL = raw.INTERVAL
        self.LEVERAGE = raw.LEVERAGE
        self.SIZE = raw.SIZE
        self.AVERAGING = [float(ratio.strip()) for ratio in raw.AVERAGING.split(",")]
        self.IS_TESTNET = raw.IS_TESTNET
        self.GTD_NLINES = raw.GTD_NLINES
        self.WEBHOOK_URL = raw.WEBHOOK_URL
        self.TIMEZONE = raw.TIMEZONE


class _BacktestConfigRaw(BaseSettings):
    """Internal model for raw environment values."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    IS_BACKTEST: bool = False
    BACKTEST_BALANCE: float = Field(default=100.0, gt=0.0)
    BACKTEST_KLINES_LIMIT: int = Field(default=1000, ge=1, le=1000)
    BACKTEST_INDICATOR_INIT_SIZE: Optional[int] = Field(default=None, ge=1)


class BacktestConfig:
    """Backtest configuration with computed values."""
    def __init__(self):
        raw = _BacktestConfigRaw()
        self.IS_BACKTEST = raw.IS_BACKTEST
        self.BALANCE = raw.BACKTEST_BALANCE
        self.KLINES_LIMIT = raw.BACKTEST_KLINES_LIMIT
        self.INDICATOR_INIT_SIZE = (
            raw.BACKTEST_INDICATOR_INIT_SIZE
            if raw.BACKTEST_INDICATOR_INIT_SIZE is not None
            else int(raw.BACKTEST_KLINES_LIMIT * 0.2)
        )
        self.SAMPLE_SIZE = self.KLINES_LIMIT - self.INDICATOR_INIT_SIZE


class _BracketRaw(BaseSettings):
    """Internal model for raw environment values."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TPSL_STOP_LOSS_RATIO: float = Field(default=0.05, gt=0.0)
    TPSL_TAKE_PROFIT_RATIO: Optional[float] = Field(default=None, gt=0.0)


class Bracket:
    """Bracket order configuration."""
    def __init__(self):
        raw = _BracketRaw()
        self.STOP_LOSS_RATIO = raw.TPSL_STOP_LOSS_RATIO
        self.TAKE_PROFIT_RATIO = (
            raw.TPSL_TAKE_PROFIT_RATIO
            if raw.TPSL_TAKE_PROFIT_RATIO is not None
            else self.STOP_LOSS_RATIO * 2
        )


class _TrailingStopRaw(BaseSettings):
    """Internal model for raw environment values."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TS_ACTIVATION_RATIO: Optional[float] = Field(default=None, gt=0.0)
    TS_CALLBACK_RATIO: Optional[float] = Field(default=None, gt=0.0)


class TrailingStop:
    """Trailing stop configuration."""
    def __init__(self, bracket: Optional['Bracket'] = None):
        raw = _TrailingStopRaw()
        if bracket is None:
            bracket = _BracketClass()
        self.ACTIVATION_RATIO = (
            raw.TS_ACTIVATION_RATIO
            if raw.TS_ACTIVATION_RATIO is not None
            else bracket.TAKE_PROFIT_RATIO
        )
        self.CALLBACK_RATIO = (
            raw.TS_CALLBACK_RATIO
            if raw.TS_CALLBACK_RATIO is not None
            else abs(self.ACTIVATION_RATIO - bracket.STOP_LOSS_RATIO)
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


# Create singleton instances for backward compatibility with class attribute access pattern
# This allows existing code like AppConfig.SYMBOLS to continue working
_RequiredClass = Required
_AppConfigClass = AppConfig
_BacktestConfigClass = BacktestConfig
_BracketClass = Bracket
_TrailingStopClass = TrailingStop

# Initialize in correct order to handle dependencies
Required = _RequiredClass()
AppConfig = _AppConfigClass()
BacktestConfig = _BacktestConfigClass()
Bracket = _BracketClass()
TrailingStop = _TrailingStopClass(bracket=Bracket)
