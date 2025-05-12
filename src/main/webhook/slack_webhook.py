import logging
from typing import override

from slack_sdk.webhook import WebhookClient

from webhook import Webhook

LOGGER = logging.getLogger(__name__)


class SlackWebhook(Webhook):
    def __init__(self, url: str):
        self.client = WebhookClient(url)
        LOGGER.info("Slack webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        self.client.send(text=message, **kwargs)
