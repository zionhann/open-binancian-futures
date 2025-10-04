from typing import Optional

from dotenv import load_dotenv

from runner import Runner

load_dotenv()

import traceback
import logging_config

from model.constant import BacktestConfig
from runner.backtesting import Backtesting
from runner.live_trading import LiveTrading

logger = logging_config.init(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")
    app: Optional[Runner] = None

    try:
        app = LiveTrading() if not BacktestConfig.IS_BACKTEST else Backtesting()
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}: {traceback.format_exc()}")
    finally:
        if app is not None:
            app.close()
