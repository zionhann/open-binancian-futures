import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_live_runtime import SYMBOL, Adapter, Streams, candle, eventually, gateway

from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.live import LiveTrading
from open_binancian_futures.models import (
    Balance,
    Filter,
    Indicator,
    OrderBook,
    OrderIntent,
    PositionBook,
)
from open_binancian_futures.types import OrderType, PositionSide

CONFIG = ExecutionConfig(leverage=5, position_size=0.2)
ENTRY = OrderIntent(SYMBOL, PositionSide.BUY, OrderType.MARKET, price=2695.78)


def small_account(balance=19.58):
    adapter = Adapter()
    adapter.state.balance = Balance(0)
    adapter.state.balance.increase_balance(balance)
    adapter.state.leverage[SYMBOL] = 5
    adapter.state.exchange_info.filters[SYMBOL] = Filter(0.01, 0.001, 20)
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("balance,quantity", [(19.58, 0.007), (1, 0.0), (0, 0.0)])
async def test_small_auto_entry_skips_before_side_effects(tmp_path, balance, quantity):
    adapter = small_account(balance)
    managed, journal = gateway(tmp_path, adapter, CONFIG)
    reports = []
    managed.report = reports.append
    try:
        assert await managed.submit_order(ENTRY) is False
        assert adapter.mutations == []
        assert journal.connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        assert managed.snapshot().balance.reserved_margin == 0
        assert managed.snapshot().balance.available == balance
        assert managed.active and not managed.failed and not managed.blocked
        assert reports == [
            f"Auto-sized entry skipped: {SYMBOL} quantity={quantity} "
            "reference_price=2695.78 min_notional=20"
        ]
        # Protection still submits after an entry skip, without an entry margin.
        assert await managed.submit_order(OrderIntent(
            SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, 2600,
            close_position=True,
        )) is True
        assert managed.snapshot().balance.reserved_margin == 0
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_exact_minimum_auto_entry_submits(tmp_path):
    adapter = small_account(20)
    managed, journal = gateway(tmp_path, adapter, CONFIG)
    try:
        assert await managed.submit_order(replace(ENTRY, price=2500)) is True
        submitted = adapter.mutations[0][1]
        assert submitted.quantity == 0.008
        assert submitted.quantity * submitted.price == 20
        assert len(journal.pending()) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [
    replace(ENTRY, quantity=0.007),
    replace(ENTRY, quantity=0),
    replace(ENTRY, price=-1),
    replace(ENTRY, side=PositionSide.SELL, reduce_only=True),
])
async def test_invalid_explicit_order_and_zero_auto_protection_still_raise(tmp_path, intent):
    adapter = small_account(1)
    managed, journal = gateway(tmp_path, adapter, CONFIG)
    try:
        with pytest.raises(ValueError):
            await managed.submit_order(intent)
        assert adapter.mutations == [] and not journal.pending()
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_skipped_entry_does_not_stop_other_symbols_or_next_bar(tmp_path):
    other = "ETHUSDT"
    adapter = small_account()
    adapter.state.exchange_info.filters[other] = Filter(0.01, 0.001, 20)
    adapter.state.leverage[other] = 5
    adapter.state.orders = OrderBook(symbols=[SYMBOL, other])
    adapter.state.positions = PositionBook(symbols=[SYMBOL, other])
    original_indicators = adapter.initial_indicators

    def indicators(*args):
        frame = original_indicators(*args)[SYMBOL]['1m']
        return Indicator({SYMBOL: {'1m': frame}, other: {'1m': frame.assign(Symbol=other)}})

    adapter.initial_indicators = indicators
    calls = []

    class Strategy:
        async def run(self, symbol, interval):
            result = await self.order_gateway.submit_order(replace(ENTRY, symbol=symbol))
            calls.append((symbol, interval, result))

    streams = Streams()
    runner = LiveTrading(
        adapter, streams_factory=lambda: streams, strategy=Strategy(),
        symbols=[SYMBOL, other], intervals=['1m'], config=CONFIG,
        journal_path=tmp_path/'orders.sqlite3',
        webhook=SimpleNamespace(send_message=lambda message: None),
    )
    task = asyncio.create_task(runner.run_async())
    try:
        await eventually(lambda: runner.active)
        for symbol, opened in [(SYMBOL, 60000), (other, 60000), (SYMBOL, 120000)]:
            event = candle(opened)
            event['s'] = event['k']['s'] = symbol
            streams.emit(event)
        await eventually(lambda: len(calls) == 3)
        assert calls == [(SYMBOL, '1m', False), (other, '1m', False), (SYMBOL, '1m', False)]
        assert runner.active and not runner.failed and not runner.gateway.failed
        assert not runner.gateway.blocked and not runner.journal.pending()
        assert runner.balance.reserved_margin == 0
        assert not any(isinstance(m, tuple) and m[0] == 'submit' for m in adapter.mutations)
    finally:
        runner.close()
        await task
