from __future__ import annotations

import pandas as pd
import pytest

from open_binancian_futures import (
    Candle,
    DeterministicFillPolicy,
    MarketExecutionPolicy,
)
from open_binancian_futures.models import Order
from open_binancian_futures.types import OrderType, PositionSide


def make_order(order_type: OrderType, side: PositionSide, price: float) -> Order:
    return Order(
        symbol="ETHUSDT",
        order_id=1,
        type=order_type,
        side=side,
        price=price,
        quantity=1.0,
    )


def test_limit_and_stop_gap_fill_at_candle_open() -> None:
    candle = Candle(
        pd.Timestamp("2026-01-01", tz="UTC"),
        open=85.0,
        high=95.0,
        low=80.0,
        close=90.0,
    )
    policy = DeterministicFillPolicy()

    assert (
        policy.fill_price(
            make_order(OrderType.LIMIT, PositionSide.BUY, 90.0), candle
        )
        == 85.0
    )
    assert (
        policy.fill_price(
            make_order(OrderType.STOP_MARKET, PositionSide.SELL, 90.0), candle
        )
        == 85.0
    )


def test_intrabar_orders_fill_at_their_configured_price() -> None:
    candle = Candle(
        pd.Timestamp("2026-01-01", tz="UTC"),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
    )
    policy = DeterministicFillPolicy()

    assert (
        policy.fill_price(
            make_order(OrderType.LIMIT, PositionSide.BUY, 95.0), candle
        )
        == 95.0
    )
    assert (
        policy.fill_price(
            make_order(OrderType.STOP_MARKET, PositionSide.BUY, 105.0), candle
        )
        == 105.0
    )


def test_stop_limit_gap_preserves_the_limit_price_constraint() -> None:
    gap = Candle(
        pd.Timestamp("2026-01-01", tz="UTC"),
        open=90.0,
        high=94.0,
        low=85.0,
        close=92.0,
    )
    rebound = Candle(
        pd.Timestamp("2026-01-02", tz="UTC"),
        open=90.0,
        high=95.0,
        low=85.0,
        close=93.0,
    )
    order = make_order(OrderType.STOP_LIMIT, PositionSide.SELL, 95.0)
    policy = DeterministicFillPolicy()

    assert policy.fill_price(order, gap) is None
    assert policy.fill_price(order, rebound) == 95.0


@pytest.mark.parametrize(
    ("side", "gap", "later_candle", "expected_fill"),
    [
        (
            PositionSide.BUY,
            Candle(
                pd.Timestamp("2026-01-01", tz="UTC"),
                open=100.0,
                high=105.0,
                low=96.0,
                close=100.0,
            ),
            Candle(
                pd.Timestamp("2026-01-02", tz="UTC"),
                open=90.0,
                high=91.0,
                low=89.0,
                close=90.0,
            ),
            90.0,
        ),
        (
            PositionSide.SELL,
            Candle(
                pd.Timestamp("2026-01-01", tz="UTC"),
                open=90.0,
                high=94.0,
                low=85.0,
                close=90.0,
            ),
            Candle(
                pd.Timestamp("2026-01-02", tz="UTC"),
                open=100.0,
                high=101.0,
                low=100.0,
                close=100.0,
            ),
            100.0,
        ),
    ],
)
def test_stop_limit_trigger_remains_active_after_gap(
    side: PositionSide,
    gap: Candle,
    later_candle: Candle,
    expected_fill: float,
) -> None:
    order = make_order(OrderType.STOP_LIMIT, side, 95.0)
    policy = DeterministicFillPolicy()

    assert policy.fill_price(order, gap) is None
    assert policy.fill_price(order, later_candle) == expected_fill


def test_stop_loss_wins_when_stop_and_take_profit_both_trigger() -> None:
    candle = Candle(
        pd.Timestamp("2026-01-01", tz="UTC"),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
    )
    stop = make_order(OrderType.STOP_MARKET, PositionSide.SELL, 95.0)
    take_profit = make_order(OrderType.TAKE_PROFIT_MARKET, PositionSide.SELL, 105.0)

    selected = DeterministicFillPolicy().select_exit(
        [take_profit, stop], candle, PositionSide.BUY
    )

    assert selected is not None
    assert selected[0] is stop
    assert selected[1] == 95.0


def test_market_policy_defaults_to_completed_candle_close() -> None:
    candle = Candle(
        pd.Timestamp("2026-01-01", tz="UTC"),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
    )
    market = make_order(OrderType.MARKET, PositionSide.SELL, 0.0)

    assert DeterministicFillPolicy().fill_price(market, candle) == 105.0
    assert (
        DeterministicFillPolicy(market_execution=MarketExecutionPolicy.NEXT_OPEN)
        .market_execution
        == MarketExecutionPolicy.NEXT_OPEN
    )
