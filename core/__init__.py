from dataclasses import dataclass
import json
import math

from matplotlib.pylab import f
from indicator import rsi
from pandas import DataFrame
import pandas as pd
from binance.um_futures import UMFutures
from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
import asyncio
import time
import logging
import const
import utils

logger = logging.getLogger(__name__)


class Joshua:
    def __init__(
        self,
        symbols: list[str],
        interval: str,
        leverage: int,
        size: int,
        rsi_window: int,
        is_testnet: bool,
    ) -> None:
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
            stream_url=stream_url, on_message=self._message_handler
        )

        self.listen_key = self.client.new_listen_key()[const.LISTEN_KEY]
        self.client_ws_user = UMFuturesWebsocketClient(
            stream_url=stream_url,
            on_message=self._user_data_handler,
        )

        self.symbols = symbols
        self.interval = interval
        self.leverage = leverage
        self.size = size
        self.rsi_window = rsi_window
        self.steram_url = stream_url

        self.avbl_usdt = self._init_avbl_usdt()

        self.positions = {s: self._init_positions(s) for s in symbols}
        logger.info(f"Loaded positions:\n{json.dumps(self.positions, indent=2)}")

        self.RSIs = {s: self._init_rsi(symbol=s) for s in symbols}

    def _init_avbl_usdt(self) -> float:
        logger.info("Fetching available USDT balance...")
        res = self.client.balance()
        df = pd.DataFrame(res)
        usdt_balance = df[df["asset"] == const.USDT]

        available = float(usdt_balance["availableBalance"].iloc[0])
        logger.info(f'Available USDT balance: "{available:.2f}"')

        return math.floor(available * 100) / 100

    def _init_open_orders(self, symbol: str) -> int:
        orders = self.client.get_orders(symbol=symbol)

        if not orders:
            return 0

        limit_orders = list(
            filter(lambda o: o["type"] == const.OrderType.LIMIT.value, orders)
        )
        return int(limit_orders[0]["orderId"]) if limit_orders else 0

    def _init_positions(self, symbol: str) -> dict[str, str | None]:
        logger.info(f"Fetching positions for {symbol}...")
        positions = self.client.get_position_risk(symbol=symbol)

        if not positions:
            return {
                "ps": None,
                "ep": None,
                "pa": None,
            }

        position = positions[0]
        return {
            "ps": (
                const.OrderSide.BUY.value
                if float(position["positionAmt"]) > 0
                else const.OrderSide.SELL.value
            ),
            "ep": position["entryPrice"],
            "pa": position["positionAmt"],
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
        klines_data = self.client.klines(symbol, self.interval)

        df = pd.DataFrame(data=klines_data, columns=klines_columns)
        df["Close"] = df["Close"].astype(float)
        df["RSI"] = rsi(df, window=self.rsi_window)
        initial_rsi = df[["Close", "RSI"]]

        logger.info(
            f"Loaded RSI for {symbol}:\n{initial_rsi.tail().to_string(index=False)}"
        )
        return initial_rsi

    def _message_handler(self, _, message) -> None:
        data = json.loads(message)

        if "e" in data:
            if data["e"] == const.EventType.KLINE.value:
                self._handle_kline(kline=data["k"], symbol=data["s"])

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
            current_rsi <= 30
            and self.positions[symbol]["ps"] != const.OrderSide.BUY.value
        ):
            entry_price = round(close_price, 1)
            target_position = self.positions[symbol]

            logger.info(
                f"Buy signal for {symbol} detected: Current Price: {close_price}, RSI: {current_rsi}"
            )

            if target_position["ps"] == const.OrderSide.SELL.value:
                logger.info(f"Found {target_position['ps']} position for {symbol}.")
                self._close_position(target_position, symbol, entry_price)
            elif self.client.get_orders(symbol=symbol):
                logger.info(f"Found open orders for {symbol}. Skipping...")
                return

            quantity = self._calculate_quantity(close_price)

            self.client.new_order(
                symbol=symbol,
                side=const.OrderSide.BUY.value,
                type=const.OrderType.LIMIT.value,
                quantity=quantity,
                timeInForce=const.TimeInForce.GTD.value,
                price=entry_price,
                goodTillDate=self._gtd(),
            )

        # SHORT 포지션 진입 조건
        elif (
            current_rsi >= 70
            and self.positions[symbol]["ps"] != const.OrderSide.SELL.value
        ):
            entry_price = round(close_price, 1)
            target_position = self.positions[symbol]

            logger.info(
                f"Sell signal for {symbol} detected: Current Price: {close_price}, RSI: {current_rsi}"
            )

            if target_position["ps"] == const.OrderSide.BUY.value:
                logger.info(f"Found {target_position['ps']} position for {symbol}.")
                self._close_position(target_position, symbol, entry_price)
            elif open_orders := self.client.get_orders(symbol=symbol):
                limit_orders = [
                    order
                    for order in open_orders
                    if order["type"] == const.OrderType.LIMIT.value
                ]
                logger.info(
                    f"Found open orders for {symbol}:\n{json.dumps(limit_orders, indent=2)}"
                )
                logger.info(f"Skipping...")
                return

            quantity = self._calculate_quantity(close_price)

            self.client.new_order(
                symbol=symbol,
                side=const.OrderSide.SELL.value,
                type=const.OrderType.LIMIT.value,
                quantity=quantity,
                timeInForce=const.TimeInForce.GTD.value,
                price=entry_price,
                goodTillDate=self._gtd(),
            )

    def _calculate_quantity(self, entry_price: float):
        try:
            quantity = self.avbl_usdt * (self.size / 100) / entry_price

            return math.floor(quantity * 1000) / 1000
        except Exception as e:
            print("Unexpected error in calculate_quantity: {}".format(e))

    def _gtd(self, line=4) -> int:
        interval_to_seconds = {"m": 60, "h": 3600, "d": 86400}
        unit = self.interval[-1]
        base = interval_to_seconds[unit] * int(self.interval[:-1])
        exp = max(base * line, 660)

        return int(time.time() + exp) * 1000

    def _user_data_handler(self, _, message) -> None:
        try:
            data = json.loads(message)

            if "e" in data:
                if data["e"] == const.EventType.ORDER_TRADE_UPDATE.value:
                    order = data["o"]
                    symbol = order["s"]
                    order_type = order["ot"]
                    status = order["X"]
                    side = order["S"]
                    price = order["p"]
                    quantity = order["q"]
                    realized_profit = order["rp"]
                    is_maker = order["m"]

                    if symbol in self.symbols:
                        if order_type == const.OrderType.LIMIT.value:
                            if status == const.OrderStatus.NEW.value:
                                self._set_stop_loss(
                                    symbol=symbol,
                                    stop_side=(
                                        const.OrderSide.SELL.value
                                        if side == const.OrderSide.BUY.value
                                        else const.OrderSide.BUY.value
                                    ),
                                    price=float(price),
                                    quantity=float(quantity),
                                )
                                self._set_take_profit(
                                    symbol=symbol,
                                    take_side=(
                                        const.OrderSide.SELL.value
                                        if side == const.OrderSide.BUY.value
                                        else const.OrderSide.BUY.value
                                    ),
                                    price=float(price),
                                    quantity=float(quantity),
                                )
                            elif status in [
                                const.OrderStatus.PARTIALLY_FILLED.value,
                                const.OrderStatus.FILLED.value,
                            ]:
                                filled_quantity = order["z"]
                                percentage_filled = (
                                    float(filled_quantity) / float(quantity)
                                ) * 100
                                logger.info(
                                    f"Order for {symbol} filled: Type={order_type}, Side={side}, Price={price}, Quantity={filled_quantity}/{quantity} ({percentage_filled:.2f}%)"
                                )
                        elif (
                            order_type
                            in [
                                const.OrderType.MARKET.value,
                                *[typ.value for typ in const.OrderType.TPSL],
                            ]
                            and status
                            in [
                                const.OrderStatus.FILLED.value,
                                const.OrderStatus.PARTIALLY_FILLED.value,
                            ]
                            and not is_maker  # Taker
                        ):
                            logger.info(
                                f"Realized profit/loss for {symbol}: {realized_profit}"
                            )

                            if status == const.OrderStatus.FILLED.value:
                                open_orders = self.client.get_orders(symbol=symbol)
                                tpsl_orders = [
                                    order
                                    for order in open_orders
                                    if order["type"]
                                    in [typ.value for typ in const.OrderType.TPSL]
                                ]

                                if tpsl_orders:
                                    logger.info(
                                        f"Cancelling orders for {symbol}:\n{json.dumps(tpsl_orders, indent=2)}"
                                    )
                                    self.client.cancel_batch_order(
                                        symbol=symbol,
                                        orderIdList=[
                                            order["orderId"] for order in tpsl_orders
                                        ],
                                        origClientOrderIdList=[],
                                    )
                        elif status in [
                            const.OrderStatus.CANCELLED.value,
                            const.OrderStatus.EXPIRED.value,
                        ]:
                            logger.info(
                                f"Order for {symbol} cancelled or expired: Type={order_type}, Side={side}, Status={status}"
                            )

                if data["e"] == const.EventType.ACCOUNT_UPDATE.value:
                    account_update = data["a"]

                    if "P" in account_update and account_update["P"]:
                        for position in account_update["P"]:
                            symbol = position["s"]
                            is_closed = position["pa"] == "0"

                            self.positions[symbol] = (
                                {
                                    "pa": None,
                                    "ep": None,
                                    "ps": None,
                                }
                                if is_closed
                                else {
                                    "pa": position["pa"],
                                    "ep": position["ep"],
                                    "ps": (
                                        const.OrderSide.BUY.value
                                        if float(position["pa"]) > 0
                                        else const.OrderSide.SELL.value
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
                    self.listen_key = self.client.new_listen_key()[const.LISTEN_KEY]
                    self.client_ws_user.user_data(listen_key=self.listen_key)

        except Exception as e:
            logger.error(f"An error occurred in the user data handler: {e}")

    def _set_stop_loss(
        self, symbol: str, stop_side: str, price: float, quantity: float
    ) -> None:
        logger.info(
            f"Setting Stop Loss for {symbol}: Price={price}, Quantity={quantity}, Side={stop_side}"
        )
        weight = 0.12
        factor = (
            1 - (weight / self.leverage)
            if stop_side == const.OrderSide.SELL.value
            else 1 + (weight / self.leverage)
        )
        stop_price = round(price * factor, 1)

        self.client.new_order(
            symbol=symbol,
            side=stop_side,
            type=const.OrderType.TPSL.STOP.value,
            stopPrice=stop_price,
            price=stop_price,
            quantity=quantity,
        )

    def _set_take_profit(
        self, symbol: str, take_side: str, price: float, quantity: float
    ) -> None:
        logger.info(
            f"Setting Take Profit for {symbol}; Price={price}, Quantity={quantity}, Side={take_side}"
        )
        weight = 0.18
        factor = (
            1 + (weight / self.leverage)
            if take_side == const.OrderSide.SELL.value
            else 1 - (weight / self.leverage)
        )
        take_price = round(price * factor, 1)

        self.client.new_order(
            symbol=symbol,
            side=take_side,
            type=const.OrderType.TPSL.TAKE_PROFIT.value,
            stopPrice=take_price,
            price=take_price,
            quantity=quantity,
        )

    def run(self) -> None:
        logger.info("Starting to run...")
        logger.info("Connecting to User Data Stream...")
        self.client_ws_user.user_data(listen_key=self.listen_key)

        for s in self.symbols:
            logger.info(f"Setting leverage to {self.leverage} for {s}...")
            self.client.change_leverage(symbol=s, leverage=self.leverage)
            self._subscribe_to_stream(symbol=s)

        asyncio.run(self._keepalive_stream())

    def _subscribe_to_stream(self, symbol: str):
        logger.info(f"Subscribing to {symbol} klines by {self.interval}...")
        self.client_ws.kline(symbol=symbol, interval=self.interval)

    async def _keepalive_stream(self):
        while True:
            await asyncio.sleep(const.KEEPALIVE_INTERVAL)
            logger.info("Reconnecting to the stream to maintain the connection...")

            self.client_ws.stop()
            self.client_ws_user.stop()

            self.client_ws = UMFuturesWebsocketClient(
                stream_url=self.steram_url, on_message=self._message_handler
            )
            self.client_ws_user = UMFuturesWebsocketClient(
                stream_url=self.steram_url, on_message=self._user_data_handler
            )

            for s in self.symbols:
                self._subscribe_to_stream(symbol=s)
            self.client_ws_user.user_data(listen_key=self.listen_key)

    def close(self):
        logger.info("Initiating shutdown process...")
        self.client.close_listen_key(self.listen_key)
        self.client_ws_user.stop()
        self.client_ws.stop()
        logger.info("Shutdown process completed successfully.")

    def _close_position(self, position: dict, symbol: str, price: float) -> None:
        logger.info(f"Closing {position['ps']} position for {symbol}...")

        try:
            stop_side = (
                const.OrderSide.SELL.value
                if position["ps"] == const.OrderSide.BUY.value
                else const.OrderSide.BUY.value
            )
            price_weight = 0.999 if stop_side == const.OrderSide.BUY.value else 1.001
            stop_price = round(price * price_weight, 1)

            self.client.new_order(
                symbol=symbol,
                side=stop_side,
                type=const.OrderType.TPSL.STOP.value,
                quantity=abs(float(position["pa"])),
                priceMatch="OPPONENT",
                stopPrice=stop_price,
                timeInForce=const.TimeInForce.GTD.value,
                goodTillDate=self._gtd(),
            )
        except Exception as e:
            logger.error(f"Position closing error: {e}")
