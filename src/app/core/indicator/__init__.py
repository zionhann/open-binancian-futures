from pandas import Series


def obv(
    close: Series,
    volume: Series,
    signal_sma: int | None = None,
    signal_ema: int | None = None,
) -> Series:
    close_diff = close.diff().fillna(0)
    direction = close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (volume * direction).cumsum()

    if signal_sma:
        ma = obv.rolling(window=signal_sma, min_periods=1).mean()
    elif signal_ema:
        ma = obv.ewm(span=signal_ema, adjust=False, min_periods=1).mean()
    else:
        ma = 0

    return obv - ma


def vwap(
    high: Series, low: Series, close: Series, volume: Series, length: int
) -> Series:
    typical_price = (high + low + close) / 3
    price_volume = typical_price * volume

    weighted_price_volume = price_volume.rolling(window=length, min_periods=1).sum()
    cumulative_volume = volume.rolling(window=length, min_periods=1).sum()

    vwap = weighted_price_volume / cumulative_volume
    vwap.name = f"VWAP_{length}"

    return vwap
