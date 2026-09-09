from __future__ import annotations

import pandas as pd

from open_binancian_futures import BacktestResult, BacktestSummary, EquityPoint
from open_binancian_futures.models import Balance
from open_binancian_futures.types import OrderType, PositionSide


def test_pending_margin_reservation_round_trips() -> None:
    balance = Balance(100.0)

    balance.reserve_margin(7, 10.0)
    assert balance.available == 90.0
    assert balance.reserved_margin == 10.0
    assert balance.reserved_margin_for(7) == 10.0

    balance.release_margin(7)
    assert balance.available == 100.0
    assert balance.reserved_margin == 0.0


def test_margin_consumption_reconciles_a_gap_fill_to_actual_position_margin() -> None:
    balance = Balance(100.0)
    balance.reserve_margin(7, 10.0)

    balance.consume_margin(7, actual_margin=12.0)

    assert balance.available == 88.0
    assert balance.reserved_margin == 0.0


def test_trade_ledger_preserves_exit_order_type_and_equity_curve() -> None:
    result = BacktestResult("ETHUSDT", evaluated_bars=3)
    result.record_trade(
        PositionSide.BUY,
        2.0,
        entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
        entry_price=100.0,
        exit_time=pd.Timestamp("2026-01-02", tz="UTC"),
        exit_price=102.0,
        quantity=1.0,
        exit_order_type=OrderType.TAKE_PROFIT_MARKET,
    )
    point = EquityPoint(pd.Timestamp("2026-01-02", tz="UTC"), 102.0)
    result.add_equity_point(point)

    assert result.trades[0].exit_order_type == OrderType.TAKE_PROFIT_MARKET
    assert result.trades[0].quantity == 1.0
    assert result.equity_curve == (point,)


def test_summary_is_pure_and_repeated_formatting_does_not_accumulate() -> None:
    result = BacktestResult("ETHUSDT", evaluated_bars=10)
    result.record_trade(PositionSide.BUY, 2.0)
    summary = BacktestSummary.from_results([result])

    first = summary.format()
    second = summary.format()

    assert first == second
    assert summary.trade_count == 1
    assert summary.pnl == 2.0
