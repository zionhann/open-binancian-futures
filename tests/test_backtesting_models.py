from __future__ import annotations

from open_binancian_futures.types import PositionSide


def test_zero_pnl_is_not_a_loss_and_expectancy_is_signed() -> None:
    from open_binancian_futures import BacktestResult

    result = BacktestResult("ETHUSDT", evaluated_bars=10)
    result.record_trade(PositionSide.BUY, 10.0)
    result.record_trade(PositionSide.BUY, -4.0)
    result.record_trade(PositionSide.BUY, 0.0)

    assert (result.win_count, result.loss_count, result.break_even_count) == (1, 1, 1)
    assert result.average_win == 10.0
    assert result.average_loss == -4.0
    assert result.expectancy == 2.0
    assert result.hit_rate == 0.0


def test_summary_is_pure_and_repeated_formatting_does_not_accumulate() -> None:
    from open_binancian_futures import BacktestResult, BacktestSummary

    result = BacktestResult("ETHUSDT", evaluated_bars=10)
    result.record_trade(PositionSide.BUY, 2.0)
    summary = BacktestSummary.from_results([result])

    first = summary.format()
    second = summary.format()

    assert first == second
    assert summary.trade_count == 1
    assert summary.pnl == 2.0
