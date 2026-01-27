import asyncio
import textwrap
import time
from typing import Optional, cast, override

import pandas_ta as ta
from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewOrderSideEnum,
    NewOrderTimeInForceEnum,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    OrderTradeUpdateO,
)
from pandas import DataFrame
from model.balance import Balance
from model.constant import (
    AppConfig,
    OrderType,
    PositionSide,
)
from model.exchange_info import ExchangeInfo
from model.indicator import Indicator
from model.order import OrderBook
from model.position import PositionBook
from strategy import Strategy
from utils import decimal_places, fetch, gtd, get_or_raise
from webhook import Webhook


class Example(Strategy):
    def __init__(
        self,
        client: DerivativesTradingUsdsFutures,
        exchange_info: ExchangeInfo,
        balance: Balance,
        orders: OrderBook,
        positions: PositionBook,
        webhook: Webhook,
        indicators: Indicator,
        lock: Optional[asyncio.Lock],
    ) -> None:
        super().__init__(
            client=client,
            exchange_info=exchange_info,
            balance=balance,
            orders=orders,
            positions=positions,
            webhook=webhook,
            indicators=indicators,
            lock=lock,
        )

    @override
    def load(self, df: DataFrame) -> DataFrame:
        close = df["Close"]
        current_close = close.iloc[-1]
        price_decimals = decimal_places(current_close)

        indicators = {}

        if (rsi := ta.rsi(close, length=14)) is not None:
            indicators["RSI"] = rsi.round(price_decimals)

        return df.assign(**indicators)

    @override
    async def run(self, symbol: str) -> None:
        latest = self.indicators.get(symbol).iloc[-1]

        (high, low, close, rsi) = (
            latest["High"],
            latest["Low"],
            latest["Close"],
            latest["RSI"],
        )
        orders, positions = self.orders.get(symbol), self.positions.get(symbol)

        if orders.has_type(OrderType.LIMIT) or positions:
            self.LOGGER.info(
                f"Skipping {symbol} as there are existing orders/positions."
            )
            return

        # BUY/LONG Signal
        if rsi < 30:
            typical_price = (high + low + close) / 3.0
            entry_price = self.exchange_info.to_entry_price(symbol, typical_price)

            async with cast(asyncio.Lock, self.lock):
                if entry_quantity := self.exchange_info.to_entry_quantity(
                    symbol=symbol,
                    entry_price=entry_price,
                    size=AppConfig.SIZE * AppConfig.AVERAGING[0],
                    leverage=AppConfig.LEVERAGE,
                    balance=self.balance,
                ):
                    fetch(
                        self.client.rest_api.new_order,
                        symbol=symbol,
                        side=NewOrderSideEnum.BUY,
                        type=OrderType.LIMIT.value,
                        price=float(entry_price),
                        quantity=float(entry_quantity),
                        time_in_force=NewOrderTimeInForceEnum.GTD,
                        good_till_date=gtd(time.time(), AppConfig.GTD_NLINES),
                    )

        # SELL/SHORT Signal
        elif rsi > 70:
            typical_price = (high + low + close) / 3.0
            entry_price = self.exchange_info.to_entry_price(symbol, typical_price)

            async with cast(asyncio.Lock, self.lock):
                if entry_quantity := self.exchange_info.to_entry_quantity(
                    symbol=symbol,
                    entry_price=entry_price,
                    size=AppConfig.SIZE * AppConfig.AVERAGING[0],
                    leverage=AppConfig.LEVERAGE,
                    balance=self.balance,
                ):
                    fetch(
                        self.client.rest_api.new_order,
                        symbol=symbol,
                        side=NewOrderSideEnum.SELL,
                        type=OrderType.LIMIT.value,
                        price=float(entry_price),
                        quantity=float(entry_quantity),
                        time_in_force=NewOrderTimeInForceEnum.GTD,
                        good_till_date=gtd(time.time(), AppConfig.GTD_NLINES),
                    )

    @override
    def on_new_order(self, data: OrderTradeUpdateO) -> None:
        super().on_new_order(data=data)

    @override
    def on_filled_order(
        self,
        data: OrderTradeUpdateO,
        realized_profit: float,
    ) -> None:
        super().on_filled_order(
            data=data,
            realized_profit=realized_profit,
        )

        (og_order_type, symbol, side, price, quantity, reduce_only) = (
            OrderType(get_or_raise(data.ot)),
            get_or_raise(data.s),
            PositionSide(get_or_raise(data.S)),
            float(get_or_raise(data.p)),
            float(get_or_raise(data.q)),
            bool(get_or_raise(data.R)),
        )

        if og_order_type == OrderType.LIMIT and not reduce_only:
            stop_side = (
                PositionSide.SELL if side == PositionSide.BUY else PositionSide.BUY
            )
            self.positions.get(symbol).entry_count += 1

            for order_type in [OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET]:
                if not self.orders.get(symbol).has_type(order_type):
                    self.set_tpsl(
                        symbol=symbol,
                        position_side=stop_side,
                        price=price,
                        close_position=True,
                        order_types=[order_type],
                    )

    @override
    def run_backtest(self, symbol: str, index: int) -> None:
        latest = self.indicators.get(symbol).iloc[index]

        (date, high, low, close, rsi) = (
            latest["Open_time"],
            latest["High"],
            latest["Low"],
            latest["Close"],
            latest["RSI"],
        )

        orders, positions = self.orders.get(symbol), self.positions.get(symbol)
        gtd_time = gtd(int(date.timestamp()), AppConfig.GTD_NLINES)

        if orders.has_type(OrderType.LIMIT) or positions:
            return

        if rsi < 30:
            typical_price = (high + low + close) / 3.0
            entry_price = self.exchange_info.to_entry_price(symbol, typical_price)

            if entry_quantity := self.exchange_info.to_entry_quantity(
                symbol=symbol,
                entry_price=entry_price,
                size=AppConfig.SIZE,
                leverage=AppConfig.LEVERAGE,
                balance=self.balance,
            ):
                orders.open_order(
                    symbol=symbol,
                    type=OrderType.LIMIT,
                    side=PositionSide.BUY,
                    entry_price=entry_price,
                    entry_quantity=entry_quantity,
                    time=date,
                    gtd_time=gtd_time,
                )
                self.LOGGER.debug(
                    textwrap.dedent(
                        f"""
                    {latest.T.to_string(header=False).strip()}
                    """
                    )
                )

                for type in [OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET]:
                    stop_price = self.calculate_stop_price(
                        symbol=symbol,
                        entry_price=entry_price,
                        order_type=type,
                        stop_side=PositionSide.SELL,
                    )
                    orders.open_order(
                        symbol=symbol,
                        type=type,
                        side=PositionSide.SELL,
                        entry_price=stop_price,
                        entry_quantity=entry_quantity,
                        time=date,
                        gtd_time=0,
                    )

        elif rsi > 70:
            typical_price = (high + low + close) / 3.0
            entry_price = self.exchange_info.to_entry_price(symbol, typical_price)

            if entry_quantity := self.exchange_info.to_entry_quantity(
                symbol=symbol,
                entry_price=entry_price,
                size=AppConfig.SIZE,
                leverage=AppConfig.LEVERAGE,
                balance=self.balance,
            ):
                orders.open_order(
                    symbol=symbol,
                    type=OrderType.LIMIT,
                    side=PositionSide.SELL,
                    entry_price=entry_price,
                    entry_quantity=entry_quantity,
                    time=date,
                    gtd_time=gtd_time,
                )
                self.LOGGER.debug(
                    textwrap.dedent(
                        f"""
                    {latest.T.to_string(header=False).strip()}
                    """
                    )
                )

                for type in [OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET]:
                    stop_price = self.calculate_stop_price(
                        symbol=symbol,
                        entry_price=entry_price,
                        order_type=type,
                        stop_side=PositionSide.BUY,
                    )
                    orders.open_order(
                        symbol=symbol,
                        type=type,
                        side=PositionSide.BUY,
                        entry_price=stop_price,
                        entry_quantity=entry_quantity,
                        time=date,
                        gtd_time=0,
                    )
