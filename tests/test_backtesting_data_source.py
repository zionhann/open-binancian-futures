from __future__ import annotations

import pandas as pd

from open_binancian_futures import DataFrameDataSource, build_timeline


def frame(symbol: str, timestamps: list[pd.Timestamp]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open_time": timestamps,
            "Symbol": [symbol] * len(timestamps),
            "Open": [100.0 + index for index in range(len(timestamps))],
            "High": [101.0 + index for index in range(len(timestamps))],
            "Low": [99.0 + index for index in range(len(timestamps))],
            "Close": [100.5 + index for index in range(len(timestamps))],
            "Volume": [1.0] * len(timestamps),
        }
    )


def test_data_source_sorts_each_symbol_and_aligns_by_timestamp() -> None:
    timestamps = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    loaded = DataFrameDataSource(
        {
            "ETHUSDT": frame("ETHUSDT", [timestamps[2], timestamps[0], timestamps[3], timestamps[1]]),
            "SOLUSDT": frame("SOLUSDT", [timestamps[3], timestamps[1], timestamps[0]]),
        },
        interval="1h",
    ).load(["ETHUSDT", "SOLUSDT"], ["1h"])

    assert list(loaded["ETHUSDT"]["1h"].index) == list(timestamps)
    assert list(loaded["SOLUSDT"]["1h"].index) == [
        timestamps[0],
        timestamps[1],
        timestamps[3],
    ]
    assert list(
        build_timeline(
            {symbol: loaded[symbol]["1h"] for symbol in loaded},
            warmup_bars=1,
        )
    ) == [timestamps[1], timestamps[3]]


def test_csv_data_source_is_credential_free(tmp_path) -> None:
    timestamps = pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")
    path = tmp_path / "candles.csv"
    frame("ETHUSDT", list(timestamps)).to_csv(path, index=False)

    from open_binancian_futures import CsvDataSource

    loaded = CsvDataSource(path, symbol="ETHUSDT", interval="1h").load(
        ["ETHUSDT"], ["1h"]
    )
    assert len(loaded["ETHUSDT"]["1h"]) == 2


def test_nested_interval_mapping_is_preserved() -> None:
    timestamps = pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")
    source = DataFrameDataSource(
        {
            "ETHUSDT": {
                "1h": frame("ETHUSDT", list(timestamps)),
                "4h": frame("ETHUSDT", list(timestamps)),
            }
        }
    )

    loaded = source.load([], ["1h"])

    assert set(loaded["ETHUSDT"]) == {"1h", "4h"}
