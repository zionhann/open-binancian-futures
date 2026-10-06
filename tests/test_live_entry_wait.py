"""A not-yet-sent decision waits without replaying a strategy or sending twice."""

import asyncio
import threading

import pytest

from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.models import Balance, Order, Position
from open_binancian_futures.types import OrderType, PositionSide
from test_live_runtime import Adapter, INTENT, SYMBOL, candle, eventually, gateway, runtime


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


@pytest.mark.asyncio
@pytest.mark.parametrize('price, amount, side, external_order, eligible', [
    (1.6 / 3, 3, PositionSide.BUY, False, True),
    (.53333333, 3, PositionSide.BUY, False, True),
    (.53333335, 3, PositionSide.BUY, False, False),
    (.53333333, 3.001, PositionSide.BUY, False, False),
    (.53333333, 3, PositionSide.SELL, False, False),
    (.53333333, 3, PositionSide.BUY, True, False),
], ids=['exact', 'rounded', 'price', 'quantity', 'side', 'order'])
async def test_own_filled_addition_adopts_rounding_but_rejects_conflicting_exposure(
    tmp_path, price, amount, side, external_order, eligible,
):
    from open_binancian_futures.exchange_adapter import OrderReceipt
    from open_binancian_futures.models import Filter, OrderIntent

    adapter = Adapter()
    adapter.state.exchange_info.filters[SYMBOL] = Filter(.0001, .001, .01)
    adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, .5, 2, PositionSide.BUY, 10)])

    def fill(intent, identifier):
        number = len(adapter.receipts) + 1
        if number == 1:
            position = Position(SYMBOL, price, amount, side, 10)
        else:
            position = Position(SYMBOL, .55 if number == 2 else .56, number + 2, PositionSide.BUY, 10)
        adapter.state.positions[SYMBOL].update_positions([position])
        if external_order:
            adapter.state.orders[SYMBOL].add(Order(SYMBOL, 99, OrderType.LIMIT, PositionSide.BUY, .6, 1))
        receipt = OrderReceipt(number, identifier, SYMBOL, 'FILLED', 1, .6, {})
        adapter.receipts[identifier] = receipt
        return receipt

    adapter.submit = fill
    runner, streams = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=10))
    results = []

    async def entry(*args):
        intent = OrderIntent(SYMBOL, PositionSide.BUY, OrderType.MARKET, quantity=1)
        results.append(await runner.gateway.submit_order(intent))
        results.append(await runner.gateway.submit_order(intent))
        # The second weighted addition also uses the first rounded entry price.
        results.append(await runner.gateway.submit_order(intent))

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        data = candle()
        data['k'].update(o='.6', h='.6', l='.6', c='.6')
        streams[-1].emit(data)
        await eventually(lambda: len(results) == 3)
        assert results == [True, eligible, eligible]
        assert len(adapter.receipts) == (3 if eligible else 1)
        assert not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_recovery_server_time_corrects_a_fast_local_estimate(tmp_path):
    now = [0.]
    runner, streams = runtime(tmp_path, clock=lambda: now[0], config=ExecutionConfig(leverage=10))
    runner.strategy.trade = True
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        now[0] = 120.
        runner.recovery.set()
        await eventually(lambda: len(streams) == 2 and runner.active)
        streams[-1].emit(candle())
        await eventually(lambda: bool(runner.adapter.receipts))
        assert len(runner.adapter.receipts) == 1
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_shutdown_logs_cancelled_wait_and_drains_external_submission(tmp_path, caplog):
    runner, _ = runtime(tmp_path, config=ExecutionConfig(leverage=10))
    task = asyncio.create_task(runner.run_async())
    child = None
    try:
        await eventually(lambda: runner.active)
        caplog.set_level('INFO', logger='open_binancian_futures.managed_orders')
        runner.gateway.can_enter = lambda: False
        child = asyncio.create_task(runner.gateway.submit_order(INTENT))
        await eventually(lambda: runner.gateway.entry_counts['waits'] == 1)
        child.cancel()
        with pytest.raises(asyncio.CancelledError):
            await child
        assert runner.gateway.entry_counts['cancelled'] == 1
        assert 'reason=task_cancelled' in caplog.text
        assert not runner.adapter.receipts and not runner.journal.pending()
        # A second externally owned wait must leave before the journal closes.
        child = asyncio.create_task(runner.gateway.submit_order(INTENT))
        await eventually(lambda: runner.gateway.entry_counts['waits'] == 2)
        runner.close()
        await task

        assert child.done() and not runner.gateway._submissions
        assert not runner.adapter.receipts
        await asyncio.gather(child, return_exceptions=True)
    finally:
        if child is not None:
            await asyncio.gather(child, return_exceptions=True)
        runner.close()
        await task


@pytest.mark.asyncio
async def test_wait_idle_drains_submission_without_waiting_for_caller_lifetime(tmp_path):
    from open_binancian_futures.exchange_adapter import OrderOutcomeUnknown

    managed, journal = gateway(tmp_path, Adapter())
    managed.can_enter = lambda: False
    release, submitted = asyncio.Event(), asyncio.Event()

    async def caller():
        try:
            await managed.submit_order(INTENT)
        except OrderOutcomeUnknown:
            pass
        submitted.set()
        await release.wait()

    child = asyncio.create_task(caller())
    draining = None
    try:
        await eventually(lambda: managed.entry_counts['waits'] == 1)
        managed.active = False
        draining = asyncio.create_task(managed.wait_idle())
        await asyncio.sleep(.01)
        managed.entry_changed.set()
        await submitted.wait()
        await asyncio.wait_for(asyncio.shield(draining), .2)
        assert not child.done() and not managed._submissions
    finally:
        release.set()
        await child
        if draining is not None:
            await draining
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('during_read', [1, 2])
async def test_external_position_arriving_during_startup_prevents_leverage_change(tmp_path, during_read):
    adapter = Adapter()
    runner, _ = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=20), jitter=lambda delay: .01)
    original = adapter.snapshot
    reads = [0]

    def external_position(*args):
        state = original(*args)
        reads[0] += 1
        if reads[0] == during_read:
            adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 1, PositionSide.BUY, 10)])
            runner._enqueue(runner.generation, {'e': 'ACCOUNT_UPDATE', 'a': {'P': [{'s': SYMBOL, 'pa': '1'}]}})
        return state

    adapter.snapshot = external_position
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        assert runner.gateway.effective_leverage(SYMBOL) == 10
        assert ('leverage', 20) not in adapter.mutations
        assert runner.positions[SYMBOL].find_first().amount == 1
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['symbol', 'order_price'])
async def test_callback_validates_symbols_and_repriced_entry_orders(tmp_path, change):
    from open_binancian_futures.models import OrderIntent

    adapter = Adapter()
    adapter.state.orders[SYMBOL].add(Order(SYMBOL, 9, OrderType.LIMIT, PositionSide.BUY, 100, 1))
    runner, streams = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=10))
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    results = []

    async def entry(*args):
        entered.set()
        await release.wait()
        if change == 'symbol':
            with pytest.raises(ValueError, match='outside'):
                await runner.gateway.submit_order(OrderIntent('TYPO', PositionSide.BUY, OrderType.LIMIT, 100, 1))
        else:
            results.append(await runner.gateway.submit_order(INTENT))
        finished.set()

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await entered.wait()
        if change == 'order_price':
            adapter.state.orders[SYMBOL].orders[0].price = 101
            streams[-1].emit({'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {'s': SYMBOL, 'i': 9, 'X': 'NEW'}})
            await eventually(lambda: runner.orders[SYMBOL].orders[0].price == 101)
        release.set()
        await eventually(finished.is_set)
        assert not adapter.receipts and not runner.failed
        assert results == ([False] if change == 'order_price' else [])
        assert 'TYPO' not in runner.orders and 'TYPO' not in runner.positions
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_recovery_reference_price_uses_recovered_close_before_first_stream_price(tmp_path):
    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10))
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        forming = candle()
        forming['k'].update(x=False, c='100')
        streams[-1].emit(forming)
        runner.adapter.cutoff = 180000
        runner.adapter.history = lambda symbol, interval, start, end, limit=1000: [
            [t, '200', '201', '199', '200', '1', t + 59999, 0, 0, 0, 0, 0]
            for t in (60000, 120000) if t >= start]
        runner.recovery.set()
        await eventually(lambda: len(streams) == 2 and runner.active)
        assert runner.gateway.reference_price(SYMBOL) == 200
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_exchange_rejection_log_is_confirmed_not_pre_dispatch(tmp_path, caplog):
    from open_binancian_futures.exchange_adapter import OrderRejected

    managed, journal = gateway(tmp_path, Adapter())
    try:
        caplog.set_level('INFO', logger='open_binancian_futures.managed_orders')
        managed.adapter.submit_error = OrderRejected('margin')
        assert not await managed.submit_order(INTENT)
        assert 'reason=exchange_rejected' in caplog.text and 'stage=confirmed' in caplog.text
        assert not journal.pending() and managed.snapshot().balance.reserved_margin == 0
    finally:
        journal.close()
