import logging
import os
from openai import OpenAI


_LOGGER = logging.getLogger(__name__)
_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=_API_KEY)


def ask(input: str, model: str, **kwargs) -> str | None:
    try:
        response = client.responses.create(
            model=model,
            input=input,
            **kwargs,
        )
        return response.output_text
    except Exception as e:
        _LOGGER.error(f"Error in OpenAI API: {e}")
