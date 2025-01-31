from pandas import DataFrame, Series


def RSI(data: DataFrame, window: int, decimals=1) -> Series:
    delta = data["Close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(span=window, adjust=False, min_periods=1).mean()
    avg_loss = loss.ewm(span=window, adjust=False, min_periods=1).mean()

    rs = avg_gain / avg_loss
    rsi_values = 100 - (100 / (1 + rs))

    return rsi_values.round(decimals)


def OBV(data: DataFrame, signal: int, decimals=1) -> Series:
    close_diff = data["Close"].diff().fillna(0)
    direction = close_diff.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))

    obv = (data["Volume"] * direction).cumsum()
    ema = obv.ewm(span=signal, adjust=False, min_periods=1).mean()

    return (obv - ema).round(decimals)


def MACD(data: DataFrame, fast: int, slow: int, signal: int, decimals=1) -> Series:
    fast_ema = data["Close"].ewm(span=fast, adjust=False, min_periods=1).mean()
    slow_ema = data["Close"].ewm(span=slow, adjust=False, min_periods=1).mean()

    dif = fast_ema - slow_ema
    dea = dif.ewm(span=signal, adjust=False, min_periods=1).mean()

    return (dif - dea).round(decimals)


def VMA(data: DataFrame, window: int, decimals=3) -> Series:
    return data["Volume"].rolling(window=window, min_periods=1).mean().round(decimals)


def VWAP(data: DataFrame, length: int, decimals=1) -> Series:
    typical_price = (data["High"] + data["Low"] + data["Close"]) / 3
    price_volume = typical_price * data["Volume"]

    weighted_price_volume = price_volume.rolling(window=length, min_periods=1).sum()
    cumulative_volume = data["Volume"].rolling(window=length, min_periods=1).sum()

    return (weighted_price_volume / cumulative_volume).round(decimals)
