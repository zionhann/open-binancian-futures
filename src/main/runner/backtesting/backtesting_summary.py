import logging
import textwrap

from model.constant import BacktestConfig
from runner.backtesting.backtesting_result import BacktestingResult

LOGGER = logging.getLogger(__name__)


class BacktestingSummary:
    def __init__(self, results: list[BacktestingResult]) -> None:
        self._results = results
        self._total_trade_count = 0
        self._total_hit_count = 0
        self._total_win_count = 0
        self._cumulative_pnl = 0.0

    def print(self):
        self._print_details()
        self._print_summary()

    def _print_details(self):
        for result in self._results:
            self._total_trade_count += result.total_trade_count
            self._total_hit_count += result.total_hit_count
            self._total_win_count += result.total_win_count
            self._cumulative_pnl += result.cumulative_pnl
            result.print()

    def _print_summary(self):
        total_hit_rate = (
            self._total_hit_count / BacktestConfig.SAMPLE_SIZE * 100
            if BacktestConfig.SAMPLE_SIZE > 0
            else 0
        )
        total_win_rate = (
            self._total_win_count / self._total_trade_count * 100
            if self._total_trade_count > 0
            else 0
        )
        roi = self._cumulative_pnl / BacktestConfig.BALANCE * 100
        expectancy = self._cumulative_pnl / self._total_trade_count

        LOGGER.info(
            textwrap.dedent(
                f"""
                ===Overall Results===
                Hit Rate: {total_hit_rate:.2f}%
                Win Rate: {total_win_rate:.2f}%
                Cumulative PNL: {self._cumulative_pnl:.2f} USDT ({roi:.2f}%)
                Expectancy: {expectancy:.2f} USDT
                """
            )
        )
