import time
from typing import Callable
import logging
from core import constants as const
import json

logger = logging.getLogger(__name__)


def check_required_params(**kwargs) -> None:
    for name, value in kwargs.items():
        if value is None:
            raise ValueError(f"Parameter '{name}' cannot be None")


def fetch(request: Callable, base_delay=0.25, max_retries=3, **kwargs) -> dict:
    """
    Fetch data from the server with exponential backoff.
    If the request fails, it will retry with a delay that increases exponentially.
    Do not use this function for requests that return None.

    :param request: The function to call.
    :param base_delay: The base delay in seconds; default to 0.25.
    :param max_retries: The maximum number of retries; default to 5.
    :param kwargs: The keyword arguments to pass to the request function.
    """
    retries = 0

    while retries < max_retries:
        try:
            res = request(**kwargs)

            if res is None:
                break
            elif isinstance(res, str):
                res = json.loads(res)

            if isinstance(res, list):
                return {"data": res}

            if const.CODE in res:
                raise Exception(res[const.MESSAGE])

            return res

        except Exception as e:
            logger.error(f"Fetch error: {e}", exc_info=True)

            if retries == max_retries:
                logger.error(f"Max retries ({max_retries}) reached.")
                raise

            delay = base_delay * (2**retries)
            retries += 1

            logger.info(
                f"Retrying in {delay:.2f} seconds... (Attempt {retries}/{max_retries})"
            )
            time.sleep(delay)

    return {}
