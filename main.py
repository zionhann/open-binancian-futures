from joshua import Joshua
import logging
from datetime import datetime

LOG_FORMAT = "[%(asctime)s] %(levelname)-10s [%(name)10s] %(module)s.%(funcName)s:%(lineno)d --- %(message)s"

logging.basicConfig(
    format=LOG_FORMAT,
    level=logging.INFO,
    filename=f".log/{datetime.now().strftime('%Y%m%d')}.log",
)

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Boot process initiated. Preparing to start the application...")

    try:
        app = Joshua(
            symbols=["BTCUSDT", "ETHUSDT"],
            interval="5m",
            leverage=10,
            size=30,
            rsi_window=6,
            is_testnet=True,
        )
        app.run()
    except KeyboardInterrupt:
        logger.info("Application terminated by KeyboardInterrupt")
        app.close()
