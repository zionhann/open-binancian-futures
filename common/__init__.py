import math
import time
import pandas as pd
import logging

from pandas import DataFrame
from binance.um_futures import UMFutures
from utils import fetch
from core.constants import *
from core.strategy import Strategy
from core.strategy.rsi_macd import RSI_MACD
from core.balance import Balance

logger = logging.getLogger(__name__)


def init_strategy() -> Strategy:
    if AppConfig.STRATEGY.value == "RSI_MACD":
        return RSI_MACD()

    raise ValueError(f"Invalid strategy: {AppConfig.STRATEGY.value}")


def init_indicators(symbol: str, client: UMFutures, strategy: Strategy) -> DataFrame:
    logger.info(f"Fetching {symbol} klines by {AppConfig.INTERVAL.value}...")
    klines_data = fetch(
        client.klines, symbol=symbol, interval=AppConfig.INTERVAL.value
    )["data"][:-1]

    df = pd.DataFrame(data=klines_data, columns=KLINES_COLUMNS)
    df["Open_time"] = (
        pd.to_datetime(df["Open_time"], unit="ms")
        .dt.tz_localize("UTC")
        .dt.tz_convert("Asia/Seoul")
        .dt.strftime("%Y-%m-%d %H:%M")
    )
    df["Symbol"] = symbol
    df["Close"] = df["Close"].astype(float)
    df["Volume"] = df["Volume"].astype(float)

    initial_indicators = strategy.load(df)

    logger.info(
        f"Loaded indicators for {symbol}:\n{initial_indicators.tail().to_string(index=False)}"
    )
    return initial_indicators


def calculate_quantity(symbol: str, balance: Balance, entry_price: float) -> float:
    initial_margin = balance.get() * AppConfig.SIZE.value
    quantity = initial_margin * AppConfig.LEVERAGE.value / entry_price
    position_amount = math.floor(quantity * 1000) / 1000

    """
    Estimate the minimum notional value required for TP/SL orders.
    Since take profit ratio is usually higher than stop loss ratio, use take profit ratio on both positions.
    This guarantees that TP/SL orders can be placed without any nominal value issues.
    """
    factor = 1 - (TPSL.TAKE_PROFIT.value / AppConfig.LEVERAGE.value)
    notional = position_amount * (entry_price * factor)

    return position_amount if notional >= MIN_NOTIONAL[symbol] else 0.0


def gtd(line=2, to_milli=True) -> int:
    interval_to_seconds = {"m": 60, "h": 3600, "d": 86400}
    unit = AppConfig.INTERVAL.value[-1]
    base = interval_to_seconds[unit] * int(AppConfig.INTERVAL.value[:-1])
    exp = max(base * line, MIN_EXP)
    gtd = int(time.time() + exp)

    return gtd * TO_MILLI if to_milli else gtd


def calculate_stop_price(
    entry_price: float, order_type: OrderType, stop_side: PositionSide
) -> float:
    ratio = (
        TPSL.STOP_LOSS.value / AppConfig.LEVERAGE.value
        if order_type == OrderType.STOP
        else TPSL.TAKE_PROFIT.value / AppConfig.LEVERAGE.value
    )
    factor = (
        (1 - ratio)
        if (order_type == OrderType.STOP and stop_side == PositionSide.SELL)
        or (order_type == OrderType.TAKE_PROFIT and stop_side == PositionSide.BUY)
        else (1 + ratio)
    )

    return round(entry_price * factor, 1)
