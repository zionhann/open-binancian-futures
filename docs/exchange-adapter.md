# Injected exchange boundary

`BinanceExchangeAdapter(client)` wraps the supplied SDK client. Every exchange
initializer also accepts `sdk_client=`; supplying one never reads the singleton.
REST methods are synchronous: callers running an event loop must offload them.
Placement calls the SDK exactly once, bypassing the repository retry helper.

`LiveTrading(adapter=..., order_gateway=...)` accepts domain snapshots and initial
indicators without constructing a global client. This foundational change exposes
the constructor boundary only: injected `run()` fails before remote mutations
until the dependent managed stream supervisor is installed. The existing default
runner remains available and does not yet gain durable execution guarantees.

## Managed strategy surface

`StrategyContext.order_gateway` implements `async submit_order(intent) -> bool`.
`Strategy.submit_order` forwards the complete intent; `Strategy.open_order` forwards
entry price, side, order type, explicit time in force and good-till-date. The gateway
owns sizing/filter validation, reservation, durable submission and reconciliation.
Unknown outcomes propagate to the caller; they are never converted into `False`
by these managed helper paths.

Synchronous `set_tpsl` and `set_trailing_stop` explicitly reject use with a managed
gateway before any raw SDK request. Migrate these calls to:

```python
await strategy.submit_order(OrderIntent(
    symbol="BTCUSDT", side=PositionSide.SELL,
    order_type=OrderType.STOP_MARKET, price=60000,
    close_position=True, time_in_force="GTE_GTC",
))
await strategy.submit_order(OrderIntent(
    symbol="BTCUSDT", side=PositionSide.SELL,
    order_type=OrderType.TRAILING_STOP_MARKET, quantity=0.01,
    reduce_only=True, activation_price=65000, callback_rate=0.5,
    time_in_force="GTE_GTC",
))
```

`callback_rate` is the exchange percentage, not the leverage-adjusted ratio accepted
by the legacy helper. Calculate/clamp it using the same policy before constructing
the intent. Explicit time in force is preserved; GTD with another time in force is
rejected locally. SDK 7.1.1 lacks a GTE_GTC enum member, so the historical literal
is forwarded unchanged. The exchange may reject it; it is never replaced silently.
These extended conditional semantics are for the live managed adapter; the current
backtest gateway does not implement trailing stops or close-position intents.

Direct `strategy.client` calls and helpers used without a managed gateway remain
legacy escape hatches outside durable execution guarantees.

## Receipts and snapshots

Receipts normalize raw REST dictionaries, generated `to_dict()` models, and result
envelopes. A positive integer exchange ID is mandatory. Placement ACKs may omit
status/fill details; query requires a recognized status and rejects any supplied
symbol/client ID that differs from the lookup. Missing optional execution values
remain `None`, never fabricated zeros. Algo cancel ACKs can omit status.

Only a conservative explicit Binance negative-code allowlist yields `OrderRejected`.
Network errors, 5xx, unknown codes, malformed receipts, and lookup not-found yield
`OrderOutcomeUnknown`. Not-found does not establish that a timed-out order failed.

Snapshots query actual per-symbol leverage from `symbol_configuration`; existing
regular orders retain reduce-only and remaining quantity. Managed contexts preserve
position leverage when configuring strategy execution defaults.

## Dependent supervisor work

`ExchangeStreams` declares async `connect`, `subscribe_klines`, `subscribe_user`,
and `close` with synchronous callbacks. The supervisor implementation must own SDK
receiver/timer tasks, scoped stale-subscription cleanup, and generation recovery;
this change intentionally exposes no unowned stream implementation.

`history` fetches one bounded raw kline page with an explicit start/end cutoff.
Pagination, completed-bar filtering, gap checks and deduplication belong to recovery.
Listen-key lifecycle, account fingerprint, durable journal, reconnect supervision,
full fake-adapter run tests and injected execution activation ship in the dependent
live-runtime change. No live exchange operations were used to validate this change.

Strategy constructors and indicator `load()` methods must only initialize state;
they must not trade. Constructors accepting `order_gateway` receive it directly.
Legacy constructors are bound after initialization. The dependent managed runner
must instantiate with `client=None` and complete injected domain state until its
journal/gateway is ready, then expose raw SDK access only at activation.
