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
        self._total_profit = 0.0
        self._total_loss = 0.0

    def print(self):
        self._print_details()
        self._print_summary()

    def _print_details(self):
        for result in self._results:
            self._total_trade_count += result.trade_count
            self._total_hit_count += result.hit_count
            self._total_win_count += result.win_count
            self._total_profit += result._profit
            self._total_loss += result._loss

            result.print()

    def _print_summary(self):
        total_hit_rate = (
            self._total_hit_count / BacktestConfig.SAMPLE_SIZE
            if BacktestConfig.SAMPLE_SIZE > 0
            else 0
        )
        total_win_rate = (
            self._total_win_count / self._total_trade_count
            if self._total_trade_count > 0
            else 0
        )
        cumulative_pnl = self._total_profit + self._total_loss
        roi = cumulative_pnl / BacktestConfig.BALANCE * 100

        total_average_win = (
            self._total_profit / self._total_win_count
            if self._total_win_count > 0
            else 0.0
        )
        total_average_loss = (
            self._total_loss / (self._total_trade_count - self._total_win_count)
            if self._total_trade_count > 0
            else 0.0
        )

        expectancy = (total_win_rate * total_average_win) - (
            (1 - total_win_rate) * total_average_loss
        )
        rr = total_average_win / abs(total_average_loss) if total_average_loss else 0.0
        pf = self._total_profit / abs(self._total_loss) if self._total_loss else 0.0

        LOGGER.info(
            textwrap.dedent(
                f"""
                ===Overall Results===
                Hit Rate: {total_hit_rate*100:.2f}%
                Win Rate: {total_win_rate*100:.2f}%
                Cumulative PNL: {cumulative_pnl:.2f} USDT ({roi:.2f}%)
                
                Expectancy: {expectancy:.2f} USDT
                Risk-Reward Ratio: {rr:.2f}
                Profit Factor: {pf:.2f}
                """
            )
        )
