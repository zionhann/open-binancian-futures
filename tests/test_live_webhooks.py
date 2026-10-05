import asyncio
import threading
from types import SimpleNamespace

import pytest

from open_binancian_futures.strategy import Strategy
from open_binancian_futures.webhook import AsyncWebhook
from test_live_runtime import runtime, eventually


@pytest.mark.asyncio
async def test_slow_webhook_does_not_block_loop_and_shutdown_drains():
    entered, release = threading.Event(), threading.Event()
    messages = []
    def send(message):
        entered.set()
        assert release.wait(2)
        messages.append(message)
    hook = AsyncWebhook(SimpleNamespace(send_message=send))
    try:
        hook.send_message('first')
        await eventually(entered.is_set)
        closing = asyncio.create_task(hook.close())
        await asyncio.sleep(.01)
        assert not release.is_set() and not closing.done()
        release.set()
        await closing
        assert messages == ['first'] and hook.task.done()
    finally:
        release.set()
        await hook.close()


@pytest.mark.asyncio
async def test_default_fill_notification_failure_does_not_latch_strategy(tmp_path):
    class RealStrategy(Strategy):
        def load(self, frame): return frame
        async def run(self, *args): pass
        async def run_backtest(self, *args): pass
    strategy = RealStrategy(None, None, None, None, None, None, None)
    runner, streams = runtime(tmp_path, strategy=strategy)
    attempted = []
    def send(message):
        attempted.append(message)
        raise RuntimeError('mock notification outage')
    runner.webhook = SimpleNamespace(send_message=send)
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit({'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {
            's': 'BTCUSDT', 'i': 7, 't': 12, 'z': '1', 'rp': '1', 'X': 'FILLED',
            'o': 'LIMIT', 'S': 'BUY', 'q': '1', 'p': '100', 'ap': '100'}})
        await eventually(lambda: any('[FILLED]' in message for message in attempted))
        assert runner.active and not runner.failed and not runner.gateway.failed
        assert strategy._realized_profit['BTCUSDT'] == 0
    finally:
        runner.close()
        await task
    assert runner._notifications.task.done()


@pytest.mark.asyncio
async def test_notification_backlog_is_bounded(caplog):
    hook = AsyncWebhook(SimpleNamespace(send_message=lambda message: None))
    for _ in range(129):
        hook.send_message('notice')
    await asyncio.sleep(0)
    assert hook.queue.qsize() == 128
    assert 'backlog full' in caplog.text
    await hook.close()


@pytest.mark.asyncio
async def test_notifications_from_a_worker_thread_are_delivered():
    messages = []
    hook = AsyncWebhook(SimpleNamespace(send_message=messages.append))
    await asyncio.to_thread(hook.send_message, 'worker notice')
    await hook.close()
    assert messages == ['worker notice']


@pytest.mark.asyncio
async def test_shutdown_does_not_hang_if_notification_worker_is_cancelled():
    hook = AsyncWebhook(SimpleNamespace(send_message=lambda message: None))
    hook.task.cancel()
    await asyncio.gather(hook.task, return_exceptions=True)
    hook.send_message('undeliverable notice')
    await asyncio.sleep(0)
    await asyncio.wait_for(hook.close(), timeout=.5)


@pytest.mark.asyncio
async def test_full_backlog_keeps_final_shutdown_notice():
    entered, release = threading.Event(), threading.Event()
    messages = []
    def send(message):
        entered.set()
        assert release.wait(2)
        messages.append(message)
    hook = AsyncWebhook(SimpleNamespace(send_message=send))
    try:
        hook.send_message('in flight')
        await eventually(entered.is_set)
        for _ in range(128):
            hook.send_message('queued')
        await asyncio.sleep(0)
        assert hook.queue.full()
        closing = asyncio.create_task(hook.close('shutdown'))
        await asyncio.sleep(.01)
        release.set()
        await closing
        assert messages == ['in flight'] + ['queued'] * 128 + ['shutdown']
    finally:
        release.set()
        await hook.close()
