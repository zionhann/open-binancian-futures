import logging
import os
from typing import Iterable, Optional

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, Message


API_KEY = os.getenv("ANTHROPIC_API_KEY")
LOGGER = logging.getLogger(__name__)

client = AsyncAnthropic(api_key=API_KEY)


async def ask(
    messages: Iterable[MessageParam], model: str, max_tokens: int, **kwargs
) -> Optional[Message]:
    try:
        response: Message = await client.messages.create(
            max_tokens=max_tokens,
            model=model,
            messages=messages,
            **kwargs,
        )
        return response
    except Exception as e:
        LOGGER.error(f"Error in Anthropic API: {e}")
        return None
