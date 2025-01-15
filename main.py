from core import Joshua
import logging
from datetime import datetime
import const

log_subdir = "test" if const.TradeConfig.IS_TESTNET.value else "main"

logging.basicConfig(
    format=const.LOG_FORMAT,
    level=logging.INFO,
    filename=f"{const.LOG_BASEDIR}/{log_subdir}/{datetime.now().strftime('%Y%m%d-%H%M%S')}.log",
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = Joshua(is_testnet=const.TradeConfig.IS_TESTNET.value)
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}")
        app.close()
