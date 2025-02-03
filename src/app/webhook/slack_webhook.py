import logging

from typing import override
from slack_sdk.webhook import WebhookClient
from app.webhook import Webhook


class SlackWebhook(Webhook):
    _LOGGER = logging.getLogger(__name__)

    def __init__(self, url: str):
        self.client = WebhookClient(url)
        SlackWebhook._LOGGER.info("Slack webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        self.client.send(text=message, **kwargs)
