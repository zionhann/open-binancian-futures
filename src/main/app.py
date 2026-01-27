import traceback

from dotenv import load_dotenv

load_dotenv()

import logging_config
from model.constant import BacktestConfig
from runner.backtesting import Backtesting
from runner.live_trading import LiveTrading

logger = logging_config.init(__name__)


def main() -> None:
    """Main application entry point."""
    logger.info("Boot process initiated. Preparing to start the application...")

    # Select runner based on configuration
    runner_class = LiveTrading if not BacktestConfig.IS_BACKTEST else Backtesting

    try:
        # Use context manager to ensure proper cleanup
        with runner_class() as runner:
            runner.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}: {traceback.format_exc()}")


if __name__ == "__main__":
    main()
