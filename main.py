from core import Joshua
from core.constants import TradingConfig
import loggin
from dotenv import load_dotenv

load_dotenv()
logger = loggin.init(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = Joshua(is_testnet=TradingConfig.IS_TESTNET.value)
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}")
        app.close()
