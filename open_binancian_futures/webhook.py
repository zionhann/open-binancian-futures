import logging
from abc import ABC, abstractmethod
from typing import override

import requests
from requests.exceptions import RequestException, Timeout
from slack_sdk.webhook import WebhookClient

LOGGER = logging.getLogger(__name__)

HOOKS_SLACK_PREFIX = "https://hooks.slack.com/services/"
HOOKS_DISCORD_PREFIX = "https://discord.com/api/webhooks/"
DEFAULT_TIMEOUT = 10  # seconds


class Webhook(ABC):
    @staticmethod
    def of(url: str | None) -> "Webhook":
        if not url:
            return DefaultWebhook()
        if "slack.com" in url:
            return SlackWebhook(url)
        if "discord.com" in url:
            return DiscordWebhook(url)
        raise ValueError(f"Unsupported webhook URL: {url}")

    @abstractmethod
    def send_message(self, message: str, **kwargs): ...


class DefaultWebhook(Webhook):
    def __init__(self) -> None:
        LOGGER.info("Default webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        pass


class SlackWebhook(Webhook):
    def __init__(self, url: str):
        self.client = WebhookClient(url)
        LOGGER.info("Slack webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs):
        self.client.send(text=message, **kwargs)


class DiscordWebhook(Webhook):
    def __init__(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout
        LOGGER.info("Discord webhook initialized.")

    @override
    def send_message(self, message: str, **kwargs) -> bool:
        """
        Send a message to Discord webhook with error handling.

        Args:
            message: Message content to send
            **kwargs: Additional JSON fields for Discord webhook

        Returns:
            bool: True if message was sent successfully, False otherwise
        """
        try:
            response = requests.post(
                url=self.url, json={**kwargs, "content": message}, timeout=self.timeout
            )
            response.raise_for_status()
            LOGGER.debug(f"Discord message sent successfully: {message[:100]}")
            return True

        except Timeout:
            LOGGER.error(f"Discord webhook timeout after {self.timeout}s")
            return False

        except RequestException as e:
            LOGGER.error(f"Discord webhook request failed: {e}")
            return False
