import asyncio
import threading

import pytest

from open_binancian_futures.exchange_adapter import OrderOutcomeUnknown
from test_live_runtime import Adapter, INTENT, SYMBOL, candle, eventually, gateway, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['snapshot', 'submit', 'set_leverage'])
async def test_rest_wait_does_not_block_event_loop(tmp_path, operation):
    from open_binancian_futures.execution import ExecutionConfig

    adapter = Adapter()
    entered, release = threading.Event(), threading.Event()
    original = getattr(adapter, operation)

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    runner, streams = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=20))
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    setattr(adapter, operation, slow)
    runner.strategy.trade = operation != 'snapshot'
    timer = threading.Timer(.3, release.set)
    timer.start()
    try:
        if operation == 'snapshot':
            streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT'}]}})
        else:
            streams[-1].emit(candle())
        await eventually(entered.is_set)
        await asyncio.sleep(.01)
        assert not release.is_set(), 'REST blocked the event loop until the request returned'
    finally:
        release.set()
        timer.cancel()
        timer.join()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_cancelled_placement_drains_before_journal_closes_and_never_resends(tmp_path):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    entered, release = threading.Event(), threading.Event()
    original = adapter.submit

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    adapter.submit = slow
    task = asyncio.create_task(managed.submit_order(INTENT))
    timer = threading.Timer(.3, release.set)
    timer.start()
    try:
        await eventually(entered.is_set)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and not release.is_set()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert journal.pending()[0].state == 'unknown'
        assert managed.snapshot().balance.reserved_margin == 10
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(INTENT)
        assert len(adapter.receipts) == 1 and SYMBOL in managed.blocked
    finally:
        release.set()
        timer.cancel()
        timer.join()
        await asyncio.gather(task, return_exceptions=True)
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['submit', 'cancel'])
async def test_dispatch_gate_rechecked_after_rest_wait_without_uncertain_record(tmp_path, operation):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    if operation == 'cancel':
        await managed.submit_order(INTENT)
        identifier = journal.pending()[0].client_order_id
    await managed.rest._mutex.acquire()
    work = managed.submit_order(INTENT) if operation == 'submit' else managed.cancel_order(identifier)
    task = asyncio.create_task(work)
    try:
        expected = 'prepared' if operation == 'submit' else 'cancel_unknown'
        await eventually(lambda: bool(journal.pending()) and journal.pending()[0].state == expected)
        managed.active = False
        managed.rest._mutex.release()
        with pytest.raises(OrderOutcomeUnknown, match='paused'):
            await task
        assert not managed.blocked and managed.snapshot().balance.reserved_margin == 0
        assert not journal.pending() if operation == 'submit' else journal.pending()[0].state == 'accepted'
        assert 'cancel' not in adapter.mutations
        assert len(adapter.receipts) == (0 if operation == 'submit' else 1)
    finally:
        if managed.rest._mutex.locked():
            managed.rest._mutex.release()
        await asyncio.gather(task, return_exceptions=True)
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('direct_close', [False, True])
async def test_shutdown_during_startup_rest_never_activates_gateway(tmp_path, direct_close):
    adapter = Adapter()
    entered, release = threading.Event(), threading.Event()
    original = adapter.snapshot

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    adapter.snapshot = slow
    runner, streams = runtime(tmp_path, adapter)
    task = asyncio.create_task(runner.run_async())
    closing = None
    try:
        await eventually(entered.is_set)
        if direct_close:
            closing = asyncio.create_task(runner.aclose())
        else:
            runner.close()
        await asyncio.sleep(.01)
        assert not task.done() and not runner.active
        release.set()
        if closing is not None:
            await closing
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        assert runner.closed and not runner.gateway.active and not runner.tasks
        assert streams[0].closed and not adapter.receipts
    finally:
        release.set()
        runner.close()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_drains_strategy_created_submission_before_journal_close(tmp_path):
    adapter = Adapter()
    runner, _ = runtime(tmp_path, adapter)
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    entered, release = threading.Event(), threading.Event()
    original = adapter.submit

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    adapter.submit = slow
    child = asyncio.create_task(runner.gateway.submit_order(INTENT))
    try:
        await eventually(entered.is_set)
        runner.close()
        await asyncio.sleep(.01)
        assert not task.done() and not child.done()
        release.set()
        assert await child
        await task
        assert runner.closed and len(adapter.receipts) == 1 and not runner.tasks
    finally:
        release.set()
        runner.close()
        await asyncio.gather(child, task, return_exceptions=True)


@pytest.mark.asyncio
async def test_account_event_waiting_on_order_lock_prevents_entry_after_leverage_read(tmp_path):
    from open_binancian_futures.execution import ExecutionConfig

    adapter = Adapter()
    runner, streams = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=20))
    runner.strategy.trade = True
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    entered, release = threading.Event(), threading.Event()
    original = adapter.set_leverage

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    adapter.set_leverage = slow
    try:
        streams[-1].emit(candle())
        await eventually(entered.is_set)
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT'}]}})
        await eventually(lambda: runner.gateway._refresh_waiters == 1)
        assert runner.user_queue.empty()
        release.set()
        await eventually(lambda: not runner.gateway._mutex.locked())
        assert not adapter.receipts and not runner.journal.pending() and not runner.failed
        streams[-1].emit(candle(120000))
        await eventually(lambda: bool(adapter.receipts))
    finally:
        release.set()
        runner.close()
        await task
