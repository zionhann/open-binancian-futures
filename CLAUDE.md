# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

### Running the Application

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python src/main/app.py

# Run via Docker
docker build -t auto-trade .
docker run --env-file .env auto-trade
```

### Configuration

All configuration is done through environment variables in a `.env` file. Required variables:
- `STRATEGY`: Strategy file name from `src/main/strategy/` (without `.py` extension)
- `API_KEY` + `API_SECRET` (mainnet) OR `API_KEY_TEST` + `API_SECRET_TEST` (testnet)
- `IS_TESTNET`: `true` or `false`
- `IS_BACKTEST`: `true` for backtesting mode, `false` for live trading

See [README.md](README.md) for the complete list of configuration options.

## Architecture Overview

### Core Pattern: Runner + Strategy

The application uses a two-layer architecture:

1. **Runner Layer** ([src/main/runner/](src/main/runner/__init__.py))
   - Abstract base class `Runner` with `run()` and `close()` methods
   - Two implementations:
     - `LiveTrading`: Connects to Binance WebSocket streams, handles real-time market data and order updates
     - `Backtesting`: Simulates trading on historical data, evaluates strategy performance

2. **Strategy Layer** ([src/main/strategy/](src/main/strategy/__init__.py))
   - Abstract base class `Strategy` that defines the trading logic interface
   - Strategies are dynamically loaded by filename via `Strategy.of(name=...)`
   - Each strategy must:
     - Extend `Strategy` base class
     - Implement `load(df: DataFrame) -> DataFrame` to add custom technical indicators
     - Implement `run(symbol: str) -> None` (live trading) or `run_backtest(symbol: str, index: int) -> None` (backtesting)

### Entry Point Flow

[src/main/app.py](src/main/app.py) → Selects runner based on `IS_BACKTEST` flag → Runner initializes and loads the specified strategy → Runs trading loop

### Data Models

Located in [src/main/model/](src/main/model/):

- **ExchangeInfo**: Symbol metadata (price precision, lot size, min notional)
- **Balance**: Account balance tracking
- **OrderBook**: Container managing orders per symbol
- **PositionBook**: Container managing positions per symbol
- **Indicator**: DataFrame container for OHLCV data + technical indicators per symbol
- **Order/Position**: Individual order and position entities

These models are shared state passed to strategies, allowing them to query current market state and make trading decisions.

### WebSocket Event Handling

[LiveTrading](src/main/runner/live_trading.py) subscribes to two WebSocket streams:

1. **Market Stream** (`_market_stream_handler`):
   - Receives kline/candlestick updates
   - Triggers `strategy.on_new_candlestick()` for each completed candle

2. **User Data Stream** (`_user_stream_handler`):
   - Receives `ORDER_TRADE_UPDATE` events → `strategy.on_filled_order()`
   - Receives `ACCOUNT_UPDATE` events → Updates balance/position tracking
   - Handles `LISTEN_KEY_EXPIRED` → Automatically refreshes the stream

The strategy never directly calls Binance APIs for market data; it reacts to WebSocket events.

## Creating Custom Strategies

### Strategy Template

1. Create `src/main/strategy/your_strategy_name.py`
2. Extend `Strategy` base class
3. Implement required methods:

```python
from strategy import Strategy

class YourStrategyName(Strategy):
    def load(self, df: DataFrame) -> DataFrame:
        # Add your custom indicators to the DataFrame
        # Return the modified DataFrame with new columns
        pass

    async def run(self, symbol: str) -> None:
        # Your trading logic for live trading
        # Access: self.indicators, self.orders, self.positions, self.balance
        pass

    async def run_backtest(self, symbol: str, index: int) -> None:
        # Your trading logic for backtesting
        # Similar to run(), but uses historical data at 'index'
        pass
```

4. Set `STRATEGY=your_strategy_name` in `.env`

### Strategy Utilities

Strategies have access to:
- `self.client`: Binance API client
- `self.exchange_info`: Symbol trading rules
- `self.balance`: Current account balance
- `self.orders.get(symbol)`: Orders for a symbol
- `self.positions.get(symbol)`: Positions for a symbol
- `self.indicators.get(symbol)`: DataFrame with OHLCV + indicators
- `self.webhook`: Notification handler (Slack/Discord)

Helper methods:
- `self.calculate_stop_price()`: Calculate stop-loss/take-profit prices
- `self.exchange_info.to_entry_price()`: Round price to exchange precision
- `self.exchange_info.to_entry_quantity()`: Calculate order quantity based on balance and leverage

### Preset Functions

[src/main/preset/](src/main/preset/) contains reusable strategy components:

- `ask_ai.py`: AI-powered trade decision using Claude/GPT with TOON format for token reduction
- `circular_averaging.py`: Dollar-cost averaging into positions
- `chandelier_stop.py`: ATR-based trailing stop
- `margin_ratio.py`: Position sizing based on margin
- `half_n_half.py`: Split orders into two tranches
- `roi.py`: Take profit at target return percentage

Import and use these in your strategies as needed.

## Backtesting

Backtesting mode ([src/main/runner/backtesting/](src/main/runner/backtesting/__init__.py)):

1. Fetches historical klines up to `BACKTEST_KLINES_LIMIT`
2. Uses first `BACKTEST_INDICATOR_INIT_SIZE` candles to initialize indicators
3. Iterates through remaining candles, calling `strategy.run_backtest()` for each
4. Simulates order fills when price crosses order levels
5. Prints performance summary with metrics per symbol

Set `IS_BACKTEST=true` in `.env` to enable.

## Webhook Notifications

[src/main/webhook/](src/main/webhook/) provides notification abstraction:

- `SlackWebhook`: Send messages to Slack
- `DiscordWebhook`: Send messages to Discord
- `DefaultWebhook`: No-op implementation when `WEBHOOK_URL` is not set

The webhook is automatically selected based on the URL prefix. Use `self.webhook.send_message()` in strategies.

## Constants and Configuration

[src/main/model/constant/__init__.py](src/main/model/constant/__init__.py) defines:

- **Required**: API keys and strategy name
- **AppConfig**: Trading parameters (symbols, leverage, interval, etc.)
- **BacktestConfig**: Backtesting parameters
- **Bracket/TrailingStop**: Stop-loss and take-profit configuration
- **Enums**: OrderType, PositionSide, OrderStatus, EventType, etc.

All values are loaded from environment variables with sensible defaults.

## AI Integration

The codebase supports AI-powered trading decisions through [src/main/preset/ask_ai.py](src/main/preset/ask_ai.py):

- Uses Claude (Anthropic) or GPT (OpenAI) models
- Encodes indicator data using TOON format to reduce token usage by ~85%
- Provides structured tool calling for trade action decisions (LONG/SHORT/NO_TRADE)
- Requires `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in environment

The AI receives recent OHLCV data and candlestick patterns, then returns a structured trade decision with reasoning.

## Deployment

### Docker Build

```bash
# Build for multiple architectures
docker buildx build --platform linux/arm64,linux/amd64 -t your-image:tag --push .

# Run with env file
docker run --env-file .env your-image:tag
```

The [Dockerfile](Dockerfile) handles TA-Lib compilation and sets up the Python environment.

### Important Notes

- Logs are written to `log/main/` directory (created automatically)
- The `.gitignore` excludes `strategy/strategy_*.py` files (custom strategies)
- All strategies must be in `src/main/strategy/` and follow the naming convention
- The application uses async/await patterns extensively; always use `asyncio.create_task()` or `await` when calling async strategy methods

## Testing

There is currently no formal test suite. Test strategies by:

1. Running in testnet mode (`IS_TESTNET=true`)
2. Running in backtest mode (`IS_BACKTEST=true`)
3. Monitoring logs in `log/main/` for errors

When modifying core components (runners, models), always verify with both live (testnet) and backtest modes.
