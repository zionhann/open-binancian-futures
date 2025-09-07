import json
import logging
import time
from decimal import Decimal
from typing import Callable, TypeVar

from model.constant import INTERVAL_TO_SECONDS, AppConfig

CODE = "code"
MESSAGE = "msg"
STATUS_OK = 200
UNIT_MS = 1000
MIN_EXP = 660

logger = logging.getLogger(__name__)


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

    while True:
        try:
            res = request(**kwargs)

            if res is None:
                break
            elif isinstance(res, str):
                res = json.loads(res)

            if isinstance(res, list):
                return {"data": res}

            if CODE in res and res[CODE] != STATUS_OK:
                raise Exception(res[MESSAGE])

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


def gtd(timestamp: float, nlines: int) -> int:
    unit = AppConfig.INTERVAL[-1]
    base = INTERVAL_TO_SECONDS[unit] * int(AppConfig.INTERVAL[:-1])
    exp = max(base * nlines, MIN_EXP)
    gtd = int(timestamp + exp)
    return gtd * UNIT_MS


def decimal_places(num: float) -> int:
    exponent = Decimal(str(num)).as_tuple().exponent
    return max(0, -int(exponent))


T = TypeVar("T")


def get_or_raise(
        value: T | None,
        raisable: Callable[[], Exception] = lambda: ValueError("Value is None"),
) -> T:
    if value is None:
        raise raisable()
    return value
