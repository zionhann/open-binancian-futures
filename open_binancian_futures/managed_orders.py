"""Single-loop managed order gateway: record once, send once, reconcile by ID."""

import asyncio
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from .exchange_adapter import (
    ExchangeAdapter,
    ExchangeSnapshot,
    OrderOutcomeUnknown,
    OrderReceipt,
    OrderRejected,
    RestCalls,
)
from .execution import ExecutionConfig
from .live_journal import JournalOrder, OrderJournal
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
        self._mutex = asyncio.Lock()
        self._refresh_waiters = 0
        self.rest = RestCalls()

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
        self._account_updated_at = observed_at
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
        outcomes, blocked = await self.rest.call(self._query_pending, records)
        self._record_unknown(records, outcomes)
        observed_at = self.clock()
        state = await self.rest.call(self._read_snapshot, records, blocked, event)
        self._apply_reconciliation(records, outcomes, blocked, state, observed_at)

    async def reconcile_async(self, *, event: str | None = None) -> None:
        self._refresh_waiters += 1
        try:
            async with self._mutex:
                await self._reconcile(event=event)
        finally:
            self._refresh_waiters -= 1

    async def wait_idle(self) -> None:
        """Drain submissions from strategy-created tasks before closing the journal."""
        async with self._mutex:
            pass

    async def _leverage_for_entry(self, symbol: str) -> int:
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
            await self.rest.call(
                self.adapter.set_leverage, symbol, self.config.leverage,
                check=self._check_transport,
            )
            confirmed = await self.rest.call(self.adapter.leverage, symbol)
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

    def _prepare_intent(self, intent: OrderIntent) -> tuple[OrderIntent, float]:
        """Validate and normalize fields without using any leverage-dependent size."""
        if intent.symbol not in self.symbols:
            raise ValueError("Order symbol is outside managed symbols")
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

    def _normalize(
        self, intent: OrderIntent, leverage: int
    ) -> tuple[OrderIntent, float] | None:
        prepared, reference = self._prepare_intent(intent)
        return self._size_intent(prepared, reference, leverage)

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
        self._check_transport()
        if not (intent.reduce_only or intent.close_position) and (
            self._refresh_waiters or not self.can_enter()
        ):
            raise OrderOutcomeUnknown(
                "Account update pending or decision stale; wait for a new entry decision"
            )

    async def submit_order(self, intent: OrderIntent) -> bool:
        async with self._mutex:
            self._check_submission(intent)
            if intent.symbol in self.blocked:
                await self._refresh_for_protection(intent)
            if (
                not (intent.reduce_only or intent.close_position)
                and self.clock() - self._account_updated_at >= 15
            ):
                try:
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
            leverage = (
                self.effective_leverage(intent.symbol)
                if intent.reduce_only or intent.close_position
                else await self._leverage_for_entry(intent.symbol)
            )
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
                dispatched = True

            try:
                receipt = await self.rest.call(
                    self.adapter.submit, intent, identifier,
                    check=authorize,
                )
                if receipt.status == "REJECTED":
                    raise OrderRejected("Exchange receipt reports REJECTED")
            except OrderRejected:
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
                await self._reconcile()
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
            already_blocked = record.intent.symbol in self.blocked
            self.blocked.add(record.intent.symbol)
            dispatched = False

            def authorize() -> None:
                nonlocal dispatched
                self._check_transport()
                dispatched = True

            try:
                await self.rest.call(
                    self.adapter.cancel,
                    record.intent.symbol,
                    client_order_id,
                    algo=self.is_algo(record.intent),
                    check=authorize,
                )
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
                await self._reconcile()
            except Exception as error:
                self.active = False
                self.request_recovery()
                raise OrderOutcomeUnknown(
                    f"Cancellation {client_order_id} requires account reconciliation"
                ) from error
