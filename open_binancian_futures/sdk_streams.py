"""SDK 7.1.1 compatibility boundary owning its otherwise detached tasks."""

import asyncio
import json
from collections.abc import Callable
from copy import copy
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from binance_common.configuration import ConfigurationWebSocketStreams
from binance_common.utils import parse_proxies
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
        configuration = copy(configuration)
        endpoint = urlsplit(configuration.stream_url)
        self._private_url = None
        if endpoint.hostname == "fstream.binance.com":
            configuration.stream_url = "wss://fstream.binance.com/market/stream"
            self._private_url = "wss://fstream.binance.com/private/ws/"
        self.sdk = OwnedSDKStreams(configuration)
        self.recovery = self.sdk.recovery
        self._user_socket: aiohttp.ClientWebSocketResponse | None = None
        self._user_task: asyncio.Task[None] | None = None
        self._closing = False
        self._listen_key: str | None = None

    async def connect(self) -> None:
        await self.sdk.create_connection()
        if not self.healthy():
            raise ConnectionError("SDK connection was not established")

    def healthy(self) -> bool:
        return (
            bool(self.sdk.connections)
            and not self.recovery.is_set()
            and (
                self._user_task is None
                or (
                    not self._user_task.done()
                    and self._user_socket is not None
                    and not self._user_socket.closed
                )
            )
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
                raise RuntimeError("Stream already owned by another runtime")

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
        if self._private_url is None:
            handle = await self.sdk.user_data(listen_key)
            handle.on("message", callback)
            return
        if self._user_task is not None:
            raise RuntimeError("User stream already subscribed")
        try:
            configuration = self.sdk.configuration
            proxy = (
                parse_proxies(configuration.proxy)[configuration.proxy["protocol"]]
                if configuration.proxy is not None
                else None
            )
            self._user_socket = await self.sdk.session.ws_connect(
                self._private_url + listen_key,
                compress=configuration.compression,
                headers={"User-Agent": configuration.user_agent},
                max_msg_size=20 * 1024 * 1024,
                proxy=proxy,
                ssl=configuration.https_agent,
            )
        except Exception:
            self.recovery.set()
            raise ConnectionError("Private stream connection failed") from None
        self._check_owner(listen_key)
        global_user_stream_connections.stream_connections_map[listen_key] = self
        self._listen_key = listen_key
        self._user_task = asyncio.create_task(self._receive_user(callback))

    async def _receive_user(self, callback: Callable[[Any], None]) -> None:
        try:
            assert self._user_socket is not None
            async for message in self._user_socket:
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise ConnectionError("Private stream receive failed")
                payload = json.loads(message.data)
                event = payload.get("data", payload) if isinstance(payload, dict) else None
                if not isinstance(event, dict) or not isinstance(event.get("e"), str):
                    raise ValueError("Invalid private stream event")
                callback(payload)
                if event["e"] == "listenKeyExpired":
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            # Exceptions can contain the listen-key URL or raw account data.
            self.recovery.set()
        finally:
            if not self._closing:
                self.recovery.set()

    async def close(self) -> None:
        self._closing = True
        try:
            if self._user_task is not None:
                self._user_task.cancel()
                await asyncio.gather(self._user_task, return_exceptions=True)
            if self._user_socket is not None:
                await self._user_socket.close()
        finally:
            mapping = global_user_stream_connections.stream_connections_map
            if self._listen_key is not None and mapping.get(self._listen_key) is self:
                mapping.pop(self._listen_key, None)
            await self.sdk.retire()
