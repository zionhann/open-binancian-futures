# Open Binancian Futures

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

**Open Binancian Futures** is a robust framework designed to help you effortlessly create, backtest, and deploy your own trading bots for Binance USDⓈ-M Futures.

## 🚀 Key Features

*   **Live Trading:** Automatically track multiple symbols and execute trades based on your custom strategy.
*   **Backtesting:** Evaluate your strategies on historical data before risking real capital. (Experimental)
*   **Webhook Integration:** Receive real-time trade notifications via Slack or Discord.

## 📦 Installation

### Prerequisites

*   **Python 3.8+**
*   **Binance API Keys** (with "Enable Futures" permission)
*   **TA-Lib Library**: Refer to [Installation Guide](https://ta-lib.org/install/)

### From Source

```bash
git clone https://github.com/zionhann/open-binancian-futures.git
cd open-binancian-futures
pip install -e .  # Development mode
```

### Optional Dependencies

```bash
pip install open-binancian-futures[ai]        # AI-powered trading (OpenAI/Anthropic)
pip install open-binancian-futures[webhook]   # Slack notifications
pip install open-binancian-futures[dev]       # Development tools
pip install open-binancian-futures[all]       # Everything
```

## 🛠️ Getting Started

1.  **Create your strategy:**
    Create a new file (e.g., `my_strategy.py`) in your current directory extending the `Strategy` class. Or use one of the presets like `roi`.
    > **⚠️ Warning:** Do NOT use example strategies as is — they are templates and may lead to significant losses in real trading conditions.

2.  **Configuration:**
    Configure environment variables in a `.env` file (see [`.env.example`](open_binancian_futures/.env.example)):

    | Variable | Type | Required | Default | Description |
    | :--- | :---: | :---: | :--- | :--- |
    | **API Keys** |
    | `API_KEY` | string | Yes* | - | Binance API key for mainnet |
    | `API_SECRET` | string | Yes* | - | Binance API secret for mainnet |
    | `API_KEY_TEST` | string | Yes* | - | Binance API key for testnet |
    | `API_SECRET_TEST` | string | Yes* | - | Binance API secret for testnet |
    | **Strategy** |
    | `STRATEGY` | string | No | - | Default strategy name if not passed via CLI |
    | `SYMBOLS` | string | No | `BTCUSDT` | Target symbols (USDT-based only), e.g., `BTCUSDT,ETHUSDT` |
    | `INTERVAL` | string | No | `1d` | Candle interval: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d` |
    | `LEVERAGE` | number | No | `1` | Leverage multiplier |
    | `SIZE` | number | No | `0.05` | Max trade portion per order (e.g., `0.3` = 30%) |
    | `AVERAGING` | string | No | `1` | Averaging ratios (e.g., `0.25,0.25,0.50`). Set `1` to disable |
    | `GTD_NLINES` | number | No | `3` | Candles to hold open orders (use `GTC` to disable) |
    | `TIMEZONE` | string | No | `UTC` | Timezone (e.g., `Asia/Seoul`) |
    | **Mode** |
    | `IS_TESTNET` | boolean | No | `false` | Use Binance Testnet (`true`/`false`) |
    | `IS_BACKTEST` | boolean | No | `false` | Run in backtest mode (`true`/`false`) |
    | **Risk Management** |
    | `TPSL_TAKE_PROFIT_RATIO` | number | No | `2x Stop Loss` | Take profit ratio (e.g., `0.1` = 10%) |
    | `TPSL_STOP_LOSS_RATIO` | number | No | `0.05` | Stop loss ratio (e.g., `0.05` = 5%) |
    | **Trailing Stop** |
    | `TS_ACTIVATION_RATIO` | number | No | - | Ratio to activate trailing stop |
    | `TS_CALLBACK_RATIO` | number | No | - | Ratio to close trailing stop from peak |
    | **Notifications** |
    | `WEBHOOK_URL` | string | No | - | Slack/Discord Webhook URL |
    | **Backtesting** |
    | `BACKTEST_BALANCE` | number | No | `100` | Initial backtest balance |
    | `BACKTEST_KLINES_LIMIT` | number | No | `1000` | Historical candles to fetch (max 1000) |
    | `BACKTEST_INDICATOR_INIT_SIZE`| number | No | `20% of Limit`| Candles for indicator initialization |

    > \* *Either Mainnet or Testnet credentials are required depending on `IS_TESTNET`.*

### ▶️ Usage

Run your strategy using the CLI entry point:

```bash
open-binancian-futures <strategy_name>
```

Example:
```bash
# Runs roi strategy from presets or current directory
open-binancian-futures roi
```

#### Overrides
You can also override environment variables directly from the CLI:

```bash
open-binancian-futures roi --backtest --testnet --symbols BTCUSDT,ETHUSDT
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

**USE THIS SOFTWARE AT YOUR OWN RISK.**

The author and contributors are not responsible for any financial losses or damages arising from the use of this software. Cryptocurrency trading involves significant risk. Always test thoroughly and trade responsibly.