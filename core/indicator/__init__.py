from pandas import DataFrame, Series


def rsi(data: DataFrame, window=14) -> Series:
    delta = data["Close"].astype(float).diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(span=window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(span=window, min_periods=window, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi_values = 100 - (100 / (1 + rs))

    return rsi_values


def obv_dif(data: DataFrame, window=7) -> Series:
    close_diff = data["Close"].diff()
    direction = close_diff.clip(lower=-1, upper=1)

    obv_values = (data["Volume"] * direction).cumsum()
    ema = obv_values.ewm(span=window, adjust=False).mean()

    return obv_values - ema


def macd(data: DataFrame, fast=12, slow=26, signal=9) -> Series:
    fast_ema = data["Close"].ewm(span=fast, adjust=False).mean()
    slow_ema = data["Close"].ewm(span=slow, adjust=False).mean()

    dif = fast_ema - slow_ema
    dea = dif.ewm(span=signal, adjust=False).mean()

    return dif - dea
