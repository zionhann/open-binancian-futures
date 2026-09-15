import re

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .execution import ExecutionConfig, validate_integer

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class GlobalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Auth
    api_key: str | None = None
    api_secret: str | None = None
    api_key_test: str | None = None
    api_secret_test: str | None = None

    # App
    strategy: str | None = None
    symbols: str = "BTCUSDT"
    intervals: str = "1d"
    leverage: int = Field(default=1, ge=1)
    size: float = Field(default=0.05, gt=0.0, le=1.0, allow_inf_nan=False)
    is_testnet: bool = False
    gtd_nlines: int = Field(default=1, ge=1)
    webhook_url: str | None = None
    timezone: str = "UTC"

    # Backtest
    is_backtest: bool = Field(default=False)
    balance: float = Field(default=100.0, gt=0.0, allow_inf_nan=False)
    klines_limit: int = Field(default=1000, ge=1, le=1000)
    indicator_init_size: int = Field(default=200, ge=0)
    backtest_start_date: str | None = None
    backtest_end_date: str | None = None
    backtest_data_dir: str | None = None

    @field_validator("leverage", "indicator_init_size", mode="before")
    @classmethod
    def validate_integer_setting(cls, value: object, info: ValidationInfo) -> int:
        field = info.field_name or "integer setting"
        # Environment values are strings; accept integer notation only.
        if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
            value = int(value.strip())
        return validate_integer(value, field, 1 if field == "leverage" else 0)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ExecutionConfig(timezone=value)
        return value

    @property
    def symbols_list(self) -> list[str]:
        return [s.strip() for s in self.symbols.split(",")]

    @property
    def intervals_list(self) -> list[str]:
        return [i.strip() for i in self.intervals.split(",") if i.strip()]

    @property
    def sample_size(self) -> int:
        return self.klines_limit - self.indicator_init_size


settings = GlobalSettings()
