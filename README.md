# Open Binancian Futures

![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

A Python framework for creating, backtesting, and deploying automated trading bots on Binance USDⓈ-M Futures.

## Features

- **Live Trading** – Monitor multiple symbols and execute trades automatically
- **Backtesting** – Run deterministic, credential-free backtests on injected historical data
- **Webhooks** – Real-time notifications via Slack/Discord

## Prerequisites

- Python 3.12+
- Binance API keys with `Enable Futures` permission for live trading ([Get keys](https://www.binance.com/en/support/faq/360002502072))

## Getting Started

### 1. Install the package

```bash
pip install open-binancian-futures
```

### 2. Create a `.env` file (see [.env.example](./.env.example))

| Variable     | Required | Default   | Description                                         |
| ------------ | :------: | --------- | --------------------------------------------------- |
| `API_KEY`    |  Yes\*   | -         | Binance API key (mainnet)                           |
| `API_SECRET` |  Yes\*   | -         | Binance API secret (mainnet)                        |
| `SYMBOLS`    |    No    | `BTCUSDT` | comma-separated list of symbols to trade            |
| `INTERVALS`  |    No    | `1d`      | Comma-separated candle intervals (`1m`, `5m`, `1h`, ...). First entry is the primary interval |
| `LEVERAGE`   |    No    | `1`       | Leverage multiplier (1 ~ 125)                       |
| `SIZE`       |    No    | `0.05`    | Trade size per order (e.g., `0.05` = 5% of balance) |

**\* For testnet, use `API_KEY_TEST` and `API_SECRET_TEST` instead**

<details>
<summary><b>View All Configuration Options</b></summary>

| Variable              |  Type  | Default | Description                                  |
| --------------------- | :----: | ------- | -------------------------------------------- |
| `IS_TESTNET`          |  bool  | `false` | Use testnet (`true`/`false`)                 |
| `GTD_NLINES`          | number | -       | Candles to hold open orders (GTC if not set) |
| `TIMEZONE`            | string | `UTC`   | Timezone (e.g., `Asia/Seoul`)                |
| `WEBHOOK_URL`         | string | -       | Slack/Discord webhook for notifications      |
| **Backtesting**       |        |         |                                              |
| `IS_BACKTEST`         |  bool  | `false` | Enable backtest mode                         |
| `BALANCE`             | number | `100`   | Initial backtest balance                     |
| `KLINES_LIMIT`        | number | `1000`  | Historical candles to fetch (max 1000)       |
| `INDICATOR_INIT_SIZE` | number | `200`   | Candles for indicator warm-up                |

</details>

### 3. Create your strategy

Extend the `Strategy` class and implement `load()`, `run()`, and `run_backtest()` functions:

- `load(DataFrame)`: Loads technical indicators you want to use
- `run(str, str)`: Executes your trading logic
- `run_backtest(str, str, int)`: Backtesting logic (optional)

<details>
<summary><b>Example Strategy</b></summary>

```python
import asyncio
import pandas_ta as ta

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewOrderSideEnum,
    NewOrderTimeInForceEnum,
)
from open_binancian_futures.types import OrderType
from open_binancian_futures.strategy import Strategy
from open_binancian_futures.constants import settings
from open_binancian_futures.utils import fetch
from pandas import DataFrame
from typing import cast, override

class MyStrategy(Strategy):

    @override
    def load(self, df: DataFrame) -> DataFrame:
        """Add technical indicators to the dataframe"""
        # You can use `pandas_ta` to add technical indicators
        df["RSI_14"] = ta.rsi(df["Close"], length=14)
        return df

    @override
    async def run(self, symbol: str, interval: str) -> None:
        """Execute your trading logic"""
        latest = self.indicators[symbol][interval].iloc[-1] # Access to the latest candle
        entry_price = latest["Close"]

        if latest["RSI_14"] < 30:
            async with cast(asyncio.Lock, self.lock):
                if entry_quantity := self.exchange_info.to_entry_quantity(
                    symbol=symbol,
                    entry_price=entry_price,
                    balance=self.balance,
                ):
                    fetch(
                        self.client.rest_api.new_order,
                        symbol=symbol,
                        side=NewOrderSideEnum.BUY,
                        type=OrderType.LIMIT.value,
                        price=float(entry_price),
                        quantity=float(entry_quantity),
                        time_in_force=NewOrderTimeInForceEnum.GTC,
                    )

    @override
    async def run_backtest(self, symbol: str, interval: str, index: int) -> None:
        """Backtesting logic (optional)"""
        ...
```

</details>

### 4. Deterministic backtesting without credentials

The package backtester accepts a `DataFrame`, a CSV/Parquet path, or a custom
`HistoricalDataSource`. The input must contain `Open_time`, `Open`, `High`,
`Low`, and `Close`; add `Symbol` when more than one symbol is present.

```python
from open_binancian_futures import (
    BacktestConfig,
    Backtesting,
    DataFrameDataSource,
    MarketExecutionPolicy,
    OrderType,
    PositionSide,
)

runner = Backtesting(
    strategy=my_strategy,
    data_source=DataFrameDataSource(candles, interval="1h"),
    config=BacktestConfig(
        initial_balance=100.0,
        leverage=1,
        warmup_bars=0,
        interval="1h",
        market_execution=MarketExecutionPolicy.CLOSE,
    ),
)
result = runner.run()

print(result.summary.format())
print(result.summary.trades)
print(result.equity_curve)
```

`Backtesting.run()` returns the `BacktestRunResult`; existing callers that
only use the side effects can continue to ignore the return value.

`CsvDataSource(path, symbol="ETHUSDT", interval="1h")` and
`ParquetDataSource(...)` provide the file-backed equivalents. Injected data
does not create a Binance client or make a network request. Calling
`Backtesting()` without a source keeps the existing Binance REST data loader
for CLI compatibility. When `BacktestConfig.interval` is omitted, a direct
DataFrame/CSV/Parquet source uses its declared interval; otherwise the
configured interval takes precedence.

The default engine evaluates completed candles in UTC chronological order.
Existing orders are eligible on the current candle, while newly created
`LIMIT`, `STOP`, `TAKE_PROFIT`, and `TAKE_PROFIT_MARKET` orders wait until the
next candle. A newly created `MARKET` order fills at the completed candle close
by default; `MarketExecutionPolicy.NEXT_OPEN` explicitly defers it to the next
candle open. Limit and stop gaps fill at the candle open, intrabar triggers
fill at the configured price, and Stop Loss wins over Take Profit when both
are reached. Costs, slippage, and funding are zero by default. Open positions
are realized at each symbol's final evaluated close.

The returned `BacktestRunResult` exposes `by_symbol`, `summary`,
`equity_curve`, and `final_balance`. Metrics use each symbol's actual
evaluated-bar count, classify zero PNL as break-even, and keep the loss sign
negative in expectancy calculations.

For strategy code that should work without Binance SDK enums, use the domain
adapter:

```python
await self.submit_order(
    self.order_intent(
        "ETHUSDT", PositionSide.BUY, OrderType.LIMIT,
        price=99.0, quantity=1.0,
    )
)
```

Existing `run_backtest(symbol, interval, index)` implementations and direct
`OrderList.open_order(...)` calls remain supported. The latter are discovered
by the runner after each callback; new non-market orders still follow the
same-candle deferral rule. The SDK-specific `open_order(...)` method remains
available for live REST execution.

### 5. Running

```bash
open-binancian-futures my_strategy.py
```

You can override environment variables from the command line:

```bash
open-binancian-futures --backtest --symbols BTCUSDT,ETHUSDT --intervals 1h,4h my_strategy.py
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Disclaimer

**USE AT YOUR OWN RISK.**

The author and contributors are not responsible for any financial losses or damages arising from the use of this software. Cryptocurrency trading involves significant risk. Always test thoroughly and trade responsibly.
