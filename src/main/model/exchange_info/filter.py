from typing import Iterable

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponseSymbolsInnerFiltersInner,
)

from model.constant import FilterType


class Filter:
    def __init__(
            self, filters: Iterable[ExchangeInformationResponseSymbolsInnerFiltersInner]
    ) -> None:
        filter_types: dict[str, ExchangeInformationResponseSymbolsInnerFiltersInner] = {
            f.filter_type: f for f in filters if f.filter_type is not None
        }
        if (pf := filter_types.get(FilterType.PRICE_FILTER.value)) and pf.tick_size:
            self._tick_size = float(pf.tick_size)

        if (ls := filter_types.get(FilterType.LOT_SIZE.value)) and ls.step_size:
            self._step_size = float(ls.step_size)

        if (mn := filter_types.get(FilterType.MIN_NOTIONAL.value)) and mn.notional:
            self._min_notional = int(mn.notional)

    @property
    def tick_size(self) -> float:
        return self._tick_size

    @property
    def step_size(self) -> float:
        return self._step_size

    @property
    def min_notional(self) -> float:
        return self._min_notional
