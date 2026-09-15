# Framework Reliability Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Read the approved spec and this plan before editing. Each task has its own branch and PR; do not merge.

**Goal:** Execute approved work 1–5 with traceable AC and network-free regression tests.

**Architecture:** Keep user signal logic outside the framework. Introduce an injectable exchange boundary, a durable managed-order gateway, and a supervised live session; retain the current strategy callbacks with bounded candle views.

**Tech Stack:** Python 3.12/3.13, pytest, pandas, Binance SDK 7.1.1, SQLite, asyncio.

**Spec:** `docs/superpowers/specs/2026-09-15-framework-reliability-design.md`

## Global constraints

- No user strategy, credentials, account data, or runtime database in commits.
- No real account operations; SDK calls are tested with transport doubles.
- Single direction only. Existing exchange orders and positions survive stop/restart.
- Tests first for behavioral changes; document failing evidence and verification in the progress ledger.
- Python 3.12 and 3.13 CI; pinned SDK 7.1.1 remains unchanged.
- Base is 737fbc9. Follow-up branches are stacked when predecessor is not merged.

## Task 1: Validate execution inputs (AC-1)

Files: `execution.py`, `backtesting.py`, `constants.py`, new `tests/test_input_validation.py`.

Interfaces: `ExecutionConfig`/`BacktestConfig` field order remains unchanged. Validation helpers reject bool/non-integral integer settings and nonfinite numeric settings. Environment integer strings remain accepted as exact integers.

- [x] Add parameterized failure tests before implementation:

```python
@pytest.mark.parametrize("value", [True, 1.5, float("nan"), float("inf"), 0, -1])
def test_invalid_leverage(value):
    with pytest.raises(ValueError, match="leverage"):
        ExecutionConfig(leverage=value)
```

- [x] Cover initial balance, position size, warmup, environment conversion, field-specific messages and accepted boundaries.
- [x] Run `python -m pytest tests/test_input_validation.py -q`, observe failure, implement shared validation, run full suite.
- [x] Commit `fix(execution): reject invalid runtime numeric settings`; open PR against main with docs and AC results.

## Task 2: Repository-owned quality gate (AC-2)

Files: `pyproject.toml`, `.github/workflows/ci.yml`, existing package lint/type findings.

- [x] Record baseline with explicit config: `python -m ruff check --config pyproject.toml open_binancian_futures`; `python -m mypy --config-file pyproject.toml open_binancian_futures`.
- [x] Configure F/E4/E7/E9/I/UP rules, Python 3.12, explicit narrow external-module typing exceptions. No global ignores for package errors.
- [x] Correct annotations/imports without changing order behavior. Use pandas index types explicitly rather than suppressing errors.
- [x] CI installs package dev dependencies, runs pip check and pytest on 3.12/3.13, and runs the same quality commands.
- [x] Run all gates, commit `chore(ci): enforce repository lint and type checks`; open stacked PR.

## Task 3: Causal backtest execution (AC-3)

Files: `backtesting.py`, `runners.py`, strategy binding, tests and migration docs.

Interfaces: default `MarketExecutionPolicy.NEXT_OPEN`; explicit CLOSE retained. Add optional `Trade.exit_reason` with default preserving existing construction. `run_backtest(symbol, interval, index)` receives a truncated indicator view; index addresses its last execution candle. Plain two-argument strategies remain supported.

- [x] Add regressions using fixed OHLC data: prior limit fills before close-based cancellation; market entry fills at next open; last market intent never fills; final liquidation reason is `end_of_backtest`.
- [x] Add a strategy that reads all symbol/interval tail timestamps and assert each close time is <= decision time. A later hour's value must never appear in an earlier 5m decision.
- [x] Refactor loop into phases: expire/fill existing orders for every symbol; construct visible copies using completed close boundaries; call strategies in fixed symbol order; register new orders; fill explicit CLOSE market orders; mark equity. Keep engine frames private and restore full engine state in finally.
- [x] Recompute indicators from bounded visible copies at each decision/hook. The approved strict visibility contract supersedes full-history precomputation; document the potential quadratic cost and side-effect-free `load()` migration.
- [x] Run focused and full tests, adapting legacy tests to request CLOSE only when close execution is the tested historical contract. Update README migration examples.
- [x] Commit `fix(backtesting)!: enforce causal candle execution`; open stacked PR.

## Task 4: Injectable exchange boundary (AC-4)

Files: new `exchange_adapter.py`, `strategy.py`, `exchange.py`, `runners.py`, new adapter tests.

Interfaces: `ExchangeAdapter` owns sync snapshot/submit/query/cancel/leverage calls and async stream connect/close/subscribe; `BinanceExchangeAdapter` wraps injected SDK client. `OrderOutcomeUnknown` distinguishes ambiguous result from confirmed rejection. A strategy receives a managed gateway through context; explicit raw SDK access remains an escape hatch outside guarantees.

- [x] Write contract tests with real SDK response models and a narrow fake REST transport. Assert requested enum/boolean/identifier fields for regular and algo orders, not merely call counts.
- [x] Inspect installed SDK signatures for position mode, regular/algo client IDs, query, stream handlers; do not invent SDK parameters.
- [x] Implement adapter and explicit client injection in exchange initializers. No injected path may fall back to the global singleton.
- [x] Route supported common order helpers through the gateway, preserving sizing, filters, and public signatures. Enumerate guaranteed helpers in documentation; reject unsupported managed semantics clearly.
- [x] Full suite and quality gates, commit `refactor(exchange): inject SDK adapter and order gateway`; open stacked PR.

## Task 5: Durable supervised live execution (AC-5)

Files: new `live.py`, `live_journal.py`, `managed_orders.py`, `live_history.py`, `sdk_streams.py`, adapter additions, runtime settings, lifecycle tests, operations docs.

Interfaces: `LiveTrading` accepts injected adapter and policy. Runtime states are STARTING/RECOVERING/RUNNING/STRATEGY_FAILED/STOPPED. `OrderJournal(path, identity)` uses SQLite and an OS lock. `ManagedOrderGateway.submit_order(intent)` records before send; confirmed rejection releases reserved margin, unknown outcome persists and raises `OrderOutcomeUnknown`, accepted result returns true. Journal statuses prepared/unknown/accepted/rejected are durable and account-bound without saving secrets.

- [x] Write journal tests: second process lock denied, crash unlock, account mismatch, write failure before send, persisted unresolved intent after reopening.
- [x] Write gateway tests: accepted-but-timeout then query success, repeated not-found stays unresolved, terminal rejection rolls back once, duplicate event doesn't repeat PNL, blocked symbol never submits another order.
- [x] Implement identity from stable account configuration fingerprint (one-way digest; no raw key storage); enforce explicit state path and deny identity mismatch. `fcntl` lock released on close/process exit; use platform-compatible locking or reject unsupported platform clearly.
- [x] Test live startup with fake adapter: hedge rejection before mutations, full state restored before callback, existing orders untouched, per-symbol leverage retained until flat and prior entry orders absent.
- [x] Test supervisor failures with injected clock/sleep: recovery delay 1..60 seconds with jitter; cancel/await child tasks on shutdown; streams and heartbeat owned by one event loop; no silent task exceptions.
- [x] Reconnect buffers events while querying snapshot, reconciles unresolved IDs, backfills missing candles without callbacks, resumes only on a post-sync candle. Retry interruption and shutdown must not wait for the backoff cap.
- [x] Strategy exceptions latch failure, keep remote orders, and never resume due to network recovery. Unknown-order exceptions block the affected symbol and remain recovery-managed rather than becoming user-strategy errors.
- [x] Provide safe default SQLite location under a documented runtime directory, ignore runtime files, and expose environment/constructor override. Persist only minimal intent state; report states without credentials.
- [x] Add testnet checklist for start/adopt/reconnect/restart/stop without executing live operations. Map every AC to tests/documentation in a completion ledger.
- [x] Full suite and quality checks, review against every AC, commit `feat(live): supervise recovery and persist managed orders`; open stacked PR.

## Delivery verification

- [x] Independently review each task's spec compliance and patch.
- [x] CI checks all five current heads. Record exact SHA and links.
- [x] No PR merge or deployment. Report any unmet AC explicitly.

Final evidence and AC mapping: [reliability-verification.md](../../reliability-verification.md). Task 5 source passed independent re-review at `b45b1e9` with 253 full tests passing.
