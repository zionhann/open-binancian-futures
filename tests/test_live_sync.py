"""REST call boundaries for event-driven live synchronization."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from test_live_runtime import Adapter, INTENT, SYMBOL, Streams, gateway, runtime
from open_binancian_futures.exchange_adapter import BinanceExchangeAdapter
from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.models import Balance


def test_account_refresh_preserves_orders_filters_and_leverage(monkeypatch):
    from open_binancian_futures import exchange

    state = Adapter().state
    sdk = SimpleNamespace(rest_api=Mock())
    adapter = BinanceExchangeAdapter(sdk)
    balance = Balance(42)
    positions = copy.deepcopy(state.positions)
    monkeypatch.setattr(exchange, 'init_balance', Mock(return_value=balance))
    monkeypatch.setattr(exchange, 'init_positions', Mock(return_value=positions))
    monkeypatch.setattr(exchange, 'init_orders', Mock(side_effect=AssertionError('orders queried')))
    monkeypatch.setattr(exchange, 'init_exchange_info', Mock(side_effect=AssertionError('filters queried')))
    adapter.leverage = Mock(side_effect=AssertionError('leverage queried'))
    refreshed = adapter.refresh_snapshot(state, [SYMBOL], ExecutionConfig(), account_only=True)
    assert refreshed.balance is balance and refreshed.positions[SYMBOL] is positions[SYMBOL]
    assert refreshed.orders is state.orders and refreshed.exchange_info is state.exchange_info
    assert refreshed.leverage == state.leverage


@pytest.mark.asyncio
@pytest.mark.parametrize('uncertain', [False, True])
async def test_watch_only_reconciles_uncertain_orders_every_15_seconds(tmp_path, uncertain):
    now = [0.0]
    runner, _ = runtime(tmp_path, clock=lambda: now[0])
    calls = []
    runner.streams = Streams()
    runner.active = True
    runner.gateway = SimpleNamespace(reconcile_async=AsyncMock(side_effect=lambda: calls.append(now[0])))
    runner.journal = SimpleNamespace(pending=lambda: [SimpleNamespace(state='unknown' if uncertain else 'accepted')])

    async def tick(delay):
        now[0] += delay
        runner._last_market[(SYMBOL, '1m')] = now[0]
        if now[0] >= 300:
            runner.stop_event.set()

    runner.sleep = tick
    await runner._watch()
    assert calls == (list(range(15, 301, 15)) if uncertain else [300])
    # Avoid closing the deliberately minimal test doubles.
    runner.gateway = runner.journal = runner.streams = None
    runner.close()


@pytest.mark.asyncio
async def test_stale_available_balance_is_refreshed_before_entry(tmp_path):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    try:
        managed.clock = lambda: 16.0
        managed._account_updated_at = 0.0
        adapter.state.balance = Balance(5)
        assert not await managed.submit_order(INTENT)
        assert not adapter.mutations and not journal.pending()
        assert managed.snapshot().balance.available == 5
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_duplicate_order_event_does_not_repeat_rest_or_hook(tmp_path):
    from test_live_runtime import eventually

    runner, streams = runtime(tmp_path)
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    event = {'e': 'ORDER_TRADE_UPDATE', 'T': 20, 'o': {'s': SYMBOL, 'i': 7, 't': 12, 'z': '1', 'rp': '3', 'X': 'FILLED'}}
    try:
        before = runner.adapter.calls.count('snapshot')
        streams[-1].emit(event)
        streams[-1].emit(event)
        await eventually(lambda: runner.user_queue.empty())
        assert runner.adapter.calls.count('snapshot') == before + 1
        assert runner.strategy.pnl == 3
    finally:
        runner.close()
        await task


@pytest.mark.parametrize('event,account_only', [('ACCOUNT_UPDATE', True), ('ORDER_TRADE_UPDATE', False), ('ALGO_UPDATE', False)])
def test_gateway_uses_scoped_refresh_for_known_state(tmp_path, event, account_only):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    try:
        fresh = copy.deepcopy(adapter.state)
        fresh.balance = Balance(42)
        adapter.refresh_snapshot = Mock(return_value=fresh)
        adapter.snapshot = Mock(side_effect=AssertionError('full snapshot queried'))
        managed.reconcile(event=event)
        assert managed.snapshot().balance.available == 42
        assert adapter.refresh_snapshot.call_args.kwargs == {'account_only': account_only}
    finally:
        journal.close()


@pytest.mark.parametrize('record_state', ['prepared', 'unknown', 'cancel_unknown', 'accepted'])
def test_uncertain_lookup_retains_full_snapshot_and_margin(tmp_path, record_state):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    try:
        identifier = journal.prepare(INTENT, 10)
        journal.update(identifier, record_state)
        adapter.query_unknown = True
        adapter.refresh_snapshot = Mock(side_effect=AssertionError('scoped snapshot used for uncertainty'))
        managed.reconcile(event='ACCOUNT_UPDATE')
        assert SYMBOL in managed.blocked
        assert managed.snapshot().balance.reserved_margin == 10
        assert managed.snapshot().balance.available == 90
        assert journal.pending()[0].state == ('cancel_unknown' if record_state == 'cancel_unknown' else 'unknown')
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_failed_balance_refresh_pauses_without_submitting(tmp_path):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    try:
        managed.clock = lambda: 16.0
        managed._account_updated_at = 0.0
        managed.request_recovery = Mock()
        adapter.snapshot = Mock(side_effect=ConnectionError('account unavailable'))
        from open_binancian_futures.exchange_adapter import OrderOutcomeUnknown
        with pytest.raises(OrderOutcomeUnknown, match='fresh available balance'):
            await managed.submit_order(INTENT)
        assert not managed.active and not adapter.mutations and not journal.pending()
        managed.request_recovery.assert_called_once()
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_configuration_event_refreshes_actual_leverage(tmp_path):
    from test_live_runtime import eventually

    runner, streams = runtime(tmp_path)
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    try:
        runner.adapter.state.leverage[SYMBOL] = 7
        streams[-1].emit({'e': 'ACCOUNT_CONFIG_UPDATE', 'T': 20, 'ac': {'s': SYMBOL, 'l': 7}})
        await eventually(lambda: runner.gateway.effective_leverage(SYMBOL) == 7)
        assert not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.parametrize('status,remaining', [('FILLED', 0), ('CANCELED', 0), ('EXPIRED', 0), ('PARTIALLY_FILLED', 0.5)])
def test_account_lookup_refreshes_changed_pending_orders(tmp_path, status, remaining):
    from open_binancian_futures.exchange_adapter import OrderReceipt

    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    try:
        identifier = journal.prepare(INTENT, 10)
        adapter.submit(INTENT, identifier)
        journal.update(identifier, 'accepted')
        managed.reconcile()
        assert next(iter(managed.snapshot().orders[SYMBOL])).quantity == 1
        adapter.receipts[identifier] = OrderReceipt(1, identifier, SYMBOL, status, 1 - remaining, None, {})
        if remaining:
            next(iter(adapter.state.orders[SYMBOL])).quantity = remaining
        else:
            adapter.state.orders[SYMBOL].clear()

        def refresh(state, symbols, config, *, account_only=False):
            fresh = copy.deepcopy(adapter.state)
            if account_only:
                fresh.orders = state.orders
            return fresh

        adapter.refresh_snapshot = refresh
        managed.reconcile(event='ACCOUNT_UPDATE')
        actual = list(managed.snapshot().orders[SYMBOL])
        assert ([order.quantity for order in actual] if remaining else actual) == ([remaining] if remaining else [])
        assert bool(journal.pending()) == bool(remaining)
    finally:
        journal.close()
