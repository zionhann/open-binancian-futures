import json
import math
from typing import Iterable

from indicator import rsi
from pandas import DataFrame
import pandas as pd
from binance.um_futures import UMFutures
from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
import asyncio
import time
import logging
import core.constants as const
from utils import fetch
import utils

logger = logging.getLogger(__name__)


class Joshua:
    def __init__(self, is_testnet: bool) -> None:
        api_key, api_secret = (
            (const.ApiKey.CLIENT_TEST.value, const.ApiKey.SECRET_TEST.value)
            if is_testnet
            else (const.ApiKey.CLIENT.value, const.ApiKey.SECRET.value)
        )
        base_url = (
            const.BaseUrl.REST_TEST.value if is_testnet else const.BaseUrl.REST.value
        )
        stream_url = (
            const.BaseUrl.WS_TEST.value if is_testnet else const.BaseUrl.WS.value
        )

        utils.check_required_params(base_url=base_url, stream_url=stream_url)
        assert base_url is not None
        assert stream_url is not None

        self.client = UMFutures(key=api_key, secret=api_secret, base_url=base_url)
        self.client_ws = UMFuturesWebsocketClient(
            stream_url=stream_url, on_message=self._market_stream_handler
        )

        self.listen_key = fetch(self.client.new_listen_key).get(const.LISTEN_KEY)
        self.client_ws_user = UMFuturesWebsocketClient(
            stream_url=stream_url,
            on_message=self._user_stream_handler,
        )

        self.symbols = const.TradingConfig.SYMBOLS.value
        self.interval = const.TradingConfig.INTERVAL.value
        self.leverage = const.TradingConfig.LEVERAGE.value
        self.size = const.TradingConfig.SIZE.value
        self.rsi_window = const.RSI.WINDOW.value
        self.steram_url = stream_url

        self.avbl_usdt = self._init_avbl_usdt()

        self.open_orders = {s: self._init_open_orders(s) for s in self.symbols}
        logger.debug(f"Loaded open orders:\n{json.dumps(self.open_orders, indent=2)}")

        self.positions = {s: self._init_positions(s) for s in self.symbols}
        logger.debug(f"Loaded positions:\n{json.dumps(self.positions, indent=2)}")

        self.RSIs = {s: self._init_rsi(symbol=s) for s in self.symbols}

    def _init_avbl_usdt(self) -> float:
        logger.info("Fetching available USDT balance...")
        res = fetch(self.client.balance)["data"]
        df = pd.DataFrame(res)
        usdt_balance = df[df["asset"] == const.USDT]

        available = float(usdt_balance["availableBalance"].iloc[0])
        logger.info(f'Available USDT balance: "{available:.2f}"')

        return math.floor(available * 100) / 100

    def _init_open_orders(self, symbol: str) -> list[dict[str, str | int | None]]:
        orders = fetch(self.client.get_orders, symbol=symbol)["data"]

        if not orders:
            return []

        return [
            {
                const.OpenOrder.ID.value: order["orderId"],
                const.OpenOrder.TYPE.value: order["origType"],
                const.OpenOrder.SIDE.value: order["side"],
                const.OpenOrder.PRICE.value: order["price"],
                const.OpenOrder.AMOUNT.value: order["origQty"],
            }
            for order in orders
        ]

    def _init_positions(self, symbol: str) -> dict[str, str | float | None]:
        logger.info(f"Fetching positions for {symbol}...")
        positions = fetch(self.client.get_position_risk, symbol=symbol)["data"]

        if not positions:
            return {
                const.Position.SIDE.value: None,
                const.Position.ENTRY_PRICE.value: None,
                const.Position.AMOUNT.value: None,
            }

        position = positions[0]
        position_amount = float(position["positionAmt"])
        entry_price = float(position["entryPrice"])
        is_long_position = position_amount > 0

        return {
            const.Position.SIDE.value: (
                const.Position.Side.BUY.value
                if is_long_position
                else const.Position.Side.SELL.value
            ),
            const.Position.ENTRY_PRICE.value: entry_price,
            const.Position.AMOUNT.value: abs(position_amount),
        }

    def _init_rsi(self, symbol: str) -> DataFrame:
        klines_columns = [
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
        logger.info(f"Fetching {symbol} klines by {self.interval}...")
        klines_data = fetch(self.client.klines, symbol=symbol, interval=self.interval)[
            "data"
        ]

        df = pd.DataFrame(data=klines_data, columns=klines_columns)
        df["Close"] = df["Close"].astype(float)
        df["RSI"] = rsi(df, window=self.rsi_window)
        initial_rsi = df[["Close", "RSI"]]

        logger.info(
            f"Loaded RSI for {symbol}:\n{initial_rsi.tail().to_string(index=False)}"
        )
        return initial_rsi

    def run(self) -> None:
        logger.info("Starting to run...")
        logger.info("Connecting to User Data Stream...")
        fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        for s in self.symbols:
            logger.info(f"Setting leverage to {self.leverage} for {s}...")
            fetch(self.client.change_leverage, symbol=s, leverage=self.leverage)
            self._subscribe_to_stream(symbol=s)

        asyncio.run(self._keepalive_stream())

    def _subscribe_to_stream(self, symbol: str):
        logger.info(f"Subscribing to {symbol} klines by {self.interval}...")
        fetch(self.client_ws.kline, symbol=symbol, interval=self.interval)

    async def _keepalive_stream(self):
        while True:
            await asyncio.sleep(const.KEEPALIVE_INTERVAL)
            logger.info("Reconnecting to the stream to maintain the connection...")

            fetch(self.client_ws.stop)
            fetch(self.client_ws_user.stop)

            self.client_ws = UMFuturesWebsocketClient(
                stream_url=self.steram_url, on_message=self._market_stream_handler
            )
            self.client_ws_user = UMFuturesWebsocketClient(
                stream_url=self.steram_url, on_message=self._user_stream_handler
            )

            for s in self.symbols:
                self._subscribe_to_stream(symbol=s)
            fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

    def _market_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                if data["e"] == const.EventType.KLINE.value:
                    self._handle_kline(kline=data["k"], symbol=data["s"])
        except Exception as e:
            logger.error(f"An error occurred in the market stream handler: {e}")

    def _handle_kline(self, kline: dict, symbol: str) -> None:
        close_price = float(kline["c"])

        df = pd.concat(
            [self.RSIs[symbol], pd.DataFrame({"Close": [close_price]})],
            ignore_index=True,
        )
        df["RSI"] = rsi(df, window=self.rsi_window)

        if kline["x"]:
            logger.info(f"{self.interval} candle closed for {symbol}.")
            self.RSIs[symbol] = df
            logger.info(
                f"Updated RSI for {symbol}:\n{self.RSIs[symbol].tail().to_string(index=False)}"
            )

        current_rsi = df["RSI"].iloc[-1]

        # LONG 포지션 진입 조건
        if (
            current_rsi <= const.RSI.OVERSOLD_THRESHOLD.value
            and self.positions[symbol][const.Position.SIDE.value]
            != const.Position.Side.BUY.value
        ):
            entry_price = round(close_price, 1)

            position = self.positions[symbol]
            position_side = position[const.Position.SIDE.value]

            logger.info(
                f"Buy signal for {symbol} detected: Current Price: {entry_price}, RSI: {current_rsi}"
            )

            if position_side == const.Position.Side.SELL.value:
                if const.OrderType.TPSL.TRAILING_STOP_MARKET.value not in [
                    order[const.OpenOrder.TYPE.value]
                    for order in self.open_orders[symbol]
                ]:
                    logger.info(f"Found {position_side} position for {symbol}.")
                    self._open_trailing_stop(position, symbol)

                logger.info(f"Found open orders for {symbol}. Skipping...")
                return

            if quantity := self._calculate_quantity(close_price, symbol):
                logger.info(f"Opening a limit order for {symbol}...")

                fetch(
                    self.client.new_order,
                    symbol=symbol,
                    side=const.Position.Side.BUY.value,
                    type=const.OrderType.LIMIT.value,
                    quantity=quantity,
                    timeInForce=const.TimeInForce.GTD.value,
                    price=entry_price,
                    goodTillDate=self._gtd(),
                )

        # SHORT 포지션 진입 조건
        elif (
            current_rsi >= const.RSI.OVERBOUGHT_THRESHOLD.value
            and self.positions[symbol][const.Position.SIDE.value]
            != const.Position.Side.SELL.value
        ):
            entry_price = round(close_price, 1)

            position = self.positions[symbol]
            position_side = position[const.Position.SIDE.value]

            logger.info(
                f"Sell signal for {symbol} detected: Current Price: {entry_price}, RSI: {current_rsi}"
            )

            if position_side == const.Position.Side.BUY.value:
                if const.OrderType.TPSL.TRAILING_STOP_MARKET.value not in [
                    order[const.OpenOrder.TYPE.value]
                    for order in self.open_orders[symbol]
                ]:
                    logger.info(f"Found {position_side} position for {symbol}.")
                    self._open_trailing_stop(position, symbol)

                logger.info(f"Found open orders for {symbol}. Skipping...")
                return

            if quantity := self._calculate_quantity(close_price, symbol):
                logger.info(f"Opening a limit order for {symbol}...")

                fetch(
                    self.client.new_order,
                    symbol=symbol,
                    side=const.Position.Side.SELL.value,
                    type=const.OrderType.LIMIT.value,
                    quantity=quantity,
                    timeInForce=const.TimeInForce.GTD.value,
                    price=entry_price,
                    goodTillDate=self._gtd(),
                )

    def _open_trailing_stop(self, position: dict, symbol: str) -> None:
        logger.info(f"Opening a trailing stop order for {symbol}...")
        unrealized_profit_percentage = 0

        try:
            if positions := fetch(self.client.get_position_risk, symbol=symbol)["data"]:
                unrealized_profit = float(positions[0]["unRealizedProfit"])
                initial_margin = float(positions[0]["initialMargin"])

                unrealized_profit_percentage = (
                    unrealized_profit / initial_margin
                ) * 100

            stop_side = (
                const.Position.Side.SELL.value
                if position[const.Position.SIDE.value] == const.Position.Side.BUY.value
                else const.Position.Side.BUY.value
            )
            estimated_delta = round(
                (unrealized_profit_percentage * const.TPSL.TRAILING_STOP.value)
                / self.leverage,
                1,
            )
            delta = min(max(estimated_delta, 0.1), 5)
            logger.info(f"Delta: {delta}")

            fetch(
                self.client.new_order,
                symbol=symbol,
                side=stop_side,
                type=const.OrderType.TPSL.TRAILING_STOP_MARKET.value,
                quantity=position[const.Position.AMOUNT.value],
                callbackRate=delta,
            )
        except Exception as e:
            logger.error(f"Opening a trailing stop order error: {e}")

    def _calculate_quantity(self, entry_price: float, symbol: str):
        logger.info(f"Available USDT: {self.avbl_usdt}, Size: {self.size}")

        while True:
            if round(self.size, 2) > 1:
                logger.info(
                    f"Size cannot be greater than 1. Skipping..."
                )
                self.size = const.TradingConfig.SIZE.value
                return 0.0

            initial_margin = self.avbl_usdt * self.size
            quantity = initial_margin * self.leverage / entry_price
            position_amount = math.floor(quantity * 1000) / 1000

            if not position_amount:
                self.size += 0.01
                logger.info(
                    f"Not enough margin to open an order. Increasing the size to {int(self.size * 100)}%."
                )
                continue
            elif position_amount * entry_price < const.NOTIONAL[symbol]:
                self.size += 0.01
                logger.info(
                    f"Not enough notional to open an order. Increasing the size to {int(self.size * 100)}%."
                )
                continue

            self.size = const.TradingConfig.SIZE.value
            return position_amount

    def _gtd(self, line=2, to_milli=True) -> int:
        interval_to_seconds = {"m": 60, "h": 3600, "d": 86400}
        unit = self.interval[-1]
        base = interval_to_seconds[unit] * int(self.interval[:-1])
        exp = max(base * line, const.MIN_EXP)
        gtd = int(time.time() + exp)

        return gtd * const.TO_MILLI if to_milli else gtd

    def _user_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                logger.debug(
                    f"Received {data['e']} event:\n{json.dumps(data, indent=2)}"
                )

                if data["e"] == const.EventType.ORDER_TRADE_UPDATE.value:
                    order = data["o"]

                    order_id = order["i"]
                    og_order_type = order["ot"]
                    curr_order_type = order["o"]
                    side = order["S"]
                    price = order["p"]
                    quantity = order["q"]
                    status = order["X"]
                    symbol = order["s"]
                    realized_profit = order["rp"]
                    tif = order["f"]
                    filled = order["z"]

                    if symbol in self.symbols:
                        if (
                            status == const.OrderStatus.NEW.value
                            and og_order_type == curr_order_type
                        ):
                            self.open_orders[symbol].append(
                                {
                                    const.OpenOrder.ID.value: order_id,
                                    const.OpenOrder.TYPE.value: og_order_type,
                                    const.OpenOrder.SIDE.value: side,
                                    const.OpenOrder.PRICE.value: price,
                                    const.OpenOrder.AMOUNT.value: quantity,
                                }
                            )
                            logger.debug(
                                f"Updated open orders:\n{json.dumps(self.open_orders[symbol], indent=2)}"
                            )

                            if og_order_type == const.OrderType.LIMIT.value:
                                self._set_stop_loss(
                                    symbol=symbol,
                                    stop_side=(
                                        const.Position.Side.SELL.value
                                        if side == const.Position.Side.BUY.value
                                        else const.Position.Side.BUY.value
                                    ),
                                    price=float(price),
                                    quantity=float(quantity),
                                )
                                self._set_take_profit(
                                    symbol=symbol,
                                    take_side=(
                                        const.Position.Side.SELL.value
                                        if side == const.Position.Side.BUY.value
                                        else const.Position.Side.BUY.value
                                    ),
                                    price=float(price),
                                    quantity=float(quantity),
                                )

                        elif status in [
                            const.OrderStatus.PARTIALLY_FILLED.value,
                            const.OrderStatus.FILLED.value,
                        ]:
                            filled_percentage = float(filled) / float(quantity) * 100
                            logger.info(
                                f"Order for {symbol} filled: Type={og_order_type}, Side={side}, Price={price}, Quantity={filled}/{quantity} ({filled_percentage:.2f}%)"
                            )

                            logger.info(
                                f"Realized profit/loss for {symbol}: {realized_profit}"
                            )

                            if status == const.OrderStatus.FILLED.value:
                                order_to_remove = next(
                                    (
                                        order
                                        for order in self.open_orders[symbol]
                                        if order[const.OpenOrder.ID.value] == order_id
                                    ),
                                    None,
                                )

                                if order_to_remove:
                                    self.open_orders[symbol].remove(order_to_remove)
                                    logger.debug(
                                        f"Updated open orders:\n{json.dumps(self.open_orders[symbol], indent=2)}"
                                    )

                                if og_order_type in [
                                    typ.value for typ in const.OrderType.TPSL
                                ]:
                                    tpsl_orders = [
                                        order
                                        for order in self.open_orders[symbol]
                                        if order[const.OpenOrder.TYPE.value]
                                        != og_order_type
                                    ]
                                    logger.info(
                                        f"Closing {len(tpsl_orders)} TP/SL orders..."
                                    )
                                    logger.debug(
                                        f"Open orders:\n{json.dumps(self.open_orders[symbol], indent=2)}"
                                    )
                                    fetch(
                                        self.client.cancel_batch_order,
                                        symbol=symbol,
                                        orderIdList=[
                                            order[const.OpenOrder.ID.value]
                                            for order in tpsl_orders
                                        ],
                                        origClientOrderIdList=[],
                                    )

                        elif status == const.OrderStatus.CANCELLED.value:
                            order_to_remove = next(
                                (
                                    order
                                    for order in self.open_orders[symbol]
                                    if order[const.OpenOrder.ID.value] == order_id
                                ),
                                None,
                            )

                            if order_to_remove:
                                self.open_orders[symbol].remove(order_to_remove)
                                logger.debug(
                                    f"Updated open orders:\n{json.dumps(self.open_orders[symbol], indent=2)}"
                                )

                            if (
                                tif == const.TimeInForce.GTD.value
                                and self.positions[symbol][const.Position.SIDE.value]
                                is None
                            ):
                                logger.info(
                                    f"{og_order_type} order for {symbol} is cancelled since TIF is expired."
                                )
                                logger.info("Closing all open orders...")
                                logger.debug(
                                    f"Open orders:\n{json.dumps(self.open_orders[symbol], indent=2)}"
                                )
                                fetch(self.client.cancel_open_orders, symbol=symbol)

                        elif (
                            status == const.OrderStatus.EXPIRED.value
                            and self.positions[symbol][const.Position.SIDE.value]
                            is None
                        ):
                            logger.info(
                                f"{og_order_type} for {symbol} is triggered but no existing position found."
                            )
                            logger.info("Closing all open orders...")
                            logger.debug(
                                f"Open orders:\n{json.dumps(self.open_orders[symbol], indent=2)}"
                            )
                            fetch(self.client.cancel_open_orders, symbol=symbol)

                if data["e"] == const.EventType.ACCOUNT_UPDATE.value:
                    account_update = data["a"]

                    if "P" in account_update and account_update["P"]:
                        for position in account_update["P"]:
                            symbol = position["s"]
                            position_amount = float(position["pa"])
                            entry_price = float(position["ep"])

                            is_closed = position_amount == 0
                            is_long_position = position_amount > 0

                            self.positions[symbol] = (
                                {
                                    const.Position.AMOUNT.value: None,
                                    const.Position.ENTRY_PRICE.value: None,
                                    const.Position.SIDE.value: None,
                                }
                                if is_closed
                                else {
                                    const.Position.AMOUNT.value: abs(position_amount),
                                    const.Position.ENTRY_PRICE.value: entry_price,
                                    const.Position.SIDE.value: (
                                        const.Position.Side.BUY.value
                                        if is_long_position
                                        else const.Position.Side.SELL.value
                                    ),
                                }
                            )
                        logger.info(
                            f"Updated positions:\n{json.dumps(self.positions, indent=2)}"
                        )

                    if "B" in account_update and account_update["B"]:
                        usdt_balance = list(
                            filter(lambda b: b["a"] == const.USDT, account_update["B"])
                        )
                        if usdt_balance:
                            self.avbl_usdt = (
                                math.floor(float(usdt_balance[0]["cw"]) * 100) / 100
                            )
                            logger.info(f"Updated available USDT: {self.avbl_usdt:.2f}")

                if data["e"] == const.EventType.LISTEN_KEY_EXPIRED.value:
                    logger.info("Listen key expired. Recreating listen key...")
                    self.listen_key = fetch(self.client.new_listen_key).get(
                        const.LISTEN_KEY
                    )

                    fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        except Exception as e:
            logger.error(f"An error occurred in the user data handler: {e}")

    def _set_stop_loss(
        self, symbol: str, stop_side: str, price: float, quantity: float
    ) -> None:
        factor = (
            1 - (const.TPSL.STOP_LOSS.value / self.leverage)
            if stop_side == const.Position.Side.SELL.value
            else 1 + (const.TPSL.STOP_LOSS.value / self.leverage)
        )
        stop_price = round(price * factor, 1)

        fetch(
            self.client.new_order,
            symbol=symbol,
            side=stop_side,
            type=const.OrderType.TPSL.STOP.value,
            stopPrice=stop_price,
            price=stop_price,
            quantity=quantity,
        )
        logger.info(
            f"Setting Stop Loss for {symbol}: Price={stop_price}, Quantity={quantity}, Side={stop_side}"
        )

    def _set_take_profit(
        self, symbol: str, take_side: str, price: float, quantity: float
    ) -> None:
        factor = (
            1 + (const.TPSL.TAKE_PROFIT.value / self.leverage)
            if take_side == const.Position.Side.SELL.value
            else 1 - (const.TPSL.TAKE_PROFIT.value / self.leverage)
        )
        take_price = round(price * factor, 1)

        fetch(
            self.client.new_order,
            symbol=symbol,
            side=take_side,
            type=const.OrderType.TPSL.TAKE_PROFIT.value,
            stopPrice=take_price,
            price=take_price,
            quantity=quantity,
        )
        logger.info(
            f"Setting Take Profit for {symbol}; Price={take_price}, Quantity={quantity}, Side={take_side}"
        )

    def close(self):
        logger.info("Initiating shutdown process...")
        fetch(self.client.close_listen_key, listenKey=self.listen_key)
        fetch(self.client_ws_user.stop)
        fetch(self.client_ws.stop)
        logger.info("Shutdown process completed successfully.")
