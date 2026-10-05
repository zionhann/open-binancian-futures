"""Account events must progress while a market decision awaits external work."""

import asyncio

import pytest

from test_live_runtime import INTENT, SYMBOL, candle, eventually, runtime
from open_binancian_futures.exchange_adapter import OrderOutcomeUnknown
from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.models import Balance, OrderIntent, Position
from open_binancian_futures.types import OrderType, PositionSide


@pytest.mark.asyncio
async def test_account_refresh_invalidates_suspended_entry_without_stopping_runtime(tmp_path):
    runner, streams = runtime(tmp_path, config=ExecutionConfig(leverage=10))
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    rejected = []

    async def decision(*args):
        entered.set()
        await release.wait()
        try:
            await runner.gateway.submit_order(INTENT)
        except OrderOutcomeUnknown as error:
            rejected.append(str(error))
        finally:
            finished.set()

    runner.strategy.run = decision
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await entered.wait()
        runner.adapter.state.balance = Balance(42)
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT', 'wb': '42'}]}})
        await eventually(lambda: runner.balance.available == 42)
        assert not finished.is_set()
        release.set()
        await finished.wait()
        assert rejected and not runner.adapter.receipts
        assert runner.active and not runner.failed
        async def current_decision(*args):
            await runner.gateway.submit_order(INTENT)
        runner.strategy.run = current_decision
        streams[-1].emit(candle(120000))
        await eventually(lambda: bool(runner.adapter.receipts))
    finally:
        release.set()
        runner.close()
        await task
    assert not runner.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['PARTIALLY_FILLED', 'FILLED', 'CANCELED'])
async def test_order_state_and_fill_protection_progress_during_slow_strategy(tmp_path, status):
    runner, streams = runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    hooks = []

    async def slow(*args):
        entered.set()
        await release.wait()

    async def protect(event):
        hooks.append(event.order_id)
        await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))

    runner.strategy.run = slow
    runner.strategy.on_filled_order = protect
    runner.strategy.on_cancelled_order = lambda event: hooks.append(event.order_id)
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await entered.wait()
        amount = 0 if status == 'CANCELED' else 0.5
        runner.adapter.state.positions[SYMBOL].update_positions(
            [Position(SYMBOL, 100, amount, PositionSide.BUY, leverage=10)] if amount else []
        )
        runner.adapter.state.balance = Balance(77)
        streams[-1].emit({'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {'s': SYMBOL, 'i': 7, 't': 12, 'z': str(amount), 'rp': '1' if amount else '0', 'X': status, 'o': 'LIMIT', 'S': 'BUY', 'q': '1', 'p': '100'}})
        await eventually(lambda: runner.balance.available == 77)
        if status == 'FILLED':
            await eventually(lambda: bool(runner.adapter.receipts))
            intent = next(m[1] for m in runner.adapter.mutations if isinstance(m, tuple) and m[0] == 'submit')
            assert intent.close_position and intent.side == PositionSide.SELL
        elif status == 'CANCELED':
            assert hooks == [7]
        else:
            assert runner.positions[SYMBOL].find_first().amount == 0.5
            assert runner.strategy.pnl == 1
        assert not release.is_set() and not runner.failed
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_market_callbacks_remain_sequential(tmp_path):
    runner, streams = runtime(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow(*args):
        calls.append(runner.last_bars[(SYMBOL, '1m')])
        if len(calls) == 1:
            entered.set()
            await release.wait()

    runner.strategy.run = slow
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await entered.wait()
        streams[-1].emit(candle(120000))
        await asyncio.sleep(0.01)
        assert calls == [60000]
        release.set()
        await eventually(lambda: calls == [60000, 120000])
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_own_order_refresh_keeps_current_decision_eligible(tmp_path):
    runner, streams = runtime(tmp_path)
    completed = asyncio.Event()

    async def decision(*args):
        assert await runner.gateway.submit_order(INTENT)
        assert await runner.gateway.submit_order(INTENT)
        completed.set()

    runner.strategy.run = decision
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await eventually(completed.is_set)
        assert len(runner.adapter.receipts) == 2 and not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('delegation', ['child_then_parent', 'gather'])
async def test_callback_owned_refresh_is_shared_with_parent_and_sibling_tasks(tmp_path, delegation):
    runner, streams = runtime(tmp_path)
    completed = asyncio.Event()

    async def decision(*args):
        if delegation == 'gather':
            assert all(await asyncio.gather(
                runner.gateway.submit_order(INTENT), runner.gateway.submit_order(INTENT)
            ))
        else:
            assert await asyncio.create_task(runner.gateway.submit_order(INTENT))
            assert await runner.gateway.submit_order(INTENT)
        completed.set()

    runner.strategy.run = decision
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await eventually(completed.is_set)
        assert len(runner.adapter.receipts) == 2 and not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_retirement_drops_old_queued_events_before_next_entry(tmp_path):
    runner, streams = runtime(tmp_path)
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        old_generation = runner.generation
        runner.user_queue.put_nowait((old_generation, {'e': 'ACCOUNT_UPDATE'}))
        runner.queue.put_nowait((old_generation, candle()))
        await runner._retire()
        assert runner.user_queue.empty() and runner.queue.empty()
        assert runner._entry_ready()
        streams[0].emit({'e': 'ACCOUNT_UPDATE'})
        assert runner.user_queue.empty()
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_strategy_failure_keeps_account_monitoring_without_hooks_or_orders(tmp_path):
    runner, streams = runtime(tmp_path)
    runner.strategy.fail = True
    hooks = []
    runner.strategy.on_filled_order = lambda event: hooks.append(event)
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await eventually(lambda: runner.failed)
        runner.adapter.state.balance = Balance(42)
        streams[-1].emit({'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {
            's': SYMBOL, 'i': 7, 't': 12, 'z': '1', 'rp': '0', 'X': 'FILLED',
            'o': 'LIMIT', 'S': 'BUY', 'q': '1', 'p': '100',
        }})
        await eventually(lambda: runner.balance.available == 42)
        with pytest.raises(OrderOutcomeUnknown, match='paused'):
            await runner.gateway.submit_order(INTENT)
        assert not hooks and not runner.adapter.receipts and not runner.active
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_suspended_protection_can_use_updated_confirmed_position(tmp_path):
    runner, streams = runtime(tmp_path)
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def protection(*args):
        entered.set()
        await release.wait()
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))
        completed.set()

    runner.strategy.run = protection
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        streams[-1].emit(candle())
        await entered.wait()
        runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 0.5, PositionSide.BUY, leverage=10)])
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'P': [{'s': SYMBOL, 'pa': '0.5'}]}})
        await eventually(lambda: runner.positions[SYMBOL].find_first() is not None)
        release.set()
        await eventually(completed.is_set)
        assert len(runner.adapter.receipts) == 1 and not runner.failed
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
async def test_queued_account_event_cannot_be_overtaken_by_new_entry(tmp_path):
    runner, streams = runtime(tmp_path)
    finished = asyncio.Event()

    async def entry(*args):
        try:
            assert not await runner.gateway.submit_order(INTENT)
        except OrderOutcomeUnknown:
            pass  # The account queue may still be waiting to run.
        finally:
            finished.set()

    runner.strategy.run = entry
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        runner.adapter.state.balance = Balance(5)
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'a': {'B': [{'a': 'USDT', 'wb': '5'}]}})
        streams[-1].emit(candle())
        await eventually(finished.is_set)
        await eventually(lambda: runner.balance.available == 5)
        assert not runner.adapter.receipts and not runner.failed
    finally:
        runner.close()
        await task
