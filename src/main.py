import traceback
import app.logging as log

from dotenv import load_dotenv
from app.core import Joshua
from app.core.constant import BacktestConfig
from app.backtest import Backtest

load_dotenv()
logger = log.init(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = Joshua() if not BacktestConfig.IS_BACKTEST.value else Backtest()
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}: {traceback.format_exc()}")
        app.close()
