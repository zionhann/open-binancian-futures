import traceback

from dotenv import load_dotenv

import logging_config
from model.constant import BacktestConfig
from runner.backtesting import Backtesting
from runner.live_trading import LiveTrading

load_dotenv()
logger = logging_config.init(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = LiveTrading() if not BacktestConfig.IS_BACKTEST else Backtesting()
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}: {traceback.format_exc()}")
        app.close()
