import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from open_binancian_futures.types import *

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}

class GlobalSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Auth
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    api_key_test: Optional[str] = None
    api_secret_test: Optional[str] = None

    # App
    strategy: Optional[str] = None
    symbols: str = "BTCUSDT"
    interval: str = "1d"
    leverage: int = Field(default=1, ge=1)
    size: float = Field(default=0.05, gt=0.0, le=1.0)
    averaging: str = "1"
    max_averaging: int = Field(default=5, ge=1)
    is_testnet: bool = False
    gtd_nlines: int = Field(default=3, ge=1)
    webhook_url: Optional[str] = None
    timezone: str = "UTC"

    # Backtest (Supports legacy ENV names)
    is_backtest: bool = Field(default=False, validation_alias="BACKTEST")
    balance: float = Field(default=100.0, gt=0.0, validation_alias="BACKTEST_BALANCE")
    klines_limit: int = Field(default=1000, ge=1, le=1000, validation_alias="BACKTEST_KLINES_LIMIT")
    indicator_init_size: Optional[int] = Field(default=None, ge=1, validation_alias="BACKTEST_INDICATOR_INIT_SIZE")

    # Risk (Supports legacy ENV names)
    stop_loss_ratio: float = Field(default=0.05, gt=0.0, validation_alias="TPSL_STOP_LOSS_RATIO")
    take_profit_ratio: Optional[float] = Field(default=None, gt=0.0, validation_alias="TPSL_TAKE_PROFIT_RATIO")
    activation_ratio: Optional[float] = Field(default=None, gt=0.0, validation_alias="TS_ACTIVATION_RATIO")
    callback_ratio: Optional[float] = Field(default=None, gt=0.0, validation_alias="TS_CALLBACK_RATIO")

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",")]

    @property
    def averaging_list(self) -> list[float]:
        return [float(r.strip()) for r in self.averaging.split(",")]

    @property
    def backtest_indicator_init_size(self) -> int:
        return self.indicator_init_size or int(self.klines_limit * 0.2)

    @property
    def backtest_sample_size(self) -> int:
        return self.klines_limit - self.backtest_indicator_init_size

    @property
    def effective_take_profit_ratio(self) -> float:
        return self.take_profit_ratio or (self.stop_loss_ratio * 2)

    @property
    def effective_activation_ratio(self) -> float:
        return self.activation_ratio or self.effective_take_profit_ratio

    @property
    def effective_callback_ratio(self) -> float:
        return self.callback_ratio or abs(self.effective_activation_ratio - self.stop_loss_ratio)

settings = GlobalSettings()

def reload_settings():
    global settings
    settings = GlobalSettings()
