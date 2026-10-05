import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from .constants import settings

BASE_DIR = "log"
FILE_HANDLER_INTERVAL = 1
FILE_HANDLER_BACKUP_COUNT = 7
FILE_HANDLER_WHEN = "midnight"
file_handler: TimedRotatingFileHandler | None = None
stream_handler: logging.StreamHandler | None = None


def init(name: str) -> logging.Logger:
    global file_handler, stream_handler
    directory = Path(BASE_DIR) / ("test" if settings.is_testnet else "main")
    directory.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    if file_handler is not None:
        root.removeHandler(file_handler)
        file_handler.close()
    file_handler = TimedRotatingFileHandler(
        filename=directory / f"{datetime.now():%Y%m%d-%H%M%S}.log",
        when=FILE_HANDLER_WHEN,
        interval=FILE_HANDLER_INTERVAL,
        backupCount=FILE_HANDLER_BACKUP_COUNT,
    )
    if stream_handler is None:
        stream_handler = logging.StreamHandler()
        root.addHandler(stream_handler)
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s] %(module)s.%(funcName)s:%(lineno)d --- %(message)s"
    )
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
    root.addHandler(file_handler)
    root.setLevel(int(os.getenv("LOGGING_LEVEL", logging.INFO)))
    return logging.getLogger(name)
