import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from binance_common.configuration import ConfigurationWebSocketStreams
from binance_common.websocket import WebSocketCommon, global_stream_connections
from open_binancian_futures.sdk_streams import OwnedSDKStreams, BinanceStreams


@pytest.mark.asyncio
async def test_receive_exit_and_retired_timer_are_owned(monkeypatch):
    monkeypatch.setattr(WebSocketCommon, 'receive_loop', AsyncMock())
    sdk = OwnedSDKStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    await sdk.receive_loop(SimpleNamespace())
    assert sdk.recovery.is_set()
    sdk.recovery.clear()
    task = asyncio.create_task(sdk.schedule_reconnect(None, None, 10000))
    await asyncio.sleep(0)
    assert task in sdk.owned_tasks
    await sdk.retire()
    assert task.done() and not sdk.owned_tasks and not sdk.recovery.is_set()


@pytest.mark.asyncio
async def test_cleanup_identity_scoped_even_close_failure(monkeypatch):
    sdk = OwnedSDKStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    own = SimpleNamespace(id=1, stream_callback_map={'mine': []}, response_types={'mine': object})
    foreign = SimpleNamespace(id=1, stream_callback_map={}, response_types={})
    sdk.owned_connections.append(own)
    mapping = global_stream_connections.stream_connections_map
    mapping['mine'], mapping['foreign'] = own, foreign
    monkeypatch.setattr(sdk, 'close_connection', AsyncMock(side_effect=OSError('closed')))
    try:
        with pytest.raises(OSError):
            await sdk.retire()
        assert 'mine' not in mapping and mapping['foreign'] is foreign
    finally:
        mapping.pop('foreign', None)


@pytest.mark.asyncio
async def test_swallowed_sdk_connection_failure_not_healthy(monkeypatch):
    stream = BinanceStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    monkeypatch.setattr(stream.sdk, 'create_connection', AsyncMock())
    with pytest.raises(ConnectionError):
        await stream.connect()
    await stream.close()

@pytest.mark.asyncio
async def test_actual_sdk_immediate_connection_close_owns_all_tasks(monkeypatch):
    socket=SimpleNamespace(closed=False,close=AsyncMock())
    async def messages():
        await asyncio.Event().wait()
        yield None
    class Socket:
        closed=False
        def __aiter__(self): return messages()
        async def close(self): self.closed=True
    ws=Socket()
    sdk=OwnedSDKStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    sdk.session=SimpleNamespace(ws_connect=AsyncMock(return_value=ws),close=AsyncMock(),closed=False)
    await sdk.init_connection('wss://example/stream',sdk.configuration)
    tasks=set(sdk.owned_tasks)
    assert len(tasks)==2
    await sdk.retire()
    assert all(task.done() for task in tasks) and not sdk.owned_tasks and ws.closed


@pytest.mark.asyncio
async def test_actual_sdk_receiver_error_triggers_recovery(monkeypatch):
    monkeypatch.setattr(WebSocketCommon,'receive_loop',AsyncMock(side_effect=ValueError('malformed payload')))
    sdk=OwnedSDKStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    await sdk.receive_loop(SimpleNamespace())
    assert sdk.recovery.is_set() and isinstance(sdk.error,ValueError) and not sdk.owned_tasks
    await sdk.retire()


@pytest.mark.asyncio
async def test_rotation_signals_supervisor_without_sdk_reconnect(monkeypatch):
    sdk=OwnedSDKStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    reconnect=AsyncMock();monkeypatch.setattr(sdk,'reconnect',reconnect)
    await sdk.schedule_reconnect(None,None,0)
    assert sdk.recovery.is_set() and not sdk.owned_tasks
    reconnect.assert_not_called()
    await sdk.retire()


@pytest.mark.asyncio
async def test_foreign_user_registry_blocks_subscription():
    from binance_common.websocket import global_user_stream_connections
    stream=BinanceStreams(ConfigurationWebSocketStreams(stream_url='wss://example/stream'))
    foreign=object();mapping=global_user_stream_connections.stream_connections_map
    mapping['foreign-key']=foreign
    try:
        with pytest.raises(RuntimeError,match='another runtime'):
            await stream.subscribe_user('foreign-key',lambda event:None)
        assert mapping['foreign-key'] is foreign
    finally:
        mapping.pop('foreign-key',None)
        await stream.close()
