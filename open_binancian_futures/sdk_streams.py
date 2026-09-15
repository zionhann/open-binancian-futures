"""SDK 7.1.1 compatibility boundary owning its otherwise detached tasks."""

import asyncio
from collections.abc import Callable
from copy import copy
from typing import Any

from binance_common.configuration import ConfigurationWebSocketStreams
from binance_common.websocket import (
    global_stream_connections,
    global_user_stream_connections,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams import (
    DerivativesTradingUsdsFuturesWebSocketStreams,
)


class OwnedSDKStreams(DerivativesTradingUsdsFuturesWebSocketStreams):
    def __init__(self, configuration: ConfigurationWebSocketStreams) -> None:
        super().__init__(copy(configuration))
        self.owned_tasks: set[asyncio.Task[Any]] = set()
        self.owned_connections: list[Any] = []
        self.recovery = asyncio.Event()
        self.retiring = False
        self.error: Exception | None = None

    async def init_connection(self, *args: Any, **kwargs: Any) -> None:
        try:
            await super().init_connection(*args, **kwargs)
        finally:
            for connection in self.connections:
                if not any(connection is item for item in self.owned_connections):
                    self.owned_connections.append(connection)
            await asyncio.sleep(0)

    async def receive_loop(self, connection: Any) -> None:
        task = asyncio.current_task()
        if task is None or self.retiring:
            return
        self.owned_tasks.add(task)
        cancelled = False
        try:
            await super().receive_loop(connection)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as error:
            self.error = error
        finally:
            self.owned_tasks.discard(task)
            if not self.retiring and not cancelled:
                self.recovery.set()

    async def schedule_reconnect(
        self, connection: Any, configuration: Any, delay: float
    ) -> None:
        task = asyncio.current_task()
        if task is None or self.retiring:
            return
        self.owned_tasks.add(task)
        try:
            await asyncio.sleep(delay)
            if not self.retiring:
                self.recovery.set()
        finally:
            self.owned_tasks.discard(task)

    async def retire(self) -> None:
        self.retiring = True
        await asyncio.sleep(0)
        tasks = self.owned_tasks - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.close_connection()
        finally:
            for connection in self.connections:
                if not any(connection is item for item in self.owned_connections):
                    self.owned_connections.append(connection)
            for registry in (global_stream_connections, global_user_stream_connections):
                mapping = registry.stream_connections_map
                for name, connection in list(mapping.items()):
                    if any(connection is owned for owned in self.owned_connections):
                        if mapping.get(name) is connection:
                            mapping.pop(name, None)
                        connection.stream_callback_map.pop(name, None)
                        connection.response_types.pop(name, None)


class BinanceStreams:
    def __init__(self, configuration: ConfigurationWebSocketStreams) -> None:
        self.sdk = OwnedSDKStreams(configuration)
        self.recovery = self.sdk.recovery

    async def connect(self) -> None:
        await self.sdk.create_connection()
        if not self.healthy():
            raise ConnectionError("SDK connection was not established")

    def healthy(self) -> bool:
        return (
            bool(self.sdk.connections)
            and not self.recovery.is_set()
            and all(
                connection.websocket is not None
                and not connection.websocket.closed
                and not connection.reconnect
                for connection in self.sdk.connections
            )
        )

    def _check_owner(self, name: str) -> None:
        for registry in (global_stream_connections, global_user_stream_connections):
            existing = registry.stream_connections_map.get(name)
            if existing is not None and not any(
                existing is connection for connection in self.sdk.owned_connections
            ):
                raise RuntimeError(f"Stream already owned by another runtime: {name}")

    async def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[Any], None]
    ) -> None:
        self._check_owner(f"{symbol.lower()}@kline_{interval}")
        handle = await self.sdk.kline_candlestick_streams(
            symbol=symbol.lower(), interval=interval
        )
        handle.on("message", callback)

    async def subscribe_user(
        self, listen_key: str, callback: Callable[[Any], None]
    ) -> None:
        self._check_owner(listen_key)
        handle = await self.sdk.user_data(listen_key)
        handle.on("message", callback)

    async def close(self) -> None:
        await self.sdk.retire()
