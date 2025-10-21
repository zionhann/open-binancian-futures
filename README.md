# Open Binancian Futures

Open Binancian Futures is a framework to make you easily create your own trading bot for Binance USDs Futures.

It supports:

- Live Trading: track multiple symbols and trade them automatically based on your strategy.
- Backtesting: test your strategy on historical data to evaluate its performance. (Experimental)
- Webhook Integration: receive notifications on your trades via Slack or Discord.

## Getting Started

You need the following prerequisites:

- Python 3+
- Binance API keys with `enable Futures` permission.
- `TA-Lib` library installed. Refer to [Install](https://ta-lib.org/install/) for more details.

1. Clone the repository:

   ```bash
   git clone https://github.com/zionhann/open-binancian-futures.git
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Customise [example.py](src/main/strategy/example.py) with your own trading logic, or create a new file in `src/main/strategy/` that extends the [Strategy](src/main/strategy/__init__.py) class. Do NOT use the example strategy as is — it is a simplified template and may lead to significant losses in real trading conditions.

4. Configure environment variables in `.env.example` and rename it to `.env`:

   | Variable                       | Type    | Required | Default                       | Description                                                                                                    |
   | ------------------------------ | ------- | -------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
   | **API Keys**                   |
   | `API_KEY`                      | string  | Yes\*    | -                             | Binance API key for mainnet                                                                                    |
   | `API_SECRET`                   | string  | Yes\*    | -                             | Binance API secret for mainnet                                                                                 |
   | `API_KEY_TEST`                 | string  | Yes\*    | -                             | Binance API key for testnet                                                                                    |
   | `API_SECRET_TEST`              | string  | Yes\*    | -                             | Binance API secret for testnet                                                                                 |
   | **Strategy Configuration**     |
   | `STRATEGY`                     | string  | Yes      | -                             | A strategy file name located in `src/main/strategy/*` (case-sensitive)                                         |
   | `SYMBOLS`                      | string  | No       | `BTCUSDT`                     | Comma-separated list of symbols to track (USDT-based only), e.g., `BTCUSDT,ETHUSDT`                            |
   | `INTERVAL`                     | string  | No       | `1d`                          | Time interval for all symbols, e.g., `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`                          |
   | `LEVERAGE`                     | number  | No       | `1`                           | Leverage to use for all symbols                                                                                |
   | `SIZE`                         | number  | No       | `0.05`                        | Maximum portion of each trade, e.g., `0.3` for 30%                                                             |
   | `AVERAGING`                    | string  | No       | `1`                           | Comma-separated ratios for averaging strategy, e.g., `0.25,0.25,0.50` for 25%, 25%, 50%. Set to `1` to disable |
   | `GTD_NLINES`                   | number  | No       | `3`                           | Number of candles to hold open orders. Use `GTC` in your strategy to disable                                   |
   | `TIMEZONE`                     | string  | No       | `UTC`                         | Timezone for date-time operations, e.g., `Asia/Seoul`                                                          |
   | **Mode Settings**              |
   | `IS_TESTNET`                   | boolean | No       | `false`                       | Whether to use testnet (lowercase `true` or `false`)                                                           |
   | `IS_BACKTEST`                  | boolean | No       | `false`                       | Whether to run a backtest (lowercase `true` or `false`)                                                        |
   | **Take Profit / Stop Loss**    |
   | `TPSL_TAKE_PROFIT_RATIO`       | number  | No       | `TPSL_STOP_LOSS_RATIO * 2`    | Take-profit ratio, e.g., `0.1` for +10%                                                                        |
   | `TPSL_STOP_LOSS_RATIO`         | number  | No       | `0.05`                        | Stop-loss ratio, e.g., `0.1` for -10%                                                                          |
   | **Trailing Stop**              |
   | `TS_ACTIVATION_RATIO`          | number  | No       | -                             | Ratio to activate a trailing stop order, e.g., `0.1` to activate on +10%                                       |
   | `TS_CALLBACK_RATIO`            | number  | No       | -                             | Ratio to close a trailing stop from its highest point, e.g., `0.1` to close on -10% from peak                  |
   | **Notifications**              |
   | `WEBHOOK_URL`                  | string  | No       | -                             | Webhook URL to receive trade messages (Slack/Discord)                                                          |
   | **Backtest Configuration**     |
   | `BACKTEST_BALANCE`             | number  | No       | `100`                         | Initial balance for backtesting                                                                                |
   | `BACKTEST_KLINES_LIMIT`        | number  | No       | `1000`                        | Number of historical candles to fetch (max 1000)                                                               |
   | `BACKTEST_INDICATOR_INIT_SIZE` | number  | No       | `BACKTEST_KLINES_LIMIT * 0.2` | Number of candles for indicator initialization                                                                 |

   \* Either mainnet (`API_KEY` + `API_SECRET`) or testnet (`API_KEY_TEST` + `API_SECRET_TEST`) credentials are required.

5. Run the app:

   ```bash
   python src/main/app.py
   ```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

USE THE SOFTWARE AT YOUR OWN RISK. THE AUTHOR IS NOT RESPONSIBLE FOR ANY LOSSES OR DAMAGES ARISING FROM ITS USE.
