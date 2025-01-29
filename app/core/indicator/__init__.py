from pandas import DataFrame, Series


def rsi(data: DataFrame, window=14) -> Series:
    delta = data["Close"].astype(float).diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(span=window, adjust=False, min_periods=1).mean()
    avg_loss = loss.ewm(span=window, adjust=False, min_periods=1).mean()

    rs = avg_gain / avg_loss
    rsi_values = 100 - (100 / (1 + rs))

    return rsi_values.round(2)


def obv_dif(data: DataFrame, window=10) -> Series:
    close_diff = data["Close"].diff()
    direction = close_diff.clip(lower=-1, upper=1)

    obv = (data["Volume"] * direction).cumsum()
    ema = obv.ewm(span=window, adjust=False, min_periods=1).mean()

    return (obv - ema).round(2)


def macd(data: DataFrame, fast=12, slow=26, signal=9) -> Series:
    fast_ema = data["Close"].ewm(span=fast, adjust=False, min_periods=1).mean()
    slow_ema = data["Close"].ewm(span=slow, adjust=False, min_periods=1).mean()

    dif = fast_ema - slow_ema
    dea = dif.ewm(span=signal, adjust=False, min_periods=1).mean()

    return (dif - dea).round(2)


def emavol(data: DataFrame, window=10) -> Series:
    return data["Volume"].ewm(span=window, adjust=False, min_periods=1).mean()


def vwap(data: DataFrame, length=14) -> Series:
    typical_price = (data["High"] + data["Low"] + data["Close"]) / 3
    price_volume = typical_price * data["Volume"]

    weighted_price_volume = price_volume.rolling(window=length, min_periods=1).sum()
    cumulative_volume = data["Volume"].rolling(window=length, min_periods=1).sum()

    return (weighted_price_volume / cumulative_volume).round(2)
