from dataclasses import dataclass
from typing import Iterable

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponseSymbolsInnerFiltersInner,
)

from model.constant import FilterType


@dataclass(frozen=True)
class Filter:
    """Immutable filter data for symbol trading rules."""
    tick_size: float
    step_size: float
    min_notional: float

    @classmethod
    def from_binance(
            cls, filters: Iterable[ExchangeInformationResponseSymbolsInnerFiltersInner]
    ) -> "Filter":
        """Factory method to construct Filter from Binance API response."""
        filter_types: dict[str, ExchangeInformationResponseSymbolsInnerFiltersInner] = {
            f.filter_type: f for f in filters if f.filter_type is not None
        }

        tick_size = 0.0
        if (pf := filter_types.get(FilterType.PRICE_FILTER.value)) and pf.tick_size:
            tick_size = float(pf.tick_size)

        step_size = 0.0
        if (ls := filter_types.get(FilterType.LOT_SIZE.value)) and ls.step_size:
            step_size = float(ls.step_size)

        min_notional = 0.0
        if (mn := filter_types.get(FilterType.MIN_NOTIONAL.value)) and mn.notional:
            min_notional = float(mn.notional)

        return cls(
            tick_size=tick_size,
            step_size=step_size,
            min_notional=min_notional,
        )
