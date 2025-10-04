import logging
import textwrap

from model.constant import BacktestConfig, PositionSide

logger = logging.getLogger(__name__)


class BacktestingResult:
    def __init__(self, symbol: str):
        self._symbol = symbol

        self._hit_count_buy = 0
        self._hit_count_sell = 0

        self._win_count_buy = 0
        self._win_count_sell = 0

        self._trade_count_buy = 0
        self._trade_count_sell = 0

        self._cumulative_pnl = 0.0

    @property
    def total_trade_count(self) -> int:
        return self._trade_count_buy + self._trade_count_sell

    @property
    def total_hit_count(self) -> int:
        return self._hit_count_buy + self._hit_count_sell

    @property
    def total_win_count(self) -> int:
        return self._win_count_buy + self._win_count_sell

    @property
    def cumulative_pnl(self) -> float:
        return self._cumulative_pnl

    def increment_hit_count(self, side: PositionSide) -> None:
        if side == PositionSide.BUY:
            self._hit_count_buy += 1
        elif side == PositionSide.SELL:
            self._hit_count_sell += 1

    def increment_trade_count(self, side: PositionSide, is_win: bool) -> None:
        if side == PositionSide.BUY:
            self._trade_count_buy += 1
            self._win_count_buy += 1 if is_win else 0

        elif side == PositionSide.SELL:
            self._trade_count_sell += 1
            self._win_count_sell += 1 if is_win else 0

    def increase_pnl(self, pnl: float) -> None:
        self._cumulative_pnl += pnl

    def print(self):
        trade_count = self._trade_count_buy + self._trade_count_sell
        hit_count = self._hit_count_buy + self._hit_count_sell
        win_count = self._win_count_buy + self._win_count_sell

        hit_rate = round(hit_count / BacktestConfig.SAMPLE_SIZE * 100, 2)
        win_rate = round(win_count / trade_count * 100 if trade_count else 0, 2)

        buy_rate = round(self._hit_count_buy / hit_count * 100 if hit_count else 0, 2)
        sell_rate = round(self._hit_count_sell / hit_count * 100 if hit_count else 0, 2)

        buy_win_rate = round(
            (
                self._win_count_buy / self._trade_count_buy * 100
                if self._trade_count_buy
                else 0
            ),
            2,
        )
        sell_win_rate = round(
            (
                self._win_count_sell / self._trade_count_sell * 100
                if self._trade_count_sell
                else 0
            ),
            2,
        )
        expectancy = self._cumulative_pnl / trade_count if trade_count else 0.0

        logger.info(
            textwrap.dedent(
                f"""
                ==={self._symbol} Backtesting Results===
                Hit Rate: {hit_rate}% ({hit_count}/{BacktestConfig.SAMPLE_SIZE})
                - BUY Hit Rate: {buy_rate}% ({self._hit_count_buy}/{hit_count})
                - SELL Hit Rate: {sell_rate}% ({self._hit_count_sell}/{hit_count})
                
                Win Rate: {win_rate}% ({win_count}/{trade_count})
                - BUY Win Rate: {buy_win_rate}% ({self._win_count_buy}/{self._trade_count_buy})
                - SELL Win Rate: {sell_win_rate}% ({self._win_count_sell}/{self._trade_count_sell})

                Cumulative PNL: {self._cumulative_pnl:.2f} USDT
                Expectancy: {expectancy:.2f} USDT
                """
            )
        )
