import logging
import requests

from typing import override
from app.webhook import Webhook


class DiscordWebhook(Webhook):
    _LOGGER = logging.getLogger(__name__)

    def __init__(self, url: str) -> None:
        self.url = url
        DiscordWebhook._LOGGER.info("Discord webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        requests.post(url=self.url, json={**kwargs, "content": message})
