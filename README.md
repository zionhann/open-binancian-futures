# Open Binancian Futures

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)

**Open Binancian Futures** is a robust framework designed to help you effortlessly create, backtest, and deploy your own trading bots for Binance USDⓈ-M Futures.

## 🚀 Key Features

- **Live Trading:** Automatically track multiple symbols and execute trades based on your custom strategy.
- **Backtesting:** Evaluate your strategies on historical data before risking real capital. (Experimental)
- **Webhook Integration:** Receive real-time trade notifications via Slack or Discord.

## 📦 Installation

### Quick Install

```bash
pip install open-binancian-futures
```

### From Source (Development)

For contributors or those wanting to modify the code:

```bash
git clone https://github.com/zionhann/open-binancian-futures.git
cd open-binancian-futures
pip install -e .
```

### Prerequisites

Ensure you have:

- **Python 3.8+**
- **Binance API Keys** with "Enable Futures" permission ([Get API Keys](https://www.binance.com/en/support/faq/360002502072))

## 🛠️ Getting Started

### Quick Start

1.  **Install the package:**

    ```bash
    pip install open-binancian-futures
    ```

2.  **Set up configuration:**
    Create a `.env` file in your working directory (see [Configuration](#configuration)):

    ```bash
    # Copy example config
    curl -o .env https://raw.githubusercontent.com/zionhann/open-binancian-futures/main/.env.example
    # Edit with your API keys and settings
    ```

3.  **Create your strategy:**
    Create `my_strategy.py` in your working directory extending the `Strategy` class:

    ```python
    from open_binancian_futures.strategy import Strategy
    from pandas import DataFrame

    class MyStrategy(Strategy):
        def load(self, df: DataFrame) -> DataFrame:
            # Add your technical indicators
            return df

        async def run(self, symbol: str) -> None:
            # Your trading logic
            pass

        async def run_backtest(self, symbol: str, index: int) -> None:
            # Your backtesting logic
            pass
    ```

4.  **Run your bot:**
    ```bash
    open-binancian-futures my_strategy
    ```

### Configuration

Configure environment variables in a `.env` file (see [`.env.example`](.env.example)):

    | Variable | Type | Required | Default | Description |
    | :--- | :---: | :---: | :--- | :--- |
    | **API Keys** |
    | `API_KEY` | string | Yes* | - | Binance API key for mainnet |
    | `API_SECRET` | string | Yes* | - | Binance API secret for mainnet |
    | `API_KEY_TEST` | string | Yes* | - | Binance API key for testnet |
    | `API_SECRET_TEST` | string | Yes* | - | Binance API secret for testnet |
    | **Strategy** |
    | `STRATEGY` | string | Yes | - | Strategy name |
    | `SYMBOLS` | string | No | `BTCUSDT` | Target symbols (USDT-based only), e.g., `BTCUSDT,ETHUSDT` |
    | `INTERVAL` | string | No | `1d` | Candle interval: `1m`, `3m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d` |
    | `LEVERAGE` | number | No | `1` | Leverage multiplier |
    | `SIZE` | number | No | `0.05` | Max trade portion per order (e.g., `0.3` = 30%) |
    | `GTD_NLINES` | number | No | - | Candles to hold open orders (defaults to GTC if not set) |
    | `TIMEZONE` | string | No | `UTC` | Timezone (e.g., `Asia/Seoul`) |
    | **Mode** |
    | `IS_TESTNET` | boolean | No | `false` | Use Binance Testnet (`true`/`false`) |
    | `IS_BACKTEST` | boolean | No | `false` | Run in backtest mode (`true`/`false`) |
    | **Notifications** |
    | `WEBHOOK_URL` | string | No | - | Slack/Discord Webhook URL |
    | **Backtesting** |
    | `BALANCE` | number | No | `100` | Initial backtest balance |
    | `KLINES_LIMIT` | number | No | `1000` | Historical candles to fetch (max 1000) |
    | `INDICATOR_INIT_SIZE`| number | No | `200` | Candles for indicator initialization |

    > \* *Either Mainnet or Testnet credentials are required depending on `IS_TESTNET`.*

#### Configuration Notes

- **Automatic Loading**: Environment variables are loaded automatically from `.env` using pydantic-settings
- **CLI Overrides**: Command-line flags override `.env` values (see [Usage](#-usage))
- **Optional vs Required**: Only API keys and STRATEGY are truly required; all others have sensible defaults

### ▶️ Usage

Run your strategy using the CLI entry point:

```bash
open-binancian-futures <strategy_name>
```

You can also override environment variables directly from the CLI:

```bash
open-binancian-futures foo --backtest --symbols BTCUSDT,ETHUSDT
```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

**USE THIS SOFTWARE AT YOUR OWN RISK.**

The author and contributors are not responsible for any financial losses or damages arising from the use of this software. Cryptocurrency trading involves significant risk. Always test thoroughly and trade responsibly.
