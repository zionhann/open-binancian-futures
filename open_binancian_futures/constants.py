from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class GlobalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

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
    is_testnet: bool = False
    gtd_nlines: int = Field(default=1, ge=1)
    webhook_url: Optional[str] = None
    timezone: str = "UTC"

    # Backtest
    is_backtest: bool = Field(default=False)
    balance: float = Field(default=100.0, gt=0.0)
    klines_limit: int = Field(default=1000, ge=1, le=1000)
    indicator_init_size: int = Field(default=200, ge=1)

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",")]

    @property
    def sample_size(self) -> int:
        return self.klines_limit - self.indicator_init_size


settings = GlobalSettings()
