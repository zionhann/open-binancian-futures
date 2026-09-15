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

The existing position's actual symbol leverage also applies to additions and stop
calculations. After the position becomes flat, old entry orders hold back a new
leverage setting. Once those orders are gone, the next entry confirms the requested
leverage at the exchange before it sends an order. Protective close-position algo
orders count as reduce-only and do not hold back this transition.

The journal stores generated client IDs, normalized intents, margin and processing
state in SQLite with FULL synchronous commits before each send. It stores no API
keys or secrets. A different endpoint/key cannot silently reuse it. API key rotation
therefore requires an operator to reconcile existing records before choosing a new
journal; do not simply delete unresolved records to unblock trading.

The OS releases the lock after a crash. The lock excludes processes using the same
resolved path, not different paths, machines, symlink aliases to the DB file itself,
or a distributed fleet. Use a local filesystem with working POSIX locks and SQLite
durability. Do not place the journal in source control; default DB files are ignored.

## Uncertain orders and recovery

Timeout, disconnection, malformed response and query not-found are unknown outcomes,
not `False`. `OrderOutcomeUnknown` holds the symbol and its reservation. Prepared,
unknown and accepted records are looked up by their original client IDs after
restart. They are never automatically submitted again. A successful lookup is
followed by a fresh account snapshot before the hold can clear. Unknown records
are queried again on account activity and the periodic 15-second reconciliation.
A cancellation timeout similarly remains held while the order is still open; the
runtime does not blindly repeat cancellation. Definitive rejection rolls back once.

Unknown reservations are conservatively deducted from fresh free balance even if
the exchange may already reserve that money. This can temporarily understate free
balance, deliberately preventing reuse while the outcome remains unresolved.
Accepted orders/positions instead use exchange-reported available balance.

Every account/order event triggers an authoritative REST snapshot. Event `cw`
(cross-wallet balance) never replaces free balance. Event order quantities never
replace snapshot quantities. Per-order versions/cumulative progress reject backward
state movement; trade IDs deduplicate realized-PNL notifications. Delayed distinct
partial trades can contribute their own PNL once even when received out of order.
No past trade notification is fabricated for fills missed entirely during downtime.
Sync/async notification hooks observe fresh state and are deduplicated by status;
base hooks do not overwrite it. Strategy trading remains driven by new closed bars.

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
or closes positions. Stop interrupts asynchronous backoff. A synchronous REST call
already executing must finish before cleanup can continue; production defaults to
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
