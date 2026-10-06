# Live runtime operations

## State and startup

`LiveTrading()` and the CLI use the managed runtime by default. The default journal
is `.obf-runtime/orders.sqlite3`, relative to the working directory. Override it
with `OBF_RUNTIME_PATH` or `LiveTrading(journal_path=...)`. Keep the same writable
local path across redeployments. The resolved path is logged and notified.

The runtime takes a POSIX OS lock, checks the endpoint/API-key fingerprint and
one-way account mode, opens streams, and synchronizes account state and closed
candles before activating strategy decisions. All existing target-symbol orders
and positions are adopted without cancel/recreate. Other symbols are unmanaged.
Hedge mode fails without automatically changing account mode or leverage.

At each process start, flat symbols without existing entry orders or unresolved
journal records confirm the configured leverage before activation. Existing positions
and entry orders retain their actual leverage; closing an adopted position does not
cause a later entry to reset it. Startup retries do not repeat confirmed changes,
and reconnect never reapplies startup configuration. Manual leverage changes are
adopted for subsequent sizing and stop calculations.

The journal stores generated client IDs, normalized intents, margin and processing
state in SQLite with FULL synchronous commits before each send. It stores no API
keys or secrets. A different endpoint/key cannot silently reuse it. API key rotation
therefore requires an operator to reconcile existing records before choosing a new
journal; do not simply delete unresolved records to unblock trading.

The OS releases the lock after a crash. The lock excludes processes using the same
resolved path, not different paths, machines, symlink aliases to the DB file itself,
or a distributed fleet. Use a local filesystem with working POSIX locks and SQLite
durability. Platforms without `fcntl` can still import the package and backtesting
APIs; opening the live journal explicitly reports the POSIX requirement. Do not
place the journal in source control; default DB files are ignored.

## Uncertain orders and recovery

Timeout, disconnection, malformed response and query not-found are unknown outcomes,
not `False`. `OrderOutcomeUnknown` holds the symbol and its reservation. Prepared,
unknown and accepted records are looked up by their original client IDs after
restart. They are never automatically submitted again. A successful lookup is
followed by a fresh account snapshot before the hold can clear. Unknown records
are queried again on account activity and the periodic 15-second reconciliation.
A cancellation timeout similarly remains held while the order is still open; the
runtime does not blindly repeat cancellation. Definitive rejection rolls back once.

An unresolved entry continues to block new or additional exposure in that symbol.
Protection is an exception: the gateway first repeats reconciliation and requires
a fresh snapshot containing an opposite-side position. It permits a close-position
stop/take-profit market order, or an explicit reduce-only quantity no larger than
that position after exchange-step normalization. Automatic quantity sizing is not
available for this exception. Snapshot failure pauses the gateway for recovery.
Any pending managed protection record (including accepted orders and uncertain
placement/cancellation) blocks another protection request through this exception,
even of a different type. Existing accepted protection is kept until resolved;
this conservative path does not replace or layer protection. Rejection of the new
protection does not release the original entry hold. Recording, original-ID lookup,
and periodic reconciliation remain unchanged. This does not automatically install
stops or guarantee protection of additional fills after the snapshot.

Unknown reservations are conservatively deducted from fresh free balance even if
the exchange may already reserve that money. This can temporarily understate free
balance, deliberately preventing reuse while the outcome remains unresolved.
Accepted orders/positions instead use exchange-reported available balance.

Startup and reconnect use full REST snapshots. While active, a full safety snapshot
runs every five minutes; only prepared/unknown/cancel-unknown outcomes require the
15-second recovery check. New exposure refreshes account state if its last read
started at least 15 seconds ago, and a failed refresh pauses submission.

Complete `ACCOUNT_UPDATE` events merge only named wallet assets and one-way
positions. Zero quantity removes that position. Wallet/cross-wallet values never
replace available balance: funding, transfers, untracked-symbol activity and order
changes mark free balance dirty. Multiple such events coalesce into one authoritative
balance read before the next new margin reservation. `ACCOUNT_CONFIG_UPDATE.ac`
updates actual leverage and adopted position metadata directly. Multi-assets mode
and hedge mode block new exposure without changing the user's mode; position mode
is checked again on reconnect. Independent mode holds cannot clear one another.

Complete regular/algo order events update their remaining quantities and original-ID
journal records. Normal known placement/cancel acknowledgements update the same
journal without querying all pending orders. Partial or missing acknowledgement
information uses original-ID lookup and affected-symbol reads. Query results never
replace fresher open-order quantities. A query/snapshot disagreement retains an
unknown hold. Unknown placement and unresolved cancellation retain conservative
reservations and never trigger automatic placement/cancel retries.

Order/entity time and receive-sequence tracking reject reversed or snapshot-covered
updates, while distinct changes sharing a millisecond still apply. Interleaved
`A → B → A` order duplicates produce neither another read nor another callback.
An identical same-millisecond entity reversion cannot be distinguished from replay;
that ambiguity reads only the affected position/order, free balance or configuration.
A REST read fences only the entities it actually queried. Events received during the
read merge afterward, so queued older data cannot replace the authoritative result.
Terminal acknowledgements/events cannot be undone by a delayed NEW update. Regular
and algo IDs have separate namespaces. Algo `ai` links the triggered parent to its
actual order; trigger is not fill, and actual terminal execution resolves the parent
regardless of which event domain arrives first. Realized profit is deduplicated by
trade ID, including delayed distinct partial trades; nonfinite numeric fields cause
recovery before poisoning strategy profit. `TRADE_LITE` is deliberately ignored,
so it cannot double count the ordinary trade event.

Hooks observe synchronized account state. Queued account events establish a fill's
position first; if it is missing, only that symbol's positions are read before the
hook. Protective rejection/trigger failure and `MARGIN_CALL` are explicitly reported
and hold new exposure in affected managed symbols until the position is flat.
Protection retains its risk-reducing route. State events still progress while hooks
await their own orders. REST reason/scope, event application and each entry lifecycle
are logged without raw payloads, credentials, signatures or listen keys.

Incomplete events and legacy adapters keep authoritative reconciliation as a safety
fallback. Startup/reconnect and the five-minute safety read retain full snapshots;
uncertain records retain the fifteen-second recovery check. These optimizations do
not alter strategy signal/stop rules, backtest visibility or callback invocation
counts for a given observed order status. See the [issue #36 coverage map](issue-36-coverage.md)
for the two PR scopes and executable acceptance cases.

### WebSocket routing

Mainnet Klines use `wss://fstream.binance.com/market/stream`. Account events use
`wss://fstream.binance.com/private/ws/<listenKey>` on a separate socket sharing
the SDK's aiohttp session and transport settings. The private socket delivers raw
events to the existing generation-bound runtime callback; it does not use the
SDK's market subscription protocol or log listen keys, private URLs or payloads.
Receiver exit, malformed events, expiry and socket errors signal the same recovery
supervisor. Shutdown cancels and awaits the private receiver and closes its socket
before retiring the SDK receivers, timers and session.

See Binance's [migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice)
and [user-stream connection contract](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams).
Testnet and custom endpoints retain their existing SDK routing. This migration has
not been validated against authenticated mainnet events or a testnet account.

Required symbol/interval subscriptions each track their own last update, including
forming-candle heartbeats. A new subscription generation gets a fresh first-update
grace period. Forming candles update freshness but never enter the strategy event
queue. Closed candles and user events have separate sequential state consumers, so a
strategy awaiting external work does not delay account synchronization or fill
hooks. A not-yet-sent entry waits inside the same submission call while account events or
refreshes are pending, without holding the order/REST locks. It revalidates available
balance, actual leverage, exchange filters and the latest received candle price.
The candle decision expires at the next scheduled close or when that close arrives,
even if the market worker is still awaiting the previous decision. A changed position
or entry order, shutdown, or connection recovery cancels the decision with `False`.
No strategy signal callback is replayed and no strategy-specific signal validator is
added: the signal is assumed valid only inside that candle interval. Non-candle hooks
have a 15-second deadline. Own confirmed submissions update the shared decision
state, including child tasks. Risk-reducing orders bypass entry waits/expiry and retain
protection checks. Account hooks run as owned tasks so a hook awaiting a new entry
cannot block account processing or another protection hook. Hooks start in event
order and may overlap across awaits; they must reread current synchronized state.
Each entry wait/result is logged with its reason, decision/state versions, stage,
elapsed time and counters; webhook deduplication does not hide these records.
Task cancellation propagates `CancelledError`; a pre-dispatch cancellation is logged
without treating dispatched outcomes as definitive cancellation. Shutdown drains
submission calls before closing the journal, without waiting for unrelated caller work.
Ordering is preserved within each queue. REST waits run in serialized worker-thread
calls; socket reception and timers continue while requests wait. Account updates
and orders share a lock to preserve snapshot/reservation consistency. Synchronous
callback and indicator work can still delay both consumers.

On receiver exit/error, stale market transport, listen-key expiry/keepalive failure,
or scheduled rotation, decisions pause. A fresh SDK stream instance is created;
owned old receivers/timers and identity-scoped subscriptions are retired first.
Retries begin at 1 second, increase exponentially, add jitter and cap at 60 seconds.
They continue until stopped. Reconciliation and continuous closed-candle backfill
must succeed before resuming. Historical gaps keep the runtime paused. Backfilled
candles rebuild indicators without replaying trading callbacks. Synchronization
checks exchange time again after REST/indicator loading and warms any candles that
closed through completion before activating decisions. A callback that was awaiting
work before disconnection retains its old generation: reconnect cannot authorize
its later managed placement or cancellation. Only a new callback receives the new
generation. Adapter failures during leverage change or post-send reconciliation
pause for infrastructure recovery; they do not latch a strategy-code failure.

Strategy `run`, indicator load, and order-hook failures latch the runtime as failed.
Network recovery cannot clear that latch. Repeated identical retry alerts are suppressed within each incident; each new
interruption/recovery cycle is reported again;
interruption, recovery, unknown outcome, failure and shutdown are logged and sent to
the configured webhook. Without a webhook URL, logging remains available.

## Stop and injection

Use `runner.run()` for the CLI/synchronous entry point. Ctrl-C completes cleanup.
Inside an existing loop, run `await runner.run_async()` and stop with `close()` or
`await runner.aclose()`. Cleanup is idempotent: it cancels/awaits owned tasks, closes
streams/listen key, and releases the journal lock. It never cancels exchange orders
or closes positions. Stop interrupts asynchronous backoff and prevents queued
requests from dispatching orders. A REST call already dispatched must finish before
cleanup releases the journal, including requests from strategy-created tasks.
Cancellation does not stop the underlying thread or authorize a retry; dispatched
placements/cancellations retain uncertain outcomes for reconciliation. Production defaults to
a 2-second per-request timeout, and a snapshot has multiple requests/read retries.

For offline execution inject an adapter, fresh `streams_factory`, a strategy object,
`ExecutionConfig`, symbols/intervals and a temporary journal path. The adapter must
provide bounded synchronous calls and a stable identity. Production strategy
constructors/load must not trade. Supplied preconstructed strategy objects are the
caller's responsibility. Raw SDK calls remain possible after initialization and
are outside journal/duplicate-prevention guarantees.

## Manual testnet checklist (not executed by automated tests)

Use a dedicated one-way testnet account and symbols, a stable writable journal path,
and a strategy with small explicit orders. Record exchange order IDs and client IDs
before/after each step. Do not use a real-money account for this checklist.

1. **Startup:** start with an existing entry order, protective close-position order,
   and position. Confirm all are adopted, no orders recreated, actual leverage kept.
2. **Preflight:** start a second process on the same journal and separately test hedge
   mode. Expect errors before leverage/order changes. Try an unwritable path and an
   account/key mismatch. No managed order should be sent.
3. **Leverage:** add to an existing position with a different configured leverage;
   confirm actual leverage remains. Flatten deliberately via operator/test strategy,
   retain an old entry order, then resolve that order. Confirm next entry waits until
   the old order is gone and the requested leverage is confirmed.
4. **Disconnect:** interrupt transport twice, including during a partial fill. Expect
   paused decisions, bounded repeated retry, fresh subscriptions, authoritative state,
   no duplicate realized-PNL notification, and automatic resume only after backfill.
5. **Backfill:** remain offline for more than 1000 base-interval candles (or use an
   isolated replay proxy). Confirm multiple pages, no current forming candle, and
   no historical trading callbacks. Introduce a missing page: expect continued pause.
6. **Response loss:** at a test proxy, accept placement then drop its response. Confirm
   one client ID, symbol hold and conservative reservation. Restart using the same DB;
   expect lookup/snapshot rather than placement resend. A temporary `-2013` stays held.
7. **Strategy failure:** throw from run/load/hook, then reconnect transport. Expect
   failure to remain latched and existing exchange orders/positions preserved.
8. **Shutdown/crash:** stop normally twice, then kill/restart a test process. Confirm
   local lock release, no lingering receive/rotation tasks or duplicate callbacks,
   journal recovery, and no automatic order cancellation/position closure.
9. **Notifications:** verify configured webhook delivery for interruption, recovery,
   unknown order and strategy failure; repeated identical retry failures are suppressed.

## Acceptance mapping

| AC | Automated evidence |
| --- | --- |
| 4.1–4.5 completion | `test_injected_fullrun_default_gateway_stop_preserves_orders`, `test_real_strategy_factory_receives_protected_complete_context`, adapter SDK model tests; injected path is the same runtime as default |
| 5.1 | `test_hedge_and_second_instance_fail_before_mutations`, journal identity tests |
| 5.2 | full-run/adoption tests, snapshot buffering test, algo string-zero/close-position model test |
| 5.3 | `test_actual_leverage_additions_then_flat_transition_after_old_orders`, `test_leverage_confirmation_failure_never_sends`, decimal filter transport test |
| 5.4 | two-recovery latch test, initial retry/stop test, real SDK receiver/rotation/connection health tests |
| 5.5 | history pagination >1000, fixed cutoff, gap/nonprogress tests and `test_recovery_backfills_indicators_and_only_calls_next_new_bar` |
| 5.6 | snapshot race, duplicate partial fill/stale NEW, ordered hooks/out-of-order distinct trade tests |
| 5.7 | strategy/run/load failure tests, malformed worker recovery test; unknown order does not latch strategy failure |
| 5.8 | full run stop, canceled-run task/lock cleanup, immediate real SDK creation/retirement and map tests |
| 5.9 | durable journal round-trip, prepared record and storage failure tests |
| 5.10 | accepted-timeout/restart/no-resend, receipt rejection rollback, cancellation-uncertainty tests |
| 5.11 | journal identity/read-only tests, restored unknown margin and subsequent lookup/snapshot |
| 5.12 | real multiprocess lock and abrupt OS-exit lock-release test |
| 5.13 | runtime alert state transitions, retry suppression and manual webhook checklist; webhook sends are mocked offline |

Offline tests do not constitute completed exchange/testnet validation. Direct SDK
calls, distributed ownership, exact fills, downtime PNL reconstruction and market
microstructure/cost modeling are outside this runtime's guarantees.

Managed live runtime wraps the configured synchronous webhook in an asynchronous
sender. Strategy hook signatures remain unchanged. Notifications are sent in order
from a bounded queue (128 messages); overflow logs a warning and skips the new
notification. Transport failures are logged without failing the strategy. Shutdown
drains queued notifications, including the shutdown notice. Injected webhooks must
provide bounded synchronous calls; production HTTP sends use transport timeouts.
