"""Merge review regressions for the live event/account contract."""

import pytest

from open_binancian_futures.exchange_adapter import OrderReceipt
from open_binancian_futures.models import Balance, OrderIntent
from open_binancian_futures.types import OrderType, PositionSide
from test_live_account_events import account, algo, order, start
from test_live_runtime import SYMBOL, eventually


@pytest.mark.asyncio
async def test_regular_liquidation_and_algo_share_id_without_stale_book_entries(tmp_path):
    runner, streams, task = await start(tmp_path)
    hooks = []
    runner.strategy.on_new_order = lambda event: hooks.append(event.source.value)
    try:
        parent = algo(100, 'NEW')
        parent['o']['aid'] = 91
        liquidation = order(91, 101)
        liquidation['o'].update(o='LIQUIDATION', ot='LIQUIDATION')
        streams[-1].emit(parent)
        streams[-1].emit(liquidation)
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        await eventually(lambda: hooks == ['ALGO_UPDATE', 'ORDER_TRADE_UPDATE'])
        assert len(list(runner.orders[SYMBOL])) == 2
        streams[-1].emit(account(110, amount='0', price='0'))
        liquidation = order(91, 110, 'FILLED', '2')
        liquidation['o'].update(o='LIQUIDATION', ot='LIQUIDATION')
        streams[-1].emit(liquidation)
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        remaining = list(runner.orders[SYMBOL])
        assert len(remaining) == 1 and remaining[0].type == OrderType.STOP_MARKET
        assert not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_conditional_limit_keeps_execution_and_trigger_prices(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        event = algo(100, 'NEW')
        event['o'].update(o='STOP', p='95', tp='90', q='2', cp=False)
        streams[-1].emit(event)
        await eventually(lambda: bool(list(runner.orders[SYMBOL])))
        actual = next(iter(runner.orders[SYMBOL]))
        assert actual.price == 95 and actual.trigger_price == 90
        assert not runner.adapter.calls
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('ps', 'LONG'), ('sp', 'nan'), ('outer_T', 'bad'), ('outer_T', None)])
async def test_invalid_event_cannot_mutate_profit_or_trade_dedup(tmp_path, field, value):
    runner, streams, task = await start(tmp_path)
    try:
        event = order(7, 110, 'PARTIALLY_FILLED', '1')
        if field == 'outer_T':
            event['T'] = value
        else:
            event['o'][field] = value
        streams[-1].emit(event)
        await eventually(lambda: len(streams) > 1)
        assert runner.strategy.pnl == 0
        assert not runner._trade_ids and not runner.failed
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize('read', ['full', 'position', 'balance'])
async def test_risk_hold_clears_only_after_authoritative_flat_position_read(tmp_path, read):
    runner, _, task = await start(tmp_path)
    try:
        await runner.gateway.apply_event(account())
        original = runner.adapter.submit
        runner.adapter.submit = lambda intent, identifier: OrderReceipt(7, identifier, SYMBOL, 'REJECTED', 0., None, {})
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 90, close_position=True))
        runner.adapter.submit = original
        runner.adapter.state.positions[SYMBOL].clear()
        if read == 'full':
            await runner.gateway.reconcile_async()
        else:
            async with runner.gateway._mutex:
                await runner.gateway._refresh_event([SYMBOL] if read == 'position' else [],
                                                     positions=read == 'position', balance=read == 'balance',
                                                     reason='review_probe')
        result = await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert result == (read != 'balance')
    finally:
        runner.close()
        await task


@pytest.mark.asyncio
async def test_untracked_position_only_event_invalidates_available_balance(tmp_path):
    runner, streams, task = await start(tmp_path)
    try:
        streams[-1].emit(account(100, symbol='ETHUSDT', amount='1'))
        await eventually(lambda: runner.user_queue.empty() and not runner._user_busy)
        runner.adapter.state.balance = Balance(5)
        assert not await runner.gateway.submit_order(OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1))
        assert runner.balance.available == 5
        assert runner.adapter.calls == [('event_read', (), True, False, False)]
        assert 'ETHUSDT' not in runner.positions
    finally:
        runner.close()
        await task


def test_rest_orders_preserve_source_and_execution_trigger_price_parity():
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import AllOrdersResponse, CurrentAllAlgoOpenOrdersResponse
    from open_binancian_futures.exchange import _create_order_from_algo, _create_order_from_regular
    regular = _create_order_from_regular(AllOrdersResponse.model_validate({
        'orderId': 91, 'origType': 'LIQUIDATION', 'side': 'SELL', 'price': '95', 'origQty': '2',
    }), SYMBOL)
    conditional = _create_order_from_algo(CurrentAllAlgoOpenOrdersResponse.model_validate({
        'algoId': 91, 'orderType': 'STOP', 'side': 'SELL', 'price': '95', 'triggerPrice': '90', 'quantity': '2',
    }), SYMBOL)
    assert not regular.is_algo and regular.price == 95
    assert conditional.is_algo and conditional.price == 95 and conditional.trigger_price == 90
