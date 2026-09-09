"""Deterministic, dependency-injectable backtesting domain objects.

The runner lives in :mod:`open_binancian_futures.runners`; this module contains
the policies and result objects that make a run reproducible without Binance
credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from .models import OrderIntent
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

    @classmethod
    def from_series(cls, row: Any) -> "Candle":
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
        order: Any,
        fill_price: float,
        quantity: float,
        timestamp: pd.Timestamp,
    ) -> float:
        return 0.0


@dataclass(frozen=True)
class BacktestConfig:
    """Configuration shared by the deterministic backtest engine."""

    initial_balance: float = 100.0
    leverage: int = 1
    warmup_bars: int = 0
    interval: str | None = None
    timeline_mode: str = "intersection"
    fill_policy: Any = None
    cost_model: ZeroCostModel = field(default_factory=ZeroCostModel)
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

    def record_bars(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError("count must not be negative")
        self.evaluated_bars += count

    def record_entry(self, side: PositionSide) -> None:
        del side
        self.entry_count += 1

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

    def __init__(self, results: tuple[BacktestResult, ...]) -> None:
        self.results = results

    @classmethod
    def from_results(cls, results: list[BacktestResult]) -> "BacktestSummary":
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
    def average_win(self) -> float:
        return self.profit / self.win_count if self.win_count else 0.0

    @property
    def average_loss(self) -> float:
        return self.loss / self.loss_count if self.loss_count else 0.0

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
    "Candle",
    "EquityPoint",
    "MarketExecutionPolicy",
    "OrderIntent",
    "Trade",
    "ZeroCostModel",
]
