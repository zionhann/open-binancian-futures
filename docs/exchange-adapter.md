# Injected exchange boundary

`BinanceExchangeAdapter(client)` wraps the supplied SDK client. Every exchange
initializer also accepts `sdk_client=`; supplying one never reads the singleton.
REST methods are synchronous. The live runtime serializes account reads, journal
writes and placement on its single event loop; no unowned thread can place an
order after shutdown. A currently executing synchronous REST request must return
before shutdown proceeds (the default client uses a 2-second request timeout;
SDK/read retry behavior can extend a multi-request synchronization). Injected
adapters must have bounded calls. Do not add a blind retry around placement.

`LiveTrading()` now uses the durable gateway and supervised SDK streams by default.
`LiveTrading(adapter=..., streams_factory=..., strategy=..., config=...,
journal_path=...)` runs the same full lifecycle offline with injected dependencies.
Construction performs no account reads or remote changes. `run()` owns one event
loop; applications with an existing loop use `await run_async()` and
`await aclose()`. `close()` requests shutdown on the owning loop.

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

## Supervised execution

`ExchangeStreams` declares async `connect`, `subscribe_klines`, `subscribe_user`,
and `close` with synchronous callbacks. A stream factory creates a fresh instance
per connection generation. Production `BinanceStreams` owns SDK receiver/rotation
tasks, validates that connect actually opened a socket, and removes only its own
connection identities from SDK global subscription maps. Injected streams can
expose a `recovery: asyncio.Event` and `healthy() -> bool` to signal failures.
Callbacks enqueue events; they never launch strategy tasks.

The REST adapter also supplies `identity`, `server_time`, `start_listen_key`,
`keepalive_listen_key`, and `close_listen_key`. Identity is an endpoint/API-key
fingerprint, never a raw credential. A fake adapter supplies a stable test identity.

`history` fetches one bounded raw kline page with explicit start/end timestamps.
Recovery paginates up to the fixed exchange cutoff, excludes forming candles,
rejects nonadvancing/missing pages, and checks calendar-month boundaries. It loads
recovered indicators without replaying `run`; only the next new closed bar trades.

Strategy constructors and indicator `load()` methods must only initialize state.
The default strategy factory receives `client=None`, complete synchronized domain
objects, and a paused gateway. Raw SDK access binds after construction/load.
Sync and async legacy order-notification hooks are called at most once per observed
order/status after an authoritative snapshot; their base implementations no longer
mutate managed order books. Historical NEW events for absent orders are discarded.
Async hook results are awaited inside the owned event worker; protective orders
can therefore use `await submit_order(...)` in an async fill hook.
Custom hooks should use the provided synchronized state rather than replay event
quantities. Hook/load/run errors latch failure until a fresh runtime is started.

A trailing or market intent without an explicit reference `price` uses the latest
closed candle of the first configured interval for sizing/filter checks. This is
not a fill-price guarantee. Explicit reference price remains supported; trailing
activation and callback fields are forwarded independently.

See [live operations and AC mapping](live-operations.md) for recovery storage,
unknown-order handling, limits, and the manual testnet checklist.
