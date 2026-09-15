"""Closed-candle recovery with fixed cutoff and continuity validation."""

from typing import Any

import pandas as pd

from .exchange_adapter import ExchangeAdapter


def next_open(timestamp: int, interval: str) -> int:
    value = pd.Timestamp(timestamp, unit="ms", tz="UTC")
    if interval.endswith("M"):
        value += pd.DateOffset(months=int(interval[:-1]))
    else:
        units = {"m": "min", "h": "h", "d": "D", "w": "W", "s": "s"}
        value += pd.Timedelta(f"{int(interval[:-1])}{units[interval[-1]]}")
    return int(value.timestamp() * 1000)


def recover_history(
    adapter: ExchangeAdapter, symbol: str, interval: str, last_open: int, cutoff: int
) -> list[list[Any]]:
    expected = next_open(last_open, interval)
    cursor = last_open + 1
    result: list[list[Any]] = []
    while next_open(expected, interval) <= cutoff:
        page = adapter.history(symbol, interval, cursor, cutoff, limit=1000)
        if not page:
            raise ValueError(f"Historical gap for {symbol} {interval} at {expected}")
        maximum = max(int(row[0]) for row in page)
        if maximum < cursor:
            raise ValueError("Historical page failed to advance")
        unique = {int(row[0]): row for row in page}
        for opened, row in sorted(unique.items()):
            if opened < expected:
                continue
            if int(row[6]) >= cutoff:
                continue
            if opened != expected:
                raise ValueError(
                    f"Historical gap for {symbol} {interval} at {expected}"
                )
            if int(row[6]) != next_open(opened, interval) - 1:
                raise ValueError("Invalid candle close time")
            result.append(row)
            expected = next_open(opened, interval)
        cursor = maximum + 1
    return result
