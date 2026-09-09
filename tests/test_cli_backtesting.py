from __future__ import annotations

from pathlib import Path

from open_binancian_futures import cli
from open_binancian_futures.constants import settings


class FakeRunner:
    instances: list["FakeRunner"] = []

    def __init__(self) -> None:
        self.ran = False
        self.__class__.instances.append(self)

    def __enter__(self) -> "FakeRunner":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def run(self) -> None:
        self.ran = True


def test_backtest_cli_accepts_fixed_period_and_cache_directory(monkeypatch, tmp_path: Path) -> None:
    FakeRunner.instances.clear()
    monkeypatch.setattr(cli, "Backtesting", FakeRunner)
    monkeypatch.setattr(settings, "is_backtest", False)

    cli.run(
        strategy="strategy.py",
        backtest=True,
        symbols="ETHUSDT,SOLUSDT",
        intervals="1h,4h",
        start_date="2024-01-01",
        end_date="2024-01-31",
        data_dir=str(tmp_path),
    )

    assert settings.is_backtest is True
    assert settings.symbols == "ETHUSDT,SOLUSDT"
    assert settings.intervals == "1h,4h"
    assert settings.backtest_start_date == "2024-01-01"
    assert settings.backtest_end_date == "2024-01-31"
    assert settings.backtest_data_dir == str(tmp_path)
    assert FakeRunner.instances[-1].ran is True


def test_live_flag_still_selects_live_runner(monkeypatch) -> None:
    FakeRunner.instances.clear()
    monkeypatch.setattr(cli, "LiveTrading", FakeRunner)
    monkeypatch.setattr(settings, "is_backtest", True)

    cli.run(
        strategy="strategy.py",
        testnet=None,
        backtest=False,
        symbols=None,
        intervals=None,
        start_date=None,
        end_date=None,
        data_dir=None,
    )

    assert settings.is_backtest is False
    assert FakeRunner.instances[-1].ran is True
