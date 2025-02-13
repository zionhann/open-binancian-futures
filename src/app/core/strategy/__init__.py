import logging
import pandas as pd
import pkgutil
import importlib
import sys

from abc import ABC, abstractmethod
from pandas import DataFrame
from binance.um_futures import UMFutures
from app.core.balance import Balance
from app.core.exchange_info import ExchangeInfo
from app.core.position import Positions
from app.core.order import Orders
from app.core.constant import *
from app.utils import decimal_places, fetch


class Strategy(ABC):
    _LOGGER = logging.getLogger(__name__)
    _KLINES_COLUMNS = [
        "Open_time",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Close_time",
        "Quote_volume",
        "Trades",
        "Taker_buy_volume",
        "Taker_buy_quote_volume",
        "Ignore",
    ]
    _BASIC_COLUMNS = [
        "Open_time",
        "Symbol",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]
    _is_imported = False

    @staticmethod
    def of(strategy_name: str | None) -> "Strategy":
        if strategy_name is None:
            raise ValueError("Strategy is not specified")

        if strategy_name not in (strategies := Strategy._import_strategies()):
            raise ValueError(f"Invalid strategy: {strategy_name}")

        return strategies[strategy_name]()

    @staticmethod
    def _import_strategies() -> dict[str, type["Strategy"]]:
        if not Strategy._is_imported:
            for _, mod_name, _ in pkgutil.iter_modules(
                sys.modules[__name__].__path__, __name__ + "."
            ):
                importlib.import_module(mod_name)
            Strategy._is_imported = True

        return {clazz.__name__: clazz for clazz in Strategy.__subclasses__()}

    def __init__(
        self,
        interval=AppConfig.INTERVAL.value,
        size=AppConfig.SIZE.value,
        leverage=AppConfig.LEVERAGE.value,
        gtd_nlines=AppConfig.GTD_NLINES.value,
        take_profit_ratio=TPSL.TAKE_PROFIT_RATIO.value,
        stop_loss_ratio=TPSL.STOP_LOSS_RATIO.value,
        activation_ratio=TS.ACTIVATION_RATIO.value,
        callback_ratio=TS.CALLBACK_RATIO.value,
    ) -> None:
        self.interval = interval
        self.size = size
        self.leverage = leverage
        self.gtd_nlines = gtd_nlines
        self.take_profit_ratio = take_profit_ratio
        self.stop_loss_ratio = stop_loss_ratio
        self.activation_ratio = activation_ratio
        self.callback_ratio = callback_ratio

    def init_indicators(
        self, symbol: str, client: UMFutures, limit: int | None = None
    ) -> DataFrame:
        self._LOGGER.info(f"Fetching {symbol} klines by {self.interval}...")
        klines_data = fetch(
            client.klines, symbol=symbol, interval=self.interval, limit=limit
        )["data"][:-1]

        df = pd.DataFrame(data=klines_data, columns=self._KLINES_COLUMNS)
        df["Open_time"] = (
            pd.to_datetime(df["Open_time"], unit="ms")
            .dt.tz_localize("UTC")
            .dt.tz_convert("Asia/Seoul")
        )
        df.set_index("Open_time", inplace=True, drop=False)

        df["Symbol"] = symbol
        df["Open"] = df["Open"].astype(float)
        df["High"] = df["High"].astype(float)
        df["Low"] = df["Low"].astype(float)
        df["Close"] = df["Close"].astype(float)
        df["Volume"] = df["Volume"].astype(float)

        initial_indicators = self.load(df[self._BASIC_COLUMNS])

        self._LOGGER.info(
            f"Loaded indicators for {symbol}:\n{initial_indicators.tail().to_string(index=False)}"
        )
        return initial_indicators

    def calculate_stop_price(
        self, entry_price: float, order_type: OrderType, stop_side: PositionSide
    ) -> float:
        ratio = (
            self.stop_loss_ratio / self.leverage
            if order_type == OrderType.STOP_MARKET
            else self.take_profit_ratio / self.leverage
        )
        factor = (
            (1 - ratio)
            if (order_type == OrderType.STOP_MARKET and stop_side == PositionSide.SELL)
            or (
                order_type == OrderType.TAKE_PROFIT_MARKET
                and stop_side == PositionSide.BUY
            )
            else (1 + ratio)
        )
        decimals = decimal_places(entry_price)
        return round(entry_price * factor, decimals)

    def set_tpsl(
        self,
        symbol: str,
        position_side: PositionSide,
        price: float,
        client: UMFutures,
        order_types: list[OrderType] | None = None,
    ) -> None:
        order_types = order_types or [
            OrderType.STOP_MARKET,
            OrderType.TAKE_PROFIT_MARKET,
        ]

        for order_type in order_types:
            stop_price = self.calculate_stop_price(price, order_type, position_side)

            fetch(
                client.new_order,
                symbol=symbol,
                side=position_side.value,
                type=order_type.value,
                stopPrice=stop_price,
                closePosition=True,
                timeInForce=TimeInForce.GTE_GTC.value,
            )

    def set_trailing_stop(
        self,
        symbol: str,
        position_side: PositionSide,
        quantity: float,
        client: UMFutures,
        price: float | None = None,
        activation_ratio: float | None = None,
        callback_ratio: float | None = None,
    ) -> None:
        activation_ratio = activation_ratio or (self.activation_ratio / self.leverage)
        factor = (
            (1 + activation_ratio)
            if position_side == PositionSide.SELL
            else (1 - activation_ratio)
        )
        decimals = decimal_places(price) if price else None
        activation_price = round(price * factor, decimals) if price else None

        callback_ratio = callback_ratio or (self.callback_ratio / self.leverage * 100)
        safe_cb_rate = round(min(max(0.1, callback_ratio), 5), 2)

        fetch(
            client.new_order,
            symbol=symbol,
            side=position_side.value,
            type=OrderType.TRAILING_STOP_MARKET.value,
            quantity=quantity,
            timeInForce=TimeInForce.GTE_GTC.value,
            reduceOnly=True,
            activationPrice=activation_price,
            callbackRate=safe_cb_rate,
        )

    @abstractmethod
    def load(self, df: DataFrame) -> DataFrame: ...

    @abstractmethod
    def run(
        self,
        indicators: DataFrame,
        positions: Positions,
        orders: Orders,
        exchange_info: ExchangeInfo,
        balance: Balance,
        client: UMFutures,
    ) -> None: ...

    @abstractmethod
    def run_backtest(
        self,
        indicators: DataFrame,
        positions: Positions,
        orders: Orders,
        exchange_info: ExchangeInfo,
        balance: Balance,
    ) -> None: ...
