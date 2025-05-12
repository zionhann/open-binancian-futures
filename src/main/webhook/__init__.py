from abc import ABC, abstractmethod

HOOKS_SLACK_PREFIX = "https://hooks.slack.com/services/"
HOOKS_DISCORD_PREFIX = "https://discord.com/api/webhooks/"


class Webhook(ABC):
    @staticmethod
    def of(url: str | None) -> "Webhook":
        if not url:
            from webhook.default_webhook import DefaultWebhook

            return DefaultWebhook()

        elif url.startswith(HOOKS_SLACK_PREFIX):
            from webhook.slack_webhook import SlackWebhook

            return SlackWebhook(url)

        elif url.startswith(HOOKS_DISCORD_PREFIX):
            from webhook.discord_webhook import DiscordWebhook

            return DiscordWebhook(url)

        raise ValueError(f"Unsupported webhook type: {url}")

    @abstractmethod
    def send_message(self, message: str, **kwargs): ...
