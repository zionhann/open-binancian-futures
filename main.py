from core import Joshua
import logging
import const
import loggin

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    loggin.init()
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = Joshua(is_testnet=const.TradeConfig.IS_TESTNET.value)
        app.run()
    except Exception as e:
        logger.error(f"Application terminated by {e}")
        app.close()
