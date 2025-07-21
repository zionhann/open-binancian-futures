import importlib
import logging
import pkgutil
import sys
import textwrap
from abc import ABC, abstractmethod
from typing import Any

import pandas as pd
from binance.um_futures import UMFutures
from pandas import DataFrame

from model.balance import Balance
from model.constant import *
from model.exchange_info import ExchangeInfo
from model.indicator import Indicator
from model.order import Order, OrderBook
from model.position import Position, PositionBook
from openapi import binance_futures as futures
from utils import decimal_places, fetch
from webhook import Webhook

BASIC_COLUMNS = [
    "Open_time",
    "Symbol",
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
]


class Strategy(ABC):
    LOGGER = logging.getLogger(__name__)
    is_imported = False

    @staticmethod
    def of(
            name: str | None,
            client: UMFutures,
            exchange_info: ExchangeInfo | None = None,
            balance: Balance | None = None,
            orders: OrderBook | None = None,
            positions: PositionBook | None = None,
            indicators: Indicator | None = None,
    ) -> "Strategy":
        if name is None:
            raise ValueError("Strategy is not specified")

        if name not in (strategies := Strategy._import_strategies()):
            raise ValueError(f"Invalid strategy: {name}")

        return strategies[name](
            client=client,
            exchange_info=exchange_info,
            balance=balance,
            orders=orders,
            positions=positions,
            indicators=indicators,
        )

    @staticmethod
    def _import_strategies() -> dict[str, type["Strategy"]]:
        if not Strategy.is_imported:
            for _, mod_name, _ in pkgutil.iter_modules(
                    sys.modules[__name__].__path__, __name__ + "."
            ):
                importlib.import_module(mod_name)
            Strategy.is_imported = True

        return {clazz.__name__: clazz for clazz in Strategy.__subclasses__()}

    def __init__(
            self,
            client: UMFutures,
            exchange_info: ExchangeInfo | None,
            balance: Balance | None,
            orders: OrderBook | None,
            positions: PositionBook | None,
            indicators: Indicator | None = None,
    ) -> None:
        self.client = client
        self.exchange_info = exchange_info or futures.init_exchange_info()
        self.balance = balance or futures.init_balance()
        self.orders = orders or futures.init_orders()
        self.positions = positions or futures.init_positions()
        self.webhook = Webhook.of(AppConfig.WEBHOOK_URL.value)

        self.indicators = indicators or futures.init_indicators()
        self.add_indicators(self.indicators)

    def add_indicators(self, indicator: Indicator) -> None:
        for symbol in AppConfig.SYMBOLS.value:
            df = indicator.get(symbol)[BASIC_COLUMNS]
            indicator.update(symbol, self.load(df))

            """
            # Use this if displaying rows in vertical alignment
            self._LOGGER.info(
                f"Loaded indicators for {symbol}:\n{initial_indicators.tail().T.to_string(header=False)}"
            )
            """
            self.LOGGER.info(
                f"Loaded indicators for {symbol}:\n{indicator.get(symbol).tail().to_string(index=False)}"
            )

    def calculate_stop_price(
            self,
            entry_price: float,
            order_type: OrderType,
            stop_side: PositionSide,
            tp_ratio: float | None = None,
            sl_ratio: float | None = None,
    ) -> float:
        sl_ratio = (sl_ratio or TPSL.STOP_LOSS_RATIO.value) / AppConfig.LEVERAGE.value
        tp_ratio = (tp_ratio or TPSL.TAKE_PROFIT_RATIO.value) / AppConfig.LEVERAGE.value

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
            price: float | None = None,
            stop_price: float | None = None,
            close_position: bool | None = None,
            order_types: list[OrderType] | None = None,
            quantity: float | None = None,
            tp_ratio: float | None = None,
            sl_ratio: float | None = None,
    ) -> None:
        if price is None and stop_price is None:
            raise ValueError("Either price or stop_price must be provided")

        _order_types = (
            order_types
            if order_types is not None
            else [
                OrderType.STOP_MARKET,
                OrderType.TAKE_PROFIT_MARKET,
            ]
        )

        for order_type in _order_types:
            reduce_only = None if close_position else True
            _stop_price = stop_price or self.calculate_stop_price(
                entry_price=price,
                order_type=order_type,
                stop_side=position_side,
                tp_ratio=tp_ratio,
                sl_ratio=sl_ratio,
            )
            _price = (_stop_price or price) if reduce_only else None
            _quantity = quantity if reduce_only else None

            fetch(
                self.client.new_order,
                symbol=symbol,
                side=position_side.value,
                type=order_type.value,
                price=_price,
                stopPrice=_stop_price,
                quantity=_quantity,
                closePosition=close_position,
                timeInForce=TimeInForce.GTE_GTC.value,
                reduceOnly=reduce_only,
            )

    def set_trailing_stop(
            self,
            symbol: str,
            position_side: PositionSide,
            quantity: float,
            price: float | None = None,
            activation_ratio: float | None = None,
            callback_ratio: float | None = None,
    ) -> None:
        activation_ratio = (activation_ratio or TS.ACTIVATION_RATIO.value) / AppConfig.LEVERAGE.value
        factor = (
            (1 + activation_ratio)
            if position_side == PositionSide.SELL
            else (1 - activation_ratio)
        )
        decimals = decimal_places(price) if price else None
        activation_price = round(price * factor, decimals) if price else None

        callback_ratio = (callback_ratio or TS.CALLBACK_RATIO.value) / AppConfig.LEVERAGE.value * 100
        safe_cb_rate = round(min(max(0.1, callback_ratio), 5), 2)

        fetch(
            self.client.new_order,
            symbol=symbol,
            side=position_side.value,
            type=OrderType.TRAILING_STOP_MARKET.value,
            quantity=quantity,
            timeInForce=TimeInForce.GTE_GTC.value,
            reduceOnly=True,
            activationPrice=activation_price,
            callbackRate=safe_cb_rate,
        )

    def on_new_candlestick(self, data: dict[str, Any]) -> None:
        if not data["x"]:
            return

        open_time = (
            pd.to_datetime(data["t"], unit="ms")
            .tz_localize("UTC")
            .tz_convert("Asia/Seoul")
        )
        symbol = data["s"]
        new_data = {
            "Open_time": open_time,
            "Symbol": symbol,
            "Open": float(data["o"]),
            "High": float(data["h"]),
            "Low": float(data["l"]),
            "Close": float(data["c"]),
            "Volume": float(data["v"]),
        }
        new_df = pd.DataFrame([new_data], index=[open_time])
        df = pd.concat([self.indicators.get(symbol), new_df])
        self.indicators.update(symbol, self.load(df))

        """
        # Use this if displaying rows in vertical alignment
        self._LOGGER.info(
            f"Updated indicators for {symbol}:\n{self.indicators[symbol].tail().T.to_string(header=False)}"
        )
        """
        self.LOGGER.info(
            f"Updated indicators for {symbol}:\n{self.indicators.get(symbol).tail().to_string(index=False)}"
        )
        self.run(symbol)

    def on_position_update(self, data: dict[str, str]) -> None:
        symbol, price, amount, bep = (
            data["s"],
            float(data["ep"]),
            float(data["pa"]),
            float(data["bep"]),
        )

        if price == 0.0 or amount == 0.0:
            self.positions.get(symbol).clear()
            return

        self.positions.get(symbol).update_positions(
            [
                Position(
                    symbol=symbol,
                    price=price,
                    amount=amount,
                    side=(PositionSide.BUY if amount > 0 else PositionSide.SELL),
                    leverage=AppConfig.LEVERAGE.value,
                    bep=bep,
                )
            ]
        )
        self.LOGGER.info(f"Updated positions: {self.positions.get(symbol)}")

    def on_balance_update(self, data: dict[str, str]) -> None:
        if data["a"] == "USDT":
            self.balance = Balance(float(data["cw"]))
            self.LOGGER.info(f"Updated available USDT: {self.balance}")

    def on_new_order(self, data: dict[str, Any]) -> None:
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
        valid_price = price or stop_price

        self.orders.get(symbol).add(
            Order(
                symbol=symbol,
                id=order_id,
                type=og_order_type,
                side=side,
                price=valid_price,
                quantity=quantity,
                gtd=gtd,
            )
        )

        self.LOGGER.info(
            textwrap.dedent(
                f"""
                Opened {og_order_type.value} order @ {valid_price}
                Symbol: {symbol}
                Side: {side.value}
                Quantity: {quantity or "N/A"}
                Reduce-Only: {is_reduce_only}
                """
            )
        )

    def on_filled_order(
            self,
            data: dict[str, Any],
            realized_profit: float,
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

        self.LOGGER.info(
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

        self.webhook.send_message(
            message=textwrap.dedent(
                f"""
                [{status.value}] {side.value} {filled} {symbol[:-4]} @ {average_price}
                Order: {og_order_type.value}
                PNL: {realized_profit} USDT
                """
            )
        )
        self.orders.get(symbol).remove_by_id(order_id)

    def on_cancelled_order(self, data: dict[str, Any]) -> None:
        symbol, order_id, og_order_type, side = (
            data["s"],
            data["i"],
            OrderType(data["ot"]),
            PositionSide(data["S"]),
        )
        self.orders.get(symbol).remove_by_id(order_id)
        self.LOGGER.info(
            f"{og_order_type.value} order for {symbol} has been cancelled. (Side: {side.value})"
        )

    def on_expired_order(self, data: dict[str, Any]) -> None:
        symbol, order_id, og_order_type, side = (
            data["s"],
            data["i"],
            OrderType(data["ot"]),
            PositionSide(data["S"]),
        )
        self.orders.get(symbol).remove_by_id(order_id)
        self.LOGGER.info(
            f"{og_order_type.value} order for {symbol} has expired. (Side: {side.value})"
        )

    @abstractmethod
    def load(self, df: DataFrame) -> DataFrame:
        ...

    @abstractmethod
    def run(self, symbol: str) -> None:
        ...

    @abstractmethod
    def run_backtest(self, symbol: str, index: int) -> None:
        ...
