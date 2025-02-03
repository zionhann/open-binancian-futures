import logging

from typing import override
from app.webhook import Webhook


class DefaultWebhook(Webhook):
    _LOGGER = logging.getLogger(__name__)

    def __init__(self) -> None:
        DefaultWebhook._LOGGER.info("Default webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        pass
