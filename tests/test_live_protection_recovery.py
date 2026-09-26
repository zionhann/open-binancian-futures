"""Protection during uncertain entry outcomes uses fresh exchange state."""

from dataclasses import replace

import pytest

from test_live_runtime import Adapter, INTENT, SYMBOL, gateway
from open_binancian_futures.exchange_adapter import OrderOutcomeUnknown, OrderRejected
from open_binancian_futures.models import OrderIntent, Position
from open_binancian_futures.types import OrderType, PositionSide

STOP = OrderIntent(
    SYMBOL, PositionSide.SELL, OrderType.STOP_MARKET, price=90, close_position=True
)
REDUCE = OrderIntent(
    SYMBOL, PositionSide.SELL, OrderType.MARKET, quantity=1, reduce_only=True
)


def uncertain_entry(tmp_path):
    adapter = Adapter()
    managed, journal = gateway(tmp_path, adapter)
    managed.reference_price = lambda symbol: 100
    identifier = journal.prepare(INTENT, 10)
    journal.update(identifier, "unknown")
    adapter.state.positions[SYMBOL].update_positions(
        [Position(SYMBOL, 100, 1, PositionSide.BUY, 10)]
    )
    managed.reconcile()
    return adapter, managed, journal


def sends(adapter):
    return [m for m in adapter.mutations if isinstance(m, tuple) and m[0] == "submit"]


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [STOP, REDUCE])
async def test_unknown_entry_allows_confirmed_protection(tmp_path, intent):
    adapter, managed, journal = uncertain_entry(tmp_path)
    try:
        assert await managed.submit_order(intent)
        assert len(sends(adapter)) == 1
        assert SYMBOL in managed.blocked
        assert managed.snapshot().balance.reserved_margin == 10
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(INTENT)
        assert len(sends(adapter)) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [STOP, REDUCE])
async def test_uncertain_protection_blocks_repeated_send_and_survives_restart(
    tmp_path, intent
):
    adapter, managed, journal = uncertain_entry(tmp_path)
    adapter.submit_error = TimeoutError("accepted but response lost")
    try:
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(intent)
        assert len(sends(adapter)) == 1
        adapter.query_unknown = True
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(intent)
        journal.close()
        managed, journal = gateway(tmp_path, adapter)
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(intent)
        assert len(sends(adapter)) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        replace(REDUCE, quantity=2),
        replace(REDUCE, quantity=None),
        replace(REDUCE, side=PositionSide.BUY),
        replace(STOP, side=PositionSide.BUY),
    ],
)
async def test_unsafe_protection_does_not_send(tmp_path, intent):
    adapter, managed, journal = uncertain_entry(tmp_path)
    try:
        with pytest.raises((ValueError, OrderOutcomeUnknown)):
            await managed.submit_order(intent)
        assert not sends(adapter)
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_position_must_still_exist_in_fresh_snapshot(tmp_path):
    adapter, managed, journal = uncertain_entry(tmp_path)
    adapter.state.positions[SYMBOL].clear()
    try:
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(STOP)
        assert not sends(adapter)
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_snapshot_failure_pauses_gateway_without_sending(tmp_path):
    adapter, managed, journal = uncertain_entry(tmp_path)
    recovered = []
    managed.request_recovery = lambda: recovered.append(True)

    def fail():
        raise ConnectionError("snapshot unavailable")

    adapter.snapshot_hook = fail
    try:
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(STOP)
        assert not sends(adapter)
        assert not managed.active and recovered
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_rejected_protection_preserves_unknown_entry_hold(tmp_path):
    adapter, managed, journal = uncertain_entry(tmp_path)
    adapter.submit_error = OrderRejected("rejected")
    try:
        assert not await managed.submit_order(STOP)
        assert SYMBOL in managed.blocked
        assert managed.snapshot().balance.reserved_margin == 10
        with pytest.raises(OrderOutcomeUnknown):
            await managed.submit_order(INTENT)
        assert len(sends(adapter)) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["prepared", "unknown", "accepted", "cancel_unknown"])
async def test_pending_protection_blocks_another_kind(tmp_path, state):
    adapter, managed, journal = uncertain_entry(tmp_path)
    identifier = journal.prepare(STOP, 0)
    journal.update(identifier, state)
    if state == "accepted":
        from open_binancian_futures.exchange_adapter import OrderReceipt

        adapter.receipts[identifier] = OrderReceipt(
            2, identifier, SYMBOL, "NEW", 0, None, {}
        )
    try:
        with pytest.raises(OrderOutcomeUnknown, match="Pending protection"):
            await managed.submit_order(REDUCE)
        assert not sends(adapter)
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_short_position_allows_buy_protection(tmp_path):
    adapter, managed, journal = uncertain_entry(tmp_path)
    adapter.state.positions[SYMBOL].update_positions(
        [Position(SYMBOL, 100, 1, PositionSide.SELL, 10)]
    )
    try:
        assert await managed.submit_order(
            replace(STOP, side=PositionSide.BUY, price=110)
        )
        assert len(sends(adapter)) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_concurrent_protection_sends_only_once(tmp_path):
    import asyncio

    adapter, managed, journal = uncertain_entry(tmp_path)
    try:
        results = await asyncio.gather(
            managed.submit_order(STOP),
            managed.submit_order(STOP),
            return_exceptions=True,
        )
        assert results[0] is True
        assert isinstance(results[1], OrderOutcomeUnknown)
        assert len(sends(adapter)) == 1
    finally:
        journal.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_protection", [False, True])
async def test_resolved_entry_restores_normal_protection_rules(
    tmp_path, existing_protection
):
    from open_binancian_futures.exchange_adapter import OrderReceipt

    adapter, managed, journal = uncertain_entry(tmp_path)
    identifier = journal.pending()[0].client_order_id
    adapter.receipts[identifier] = OrderReceipt(
        1, identifier, SYMBOL, "CANCELED", 0, None, {}
    )
    if existing_protection:
        protection_id = journal.prepare(STOP, 0)
        journal.update(protection_id, "accepted")
        adapter.receipts[protection_id] = OrderReceipt(
            2, protection_id, SYMBOL, "NEW", 0, None, {}
        )
    try:
        # Automatic sizing is permitted by the normal gateway, but not by the
        # exception for entries which remain uncertain after reconciliation.
        assert await managed.submit_order(replace(REDUCE, quantity=None))
        assert SYMBOL not in managed.blocked
        assert len(sends(adapter)) == 1
    finally:
        journal.close()
