import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from binance_common.configuration import ConfigurationWebSocketStreams
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL,
)

from open_binancian_futures.client import MAINNET_WS_STREAMS
from open_binancian_futures.sdk_streams import BinanceStreams


class Socket:
    def __init__(self):
        self.closed = False
        self.messages = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.messages.get()
        if message is None:
            raise StopAsyncIteration
        if isinstance(message, Exception):
            raise message
        return message

    async def close(self):
        self.closed = True

    def send(self, payload):
        self.messages.put_nowait(
            SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(payload))
        )


def stream_with_socket(monkeypatch):
    stream = BinanceStreams(ConfigurationWebSocketStreams(stream_url=MAINNET_WS_STREAMS))
    socket = Socket()
    stream.sdk.session = SimpleNamespace(
        ws_connect=AsyncMock(return_value=socket), close=AsyncMock()
    )
    stream.sdk.connections = [
        SimpleNamespace(websocket=SimpleNamespace(closed=False), reconnect=False)
    ]
    monkeypatch.setattr(stream.sdk, 'retire', AsyncMock())
    monkeypatch.setattr(stream.sdk, 'user_data', AsyncMock())
    return stream, socket


@pytest.mark.parametrize('url', [MAINNET_WS_STREAMS, 'wss://fstream.binance.com/stream'])
def test_mainnet_routes_on_copy(url):
    config = ConfigurationWebSocketStreams(stream_url=url)
    stream = BinanceStreams(config)
    assert stream.sdk.configuration.stream_url == 'wss://fstream.binance.com/market/stream'
    assert config.stream_url == url


@pytest.mark.asyncio
async def test_testnet_retains_sdk_subscription(monkeypatch):
    config = ConfigurationWebSocketStreams(
        stream_url=DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL
    )
    stream = BinanceStreams(config)
    handle = SimpleNamespace(on=lambda name, callback: None)
    subscribe = AsyncMock(return_value=handle)
    monkeypatch.setattr(stream.sdk, 'user_data', subscribe)
    await stream.subscribe_user('test-key', lambda event: None)
    subscribe.assert_awaited_once_with('test-key')
    assert stream.sdk.configuration.stream_url == config.stream_url.rstrip("/") + "/stream"
    await stream.close()


@pytest.mark.asyncio
async def test_private_delivery_expiry_and_task_cleanup(monkeypatch, caplog):
    stream, socket = stream_with_socket(monkeypatch)
    received = []
    await stream.subscribe_user('fake-secret-key', received.append)
    assert stream.healthy()
    assert stream.sdk.session.ws_connect.call_args.args == (
        'wss://fstream.binance.com/private/ws/fake-secret-key',
    )
    stream.sdk.user_data.assert_not_called()
    for event in ['ACCOUNT_UPDATE', 'ORDER_TRADE_UPDATE', 'ALGO_UPDATE']:
        socket.send({'e': event})
    socket.send({'data': {'e': 'listenKeyExpired'}})
    await asyncio.wait_for(stream.recovery.wait(), 1)
    assert [item.get('e', item.get('data', {}).get('e')) for item in received] == [
        'ACCOUNT_UPDATE', 'ORDER_TRADE_UPDATE', 'ALGO_UPDATE', 'listenKeyExpired'
    ]
    assert not stream.healthy()
    await stream.close()
    assert stream._user_task.done() and socket.closed
    stream.sdk.retire.assert_awaited_once()
    assert 'fake-secret-key' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['json', 'shape', 'error-frame', 'disconnect', 'exception', 'callback'])
async def test_private_failure_requests_recovery_without_secret_logs(monkeypatch, caplog, failure):
    stream, socket = stream_with_socket(monkeypatch)

    def callback(event):
        if failure == 'callback':
            raise ValueError('fake-secret-key')

    await stream.subscribe_user('fake-secret-key', callback)
    if failure == 'json':
        socket.messages.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data='fake-secret-key'))
    elif failure == 'shape':
        socket.send(['fake-secret-key'])
    elif failure == 'error-frame':
        socket.messages.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.ERROR))
    elif failure == 'disconnect':
        socket.messages.put_nowait(None)
    elif failure == 'exception':
        socket.messages.put_nowait(OSError('fake-secret-key'))
    else:
        socket.send({'e': 'ACCOUNT_UPDATE'})
    await asyncio.wait_for(stream.recovery.wait(), 1)
    assert not stream.healthy()
    await stream.close()
    assert socket.closed and stream._user_task.done()
    assert 'fake-secret-key' not in caplog.text


@pytest.mark.asyncio
async def test_private_connect_failure_is_sanitized(monkeypatch, caplog):
    stream, _ = stream_with_socket(monkeypatch)
    stream.sdk.session.ws_connect.side_effect = OSError('fake-secret-key')
    with pytest.raises(ConnectionError, match='Private stream connection failed') as error:
        await stream.subscribe_user('fake-secret-key', lambda event: None)
    assert 'fake-secret-key' not in str(error.value)
    assert error.value.__suppress_context__ and stream.recovery.is_set()
    await stream.close()
    stream.sdk.retire.assert_awaited_once()
    assert 'fake-secret-key' not in caplog.text


@pytest.mark.asyncio
async def test_close_cancels_pending_receiver_even_if_socket_close_fails(monkeypatch):
    stream, socket = stream_with_socket(monkeypatch)
    await stream.subscribe_user('fake-secret-key', lambda event: None)
    await asyncio.sleep(0)
    monkeypatch.setattr(socket, 'close', AsyncMock(side_effect=OSError('close failed')))
    with pytest.raises(OSError):
        await stream.close()
    assert stream._user_task.done() and not stream.recovery.is_set()
    stream.sdk.retire.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_subscription_without_kline_still_recovers(tmp_path):
    from test_live_runtime import runtime

    now = [0.0]
    runner, _ = runtime(tmp_path, clock=lambda: now[0])
    await runner._connect()  # Fake subscriptions succeed but deliver no market data.
    runner.active = True

    async def tick(delay):
        now[0] = 91
        runner.stop_event.set()

    runner.sleep = tick
    await runner._watch()
    assert runner.recovery.is_set() and not runner.active
    await runner.aclose()


@pytest.mark.asyncio
async def test_private_registry_ownership_and_cleanup(monkeypatch):
    from binance_common.websocket import global_user_stream_connections

    first, _ = stream_with_socket(monkeypatch)
    second, _ = stream_with_socket(monkeypatch)
    await first.subscribe_user('fake-secret-key', lambda event: None)
    try:
        with pytest.raises(RuntimeError, match='another runtime'):
            await second.subscribe_user('fake-secret-key', lambda event: None)
        second.sdk.session.ws_connect.assert_not_called()
        assert global_user_stream_connections.stream_connections_map['fake-secret-key'] is first
    finally:
        await first.close()
        await second.close()
    assert 'fake-secret-key' not in global_user_stream_connections.stream_connections_map
