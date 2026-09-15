import re
from typing import Any

from pydantic import Field, ValidationInfo, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .execution import ExecutionConfig, validate_integer

INTERVAL_TO_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class _IntegerEnvironmentSource(PydanticBaseSettingsSource):
    def __init__(self, source: PydanticBaseSettingsSource) -> None:
        super().__init__(source.settings_cls)
        self.source = source

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self.source.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        values = self.source()
        for field in ("leverage", "indicator_init_size"):
            value = values.get(field)
            if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
                values[field] = int(value.strip())
        return values


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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Only textual environment sources may parse integer notation. Keep
        # constructor values strict and retain init > env > dotenv > secrets.
        return (
            init_settings,
            _IntegerEnvironmentSource(env_settings),
            _IntegerEnvironmentSource(dotenv_settings),
            file_secret_settings,
        )

    @field_validator("leverage", "indicator_init_size", mode="before")
    @classmethod
    def validate_integer_setting(cls, value: object, info: ValidationInfo) -> int:
        field = info.field_name or "integer setting"
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
