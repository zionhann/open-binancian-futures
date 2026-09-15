"""Public constructors and CLI environment settings share numeric boundaries."""

import pytest

from open_binancian_futures import BacktestConfig, ExecutionConfig
from open_binancian_futures.constants import GlobalSettings


@pytest.mark.parametrize("factory", [ExecutionConfig, BacktestConfig])
@pytest.mark.parametrize("value", [True, False, 1.0, 1.5, float("nan"), float("inf"), 0, -1, "2", None])
def test_invalid_leverage(factory, value):
    with pytest.raises(ValueError, match="leverage"):
        factory(leverage=value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_invalid_initial_balance(value):
    with pytest.raises(ValueError, match="initial_balance"):
        BacktestConfig(initial_balance=value)


@pytest.mark.parametrize("factory", [ExecutionConfig, BacktestConfig])
@pytest.mark.parametrize("value", [0, -0.1, 1.1, float("nan"), float("inf"), -float("inf")])
def test_invalid_position_size(factory, value):
    with pytest.raises(ValueError, match="position_size"):
        factory(position_size=value)


@pytest.mark.parametrize("value", [True, False, 0.0, 1.5, -1, float("nan"), float("inf"), "0", None])
def test_invalid_warmup(value):
    with pytest.raises(ValueError, match="warmup_bars"):
        BacktestConfig(warmup_bars=value)


@pytest.mark.parametrize("field", ["leverage", "indicator_init_size"])
@pytest.mark.parametrize("value", [True, False, 1.0, 1.5, float("nan"), float("inf"), -1, "1.0", "1e0"])
def test_settings_reject_integer_coercion(field, value):
    with pytest.raises(ValueError, match=field):
        GlobalSettings(_env_file=None, **{field: value})


@pytest.mark.parametrize("field", ["balance", "size"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_settings_reject_invalid_numeric_values(field, value):
    with pytest.raises(ValueError, match=field):
        GlobalSettings(_env_file=None, **{field: value})


@pytest.mark.parametrize("field,value", [("leverage", "1.0"), ("leverage", "0"), ("indicator_init_size", "0.0"), ("balance", "inf"), ("size", "nan"), ("timezone", "Not/AZone")])
def test_cli_environment_rejects_invalid_values(monkeypatch, field, value):
    monkeypatch.setenv(field.upper(), value)
    with pytest.raises(ValueError, match=field):
        GlobalSettings(_env_file=None)


def test_cli_environment_accepts_integer_strings_and_zero_warmup(monkeypatch):
    monkeypatch.setenv("LEVERAGE", "2")
    monkeypatch.setenv("INDICATOR_INIT_SIZE", "0")
    monkeypatch.setenv("BALANCE", "0.01")
    monkeypatch.setenv("SIZE", "1")
    monkeypatch.setenv("TIMEZONE", "Asia/Seoul")
    settings = GlobalSettings(_env_file=None)
    assert settings.leverage == 2
    assert type(settings.leverage) is int
    assert settings.indicator_init_size == 0
    assert settings.balance == 0.01
    assert settings.size == 1
    assert settings.timezone == "Asia/Seoul"


@pytest.mark.parametrize("warmup", [0, 1])
@pytest.mark.parametrize("size", [0.0001, 1.0])
def test_valid_boundaries_and_positional_order(warmup, size):
    config = BacktestConfig(0.01, 1, warmup, "1h", "union", position_size=size)
    assert config.initial_balance == 0.01
    assert config.leverage == 1
    assert config.warmup_bars == warmup
    assert config.interval == "1h"
    assert config.timeline_mode == "union"
    assert config.execution_config.position_size == size
