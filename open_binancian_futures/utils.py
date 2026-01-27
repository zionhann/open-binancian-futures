import logging
from decimal import Decimal
from typing import Callable, TypeVar

from binance_common.models import ApiResponse

from open_binancian_futures.constants import INTERVAL_TO_SECONDS, settings

UNIT_MS = 1000
MIN_EXP = 660

logger = logging.getLogger(__name__)


def fetch(request: Callable, **kwargs):
    try:
        response: ApiResponse = request(**kwargs)
        return response.data()

    except Exception as e:
        logger.error(f"Fetch error: {e}", exc_info=True)
        raise


def gtd(timestamp: float, nlines: int) -> int:
    unit = settings.interval[-1]
    base = INTERVAL_TO_SECONDS[unit] * int(settings.interval[:-1])
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
