import typer
from typing import Optional
from open_binancian_futures.constants import reload_settings
from open_binancian_futures.runners import Backtesting, LiveTrading
import open_binancian_futures.logging_config as logging_config
import traceback

app = typer.Typer(help="Open Binancian Futures CLI")
logger = logging_config.init(__name__)

@app.command()
def run(
    strategy: str = typer.Argument(..., help="Strategy to use."),
    testnet: Optional[bool] = typer.Option(None, "--testnet/--mainnet", help="Use Binance Testnet. (Overrides .env)"),
    backtest: Optional[bool] = typer.Option(None, "--backtest/--live", help="Run in backtest mode. (Overrides .env)"),
    symbols: Optional[str] = typer.Option(None, "--symbols", help="Comma-separated list of symbols. (Overrides .env)"),
):
    """Run the trading bot or backtest."""
    # Reload settings
    reload_settings()
    
    # Apply overrides (simplistic approach for now)
    from open_binancian_futures.constants import settings
    if strategy:
        settings.strategy = strategy
    if testnet is not None:
        settings.is_testnet = testnet
    if backtest is not None:
        settings.is_backtest = backtest
    if symbols:
        settings.symbols = symbols
        
    logger.info("Boot process initiated. Preparing to start the application...")

    # Select runner based on configuration
    runner_class = LiveTrading if not settings.is_backtest else Backtesting

    try:
        # Use context manager to ensure proper cleanup
        with runner_class() as runner:
            runner.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}: {traceback.format_exc()}")
