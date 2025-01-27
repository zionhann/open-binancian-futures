class TestResult:
    def __init__(self, symbol: str, trials: int):
        self.symbol = symbol
        self.trials = trials

        self.cumulative_pnl = 0.0
        self.trigger_count = 0
        self.winning_count = 0
        self.triggering_rate = 0.0
        self.winning_rate = 0.0

    def update(self, trigger_count: int, cumulative_pnl: float, winning_count: int):
        self.cumulative_pnl = cumulative_pnl
        self.trigger_count = trigger_count
        self.winning_count = winning_count
        self.triggering_rate = round(self.trigger_count / self.trials * 100, 2)
        self.winning_rate = round(self.winning_count / self.trigger_count * 100, 2)

    def __str__(self):
        return f"""
===Backtest Result===
Symbol: {self.symbol}
Trials: {self.trials}
Cumulative PNL: {self.cumulative_pnl:.2f}USDT
Trigger Count: {self.trigger_count}
Winning Count: {self.winning_count}
Triggering Rate: {self.triggering_rate}%
Winning Rate: {self.winning_rate}%
"""
