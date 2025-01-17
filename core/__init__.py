import json
import math

from indicator import rsi
from pandas import DataFrame
import pandas as pd
from binance.um_futures import UMFutures
from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
import asyncio
import time
import logging
from core.constants import *
from utils import fetch
import utils

logger = logging.getLogger(__name__)


class Joshua:
    def __init__(self, is_testnet: bool) -> None:
        api_key, api_secret = (
            (ApiKey.CLIENT_TEST.value, ApiKey.SECRET_TEST.value)
            if is_testnet
            else (ApiKey.CLIENT.value, ApiKey.SECRET.value)
        )
        base_url = BaseUrl.REST_TEST.value if is_testnet else BaseUrl.REST.value
        stream_url = BaseUrl.WS_TEST.value if is_testnet else BaseUrl.WS.value

        utils.check_required_params(base_url=base_url, stream_url=stream_url)
        assert base_url is not None
        assert stream_url is not None

        self.client = UMFutures(key=api_key, secret=api_secret, base_url=base_url)
        self.client_ws = UMFuturesWebsocketClient(
            stream_url=stream_url, on_message=self._market_stream_handler
        )

        self.listen_key = fetch(self.client.new_listen_key).get(LISTEN_KEY)
        self.client_ws_user = UMFuturesWebsocketClient(
            stream_url=stream_url,
            on_message=self._user_stream_handler,
        )

        self.symbols = AppConfig.SYMBOLS.value
        self.interval = AppConfig.INTERVAL.value
        self.leverage = AppConfig.LEVERAGE.value
        self.size = AppConfig.SIZE.value
        self.rsi_window = RSI.WINDOW.value
        self.steram_url = stream_url

        self.avbl_usdt = self._init_avbl_usdt()
        self.open_orders = {s: self._init_open_orders(s) for s in self.symbols}
        self.positions = {s: self._init_positions(s) for s in self.symbols}
        self.RSIs = {s: self._init_rsi(symbol=s) for s in self.symbols}

    def _init_avbl_usdt(self) -> float:
        logger.info("Fetching available USDT balance...")
        res = fetch(self.client.balance)["data"]
        df = pd.DataFrame(res)
        usdt_balance = df[df["asset"] == USDT]

        available = float(usdt_balance["availableBalance"].iloc[0])
        logger.info(f'Available USDT balance: "{available:.2f}"')

        return math.floor(available * 100) / 100

    def _init_open_orders(
        self, symbol: str
    ) -> list[dict[OpenOrder, int | OrderType | PositionSide | float | None]]:
        orders = fetch(self.client.get_orders, symbol=symbol)["data"]

        if not orders:
            return []

        return [
            {
                OpenOrder.ID: order["orderId"],
                OpenOrder.TYPE: OrderType(order["origType"]),
                OpenOrder.SIDE: PositionSide(order["side"]),
                OpenOrder.PRICE: float(order["price"]),
                OpenOrder.QUANTITY: float(order["origQty"]),
            }
            for order in orders
        ]

    def _init_positions(
        self, symbol: str
    ) -> dict[Position, PositionSide | float | None]:
        logger.info(f"Fetching positions for {symbol}...")
        positions = fetch(self.client.get_position_risk, symbol=symbol)["data"]

        if not positions:
            return {
                Position.SIDE: None,
                Position.ENTRY_PRICE: None,
                Position.AMOUNT: None,
            }

        position_amount = float(positions[0]["positionAmt"])
        entry_price = float(positions[0]["entryPrice"])
        is_long_position = position_amount > 0

        return {
            Position.SIDE: (
                PositionSide.BUY if is_long_position else PositionSide.SELL
            ),
            Position.ENTRY_PRICE: entry_price,
            Position.AMOUNT: abs(position_amount),
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
            await asyncio.sleep(KEEPALIVE_INTERVAL)
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
                if data["e"] == EventType.KLINE.value:
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
        current_position = self.positions[symbol]
        entry_price = round(close_price, 1)

        # LONG 포지션 진입 조건
        if current_rsi <= RSI.OVERSOLD_THRESHOLD.value:
            if current_position[
                Position.SIDE
            ] == PositionSide.BUY or OrderType.TRAILING_STOP_MARKET in [
                order[OpenOrder.TYPE] for order in self.open_orders[symbol]
            ]:
                return

            if current_position[Position.SIDE] == PositionSide.SELL:
                self._open_trailing_stop(current_position, symbol)
                return

            if not current_position[Position.SIDE] and (
                quantity := self._calculate_quantity(entry_price, symbol)
            ):
                logger.info(
                    f"Buy signal for {symbol} detected: Current Price: {entry_price}, RSI: {current_rsi}"
                )
                fetch(
                    self.client.new_order,
                    symbol=symbol,
                    side=PositionSide.BUY.value,
                    type=OrderType.LIMIT.value,
                    quantity=quantity,
                    timeInForce=TimeInForce.GTD.value,
                    price=entry_price,
                    goodTillDate=self._gtd(),
                )

        # SHORT 포지션 진입 조건
        elif current_rsi >= RSI.OVERBOUGHT_THRESHOLD.value:
            if current_position[
                Position.SIDE
            ] == PositionSide.SELL or OrderType.TRAILING_STOP_MARKET in [
                order[OpenOrder.TYPE] for order in self.open_orders[symbol]
            ]:
                return

            if current_position[Position.SIDE] == PositionSide.BUY:
                self._open_trailing_stop(current_position, symbol)
                return

            if not current_position[Position.SIDE] and (
                quantity := self._calculate_quantity(entry_price, symbol)
            ):
                logger.info(
                    f"Sell signal for {symbol} detected: Current Price: {entry_price}, RSI: {current_rsi}"
                )
                fetch(
                    self.client.new_order,
                    symbol=symbol,
                    side=PositionSide.SELL.value,
                    type=OrderType.LIMIT.value,
                    quantity=quantity,
                    timeInForce=TimeInForce.GTD.value,
                    price=entry_price,
                    goodTillDate=self._gtd(),
                )

    def _open_trailing_stop(self, position: dict, symbol: str) -> None:
        logger.info(f"Opening a trailing stop order for {symbol}...")

        try:
            if positions := fetch(self.client.get_position_risk, symbol=symbol)["data"]:
                unrealized_profit = float(positions[0]["unRealizedProfit"])
                initial_margin = float(positions[0]["initialMargin"])

                roi = (unrealized_profit / initial_margin) * 100

                stop_side = (
                    PositionSide.SELL
                    if position[Position.SIDE] == PositionSide.BUY
                    else PositionSide.BUY
                )
                estimated_delta = round(
                    (roi * TPSL.TRAILING_STOP.value) / self.leverage,
                    1,
                )
                delta = abs(min(max(estimated_delta, 0.1), 5))

                fetch(
                    self.client.new_order,
                    symbol=symbol,
                    side=stop_side.value,
                    type=OrderType.TRAILING_STOP_MARKET.value,
                    quantity=position[Position.AMOUNT],
                    callbackRate=delta,
                )
        except Exception as e:
            logger.error(f"Opening a trailing stop order error: {e}")

    def _calculate_quantity(self, entry_price: float, symbol: str) -> float:
        initial_margin = self.avbl_usdt * self.size
        quantity = initial_margin * self.leverage / entry_price

        position_amount = math.floor(quantity * 1000) / 1000
        notional = position_amount * entry_price

        return position_amount if notional >= MIN_NOTIONAL[symbol] else 0.0

    def _gtd(self, line=2, to_milli=True) -> int:
        interval_to_seconds = {"m": 60, "h": 3600, "d": 86400}
        unit = self.interval[-1]
        base = interval_to_seconds[unit] * int(self.interval[:-1])
        exp = max(base * line, MIN_EXP)
        gtd = int(time.time() + exp)

        return gtd * TO_MILLI if to_milli else gtd

    def _user_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                logger.debug(
                    f"Received {data['e']} event:\n{json.dumps(data, indent=2)}"
                )

                if data["e"] == EventType.ORDER_TRADE_UPDATE.value:
                    order = data["o"]

                    order_id = order["i"]
                    symbol = order["s"]
                    og_order_type = OrderType(order["ot"])
                    curr_order_type = OrderType(order["o"])
                    status = OrderStatus(order["X"])
                    side = PositionSide(order["S"])
                    price = float(order["p"])
                    quantity = float(order["q"])
                    realized_profit = float(order["rp"])
                    filled = float(order["z"])

                    if symbol in self.symbols:
                        if (
                            status == OrderStatus.NEW
                            and og_order_type == curr_order_type
                        ):
                            self.open_orders[symbol].append(
                                {
                                    OpenOrder.ID: order_id,
                                    OpenOrder.TYPE: og_order_type,
                                    OpenOrder.SIDE: side,
                                    OpenOrder.PRICE: price,
                                    OpenOrder.QUANTITY: quantity,
                                }
                            )

                            if og_order_type == OrderType.LIMIT:
                                self._set_stop_loss(
                                    symbol=symbol,
                                    stop_side=(
                                        PositionSide.SELL
                                        if side == PositionSide.BUY
                                        else PositionSide.BUY
                                    ),
                                    price=price,
                                    quantity=quantity,
                                )
                                self._set_take_profit(
                                    symbol=symbol,
                                    take_side=(
                                        PositionSide.SELL
                                        if side == PositionSide.BUY
                                        else PositionSide.BUY
                                    ),
                                    price=price,
                                    quantity=quantity,
                                )

                        elif status in [
                            OrderStatus.PARTIALLY_FILLED,
                            OrderStatus.FILLED,
                        ]:
                            filled_percentage = (filled / quantity) * 100
                            logger.info(
                                f"Order for {symbol} filled: Type={og_order_type}, Side={side}, Price={price}, Quantity={filled}/{quantity} ({filled_percentage:.2f}%)"
                            )

                            logger.info(
                                f"Realized profit/loss for {symbol}: {realized_profit}"
                            )

                            if status == OrderStatus.FILLED:
                                if order_to_remove := next(
                                    (
                                        order
                                        for order in self.open_orders[symbol]
                                        if order[OpenOrder.ID] == order_id
                                    ),
                                    None,
                                ):
                                    self.open_orders[symbol].remove(order_to_remove)

                                if tpsl_orders := [
                                    order
                                    for order in self.open_orders[symbol]
                                    if order[OpenOrder.TYPE] != og_order_type
                                    and order[OpenOrder.SIDE] == side
                                ]:
                                    logger.info(
                                        f"Closing {len(tpsl_orders)} TP/SL orders..."
                                    )
                                    logger.debug(
                                        f"Open orders:\n{self.open_orders[symbol]}"
                                    )
                                    fetch(
                                        self.client.cancel_batch_order,
                                        symbol=symbol,
                                        orderIdList=[
                                            order[OpenOrder.ID] for order in tpsl_orders
                                        ],
                                        origClientOrderIdList=[],
                                    )

                        elif status == OrderStatus.CANCELED:
                            order_to_remove = next(
                                (
                                    order
                                    for order in self.open_orders[symbol]
                                    if order[OpenOrder.ID] == order_id
                                ),
                                None,
                            )

                            if order_to_remove:
                                self.open_orders[symbol].remove(order_to_remove)

                            if (
                                og_order_type == OrderType.LIMIT
                                and self.positions[symbol][Position.SIDE] is None
                            ):
                                logger.info("Closing all open orders...")
                                logger.debug(
                                    f"Open orders:\n{self.open_orders[symbol]}"
                                )
                                fetch(self.client.cancel_open_orders, symbol=symbol)

                        elif (
                            status == OrderStatus.EXPIRED
                            and self.positions[symbol][Position.SIDE] is None
                        ):
                            logger.info(
                                f"{og_order_type} for {symbol} is triggered but no existing position found."
                            )
                            logger.info("Closing all open orders...")
                            logger.debug(f"Open orders:\n{self.open_orders[symbol]}")
                            fetch(self.client.cancel_open_orders, symbol=symbol)

                if data["e"] == EventType.ACCOUNT_UPDATE.value:
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
                                    Position.AMOUNT: None,
                                    Position.ENTRY_PRICE: None,
                                    Position.SIDE: None,
                                }
                                if is_closed
                                else {
                                    Position.AMOUNT: abs(position_amount),
                                    Position.ENTRY_PRICE: entry_price,
                                    Position.SIDE: (
                                        PositionSide.BUY
                                        if is_long_position
                                        else PositionSide.SELL
                                    ),
                                }
                            )
                        logger.info(f"Updated positions:\n{self.positions}")

                    if "B" in account_update and account_update["B"]:
                        usdt_balance = list(
                            filter(lambda b: b["a"] == USDT, account_update["B"])
                        )
                        if usdt_balance:
                            self.avbl_usdt = (
                                math.floor(float(usdt_balance[0]["cw"]) * 100) / 100
                            )
                            logger.info(f"Updated available USDT: {self.avbl_usdt:.2f}")

                if data["e"] == EventType.LISTEN_KEY_EXPIRED.value:
                    logger.info("Listen key expired. Recreating listen key...")
                    self.listen_key = fetch(self.client.new_listen_key).get(LISTEN_KEY)

                    fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        except Exception as e:
            logger.error(f"An error occurred in the user data handler: {e}")

    def _set_stop_loss(
        self, symbol: str, stop_side: PositionSide, price: float, quantity: float
    ) -> None:
        factor = (
            1 - (TPSL.STOP_LOSS.value / self.leverage)
            if stop_side == PositionSide.SELL
            else 1 + (TPSL.STOP_LOSS.value / self.leverage)
        )
        stop_price = round(price * factor, 1)

        fetch(
            self.client.new_order,
            symbol=symbol,
            side=stop_side.value,
            type=OrderType.STOP.value,
            stopPrice=stop_price,
            price=stop_price,
            quantity=quantity,
        )
        logger.info(
            f"Setting Stop Loss for {symbol}: Price={stop_price}, Quantity={quantity}, Side={stop_side.value}"
        )

    def _set_take_profit(
        self, symbol: str, take_side: PositionSide, price: float, quantity: float
    ) -> None:
        factor = (
            1 + (TPSL.TAKE_PROFIT.value / self.leverage)
            if take_side == PositionSide.SELL
            else 1 - (TPSL.TAKE_PROFIT.value / self.leverage)
        )
        take_price = round(price * factor, 1)

        fetch(
            self.client.new_order,
            symbol=symbol,
            side=take_side.value,
            type=OrderType.TAKE_PROFIT.value,
            stopPrice=take_price,
            price=take_price,
            quantity=quantity,
        )
        logger.info(
            f"Setting Take Profit for {symbol}; Price={take_price}, Quantity={quantity}, Side={take_side.value}"
        )

    def close(self):
        logger.info("Initiating shutdown process...")
        fetch(self.client.close_listen_key, listenKey=self.listen_key)
        fetch(self.client_ws_user.stop)
        fetch(self.client_ws.stop)
        logger.info("Shutdown process completed successfully.")
