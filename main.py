import loggin

from core import Joshua
from core.constants import AppConfig
from dotenv import load_dotenv
from backtest import Backtest

load_dotenv()
logger = loggin.init(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = Joshua() if not AppConfig.IS_BACKTEST.value else Backtest()
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}")
        app.close()
