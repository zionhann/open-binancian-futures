"""Supervised, single-loop live runtime. Construction never contacts the account."""

import asyncio
import inspect
import logging
import math
import os
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    AlgoUpdateO,
    OrderTradeUpdateO,
)

from .client import client
from .constants import settings
from .exchange_adapter import (
    BinanceExchangeAdapter,
    ExchangeAdapter,
    ExchangeSnapshot,
    ExchangeStreams,
    OrderOutcomeUnknown,
    OrderReceipt,
    RestCalls,
    response_data,
)
from .execution import ExecutionConfig
from .live_history import next_open, recover_history
from .live_journal import OrderJournal
from .managed_orders import ManagedOrderGateway
from .models import Indicator, OrderEvent, OrderIntent
from .sdk_streams import BinanceStreams
from .strategy import Strategy, StrategyContext
from .types import OrderType
from .webhook import AsyncWebhook, Webhook

LOGGER = logging.getLogger(__name__)


@dataclass
class _Decision:
    generation: int
    revision: int
    deadline: float
    bar: tuple[str, str, int] | None
    exposure: dict[str, tuple[Any, ...]] = field(default_factory=dict)


class LiveTrading:
    def __init__(
        self,
        adapter: ExchangeAdapter | None = None,
        *,
        streams_factory: Callable[[], ExchangeStreams] | None = None,
        strategy: Any = None,
        config: ExecutionConfig | None = None,
        symbols: Sequence[str] | None = None,
        intervals: Sequence[str] | None = None,
        journal_path: str | Path | None = None,
        order_gateway: ManagedOrderGateway | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float], float] = lambda delay: random.uniform(
            0.8 * delay, delay
        ),
        webhook: Any = None,
    ) -> None:
        self.client = client() if adapter is None else getattr(adapter, "client", None)
        self.adapter: ExchangeAdapter = (
            adapter if adapter is not None else BinanceExchangeAdapter(self.client)
        )
        self.symbols = tuple(symbols if symbols is not None else settings.symbols_list)
        self.intervals = tuple(
            intervals if intervals is not None else settings.intervals_list
        )
        self.execution_config = config or ExecutionConfig(
            leverage=settings.leverage,
            position_size=settings.size,
            timezone=settings.timezone,
        )
        self.journal_path = Path(
            journal_path
            if journal_path is not None
            else os.environ.get("OBF_RUNTIME_PATH", ".obf-runtime/orders.sqlite3")
        )
        self.streams_factory = streams_factory or self._default_streams
        self.strategy = strategy
        self.gateway = order_gateway
        self.rest = order_gateway.rest if order_gateway is not None else RestCalls()
        self.webhook = webhook or Webhook.of(settings.webhook_url)
        self.clock, self.sleep, self.jitter = clock, sleep, jitter
        self.stop_event = asyncio.Event()
        self.recovery = asyncio.Event()
        self._ready = asyncio.Event()
        self.queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
        self.user_queue: asyncio.Queue[tuple[int, dict[str, Any]]] = asyncio.Queue()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.streams: ExchangeStreams | None = None
        self.journal: OrderJournal | None = None
        self.listen_key: str | None = None
        self.generation = 0
        self._snapshot_revision = 0
        self._account_activity = 0
        self._decision_generation: ContextVar[_Decision | None] = ContextVar(
            "live_decision_generation", default=None
        )
        self._user_busy = 0
        self._seen_bars: dict[tuple[str, str], int] = {}
        self._reference_prices: dict[str, float] = {}
        self._server_ms = 0
        self._server_at = self.clock()
        self._connecting = False
        self.active = False
        self.failed = False
        self.running = False
        self.closed = False
        self.indicators = Indicator()
        self.last_bars: dict[tuple[str, str], int] = {}
        self._trade_progress: dict[tuple[str, str, int], float] = {}
        self._trade_ids: set[tuple[str, str, int, int]] = set()
        self._versions: dict[tuple[str, str, int], int] = {}
        # ponytail: cache one update; widen only if interleaved duplicates cause REST load.
        self._last_order_update: tuple[tuple[str, str, int], int, dict[str, Any]] | None = None
        self._hook_events: set[tuple[str, str, int, str]] = set()
        self._notified: set[str] = set()
        self._last_market = {
            (symbol, interval): self.clock()
            for symbol in self.symbols
            for interval in self.intervals
        }
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notifications: AsyncWebhook | None = None

    def _default_streams(self) -> ExchangeStreams:
        if self.client is None:
            raise ValueError("Injected adapter requires streams_factory")
        return BinanceStreams(self.client.websocket_streams.configuration)

    def report(self, message: str) -> None:
        if message in self._notified:
            return
        self._notified.add(message)
        LOGGER.warning(message)
        try:
            self.webhook.send_message(message)
        except Exception:
            LOGGER.exception("Runtime notification failed")

    def _transport_ready(self) -> bool:
        decision_generation = self._decision_generation.get()
        if decision_generation is not None and decision_generation.generation != self.generation:
            return False
        if self.streams is None or self._connecting or self.recovery.is_set() or self.stop_event.is_set() or self.closed:
            return False
        signal = getattr(self.streams, "recovery", None)
        healthy = getattr(self.streams, "healthy", lambda: True)
        return not (signal is not None and signal.is_set()) and healthy()

    def _entry_ready(self) -> bool:
        return self.user_queue.empty() and not self._user_busy

    def _exposure(self, symbol: str) -> tuple[Any, ...]:
        if self.gateway is None or self.gateway.state is None:
            return ()
        state = self.gateway.snapshot()
        return (
            tuple((p.side, p.amount, p.price) for p in state.positions[symbol]),
            tuple(sorted((o.order_id, o.type.value, o.side.value, o.quantity, o.price)
                         for o in state.orders[symbol] if not o.reduce_only)),
        )

    def _entry_abort_reason(self, intent: OrderIntent) -> str | None:
        decision = self._decision_generation.get()
        if decision is None:
            return None
        if self.stop_event.is_set() or self.closed:
            return "shutdown"
        if decision.generation != self.generation or self.recovery.is_set():
            return "recovery"
        if self.clock() >= decision.deadline:
            return "signal_expired"
        if decision.bar is not None:
            symbol, interval, opened = decision.bar
            if self._seen_bars.get((symbol, interval), opened) > opened:
                return "next_bar"
        if self._exposure(intent.symbol) != decision.exposure.get(intent.symbol):
            return "state_conflict"
        return None

    def _entry_deadline(self) -> float | None:
        decision = self._decision_generation.get()
        return decision.deadline if decision is not None else None

    def _entry_context(self) -> str:
        decision = self._decision_generation.get()
        return (f"decision_bar={decision.bar} revision={decision.revision} "
                f"required_revision={self._snapshot_revision}") if decision else "decision=standalone"

    def _observe_time(self, server_ms: int, *, force: bool = False) -> None:
        estimated = self._server_ms + int((self.clock() - self._server_at) * 1000)
        self._server_ms = server_ms if force else max(server_ms, estimated)
        self._server_at = self.clock()

    def _request_recovery(self) -> None:
        self._pause()
        self.recovery.set()

    def _pause(self) -> None:
        self.active = False
        self._ready.clear()
        if self.gateway is not None:
            self.gateway.active = False
            self.gateway.entry_changed.set()

    def _bind_snapshot(self, snapshot: ExchangeSnapshot) -> None:
        previous = self._snapshot_revision
        self._snapshot_revision += 1
        decision = self._decision_generation.get()
        # Child tasks inherit this decision's shared revision, not a tuple copy.
        if decision is not None and decision.revision == previous:
            decision.revision = self._snapshot_revision
        self.exchange_info, self.balance = snapshot.exchange_info, snapshot.balance
        self.orders, self.positions = snapshot.orders, snapshot.positions
        if self.strategy is not None:
            for name in ("exchange_info", "balance", "orders", "positions"):
                setattr(self.strategy, name, getattr(self, name))

    def _adopt_submission(self, intent: OrderIntent, receipt: OrderReceipt) -> None:
        decision = self._decision_generation.get()
        if decision is None:
            return
        decision.revision = self._snapshot_revision
        positions, orders = decision.exposure[intent.symbol]
        current_positions, current_orders = self._exposure(intent.symbol)
        algo = intent.order_type not in {OrderType.MARKET, OrderType.LIMIT}

        def own(order: tuple[Any, ...]) -> bool:
            return order[0] == receipt.order_id and (
                order[1] not in {OrderType.MARKET.value, OrderType.LIMIT.value}
            ) == algo

        orders = tuple(sorted(tuple(o for o in orders if not own(o)) +
                              tuple(o for o in current_orders if own(o))))
        # Adopt a position only if this confirmed execution alone explains it.
        filled = receipt.executed_quantity or 0.
        if not algo and filled > 0 and len(positions) <= 1:
            signed = positions[0][1] * (1 if positions[0][0].value == "BUY" else -1) if positions else 0.
            expected = signed + filled * (1 if intent.side.value == "BUY" else -1)
            if abs(expected) < 1e-12 and not current_positions:
                positions = ()
            elif len(current_positions) == 1:
                side, amount, price = current_positions[0]
                actual = amount * (1 if side.value == "BUY" else -1)
                fill_price = receipt.average_price
                if fill_price and (not positions or signed * expected <= 0):
                    expected_price = fill_price
                elif positions and signed * (expected - signed) < 0 and signed * expected > 0:
                    expected_price = positions[0][2]
                elif positions and fill_price:
                    expected_price = (abs(signed) * positions[0][2] + filled * fill_price) / abs(expected)
                else:
                    expected_price = None
                if expected_price is not None and math.isclose(actual, expected) and math.isclose(price, expected_price):
                    positions = current_positions
        decision.exposure[intent.symbol] = (positions, orders)

    def _strategy_failed(self, error: Exception) -> None:
        self.failed = True
        if self.gateway is not None:
            self.gateway.failed = True
        self._pause()
        self.report(
            f"Strategy failed; stopped until restart: {type(error).__name__}: {error}"
        )

    def _reload_indicators(self) -> None:
        if self.strategy is None or self.failed:
            return
        self.strategy.indicators = self.indicators
        load = getattr(self.strategy, "add_indicators", None)
        if load is not None:
            try:
                load(self.indicators)
            except Exception as error:
                self._strategy_failed(error)

    def _build_strategy(self) -> None:
        if self.strategy is None:
            context = StrategyContext(
                client=None,
                exchange_info=self.exchange_info,
                balance=self.balance,
                orders=self.orders,
                positions=self.positions,
                webhook=self.webhook,
                indicators=self.indicators,
                execution_config=self.execution_config,
                order_gateway=self.gateway,
                preserve_position_leverage=True,
            )
            self.strategy = Strategy.of(settings.strategy, context=context)
        else:
            for name in (
                "exchange_info",
                "balance",
                "orders",
                "positions",
                "indicators",
                "webhook",
            ):
                setattr(self.strategy, name, getattr(self, name))
            self.strategy.order_gateway = self.gateway
            self.strategy._preserve_position_leverage = True
            configure = getattr(self.strategy, "configure_execution", None)
            if configure is not None:
                configure(self.execution_config)
            load = getattr(self.strategy, "add_indicators", None)
            if load is not None:
                load(self.indicators)
        # Raw SDK access is deliberately bound only after protected construction/load.
        self.strategy.client = self.client

    def _enqueue(self, generation: int, payload: Any) -> None:
        if generation != self.generation or self.closed:
            return
        if self._loop is not None:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is not self._loop:
                self._loop.call_soon_threadsafe(self._enqueue, generation, payload)
                return
        try:
            payload = getattr(payload, "actual_instance", payload)
            data = response_data(payload)
            while isinstance(data, dict) and ("data" in data or "event" in data):
                data = data.get("data", data.get("event"))
            if not isinstance(data, dict):
                raise ValueError("Invalid stream payload")
            if data.get("e") == "listenKeyExpired":
                self._pause()
                self.recovery.set()
            if data.get("e") == "kline":
                candle = data.get("k")
                if not isinstance(candle, dict):
                    raise ValueError("Malformed kline payload")
                key = (candle.get("s", data.get("s")), candle.get("i"))
                if key not in self._last_market:
                    return
                if not isinstance(candle.get("x"), bool):
                    raise ValueError("Malformed kline close flag")
                self._last_market[key] = self.clock()
                reference = float(candle["c"])
                if not math.isfinite(reference) or reference <= 0:
                    raise ValueError("Invalid market reference price")
                self._reference_prices[key[0]] = reference
                if not candle["x"]:
                    return
                self._observe_time(int(candle["T"]) + 1)
                self._seen_bars[key] = max(int(candle["t"]), self._seen_bars.get(key, -1))
            if self.gateway is not None:
                self.gateway.entry_changed.set()
            if data.get("e") != "kline":
                self._account_activity += 1
            queue = self.queue if data.get("e") == "kline" else self.user_queue
            queue.put_nowait((generation, data))
        except Exception:
            self._pause()
            self.recovery.set()
            self.report("Malformed stream payload; recovering")

    async def _connect(self) -> None:
        self._connecting = True
        try:
            self.generation += 1
            generation = self.generation
            self._last_market = {}
            self.streams = self.streams_factory()
            await self.streams.connect()
            self.listen_key = await self.rest.call(self.adapter.start_listen_key)

            def callback(payload: Any) -> None:
                self._enqueue(generation, payload)

            await self.streams.subscribe_user(self.listen_key, callback)
            for symbol in self.symbols:
                for interval in self.intervals:
                    self._last_market[(symbol, interval)] = self.clock()
                    await self.streams.subscribe_klines(symbol, interval, callback)
        finally:
            self._connecting = False

    async def _retire(self) -> None:
        self._pause()
        self.generation += 1
        self._reference_prices.clear()
        for queue in (self.queue, self.user_queue):
            while not queue.empty():
                queue.get_nowait()
        streams, self.streams = self.streams, None
        key, self.listen_key = self.listen_key, None
        try:
            if streams is not None:
                try:
                    await streams.close()
                except Exception:
                    self.report("Stream close failed; owned tasks retired")
        finally:
            if key is not None:
                try:
                    await self.rest.call(self.adapter.close_listen_key, key)
                except Exception:
                    self.report("Listen key close failed; local runtime stopped")

    async def _load_backfill(self, cutoff: int) -> None:
        for symbol in self.symbols:
            for interval in self.intervals:
                key = (symbol, interval)
                frame = self.indicators[symbol][interval]
                if key not in self.last_bars:
                    # Initial REST indicators may include the current forming candle.
                    closed = [
                        next_open(int(pd.Timestamp(t).timestamp() * 1000), interval)
                        <= cutoff
                        for t in frame.index
                    ]
                    frame = frame.loc[closed].copy()
                    if frame.empty:
                        raise ValueError(
                            f"No closed warmup candles for {symbol} {interval}"
                        )
                    self.last_bars[key] = int(
                        pd.Timestamp(frame.index[-1]).timestamp() * 1000
                    )
                rows = await self.rest.call(
                    recover_history, self.adapter, symbol, interval, self.last_bars[key], cutoff
                )
                if rows:
                    records = [
                        {
                            "Open_time": pd.Timestamp(
                                r[0], unit="ms", tz="UTC"
                            ).tz_convert(self.execution_config.timezone),
                            "Symbol": symbol,
                            "Open": float(r[1]),
                            "High": float(r[2]),
                            "Low": float(r[3]),
                            "Close": float(r[4]),
                            "Volume": float(r[5]),
                        }
                        for r in rows
                    ]
                    addition = pd.DataFrame(records)
                    addition.index = pd.DatetimeIndex(addition["Open_time"])
                    frame = pd.concat([frame, addition])
                    self.last_bars[key] = int(rows[-1][0])
                self.indicators[symbol][interval] = frame
        self._reload_indicators()

    async def _synchronize(self, *, initial: bool = False) -> None:
        assert self.gateway is not None
        await self.gateway.reconcile_async()
        if initial:
            await self.gateway.initialize_leverage()
            await self.gateway.reconcile_async()
        cutoff = await self.rest.call(self.adapter.server_time)
        self._observe_time(cutoff, force=True)
        if initial:
            self.indicators = await self.rest.call(
                self.adapter.initial_indicators,
                self.symbols, self.intervals, self.execution_config.timezone
            )
        await self._load_backfill(cutoff)
        if initial and not self.failed:
            try:
                self._build_strategy()
            except Exception as error:
                self._strategy_failed(error)
        # REST and indicator loading may span another candle close. Advance the
        # warmup watermark through completion, never dispatch those queued bars as
        # fresh decisions. Each individual history pass retains its fixed cutoff.
        while not self.stop_event.is_set():
            completed_at = await self.rest.call(self.adapter.server_time)
            missing_closed_bar = any(
                next_open(next_open(opened, interval), interval) <= completed_at
                for (_, interval), opened in self.last_bars.items()
            )
            if not missing_closed_bar:
                break
            await self._load_backfill(completed_at)
        # All buffered account events use another authoritative snapshot; candle
        # identities at/before the recovery cutoff are already consumed as history.

    async def _call_strategy(
        self, callback: Callable[..., Any], *args: Any,
        bar: tuple[str, str, int] | None = None,
    ) -> None:
        deadline = self.clock() + 15
        if bar is not None:
            _, interval, opened = bar
            next_close = next_open(next_open(opened, interval), interval)
            server_now = self._server_ms + int((self.clock() - self._server_at) * 1000)
            deadline = self.clock() + max(0., (next_close - server_now) / 1000)
        decision = _Decision(self.generation, self._snapshot_revision, deadline, bar,
                             {s: self._exposure(s) for s in self.symbols})
        token = self._decision_generation.set(decision)
        try:
            result = callback(*args)
            if inspect.isawaitable(result):
                await result
        finally:
            self._decision_generation.reset(token)

    async def _order_hook(self, callback: Callable[..., Any], event: OrderEvent, generation: int) -> None:
        if generation != self.generation or self.stop_event.is_set():
            return
        try:
            await self._call_strategy(callback, event)
        except OrderOutcomeUnknown as error:
            self.report(str(error))
        except Exception as error:
            self._strategy_failed(error)

    async def _market(self, data: dict[str, Any]) -> None:
        candle = data.get("k", {})
        symbol, interval = candle.get("s", data.get("s")), candle.get("i")
        if (
            symbol not in self.symbols
            or interval not in self.intervals
            or not candle.get("x")
        ):
            return
        key = (symbol, interval)
        opened = int(candle["t"])
        previous = self.last_bars[key]
        if opened <= previous:
            return
        if opened != next_open(previous, interval):
            self._pause()
            self.recovery.set()
            return
        if not self.active or self.failed or not self._transport_ready():
            return
        timestamp = pd.Timestamp(opened, unit="ms", tz="UTC").tz_convert(
            self.execution_config.timezone
        )
        row = pd.DataFrame(
            [
                {
                    "Open_time": timestamp,
                    "Symbol": symbol,
                    "Open": float(candle["o"]),
                    "High": float(candle["h"]),
                    "Low": float(candle["l"]),
                    "Close": float(candle["c"]),
                    "Volume": float(candle["v"]),
                }
            ],
            index=[timestamp],
        )
        frame = pd.concat([self.indicators[symbol][interval], row])
        self.indicators[symbol][interval] = frame
        self.last_bars[key] = opened
        try:
            load = getattr(self.strategy, "load", None)
            self.indicators[symbol][interval] = (
                load(frame) if load is not None else frame
            )
            self.strategy.indicators = self.indicators
            if LOGGER.isEnabledFor(logging.INFO):
                LOGGER.info(
                    "Updated indicators for %s [%s]:\n%s",
                    symbol,
                    interval,
                    self.indicators[symbol][interval].tail().to_string(index=False),
                )
            await self._call_strategy(self.strategy.run, symbol, interval, bar=(symbol, interval, opened))
        except OrderOutcomeUnknown as error:
            self.report(str(error))
        except Exception as error:
            self._strategy_failed(error)

    async def _user(self, data: dict[str, Any]) -> None:
        assert self.gateway is not None
        generation = self.generation
        event = data.get("e")
        hook_data: tuple[str, dict[str, Any], str, int] | None = None
        if event not in {
            "ORDER_TRADE_UPDATE", "ALGO_UPDATE", "ACCOUNT_UPDATE", "ACCOUNT_CONFIG_UPDATE"
        }:
            return
        if event in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"}:
            order = data.get("o", {})
            symbol = order.get("s")
            if symbol not in self.symbols:
                if isinstance(symbol, str):
                    # Free margin is account-wide, even for untracked symbols.
                    await self.gateway.reconcile_async(event="ACCOUNT_UPDATE")
                return
            identifier = int(order.get("i", order.get("aid", 0)))
            key = (event, symbol, identifier)
            version = int(order.get("T", data.get("T", data.get("E", 0))))
            cumulative = float(order.get("z", 0))
            trade_id = int(order.get("t", -1))
            trade_key = (*key, trade_id)
            if event == "ORDER_TRADE_UPDATE" and cumulative > 0 and not self.failed:
                if trade_id >= 0 and trade_key not in self._trade_ids:
                    self._trade_ids.add(trade_key)
                    accumulate = getattr(
                        self.strategy, "accumulate_realized_profit", None
                    )
                    if accumulate is not None:
                        try:
                            accumulate(symbol, float(order.get("rp", 0)))
                        except Exception as error:
                            self._strategy_failed(error)
            if version < self._versions.get(
                key, -1
            ) or cumulative < self._trade_progress.get(key, 0):
                return
            self._trade_progress[key] = cumulative
            self._versions[key] = version
            status = str(order.get("X", order.get("status", "")))
            if self._last_order_update == (key, version, order):
                return
            hook_data = (symbol, order, status, identifier)
        elif (
            event == "ACCOUNT_UPDATE"
            and not any(p.get("s") in self.symbols for p in data.get("a", {}).get("P", []))
            and not data.get("a", {}).get("B")
        ):
            return
        # cw is cross-wallet balance, NOT available balance. Never apply it or
        # stale event quantities over an account snapshot. No side-effect hooks.
        await self.gateway.reconcile_async(event=event)
        if (
            generation != self.generation
            or self.recovery.is_set() or self.stop_event.is_set()
        ):
            return
        if hook_data is not None and not self.failed:
            symbol, order, status, identifier = hook_data
            hook_key = (event, symbol, identifier, status)
            methods = {
                "NEW": "on_new_order",
                "FILLED": "on_filled_order",
                "CANCELED": "on_cancelled_order",
                "EXPIRED": "on_expired_order",
                "TRIGGERED": "on_triggered_algo",
            }
            name = methods.get(status)
            # A delayed NEW notification for an absent order is stale relative to
            # the fresh snapshot. Never invoke entry hooks for it.
            present = any(
                item.order_id == identifier
                and (item.type in {OrderType.LIMIT, OrderType.MARKET})
                == (event == "ORDER_TRADE_UPDATE")
                for item in self.orders[symbol]
            )
            if status != "NEW" or present:
                self._last_order_update = (key, version, dict(order))
            method = getattr(self.strategy, name, None) if name is not None else None
            if (
                method is not None
                and hook_key not in self._hook_events
                and (status != "NEW" or present)
            ):
                self._hook_events.add(hook_key)
                try:
                    model = (
                        OrderTradeUpdateO.from_dict(order)
                        if event == "ORDER_TRADE_UPDATE"
                        else AlgoUpdateO.from_dict(order)
                    )
                    notification = (
                        OrderEvent.from_order_trade_update(model)
                        if event == "ORDER_TRADE_UPDATE"
                        else OrderEvent.from_algo_update(model)
                    )
                    # Hooks may await submissions; they must not own the account consumer.
                    task = asyncio.create_task(self._order_hook(method, notification, generation))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                    await asyncio.sleep(0)
                except OrderOutcomeUnknown as error:
                    self.report(str(error))
                except Exception as error:
                    self._strategy_failed(error)

    async def _events(self, queue: asyncio.Queue[tuple[int, dict[str, Any]]]) -> None:
        while not self.stop_event.is_set():
            generation, data = await queue.get()
            if not self.active and not self.failed:
                await self._ready.wait()
            if generation != self.generation:
                continue
            try:
                if data.get("e") == "kline":
                    await self._market(data)
                else:
                    self._user_busy += 1
                    try:
                        await self._user(data)
                    finally:
                        self._user_busy -= 1
                        if self.gateway is not None:
                            self.gateway.entry_changed.set()
            except Exception:
                self._pause()
                self.recovery.set()
                self.report("Stream processing failed; recovering")

    async def _watch(self) -> None:
        last_keepalive = self.clock()
        last_reconcile = self.clock()
        last_full_reconcile = self.clock()
        while not self.stop_event.is_set():
            await self.sleep(1)
            stream = self.streams
            if stream is None or self._connecting:
                continue
            signal = getattr(stream, "recovery", None)
            healthy = getattr(stream, "healthy", lambda: True)
            try:
                if (
                    (signal is not None and signal.is_set())
                    or not healthy()
                    or any(
                        self.clock()
                        - self._last_market.get((symbol, interval), float("-inf"))
                        > 90
                        for symbol in self.symbols
                        for interval in self.intervals
                    )
                ):
                    raise ConnectionError("Stream disconnected or stale")
                if self.active and self.clock() - last_reconcile >= 15:
                    assert self.gateway is not None
                    assert self.journal is not None
                    full = self.clock() - last_full_reconcile >= 5 * 60
                    if full or any(
                        record.state != "accepted" for record in self.journal.pending()
                    ):
                        await self.gateway.reconcile_async()
                        if full:
                            last_full_reconcile = self.clock()
                    last_reconcile = self.clock()
                if self.clock() - last_keepalive >= 50 * 60:
                    assert self.listen_key is not None
                    await self.rest.call(self.adapter.keepalive_listen_key, self.listen_key)
                    last_keepalive = self.clock()
            except Exception:
                self._pause()
                self.recovery.set()

    async def _wait_or_stop(self, awaitable: Awaitable[Any]) -> None:
        work = asyncio.ensure_future(awaitable)
        stop = asyncio.create_task(self.stop_event.wait())
        try:
            await asyncio.wait({work, stop}, return_when=asyncio.FIRST_COMPLETED)
            if work.done():
                work.result()
        finally:
            for task in (work, stop):
                if not task.done():
                    task.cancel()
            await asyncio.gather(work, stop, return_exceptions=True)

    async def _supervise(self) -> None:
        delay = 1.0
        while not self.stop_event.is_set():
            await self._wait_or_stop(self.recovery.wait())
            if self.stop_event.is_set():
                return
            self._pause()
            self._notified.difference_update(
                {
                    "Connection interrupted; strategy paused",
                    "Connection recovered; state synchronized",
                    "Recovery pending; retrying with bounded backoff",
                }
            )
            self.report("Connection interrupted; strategy paused")
            while not self.stop_event.is_set():
                try:
                    await self._retire()
                    self.recovery.clear()
                    await self._connect()
                    await self._synchronize()
                    if self.stop_event.is_set():
                        return
                    if self.recovery.is_set():
                        raise ConnectionError("Disconnected during reconciliation")
                    self.active = not self.failed
                    assert self.gateway is not None
                    self.gateway.active = self.active
                    self._ready.set()
                    self.report("Connection recovered; state synchronized")
                    delay = 1.0
                    break
                except Exception:
                    self.report("Recovery pending; retrying with bounded backoff")
                    await self._wait_or_stop(
                        self.sleep(min(60.0, max(0.0, self.jitter(delay))))
                    )
                    delay = min(60.0, delay * 2)

    def _task_finished(self, task: asyncio.Task[Any]) -> None:
        if task.cancelled() or self.closed:
            return
        error = task.exception()
        self._pause()
        self.report(f"Runtime worker stopped: {error!r}")
        self.stop_event.set()

    async def run_async(self) -> None:
        if self.running or self.closed:
            raise RuntimeError("Runtime can only be run once")
        self.running = True
        self._loop = asyncio.get_running_loop()
        owner = asyncio.current_task()
        if owner is not None:
            self.tasks.add(owner)
        try:
            self._notifications = AsyncWebhook(self.webhook)
            self.webhook = self._notifications
            self.journal = OrderJournal(self.journal_path, self.adapter.identity())
            self.journal.open()
            self.report(f"Live journal: {self.journal.path}")
            if await self.rest.call(self.adapter.account_mode):
                raise ValueError("Hedge mode is unsupported; use one-way account mode")
            self.gateway = self.gateway or ManagedOrderGateway(
                self.adapter,
                self.journal,
                self.symbols,
                self.execution_config,
                self.report,
                clock=self.clock,
            )
            self.gateway.rest = self.rest
            self.gateway.on_snapshot = self._bind_snapshot
            self.gateway.on_submission = self._adopt_submission
            self.gateway.can_send = self._transport_ready
            self.gateway.can_enter = self._entry_ready
            self.gateway.entry_abort_reason = self._entry_abort_reason
            self.gateway.entry_deadline = self._entry_deadline
            self.gateway.entry_context = self._entry_context
            self.gateway.account_activity = lambda: self._account_activity
            self.gateway.request_recovery = self._request_recovery
            self.gateway.reference_price = lambda symbol: self._reference_prices.get(
                symbol, float(self.indicators[symbol][self.intervals[0]]["Close"].iloc[-1])
            )
            # Open streams first: snapshot/backfill runs behind a closed decision gate.
            delay = 1.0
            while not self.stop_event.is_set():
                try:
                    await self._connect()
                    await self._synchronize(initial=True)
                    if self.stop_event.is_set():
                        return
                    if self.recovery.is_set():
                        raise ConnectionError("Disconnected during startup")
                    self.active = self.gateway.active = not self.failed
                    self._ready.set()
                    break
                except Exception:
                    self._pause()
                    self.report("Startup synchronization pending; retrying")
                    await self._retire()
                    self.recovery.clear()
                    await self._wait_or_stop(
                        self.sleep(min(60.0, max(0.0, self.jitter(delay))))
                    )
                    delay = min(60.0, delay * 2)
            if self.stop_event.is_set():
                return
            for coroutine in (
                self._events(self.queue), self._events(self.user_queue),
                self._watch(), self._supervise(),
            ):
                task = asyncio.create_task(coroutine)
                self.tasks.add(task)
                task.add_done_callback(self._task_finished)
            await self.stop_event.wait()
        finally:
            await self.aclose()

    def run(self) -> None:
        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            pass

    def close(self) -> None:
        self._pause()
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.stop_event.set)
        else:
            self.stop_event.set()

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        self._pause()
        tasks = self.tasks - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        try:
            if self.gateway is not None:
                await self.gateway.wait_idle()
            await self._retire()
        finally:
            if self.journal is not None:
                self.journal.close()
            self.running = False
            message = "Live runtime stopped; exchange orders and positions preserved"
            if self._notifications is not None:
                LOGGER.warning(message)
                await self._notifications.close(message)
            else:
                self.report(message)

    def __enter__(self) -> "LiveTrading":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
