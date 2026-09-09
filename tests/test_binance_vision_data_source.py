from __future__ import annotations

import io
import zipfile
from collections.abc import Callable

import pandas as pd
import pytest

from open_binancian_futures import BinanceVisionDataSource


VISION_COLUMNS = [
    "Open_time",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
    "Close_time",
    "Quote_volume",
    "Trades",
    "Taker_buy_volume",
    "Taker_buy_quote_volume",
    "Ignore",
]


def archive(
    member_name: str,
    timestamps: list[str],
    *,
    header: bool = True,
) -> bytes:
    rows = []
    for index, timestamp in enumerate(timestamps):
        open_time = int(pd.Timestamp(timestamp, tz="UTC").timestamp() * 1000)
        rows.append(
            [
                open_time,
                100 + index,
                101 + index,
                99 + index,
                100.5 + index,
                10 + index,
                open_time + 3599999,
                1000 + index,
                5 + index,
                4 + index,
                400 + index,
                0,
            ]
        )
    frame = pd.DataFrame(rows, columns=VISION_COLUMNS)
    csv = frame.to_csv(index=False, header=header).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive_file:
        archive_file.writestr(member_name, csv)
    return output.getvalue()


class ArchiveStore:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        try:
            return self.payloads[url]
        except KeyError as error:
            raise FileNotFoundError(url) from error


def source(
    store: Callable[[str], bytes],
    tmp_path,
    *,
    start: str = "2024-01-02",
    end: str = "2024-01-03",
) -> BinanceVisionDataSource:
    return BinanceVisionDataSource(
        start_date=start,
        end_date=end,
        data_dir=tmp_path,
        downloader=store,
    )


def test_monthly_archive_is_loaded_and_end_date_is_inclusive(tmp_path) -> None:
    url = (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        "ETHUSDT/1h/ETHUSDT-1h-2024-01.zip"
    )
    store = ArchiveStore(
        {
            url: archive(
                "ETHUSDT-1h-2024-01.csv",
                [
                    "2024-01-01 00:00",
                    "2024-01-02 00:00",
                    "2024-01-03 00:00",
                    "2024-01-04 00:00",
                ],
            )
        }
    )

    loaded = source(store, tmp_path).load(["ETHUSDT"], ["1h"])

    frame = loaded["ETHUSDT"]["1h"]
    assert list(frame.index) == [
        pd.Timestamp("2024-01-02", tz="UTC"),
        pd.Timestamp("2024-01-03", tz="UTC"),
    ]
    assert frame.loc[frame.index[0], "Open"] == 101.0
    assert pd.api.types.is_datetime64tz_dtype(frame["Close_time"])
    assert store.calls == [url]


def test_headerless_daily_fallback_is_parsed(tmp_path) -> None:
    urls = {
        (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            "ETHUSDT/1h/ETHUSDT-1h-2024-02-02.zip"
        ): archive(
            "ETHUSDT-1h-2024-02-02.csv",
            ["2024-02-02 00:00"],
            header=False,
        ),
        (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            "ETHUSDT/1h/ETHUSDT-1h-2024-02-03.zip"
        ): archive(
            "ETHUSDT-1h-2024-02-03.csv",
            ["2024-02-03 00:00"],
            header=False,
        ),
    }
    store = ArchiveStore(urls)

    loaded = source(
        store,
        tmp_path,
        start="2024-02-02",
        end="2024-02-03",
    ).load(["ETHUSDT"], ["1h"])

    frame = loaded["ETHUSDT"]["1h"]
    assert list(frame.index) == list(
        pd.date_range("2024-02-02", "2024-02-03", freq="D", tz="UTC")
    )
    assert frame["Close"].tolist() == [100.5, 100.5]
    assert store.calls[0].endswith("monthly/klines/ETHUSDT/1h/ETHUSDT-1h-2024-02.zip")
    assert any("daily/klines" in call for call in store.calls)


def test_cached_archive_is_reused_without_downloading_again(tmp_path) -> None:
    url = (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        "ETHUSDT/1h/ETHUSDT-1h-2024-01.zip"
    )
    first_store = ArchiveStore(
        {url: archive("ETHUSDT-1h-2024-01.csv", ["2024-01-02 00:00"])}
    )
    first = source(first_store, tmp_path).load(["ETHUSDT"], ["1h"])

    second_store = ArchiveStore({})
    second = source(second_store, tmp_path).load(["ETHUSDT"], ["1h"])

    pd.testing.assert_frame_equal(first["ETHUSDT"]["1h"], second["ETHUSDT"]["1h"])
    assert first_store.calls == [url]
    assert second_store.calls == []


def test_invalid_period_is_rejected() -> None:
    with pytest.raises(ValueError, match="end_date"):
        BinanceVisionDataSource(start_date="2024-01-03", end_date="2024-01-02")


def test_missing_period_data_has_a_descriptive_error(tmp_path) -> None:
    store = ArchiveStore({})

    with pytest.raises(FileNotFoundError, match="Binance Vision historical data"):
        source(store, tmp_path).load(["ETHUSDT"], ["1h"])
