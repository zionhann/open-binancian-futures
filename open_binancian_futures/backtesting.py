"""Deterministic, dependency-injectable backtesting domain objects.

The runner lives in :mod:`open_binancian_futures.runners`; this module contains
the policies and result objects that make injected-data runs reproducible
without Binance credentials.
"""

from __future__ import annotations

import io
import logging
import os
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .models import Indicator, Order, OrderIntent
from .types import OrderType, PositionSide

LOGGER = logging.getLogger(__name__)


def _profit_factor(profit: float, loss: float) -> float:
    """Return gross profit divided by gross loss with an explicit no-loss case."""
    if loss < 0:
        return profit / abs(loss)
    return float("inf") if profit > 0 else 0.0


@dataclass(frozen=True)
class Candle:
    """One completed OHLC candle used by a deterministic backtest."""

    time: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    symbol: str | None = None
    volume: float | None = None

    def __post_init__(self) -> None:
        timestamp = pd.Timestamp(self.time)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        object.__setattr__(self, "time", timestamp)
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, float(getattr(self, name)))
        if self.volume is not None:
            object.__setattr__(self, "volume", float(self.volume))

    @classmethod
    def from_series(cls, row: Any) -> Candle:
        def value(name: str, default: Any = None) -> Any:
            if hasattr(row, "get"):
                return row.get(name, default)
            return getattr(row, name, default)

        timestamp = value("Open_time")
        if timestamp is None:
            timestamp = value("timestamp")
        if timestamp is None:
            timestamp = getattr(row, "name", None)
        if timestamp is None:
            raise ValueError("A candle requires an Open_time or timestamp")

        return cls(
            time=pd.Timestamp(timestamp),
            open=float(value("Open")),
            high=float(value("High")),
            low=float(value("Low")),
            close=float(value("Close")),
            symbol=value("Symbol"),
            volume=(
                float(value("Volume"))
                if value("Volume") is not None
                else None
            ),
        )


class MarketExecutionPolicy(str, Enum):
    """When a market order is filled relative to a completed candle."""

    CLOSE = "close"
    NEXT_OPEN = "next_open"


@dataclass(frozen=True)
class ZeroCostModel:
    """Default cost model: no commission, slippage, or funding."""

    def cost(
        self,
        order: Order | None,
        fill_price: float,
        quantity: float,
        timestamp: pd.Timestamp,
    ) -> float:
        return 0.0


class CostModel(Protocol):
    """Protocol for an optional backtest cost model."""

    def cost(
        self,
        order: Order | None,
        fill_price: float,
        quantity: float,
        timestamp: pd.Timestamp,
    ) -> float: ...


class FillPolicy(Protocol):
    """Protocol for candle-level order fill and exit selection policies."""

    def fill_price(self, order: Order, candle: Candle) -> float | None: ...

    def select_exit(
        self,
        orders: Sequence[Order],
        candle: Candle,
        position_side: PositionSide,
    ) -> tuple[Order, float] | None: ...


@dataclass(frozen=True)
class DeterministicFillPolicy:
    """Deterministic OHLC fill policy used by the default engine."""

    market_execution: MarketExecutionPolicy = MarketExecutionPolicy.CLOSE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "market_execution",
            MarketExecutionPolicy(self.market_execution),
        )

    @property
    def stop_loss_priority(self) -> int:
        return 0

    @property
    def take_profit_priority(self) -> int:
        return 1

    @staticmethod
    def _fill_limit(order: Order, candle: Candle) -> float | None:
        if order.side == PositionSide.BUY:
            if candle.open <= order.price:
                return candle.open
            return order.price if candle.low <= order.price else None
        if candle.open >= order.price:
            return candle.open
        return order.price if candle.high >= order.price else None

    @staticmethod
    def _fill_stop(order: Order, candle: Candle) -> float | None:
        if order.side == PositionSide.BUY:
            if candle.open >= order.price:
                return candle.open
            return order.price if candle.high >= order.price else None
        if candle.open <= order.price:
            return candle.open
        return order.price if candle.low <= order.price else None

    @staticmethod
    def _fill_take_profit(order: Order, candle: Candle) -> float | None:
        if order.side == PositionSide.BUY:
            if candle.open <= order.price:
                return candle.open
            return order.price if candle.low <= order.price else None
        if candle.open >= order.price:
            return candle.open
        return order.price if candle.high >= order.price else None

    def fill_price(self, order: Order, candle: Candle) -> float | None:
        if order.type == OrderType.MARKET:
            if self.market_execution == MarketExecutionPolicy.NEXT_OPEN:
                return candle.open
            return candle.close
        if order.type in {OrderType.LIMIT, OrderType.TAKE_PROFIT_LIMIT}:
            return (
                self._fill_take_profit(order, candle)
                if order.type == OrderType.TAKE_PROFIT_LIMIT
                else self._fill_limit(order, candle)
            )
        if order.type in {OrderType.STOP_LIMIT, OrderType.STOP_MARKET}:
            return self._fill_stop(order, candle)
        if order.type == OrderType.TAKE_PROFIT_MARKET:
            return self._fill_take_profit(order, candle)
        return None

    def select_exit(
        self,
        orders: Sequence[Order],
        candle: Candle,
        position_side: PositionSide,
    ) -> tuple[Order, float] | None:
        close_side = (
            PositionSide.SELL
            if position_side == PositionSide.BUY
            else PositionSide.BUY
        )
        priority = {
            OrderType.LIMIT: self.take_profit_priority,
            OrderType.STOP_MARKET: self.stop_loss_priority,
            OrderType.STOP_LIMIT: self.stop_loss_priority,
            OrderType.TAKE_PROFIT_MARKET: self.take_profit_priority,
            OrderType.TAKE_PROFIT_LIMIT: self.take_profit_priority,
            OrderType.MARKET: 2,
        }
        candidates: list[tuple[int, int, Order, float]] = []
        for sequence, order in enumerate(orders):
            if order.side != close_side or order.type not in priority:
                continue
            fill = self.fill_price(order, candle)
            if fill is not None:
                candidates.append((priority[order.type], sequence, order, fill))
        if not candidates:
            return None
        _, _, order, fill = min(candidates, key=lambda item: (item[0], item[1]))
        return order, fill


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration shared by the deterministic backtest engine."""

    initial_balance: float = 100.0
    leverage: int = 1
    warmup_bars: int = 0
    interval: str | None = None
    timeline_mode: str = "intersection"
    fill_policy: FillPolicy | None = None
    cost_model: CostModel = field(default_factory=ZeroCostModel)
    market_execution: MarketExecutionPolicy = MarketExecutionPolicy.CLOSE

    def __post_init__(self) -> None:
        if self.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if self.leverage < 1:
            raise ValueError("leverage must be at least one")
        if self.warmup_bars < 0:
            raise ValueError("warmup_bars must not be negative")
        if self.timeline_mode not in {"intersection", "union"}:
            raise ValueError("timeline_mode must be 'intersection' or 'union'")
        object.__setattr__(
            self,
            "market_execution",
            MarketExecutionPolicy(self.market_execution),
        )
        if self.fill_policy is None:
            object.__setattr__(
                self,
                "fill_policy",
                DeterministicFillPolicy(self.market_execution),
            )


class HistoricalDataSource(Protocol):
    """Source protocol for completed OHLCV frames.

    A source may expose an optional ``start_time`` attribute.  When present,
    the runner uses it as the exact evaluation boundary after warm-up data.
    """

    def load(
        self, symbols: Sequence[str], intervals: Sequence[str]
    ) -> Indicator: ...


def normalize_ohlcv_frame(
    frame: pd.DataFrame, *, symbol: str | None = None
) -> pd.DataFrame:
    """Return a UTC-indexed, chronologically sorted OHLCV frame."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("historical data must be a pandas DataFrame")
    normalized = frame.copy()
    if "Open_time" not in normalized.columns:
        normalized["Open_time"] = normalized.index
    required = {"Open_time", "Open", "High", "Low", "Close"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(f"Missing OHLC columns: {sorted(missing)}")
    normalized["Open_time"] = pd.to_datetime(normalized["Open_time"], utc=True)
    if normalized["Open_time"].duplicated().any():
        raise ValueError("Duplicate candle timestamps are not supported")
    if symbol is not None:
        normalized["Symbol"] = symbol
    elif "Symbol" not in normalized.columns:
        normalized["Symbol"] = ""
    if "Volume" not in normalized.columns:
        normalized["Volume"] = 0.0
    for column in ("Open", "High", "Low", "Close", "Volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="raise").astype(float)
    normalized.index.name = None
    normalized = normalized.sort_values("Open_time", kind="mergesort")
    normalized = normalized.set_index("Open_time", drop=False)
    normalized.index.name = "Open_time"
    return normalized


def build_timeline(
    frames: Mapping[str, pd.DataFrame],
    warmup_bars: int,
    mode: str = "intersection",
    *,
    start_time: pd.Timestamp | None = None,
) -> pd.DatetimeIndex:
    """Build a sorted timestamp timeline after per-symbol warm-up.

    ``start_time`` is useful for sources that load an exact warm-up context but
    expose a requested evaluation boundary that cannot be represented by a
    fixed number of rows (for example, sparse daily candles).
    """

    if warmup_bars < 0:
        raise ValueError("warmup_bars must not be negative")
    if mode not in {"intersection", "union"}:
        raise ValueError("mode must be 'intersection' or 'union'")
    normalized_start = None
    if start_time is not None:
        normalized_start = pd.Timestamp(start_time)
        if normalized_start.tzinfo is None:
            normalized_start = normalized_start.tz_localize("UTC")
        else:
            normalized_start = normalized_start.tz_convert("UTC")
    timeline: pd.DatetimeIndex | None = None
    for frame in frames.values():
        index = pd.DatetimeIndex(frame.index).sort_values()
        if normalized_start is not None:
            index = index[index >= normalized_start]
        else:
            index = index[warmup_bars:]
        if timeline is None:
            timeline = index
        elif mode == "intersection":
            timeline = timeline.intersection(index)
        else:
            timeline = timeline.union(index)
    if timeline is None:
        return pd.DatetimeIndex([], tz="UTC")
    return timeline.sort_values()


class DataFrameDataSource:
    """Credential-free source backed by one or more DataFrames."""

    def __init__(
        self,
        data: pd.DataFrame | Mapping[str, Any],
        interval: str = "1d",
        *,
        symbol: str | None = None,
    ) -> None:
        self.data = data
        self.interval = interval
        self.symbol = symbol

    def _frames(self, symbols: Sequence[str]) -> dict[str, pd.DataFrame]:
        if isinstance(self.data, pd.DataFrame):
            if "Symbol" in self.data.columns:
                grouped = {
                    str(symbol): group.assign(Symbol=str(symbol))
                    for symbol, group in self.data.groupby("Symbol", sort=False)
                }
            elif self.symbol is not None:
                grouped = {self.symbol: self.data}
            elif len(symbols) == 1:
                grouped = {symbols[0]: self.data}
            else:
                raise ValueError(
                    "A symbol is required for a DataFrame without a Symbol column"
                )
        else:
            if any(isinstance(frame, Mapping) for frame in self.data.values()):
                raise TypeError("Nested interval data must be loaded through load()")
            grouped = {str(symbol): frame for symbol, frame in self.data.items()}
        selected = list(symbols) or list(grouped)
        missing = [symbol for symbol in selected if symbol not in grouped]
        if missing:
            raise KeyError(f"Missing historical data for symbols: {missing}")
        return {symbol: grouped[symbol] for symbol in selected}

    def load(self, symbols: Sequence[str], intervals: Sequence[str]) -> Indicator:
        if isinstance(self.data, Mapping) and self.data and all(
            isinstance(interval_frames, Mapping)
            for interval_frames in self.data.values()
        ):
            nested = {str(symbol): interval_frames for symbol, interval_frames in self.data.items()}
            selected = list(symbols) or list(nested)
            missing = [symbol for symbol in selected if symbol not in nested]
            if missing:
                raise KeyError(f"Missing historical data for symbols: {missing}")
            result = Indicator()
            for symbol in selected:
                for interval, frame in nested[symbol].items():
                    if not isinstance(frame, pd.DataFrame):
                        raise TypeError(
                            f"Historical data for {symbol} must be a DataFrame"
                        )
                    result[symbol][str(interval)] = normalize_ohlcv_frame(
                        frame, symbol=symbol
                    )
            return result

        interval = intervals[0] if intervals else self.interval
        result = Indicator()
        for symbol, frame in self._frames(symbols).items():
            result[symbol][interval] = normalize_ohlcv_frame(frame, symbol=symbol)
        return result


class CsvDataSource(DataFrameDataSource):
    """Credential-free source backed by a CSV file."""

    def __init__(
        self,
        path: str | Path,
        *,
        symbol: str | None = None,
        interval: str = "1d",
    ) -> None:
        self.path = Path(path)
        self.symbol = symbol
        super().__init__(pd.DataFrame(), interval=interval, symbol=symbol)

    def load(self, symbols: Sequence[str], intervals: Sequence[str]) -> Indicator:
        frame = pd.read_csv(self.path)
        has_symbol_column = "Symbol" in frame.columns
        if not has_symbol_column and self.symbol is not None:
            frame["Symbol"] = self.symbol
        self.data = frame
        selected = list(symbols)
        if not selected and not has_symbol_column and self.symbol:
            selected = [self.symbol]
        return super().load(selected, intervals)


class ParquetDataSource(CsvDataSource):
    """Credential-free source backed by a Parquet file."""

    def load(self, symbols: Sequence[str], intervals: Sequence[str]) -> Indicator:
        frame = pd.read_parquet(self.path)
        has_symbol_column = "Symbol" in frame.columns
        if not has_symbol_column and self.symbol is not None:
            frame["Symbol"] = self.symbol
        self.data = frame
        selected = list(symbols)
        if not selected and not has_symbol_column and self.symbol:
            selected = [self.symbol]
        return DataFrameDataSource.load(self, selected, intervals)


VISION_KLINE_COLUMNS = [
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


VisionDownloader = Callable[[str], bytes]


class BinanceVisionDataSource:
    """Load USDⓈ-M historical klines from Binance Vision archives.

    ``start_date`` is inclusive.  A date-only ``end_date`` is inclusive for
    the whole UTC calendar day; timestamp end values use the normal exclusive
    upper bound.  Monthly archives are preferred and daily archives are used
    only when the monthly archive is unavailable.

    The downloader is injectable for deterministic tests and private mirrors.
    Downloaded ZIP files are cached below ``data_dir`` and are reused without a
    network request on subsequent loads.
    """

    DEFAULT_BASE_URL = "https://data.binance.vision/data/futures"

    def __init__(
        self,
        start_date: str | date | datetime | pd.Timestamp,
        end_date: str | date | datetime | pd.Timestamp,
        *,
        data_dir: str | Path | None = None,
        market: str = "um",
        symbols: Sequence[str] | None = None,
        intervals: Sequence[str] | None = None,
        warmup_bars: int = 0,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 30.0,
        downloader: VisionDownloader | None = None,
    ) -> None:
        self.start_time = self._as_utc(start_date, "start_date")
        self.end_time = self._as_utc(end_date, "end_date")
        if self._is_date_only(end_date):
            self.end_time += pd.Timedelta(days=1)
        if self.end_time <= self.start_time:
            raise ValueError("end_date must be after start_date")

        normalized_market = market.strip().lower()
        if normalized_market not in {"um", "cm"}:
            raise ValueError("market must be 'um' or 'cm'")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if warmup_bars < 0:
            raise ValueError("warmup_bars must not be negative")

        self.data_dir = Path(data_dir) if data_dir is not None else (
            Path.home() / ".cache" / "open-binancian-futures" / "binance-vision"
        )
        self.market = normalized_market
        self.symbols = tuple(symbols or ())
        self.intervals = tuple(intervals or ())
        self.interval = self.intervals[0] if self.intervals else None
        self.warmup_bars = warmup_bars
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._downloader = downloader or self._download

    @staticmethod
    def _is_date_only(value: object) -> bool:
        if isinstance(value, date) and not isinstance(value, datetime):
            return True
        if isinstance(value, str):
            return len(value.strip()) == 10 and value.strip()[4] == "-"
        return False

    @staticmethod
    def _as_utc(value: object, name: str) -> pd.Timestamp:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{name} must be an ISO date or timestamp") from error
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC")

    @staticmethod
    def _month_starts(
        start_time: pd.Timestamp, end_time: pd.Timestamp
    ) -> list[pd.Timestamp]:
        first = start_time.normalize().replace(day=1)
        last_day = (end_time - pd.Timedelta(nanoseconds=1)).normalize()
        last = last_day.replace(day=1)
        return list(pd.date_range(first, last, freq="MS", tz="UTC"))

    def _load_start_time(self, interval: str) -> pd.Timestamp:
        if self.warmup_bars == 0:
            return self.start_time
        match = re.fullmatch(r"(\d+)([mhdwM])", interval)
        if match is None:
            raise ValueError(
                f"Cannot calculate warm-up period for unsupported interval '{interval}'"
            )
        amount = self.warmup_bars * int(match.group(1))
        unit = match.group(2)
        if unit == "M":
            return self.start_time - pd.DateOffset(months=amount)
        if unit == "w":
            return self.start_time - pd.Timedelta(weeks=amount)
        if unit == "d":
            return self.start_time - pd.Timedelta(days=amount)
        if unit == "h":
            return self.start_time - pd.Timedelta(hours=amount)
        return self.start_time - pd.Timedelta(minutes=amount)

    @staticmethod
    def _days_in_month(
        month: pd.Timestamp,
        start_time: pd.Timestamp,
        end_time: pd.Timestamp,
    ) -> list[pd.Timestamp]:
        month_end = month + pd.offsets.MonthBegin(1)
        start = max(month, start_time.normalize())
        end = min(month_end, end_time)
        if end <= start:
            return []
        last_day = (end - pd.Timedelta(nanoseconds=1)).normalize()
        return list(pd.date_range(start.normalize(), last_day, freq="D", tz="UTC"))

    def _archive_url(
        self,
        archive_type: str,
        symbol: str,
        interval: str,
        timestamp: pd.Timestamp,
    ) -> str:
        filename = (
            f"{symbol}-{interval}-{timestamp.year:04d}-{timestamp.month:02d}.zip"
            if archive_type == "monthly"
            else f"{symbol}-{interval}-{timestamp.year:04d}-{timestamp.month:02d}-{timestamp.day:02d}.zip"
        )
        return (
            f"{self.base_url}/{self.market}/{archive_type}/klines/"
            f"{symbol}/{interval}/{filename}"
        )

    def _archive_path(self, archive_type: str, symbol: str, interval: str, timestamp: pd.Timestamp) -> Path:
        filename = self._archive_url(archive_type, symbol, interval, timestamp).rsplit("/", 1)[-1]
        return (
            self.data_dir
            / self.market
            / archive_type
            / "klines"
            / symbol
            / interval
            / filename
        )

    def _download(self, url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise FileNotFoundError(url) from error
            raise

    @staticmethod
    def _parse_archive(payload: bytes, url: str, symbol: str) -> pd.DataFrame:
        expected_member = url.rsplit("/", 1)[-1][:-4] + ".csv"
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive_file:
                members = sorted(
                    name
                    for name in archive_file.namelist()
                    if name.lower().endswith(".csv")
                )
                if not members:
                    raise ValueError(
                        f"Binance Vision archive contains no CSV: {url}"
                    )
                member = next(
                    (name for name in members if Path(name).name == expected_member),
                    members[0],
                )
                raw = pd.read_csv(
                    io.BytesIO(archive_file.read(member)),
                    header=None,
                )

            if not raw.empty and str(raw.iloc[0, 0]).strip().lower() in {
                "open_time",
                "open time",
            }:
                raw = raw.iloc[1:].reset_index(drop=True)
            if raw.shape[1] < len(VISION_KLINE_COLUMNS):
                raise ValueError(
                    f"Binance Vision kline archive has {raw.shape[1]} columns; "
                    f"expected at least {len(VISION_KLINE_COLUMNS)}: {url}"
                )
            raw = raw.iloc[:, : len(VISION_KLINE_COLUMNS)]
            raw.columns = VISION_KLINE_COLUMNS
            for column in ("Open", "High", "Low", "Close", "Volume"):
                raw[column] = pd.to_numeric(raw[column], errors="raise").astype(float)
            for column in ("Open_time", "Close_time"):
                raw[column] = pd.to_datetime(
                    pd.to_numeric(raw[column], errors="raise"),
                    unit="ms",
                    utc=True,
                )
            return normalize_ohlcv_frame(raw, symbol=symbol)
        except (
            KeyError,
            OSError,
            OverflowError,
            TypeError,
            UnicodeError,
            ValueError,
            zipfile.BadZipFile,
            pd.errors.EmptyDataError,
            pd.errors.ParserError,
        ) as error:
            raise ValueError(f"Invalid Binance Vision archive: {url}") from error

    @staticmethod
    def _remove_cached_archive(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.warning("Unable to remove invalid Binance Vision cache: %s", path)

    @staticmethod
    def _write_cache_atomically(path: Path, payload: bytes) -> None:
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                BinanceVisionDataSource._remove_cached_archive(temporary_path)

    def _read_archive(
        self,
        archive_type: str,
        symbol: str,
        interval: str,
        timestamp: pd.Timestamp,
    ) -> pd.DataFrame:
        url = self._archive_url(archive_type, symbol, interval, timestamp)
        path = self._archive_path(archive_type, symbol, interval, timestamp)
        if path.exists():
            payload = path.read_bytes()
            try:
                return self._parse_archive(payload, url, symbol)
            except ValueError:
                LOGGER.warning("Removing invalid Binance Vision cache: %s", path)
                self._remove_cached_archive(path)

        payload = self._downloader(url)
        frame = self._parse_archive(payload, url, symbol)
        self._write_cache_atomically(path, payload)
        return frame

    def _load_interval(self, symbol: str, interval: str) -> pd.DataFrame:
        load_start = self._load_start_time(interval)
        frames: list[pd.DataFrame] = []
        missing_months: list[pd.Timestamp] = []
        for month in self._month_starts(load_start, self.end_time):
            try:
                frames.append(self._read_archive("monthly", symbol, interval, month))
            except FileNotFoundError:
                missing_months.append(month)

        for month in missing_months:
            for day in self._days_in_month(month, load_start, self.end_time):
                try:
                    frames.append(self._read_archive("daily", symbol, interval, day))
                except FileNotFoundError:
                    continue

        if not frames:
            raise FileNotFoundError(
                "Binance Vision historical data was not found for "
                f"{symbol} [{interval}] between {self.start_time} and {self.end_time}"
            )
        combined = normalize_ohlcv_frame(
            pd.concat(frames, ignore_index=True),
            symbol=symbol,
        )
        selected = combined.loc[
            (combined["Open_time"] >= load_start)
            & (combined["Open_time"] < self.end_time)
        ].copy()
        if selected.empty:
            raise ValueError(
                "Binance Vision archives contain no completed candles in the "
                f"requested period for {symbol} [{interval}]"
            )
        return selected

    def load(self, symbols: Sequence[str], intervals: Sequence[str]) -> Indicator:
        selected_symbols = list(symbols) or list(self.symbols)
        selected_intervals = list(intervals) or list(self.intervals)
        if not selected_symbols:
            raise ValueError("At least one symbol is required for Binance Vision data")
        if not selected_intervals:
            raise ValueError("At least one interval is required for Binance Vision data")
        if self.interval is None:
            self.interval = selected_intervals[0]

        result = Indicator()
        for symbol in selected_symbols:
            for interval in selected_intervals:
                result[str(symbol)][str(interval)] = self._load_interval(
                    str(symbol), str(interval)
                )
        return result


class BinanceHistoricalDataSource:
    """Compatibility source that delegates to the existing Binance REST loader."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit

    def load(self, symbols: Sequence[str], intervals: Sequence[str]) -> Indicator:
        from . import exchange as futures

        return futures.init_indicators(
            limit=self.limit,
            symbols=symbols or None,
            intervals=intervals or None,
        )


@dataclass(frozen=True)
class Trade:
    """One completed position in the public trade ledger."""

    symbol: str
    side: PositionSide
    entry_time: pd.Timestamp | None
    entry_price: float | None
    exit_time: pd.Timestamp | None
    exit_price: float | None
    quantity: float
    pnl: float
    exit_order_type: OrderType | None = None

    def __post_init__(self) -> None:
        if isinstance(self.side, str):
            object.__setattr__(self, "side", PositionSide(self.side))
        if isinstance(self.exit_order_type, str):
            object.__setattr__(
                self,
                "exit_order_type",
                OrderType(self.exit_order_type),
            )
        for field_name in ("entry_time", "exit_time"):
            timestamp = getattr(self, field_name)
            if timestamp is None:
                continue
            timestamp = pd.Timestamp(timestamp)
            if timestamp.tzinfo is None:
                timestamp = timestamp.tz_localize("UTC")
            else:
                timestamp = timestamp.tz_convert("UTC")
            object.__setattr__(self, field_name, timestamp)


@dataclass(frozen=True)
class EquityPoint:
    """One mark-to-market account value at a completed candle timestamp."""

    timestamp: pd.Timestamp
    equity: float
    balance: float | None = None
    unrealized_pnl: float = 0.0

    def __post_init__(self) -> None:
        timestamp = pd.Timestamp(self.timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        object.__setattr__(self, "timestamp", timestamp)


@dataclass
class BacktestResult:
    """Per-symbol ledger and metrics for one deterministic run."""

    symbol: str
    evaluated_bars: int = 0
    entry_count: int = 0
    trades: list[Trade] = field(default_factory=list)
    _equity_curve: list[EquityPoint] = field(default_factory=list, repr=False)
    _entry_sides: dict[PositionSide, int] = field(
        default_factory=lambda: {
            PositionSide.BUY: 0,
            PositionSide.SELL: 0,
        },
        repr=False,
    )
    _legacy_pending_pnl: float = field(default=0.0, repr=False)

    def record_bars(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("count must not be negative")
        self.evaluated_bars += count

    def record_entry(self, side: PositionSide) -> None:
        side = PositionSide(side)
        self.entry_count += 1
        self._entry_sides[side] += 1

    def record_trade(
        self,
        side: PositionSide | str,
        pnl: float,
        *,
        entry_time: pd.Timestamp | None = None,
        entry_price: float | None = None,
        exit_time: pd.Timestamp | None = None,
        exit_price: float | None = None,
        quantity: float = 0.0,
        exit_order_type: OrderType | None = None,
    ) -> None:
        self.trades.append(
            Trade(
                symbol=self.symbol,
                side=PositionSide(side),
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=exit_time,
                exit_price=exit_price,
                quantity=float(quantity),
                pnl=float(pnl),
                exit_order_type=exit_order_type,
            )
        )

    # The following aliases keep the result object source-compatible with the
    # result class shipped before the deterministic engine was introduced.
    @property
    def _symbol(self) -> str:
        return self.symbol

    @property
    def _hit_count(self) -> dict[PositionSide, int]:
        return dict(self._entry_sides)

    @property
    def _trade_count(self) -> dict[PositionSide, int]:
        return {
            side: sum(trade.side == side for trade in self.trades)
            for side in PositionSide
        }

    @property
    def _win_count(self) -> dict[PositionSide, int]:
        return {
            side: sum(trade.side == side and trade.pnl > 0 for trade in self.trades)
            for side in PositionSide
        }

    @property
    def _profit(self) -> float:
        return self.profit

    @property
    def _loss(self) -> float:
        return self.loss

    @property
    def hit_count(self) -> int:
        return self.entry_count

    def increment_hit_count(self, side: PositionSide) -> None:
        self.record_entry(side)

    def increase_pnl(self, pnl: float) -> None:
        self._legacy_pending_pnl += float(pnl)

    def increment_trade_count(self, side: PositionSide, is_win: bool) -> None:
        del is_win
        pnl = self._legacy_pending_pnl
        self._legacy_pending_pnl = 0.0
        self.record_trade(side, pnl)

    def print(self) -> str:
        message = (
            f"{self.symbol}: bars={self.evaluated_bars} "
            f"entries={self.entry_count} trades={self.trade_count} "
            f"win_rate={self.win_rate:.6f} pnl={self.pnl:.6f}"
        )
        LOGGER.info("Backtest result: %s", message)
        return message

    def add_equity_point(self, point: EquityPoint) -> None:
        self._equity_curve.append(point)

    @property
    def equity_curve(self) -> tuple[EquityPoint, ...]:
        return tuple(self._equity_curve)

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_count(self) -> int:
        return sum(trade.pnl > 0 for trade in self.trades)

    @property
    def loss_count(self) -> int:
        return sum(trade.pnl < 0 for trade in self.trades)

    @property
    def break_even_count(self) -> int:
        return sum(trade.pnl == 0 for trade in self.trades)

    @property
    def profit(self) -> float:
        return sum(trade.pnl for trade in self.trades if trade.pnl > 0)

    @property
    def loss(self) -> float:
        return sum(trade.pnl for trade in self.trades if trade.pnl < 0)

    @property
    def pnl(self) -> float:
        return self.profit + self.loss

    @property
    def average_win(self) -> float:
        return self.profit / self.win_count if self.win_count else 0.0

    @property
    def average_loss(self) -> float:
        return self.loss / self.loss_count if self.loss_count else 0.0

    @property
    def average_loss_abs(self) -> float:
        return abs(self.average_loss)

    @property
    def hit_rate(self) -> float:
        return self.entry_count / self.evaluated_bars if self.evaluated_bars else 0.0

    @property
    def entry_rate(self) -> float:
        return self.hit_rate

    @property
    def win_rate(self) -> float:
        return self.win_count / self.trade_count if self.trade_count else 0.0

    @property
    def loss_rate(self) -> float:
        return self.loss_count / self.trade_count if self.trade_count else 0.0

    @property
    def expectancy(self) -> float:
        value = (
            self.win_rate * self.average_win
            + self.loss_rate * self.average_loss
        )
        return round(value, 12)

    @property
    def profit_factor(self) -> float:
        return _profit_factor(self.profit, self.loss)


class BacktestSummary:
    """Pure aggregate view over per-symbol results."""

    def __init__(self, results: Sequence[BacktestResult]) -> None:
        self.results = tuple(results)

    @property
    def _results(self) -> tuple[BacktestResult, ...]:
        return self.results

    @classmethod
    def from_results(cls, results: Sequence[BacktestResult]) -> BacktestSummary:
        return cls(tuple(results))

    @property
    def evaluated_bars(self) -> int:
        return sum(result.evaluated_bars for result in self.results)

    @property
    def entry_count(self) -> int:
        return sum(result.entry_count for result in self.results)

    @property
    def trades(self) -> tuple[Trade, ...]:
        return tuple(trade for result in self.results for trade in result.trades)

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def win_count(self) -> int:
        return sum(trade.pnl > 0 for trade in self.trades)

    @property
    def loss_count(self) -> int:
        return sum(trade.pnl < 0 for trade in self.trades)

    @property
    def break_even_count(self) -> int:
        return sum(trade.pnl == 0 for trade in self.trades)

    @property
    def pnl(self) -> float:
        return sum(trade.pnl for trade in self.trades)

    @property
    def profit(self) -> float:
        return sum(trade.pnl for trade in self.trades if trade.pnl > 0)

    @property
    def loss(self) -> float:
        return sum(trade.pnl for trade in self.trades if trade.pnl < 0)

    @property
    def win_rate(self) -> float:
        return self.win_count / self.trade_count if self.trade_count else 0.0

    @property
    def hit_rate(self) -> float:
        return self.entry_count / self.evaluated_bars if self.evaluated_bars else 0.0

    @property
    def entry_rate(self) -> float:
        return self.hit_rate

    @property
    def average_win(self) -> float:
        return self.profit / self.win_count if self.win_count else 0.0

    @property
    def average_loss(self) -> float:
        return self.loss / self.loss_count if self.loss_count else 0.0

    @property
    def loss_rate(self) -> float:
        return self.loss_count / self.trade_count if self.trade_count else 0.0

    @property
    def profit_factor(self) -> float:
        return _profit_factor(self.profit, self.loss)

    @property
    def average_loss_abs(self) -> float:
        return abs(self.average_loss)

    @property
    def expectancy(self) -> float:
        loss_rate = self.loss_count / self.trade_count if self.trade_count else 0.0
        return round(
            self.win_rate * self.average_win + loss_rate * self.average_loss,
            12,
        )

    def format(self) -> str:
        return (
            f"bars={self.evaluated_bars} entries={self.entry_count} "
            f"trades={self.trade_count} win_rate={self.win_rate:.6f} "
            f"pnl={self.pnl:.6f} expectancy={self.expectancy:.6f}"
        )

    def print(self) -> str:
        message = self.format()
        LOGGER.info("Backtest summary: %s", message)
        return message


@dataclass(frozen=True)
class BacktestRunResult:
    """Public result returned by :class:`Backtesting`."""

    by_symbol: dict[str, BacktestResult]
    summary: BacktestSummary
    equity_curve: tuple[EquityPoint, ...]
    final_balance: float


__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestRunResult",
    "BacktestSummary",
    "BinanceHistoricalDataSource",
    "BinanceVisionDataSource",
    "Candle",
    "CostModel",
    "CsvDataSource",
    "DataFrameDataSource",
    "DeterministicFillPolicy",
    "EquityPoint",
    "FillPolicy",
    "HistoricalDataSource",
    "MarketExecutionPolicy",
    "OrderIntent",
    "ParquetDataSource",
    "Trade",
    "ZeroCostModel",
    "build_timeline",
    "normalize_ohlcv_frame",
]
