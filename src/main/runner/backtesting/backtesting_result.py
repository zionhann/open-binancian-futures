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

        self._profit = 0.0
        self._loss = 0.0

    @property
    def trade_count(self) -> int:
        return self._trade_count_buy + self._trade_count_sell

    @property
    def hit_count(self) -> int:
        return self._hit_count_buy + self._hit_count_sell

    @property
    def win_count(self) -> int:
        return self._win_count_buy + self._win_count_sell

    @property
    def loss_count(self) -> int:
        return self.trade_count - self.win_count

    @property
    def profit(self) -> float:
        return self._profit

    @property
    def loss(self) -> float:
        return self._loss

    @property
    def average_win(self) -> float:
        return self._profit / self.win_count if self.win_count else 0.0

    @property
    def average_loss(self) -> float:
        return self._loss / self.loss_count if self.loss_count else 0.0

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
        if pnl > 0:
            self._profit += pnl
        else:
            self._loss += pnl

    def print(self):
        hit_rate = round(self.hit_count / BacktestConfig.SAMPLE_SIZE, 2)
        win_rate = round(
            self.win_count / self.trade_count if self.trade_count else 0, 2
        )

        buy_rate = round(
            self._hit_count_buy / self.hit_count if self.hit_count else 0, 2
        )
        sell_rate = round(
            self._hit_count_sell / self.hit_count if self.hit_count else 0, 2
        )

        buy_win_rate = round(
            (
                self._win_count_buy / self._trade_count_buy
                if self._trade_count_buy
                else 0
            ),
            2,
        )
        sell_win_rate = round(
            (
                self._win_count_sell / self._trade_count_sell
                if self._trade_count_sell
                else 0
            ),
            2,
        )

        expectancy = (win_rate * self.average_win) - (
            (1 - win_rate) * self.average_loss
        )

        logger.info(
            textwrap.dedent(
                f"""
                ==={self._symbol} Backtesting Results===
                Hit Rate: {hit_rate*100:.2f}% ({self.hit_count}/{BacktestConfig.SAMPLE_SIZE})
                - BUY Hit Rate: {buy_rate*100:.2f}% ({self._hit_count_buy}/{self.hit_count})
                - SELL Hit Rate: {sell_rate*100:.2f}% ({self._hit_count_sell}/{self.hit_count})
                
                Win Rate: {win_rate*100:.2f}% ({self.win_count}/{self.trade_count})
                - BUY Win Rate: {buy_win_rate*100:.2f}% ({self._win_count_buy}/{self._trade_count_buy})
                - SELL Win Rate: {sell_win_rate*100:.2f}% ({self._win_count_sell}/{self._trade_count_sell})

                Cumulative PNL: {self._profit+self._loss:.2f} USDT
                Expectancy: {expectancy:.2f} USDT
                Risk-Reward Ratio: {self.average_win/abs(self.average_loss):.2f}
                Profit Factor: {self._profit/abs(self._loss):.2f}
                """
            )
        )
