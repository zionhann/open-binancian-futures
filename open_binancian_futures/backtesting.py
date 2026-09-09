"""Deterministic, dependency-injectable backtesting domain objects.

The runner lives in :mod:`open_binancian_futures.runners`; this module contains
the policies and result objects that make a run reproducible without Binance
credentials.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .models import Indicator, Order, OrderIntent
from .types import OrderType, PositionSide

LOGGER = logging.getLogger(__name__)


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
    """Source protocol for completed OHLCV frames."""

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
) -> pd.DatetimeIndex:
    """Build a sorted timestamp timeline after per-symbol warm-up."""

    if warmup_bars < 0:
        raise ValueError("warmup_bars must not be negative")
    if mode not in {"intersection", "union"}:
        raise ValueError("mode must be 'intersection' or 'union'")
    timeline: pd.DatetimeIndex | None = None
    for frame in frames.values():
        index = pd.DatetimeIndex(frame.index).sort_values()[warmup_bars:]
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
        return self.profit / abs(self.loss) if self.loss else 0.0


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
        return self.profit / abs(self.loss) if self.loss else 0.0

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
