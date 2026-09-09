from __future__ import annotations

import pandas as pd


def test_public_contract_and_candle() -> None:
    from open_binancian_futures import (
        BacktestConfig,
        Candle,
        OrderIntent,
        ZeroCostModel,
    )
    from open_binancian_futures.types import OrderType, PositionSide

    assert BacktestConfig().cost_model == ZeroCostModel()
    candle = Candle.from_series(
        {
            "Open_time": "2026-01-01T00:00:00Z",
            "Open": 100,
            "High": 105,
            "Low": 95,
            "Close": 102,
        }
    )
    assert candle.close == 102.0
    assert candle.time == pd.Timestamp("2026-01-01", tz="UTC")
    intent = OrderIntent(
        symbol="ETHUSDT",
        side=PositionSide.BUY,
        order_type=OrderType.LIMIT,
        price=100.0,
        quantity=1.0,
    )
    assert intent.price == 100.0


def test_public_result_contains_trade_and_equity_data() -> None:
    from open_binancian_futures import (
        BacktestResult,
        EquityPoint,
        Trade,
    )

    result = BacktestResult("ETHUSDT", evaluated_bars=10)
    result.record_trade(
        side="BUY",
        pnl=2.0,
        entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
        entry_price=100.0,
        exit_time=pd.Timestamp("2026-01-02", tz="UTC"),
        exit_price=102.0,
        quantity=1.0,
    )
    assert isinstance(result.trades[0], Trade)
    assert result.trades[0].pnl == 2.0
    point = EquityPoint(pd.Timestamp("2026-01-02", tz="UTC"), 102.0)
    result.add_equity_point(point)
    assert result.equity_curve == (point,)

