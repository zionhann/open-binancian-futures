import asyncio
import importlib
import logging
import textwrap
from abc import ABC, abstractmethod

import pandas as pd
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewAlgoOrderSideEnum,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    KlineCandlestickStreamsResponseK,
    OrderTradeUpdateO,
    AccountUpdateAPInner,
    AccountUpdateABInner,
)
from pandas import DataFrame

from model.balance import Balance
from model.constant import *
from model.exchange_info import ExchangeInfo
from model.indicator import Indicator
from model.order import Order, OrderBook
from model.position import Position, PositionBook
from openapi import binance_futures as futures
from utils import decimal_places, fetch, get_or_raise
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

    @staticmethod
    def of(
        name: str | None,
        client: DerivativesTradingUsdsFutures,
        exchange_info: ExchangeInfo | None = None,
        balance: Balance | None = None,
        orders: OrderBook | None = None,
        positions: PositionBook | None = None,
        webhook: Webhook | None = None,
        indicators: Indicator | None = None,
        lock: asyncio.Lock | None = None,
    ) -> "Strategy":
        if name is None:
            raise ValueError("Strategy is not specified")

        if (strategy := Strategy._import_strategy(name)) is None:
            raise ValueError(f"Invalid strategy: {name}")

        return strategy(
            client=client,
            exchange_info=exchange_info,
            balance=balance,
            orders=orders,
            positions=positions,
            webhook=webhook,
            indicators=indicators,
            lock=lock,
        )

    @staticmethod
    def _import_strategy(strategy_name: str) -> type["Strategy"] | None:
        """Import strategy by file name and return the Strategy subclass."""
        try:
            mod = importlib.import_module(f"{__name__}.{strategy_name}")
            subclasses = [
                cls
                for cls in mod.__dict__.values()
                if isinstance(cls, type)
                and issubclass(cls, Strategy)
                and cls is not Strategy
            ]
            return subclasses[0] if subclasses else None
        except ImportError:
            Strategy.LOGGER.error(f"Failed to import strategy '{strategy_name}':")
            return None

    def __init__(
        self,
        client: DerivativesTradingUsdsFutures,
        exchange_info: ExchangeInfo | None,
        balance: Balance | None,
        orders: OrderBook | None,
        positions: PositionBook | None,
        webhook: Webhook | None,
        indicators: Indicator | None,
        lock: asyncio.Lock | None = None,
    ) -> None:
        self.client = client
        self.exchange_info = exchange_info or futures.init_exchange_info()
        self.balance = balance or futures.init_balance()
        self.orders = orders or futures.init_orders()
        self.positions = positions or futures.init_positions()
        self.webhook = webhook or Webhook.of(url=None)
        self.indicators = indicators or futures.init_indicators()
        self.add_indicators(self.indicators)
        self.lock = lock

    def add_indicators(self, indicator: Indicator) -> None:
        for symbol in AppConfig.SYMBOLS:
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
        symbol: str,
        entry_price: float,
        order_type: OrderType,
        stop_side: PositionSide,
        tp_ratio: float | None = None,
        sl_ratio: float | None = None,
    ) -> float:
        sl_ratio = (sl_ratio or Bracket.STOP_LOSS_RATIO) / AppConfig.LEVERAGE
        tp_ratio = (tp_ratio or Bracket.TAKE_PROFIT_RATIO) / AppConfig.LEVERAGE

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
        return self.exchange_info.to_entry_price(symbol, entry_price * factor)

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
                symbol=symbol,
                entry_price=get_or_raise(
                    price,
                    lambda: ValueError("Either price or stop_price must be provided"),
                ),
                order_type=order_type,
                stop_side=position_side,
                tp_ratio=tp_ratio,
                sl_ratio=sl_ratio,
            )
            _price = (_stop_price or price) if reduce_only else None
            _quantity = quantity if reduce_only else None

            fetch(
                self.client.rest_api.new_algo_order,
                algo_type="CONDITIONAL",
                symbol=symbol,
                side=position_side.value,
                type=order_type.value,
                price=_price,
                trigger_price=self.exchange_info.to_entry_price(symbol, _stop_price),
                quantity=_quantity,
                close_position=close_position,
                time_in_force=TimeInForce.GTE_GTC.value,
                reduce_only=reduce_only,
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
        activation_ratio = (
            activation_ratio or TrailingStop.ACTIVATION_RATIO
        ) / AppConfig.LEVERAGE
        factor = (
            (1 + activation_ratio)
            if position_side == PositionSide.SELL
            else (1 - activation_ratio)
        )
        decimals = decimal_places(price) if price else None
        activation_price = round(price * factor, decimals) if price else None

        callback_ratio = (
            (callback_ratio or TrailingStop.CALLBACK_RATIO) / AppConfig.LEVERAGE * 100
        )
        safe_cb_rate = round(min(max(0.1, callback_ratio), 5), 2)

        fetch(
            self.client.rest_api.new_order,
            symbol=symbol,
            side=position_side.value,
            type=OrderType.TRAILING_STOP_MARKET.value,
            quantity=quantity,
            time_in_force=TimeInForce.GTE_GTC.value,
            reduce_only=True,
            activation_price=activation_price,
            callback_rate=safe_cb_rate,
        )

    async def on_new_candlestick(self, data: KlineCandlestickStreamsResponseK) -> None:
        if not get_or_raise(data.x):
            return

        open_time = (
            pd.to_datetime(get_or_raise(data.t), unit="ms")
            .tz_localize("UTC")
            .tz_convert(AppConfig.TIMEZONE)
        )
        symbol = get_or_raise(data.s)
        new_data = {
            "Open_time": open_time,
            "Symbol": symbol,
            "Open": float(get_or_raise(data.o)),
            "High": float(get_or_raise(data.h)),
            "Low": float(get_or_raise(data.l)),
            "Close": float(get_or_raise(data.c)),
            "Volume": float(get_or_raise(data.v)),
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
        await self.run(symbol)

    def on_position_update(self, data: AccountUpdateAPInner) -> None:
        symbol, price, amount, bep = (
            get_or_raise(data.s),
            float(get_or_raise(data.ep)),
            float(get_or_raise(data.pa)),
            float(get_or_raise(data.bep)),
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
                    leverage=AppConfig.LEVERAGE,
                    bep=bep,
                )
            ]
        )
        self.LOGGER.info(f"Updated positions: {self.positions.get(symbol)}")

    def on_balance_update(self, data: AccountUpdateABInner) -> None:
        if data.a == "USDT":
            self.balance = Balance(float(get_or_raise(data.cw)))
            self.LOGGER.info(f"Updated available USDT: {self.balance}")

    def on_new_order(self, data: OrderTradeUpdateO) -> None:
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
            int(get_or_raise(data.i)),
            str(get_or_raise(data.s)),
            OrderType(get_or_raise(data.ot)),
            PositionSide(get_or_raise(data.S)),
            float(get_or_raise(data.p)),
            float(get_or_raise(data.sp)),
            float(get_or_raise(data.q)),
            int(get_or_raise(data.gtd)),
            bool(get_or_raise(data.R)),
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
                Reduce-only: {is_reduce_only}
                """
            )
        )

    def on_filled_order(
        self,
        data: OrderTradeUpdateO,
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
            int(get_or_raise(data.i)),
            str(get_or_raise(data.s)),
            OrderType(get_or_raise(data.ot)),
            PositionSide(get_or_raise(data.S)),
            float(get_or_raise(data.p)),
            float(get_or_raise(data.z)),
            float(get_or_raise(data.q)),
            OrderStatus(get_or_raise(data.X)),
        )
        average_price = round(float(get_or_raise(data.ap)), decimal_places(price))
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
                [{status.value}] {symbol[:-4]} {side.value} {filled} @ {average_price}
                Order: {og_order_type.value}
                PNL: {realized_profit} USDT
                """
            )
        )
        self.orders.get(symbol).remove_by_id(order_id)

    def on_cancelled_order(self, data: OrderTradeUpdateO) -> None:
        symbol, order_id, og_order_type, side = (
            get_or_raise(data.s),
            get_or_raise(data.i),
            OrderType(get_or_raise(data.ot)),
            PositionSide(get_or_raise(data.S)),
        )
        self.orders.get(symbol).remove_by_id(order_id)
        self.LOGGER.info(
            f"{og_order_type.value} order for {symbol} has been cancelled. (Side: {side.value})"
        )

    def on_expired_order(self, data: OrderTradeUpdateO) -> None:
        symbol, order_id, og_order_type, side = (
            get_or_raise(data.s),
            get_or_raise(data.i),
            OrderType(get_or_raise(data.ot)),
            PositionSide(get_or_raise(data.S)),
        )
        self.orders.get(symbol).remove_by_id(order_id)
        self.LOGGER.info(
            f"{og_order_type.value} order for {symbol} has expired. (Side: {side.value})"
        )

    @abstractmethod
    def load(self, df: DataFrame) -> DataFrame: ...

    @abstractmethod
    async def run(self, symbol: str) -> None: ...

    @abstractmethod
    async def run_backtest(self, symbol: str, index: int) -> None: ...
