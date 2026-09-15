import asyncio
import inspect
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Self, cast, override

import pandas as pd
from pandas import Timestamp

from .backtesting import (
    BacktestConfig,
    BacktestResult,
    BacktestRunResult,
    BacktestSummary,
    BinanceVisionDataSource,
    Candle,
    CsvDataSource,
    DataFrameDataSource,
    EquityPoint,
    HistoricalDataSource,
    MarketExecutionPolicy,
    ParquetDataSource,
    build_timeline,
    normalize_ohlcv_frame,
)
from .client import client as client
from .constants import settings
from .live import LiveTrading as LiveTrading
from .models import (
    Balance,
    Indicator,
    Order,
    OrderBook,
    OrderIntent,
    OrderList,
    Position,
    PositionBook,
    PositionList,
)
from .strategy import Strategy, StrategyContext
from .types import (
    OrderType,
    PositionSide,
)

LOGGER = logging.getLogger(__name__)
MESSAGE = "message"
KEEPALIVE_USER_STREAM_INTERVAL = 60 * 50
KLINE_SUBSCRIBE_RATE_PER_SECOND = 8
OrderKey = tuple[str, int]


class Runner(ABC):
    """
    Abstract base class for trading runners with context manager support.
    """

    @abstractmethod
    def run(self): ...

    @abstractmethod
    def close(self): ...

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Backward-compatible names retained for imports from runners.py.
BacktestingResult = BacktestResult
BacktestingSummary = BacktestSummary


class Backtesting(Runner):
    """Run a deterministic backtest over completed historical candles.

    Supplying both ``strategy`` and ``data_source`` keeps construction fully
    offline.  The default CLI/programmatic path loads a fixed period from
    Binance Vision public archives without creating an authenticated client.
    """

    def __init__(
        self,
        strategy: object | None = None,
        data_source: HistoricalDataSource | pd.DataFrame | Mapping | str | Path | None = None,
        config: BacktestConfig | None = None,
    ) -> None:
        config_was_provided = config is not None
        self.client = None
        default_source = data_source is None
        source: HistoricalDataSource
        if data_source is None:
            if not settings.backtest_start_date or not settings.backtest_end_date:
                raise ValueError(
                    "A fixed backtest period is required. Set "
                    "BACKTEST_START_DATE and BACKTEST_END_DATE, or inject data_source."
                )
            source = BinanceVisionDataSource(
                start_date=settings.backtest_start_date,
                end_date=settings.backtest_end_date,
                data_dir=settings.backtest_data_dir,
                symbols=settings.symbols_list,
                intervals=settings.intervals_list,
                warmup_bars=(
                    config.warmup_bars
                    if config is not None
                    else settings.indicator_init_size
                ),
            )
            source_interval = self._source_interval(source)
            requested_symbols = settings.symbols_list
        else:
            source = self._coerce_data_source(data_source)
            source_interval = self._source_interval(source)
            requested_symbols = self._requested_source_symbols(source)

        if config is None:
            config = (
                BacktestConfig(
                    initial_balance=settings.balance,
                    leverage=settings.leverage,
                    position_size=settings.size,
                    warmup_bars=settings.indicator_init_size,
                    interval=source_interval,
                )
                if default_source
                else BacktestConfig(interval=source_interval)
            )
        self.config = config
        self._interval_explicit = (
            config_was_provided and config.interval is not None
        )
        self.interval = self.config.interval or source_interval
        self._evaluation_start = self._source_start_time(source)

        if default_source:
            requested_intervals = settings.intervals_list or [self.interval]
        else:
            declared_intervals = getattr(source, "intervals", None)
            requested_intervals = list(declared_intervals or [self.interval])
        loaded = source.load(requested_symbols, requested_intervals)
        self.indicators = self._normalize_loaded_indicators(
            loaded, requested_symbols=requested_symbols
        )
        self.symbols = tuple(sorted(self.indicators))
        if not self.symbols:
            raise ValueError("No historical data was loaded")

        self.balance = Balance(
            self.config.initial_balance,
            execution_config=self.config.execution_config,
        )
        self.orders = OrderBook({symbol: OrderList() for symbol in self.symbols})
        self.positions = PositionBook(
            {symbol: PositionList() for symbol in self.symbols}
        )
        self.test_results = {
            symbol: BacktestResult(symbol) for symbol in self.symbols
        }
        self.results = self.test_results
        self.equity_curve: list[EquityPoint] = []
        self._open_trades: dict[str, tuple[PositionSide, float, Timestamp]] = {}
        self._entry_costs: dict[str, float] = {}
        self._entry_quantities: dict[str, float] = {}
        self._known_order_ids: set[OrderKey] = set()
        self._tracked_orders: dict[OrderKey, Order] = {}
        self._next_order_id = 1
        self._current_time: Timestamp | None = None
        self._current_candle: Candle | None = None
        self._current_candles: dict[str, Candle] = {}
        self._run_backtest_takes_two_args: bool | None = None
        self._pending_fill_hooks: list[tuple[str, Timestamp]] | None = None
        self._at_candle_close = False

        initial_view = self._visible_indicators(self._initial_visibility_time())
        self.strategy: object
        if strategy is None:
            if not default_source:
                raise ValueError(
                    "strategy is required when data_source is injected"
                )
            context = StrategyContext(
                client=self.client,
                balance=self.balance,
                orders=self.orders,
                positions=self.positions,
                indicators=initial_view,
                execution_config=self.config.execution_config,
            )
            self.strategy = Strategy.of(settings.strategy, context=context)
            self._bind_strategy()
            self.strategy.indicators = initial_view
        else:
            self.strategy = strategy
            self._bind_strategy()
            self._set_strategy_view(self._initial_visibility_time())

    def _initial_visibility_time(self) -> Timestamp:
        starts = []
        for intervals in self.indicators.values():
            frame = intervals[self.interval]
            eligible = frame.index
            if self._evaluation_start is not None:
                eligible = eligible[eligible >= self._evaluation_start]
            else:
                eligible = eligible[self.config.warmup_bars:]
            if len(eligible):
                starts.append(self._as_timestamp(eligible[0]))
        if not starts:
            return pd.Timestamp.min.tz_localize("UTC")
        return min(starts) - pd.Timedelta(nanoseconds=1)

    @staticmethod
    def _close_times(frame: pd.DataFrame, interval: str) -> pd.DatetimeIndex:
        if "Close_time" in frame:
            values = frame["Close_time"]
            return pd.DatetimeIndex(
                pd.to_datetime(
                    values, utc=True,
                    unit="ms" if pd.api.types.is_numeric_dtype(values) else None,
                )
            )
        opens = pd.DatetimeIndex(frame.index)
        if interval.endswith("M"):
            ends = opens + pd.DateOffset(months=int(interval[:-1]))
        else:
            units = {"m": "min", "h": "h", "d": "D", "w": "W", "s": "s"}
            ends = opens + pd.Timedelta(f"{int(interval[:-1])}{units[interval[-1]]}")
        return ends - pd.Timedelta(milliseconds=1)

    def _visible_indicators(self, time_value: Timestamp) -> Indicator:
        visible = Indicator()
        for symbol, intervals in self.indicators.items():
            for interval, frame in intervals.items():
                visible[symbol][interval] = frame.loc[
                    self._close_times(frame, interval) <= time_value
                ].copy(deep=True)
        return visible

    def _set_strategy_view(self, time_value: Timestamp) -> None:
        visible = self._visible_indicators(time_value)
        if isinstance(self.strategy, Strategy):
            self.strategy.add_indicators(visible)
        setattr(self.strategy, "indicators", visible)

    @property
    def pending_margin(self) -> float:
        """Free-balance amount reserved by unfilled entry orders."""
        return self.balance.reserved_margin

    def _effective_market_execution(self) -> MarketExecutionPolicy:
        policy = self.config.fill_policy
        configured = getattr(policy, "market_execution", self.config.market_execution)
        return MarketExecutionPolicy(configured)

    @staticmethod
    def _default_interval() -> str:
        return settings.intervals_list[0] if settings.intervals_list else "1d"

    @classmethod
    def _source_interval(cls, source: HistoricalDataSource) -> str:
        if isinstance(source, DataFrameDataSource):
            data = source.data
            if isinstance(data, Mapping) and data and all(
                isinstance(interval_frames, Mapping)
                for interval_frames in data.values()
            ):
                first_intervals = next(iter(data.values()))
                first_interval = next(iter(first_intervals), None)
                if first_interval is not None:
                    return str(first_interval)
            if source.interval:
                return source.interval
        configured = getattr(source, "interval", None)
        if configured:
            return str(configured)
        return cls._default_interval()

    @classmethod
    def _source_start_time(
        cls, source: HistoricalDataSource
    ) -> Timestamp | None:
        configured = getattr(source, "start_time", None)
        return cls._as_timestamp(configured) if configured is not None else None

    @staticmethod
    def _coerce_data_source(data_source: object) -> HistoricalDataSource:
        if hasattr(data_source, "load"):
            return data_source
        if isinstance(data_source, pd.DataFrame):
            default_symbol = (
                settings.symbols_list[0]
                if len(settings.symbols_list) == 1
                else None
            )
            return DataFrameDataSource(data_source, symbol=default_symbol)
        if isinstance(data_source, Mapping):
            return DataFrameDataSource(data_source)
        if isinstance(data_source, (str, Path)):
            path = Path(data_source)
            default_symbol = (
                settings.symbols_list[0]
                if len(settings.symbols_list) == 1
                else None
            )
            if path.suffix.lower() == ".parquet":
                return ParquetDataSource(path, symbol=default_symbol)
            if path.suffix.lower() == ".csv":
                return CsvDataSource(path, symbol=default_symbol)
            raise ValueError("data_source path must end in .csv or .parquet")
        raise TypeError(
            "data_source must implement load(), be a DataFrame/mapping, or be a CSV/Parquet path"
        )

    @staticmethod
    def _requested_source_symbols(source: HistoricalDataSource) -> list[str]:
        if not isinstance(source, DataFrameDataSource):
            return settings.symbols_list
        if isinstance(source, (CsvDataSource, ParquetDataSource)):
            return []
        if isinstance(source.data, pd.DataFrame) and "Symbol" in source.data.columns:
            return []
        if source.symbol is not None:
            return []
        if isinstance(source.data, Mapping):
            return []
        return settings.symbols_list

    def _normalize_loaded_indicators(
        self, loaded: object, *, requested_symbols: Sequence[str] = ()
    ) -> Indicator:
        if isinstance(loaded, pd.DataFrame):
            has_symbols = "Symbol" in loaded.columns
            loaded = DataFrameDataSource(
                loaded,
                symbol=(
                    requested_symbols[0]
                    if not has_symbols and len(requested_symbols) == 1
                    else None
                ),
            ).load(
                [] if has_symbols else list(requested_symbols), [self.interval]
            )
        if not isinstance(loaded, Mapping):
            raise TypeError("historical source must return symbol-indexed frames")
        if loaded and all(isinstance(frame, pd.DataFrame) for frame in loaded.values()):
            loaded = DataFrameDataSource(loaded).load([], [self.interval])
        indicators = Indicator()
        for symbol, interval_frames in loaded.items():
            if not isinstance(interval_frames, Mapping):
                raise TypeError(f"Historical data for {symbol} must be interval-indexed")
            for interval, frame in interval_frames.items():
                if not isinstance(frame, pd.DataFrame):
                    raise TypeError(
                        f"Historical data for {symbol} must be a DataFrame"
                    )
                indicators[str(symbol)][str(interval)] = normalize_ohlcv_frame(
                    frame, symbol=str(symbol)
                )
            if self.interval not in indicators[str(symbol)] and indicators[str(symbol)]:
                loaded_intervals = sorted(indicators[str(symbol)])
                interval_label = (
                    "Configured" if self._interval_explicit else "Selected"
                )
                raise ValueError(
                    f"{interval_label} interval {self.interval!r} is missing for "
                    f"{symbol}; loaded intervals: {loaded_intervals}"
                )
        return indicators

    def _bind_strategy(self) -> None:
        for name, value in (
            ("client", self.client),
            ("balance", self.balance),
            ("orders", self.orders),
            ("positions", self.positions),
        ):
            try:
                setattr(self.strategy, name, value)
            except (AttributeError, TypeError):
                pass
        if isinstance(self.strategy, Strategy):
            self.strategy.configure_execution(self.config.execution_config)
        else:
            try:
                setattr(  # noqa: B010 - support plain strategy objects
                    self.strategy,
                    "execution_config",
                    self.config.execution_config,
                )
            except (AttributeError, TypeError):
                pass
        try:
            setattr(self.strategy, "_backtest_gateway", self)  # noqa: B010
        except (AttributeError, TypeError):
            pass

    def _next_id(self) -> int:
        order_id = self._next_order_id
        self._next_order_id += 1
        return order_id

    @staticmethod
    def _order_key(order: Order) -> OrderKey:
        return order.symbol, order.order_id

    async def submit_order(self, intent: OrderIntent) -> bool:
        """Submit a domain ``OrderIntent`` during a backtest."""
        if intent.symbol not in self.symbols:
            raise KeyError(f"Unknown backtest symbol: {intent.symbol}")
        quantity = intent.quantity
        if quantity is None:
            if intent.price is None or intent.price <= 0:
                return False
            quantity = (
                self.balance.available
                * self.config.position_size
                * self.config.leverage
                / intent.price
            )
        if quantity <= 0:
            return False
        order = Order(
            symbol=intent.symbol,
            order_id=self._next_id(),
            type=intent.order_type,
            side=intent.side,
            price=float(intent.price or 0.0),
            quantity=float(quantity),
            gtd=intent.gtd,
            created_at=self._current_time,
            reduce_only=intent.reduce_only,
        )
        self.orders[order.symbol].add(order)
        if not self._register_order(order):
            self.orders[order.symbol].remove_by_id(order.order_id)
            return False
        return True

    def _transfer_legacy_reservation(self, symbol: str) -> None:
        mapping = getattr(self.strategy, "_backtest_reserved_margin", None)
        release = getattr(self.strategy, "_release_backtest_margin", None)
        if isinstance(mapping, dict) and symbol in mapping and callable(release):
            release(symbol)

    def _register_order(self, order: Order) -> bool:
        order_key = self._order_key(order)
        if order_key in self._known_order_ids:
            return True
        if order.created_at is None:
            order.created_at = self._current_time
        self._known_order_ids.add(order_key)
        self._tracked_orders[order_key] = order
        if order.reduce_only or self._is_position_exit(order):
            return True
        if (
            order.type == OrderType.MARKET
            and self._effective_market_execution() != MarketExecutionPolicy.NEXT_OPEN
        ):
            return True
        margin_price = order.price
        if order.type == OrderType.MARKET and margin_price <= 0:
            current_candle = self._current_candles.get(order.symbol)
            if current_candle is not None:
                margin_price = (
                    current_candle.close if self._at_candle_close else current_candle.open
                )
            else:
                margin_price = 0.0
        margin = margin_price * order.quantity / self.config.leverage
        if margin <= 0:
            self._known_order_ids.discard(order_key)
            self._tracked_orders.pop(order_key, None)
            return False
        self._transfer_legacy_reservation(order.symbol)
        try:
            self.balance.reserve_margin(order_key, margin)
        except ValueError:
            self._known_order_ids.discard(order_key)
            self._tracked_orders.pop(order_key, None)
            return False
        return True

    def _is_position_exit(self, order: Order) -> bool:
        position = self.positions[order.symbol].find_first()
        if position is None:
            return False
        close_side = (
            PositionSide.SELL
            if position.side == PositionSide.BUY
            else PositionSide.BUY
        )
        return order.side == close_side and order.type in {
            OrderType.LIMIT,
            OrderType.MARKET,
            OrderType.STOP_MARKET,
            OrderType.STOP_LIMIT,
            OrderType.TAKE_PROFIT_MARKET,
            OrderType.TAKE_PROFIT_LIMIT,
        }

    def _sync_orders(self, symbol: str) -> set[OrderKey]:
        active_order_keys = {
            self._order_key(order) for order in self.orders[symbol]
        }
        for order_key, order in list(self._tracked_orders.items()):
            if order.symbol == symbol and order_key not in active_order_keys:
                self.balance.release_margin(order_key)
                self._known_order_ids.discard(order_key)
                self._tracked_orders.pop(order_key, None)

        new_order_keys: set[OrderKey] = set()
        for order in list(self.orders[symbol]):
            order_key = self._order_key(order)
            if order_key not in self._known_order_ids:
                if not self._register_order(order):
                    self.orders[symbol].remove_by_id(order.order_id)
                else:
                    new_order_keys.add(order_key)
        return new_order_keys

    def _remove_order(self, order: Order) -> None:
        order_key = self._order_key(order)
        self.orders[order.symbol].remove_by_id(order.order_id)
        self.balance.release_margin(order_key)
        self._known_order_ids.discard(order_key)
        self._tracked_orders.pop(order_key, None)

    def _clear_orders(self, symbol: str) -> None:
        order_keys = {self._order_key(order) for order in self.orders[symbol]}
        order_keys.update(
            order_key
            for order_key, order in self._tracked_orders.items()
            if order.symbol == symbol
        )
        for order_key in order_keys:
            self.balance.release_margin(order_key)
            self._known_order_ids.discard(order_key)
            self._tracked_orders.pop(order_key, None)
        self.orders[symbol].clear()

    def _expire_orders(self, symbol: str, time_value: Timestamp) -> None:
        for order in list(self.orders[symbol]):
            if order.is_expired(time_value):
                self._remove_order(order)

    @staticmethod
    def _as_timestamp(value: str | date | datetime | Timestamp) -> Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC")

    async def _run_strategy(self, symbol: str, index: int) -> None:
        method: Any = getattr(self.strategy, "run_backtest")  # noqa: B009
        if self._run_backtest_takes_two_args is None:
            parameters = list(inspect.signature(method).parameters.values())
            self._run_backtest_takes_two_args = len(parameters) == 2
        if self._run_backtest_takes_two_args:
            outcome = method(symbol, index)
        else:
            outcome = method(symbol, self.interval, index)
        if inspect.isawaitable(outcome):
            await outcome

    def _eval_orders(
        self,
        symbol: str,
        candle: Candle,
        eligible_order_keys: set[OrderKey],
    ) -> None:
        policy = self.config.fill_policy
        if policy is None:
            raise RuntimeError("BacktestConfig must provide a fill policy")

        orders = [
            order
            for order in self.orders[symbol]
            if self._order_key(order) in eligible_order_keys
        ]
        position = self.positions[symbol].find_first()
        if position is None:
            candidates = []
            for sequence, order in enumerate(orders):
                if order.reduce_only:
                    continue
                fill = policy.fill_price(order, candle)
                if fill is not None:
                    candidates.append((sequence, order, fill))
            if not candidates:
                return
            _, order, fill = min(candidates, key=lambda item: item[0])
            if not self._open_position(order, float(fill), candle.time):
                return
            self.test_results[symbol].record_entry(order.side)
            if self._pending_fill_hooks is not None:
                self._pending_fill_hooks.append((symbol, candle.time))
            else:
                self._run_hook("on_backtest_entry_filled", symbol, candle.time)
            self._sync_orders(symbol)
            return

        while position is not None:
            orders = [
                order
                for order in self.orders[symbol]
                if self._order_key(order) in eligible_order_keys
            ]
            selected = policy.select_exit(orders, candle, position.side)
            if selected is None:
                return
            order, fill = selected
            if order.quantity <= 0:
                self._remove_order(order)
                continue
            self._close_position(
                symbol,
                position,
                fill,
                candle.time,
                order,
                quantity=order.quantity,
            )
            position = self.positions[symbol].find_first()

    def _run_hook(self, name: str, *args: object) -> None:
        hook = getattr(self.strategy, name, None)
        if callable(hook):
            outcome = hook(*args)
            if inspect.isawaitable(outcome):
                raise TypeError(f"Backtest hook {name} must be synchronous")

    def _open_position(
        self, order: Order, fill: float, time_value: Timestamp
    ) -> bool:
        position = Position(
            symbol=order.symbol,
            amount=order.quantity,
            price=fill,
            side=order.side,
            leverage=self.config.leverage,
        )
        actual_margin = position.initial_margin()
        order_key = self._order_key(order)
        reserved_margin = self.balance.reserved_margin_for(order_key)
        entry_cost = float(
            self.config.cost_model.cost(
                order, fill, position.amount, time_value
            )
        )
        if actual_margin + entry_cost > self.balance.available + reserved_margin:
            self._remove_order(order)
            return False
        if reserved_margin:
            self.balance.consume_margin(order_key, actual_margin)
        else:
            self.balance.deduct(actual_margin)
        if entry_cost:
            self.balance.deduct(entry_cost)
        self._remove_order(order)
        self.positions[order.symbol].update_positions([position])
        self._open_trades[order.symbol] = (position.side, fill, time_value)
        self._entry_costs[order.symbol] = entry_cost
        self._entry_quantities[order.symbol] = position.amount
        return True

    def _close_position(
        self,
        symbol: str,
        position: Position,
        fill: float,
        time_value: Timestamp,
        exit_order: Order | None = None,
        quantity: float | None = None,
    ) -> None:
        close_quantity = (
            position.amount
            if quantity is None
            else min(float(quantity), position.amount)
        )
        if close_quantity <= 0:
            return

        gross_pnl = (
            (fill - position.price) * close_quantity
            if position.is_long()
            else (position.price - fill) * close_quantity
        )
        exit_cost = float(
            self.config.cost_model.cost(
                exit_order, fill, close_quantity, time_value
            )
        )
        entry_quantity = self._entry_quantities.get(symbol, position.amount)
        entry_cost = self._entry_costs.get(symbol, 0.0)
        allocated_entry_cost = (
            entry_cost * close_quantity / entry_quantity
            if entry_quantity > 0
            else 0.0
        )
        pnl = gross_pnl - allocated_entry_cost - exit_cost
        released_margin = close_quantity * position.price / self.config.leverage
        self.balance.increase_balance(released_margin + gross_pnl - exit_cost)

        side, entry_price, entry_time = self._open_trades.get(
            symbol, (position.side, position.price, time_value)
        )
        is_full_close = close_quantity >= position.amount - 1e-12
        if is_full_close:
            self.positions[symbol].clear()
            self._clear_orders(symbol)
            self._open_trades.pop(symbol, None)
            self._entry_costs.pop(symbol, None)
            self._entry_quantities.pop(symbol, None)
        else:
            position.amount -= close_quantity
            self.positions[symbol].update_positions([position])
            self._entry_costs[symbol] = entry_cost - allocated_entry_cost
            self._entry_quantities[symbol] = entry_quantity - close_quantity
            if exit_order is not None:
                self._remove_order(exit_order)

        self.test_results[symbol].record_trade(
            side,
            pnl,
            entry_time=entry_time,
            entry_price=entry_price,
            exit_time=time_value,
            exit_price=fill,
            quantity=close_quantity,
            exit_order_type=exit_order.type if exit_order else None,
            exit_reason="end_of_backtest" if exit_order is None else None,
        )

    def _mark_to_market(
        self,
        frames: Mapping[str, pd.DataFrame],
        timestamp: Timestamp,
    ) -> None:
        equity = self.balance.available + self.balance.reserved_margin
        unrealized = 0.0
        for symbol, frame in frames.items():
            frame_index = frame.index.searchsorted(timestamp, side="right") - 1
            if frame_index < 0:
                continue
            position = self.positions[symbol].find_first()
            if position is None:
                continue
            close = float(frame["Close"].values[frame_index])
            unrealized += position.simple_pnl(close)
            equity += position.initial_margin() + position.simple_pnl(close)
        self.equity_curve.append(
            EquityPoint(
                timestamp=timestamp,
                equity=equity,
                balance=self.balance.available,
                unrealized_pnl=unrealized,
            )
        )

    def _flush_position(self, symbol: str, candle: Candle) -> None:
        position = self.positions[symbol].find_first()
        if position is not None:
            self._close_position(symbol, position, candle.close, candle.time)
        else:
            self._clear_orders(symbol)

    async def _run_backtest_loop(self) -> BacktestRunResult:
        frames = {
            symbol: self.indicators[symbol][self.interval]
            for symbol in self.symbols
        }
        eligible_indices: dict[str, pd.DatetimeIndex] = {}
        for symbol, frame in frames.items():
            index = pd.DatetimeIndex(frame.index).sort_values()
            if self._evaluation_start is not None:
                index = index[index >= self._evaluation_start]
            else:
                index = index[self.config.warmup_bars :]
            eligible_indices[symbol] = index
        timeline = build_timeline(
            frames,
            self.config.warmup_bars,
            mode=self.config.timeline_mode,
            start_time=self._evaluation_start,
        )
        if timeline.empty:
            raise ValueError("No timestamps available after warm-up")

        for symbol in self.symbols:
            self._sync_orders(symbol)

        # normalize_ohlcv rejects duplicate timestamps, so get_loc returns an int.
        for timestamp in timeline:
            current_candles = {
                symbol: Candle.from_series(
                    frames[symbol].iloc[
                        int(cast(int, frames[symbol].index.get_loc(timestamp)))
                    ]
                )
                for symbol in self.symbols
                if timestamp in eligible_indices[symbol]
            }
            self._current_candles = current_candles
            self._at_candle_close = False
            for symbol, candle in current_candles.items():
                self._expire_orders(symbol, candle.time)
            existing_order_keys_by_symbol = {
                symbol: {
                    self._order_key(order) for order in self.orders[symbol]
                }
                for symbol in self.symbols
            }
            # Snapshot every symbol before any fill hook can create orders.
            self._pending_fill_hooks = []
            for symbol, candle in current_candles.items():
                self._current_time = candle.time
                self._current_candle = candle
                self._eval_orders(symbol, candle, existing_order_keys_by_symbol[symbol])
            pending_hooks = self._pending_fill_hooks
            self._pending_fill_hooks = None
            for symbol, hook_time in pending_hooks:
                self._current_time = hook_time
                self._current_candle = current_candles[symbol]
                self._set_strategy_view(hook_time - pd.Timedelta(nanoseconds=1))
                self._run_hook("on_backtest_entry_filled", symbol, hook_time)
            for target in self.symbols:
                self._sync_orders(target)
            for symbol, candle in current_candles.items():
                frame = frames[symbol]
                bar_index = int(cast(int, frame.index.get_loc(timestamp)))
                decision_time = self._close_times(frame, self.interval)[bar_index]
                self._at_candle_close = True
                self._current_time = candle.time
                self._current_candle = candle
                self._set_strategy_view(decision_time)
                visible = getattr(self.strategy, "indicators")[symbol][self.interval]
                self.test_results[symbol].record_bars()
                before_callback = {self._order_key(order) for order in self.orders[symbol]}
                await self._run_strategy(symbol, len(visible) - 1)
                # Reconcile cross-symbol submissions and cancellation immediately.
                for target in self.symbols:
                    self._sync_orders(target)
                if self._effective_market_execution() == MarketExecutionPolicy.CLOSE:
                    eligible = {
                        self._order_key(order) for order in self.orders[symbol]
                        if self._order_key(order) not in before_callback
                        and order.type == OrderType.MARKET
                    }
                    self._eval_orders(symbol, candle, eligible)
            self._mark_to_market(frames, self._as_timestamp(timestamp))

        for symbol in self.symbols:
            frame = frames[symbol]
            evaluated_index = eligible_indices[symbol]
            evaluated_index = evaluated_index[evaluated_index <= timeline[-1]]
            if evaluated_index.empty:
                self._clear_orders(symbol)
                continue
            last_timestamp = evaluated_index[-1]
            self._flush_position(symbol, Candle.from_series(frame.loc[last_timestamp]))

        final_timestamp = self._as_timestamp(timeline[-1])
        final_point = EquityPoint(
            timestamp=final_timestamp,
            equity=self.balance.available,
            balance=self.balance.available,
        )
        if self.equity_curve and self.equity_curve[-1].timestamp == final_timestamp:
            self.equity_curve[-1] = final_point
        else:
            self.equity_curve.append(final_point)

        summary = BacktestSummary.from_results(list(self.test_results.values()))
        summary.print()
        return BacktestRunResult(
            by_symbol=dict(self.test_results),
            summary=summary,
            equity_curve=tuple(self.equity_curve),
            final_balance=self.balance.available,
        )

    @override
    def run(self) -> BacktestRunResult:
        LOGGER.info("Starting deterministic backtesting...")
        return asyncio.run(self._run_backtest_loop())

    @override
    def close(self) -> None:
        return
