import logging
from typing import override

import requests

from webhook import Webhook

LOGGER = logging.getLogger(__name__)


class DiscordWebhook(Webhook):
    def __init__(self, url: str) -> None:
        self.url = url
        LOGGER.info("Discord webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        requests.post(url=self.url, json={**kwargs, "content": message})
