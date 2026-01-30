import logging
import os
from typing import Iterable, Optional
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, Message
from openai import AsyncOpenAI

LOGGER = logging.getLogger(__name__)


async def ask_anthropic(
    messages: Iterable[MessageParam], model: str, max_tokens: int, **kwargs
) -> Optional[Message]:
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            LOGGER.error("ANTHROPIC_API_KEY not configured")
            return None
        client = AsyncAnthropic(api_key=api_key)
        return await client.messages.create(
            max_tokens=max_tokens, model=model, messages=messages, **kwargs
        )
    except Exception as e:
        LOGGER.error(f"Error in Anthropic API: {e}")
        return None


async def ask_openai(input: str, model: str, **kwargs) -> Optional[str]:
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            LOGGER.error("OPENAI_API_KEY not configured")
            return None
        client = AsyncOpenAI(api_key=api_key)
        response = await client.responses.create(model=model, input=input, **kwargs)
        return response.output_text
    except Exception as e:
        LOGGER.error(f"Error in OpenAI API: {e}")
        return None
