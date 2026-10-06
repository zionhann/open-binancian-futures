"""Complete user events update only their entities; REST fills missing information."""

import asyncio
import copy

import pytest

from open_binancian_futures.exchange_adapter import OrderReceipt
from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.models import Balance, OrderIntent, Position
from open_binancian_futures.types import OrderType, PositionSide
from test_live_runtime import Adapter, SYMBOL, eventually, runtime


class EventAdapter(Adapter):
    def refresh_event(self, state, symbols, config, *, balance=False, positions=False, orders=False):
        self.calls.append(('event_read', tuple(symbols), balance, positions, orders))
        fresh = copy.deepcopy(state)
        if balance:
            fresh.balance = copy.deepcopy(self.state.balance)
        for symbol in symbols:
            if positions:
                fresh.positions[symbol] = copy.deepcopy(self.state.positions[symbol])
            if orders:
                fresh.orders[symbol] = copy.deepcopy(self.state.orders[symbol])
        return fresh

    def submit(self, intent, identifier):
        receipt = super().submit(intent, identifier)
        quantity = intent.quantity or 0
        raw = dict(orderId=receipt.order_id, clientOrderId=identifier, symbol=SYMBOL,
                   status='NEW', origQty=str(quantity), executedQty='0', price=str(intent.price or 0),
                   type=intent.order_type.value, side=intent.side.value)
        receipt = OrderReceipt(receipt.order_id, identifier, SYMBOL, 'NEW', 0., None, raw)
        self.receipts[identifier] = receipt
        if not (intent.reduce_only or intent.close_position):
            self.state.balance = Balance(self.state.balance.available - (intent.price or 100) * quantity / self.state.leverage[SYMBOL])
        return receipt


def account(version=100, amount='1', price='100', symbol=SYMBOL, balance=None):
    return {'e': 'ACCOUNT_UPDATE', 'E': version, 'T': version, 'a': {
        'm': 'ORDER', 'B': balance or [], 'P': [
            {'s': symbol, 'pa': amount, 'ep': price, 'bep': price, 'ps': 'BOTH', 'mt': 'cross', 'iw': '0', 'up': '0', 'cr': '0'},
        ],
    }}


def order(identifier=7, version=100, status='NEW', filled='0', client='external', symbol=SYMBOL):
    return {'e': 'ORDER_TRADE_UPDATE', 'E': version, 'T': version, 'o': {
        's': symbol, 'i': identifier, 'c': client, 'X': status, 'x': 'TRADE' if float(filled) else 'NEW',
        'o': 'LIMIT', 'ot': 'LIMIT', 'S': 'BUY', 'q': '2', 'z': filled, 'p': '100', 'ap': '100',
        'R': False, 'cp': False, 'T': version, 't': version if float(filled) else -1, 'rp': '1' if float(filled) else '0',
        'ps': 'BOTH', 'f': 'GTC', 'gtd': 0,
    }}


async def start(tmp_path):
    adapter = EventAdapter()
    runner, streams = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=10))
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    adapter.calls.clear()
    return runner, streams, task


@pytest.mark.asyncio
async def test_account_partial_updates_keep_other_state_and_do_not_use_wallet_as_available(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(account(balance=[{'a': 'USDT', 'wb': '95', 'cw': '85', 'bc': '-5'}]))
        await eventually(lambda: bool(runner.positions[SYMBOL]))
        assert runner.positions[SYMBOL].find_first().amount == 1
        assert runner.positions[SYMBOL].entry_count == 1
        assert runner.balance.available == 100
        assert not runner.adapter.calls
        streams[-1].emit(account(101, amount='0', price='0'))
        await eventually(lambda: not runner.positions[SYMBOL])
        assert runner.positions[SYMBOL].entry_count == 0
        assert runner.balance.available == 100 and not runner.adapter.calls
        # Several balance/funding events become one free-balance read before entry.
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'E': 102, 'T': 102, 'a': {'m': 'FUNDING_FEE', 'B': [{'a': 'USDT', 'wb': '94', 'cw': '84', 'bc': '-1'}], 'P': []}})
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        runner.adapter.state.balance = Balance(5)
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert runner.balance.available == 5
        assert runner.adapter.calls == [('event_read', (), True, False, False)]
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_interleaved_duplicates_and_reversed_status_do_not_repeat_reads_or_hooks(tmp_path):
    runner, streams, task = await start(tmp_path)
    hooks = []
    runner.strategy.on_new_order = lambda event: hooks.append(event.order_id)
    try:
        a, b = order(7), order(8)
        for event in (a, b, a):
            streams[-1].emit(event)
        await eventually(lambda: hooks == [7, 8])
        assert len(list(runner.orders[SYMBOL])) == 2
        assert not runner.adapter.calls
        # The account event already establishes the fill's position.
        streams[-1].emit(account(110, amount='.5'))
        streams[-1].emit(order(7, 110, 'PARTIALLY_FILLED', '.5'))
        await eventually(lambda: runner.strategy.pnl == 1)
        assert next(o for o in runner.orders[SYMBOL] if o.order_id == 7).quantity == 1.5
        streams[-1].emit(order(7, 109, 'NEW'))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert next(o for o in runner.orders[SYMBOL] if o.order_id == 7).quantity == 1.5
        assert runner.strategy.pnl == 1 and not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_known_receipt_and_event_resolve_journal_without_order_queries(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        record = runner.journal.pending()[0]
        assert record.state == 'accepted'
        assert not runner.adapter.calls
        assert runner.balance.available == 90 and runner.balance.reserved_margin == 10
        streams[-1].emit(account(110, amount='1'))
        filled = order(1, 110, 'FILLED', '1', record.client_order_id)
        filled['o']['q'] = '1'
        streams[-1].emit(filled)
        await eventually(lambda: not runner.journal.pending())
        assert not list(runner.orders[SYMBOL])
        assert runner.balance.available == 90 and runner.balance.reserved_margin == 0
        assert not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_leverage_config_is_direct_and_multi_asset_mode_blocks_entries(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(account())
        await eventually(lambda: bool(runner.positions[SYMBOL]))
        streams[-1].emit({'e': 'ACCOUNT_CONFIG_UPDATE', 'E': 101, 'T': 101, 'ac': {'s': SYMBOL, 'l': 7}})
        await eventually(lambda: runner.gateway.effective_leverage(SYMBOL) == 7)
        assert runner.positions[SYMBOL].find_first().leverage == 7 and not runner.adapter.calls
        streams[-1].emit({'e': 'ACCOUNT_CONFIG_UPDATE', 'E': 102, 'T': 102, 'ai': {'j': True}})
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert not runner.adapter.receipts and not runner.failed
        assert not any(isinstance(m, tuple) and m[0] == 'leverage' for m in runner.adapter.mutations)
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('account_first', [True, False])
async def test_fill_callback_observes_position_and_protects_without_full_reads(tmp_path, account_first):
    runner, streams, task = await start(tmp_path)
    positions = []

    async def protect(event):
        positions.append(runner.positions[SYMBOL].find_first().amount)
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))

    runner.strategy.on_filled_order = protect
    try:
        position, filled = account(110, amount='2'), order(7, 110, 'FILLED', '2')
        # Different event times can describe the same account transaction.
        position['E'] = 111 if account_first else 112
        filled['E'] = 112 if account_first else 111
        for event in ((position, filled) if account_first else (filled, position)):
            streams[-1].emit(event)
        await eventually(lambda: len(runner.adapter.receipts) == 1)
        assert positions == [2]
        assert runner.adapter.calls == []
        sent = next(m[1] for m in runner.adapter.mutations if isinstance(m, tuple) and m[0] == 'submit')
        assert sent.close_position
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_missing_fill_position_reads_only_affected_symbol(tmp_path):
    runner, streams, task = await start(tmp_path)
    observed = []
    runner.strategy.on_filled_order = lambda event: observed.append(runner.positions[SYMBOL].find_first().amount)
    runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 2, PositionSide.BUY, 10)])
    try:
        streams[-1].emit(order(7, 110, 'FILLED', '2'))
        await eventually(lambda: observed == [2])
        assert runner.adapter.calls == [('event_read', (SYMBOL,), False, True, False)]
    finally:
        runner.close()
        await task


def algo(version, status, actual=0):
    return {'e': 'ALGO_UPDATE', 'E': version, 'T': version, 'o': {
        's': SYMBOL, 'aid': 7, 'caid': 'external', 'X': status, 'o': 'STOP_MARKET', 'S': 'SELL',
        'q': '0', 'p': '0', 'tp': '90', 'R': False, 'cp': True, 'ps': 'BOTH', 'ai': str(actual) if actual else '',
    }}


@pytest.mark.asyncio
async def test_algo_trigger_is_linked_to_actual_order_and_is_not_a_fill(tmp_path):
    runner, streams, task = await start(tmp_path)
    triggered, filled = [], []
    runner.strategy.on_triggered_algo = lambda event: triggered.append(event.order_id)
    runner.strategy.on_filled_order = lambda event: filled.append(event.order_id)
    try:
        streams[-1].emit(algo(100, 'NEW'))
        streams[-1].emit(algo(101, 'TRIGGERED', 9))
        await eventually(lambda: triggered == [7])
        assert filled == [] and runner.gateway.algo_orders[(SYMBOL, 7)] == 9
        streams[-1].emit(algo(102, 'FINISHED', 9))
        await eventually(lambda: not list(runner.orders[SYMBOL]))
        assert filled == [] and runner.adapter.calls == []
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('event', ['MARGIN_CALL', 'CONDITIONAL_ORDER_TRIGGER_REJECT'])
async def test_risk_events_notify_and_stop_entries_while_allowing_protection(tmp_path, event, caplog):
    runner, streams, task = await start(tmp_path)
    runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 1, PositionSide.BUY, 10)])
    try:
        data = {'e': event, 'E': 120, 'T': 120}
        data.update({'p': [{'s': SYMBOL, 'pa': '1', 'ps': 'BOTH', 'mt': 'cross', 'mp': '90', 'iw': '0', 'up': '-10', 'mm': '1'}]} if event == 'MARGIN_CALL' else {'or': {'s': SYMBOL, 'i': 7, 'r': 'rejected'}})
        streams[-1].emit(data)
        await eventually(lambda: bool(runner.positions[SYMBOL]))
        assert event in caplog.text
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))
        assert 'snapshot' not in runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_balance_refresh_does_not_deduct_accepted_reservation_twice(tmp_path):
    runner, streams, task = await start(tmp_path)
    intent = OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1)
    try:
        assert await runner.gateway.submit_order(intent)
        streams[-1].emit({'e': 'ACCOUNT_UPDATE', 'E': 110, 'T': 110, 'a': {'B': [{'a': 'USDT', 'wb': '100', 'cw': '100'}], 'P': []}})
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert await runner.gateway.submit_order(intent)
        assert runner.balance.available == 80 and runner.balance.reserved_margin == 10
        assert runner.adapter.calls == [('event_read', (), True, False, False)]
        assert len(runner.journal.pending()) == 2
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_known_cancel_response_resolves_without_lookup_and_does_not_release_stale_money(tmp_path):
    runner, _, task = await start(tmp_path)
    try:
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        identifier = runner.journal.pending()[0].client_order_id

        def cancel(symbol, client_order_id, **kwargs):
            runner.adapter.state.orders[symbol].clear()
            runner.adapter.state.balance = Balance(100)
            return OrderReceipt(1, client_order_id, symbol, 'CANCELED', 0., None, {})

        runner.adapter.cancel = cancel
        await runner.gateway.cancel_order(identifier)
        assert not runner.journal.pending() and not list(runner.orders[SYMBOL])
        assert runner.balance.available == 90 and runner.balance.reserved_margin == 0
        assert not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_entity_times_are_independent_and_old_position_updates_do_not_regress(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit({'e': 'ACCOUNT_CONFIG_UPDATE', 'E': 200, 'T': 200, 'ac': {'s': SYMBOL, 'l': 7}})
        streams[-1].emit(account(100, amount='2'))
        await eventually(lambda: bool(runner.positions[SYMBOL]))
        assert runner.positions[SYMBOL].find_first().leverage == 7
        streams[-1].emit(account(99, amount='1'))
        streams[-1].emit({'e': 'ACCOUNT_CONFIG_UPDATE', 'E': 199, 'T': 199, 'ac': {'s': SYMBOL, 'l': 5}})
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert runner.positions[SYMBOL].find_first().amount == 2
        assert runner.gateway.effective_leverage(SYMBOL) == 7 and not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_existing_multi_assets_mode_blocks_entries_without_changing_mode(tmp_path):
    adapter = EventAdapter()
    adapter.multi_assets_mode = lambda: True
    runner, _ = runtime(tmp_path, adapter, config=ExecutionConfig(leverage=10))
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert not adapter.receipts and not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_rest_fence_prevents_queued_old_event_from_overwriting_newer_snapshot(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 3, PositionSide.BUY, 10)])
        streams[-1].emit(account(100, amount='1'))
        # Reconcile acquires the gateway before the queued state event can run.
        await runner.gateway.reconcile_async()
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert runner.positions[SYMBOL].find_first().amount == 3
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_untracked_orders_and_funding_share_one_free_balance_read(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(order(symbol='ETHUSDT'))
        streams[-1].emit(account(110, symbol='ETHUSDT', balance=[{'a': 'USDT', 'wb': '100', 'cw': '100'}]))
        streams[-1].emit({'e': 'TRADE_LITE', 'E': 111, 's': SYMBOL, 't': 12})
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert not runner.adapter.calls and 'ETHUSDT' not in runner.positions
        runner.adapter.state.balance = Balance(5)
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert runner.adapter.calls == [('event_read', (), True, False, False)]
        assert runner.strategy.pnl == 0
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_terminal_order_cannot_be_resurrected_by_a_later_new_status(tmp_path):
    runner, streams, task = await start(tmp_path)
    hooks = []
    runner.strategy.on_new_order = lambda event: hooks.append(event.order_id)
    try:
        streams[-1].emit(order(7, 100))
        streams[-1].emit(order(7, 110, 'CANCELED'))
        streams[-1].emit(order(7, 120))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        await eventually(lambda: hooks == [7])
        assert not list(runner.orders[SYMBOL]) and runner.adapter.calls == []
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_order_rest_fence_rejects_equal_time_event_queued_before_snapshot(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(order(7, 100))
        await runner.gateway.reconcile_async()
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert not list(runner.orders[SYMBOL])
        # A later distinct partial fill with the same timestamp remains usable.
        streams[-1].emit(order(8, 110))
        streams[-1].emit(order(8, 110, 'PARTIALLY_FILLED', '.5'))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert next(o for o in runner.orders[SYMBOL] if o.order_id == 8).quantity == 1.5
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_delayed_new_event_cannot_undo_confirmed_cancel_receipt(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        identifier = runner.journal.pending()[0].client_order_id
        runner.adapter.cancel = lambda symbol, client, **kwargs: OrderReceipt(1, client, symbol, 'CANCELED', 0., None, {})
        await runner.gateway.cancel_order(identifier)
        delayed = order(1, 110, client=identifier)
        delayed['o']['q'] = '1'
        streams[-1].emit(delayed)
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert not list(runner.orders[SYMBOL]) and not runner.journal.pending()
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_nonfinite_trade_profit_recovers_without_poisoning_strategy_pnl(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        malformed = order(7, 110, 'PARTIALLY_FILLED', '1')
        malformed['o']['rp'] = 'nan'
        streams[-1].emit(malformed)
        await eventually(lambda: len(streams) > 1)
        assert runner.strategy.pnl == 0 and not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('q', 'nan'), ('i', True), ('o', 'UNSUPPORTED')])
async def test_malformed_order_is_validated_before_realized_profit(tmp_path, field, value):
    runner, streams, task = await start(tmp_path)
    try:
        malformed = order(7, 110, 'PARTIALLY_FILLED', '1')
        malformed['o'][field] = value
        streams[-1].emit(malformed)
        await eventually(lambda: len(streams) > 1)
        assert runner.strategy.pnl == 0 and not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_single_asset_event_does_not_clear_an_unsupported_position_mode_hold(tmp_path):
    runner, _, task = await start(tmp_path)
    try:
        malformed = account()
        malformed['a']['P'][0]['ps'] = 'LONG'
        with pytest.raises(ValueError, match='Hedge'):
            await runner.gateway.apply_event(malformed)
        await runner.gateway.apply_event({'e': 'ACCOUNT_CONFIG_UPDATE', 'E': 110, 'T': 110, 'ai': {'j': False}})
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert not runner.adapter.receipts
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_rejected_protection_receipt_holds_new_risk_and_notifies(tmp_path, caplog):
    runner, _, task = await start(tmp_path)
    try:
        await runner.gateway.apply_event(account())
        original = runner.adapter.submit

        def reject(intent, identifier):
            return OrderReceipt(7, identifier, SYMBOL, 'REJECTED', 0., None, {})

        runner.adapter.submit = reject
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))
        runner.adapter.submit = original
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert 'Protection order rejected' in caplog.text and not runner.adapter.receipts
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_partial_lookup_without_execution_details_keeps_fresh_remaining_quantity(tmp_path):
    runner, _, task = await start(tmp_path)
    try:
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 2))
        identifier = runner.journal.pending()[0].client_order_id
        runner.adapter.state.orders[SYMBOL].orders[0].quantity = 1.5
        runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, .5, PositionSide.BUY, 10)])
        runner.adapter.query = lambda symbol, client, **kwargs: OrderReceipt(1, client, symbol, 'PARTIALLY_FILLED', None, None, {})
        await runner.gateway.reconcile_event({'e': 'ORDER_TRADE_UPDATE', 'T': 110, 'o': {'s': SYMBOL, 'i': 1, 'c': identifier, 'X': 'PARTIALLY_FILLED'}})
        assert next(iter(runner.orders[SYMBOL])).quantity == 1.5
        assert runner.positions[SYMBOL].find_first().amount == .5
        assert runner.balance.available == 80 and runner.balance.reserved_margin == 0
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_algo_and_actual_order_can_share_id_and_actual_fill_finishes_parent(tmp_path):
    runner, streams, task = await start(tmp_path)
    hooks = []
    runner.strategy.on_new_order = lambda event: hooks.append(event.source.value)
    try:
        streams[-1].emit(algo(100, 'NEW'))
        streams[-1].emit(algo(101, 'TRIGGERED', 7))
        child = order(7, 102)
        child['o'].update(o='MARKET', ot='STOP_MARKET', R=True)
        streams[-1].emit(child)
        await eventually(lambda: hooks == ['ALGO_UPDATE', 'ORDER_TRADE_UPDATE'])
        assert len(list(runner.orders[SYMBOL])) == 2
        streams[-1].emit(account(110, amount='0', price='0'))
        child = order(7, 110, 'FILLED', '2')
        child['o'].update(o='MARKET', ot='STOP_MARKET', R=True)
        streams[-1].emit(child)
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert not list(runner.orders[SYMBOL]) and not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_actual_child_inheriting_algo_client_id_resolves_correct_namespace(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        await runner.gateway.apply_event(account(100, amount='2'))
        assert await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))
        identifier = runner.journal.pending()[0].client_order_id
        triggered = algo(101, 'TRIGGERED', 1)
        triggered['o'].update(aid=1, caid=identifier)
        streams[-1].emit(triggered)
        streams[-1].emit(account(110, amount='0', price='0'))
        filled = order(1, 110, 'FILLED', '2', identifier)
        filled['o'].update(o='MARKET', ot='STOP_MARKET', S='SELL', R=True)
        streams[-1].emit(filled)
        await eventually(lambda: not runner.journal.pending())
        assert not list(runner.orders[SYMBOL]) and not runner.failed
        assert runner.adapter.calls == []
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_uncertain_placement_and_cancel_keep_original_id_and_reservations(tmp_path):
    from open_binancian_futures.exchange_adapter import OrderOutcomeUnknown

    runner, _, task = await start(tmp_path)
    original = runner.adapter.submit

    def lost_response(*args):
        original(*args)
        raise TimeoutError('response lost')

    try:
        runner.adapter.submit = lost_response
        intent = OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1)
        with pytest.raises(OrderOutcomeUnknown):
            await runner.gateway.submit_order(intent)
        record = runner.journal.pending()[0]
        identifier = record.client_order_id
        assert record.state == 'unknown' and runner.balance.reserved_margin == 10
        runner.adapter.query_unknown = True
        event = {'e': 'ORDER_TRADE_UPDATE', 'T': 110, 'o': {'s': SYMBOL, 'i': 1, 'c': identifier}}
        await runner.gateway.reconcile_event(event)
        assert runner.balance.available == 80 and runner.balance.reserved_margin == 10
        with pytest.raises(OrderOutcomeUnknown):
            await runner.gateway.submit_order(intent)
        runner.adapter.query_unknown = False
        await runner.gateway.reconcile_event(event)
        assert runner.journal.pending()[0].client_order_id == identifier
        assert runner.journal.pending()[0].state == 'accepted'
        assert runner.balance.available == 90 and runner.balance.reserved_margin == 0
        with pytest.raises(OrderOutcomeUnknown):
            await runner.gateway.cancel_order(identifier)
        await runner.gateway.reconcile_event(event)
        assert runner.journal.pending()[0].state == 'cancel_unknown'
        assert runner.balance.available == 80 and runner.balance.reserved_margin == 10
        runner.adapter.receipts[identifier] = OrderReceipt(1, identifier, SYMBOL, 'CANCELED', 0., None, {})
        runner.adapter.state.orders[SYMBOL].clear()
        runner.adapter.state.balance = Balance(100)
        await runner.gateway.reconcile_event(event)
        assert not runner.journal.pending() and runner.balance.available == 100
        assert runner.balance.reserved_margin == 0 and len(runner.adapter.receipts) == 1
        assert len([m for m in runner.adapter.mutations if isinstance(m, tuple) and m[0] == 'submit']) == 1
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_actual_child_terminal_before_link_finishes_late_algo_parent(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(order(9, 110, 'FILLED', '2'))
        streams[-1].emit(algo(111, 'TRIGGERED', 9))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert not list(runner.orders[SYMBOL])
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_distinct_equal_time_positions_apply_and_ambiguous_replay_reads_only_symbol(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(account(100, amount='1'))
        streams[-1].emit(account(100, amount='2'))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert runner.positions[SYMBOL].find_first().amount == 2 and not runner.adapter.calls
        runner.adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL, 100, 2, PositionSide.BUY, 10)])
        streams[-1].emit(account(100, amount='1'))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        assert runner.positions[SYMBOL].find_first().amount == 2
        assert runner.adapter.calls == [('event_read', (SYMBOL,), False, True, False)]
    finally:
        runner.close()
        await task
