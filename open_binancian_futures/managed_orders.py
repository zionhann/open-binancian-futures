"""Single-loop managed order gateway: record once, send once, reconcile by ID."""

import asyncio
import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

from .exchange_adapter import (
    BinanceExchangeAdapter,
    ExchangeAdapter,
    ExchangeSnapshot,
    OrderOutcomeUnknown,
    OrderReceipt,
    OrderRejected,
    RestCalls,
)
from .execution import ExecutionConfig
from .live_journal import JournalOrder, OrderJournal
from .models import Order, OrderIntent, Position
from .types import OrderType, PositionSide

LOGGER = logging.getLogger(__name__)


class _EntryPending(RuntimeError):
    """Nothing was sent; release locks and let account processing finish."""


class _EntryCancelled(RuntimeError):
    """The not-yet-sent decision is no longer eligible."""


class ManagedOrderGateway:
    def __init__(
        self,
        adapter: ExchangeAdapter,
        journal: OrderJournal,
        symbols: Sequence[str],
        config: ExecutionConfig,
        report: Callable[[str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter, self.journal = adapter, journal
        self.symbols, self.config = tuple(symbols), config
        self.report = report
        self.clock = clock
        self._account_updated_at = float("-inf")
        self.active = False
        self.can_send: Callable[[], bool] = lambda: True
        self.can_enter: Callable[[], bool] = lambda: True
        self.request_recovery: Callable[[], None] = lambda: None
        self.failed = False
        self.blocked: set[str] = set()
        self.state: ExchangeSnapshot | None = None
        self.reference_price: Callable[[str], float] | None = None
        self.on_snapshot: Callable[[ExchangeSnapshot], None] = lambda state: None
        self.on_submission: Callable[[OrderIntent, OrderReceipt], None] = lambda intent, receipt: None
        self._confirmed_receipts: dict[str, OrderReceipt] = {}
        self._mutex = asyncio.Lock()
        self._refresh_waiters = 0
        self._submissions = 0
        self._submissions_idle = asyncio.Event()
        self._submissions_idle.set()
        self.rest = RestCalls()
        self.entry_changed = asyncio.Event()
        self.entry_abort_reason: Callable[[OrderIntent], str | None] = lambda intent: None
        self.entry_deadline: Callable[[], float | None] = lambda: None
        self.entry_context: Callable[[], str] = lambda: "decision=standalone"
        self.account_activity: Callable[[], int] = lambda: 0
        self.entry_counts = {"waits": 0, "cancelled": 0, "submitted": 0}
        self._leverage_initialized: set[str] = set()
        self._account_dirty = False
        self._positions_dirty: set[str] = set()
        self._entry_holds: set[str] = set()
        self._risk_symbols: set[str] = set()
        self._position_transactions: dict[str, int] = {}
        self._received_position_transactions: dict[str, int] = {}
        self._terminal_orders: set[tuple[str, str]] = set()
        self._entity_versions: dict[tuple[str, str], int] = {}
        self._received_versions: dict[tuple[str, str], int] = {}
        self._receive_sequence = 0
        self._received_sequences: dict[tuple[str, str], int] = {}
        self._snapshot_sequences: dict[tuple[str, str], int] = {}
        self._entity_updates: dict[tuple[str, str], tuple[int, set[tuple[Any, ...]], tuple[Any, ...]]] = {}
        self._order_ids: dict[tuple[str, bool, int], str] = {}
        self.algo_orders: dict[tuple[str, int], int] = {}
        self.wallet_balances: dict[str, tuple[float, float]] = {}

    def snapshot(self) -> ExchangeSnapshot:
        if self.state is None:
            raise RuntimeError("Account has not been synchronized")
        return self.state

    def effective_leverage(self, symbol: str) -> int:
        return self.snapshot().leverage[symbol]

    @staticmethod
    def is_algo(intent: OrderIntent) -> bool:
        return intent.order_type not in {OrderType.MARKET, OrderType.LIMIT}

    def _query_pending(
        self, records: list[JournalOrder]
    ) -> tuple[dict[str, OrderReceipt], set[str]]:
        outcomes: dict[str, OrderReceipt] = {}
        blocked = set()
        for record in records:
            try:
                receipt = self.adapter.query(
                    record.intent.symbol,
                    record.client_order_id,
                    algo=self.is_algo(record.intent),
                )
                BinanceExchangeAdapter._validate_identity(receipt, record.intent.symbol, record.client_order_id)
                if receipt.status is None:
                    raise OrderOutcomeUnknown("Query missing status")
                outcomes[record.client_order_id] = receipt
            except OrderOutcomeUnknown:
                blocked.add(record.intent.symbol)
        return outcomes, blocked

    def _record_unknown(
        self, records: list[JournalOrder], outcomes: dict[str, OrderReceipt]
    ) -> None:
        for record in records:
            if record.client_order_id not in outcomes:
                self.journal.update(
                    record.client_order_id,
                    "cancel_unknown" if record.state == "cancel_unknown" else "unknown",
                )
                self.report(
                    f"Order outcome unknown: {record.intent.symbol} {record.client_order_id}"
                )

    def _read_snapshot(
        self, records: list[JournalOrder], blocked: set[str], event: str | None
    ) -> ExchangeSnapshot:
        refresh = getattr(self.adapter, "refresh_snapshot", None)
        if (
            refresh is not None
            and self.state is not None
            and event in {"ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "ALGO_UPDATE"}
            and not blocked
            and all(record.state == "accepted" for record in records)
        ):
            state = refresh(
                self.state,
                self.symbols,
                self.config,
                # A lookup may discover fills/cancels before their order event.
                account_only=event == "ACCOUNT_UPDATE" and not records,
            )
        else:
            # Legacy adapters and uncertain outcomes retain full reconciliation.
            state = self.adapter.snapshot(self.symbols, self.config)
        return state

    def _apply_reconciliation(
        self, records: list[JournalOrder], outcomes: dict[str, OrderReceipt],
        blocked: set[str], state: ExchangeSnapshot, observed_at: float,
    ) -> None:
        # A successful lookup is insufficient without an authoritative account snapshot.
        self._confirmed_receipts = outcomes
        for record in records:
            outcome = outcomes.get(record.client_order_id)
            if outcome is None:
                state.balance.restore_margin(record.client_order_id, record.margin)
            else:
                self._order_ids[(record.intent.symbol, self.is_algo(record.intent), outcome.order_id)] = record.client_order_id
                if outcome.status in {"FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED", "FINISHED"}:
                    self._terminal_orders.add(("ALGO_UPDATE" if self.is_algo(record.intent) else "ORDER_TRADE_UPDATE", f"{record.intent.symbol}:{outcome.order_id}"))
                outstanding = outcome.status in {
                    "NEW",
                    "PARTIALLY_FILLED",
                    "TRIGGERING",
                    "TRIGGERED",
                }
                if record.state == "cancel_unknown" and outstanding:
                    blocked.add(record.intent.symbol)
                    state.balance.restore_margin(record.client_order_id, record.margin)
                    self.journal.update(record.client_order_id, "cancel_unknown")
                else:
                    self.journal.update(
                        record.client_order_id,
                        "accepted" if outstanding else "resolved",
                    )
        self.state, self.blocked = state, blocked
        self._account_updated_at = observed_at
        self._account_dirty = False
        self._positions_dirty.clear()
        self._risk_symbols.difference_update(s for s in self.symbols if not state.positions[s])
        self.on_snapshot(state)

    def reconcile(self, *, event: str | None = None) -> None:
        """Synchronous initialization for standalone gateways; live uses reconcile_async."""
        records = self.journal.pending()
        outcomes, blocked = self._query_pending(records)
        self._record_unknown(records, outcomes)
        observed_at = self.clock()
        state = self._read_snapshot(records, blocked, event)
        self._apply_reconciliation(records, outcomes, blocked, state, observed_at)

    async def _reconcile(self, *, event: str | None = None) -> None:
        records = self.journal.pending()
        LOGGER.info("Account REST: reason=%s symbols=%s pending_orders=%s", event or "full_reconciliation", self.symbols, len(records))
        outcomes, blocked = await self.rest.call(self._query_pending, records)
        self._record_unknown(records, outcomes)
        observed_at = self.clock()
        scoped = (hasattr(self.adapter, "refresh_snapshot") and self.state is not None and
                  event in {"ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "ALGO_UPDATE"} and
                  not blocked and all(r.state == "accepted" for r in records))
        orders_read = not scoped or event != "ACCOUNT_UPDATE" or bool(records)
        fence = {k: v for k, v in self._received_versions.items() if
                 k[0] in {"position", "balance"} or
                 (k[0] in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"} and orders_read) or
                 (k[0] == "leverage" and not scoped)}
        transactions = dict(self._received_position_transactions)
        sequences = dict(self._received_sequences)
        state = await self.rest.call(self._read_snapshot, records, blocked, event)
        self._apply_reconciliation(records, outcomes, blocked, state, observed_at)
        for key, version in fence.items():
            self._entity_versions[key] = max(version, self._entity_versions.get(key, -1))
            self._snapshot_sequences[key] = max(sequences.get(key, 0), self._snapshot_sequences.get(key, 0))
            if key[0] == "position":
                self._position_transactions[key[1]] = max(transactions.get(key[1], 0), self._position_transactions.get(key[1], 0))

    async def reconcile_async(self, *, event: str | None = None) -> None:
        self._refresh_waiters += 1
        try:
            async with self._mutex:
                await self._reconcile(event=event)
        finally:
            self._refresh_waiters -= 1
            self.entry_changed.set()

    @staticmethod
    def _number(value: Any, *, nonnegative: bool = False) -> float:
        if isinstance(value, bool):
            raise ValueError("Boolean in numeric event field")
        result = float(value)
        if not math.isfinite(result) or (nonnegative and result < 0):
            raise ValueError("Invalid numeric event field")
        return result

    @staticmethod
    def _integer(value: Any, *, positive: bool = False) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("Invalid integer event field")
        result = int(value)
        if str(result) != str(value) or result < (1 if positive else 0):
            raise ValueError("Invalid integer event field")
        return result

    @staticmethod
    def _event_entities(data: dict[str, Any]) -> list[tuple[str, str]]:
        event = data.get("e")
        if event == "ACCOUNT_UPDATE":
            account = data.get("a", {})
            return [("balance", str(b["a"])) for b in account.get("B", [])] + [
                ("position", str(p["s"])) for p in account.get("P", [])]
        if event == "ACCOUNT_CONFIG_UPDATE":
            return ([("leverage", str(data["ac"]["s"]))] if "ac" in data else []) + (
                [("mode", "multi_asset")] if "ai" in data else [])
        if event in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"}:
            order = data.get("o", {})
            identifier = order.get("i") if event == "ORDER_TRADE_UPDATE" else order.get("aid")
            return [(str(event), f"{order.get('s')}:{identifier}")]
        return []

    def observe_received(self, data: dict[str, Any]) -> None:
        self._receive_sequence += 1
        data["__obf_sequence"] = self._receive_sequence
        version = self._integer(data.get("E", data.get("T", 0)))
        for key in self._event_entities(data):
            self._received_sequences[key] = self._receive_sequence
            if version >= self._received_versions.get(key, -1):
                self._received_versions[key] = version
                if key[0] == "position":
                    self._received_position_transactions[key[1]] = self._integer(data.get("T", version))

    async def _refresh_event(
        self, symbols: Sequence[str], *, balance: bool = False,
        positions: bool = False, orders: bool = False, reason: str,
        confirmed: set[str] | None = None,
    ) -> None:
        refresh = getattr(self.adapter, "refresh_event", None)
        LOGGER.info("Account REST: reason=%s symbols=%s balance=%s positions=%s orders=%s",
                    reason, tuple(symbols), balance, positions, orders)
        if refresh is None:
            await self._reconcile()
            return
        fence = dict(self._received_versions)
        transactions = dict(self._received_position_transactions)
        sequences = dict(self._received_sequences)
        observed_at = self.clock()
        state = await self.rest.call(refresh, self.snapshot(), symbols, self.config,
                                     balance=balance, positions=positions, orders=orders)
        if balance:
            for record in self.journal.pending():
                if record.state in {"prepared", "unknown", "cancel_unknown"} and record.client_order_id not in (confirmed or set()):
                    state.balance.restore_margin(record.client_order_id, record.margin)
            self._account_dirty = False
            self._account_updated_at = observed_at
        if positions:
            self._positions_dirty.difference_update(symbols)
            self._risk_symbols.difference_update(s for s in symbols if not state.positions[s])
        self.state = state
        for key, version in fence.items():
            if ((balance and key[0] == "balance") or
                (positions and key[0] == "position" and key[1] in symbols) or
                (orders and key[0] in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"} and key[1].split(":")[0] in symbols)):
                self._entity_versions[key] = max(version, self._entity_versions.get(key, -1))
                self._snapshot_sequences[key] = max(sequences.get(key, 0), self._snapshot_sequences.get(key, 0))
                if key[0] == "position":
                    self._position_transactions[key[1]] = max(transactions.get(key[1], 0), self._position_transactions.get(key[1], 0))
        self.on_snapshot(state)

    async def check_account_modes(self) -> None:
        position_mode = await self.rest.call(self.adapter.account_mode)
        if not isinstance(position_mode, bool):
            raise ValueError("Invalid account position mode")
        if position_mode:
            self._entry_holds.add("unsupported_position_mode")
            raise ValueError("Hedge mode is unsupported; use one-way account mode")
        self._entry_holds.discard("unsupported_position_mode")
        query = getattr(self.adapter, "multi_assets_mode", None)
        if query is None:
            return
        key = ("mode", "multi_asset")
        fence = self._received_versions.get(key, -1)
        sequence = self._received_sequences.get(key, 0)
        mode = await self.rest.call(query)
        if not isinstance(mode, bool):
            raise ValueError("Invalid multi-assets account mode")
        if mode:
            self._entry_holds.add("unsupported_multi_asset_mode")
            self.report("Multi-assets mode is unsupported; new entries paused")
        else:
            self._entry_holds.discard("unsupported_multi_asset_mode")
        self._entity_versions[key] = max(fence, self._entity_versions.get(key, -1))
        self._snapshot_sequences[key] = max(sequence, self._snapshot_sequences.get(key, 0))

    def _apply_receipt(
        self, intent: OrderIntent, identifier: str, receipt: OrderReceipt, *, update_order: bool = True,
    ) -> None:
        """Known acknowledgement changes the journal, never fabricates free balance."""
        outstanding = receipt.status in {"NEW", "PARTIALLY_FILLED", "TRIGGERING", "TRIGGERED"}
        self._order_ids[(intent.symbol, self.is_algo(intent), receipt.order_id)] = identifier
        self._confirmed_receipts[identifier] = receipt
        state = self.snapshot()
        book = state.orders[intent.symbol]
        algo = self.is_algo(intent)
        existing = [o for o in book if o.order_id == receipt.order_id and
                    o.is_algo == algo]
        if not update_order and bool(existing) != outstanding:
            record = next(r for r in self.journal.pending() if r.client_order_id == identifier)
            self.journal.update(identifier, "cancel_unknown" if record.state == "cancel_unknown" else "unknown")
            self.blocked.add(intent.symbol)
            state.balance.restore_margin(identifier, record.margin)
            self.report(f"Order state inconsistent: {intent.symbol} {identifier}; reconcile original ID")
            return
        if update_order:
            book.orders[:] = [o for o in book if o not in existing]
            if outstanding:
                book.add(Order(intent.symbol, receipt.order_id, intent.order_type, intent.side,
                               intent.price or 0., max(0., (intent.quantity or 0.) - (receipt.executed_quantity or 0.)),
                               reduce_only=intent.reduce_only or intent.close_position, gtd=intent.gtd,
                               algo=algo, trigger_price=intent.price if algo else None))
        self.journal.update(identifier, "accepted" if outstanding else "resolved")
        if not outstanding:
            self._terminal_orders.add(("ALGO_UPDATE" if algo else "ORDER_TRADE_UPDATE", f"{intent.symbol}:{receipt.order_id}"))
            state.balance.consume_margin(identifier, state.balance.reserved_margin_for(identifier))
        if not any(r.intent.symbol == intent.symbol and r.state in {"prepared", "unknown", "cancel_unknown"}
                   for r in self.journal.pending()):
            self.blocked.discard(intent.symbol)
        if receipt.status == "REJECTED" and (intent.reduce_only or intent.close_position):
            self._risk_symbols.add(intent.symbol)
            self.report(f"Protection order rejected: {intent.symbol} order_id={receipt.order_id}")
        self._account_dirty = True
        if not algo and (receipt.status in {"PARTIALLY_FILLED", "FILLED"} or (receipt.executed_quantity or 0) > 0):
            self._positions_dirty.add(intent.symbol)
        self.on_snapshot(state)

    async def _accept_entity(
        self, key: tuple[str, str], version: int, data: dict[str, Any], signature: tuple[Any, ...],
    ) -> bool:
        sequence = data.get("__obf_sequence")
        if ((sequence is not None and sequence <= self._snapshot_sequences.get(key, 0)) or
            version < self._entity_versions.get(key, -1)):
            return False
        cached_version, seen, latest = self._entity_updates.get(key, (version, set(), ()))
        if cached_version != version:
            seen = set()
        if signature in seen:
            if signature != latest:
                # Identical reversions and replay are indistinguishable within one ms.
                kind, entity = key
                if kind == "position" and entity in self.symbols:
                    await self._refresh_event([entity], positions=True, reason="same_time_position_ambiguity")
                elif kind == "balance":
                    await self._refresh_event((), balance=True, reason="same_time_balance_ambiguity")
                elif kind == "leverage" and entity in self.symbols:
                    self.snapshot().leverage[entity] = await self.rest.call(self.adapter.leverage, entity)
                    for position in self.snapshot().positions[entity]:
                        position.leverage = self.snapshot().leverage[entity]
                    self._account_dirty = True
                    self.on_snapshot(self.snapshot())
                elif kind == "mode":
                    await self.check_account_modes()
                    self._account_dirty = True
                elif kind in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"}:
                    symbol = entity.split(":")[0]
                    if symbol in self.symbols:
                        await self._refresh_event([symbol], orders=True, reason="same_time_order_ambiguity")
            return False
        seen.add(signature)
        self._entity_updates[key] = (version, seen, signature)
        self._entity_versions[key] = version
        return True

    async def apply_event(self, data: dict[str, Any]) -> bool:
        """Apply complete events under the same lock as reservations and REST reads."""
        event = data.get("e")
        if "E" not in data and "T" not in data:
            return False
        version = self._integer(data.get("E", data.get("T", 0)))
        async with self._mutex:
            state = self.snapshot()
            changed = False
            signature: tuple[Any, ...]
            if event == "ACCOUNT_UPDATE":
                account = data.get("a", {})
                for asset in account.get("B", []):
                    if not {"a", "wb", "cw"} <= asset.keys():
                        return False
                for position in account.get("P", []):
                    if not {"s", "pa", "ep", "ps"} <= position.keys():
                        return False
                for asset in account.get("B", []):
                    name = str(asset["a"])
                    key = ("balance", name)
                    values = (self._number(asset["wb"]), self._number(asset["cw"]))
                    signature = (*values, self._number(asset.get("bc", 0)), self._integer(data.get("T", version)))
                    if not await self._accept_entity(key, version, data, signature):
                        continue
                    self.wallet_balances[name] = values
                    changed = self._account_dirty = True
                for value in account.get("P", []):
                    symbol = str(value["s"])
                    key = ("position", symbol)
                    amount = self._number(value["pa"])
                    price = self._number(value["ep"], nonnegative=True)
                    bep = self._number(value.get("bep", price))
                    if value["ps"] != "BOTH":
                        self._entry_holds.add("unsupported_position_mode")
                        raise ValueError("Hedge position event in one-way account")
                    if amount and price <= 0:
                        raise ValueError("Position has no entry price")
                    signature = (amount, price, bep, self._number(value.get("cr", 0)),
                                 self._number(value.get("up", 0)), self._number(value.get("iw", 0)),
                                 str(value.get("mt")), self._integer(data.get("T", version)))
                    if not await self._accept_entity(key, version, data, signature):
                        continue
                    state = self.snapshot()
                    self._position_transactions[symbol] = self._integer(data.get("T", version))
                    changed = self._account_dirty = True
                    if symbol in self.symbols:
                        state.positions[symbol].update_positions(
                            [Position(symbol, price, abs(amount), PositionSide.BUY if amount > 0 else PositionSide.SELL,
                                      state.leverage[symbol], break_even_price=bep)] if amount else [])
                        state.positions[symbol].entry_count = int(bool(amount))
                        self._positions_dirty.discard(symbol)
                        if not amount:
                            self._risk_symbols.discard(symbol)
            elif event == "ACCOUNT_CONFIG_UPDATE":
                if "ac" in data:
                    config = data["ac"]
                    symbol = str(config["s"])
                    leverage = self._integer(config["l"], positive=True)
                    key = ("leverage", symbol)
                    if await self._accept_entity(key, version, data, (leverage, self._integer(data.get("T", version)))):
                        state = self.snapshot()
                        if symbol in self.symbols:
                            state.leverage[symbol] = leverage
                            for position in state.positions[symbol]:
                                position.leverage = leverage
                        changed = self._account_dirty = True
                if "ai" in data:
                    mode = data["ai"].get("j")
                    if not isinstance(mode, bool):
                        raise ValueError("Invalid multi-assets mode event")
                    key = ("mode", "multi_asset")
                    if await self._accept_entity(key, version, data, (mode, self._integer(data.get("T", version)))):
                        if mode:
                            self._entry_holds.add("unsupported_multi_asset_mode")
                        else:
                            self._entry_holds.discard("unsupported_multi_asset_mode")
                        if mode:
                            self.report("Multi-assets mode is unsupported; new entries paused")
                        changed = self._account_dirty = True
            elif event in {"ORDER_TRADE_UPDATE", "ALGO_UPDATE"}:
                value = data.get("o", {})
                algo = event == "ALGO_UPDATE"
                id_field, client_field = ("aid", "caid") if algo else ("i", "c")
                required = {"s", id_field, "X", "o", "S", "q", "p", "R", "ps"}
                if not algo:
                    required.add("z")
                if not required <= value.keys() or any(value[name] is None for name in required):
                    return False
                symbol = str(value["s"])
                order_id = self._integer(value[id_field], positive=True)
                kind = OrderType(value["o"])
                side = PositionSide(value["S"])
                quantity = self._number(value["q"], nonnegative=True)
                filled = self._number(value.get("z", 0), nonnegative=True)
                price = self._number(value["p"], nonnegative=True)
                raw_trigger = value.get("tp" if algo else "sp")
                trigger = self._number(raw_trigger, nonnegative=True) if raw_trigger is not None else 0.
                if algo and not price:
                    price = trigger
                if filled > quantity or value["ps"] != "BOTH" or not isinstance(value["R"], bool) or not isinstance(value.get("cp", False), bool):
                    raise ValueError("Invalid order event")
                status = str(value["X"])
                outstanding = status in {"NEW", "PARTIALLY_FILLED", "TRIGGERING", "TRIGGERED"}
                if status not in {"NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "REJECTED", "TRIGGERING", "TRIGGERED", "FINISHED"}:
                    raise ValueError("Invalid order status")
                key = (str(event), f"{symbol}:{order_id}")
                if outstanding and key in self._terminal_orders:
                    return True
                signature = (quantity, filled, price, trigger, kind, side, status, value["R"], value.get("cp", False),
                             str(value.get(client_field)), str(value.get("ai")), str(value.get("t")),
                             self._integer(data.get("T", version)))
                if not await self._accept_entity(key, version, data, signature):
                    return True
                if not outstanding:
                    self._terminal_orders.add(key)
                state = self.snapshot()
                changed = self._account_dirty = True
                identifier = value.get(client_field) or self._order_ids.get((symbol, algo, order_id))
                if symbol in self.symbols:
                    book = state.orders[symbol]
                    book.orders[:] = [o for o in book if not (o.order_id == order_id and
                                     o.is_algo == algo)]
                    if outstanding:
                        book.add(Order(symbol, order_id, kind, side, price, quantity - filled,
                                       reduce_only=value["R"] or bool(value.get("cp")), gtd=value.get("gtd"),
                                       algo=algo, trigger_price=trigger or None))
                    if not algo and filled > 0 and self._integer(value.get("T", data.get("T", version))) > self._position_transactions.get(symbol, -1):
                        self._positions_dirty.add(symbol)
                    if algo and value.get("ai"):
                        self.algo_orders[(symbol, order_id)] = self._integer(value["ai"], positive=True)
                if algo and status == "REJECTED" and (value["R"] or value.get("cp")):
                    self._risk_symbols.add(symbol)
                    self.report(f"Protection order rejected: {symbol} order_id={order_id}")
                record = next((r for r in self.journal.pending() if r.client_order_id == identifier and self.is_algo(r.intent) == algo), None)
                if record is not None:
                    if record.intent.symbol != symbol:
                        raise ValueError("Event identity conflicts with journal")
                    self._order_ids[(symbol, algo, order_id)] = record.client_order_id
                    if record.state == "cancel_unknown" and outstanding:
                        self.blocked.add(symbol)
                    else:
                        self.journal.update(record.client_order_id, "accepted" if outstanding else "resolved")
                        # Keep the optimistic deduction until authoritative free balance is read.
                        state.balance.consume_margin(record.client_order_id, state.balance.reserved_margin_for(record.client_order_id))
                        if not any(r.intent.symbol == symbol and r.state in {"prepared", "unknown", "cancel_unknown"} for r in self.journal.pending()):
                            self.blocked.discard(symbol)
                if not algo and not outstanding:
                    self._finish_algo_child(symbol, order_id)
                elif algo and value.get("ai"):
                    child = self._integer(value["ai"], positive=True)
                    if status == "FINISHED":
                        self._terminal_orders.add(("ORDER_TRADE_UPDATE", f"{symbol}:{child}"))
                    if ("ORDER_TRADE_UPDATE", f"{symbol}:{child}") in self._terminal_orders:
                        self._finish_algo_child(symbol, child)
            else:
                return False
            if changed:
                LOGGER.info("Account event applied: event=%s version=%s entities=%s", event, version, self._event_entities(data))
                self.on_snapshot(self.snapshot())
            self.entry_changed.set()
            return True

    def _finish_algo_child(self, symbol: str, order_id: int) -> None:
        # ponytail: linear lookup through observed links; index children if session volume warrants it.
        for (parent_symbol, parent), child in self.algo_orders.items():
            if parent_symbol != symbol or child != order_id:
                continue
            state = self.snapshot()
            self._terminal_orders.add(("ALGO_UPDATE", f"{symbol}:{parent}"))
            state.orders[symbol].orders[:] = [o for o in state.orders[symbol] if not
                (o.order_id == parent and o.is_algo)]
            identifier = self._order_ids.get((symbol, True, parent))
            record = next((r for r in self.journal.pending() if r.client_order_id == identifier), None)
            if record is not None:
                self.journal.update(record.client_order_id, "resolved")
                state.balance.consume_margin(record.client_order_id, state.balance.reserved_margin_for(record.client_order_id))
                if not any(r.intent.symbol == symbol and r.state in {"prepared", "unknown", "cancel_unknown"} for r in self.journal.pending()):
                    self.blocked.discard(symbol)
            LOGGER.info("Algo child finished: symbol=%s algo_id=%s order_id=%s", symbol, parent, child)

    async def _reconcile_event_locked(self, symbol: str | None, identifier: str | None, *, reason: str) -> None:
        symbols = [symbol] if symbol in self.symbols else []
        records = [r for r in self.journal.pending() if r.intent.symbol in symbols and
                   (r.client_order_id == identifier or r.state != "accepted" or
                    (reason in {"MARGIN_CALL", "CONDITIONAL_ORDER_TRIGGER_REJECT"} and
                     (r.intent.reduce_only or r.intent.close_position)))]
        outcomes, blocked = await self.rest.call(self._query_pending, records)
        self._record_unknown(records, outcomes)
        confirmed = {r.client_order_id for r in records if r.client_order_id in outcomes and not (
            r.state == "cancel_unknown" and outcomes[r.client_order_id].status in
            {"NEW", "PARTIALLY_FILLED", "TRIGGERING", "TRIGGERED"})}
        await self._refresh_event(symbols, balance=True, positions=bool(symbols), orders=bool(symbols),
                                  reason=reason, confirmed=confirmed)
        for record in records:
            outcome = outcomes.get(record.client_order_id)
            if outcome is None:
                self.blocked.add(record.intent.symbol)
            elif record.state == "cancel_unknown" and outcome.status in {"NEW", "PARTIALLY_FILLED", "TRIGGERING", "TRIGGERED"}:
                self.blocked.add(record.intent.symbol)
            else:
                self._apply_receipt(record.intent, record.client_order_id, outcome, update_order=False)
        self._account_dirty = False
        self._positions_dirty.difference_update(symbols)
        self.blocked.update(blocked)

    async def reconcile_event(self, data: dict[str, Any], *, reason: str = "event_incomplete") -> None:
        if not hasattr(self.adapter, "refresh_event"):
            await self.reconcile_async(event=str(data.get("e")))
            return
        value = data.get("o", data.get("or", {}))
        symbol = value.get("s")
        identifier = value.get("c", value.get("caid"))
        if identifier is None:
            algo = data.get("e") == "ALGO_UPDATE"
            order_id = value.get("aid" if algo else "i")
            identifier = self._order_ids.get((symbol, algo, int(order_id or 0)))
        self._refresh_waiters += 1
        try:
            async with self._mutex:
                if data.get("e") == "ACCOUNT_UPDATE":
                    symbols = [p["s"] for p in data.get("a", {}).get("P", []) if p.get("s") in self.symbols]
                    await self._refresh_event(symbols, balance=True, positions=bool(symbols), reason=reason)
                else:
                    await self._reconcile_event_locked(symbol, identifier, reason=reason)
        finally:
            self._refresh_waiters -= 1
            self.entry_changed.set()

    async def risk_event(self, data: dict[str, Any]) -> None:
        event = str(data.get("e"))
        values = data.get("p", []) if event == "MARGIN_CALL" else [data.get("or", {})]
        symbols = {str(v["s"]) for v in values if v.get("s") in self.symbols}
        self._risk_symbols.update(symbols)
        self.report(f"Account risk: event={event} symbols={','.join(sorted(symbols))}")
        if not symbols:
            self._account_dirty = True
            return
        async with self._mutex:
            for symbol in sorted(symbols):
                await self._reconcile_event_locked(symbol, None, reason=event)
                if not self.snapshot().positions[symbol]:
                    self._risk_symbols.discard(symbol)

    async def ensure_positions(self, symbol: str) -> None:
        if symbol not in self._positions_dirty:
            return
        async with self._mutex:
            if symbol in self._positions_dirty:
                await self._refresh_event([symbol], positions=True, reason="fill_position_missing")

    async def wait_idle(self) -> None:
        """Drain submissions from strategy-created tasks before closing the journal."""
        async with self._mutex:
            pass
        while self._submissions:
            await self._submissions_idle.wait()

    async def initialize_leverage(self) -> None:
        """Apply startup configuration once, retaining adopted exposure unchanged."""
        async with self._mutex:
            for symbol in self.symbols:
                if symbol in self._leverage_initialized:
                    continue
                state = self.snapshot()
                if state.leverage[symbol] != self.config.leverage:
                    revision = self.account_activity()
                    await self._reconcile()
                    state = self.snapshot()
                else:
                    revision = self.account_activity()
                if (
                    state.positions[symbol]
                    or any(not order.reduce_only for order in state.orders[symbol])
                    or symbol in self.blocked
                ):
                    self._leverage_initialized.add(symbol)
                    continue
                if state.leverage[symbol] != self.config.leverage:
                    def authorize() -> None:
                        # ponytail: any account event invalidates startup; use symbol revisions if traffic stalls it.
                        if revision != self.account_activity() or not self.can_send():
                            raise _EntryPending("Startup account activity requires fresh synchronization")

                    await self.rest.call(self.adapter.set_leverage, symbol, self.config.leverage, check=authorize)
                    confirmed = await self.rest.call(self.adapter.leverage, symbol)
                    if confirmed != self.config.leverage:
                        raise OrderOutcomeUnknown(f"Leverage change not confirmed: {symbol}")
                    state.leverage[symbol] = confirmed
                self._leverage_initialized.add(symbol)
            self.on_snapshot(self.snapshot())

    def _prepare_intent(self, intent: OrderIntent) -> tuple[OrderIntent, float]:
        """Validate and normalize fields without using any leverage-dependent size."""
        if intent.order_type == OrderType.LIQUIDATION:
            raise ValueError("Liquidation is not a placement order type")
        if intent.order_type == OrderType.MARKET and (
            intent.time_in_force is not None or intent.gtd is not None
        ):
            raise ValueError("MARKET orders do not support time_in_force or gtd")
        if intent.gtd is not None and intent.time_in_force not in {None, "GTD"}:
            raise ValueError("good till date requires GTD")
        if intent.close_position:
            if (
                intent.order_type
                not in {OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET}
                or intent.quantity is not None
                or intent.reduce_only
            ):
                raise ValueError(
                    "close_position requires stop/take-profit market without quantity or reduce_only"
                )
        if intent.order_type == OrderType.TRAILING_STOP_MARKET:
            if (
                intent.callback_rate is None
                or not math.isfinite(intent.callback_rate)
                or not 0.1 <= intent.callback_rate <= 10
            ):
                raise ValueError("callback_rate must be within 0.1..10")
        for name, value in [
            ("price", intent.price),
            ("quantity", intent.quantity),
            ("activation_price", intent.activation_price),
        ]:
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if (
            intent.order_type not in {OrderType.MARKET, OrderType.TRAILING_STOP_MARKET}
            and intent.price is None
        ):
            raise ValueError("price is required")
        state = self.snapshot()
        reference = intent.price
        if reference is None and self.reference_price is not None:
            reference = self.reference_price(intent.symbol)
        if reference is None or not math.isfinite(reference) or reference <= 0:
            raise ValueError(
                "Managed orders require a reference price for conservative sizing"
            )
        rule = state.exchange_info._get_filter(intent.symbol)

        def round_step(value: float, step: float, rounding: str) -> float:
            increment = Decimal(str(step))
            return float(
                (Decimal(str(value)) / increment).to_integral_value(rounding=rounding)
                * increment
            )

        price = (
            round_step(intent.price, rule.tick_size, ROUND_HALF_UP)
            if intent.price is not None
            else None
        )
        activation = (
            round_step(intent.activation_price, rule.tick_size, ROUND_HALF_UP)
            if intent.activation_price is not None
            else None
        )
        if price is not None:
            if price <= 0:
                raise ValueError("price is below the exchange tick size")
            reference = price
        quantity = intent.quantity
        if quantity is not None:
            quantity = round_step(quantity, rule.step_size, ROUND_DOWN)
            if quantity <= 0 or (
                not intent.reduce_only and quantity * reference < rule.min_notional
            ):
                raise ValueError("Order quantity violates exchange filters")
        normalized = replace(
            intent, quantity=quantity, price=price, activation_price=activation
        )
        return normalized, reference

    def _size_intent(
        self, intent: OrderIntent, reference: float, leverage: int
    ) -> tuple[OrderIntent, float] | None:
        quantity = intent.quantity
        if quantity is None and not intent.close_position:
            state = self.snapshot()
            rule = state.exchange_info._get_filter(intent.symbol)
            initial = (
                state.balance.available
                * self.config.position_size
                * leverage
                / reference
            )
            step = Decimal(str(rule.step_size))
            quantity = float(
                (Decimal(str(initial)) / step).to_integral_value(rounding=ROUND_DOWN)
                * step
            )
            if quantity <= 0 or (
                not intent.reduce_only and quantity * reference < rule.min_notional
            ):
                if not intent.reduce_only:
                    self.report(
                        f"Auto-sized entry skipped: {intent.symbol} quantity={quantity} "
                        f"reference_price={reference} min_notional={rule.min_notional}"
                    )
                    return None
                raise ValueError("Order quantity violates exchange filters")
        normalized = replace(intent, quantity=quantity)
        margin = (
            0.0
            if intent.reduce_only or intent.close_position
            else reference * float(quantity or 0) / leverage
        )
        return normalized, margin

    async def _refresh_for_protection(self, intent: OrderIntent) -> None:
        """Only risk-reducing requests may bypass an uncertain entry hold."""
        if not (intent.reduce_only or intent.close_position):
            raise OrderOutcomeUnknown(f"Unresolved order blocks {intent.symbol}")
        try:
            await self._reconcile()
        except Exception as error:
            self.active = False
            self.request_recovery()
            raise OrderOutcomeUnknown(
                "Protection requires a fresh account snapshot"
            ) from error
        # Keep existing protection (including a lost placement/cancel response)
        # from being duplicated while an entry is still uncertain.
        if intent.symbol in self.blocked and any(
            record.intent.symbol == intent.symbol
            and (record.intent.reduce_only or record.intent.close_position)
            for record in self.journal.pending()
        ):
            raise OrderOutcomeUnknown(
                f"Pending protection must be reconciled before another: {intent.symbol}"
            )

    def _validate_protection_position(self, intent: OrderIntent) -> None:
        position = self.snapshot().positions[intent.symbol].find_first()
        if (
            position is None
            or not math.isfinite(position.amount)
            or position.amount <= 0
            or intent.side == position.side
        ):
            raise OrderOutcomeUnknown(
                f"Protection requires a confirmed opposite position: {intent.symbol}"
            )
        if not intent.close_position and (
            intent.quantity is None or intent.quantity > position.amount
        ):
            raise ValueError("Protection requires explicit quantity within the position")

    def _check_transport(self) -> None:
        if not self.active or self.failed or not self.can_send():
            raise OrderOutcomeUnknown("Managed runtime is paused")

    def _check_submission(self, intent: OrderIntent) -> None:
        if intent.symbol not in self.symbols:
            raise ValueError("Order symbol is outside managed symbols")
        entry = not (intent.reduce_only or intent.close_position)
        if entry:
            if self._entry_holds and intent.symbol not in self.blocked:
                raise _EntryCancelled(",".join(sorted(self._entry_holds)))
            if intent.symbol in self._risk_symbols and intent.symbol not in self.blocked:
                raise _EntryCancelled("position_risk_or_protection_failure")
            reason = self.entry_abort_reason(intent)
            if reason is not None:
                raise _EntryCancelled(reason)
        self._check_transport()
        if entry and (self._refresh_waiters or not self.can_enter()):
            raise _EntryPending("account_refresh" if self._refresh_waiters else "account_events")

    def _log_entry(self, action: str, intent: OrderIntent, started: float, reason: str, *, dispatched: bool = False) -> None:
        self.entry_counts["waits" if action == "waiting" else action] += 1
        LOGGER.info(
            "Entry %s: stage=%s symbol=%s reason=%s %s wait_seconds=%.3f counts=%s",
            action, "confirmed" if dispatched or action == "submitted" else "pre_dispatch", intent.symbol, reason, self.entry_context(), self.clock() - started,
            self.entry_counts,
        )

    async def submit_order(self, intent: OrderIntent) -> bool:
        self._submissions += 1
        self._submissions_idle.clear()
        try:
            return await self._submit_when_ready(intent)
        finally:
            self._submissions -= 1
            if not self._submissions:
                self._submissions_idle.set()
            self.entry_changed.set()

    async def _submit_when_ready(self, intent: OrderIntent) -> bool:
        started = self.clock()
        dispatched = False

        def on_dispatch() -> None:
            nonlocal dispatched
            dispatched = True

        deadline = self.entry_deadline()
        if deadline is None:
            deadline = started + 15  # Non-candle callers have a bounded wait, no replay.
        waited = False
        while True:
            self.entry_changed.clear()
            try:
                self._check_submission(intent)
                async with self._mutex:
                    result = await self._submit_order(intent, on_dispatch)
                if not (intent.reduce_only or intent.close_position):
                    self._log_entry("submitted" if result else "cancelled", intent, started,
                                    "confirmed" if result else "exchange_rejected" if dispatched else "validation",
                                    dispatched=dispatched)
                return result
            except OrderOutcomeUnknown:
                if not dispatched and not self.active and not (intent.reduce_only or intent.close_position):
                    self._log_entry("cancelled", intent, started, "runtime_paused")
                raise
            except asyncio.CancelledError:
                if not dispatched and not (intent.reduce_only or intent.close_position):
                    self._log_entry("cancelled", intent, started, "task_cancelled")
                raise
            except _EntryCancelled as error:
                self._log_entry("cancelled", intent, started, str(error))
                return False
            except _EntryPending as error:
                if not waited:
                    waited = True
                    self._log_entry("waiting", intent, started, str(error))
                remaining = deadline - self.clock()
                if remaining <= 0:
                    self._log_entry("cancelled", intent, started, "wait_limit")
                    return False
                try:
                    # ponytail: 100ms fallback wakes standalone callers without runtime events.
                    await asyncio.wait_for(self.entry_changed.wait(), min(.1, remaining))
                except TimeoutError:
                    pass
                except asyncio.CancelledError:
                    self._log_entry("cancelled", intent, started, "task_cancelled")
                    raise

    async def _submit_order(self, intent: OrderIntent, on_dispatch: Callable[[], None]) -> bool:
        self._check_submission(intent)
        if intent.symbol in self.blocked:
            await self._refresh_for_protection(intent)
        if (
            not (intent.reduce_only or intent.close_position)
            and (self._account_dirty or intent.symbol in self._positions_dirty or self.clock() - self._account_updated_at >= 15)
        ):
            try:
                if hasattr(self.adapter, "refresh_event"):
                    dirty_position = intent.symbol in self._positions_dirty
                    await self._refresh_event([intent.symbol] if dirty_position else (),
                                              balance=self._account_dirty or self.clock() - self._account_updated_at >= 15,
                                              positions=dirty_position, reason="entry_available_balance")
                else:
                    await self._reconcile(event="ACCOUNT_UPDATE")
            except Exception as error:
                self.active = False
                self.request_recovery()
                raise OrderOutcomeUnknown(
                    "Entry requires a fresh available balance"
                ) from error
            if intent.symbol in self.blocked:
                raise OrderOutcomeUnknown(f"Unresolved order blocks {intent.symbol}")
        protecting_blocked = intent.symbol in self.blocked
        intent, reference = self._prepare_intent(intent)
        if protecting_blocked:
            self._validate_protection_position(intent)
        leverage = self.effective_leverage(intent.symbol)
        self._check_submission(intent)
        sized = self._size_intent(intent, reference, leverage)
        if sized is None:
            return False
        intent, margin = sized
        state = self.snapshot()
        if margin > state.balance.available:
            return False
        identifier = self.journal.prepare(intent, margin)
        state.balance.reserve_margin(identifier, margin)
        already_blocked = intent.symbol in self.blocked
        self.blocked.add(intent.symbol)
        dispatched = False

        def authorize() -> None:
            nonlocal dispatched
            self._check_submission(intent)
            if not (intent.reduce_only or intent.close_position) and intent.price is None and self.reference_price is not None:
                if self.reference_price(intent.symbol) != reference:
                    raise _EntryPending("reference_price_changed")
            dispatched = True
            on_dispatch()

        try:
            receipt = await self.rest.call(
                self.adapter.submit, intent, identifier,
                check=authorize,
            )
            BinanceExchangeAdapter._validate_identity(receipt, intent.symbol, identifier)
            if receipt.status == "REJECTED":
                raise OrderRejected("Exchange receipt reports REJECTED")
        except OrderRejected:
            if intent.reduce_only or intent.close_position:
                self._risk_symbols.add(intent.symbol)
                self.report(f"Protection order rejected: {intent.symbol} client_order_id={identifier}")
            self.journal.update(identifier, "rejected")
            state.balance.release_margin(identifier)
            if not already_blocked:
                self.blocked.discard(intent.symbol)
            return False
        except BaseException as error:
            if not dispatched:
                self.journal.update(identifier, "rejected")
                state.balance.release_margin(identifier)
                if not already_blocked:
                    self.blocked.discard(intent.symbol)
                raise
            self.blocked.add(intent.symbol)
            LOGGER.warning("Order unresolved: stage=dispatch_unknown symbol=%s client_order_id=%s",
                           intent.symbol, identifier)
            self.report(f"Order outcome unknown: {intent.symbol} {identifier}")
            self.journal.update(identifier, "unknown")
            if isinstance(
                error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)
            ):
                raise
            raise OrderOutcomeUnknown(
                f"Reconcile {identifier}; do not resend"
            ) from error
        # Keep the reservation until query + snapshot establishes account state.
        self.blocked.add(intent.symbol)
        try:
            self.journal.update(identifier, "accepted")
            if hasattr(self.adapter, "refresh_event") and receipt.status is not None and not (
                receipt.status == "PARTIALLY_FILLED" and receipt.executed_quantity is None
            ):
                self._apply_receipt(intent, identifier, receipt)
                if intent.symbol in self._positions_dirty:
                    await self._refresh_event([intent.symbol], positions=True, reason="placement_fill_position")
            elif hasattr(self.adapter, "refresh_event"):
                await self._reconcile_event_locked(intent.symbol, identifier, reason="placement_ack_incomplete")
            else:
                await self._reconcile()
        except Exception as error:
            self.active = False
            self.request_recovery()
            raise OrderOutcomeUnknown(
                f"Accepted order {identifier} requires account reconciliation"
            ) from error
        self.on_submission(intent, self._confirmed_receipts.get(identifier, receipt))
        return True

    async def cancel_order(self, client_order_id: str) -> None:
        async with self._mutex:
            if not self.active or self.failed or not self.can_send():
                raise OrderOutcomeUnknown("Managed runtime is paused")
            record = next(
                (
                    r
                    for r in self.journal.pending()
                    if r.client_order_id == client_order_id
                ),
                None,
            )
            if record is None:
                raise ValueError("Unknown managed client order ID")
            if record.state == "cancel_unknown":
                raise OrderOutcomeUnknown("Cancellation requires lookup; do not resend")
            self.journal.update(client_order_id, "cancel_unknown")
            already_blocked = record.intent.symbol in self.blocked
            self.blocked.add(record.intent.symbol)
            dispatched = False

            def authorize() -> None:
                nonlocal dispatched
                self._check_transport()
                dispatched = True

            try:
                receipt = await self.rest.call(
                    self.adapter.cancel,
                    record.intent.symbol,
                    client_order_id,
                    algo=self.is_algo(record.intent),
                    check=authorize,
                )
                BinanceExchangeAdapter._validate_identity(receipt, record.intent.symbol, client_order_id)
            except BaseException:
                if not dispatched:
                    self.journal.update(client_order_id, record.state)
                    if not already_blocked:
                        self.blocked.discard(record.intent.symbol)
                    raise
                self.report(
                    f"Cancellation outcome unknown: {record.intent.symbol} {client_order_id}"
                )
                raise
            try:
                if hasattr(self.adapter, "refresh_event") and receipt.status in {"CANCELED", "EXPIRED", "EXPIRED_IN_MATCH", "FILLED", "FINISHED", "REJECTED"}:
                    self._apply_receipt(record.intent, client_order_id, receipt)
                elif hasattr(self.adapter, "refresh_event"):
                    await self._reconcile_event_locked(record.intent.symbol, client_order_id, reason="cancel_ack_incomplete")
                else:
                    await self._reconcile()
            except Exception as error:
                self.active = False
                self.request_recovery()
                raise OrderOutcomeUnknown(
                    f"Cancellation {client_order_id} requires account reconciliation"
                ) from error
