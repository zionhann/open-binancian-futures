"""Selected PR18 review regressions; no exchange connections."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from test_live_runtime import Adapter, Streams, SYMBOL, candle, gateway, runtime
from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.models import Balance, Order, OrderIntent
from open_binancian_futures.types import OrderType, PositionSide


@pytest.mark.asyncio
async def test_regular_fill_does_not_suppress_algo_with_same_id(tmp_path):
    runner, _ = runtime(tmp_path)
    runner.gateway = SimpleNamespace(reconcile=lambda: None)
    runner.orders = runner.adapter.state.orders
    hooks = []
    runner.strategy.on_triggered_algo = lambda event: hooks.append(event.source.value)
    await runner._user(
        {
            "e": "ORDER_TRADE_UPDATE",
            "T": 20,
            "o": {
                "s": SYMBOL,
                "i": 7,
                "t": 12,
                "z": "1",
                "rp": "0",
                "X": "PARTIALLY_FILLED",
            },
        }
    )
    await runner._user(
        {
            "e": "ALGO_UPDATE",
            "T": 30,
            "o": {
                "s": SYMBOL,
                "aid": 7,
                "X": "TRIGGERED",
                "o": "STOP_MARKET",
                "S": "SELL",
            },
        }
    )
    assert hooks == ["ALGO_UPDATE"]


@pytest.mark.asyncio
async def test_new_hooks_require_matching_order_domain_and_deduplicate_separately(
    tmp_path,
):
    runner, _ = runtime(tmp_path)
    runner.gateway = SimpleNamespace(reconcile=lambda: None)
    runner.orders = runner.adapter.state.orders
    runner.orders[SYMBOL].add(
        Order(SYMBOL, 7, OrderType.LIMIT, PositionSide.BUY, 100, 1)
    )
    hooks = []
    runner.strategy.on_new_order = lambda event: hooks.append(event.source.value)
    algo = {
        "e": "ALGO_UPDATE",
        "T": 10,
        "o": {"s": SYMBOL, "aid": 7, "X": "NEW", "o": "STOP_MARKET", "S": "SELL"},
    }
    regular = {
        "e": "ORDER_TRADE_UPDATE",
        "T": 10,
        "o": {"s": SYMBOL, "i": 7, "X": "NEW", "o": "LIMIT", "S": "BUY"},
    }
    await runner._user(algo)
    assert hooks == []  # Regular order presence cannot establish algo presence.
    runner.orders[SYMBOL].add(
        Order(SYMBOL, 7, OrderType.STOP_MARKET, PositionSide.SELL, 90, 1)
    )
    await runner._user(regular)
    await runner._user(algo)
    await runner._user(regular)
    await runner._user(algo)
    assert hooks == ["ORDER_TRADE_UPDATE", "ALGO_UPDATE"]


@pytest.mark.asyncio
async def test_any_required_subscription_silence_triggers_recovery(tmp_path):
    now = [0.0]
    runner, _ = runtime(tmp_path, clock=lambda: now[0])
    runner.intervals = ("1m", "5m")
    await runner._connect()

    async def one_tick(delay):
        now[0] = 100
        data = candle()
        data["k"]["x"] = False
        runner._enqueue(runner.generation, data)
        runner.stop_event.set()

    runner.sleep = one_tick
    await runner._watch()
    assert runner.recovery.is_set()
    await runner.aclose()


def test_forming_bars_update_only_target_heartbeat_without_queueing(tmp_path):
    now = [0.0]
    runner, _ = runtime(tmp_path, clock=lambda: now[0])
    now[0] = 10
    forming = candle()
    forming["k"]["x"] = False
    for _ in range(1000):
        runner._enqueue(runner.generation, forming)
    assert runner.queue.empty()
    assert runner._last_market[(SYMBOL, "1m")] == 10
    now[0] = 20
    foreign = candle()
    foreign["k"]["s"] = "ETHUSDT"
    runner._enqueue(runner.generation, foreign)
    runner._enqueue(runner.generation - 1, forming)
    assert runner._last_market[(SYMBOL, "1m")] == 10 and runner.queue.empty()
    closed = candle()
    user = {"e": "ACCOUNT_UPDATE", "a": {"B": []}}
    runner._enqueue(runner.generation, closed)
    runner._enqueue(runner.generation, user)
    assert runner.queue.get_nowait()[1] is closed
    assert runner.queue.get_nowait()[1] is user


@pytest.mark.asyncio
async def test_flat_entry_sizes_after_target_leverage_confirmation(tmp_path):
    adapter = Adapter()
    adapter.state.balance = Balance(10)
    adapter.state.leverage[SYMBOL] = 1
    managed, journal = gateway(
        tmp_path, adapter, ExecutionConfig(leverage=20, position_size=0.1)
    )
    try:
        assert await managed.submit_order(
            OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100)
        )
        assert adapter.mutations[0] == ("leverage", 20)
        assert adapter.mutations[1][1].quantity == 0.2
    finally:
        journal.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price": -1},
        {"price": 0.001},
        {"price": 100, "quantity": 0.001},
        {"price": 100, "gtd": 1, "time_in_force": "GTC"},
        {"price": 100, "close_position": True},
    ],
)
@pytest.mark.asyncio
async def test_invalid_intent_does_not_change_leverage(tmp_path, kwargs):
    adapter = Adapter()
    adapter.state.leverage[SYMBOL] = 1
    managed, journal = gateway(tmp_path, adapter, ExecutionConfig(leverage=20))
    try:
        with pytest.raises(ValueError):
            await managed.submit_order(
                OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, **kwargs)
            )
        assert adapter.mutations == [] and not journal.pending()
    finally:
        journal.close()


def test_without_fcntl_import_backtest_works_and_journal_open_explains_platform(
    tmp_path,
):
    script = r"""
import builtins
original=builtins.__import__
def without_fcntl(name,*args,**kwargs):
    if name=='fcntl':raise ModuleNotFoundError("No module named 'fcntl'")
    return original(name,*args,**kwargs)
builtins.__import__=without_fcntl
from open_binancian_futures import BacktestConfig, Backtesting
from open_binancian_futures.live_journal import OrderJournal
assert BacktestConfig().leverage>0
import sys
try:OrderJournal(sys.argv[1],'test').open()
except RuntimeError as error:
    assert 'POSIX' in str(error)
else:raise AssertionError('Unsupported journal must fail')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(tmp_path / "unavailable" / "orders.sqlite3"),
        ],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "unavailable").exists()


@pytest.mark.asyncio
async def test_subscription_grace_resets_for_new_generation(tmp_path):
    now = [0.0]
    runner, _ = runtime(tmp_path, clock=lambda: now[0])
    await runner._connect()
    old_generation = runner.generation
    now[0] = 89

    async def tick(delay):
        runner.stop_event.set()

    runner.sleep = tick
    await runner._watch()
    assert not runner.recovery.is_set()  # First-message grace.
    runner.stop_event.clear()
    await runner._retire()
    now[0] = 1000
    await runner._connect()
    assert runner._last_market[(SYMBOL, "1m")] == 1000
    now[0] = 1010
    runner._enqueue(old_generation, candle())
    assert runner._last_market[(SYMBOL, "1m")] == 1000
    await runner._watch()
    assert not runner.recovery.is_set()
    await runner.aclose()


@pytest.mark.asyncio
async def test_slow_strategy_does_not_accumulate_forming_bar_backlog(tmp_path):
    from test_live_runtime import eventually

    runner, streams = runtime(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(*args):
        entered.set()
        await release.wait()

    runner.strategy.run = slow
    task = asyncio.create_task(runner.run_async())
    await eventually(lambda: runner.active)
    streams[-1].emit(candle())
    await entered.wait()
    forming = candle(120000)
    forming["k"]["x"] = False
    for _ in range(1000):
        streams[-1].emit(forming)
    try:
        assert runner.queue.empty()
        streams[-1].emit(candle(120000))
        assert runner.queue.qsize() == 1
    finally:
        release.set()
        runner.close()
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        OrderIntent(
            SYMBOL, PositionSide.BUY, OrderType.MARKET, 100, time_in_force="GTC"
        ),
        OrderIntent(SYMBOL, PositionSide.BUY, OrderType.MARKET, 100, gtd=123),
        OrderIntent(
            SYMBOL,
            PositionSide.SELL,
            OrderType.STOP_MARKET,
            90,
            close_position=True,
            reduce_only=True,
        ),
    ],
)
async def test_adapter_semantic_conflicts_fail_before_leverage_or_journal(
    tmp_path, intent
):
    adapter = Adapter()
    adapter.state.leverage[SYMBOL] = 1
    managed, journal = gateway(tmp_path, adapter, ExecutionConfig(leverage=20))
    try:
        with pytest.raises(ValueError):
            await managed.submit_order(intent)
        assert not adapter.mutations and not journal.pending()
    finally:
        journal.close()
