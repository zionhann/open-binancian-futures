import typer
from typing import Optional
from .constants import settings
from .runners import Backtesting, LiveTrading
from . import logging_config
import traceback

app = typer.Typer(help="Open Binancian Futures CLI")
logger = logging_config.init(__name__)


@app.command()
def run(
    strategy: str = typer.Argument(..., help="Strategy to use."),
    testnet: Optional[bool] = typer.Option(
        None, "--testnet/--mainnet", help="Use Binance Testnet. (Overrides .env)"
    ),
    backtest: Optional[bool] = typer.Option(
        None, "--backtest/--live", help="Run in backtest mode. (Overrides .env)"
    ),
    symbols: Optional[str] = typer.Option(
        None, "--symbols", help="Comma-separated list of symbols. (Overrides .env)"
    ),
    intervals: Optional[str] = typer.Option(
        None, "--intervals", help="Comma-separated list of intervals. (Overrides .env)"
    ),
    start_date: Optional[str] = typer.Option(
        None,
        "--start-date",
        help="Inclusive UTC backtest start date (required for --backtest).",
    ),
    end_date: Optional[str] = typer.Option(
        None,
        "--end-date",
        help="Inclusive UTC backtest end date (required for --backtest).",
    ),
    data_dir: Optional[str] = typer.Option(
        None,
        "--data-dir",
        help="Binance Vision ZIP cache directory.",
    ),
):
    """Run the trading bot or backtest."""
    # Apply CLI overrides to settings
    if strategy:
        settings.strategy = strategy
    if testnet is not None:
        settings.is_testnet = testnet
    if backtest is not None:
        settings.is_backtest = backtest
    if symbols:
        settings.symbols = symbols
    if intervals:
        settings.intervals = intervals
    if start_date is not None:
        settings.backtest_start_date = start_date
    if end_date is not None:
        settings.backtest_end_date = end_date
    if data_dir is not None:
        settings.backtest_data_dir = data_dir

    logger.info("Boot process initiated. Preparing to start the application...")

    # Select runner based on configuration
    runner_class = LiveTrading if not settings.is_backtest else Backtesting

    try:
        # Use context manager to ensure proper cleanup
        with runner_class() as runner:
            runner.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}: {traceback.format_exc()}")
