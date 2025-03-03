from pandas import Series


def vwap(
    high: Series, low: Series, close: Series, volume: Series, length: int
) -> Series:
    tp = (high + low + close) / 3
    tpv = tp * volume

    wpv = tpv.rolling(window=length, min_periods=1).sum()
    cumvol = volume.rolling(window=length, min_periods=1).sum()

    vwap = wpv / cumvol
    vwap.name = f"VWAP_{length}"

    return vwap
