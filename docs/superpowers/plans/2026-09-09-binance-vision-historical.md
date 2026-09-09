# Binance Vision Historical Backtesting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the deterministic backtesting engine load a user-selected Binance USDⓈ-M historical period from Binance Vision and expose the same choice through the existing backtest CLI.

**Architecture:** Keep live REST/WebSocket execution unchanged. Add a Binance Vision archive data source that resolves monthly ZIP archives first, falls back to daily archives when a monthly archive is unavailable, parses Binance's 12-column kline schema, caches downloaded archives, and filters an explicit UTC period. The runner will use Vision for the default backtest path when dates are configured; injected DataFrames and the existing REST source remain available as explicit APIs during migration.

**Tech Stack:** Python 3.12+, pandas, urllib, zipfile, hashlib, Typer, pytest.

**Spec:** User request in the conversation: replace REST historical candles with Binance Vision while retaining credential-backed exchange metadata where needed.

## Global Constraints

- Preserve `--backtest`/`--live`, `--symbols`, and `--intervals` CLI behavior.
- The first interval remains the execution interval; additional configured intervals remain indicator context.
- `start_date` is inclusive; date-only `end_date` is inclusive through 23:59:59.999999 UTC.
- Vision loads `warmup_bars` of context before `start_date`; the runner evaluates only the requested period after warm-up.
- Never silently fall back from Vision candle loading to REST candle loading.
- Preserve the existing live trading path and strategy conditions.
- Keep exchange metadata loading separate from historical candle loading.

### Task 1: Vision archive source contracts and parser tests

**Files:**
- Create: `tests/test_binance_vision_data_source.py`
- Modify: `open_binancian_futures/backtesting.py` only if a public type needs a test import

**Interfaces:**
- `BinanceVisionDataSource(start_date, end_date, data_dir, ...)` loads `Indicator` frames through `load(symbols, intervals)`.
- The test downloader receives a URL and returns ZIP bytes, allowing URL resolution and parsing to be tested without external state.

- [x] Write failing tests for URL generation, header/no-header Binance kline parsing, monthly-first selection, daily fallback, date filtering, cache reuse, and invalid periods.
- [x] Run `pytest -q tests/test_binance_vision_data_source.py` and confirm failure because the source is not present.
- [x] Commit only the tests if the repository workflow requires task-sized commits.

### Task 2: Implement Binance Vision archive loading

**Files:**
- Modify: `open_binancian_futures/backtesting.py`
- Modify: `open_binancian_futures/__init__.py`
- Test: `tests/test_binance_vision_data_source.py`

**Interfaces:**
- Add public `BinanceVisionDataSource` with `start_date`, `end_date`, optional `data_dir`, `market`, `base_url`, `timeout`, and injectable downloader.
- Normalize Vision rows into the package's `Open_time`, `Open`, `High`, `Low`, `Close`, `Volume`, and `Close_time` columns.

- [x] Implement UTC period parsing and validation.
- [x] Implement monthly archive URL resolution and daily fallback on not-found responses only.
- [x] Implement deterministic cache paths and ZIP member selection.
- [x] Implement parsing for both headered and headerless archives and normalize/sort/filter frames.
- [x] Run source tests and verify they pass.

### Task 3: Integrate Vision dates and intervals into settings, runner, and CLI

**Files:**
- Modify: `open_binancian_futures/constants.py`
- Modify: `open_binancian_futures/runners.py`
- Modify: `open_binancian_futures/cli.py`
- Modify: `tests/test_backtesting_runner.py`
- Create: `tests/test_cli_backtesting.py`

**Interfaces:**
- Add `BACKTEST_START_DATE`, `BACKTEST_END_DATE`, and `BACKTEST_DATA_DIR` settings.
- Add CLI options `--start-date`, `--end-date`, and `--data-dir`.
- `Backtesting()` without an injected source uses `BinanceVisionDataSource` when the period is configured and does not use REST historical klines.

- [x] Write failing tests for default Vision source selection, required period validation, configured symbols/intervals propagation, and unchanged `--backtest`/`--live` flags.
- [x] Run the focused tests and confirm expected failures.
- [x] Implement source selection and pass all configured intervals to Vision while retaining the first interval as execution interval.
- [x] Preserve credential-backed strategy/exchange metadata initialization without using REST candle history.
- [x] Run focused runner and CLI tests.

### Task 4: Documentation and regression verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-09-09-binance-vision-historical.md`

- [x] Document the Vision URL layout, cache behavior, date semantics, CLI examples, metadata credential behavior, and explicit legacy REST source status.
- [x] Run the full package test suite, changed-file lint, type check, import check, and a synthetic Vision-source backtest.
- [ ] Inspect the branch diff and record remaining limitations.
