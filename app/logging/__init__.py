import logging
import os

from logging.handlers import TimedRotatingFileHandler
from datetime import datetime
from app.core.constant import AppConfig

BASE_DIR = ".log"
SUB_DIR = "test" if AppConfig.IS_TESTNET.value else "main"

FILE_HANDLER_INTERVAL = 1
FILE_HANDLER_BACKUP_COUNT = 7
FILE_HANDLER_WHEN = "midnight"

file_handler = TimedRotatingFileHandler(
    filename=f"{BASE_DIR}/{SUB_DIR}/{datetime.now().strftime('%Y%m%d-%H%M%S')}.log",
    when=FILE_HANDLER_WHEN,
    interval=FILE_HANDLER_INTERVAL,
    backupCount=FILE_HANDLER_BACKUP_COUNT,
)

stream_handler = logging.StreamHandler()


def init(name: str) -> logging.Logger:
    logging.basicConfig(
        format="[%(asctime)s] %(levelname)s [%(name)s] %(module)s.%(funcName)s:%(lineno)d --- %(message)s",
        level=int(os.getenv("LOGGING_LEVEL", logging.INFO)),
        handlers=[file_handler, stream_handler],
    )
    return logging.getLogger(name)
