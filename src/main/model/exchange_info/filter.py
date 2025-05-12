from model.constant import FilterType


class Filter:
    def __init__(self, filters: list[dict]) -> None:
        filter_types = {f["filterType"]: f for f in filters}

        self._tick_size = float(filter_types[FilterType.PRICE_FILTER.value]["tickSize"])
        self._step_size = float(filter_types[FilterType.LOT_SIZE.value]["stepSize"])
        self._min_notional = int(
            filter_types[FilterType.MIN_NOTIONAL.value]["notional"]
        )

    @property
    def tick_size(self) -> float:
        return self._tick_size

    @property
    def step_size(self) -> float:
        return self._step_size

    @property
    def min_notional(self) -> float:
        return self._min_notional
