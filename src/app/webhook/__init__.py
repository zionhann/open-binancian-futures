from abc import ABC, abstractmethod


class Webhook(ABC):
    _HOOKS_SLACK_PREFIX = "https://hooks.slack.com/services/"
    _HOOKS_DISCORD_PREFIX = "https://discord.com/api/webhooks/"

    @staticmethod
    def of(url: str | None) -> "Webhook":
        if not url:
            from app.webhook.default_webhook import DefaultWebhook

            return DefaultWebhook()

        elif url.startswith(Webhook._HOOKS_SLACK_PREFIX):
            from app.webhook.slack_webhook import SlackWebhook

            return SlackWebhook(url)

        elif url.startswith(Webhook._HOOKS_DISCORD_PREFIX):
            from app.webhook.discord_webhook import DiscordWebhook

            return DiscordWebhook(url)

        raise ValueError(f"Unsupported webhook type: {url}")

    @abstractmethod
    def send_message(self, message: str, **kwargs): ...
