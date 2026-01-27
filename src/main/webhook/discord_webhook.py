import logging
from typing import override

import requests
from requests.exceptions import RequestException, Timeout

from webhook import Webhook

LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 10  # seconds


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
                url=self.url,
                json={**kwargs, "content": message},
                timeout=self.timeout
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
