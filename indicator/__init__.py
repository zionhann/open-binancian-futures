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
