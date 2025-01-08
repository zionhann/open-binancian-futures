from pandas import DataFrame, Series


def rsi(data: DataFrame, window=14) -> Series:
    delta = data["Close"].astype(float).diff()

    gain = (delta.clip(lower=0)).ewm(span=window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=window, adjust=False).mean()

    rs = gain / loss
    rsi_values = 100 - (100 / (1 + rs))

    return rsi_values
