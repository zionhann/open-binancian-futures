from decimal import Decimal
import time
import logging
import json

from typing import Callable

from app.core.constant import (
    MIN_EXP,
    TO_MILLI,
    INTERVAL_TO_SECONDS,
    TPSL,
    AppConfig,
    OrderType,
    PositionSide,
)

CODE = "code"
MESSAGE = "msg"
OK = 200

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

    while True:
        try:
            res = request(**kwargs)

            if res is None:
                break
            elif isinstance(res, str):
                res = json.loads(res)

            if isinstance(res, list):
                return {"data": res}

            if CODE in res and res[CODE] != OK:
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


def calculate_stop_price(
    entry_price: float, order_type: OrderType, stop_side: PositionSide
) -> float:
    ratio = (
        TPSL.STOP_LOSS.value / AppConfig.LEVERAGE.value
        if order_type == OrderType.STOP
        else TPSL.TAKE_PROFIT.value / AppConfig.LEVERAGE.value
    )
    factor = (
        (1 - ratio)
        if (order_type == OrderType.STOP and stop_side == PositionSide.SELL)
        or (order_type == OrderType.TAKE_PROFIT and stop_side == PositionSide.BUY)
        else (1 + ratio)
    )
    decimals = decimal_places(entry_price)
    return round(entry_price * factor, decimals)


def gtd(nlines=3, to_milli=True) -> int:
    unit = AppConfig.INTERVAL.value[-1]
    base = INTERVAL_TO_SECONDS[unit] * int(AppConfig.INTERVAL.value[:-1])
    exp = max(base * nlines, MIN_EXP)
    gtd = int(time.time() + exp)

    return gtd * TO_MILLI if to_milli else gtd


def decimal_places(num: float) -> int:
    exponent = Decimal(str(num)).as_tuple().exponent
    return max(0, -int(exponent))
