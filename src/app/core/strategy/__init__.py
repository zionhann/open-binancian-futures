import logging
import textwrap
from typing import Any
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
from app.core.order import Order, Orders
from app.core.constant import *
from app.utils import decimal_places, fetch
from app.webhook import Webhook


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
        symbols: list[str] = AppConfig.SYMBOLS.value,
        interval: str = AppConfig.INTERVAL.value,
        size: float = AppConfig.SIZE.value,
        max_averaging: int = AppConfig.MAX_AVERAGING.value,
        leverage: int = AppConfig.LEVERAGE.value,
        gtd_nlines: int = AppConfig.GTD_NLINES.value,
        take_profit_ratio: float = TPSL.TAKE_PROFIT_RATIO.value,
        stop_loss_ratio: float = TPSL.STOP_LOSS_RATIO.value,
        activation_ratio: float = TS.ACTIVATION_RATIO.value,
        callback_ratio: float = TS.CALLBACK_RATIO.value,
    ) -> None:
        self.symbols = symbols
        self.interval = interval
        self.size = size
        self.max_averaging = max_averaging
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

        """
        # Use this if displaying rows in vertical alignment
        self._LOGGER.info(
            f"Loaded indicators for {symbol}:\n{initial_indicators.tail().T.to_string(header=False)}"
        )
        """
        self._LOGGER.info(
            f"Loaded indicators for {symbol}:\n{initial_indicators.tail().to_string(index=False)}"
        )
        return initial_indicators

    def calculate_stop_price(
        self,
        entry_price: float,
        order_type: OrderType,
        stop_side: PositionSide,
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
    ) -> float:
        sl_ratio = (sl_ratio or self.stop_loss_ratio) / self.leverage
        tp_ratio = (tp_ratio or self.take_profit_ratio) / self.leverage

        ratio = (
            sl_ratio
            if order_type in [OrderType.STOP_MARKET, OrderType.STOP_LIMIT]
            else tp_ratio
        )
        factor = (
            (1 - ratio)
            if (
                order_type in [OrderType.STOP_MARKET, OrderType.STOP_LIMIT]
                and stop_side == PositionSide.SELL
            )
            or (
                order_type
                in [OrderType.TAKE_PROFIT_MARKET, OrderType.TAKE_PROFIT_LIMIT]
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
        close_position: bool | None = None,
        order_types: list[OrderType] | None = None,
        quantity: float | None = None,
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
    ) -> None:
        reduce_only = None if close_position else True
        quantity = quantity if reduce_only else None
        order_types = (
            order_types
            if order_types is not None
            else [
                OrderType.STOP_MARKET,
                OrderType.TAKE_PROFIT_MARKET,
            ]
        )

        for order_type in order_types:
            stop_price = self.calculate_stop_price(
                entry_price=price,
                order_type=order_type,
                stop_side=position_side,
                tp_ratio=tp_ratio,
                sl_ratio=sl_ratio,
            )
            _price = stop_price if reduce_only else None

            fetch(
                client.new_order,
                symbol=symbol,
                side=position_side.value,
                type=order_type.value,
                stopPrice=stop_price,
                closePosition=close_position,
                timeInForce=TimeInForce.GTE_GTC.value,
                price=_price,
                quantity=quantity,
                reduceOnly=reduce_only,
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
        activation_ratio = (activation_ratio or self.activation_ratio) / self.leverage
        factor = (
            (1 + activation_ratio)
            if position_side == PositionSide.SELL
            else (1 - activation_ratio)
        )
        decimals = decimal_places(price) if price else None
        activation_price = round(price * factor, decimals) if price else None

        callback_ratio = (callback_ratio or self.callback_ratio) / self.leverage * 100
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

    def on_new_order(
        self,
        data: dict[str, Any],
        orders: Orders,
        positions: Positions,
        exchange_info: ExchangeInfo,
        client: UMFutures,
    ) -> None:
        (
            order_id,
            symbol,
            og_order_type,
            side,
            price,
            stop_price,
            quantity,
            gtd,
            is_reduce_only,
        ) = (
            int(data["i"]),
            str(data["s"]),
            OrderType(data["ot"]),
            PositionSide(data["S"]),
            float(data["p"]),
            float(data["sp"]),
            float(data["q"]),
            int(data["gtd"]),
            bool(data["R"]),
        )

        orders.add(
            Order(
                symbol=symbol,
                id=order_id,
                type=og_order_type,
                side=side,
                price=price,
                quantity=quantity,
                gtd=gtd,
            )
        )

        self._LOGGER.info(
            textwrap.dedent(
                f"""
                Opened {og_order_type.value} order @ {price if price else stop_price}
                Symbol: {symbol}
                Side: {side.value}
                Quantity: {quantity if quantity else "N/A"}
                Reduce-Only: {is_reduce_only}
                """
            )
        )

    def on_filled_order(
        self,
        data: dict[str, Any],
        orders: Orders,
        positions: Positions,
        realized_profit: float,
        exchange_info: ExchangeInfo,
        client: UMFutures,
        webhook: Webhook,
    ) -> None:
        (
            order_id,
            symbol,
            og_order_type,
            side,
            price,
            filled,
            quantity,
            status,
        ) = (
            int(data["i"]),
            str(data["s"]),
            OrderType(data["ot"]),
            PositionSide(data["S"]),
            float(data["p"]),
            float(data["z"]),
            float(data["q"]),
            OrderStatus(data["X"]),
        )
        average_price = round(float(data["ap"]), decimal_places(price))
        filled_percentage = (filled / quantity) * 100

        self._LOGGER.info(
            textwrap.dedent(
                f"""
                {og_order_type.value} order has been filled @ {average_price}
                Symbol: {symbol}
                Side: {side.value}
                Quantity: {filled}/{quantity} {symbol[:-4]} ({filled_percentage:.2f}%)
                Realized Profit: {realized_profit} USDT
                """
            )
        )

        webhook.send_message(
            message=textwrap.dedent(
                f"""
                [{status.value}] {side.value} {filled} {symbol[:-4]} @ {average_price}
                Order: {og_order_type.value}
                PNL: {realized_profit} USDT
                """
            )
        )

        orders.remove_by_id(order_id)

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
        webhook: Webhook,
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
