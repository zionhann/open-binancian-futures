"""Single-loop managed order gateway: record once, send once, reconcile by ID."""

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .exchange_adapter import (
    ExchangeAdapter,
    ExchangeSnapshot,
    OrderOutcomeUnknown,
    OrderReceipt,
    OrderRejected,
)
from .execution import ExecutionConfig
from .live_journal import OrderJournal
from .models import OrderIntent
from .types import OrderType


class ManagedOrderGateway:
    def __init__(
        self,
        adapter: ExchangeAdapter,
        journal: OrderJournal,
        symbols: Sequence[str],
        config: ExecutionConfig,
        report: Callable[[str], None],
    ) -> None:
        self.adapter, self.journal = adapter, journal
        self.symbols, self.config = tuple(symbols), config
        self.report = report
        self.active = False
        self.can_send: Callable[[], bool] = lambda: True
        self.request_recovery: Callable[[], None] = lambda: None
        self.failed = False
        self.blocked: set[str] = set()
        self.state: ExchangeSnapshot | None = None
        self.reference_price: Callable[[str], float] | None = None
        self.on_snapshot: Callable[[ExchangeSnapshot], None] = lambda state: None
        self._mutex = asyncio.Lock()

    def snapshot(self) -> ExchangeSnapshot:
        if self.state is None:
            raise RuntimeError("Account has not been synchronized")
        return self.state

    def effective_leverage(self, symbol: str) -> int:
        return self.snapshot().leverage[symbol]

    @staticmethod
    def is_algo(intent: OrderIntent) -> bool:
        return intent.order_type not in {OrderType.MARKET, OrderType.LIMIT}

    def reconcile(self) -> None:
        """Queries precede a fresh snapshot; unknown reservations survive both."""
        records = self.journal.pending()
        outcomes: dict[str, OrderReceipt] = {}
        blocked = set()
        for record in records:
            try:
                receipt = self.adapter.query(
                    record.intent.symbol,
                    record.client_order_id,
                    algo=self.is_algo(record.intent),
                )
                if receipt.status is None:
                    raise OrderOutcomeUnknown("Query missing status")
                outcomes[record.client_order_id] = receipt
            except OrderOutcomeUnknown:
                blocked.add(record.intent.symbol)
                self.journal.update(
                    record.client_order_id,
                    "cancel_unknown" if record.state == "cancel_unknown" else "unknown",
                )
                self.report(
                    f"Order outcome unknown: {record.intent.symbol} {record.client_order_id}"
                )
        state = self.adapter.snapshot(self.symbols, self.config)
        # A successful lookup is insufficient without an authoritative account snapshot.
        for record in records:
            outcome = outcomes.get(record.client_order_id)
            if outcome is None:
                state.balance.restore_margin(record.client_order_id, record.margin)
            else:
                outstanding = outcome.status in {
                    "NEW",
                    "PARTIALLY_FILLED",
                    "TRIGGERING",
                    "TRIGGERED",
                }
                if record.state == "cancel_unknown" and outstanding:
                    blocked.add(record.intent.symbol)
                    self.journal.update(record.client_order_id, "cancel_unknown")
                else:
                    self.journal.update(
                        record.client_order_id,
                        "accepted" if outstanding else "resolved",
                    )
        self.state, self.blocked = state, blocked
        self.on_snapshot(state)

    def _leverage_for_entry(self, symbol: str) -> int:
        state = self.snapshot()
        actual = state.leverage[symbol]
        if state.positions[symbol].find_first() is not None:
            return actual
        if actual == self.config.leverage:
            return actual
        if any(not order.reduce_only for order in state.orders[symbol]):
            self.report(f"Leverage transition waiting for old entry orders: {symbol}")
            raise OrderOutcomeUnknown(
                f"Existing entry orders block leverage transition: {symbol}"
            )
        try:
            self.adapter.set_leverage(symbol, self.config.leverage)
            confirmed = self.adapter.leverage(symbol)
        except Exception as error:
            self.active = False
            self.request_recovery()
            raise OrderOutcomeUnknown(
                f"Leverage synchronization requires recovery: {symbol}"
            ) from error
        if confirmed != self.config.leverage:
            raise OrderOutcomeUnknown(f"Leverage change not confirmed: {symbol}")
        state.leverage[symbol] = confirmed
        return confirmed

    def _normalize(
        self, intent: OrderIntent, leverage: int
    ) -> tuple[OrderIntent, float]:
        if intent.symbol not in self.symbols:
            raise ValueError("Order symbol is outside managed symbols")
        if intent.order_type == OrderType.LIQUIDATION:
            raise ValueError("Liquidation is not a placement order type")
        if intent.gtd is not None and intent.time_in_force not in {None, "GTD"}:
            raise ValueError("good till date requires GTD")
        if intent.close_position:
            if (
                intent.order_type
                not in {OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET}
                or intent.quantity is not None
            ):
                raise ValueError(
                    "close_position requires stop/take-profit market without quantity"
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
        if quantity is None and not intent.close_position:
            quantity = (
                state.balance.available
                * self.config.position_size
                * leverage
                / reference
            )
        if quantity is not None:
            quantity = round_step(quantity, rule.step_size, ROUND_DOWN)
            if quantity <= 0 or (
                not intent.reduce_only and quantity * reference < rule.min_notional
            ):
                raise ValueError("Order quantity violates exchange filters")
        normalized = replace(
            intent, quantity=quantity, price=price, activation_price=activation
        )
        margin = (
            0.0
            if intent.reduce_only or intent.close_position
            else reference * float(quantity or 0) / leverage
        )
        return normalized, margin

    async def submit_order(self, intent: OrderIntent) -> bool:
        async with self._mutex:
            if not self.active or self.failed or not self.can_send():
                raise OrderOutcomeUnknown("Managed runtime is paused")
            if intent.symbol in self.blocked:
                raise OrderOutcomeUnknown(f"Unresolved order blocks {intent.symbol}")
            self._normalize(intent, self.effective_leverage(intent.symbol))
            leverage = (
                self.effective_leverage(intent.symbol)
                if intent.reduce_only or intent.close_position
                else self._leverage_for_entry(intent.symbol)
            )
            intent, margin = self._normalize(intent, leverage)
            state = self.snapshot()
            if margin > state.balance.available:
                return False
            identifier = self.journal.prepare(intent, margin)
            state.balance.reserve_margin(identifier, margin)
            self.blocked.add(intent.symbol)
            try:
                receipt = self.adapter.submit(intent, identifier)
                if receipt.status == "REJECTED":
                    raise OrderRejected("Exchange receipt reports REJECTED")
            except OrderRejected:
                self.journal.update(identifier, "rejected")
                state.balance.release_margin(identifier)
                self.blocked.discard(intent.symbol)
                return False
            except BaseException as error:
                self.blocked.add(intent.symbol)
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
                self.reconcile()
            except Exception as error:
                self.active = False
                self.request_recovery()
                raise OrderOutcomeUnknown(
                    f"Accepted order {identifier} requires account reconciliation"
                ) from error
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
            self.blocked.add(record.intent.symbol)
            try:
                self.adapter.cancel(
                    record.intent.symbol,
                    client_order_id,
                    algo=self.is_algo(record.intent),
                )
            except BaseException:
                self.report(
                    f"Cancellation outcome unknown: {record.intent.symbol} {client_order_id}"
                )
                raise
            try:
                self.reconcile()
            except Exception as error:
                self.active = False
                self.request_recovery()
                raise OrderOutcomeUnknown(
                    f"Cancellation {client_order_id} requires account reconciliation"
                ) from error
