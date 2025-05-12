import logging
from typing import override

from webhook import Webhook

LOGGER = logging.getLogger(__name__)


class DefaultWebhook(Webhook):
    def __init__(self) -> None:
        LOGGER.info("Default webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        pass
