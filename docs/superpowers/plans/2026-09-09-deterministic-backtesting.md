# Deterministic Backtesting Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`-`) syntax for tracking.

**Goal:** Make the package's default `Backtesting` runner deterministic, injectable, and compatible with the existing Binance-backed Strategy API.

**Architecture:** Add a focused `backtesting.py` domain module for candles, sources, intents, fills, account reservations, ledgers, metrics, and results. Keep live REST/websocket behavior in `runners.py` and `exchange.py`; the runner will use the domain module plus an adapter for legacy strategies that mutate `OrderBook`.

**Tech Stack:** Python 3.12, pandas, dataclasses, typing protocols, pytest, Binance derivatives SDK 7.1.1.

**Spec:** The deterministic backtesting requirements in the user task conversation.

## Global Constraints

- Current package version remains `26.2.3` unless packaging validation requires a version change.
- Verify Binance SDK compatibility with `7.1.1`; SDK enum types stay at the live adapter boundary.
- Default costs are zero fees, zero slippage, and zero funding.
- Stop Loss has priority over Take Profit on a candle that reaches both.
- New LIMIT/STOP orders cannot fill on their creation candle.
- Default Market execution uses the completed candle close; alternate execution is explicit.
- The last evaluated candle close realizes open positions.
- Do not move or modify `PATTERNS`, ADX/Bollinger/Chandelier/LLM behavior in `strategy_odesza.py`.
- Preserve `Strategy.run_backtest(symbol, interval, index)`, `Order`, `OrderList`, and no-argument `Backtesting()`.
- Work on `feat/deterministic-backtesting` in `/tmp/open-binancian-futures-deterministic`.

---

### Task 1: Establish domain contracts and public result API

**Files:**
- Create: `open_binancian_futures/backtesting.py`
- Create: `tests/test_backtesting_contract.py`
- Create: `tests/test_backtesting_models.py`
- Modify: `open_binancian_futures/models.py`
- Modify: `open_binancian_futures/__init__.py`

**Interfaces:**
- Public: `Candle`, `OrderIntent`, `ZeroCostModel`, `DeterministicFillPolicy`, `BacktestConfig`, `Trade`, `EquityPoint`, `BacktestResult`, `BacktestSummary`, and `BacktestRunResult`.
- `Candle.from_series(row)` reads `Open_time`, `Open`, `High`, `Low`, `Close`, and optional `Symbol`/`Volume`.
- `DeterministicFillPolicy.fill_price(order, candle)` returns a price or `None`; `select_exit(...) ` applies Stop Loss, Take Profit, then Market priority.
- `BacktestResult.expectancy` is `win_rate * average_win + loss_rate * average_loss`, where `average_loss` is negative and zero-PNL trades are break-even.

- [x] **Step 1: Write the failing API tests**

```python
def test_public_contract_and_candle():
    from open_binancian_futures import BacktestConfig, Candle, OrderIntent, ZeroCostModel
    from open_binancian_futures.types import OrderType, PositionSide

    assert BacktestConfig().cost_model == ZeroCostModel()
    assert Candle.from_series({
        "Open_time": "2026-01-01T00:00:00Z",
        "Open": 100, "High": 105, "Low": 95, "Close": 102,
    }).close == 102.0
    assert OrderIntent("ETHUSDT", PositionSide.BUY, OrderType.LIMIT, 100, 1).price == 100


def test_zero_pnl_is_not_a_loss_and_expectancy_is_signed():
    from open_binancian_futures import BacktestResult
    from open_binancian_futures.types import PositionSide

    result = BacktestResult("ETHUSDT", evaluated_bars=10)
    result.record_trade(PositionSide.BUY, 10.0)
    result.record_trade(PositionSide.BUY, -4.0)
    result.record_trade(PositionSide.BUY, 0.0)

    assert (result.win_count, result.loss_count, result.break_even_count) == (1, 1, 1)
    assert result.average_loss == -4.0
    assert result.expectancy == 2.0
```

- [x] **Step 2: Run the focused tests and verify the intended failure**

Run: `python3.12 -m pytest -q tests/test_backtesting_contract.py tests/test_backtesting_models.py`

Expected: collection or assertion failure because the new domain module and metrics do not exist.

- [x] **Step 3: Implement the minimum domain models and metrics**

Implement immutable candle/config/intent/value objects, zero-cost behavior, deterministic result properties, and pure summary aggregation. Keep compatibility aliases for `BacktestingResult` and `BacktestingSummary`.

- [x] **Step 4: Run the focused tests and verify green**

Run: `python3.12 -m pytest -q tests/test_backtesting_contract.py tests/test_backtesting_models.py`

Expected: all focused contract tests pass.

- [x] **Step 5: Commit**

```bash
git add open_binancian_futures/backtesting.py open_binancian_futures/models.py open_binancian_futures/__init__.py tests
git commit -m "feat: add deterministic backtesting domain contracts"
```

### Task 2: Add chronological sources and OHLC fill policy

**Files:**
- Modify: `open_binancian_futures/backtesting.py`
- Create: `tests/test_backtesting_fill_policy.py`
- Create: `tests/test_backtesting_data_source.py`
- Modify: `open_binancian_futures/exchange.py`

**Interfaces:**
- `HistoricalDataSource.load(symbols, intervals) -> Indicator`.
- `DataFrameDataSource`, `CsvDataSource`, and `ParquetDataSource` load OHLCV without credentials.
- `BinanceHistoricalDataSource` wraps the old REST `init_indicators` path.
- `normalize_ohlcv_frame` converts timestamps to UTC, validates OHLC, stably sorts, and rejects duplicates.
- `build_timeline(frames, warmup_bars, mode="intersection")` returns a sorted `DatetimeIndex`; union remains explicit.

- [x] **Step 1: Write failing gap/source tests**

```python
def test_gap_fills_use_candle_open_and_stop_wins():
    candle = Candle(pd.Timestamp("2026-01-01", tz="UTC"), 85, 95, 80, 90)
    policy = DeterministicFillPolicy()
    assert policy.fill_price(order(OrderType.LIMIT, PositionSide.BUY, 90), candle) == 85
    assert policy.fill_price(order(OrderType.STOP_MARKET, PositionSide.SELL, 90), candle) == 85

    candle = Candle(pd.Timestamp("2026-01-01", tz="UTC"), 100, 110, 90, 105)
    stop = order(OrderType.STOP_MARKET, PositionSide.SELL, 95)
    tp = order(OrderType.TAKE_PROFIT_MARKET, PositionSide.SELL, 105)
    assert policy.select_exit([tp, stop], candle, PositionSide.BUY)[0] is stop


def test_source_sorts_symbols_and_aligns_timestamps():
    loaded = DataFrameDataSource({"ETHUSDT": unsorted, "SOLUSDT": missing_one}).load(
        ["ETHUSDT", "SOLUSDT"], ["1h"]
    )
    assert list(build_timeline({s: loaded[s]["1h"] for s in loaded}, 1)) == expected
```

- [x] **Step 2: Run and verify the intended failures**

Run: `python3.12 -m pytest -q tests/test_backtesting_fill_policy.py tests/test_backtesting_data_source.py`

Expected: failures for missing source normalization, gap fills, and fixed priority.

- [x] **Step 3: Implement minimum sources and policy**

Use candle open for gaps, configured price for intrabar triggers, and fixed Stop → TP → Market priority. Default Market policy is `CLOSE`; `NEXT_OPEN` explicitly defers newly-created Market orders. Route complete candle evaluation through the policy while retaining the old two-argument `Order.is_filled` form.

- [x] **Step 4: Run and verify green**

Run: `python3.12 -m pytest -q tests/test_backtesting_fill_policy.py tests/test_backtesting_data_source.py`

Expected: all source/fill tests pass.

- [x] **Step 5: Commit**

```bash
git add open_binancian_futures/backtesting.py open_binancian_futures/exchange.py tests/test_backtesting_fill_policy.py tests/test_backtesting_data_source.py
git commit -m "feat: add deterministic candle fills and data sources"
```

### Task 3: Add pending margin, ledger, equity, and idempotent metrics

**Files:**
- Modify: `open_binancian_futures/models.py`
- Modify: `open_binancian_futures/backtesting.py`
- Create: `tests/test_backtesting_accounting.py`

**Interfaces:**
- `Balance.reserve_margin(order_id, amount)`, `release_margin(order_id)`, and `consume_margin(order_id, actual_margin)` provide reservation accounting without changing live `deduct`.
- `BacktestResult.record_trade` stores entry/exit metadata, quantity, side, order type, and signed PNL.
- `BacktestRunResult` exposes `by_symbol`, `summary`, `equity_curve`, and `final_balance`.
- `BacktestSummary.from_results(results)` is pure; repeated `format()`/summary printing is idempotent.

- [x] **Step 1: Write failing accounting tests**

```python
def test_pending_margin_reservation_round_trips():
    balance = Balance(100.0)
    balance.reserve_margin(7, 10.0)
    assert balance.available == 90.0
    assert balance.reserved_margin == 10.0
    balance.release_margin(7)
    assert balance.available == 100.0


def test_summary_does_not_accumulate_when_formatted_twice():
    result = BacktestResult("ETHUSDT", evaluated_bars=10)
    result.record_trade(PositionSide.BUY, 2.0)
    summary = BacktestSummary.from_results([result])
    assert summary.format() == summary.format()
    assert summary.trade_count == 1
```

- [x] **Step 2: Run and verify the intended failures**

Run: `python3.12 -m pytest -q tests/test_backtesting_accounting.py`

Expected: reservation methods and pure summary properties are absent.

- [x] **Step 3: Implement the reservation lifecycle and ledger**

Track reservation by order id, reconcile gap-fill margin to actual fill price, release on cancellation/expiry/exit, and expose break-even-aware metrics. Apply only zero cost and no funding.

- [x] **Step 4: Run and verify green**

Run: `python3.12 -m pytest -q tests/test_backtesting_accounting.py`

Expected: accounting, ledger, and summary tests pass.

- [x] **Step 5: Commit**

```bash
git add open_binancian_futures/models.py open_binancian_futures/backtesting.py tests/test_backtesting_accounting.py
git commit -m "feat: add backtest reservations and trade ledgers"
```

### Task 4: Replace the runner loop with the deterministic engine

**Files:**
- Modify: `open_binancian_futures/runners.py`
- Modify: `open_binancian_futures/strategy.py`
- Create: `tests/test_backtesting_runner.py`

**Interfaces:**
- `Backtesting(strategy=None, data_source=None, config=None)` preserves no-argument Binance behavior and avoids `client()`/REST initialization when injected source and strategy are supplied.
- `Backtesting.run() -> BacktestRunResult` and `await _run_backtest_loop() -> BacktestRunResult` process completed timestamps chronologically.
- `Backtesting.submit_order(intent) -> bool` is the new domain gateway; legacy direct `OrderList` mutations are discovered after each strategy callback.
- The existing callback remains `run_backtest(symbol, interval, index)`; synchronous and proven two-argument legacy forms are supported.
- New non-market orders are excluded from their creation candle; default close-executed Market orders may fill that candle.
- Finalization closes positions at each symbol's last evaluated close and records PNL.

- [x] **Step 1: Write failing runner tests**

```python
def test_injected_dataframe_needs_no_credentials(monkeypatch):
    monkeypatch.setattr("open_binancian_futures.runners.client", fail_if_called)
    runner = Backtesting(
        strategy=StrategyStub(...),
        data_source=DataFrameDataSource(frame),
        config=BacktestConfig(warmup_bars=0, initial_balance=100.0),
    )
    result = runner.run()
    assert result.final_balance == 100.0


def test_new_limit_and_stop_wait_until_a_later_candle():
    result = run_with_strategy_that_places_limit_then_stop(...)
    assert result.summary.trade_count == 1
    assert result.summary.trades[0].exit_time > result.summary.trades[0].entry_time


def test_final_close_realizes_position_and_closes_equity_curve():
    result = run_with_strategy_that_leaves_position_open(...)
    assert result.summary.pnl == 5.0
    assert result.equity_curve[-1].equity == result.final_balance
```

- [x] **Step 2: Run and verify old runner failures**

Run: `python3.12 -m pytest -q tests/test_backtesting_runner.py`

Expected: failures for injection, timestamp processing, deferral, final PNL, or result return.

- [x] **Step 3: Implement injected construction and chronological loop**

Load/normalize data before strategy construction in the injected path; bind balance/orders/positions/indicators and the gateway to supplied strategies. For each timestamp run the strategy on the completed candle, reserve newly observed legacy orders, evaluate eligible orders, mark-to-market, and append one equity point.

- [x] **Step 4: Implement order lifecycle and finalization**

Open at policy fill price, reconcile reservation, invoke optional `on_backtest_entry_filled` after entry, select exits with fixed priority, record trade metadata, release pending margins, and close remaining positions at the final close.

- [x] **Step 5: Run and verify green**

Run: `python3.12 -m pytest -q tests/test_backtesting_runner.py`

Expected: runner contract tests pass.

- [x] **Step 6: Commit**

```bash
git add open_binancian_futures/runners.py open_binancian_futures/strategy.py tests/test_backtesting_runner.py
git commit -m "feat: make backtesting chronological and deterministic"
```

### Task 5: Add the OrderIntent Strategy adapter and document migration

**Files:**
- Modify: `open_binancian_futures/strategy.py`
- Modify: `open_binancian_futures/models.py`
- Create: `tests/test_strategy_backtest_compatibility.py`
- Modify: `README.md`

**Interfaces:**
- `Strategy.order_intent(...) -> OrderIntent` creates a domain order without SDK enums.
- `await Strategy.submit_order(intent)` uses the injected backtest gateway or maps to the existing live REST call.
- Existing `Strategy.open_order(...)` remains callable with Binance SDK side enums and retains live behavior.
- Existing subclasses implementing `load`, `run`, and `run_backtest(symbol, interval, index)` continue to initialize and execute.

- [x] **Step 1: Write failing compatibility tests**

```python
async def test_domain_intent_uses_the_backtest_gateway():
    strategy = make_strategy_without_client()
    intent = strategy.order_intent(
        "ETHUSDT", PositionSide.BUY, OrderType.LIMIT, price=99.0, quantity=1.0
    )
    assert await strategy.submit_order(intent) is True
    assert strategy.orders["ETHUSDT"].find_by_type(OrderType.LIMIT) is not None


def test_existing_callback_signature_remains_public():
    assert list(inspect.signature(Strategy.run_backtest).parameters) == [
        "self", "symbol", "interval", "index"
    ]
```

- [x] **Step 2: Run and verify the intended failure**

Run: `python3.12 -m pytest -q tests/test_strategy_backtest_compatibility.py`

Expected: missing intent/gateway behavior.

- [x] **Step 3: Implement the adapter**

Keep SDK imports at the live boundary. Convert domain side/time-in-force to SDK enums only for the REST call; use the backtest gateway when bound. Do not alter strategy signal conditions.

- [x] **Step 4: Run and verify green**

Run: `python3.12 -m pytest -q tests/test_strategy_backtest_compatibility.py`

Expected: adapter and old callback tests pass.

- [x] **Step 5: Document the new API and migration**

Document `Backtesting(strategy=..., data_source=..., config=...)`, timing, `OrderIntent`, CSV/Parquet input, result access, and legacy fallback in `README.md`.

- [x] **Step 6: Commit**

```bash
git add open_binancian_futures/strategy.py open_binancian_futures/models.py tests/test_strategy_backtest_compatibility.py README.md
git commit -m "feat: expose strategy order intent adapter"
```

### Task 6: Add reference regressions and complete validation

**Files:**
- Create: `tests/test_backtesting_reference_regressions.py`
- Modify: `tests/test_backtesting_runner.py`
- Modify: `README.md`
- Optional: `pyproject.toml` only for a verified SDK 7.1.1 constraint.

- [x] **Step 1: Cover the complete contract matrix**

Assert LIMIT/STOP gap fills, same-candle prevention, Stop priority, timestamp alignment, actual-bar hit rate, final-close PNL, pending reservation, zero-PNL classification, expectancy, idempotent summary, credential-free injection, and existing Strategy API with small synthetic frames.

- [x] **Step 2: Run all new contract tests**

Run: `python3.12 -m pytest -q tests/test_backtesting_contract.py tests/test_backtesting_models.py tests/test_backtesting_fill_policy.py tests/test_backtesting_data_source.py tests/test_backtesting_accounting.py tests/test_backtesting_runner.py tests/test_strategy_backtest_compatibility.py tests/test_backtesting_reference_regressions.py`

Expected: exit code 0.

- [x] **Step 3: Run the complete package suite**

Run: `python3.12 -m pytest -q`

Expected: exit code 0.

- [x] **Step 4: Run lint, type, import, and compile checks**

```bash
python3.12 -m ruff check open_binancian_futures tests
python3.12 -m mypy open_binancian_futures
python3.12 -c "import open_binancian_futures; from open_binancian_futures import Backtesting, BacktestConfig, DeterministicFillPolicy, OrderIntent"
python3.12 -m compileall -q open_binancian_futures tests
```

Expected: lint/import/compile pass; report exact pre-existing typing gaps separately if mypy cannot pass against SDK stubs.

- [x] **Step 5: Run deterministic and reference comparisons**

Run the synthetic backtest twice and compare serialized ledgers/equity curves. Run the reference project's focused tests in the prepared Python 3.12 environment and compare fill prices and exit selection on identical candles.

- [x] **Step 6: Review the final diff**

```bash
git diff --check main...HEAD
git diff --stat main...HEAD
git diff main...HEAD -- open_binancian_futures/__init__.py open_binancian_futures/backtesting.py open_binancian_futures/runners.py open_binancian_futures/strategy.py README.md
git status --short --branch
```

Confirm no signal-condition changes, no injected-test credentials/network access, no accidental live behavior change, and no generated artifacts.

- [x] **Step 7: Commit final tests/docs**

```bash
git add tests README.md
git commit -m "test: lock deterministic backtesting behavior"
```

## Self-review coverage

- Timestamp ordering and per-symbol alignment: Tasks 2 and 4.
- Data injection and no credentials: Tasks 2 and 4.
- Completed-candle timing and same-candle protection: Task 4.
- Gap fills and Stop-over-TP priority: Task 2.
- Pending margin: Tasks 3 and 4.
- Final-close PNL, ledger, equity, metrics, zero-PNL, expectancy: Tasks 1, 3, and 4.
- Idempotent summary and public result API: Tasks 1 and 3.
- OrderIntent and Strategy compatibility: Task 5.
- Reference comparison and validation: Task 6.
