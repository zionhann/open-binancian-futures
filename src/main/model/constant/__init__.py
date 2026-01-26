"""
Application configuration using Pydantic Settings with class attributes.

Configuration values are loaded from environment variables (or .env file),
validated using Pydantic, and exposed as class attributes.

Access configuration values directly from class attributes:

    AppConfig.SYMBOLS       # Returns list[str]
    AppConfig.LEVERAGE      # Returns int
    Bracket.STOP_LOSS_RATIO # Returns float

Enum values require .value suffix:
    OrderType.LIMIT.value   # Returns "LIMIT"
    PositionSide.BUY.value  # Returns "BUY"
"""

from enum import Enum
from typing import Optional

from pydantic import Field, field_validator, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class _RequiredRaw(BaseSettings):
    """
    Internal model for raw environment values.
    Either (API_KEY, API_SECRET) or (API_KEY_TEST, API_SECRET_TEST) is required.
    STRATEGY: The class name of the strategy to use. It must be located in the `strategy` module.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    API_KEY: Optional[str] = None
    API_SECRET: Optional[str] = None
    API_KEY_TEST: Optional[str] = None
    API_SECRET_TEST: Optional[str] = None
    STRATEGY: Optional[str] = None


class Required:
    """Required configuration with class attributes."""

    API_KEY: Optional[str]
    API_SECRET: Optional[str]
    API_KEY_TEST: Optional[str]
    API_SECRET_TEST: Optional[str]
    STRATEGY: Optional[str]


class _AppConfigRaw(BaseSettings):
    """Internal model for raw environment values."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SYMBOLS: str = "BTCUSDT"
    INTERVAL: str = "1d"
    LEVERAGE: int = Field(default=1, ge=1)
    SIZE: float = Field(default=0.05, gt=0.0, le=1.0)
    AVERAGING: str = "1"
    MAX_AVERAGING: int = Field(default=5, ge=1)
    IS_TESTNET: bool = False
    GTD_NLINES: int = Field(default=3, ge=1)
    WEBHOOK_URL: Optional[str] = None
    TIMEZONE: str = "UTC"


class AppConfig:
    """Application configuration with class attributes."""

    SYMBOLS: list[str]
    INTERVAL: str
    LEVERAGE: int
    SIZE: float
    AVERAGING: list[float]
    MAX_AVERAGING: int
    IS_TESTNET: bool
    GTD_NLINES: int
    WEBHOOK_URL: Optional[str]
    TIMEZONE: str


class _BacktestConfigRaw(BaseSettings):
    """Internal model for raw environment values."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    IS_BACKTEST: bool = False
    BACKTEST_BALANCE: float = Field(default=100.0, gt=0.0)
    BACKTEST_KLINES_LIMIT: int = Field(default=1000, ge=1, le=1000)
    BACKTEST_INDICATOR_INIT_SIZE: Optional[int] = Field(default=None, ge=1)


class BacktestConfig:
    """Backtest configuration with class attributes."""

    IS_BACKTEST: bool
    BALANCE: float
    KLINES_LIMIT: int
    INDICATOR_INIT_SIZE: int
    SAMPLE_SIZE: int


class _BracketRaw(BaseSettings):
    """Internal model for raw environment values."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TPSL_STOP_LOSS_RATIO: float = Field(default=0.05, gt=0.0)
    TPSL_TAKE_PROFIT_RATIO: Optional[float] = Field(default=None, gt=0.0)


class Bracket:
    """Bracket order configuration with class attributes."""

    STOP_LOSS_RATIO: float
    TAKE_PROFIT_RATIO: float


class _TrailingStopRaw(BaseSettings):
    """Internal model for raw environment values."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TS_ACTIVATION_RATIO: Optional[float] = Field(default=None, gt=0.0)
    TS_CALLBACK_RATIO: Optional[float] = Field(default=None, gt=0.0)


class TrailingStop:
    """Trailing stop configuration with class attributes."""

    ACTIVATION_RATIO: float
    CALLBACK_RATIO: float


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


# ==============================================================================
# CLASS ATTRIBUTE INITIALIZATION
# ==============================================================================
# Load validated configuration from Pydantic and set as class attributes
# This preserves type safety while maintaining Pydantic validation benefits
# ==============================================================================

# Initialize Required (no processing needed)
_required_raw = _RequiredRaw()
Required.API_KEY = _required_raw.API_KEY
Required.API_SECRET = _required_raw.API_SECRET
Required.API_KEY_TEST = _required_raw.API_KEY_TEST
Required.API_SECRET_TEST = _required_raw.API_SECRET_TEST
Required.STRATEGY = _required_raw.STRATEGY

# Initialize AppConfig with parsed values
_app_config_raw = _AppConfigRaw()
AppConfig.SYMBOLS = [s.strip() for s in _app_config_raw.SYMBOLS.split(",")]
AppConfig.INTERVAL = _app_config_raw.INTERVAL
AppConfig.LEVERAGE = _app_config_raw.LEVERAGE
AppConfig.SIZE = _app_config_raw.SIZE
AppConfig.AVERAGING = [
    float(ratio.strip()) for ratio in _app_config_raw.AVERAGING.split(",")
]
AppConfig.MAX_AVERAGING = _app_config_raw.MAX_AVERAGING
AppConfig.IS_TESTNET = _app_config_raw.IS_TESTNET
AppConfig.GTD_NLINES = _app_config_raw.GTD_NLINES
AppConfig.WEBHOOK_URL = _app_config_raw.WEBHOOK_URL
AppConfig.TIMEZONE = _app_config_raw.TIMEZONE

# Initialize BacktestConfig with computed values
_backtest_raw = _BacktestConfigRaw()
BacktestConfig.IS_BACKTEST = _backtest_raw.IS_BACKTEST
BacktestConfig.BALANCE = _backtest_raw.BACKTEST_BALANCE
BacktestConfig.KLINES_LIMIT = _backtest_raw.BACKTEST_KLINES_LIMIT
BacktestConfig.INDICATOR_INIT_SIZE = (
    _backtest_raw.BACKTEST_INDICATOR_INIT_SIZE
    if _backtest_raw.BACKTEST_INDICATOR_INIT_SIZE is not None
    else int(_backtest_raw.BACKTEST_KLINES_LIMIT * 0.2)
)
BacktestConfig.SAMPLE_SIZE = (
    BacktestConfig.KLINES_LIMIT - BacktestConfig.INDICATOR_INIT_SIZE
)

# Initialize Bracket with computed values
_bracket_raw = _BracketRaw()
Bracket.STOP_LOSS_RATIO = _bracket_raw.TPSL_STOP_LOSS_RATIO
Bracket.TAKE_PROFIT_RATIO = (
    _bracket_raw.TPSL_TAKE_PROFIT_RATIO
    if _bracket_raw.TPSL_TAKE_PROFIT_RATIO is not None
    else Bracket.STOP_LOSS_RATIO * 2
)

# Initialize TrailingStop with computed values (depends on Bracket)
_trailing_stop_raw = _TrailingStopRaw()
TrailingStop.ACTIVATION_RATIO = (
    _trailing_stop_raw.TS_ACTIVATION_RATIO
    if _trailing_stop_raw.TS_ACTIVATION_RATIO is not None
    else Bracket.TAKE_PROFIT_RATIO
)
TrailingStop.CALLBACK_RATIO = (
    _trailing_stop_raw.TS_CALLBACK_RATIO
    if _trailing_stop_raw.TS_CALLBACK_RATIO is not None
    else abs(TrailingStop.ACTIVATION_RATIO - Bracket.STOP_LOSS_RATIO)
)
