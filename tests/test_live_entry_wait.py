"""A not-yet-sent decision waits without replaying a strategy or sending twice."""

import asyncio
import threading

import pytest

from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.models import Balance, Order, Position
from open_binancian_futures.types import OrderType, PositionSide
from test_live_runtime import Adapter, INTENT, SYMBOL, candle, eventually, runtime


@pytest.mark.asyncio
async def test_pending_refresh_resumes_same_decision_with_latest_balance(tmp_path):
    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10))
    entered, release = threading.Event(), threading.Event()
    finished = asyncio.Event()
    results = []
    original = runner.adapter.snapshot

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    async def entry(*args):
        results.append(await runner.gateway.submit_order(INTENT))
        finished.set()

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        runner.adapter.snapshot = slow
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT', 'wb': '42'}]}})
        await eventually(entered.is_set)
        streams[-1].emit(candle())
        await asyncio.sleep(.01)
        assert not finished.is_set() and not runner.adapter.receipts
        runner.adapter.state.balance = Balance(42)
        release.set()
        await eventually(finished.is_set)
        assert results == [True] and len(runner.adapter.receipts) == 1
        assert runner.balance.available == 42 and not runner.failed
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['next_bar', 'elapsed', 'position', 'order', 'recovery'])
async def test_suspended_entry_cancels_on_expiry_or_conflict(tmp_path, reason):
    now = [0.]
    runner, streams = runtime(tmp_path, clock=lambda: now[0])
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    results = []

    async def entry(*args):
        entered.set()
        await release.wait()
        results.append(await runner.gateway.submit_order(INTENT))
        finished.set()

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await entered.wait()
        if reason == 'next_bar':
            streams[-1].emit(candle(120000))
        elif reason == 'elapsed':
            now[0] = 61.
        elif reason == 'position':
            runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 1, PositionSide.BUY, 10)])
            streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'P': [{'s': SYMBOL, 'pa': '1'}]}})
            await eventually(lambda: bool(runner.positions[SYMBOL]))
        elif reason == 'order':
            runner.adapter.state.orders[SYMBOL].add(Order(SYMBOL, 9, OrderType.LIMIT, PositionSide.BUY, 100, 1))
            streams[-1].emit({'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {'s': SYMBOL, 'i': 9, 'X': 'NEW'}})
            await eventually(lambda: bool(runner.orders[SYMBOL]))
        else:
            runner.recovery.set()
            await eventually(lambda: runner.generation > 1 and runner.active)
        # Keep a later candle's separate decision from submitting in this test.
        runner.strategy.run = lambda *args: None
        release.set()
        await eventually(finished.is_set)
        assert results == [False] and not runner.adapter.receipts and not runner.failed
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_order_hook_entry_does_not_wait_for_its_own_user_consumer(tmp_path):
    runner, streams = runtime(tmp_path)
    results = []

    async def hook(event):
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT', 'wb': '100'}]}})
        results.append(await runner.gateway.submit_order(INTENT))

    runner.strategy.on_filled_order = hook
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit({'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {'s': SYMBOL, 'i': 7, 'X': 'FILLED', 'z': '1', 't': 1}})
        await eventually(lambda: bool(results))
        assert results == [True] and len(runner.adapter.receipts) == 1
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', ['none', 'position', 'order'])
async def test_startup_sets_leverage_only_without_existing_exposure(tmp_path, existing):
    adapter = Adapter()
    if existing == 'position':
        adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 1, PositionSide.BUY, 10)])
    elif existing == 'order':
        adapter.state.orders[SYMBOL].add(Order(SYMBOL, 9, OrderType.LIMIT, PositionSide.BUY, 100, 1))
    runner, streams = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=20))
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        assert runner.gateway.effective_leverage(SYMBOL) == (20 if existing == 'none' else 10)
        assert ('leverage', 20) in adapter.mutations if existing == 'none' else ('leverage', 20) not in adapter.mutations
        adapter.state.leverage[SYMBOL] = 7
        streams[-1].emit({'e': 'ACCOUNT_CONFIG_UPDATE', 'ac': {'s': SYMBOL, 'l': 7}})
        await eventually(lambda: runner.gateway.effective_leverage(SYMBOL) == 7)
        runner.recovery.set()
        await eventually(lambda: len(streams) == 2 and runner.active)
        assert runner.gateway.effective_leverage(SYMBOL) == 7
        assert await runner.gateway.submit_order(INTENT)
        assert runner.gateway.effective_leverage(SYMBOL) == 7
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_event_arriving_at_dispatch_retries_only_the_unsent_order(tmp_path, caplog):
    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10))
    runner.strategy.trade = True
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        await runner.gateway.rest._mutex.acquire()
        caplog.set_level('INFO', logger='open_binancian_futures.managed_orders')
        streams[-1].emit(candle())
        await eventually(lambda: bool(runner.journal.pending()))
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT'}]}})
        runner.gateway.rest._mutex.release()
        await eventually(lambda: runner.gateway.entry_counts['submitted'] == 1)
        assert len(runner.adapter.receipts) == 1
        assert runner.balance.reserved_margin == 0
        assert runner.gateway.entry_counts['waits'] == 1
        assert 'stage=pre_dispatch' in caplog.text and 'stage=confirmed' in caplog.text
        assert not any(record.state == 'unknown' for record in runner.journal.pending())
    finally:
        if runner.gateway.rest._mutex.locked():
            runner.gateway.rest._mutex.release()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_startup_leverage_failure_retries_before_activation(tmp_path):
    adapter = Adapter()
    original = adapter.set_leverage
    failed = asyncio.Event()
    release = asyncio.Event()
    broken = [True]

    def change(*args):
        if broken[0]:
            raise ConnectionError('unavailable')
        return original(*args)

    async def retry(delay):
        if not broken[0]:
            await asyncio.sleep(delay)
            return
        failed.set()
        await release.wait()

    adapter.set_leverage = change
    runner, _ = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=20), sleep=retry)
    task = asyncio.create_task(runner.run_async())
    try:
        await failed.wait()
        assert not runner.active and not runner.failed and not adapter.receipts
        broken[0] = False
        release.set()
        await eventually(lambda: runner.active)
        assert runner.gateway.effective_leverage(SYMBOL) == 20
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_waited_market_entry_uses_latest_forming_price_without_running_strategy(tmp_path):
    from open_binancian_futures.models import OrderIntent

    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10, position_size=.1))
    results = []

    async def entry(*args):
        results.append(await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.MARKET)))

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        await runner.gateway.rest._mutex.acquire()
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT'}]}})
        await eventually(lambda: runner.gateway._refresh_waiters == 1)
        streams[-1].emit(candle())
        await eventually(lambda: runner.gateway.entry_counts['waits'] == 1)
        forming = candle(120000)
        forming['k'].update(x=False, c='200')
        streams[-1].emit(forming)
        runner.gateway.rest._mutex.release()
        await eventually(lambda: bool(results))
        assert results == [True]
        submitted = next(m[1] for m in runner.adapter.mutations if isinstance(m, tuple) and m[0] == 'submit')
        assert submitted.quantity == .5
        assert runner.indicators[SYMBOL]['1m'].Close.iloc[-1] == 100
    finally:
        if runner.gateway.rest._mutex.locked():
            runner.gateway.rest._mutex.release()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_price_change_in_rest_dispatch_slot_resizes_unsent_market_entry(tmp_path):
    from open_binancian_futures.models import OrderIntent

    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10, position_size=.1))
    results = []

    async def entry(*args):
        results.append(await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.MARKET)))

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        await runner.gateway.rest._mutex.acquire()
        streams[-1].emit(candle())
        await eventually(lambda: bool(runner.journal.pending()))
        assert runner.balance.reserved_margin == 10
        forming = candle(120000)
        forming['k'].update(x=False, c='200')
        streams[-1].emit(forming)
        runner.gateway.rest._mutex.release()
        await eventually(lambda: bool(results))
        assert results == [True] and len(runner.adapter.receipts) == 1
        submitted = next(m[1] for m in runner.adapter.mutations if isinstance(m, tuple) and m[0] == 'submit')
        assert submitted.quantity == .5 and runner.balance.reserved_margin == 0
    finally:
        if runner.gateway.rest._mutex.locked():
            runner.gateway.rest._mutex.release()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_own_acceptance_does_not_adopt_external_position_for_second_entry(tmp_path):
    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10))
    entered, release = threading.Event(), threading.Event()
    results = []
    original = runner.adapter.submit

    def slow(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    async def entry(*args):
        results.append(await runner.gateway.submit_order(INTENT))
        results.append(await runner.gateway.submit_order(INTENT))

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        runner.adapter.submit = slow
        streams[-1].emit(candle())
        await eventually(entered.is_set)
        runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 1, PositionSide.BUY, 10)])
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'P': [{'s': SYMBOL, 'pa': '1'}]}})
        release.set()
        await eventually(lambda: len(results) == 2)
        assert results == [True, False] and len(runner.adapter.receipts) == 1
    finally:
        release.set()
        runner.close()
        await task
