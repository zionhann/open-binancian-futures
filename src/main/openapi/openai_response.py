import logging
import os
from typing import Optional

from openai import AsyncOpenAI

LOGGER = logging.getLogger(__name__)
API_KEY = os.getenv("OPENAI_API_KEY")

client = AsyncOpenAI(api_key=API_KEY)


async def ask(input: str, model: str, **kwargs) -> Optional[str]:
    try:
        response = await client.responses.create(
            model=model,
            input=input,
            **kwargs,
        )
        return response.output_text
    except Exception as e:
        LOGGER.error(f"Error in OpenAI API: {e}")
        return None
