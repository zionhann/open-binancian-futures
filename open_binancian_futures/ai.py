from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anthropic.types import Message, MessageParam

LOGGER = logging.getLogger(__name__)


async def ask_anthropic(
    messages: Iterable[MessageParam], model: str, max_tokens: int, **kwargs
) -> Message | None:
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            LOGGER.error("ANTHROPIC_API_KEY not configured")
            return None
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        return await client.messages.create(
            max_tokens=max_tokens, model=model, messages=messages, **kwargs
        )
    except ImportError:
        LOGGER.error("Install open-binancian-futures[ai] to use AI helpers")
        return None
    except Exception as e:
        LOGGER.error(f"Error in Anthropic API: {e}")
        return None


async def ask_openai(input: str, model: str, **kwargs) -> str | None:
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            LOGGER.error("OPENAI_API_KEY not configured")
            return None
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        response = await client.responses.create(model=model, input=input, **kwargs)
        return response.output_text
    except ImportError:
        LOGGER.error("Install open-binancian-futures[ai] to use AI helpers")
        return None
    except Exception as e:
        LOGGER.error(f"Error in OpenAI API: {e}")
        return None
