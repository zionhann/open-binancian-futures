import json
import asyncio
import logging
import pandas as pd

from binance.um_futures import UMFutures
from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient
from app import App
from app.core.constant import *
from app.core.balance import Balance
from app.core.exchange_info import ExchangeInfo
from app.core.strategy import Strategy
from app.utils import fetch
from app.core.position import Position, Positions
from app.core.order import Order, Orders

logger = logging.getLogger(__name__)


class Joshua(App):
    _BASEURL_REST = "https://fapi.binance.com"
    _BASEURL_WS = "wss://fstream.binance.com"

    _BASEURL_REST_TEST = "https://testnet.binancefuture.com"
    _BASEURL_WS_TEST = "wss://stream.binancefuture.com"

    _LISTEN_KEY = "listenKey"
    _USDT = "USDT"
    _KEEPALIVE_INTERVAL = 3600 * 23 + 60 * 55

    def __init__(self) -> None:
        base_url, stream_url = (
            (self._BASEURL_REST_TEST, self._BASEURL_WS_TEST)
            if AppConfig.IS_TESTNET.value
            else (self._BASEURL_REST, self._BASEURL_WS)
        )
        api_key, api_secret = (
            (Required.API_KEY_TEST.value, Required.API_SECRET_TEST.value)
            if AppConfig.IS_TESTNET.value
            else (Required.API_KEY.value, Required.API_SECRET.value)
        )
        self.strategy = Strategy.of(Required.STRATEGY.value)
        self.client = UMFutures(key=api_key, secret=api_secret, base_url=base_url)

        self.stream_url = stream_url
        self.listen_key = fetch(self.client.new_listen_key).get(self._LISTEN_KEY)

        self.client_ws = UMFuturesWebsocketClient(
            stream_url=self.stream_url, on_message=self._market_stream_handler
        )
        self.client_ws_user = UMFuturesWebsocketClient(
            stream_url=self.stream_url,
            on_message=self._user_stream_handler,
        )

        exchange_info = fetch(self.client.exchange_info)["symbols"]
        self.exchange_info = {
            s: ExchangeInfo(s, exchange_info) for s in AppConfig.SYMBOLS.value
        }

        self.balance = self._init_balance()
        self.orders = {s: self._init_orders(s) for s in AppConfig.SYMBOLS.value}
        self.positions = {s: self._init_positions(s) for s in AppConfig.SYMBOLS.value}
        self.indicators = {
            s: self.strategy.init_indicators(s, self.client)
            for s in AppConfig.SYMBOLS.value
        }

    def _init_balance(self) -> Balance:
        logger.info("Fetching available USDT balance...")
        data = fetch(self.client.balance)["data"]
        df = pd.DataFrame(data)
        usdt_balance = df[df["asset"] == self._USDT]

        available = float(usdt_balance["availableBalance"].iloc[0])
        logger.info(f'Available USDT balance: "{available:.2f}"')

        return Balance(available)

    def _init_orders(self, symbol: str) -> Orders:
        data = fetch(self.client.get_orders, symbol=symbol)["data"]
        orders = [
            Order(
                symbol=symbol,
                id=order["orderId"],
                type=OrderType(order["origType"]),
                side=PositionSide(order["side"]),
                price=float(order["price"]),
                quantity=float(order["origQty"]),
            )
            for order in data
        ]
        return Orders(orders)

    def _init_positions(self, symbol: str) -> Positions:
        logger.info(f"Fetching positions for {symbol}...")
        data = fetch(self.client.get_position_risk, symbol=symbol)["data"]

        positions = [
            Position(
                symbol=symbol,
                entry_price=float(position["entryPrice"]),
                amount=float(position["positionAmt"]),
            )
            for position in data
        ]
        return Positions(positions)

    def run(self) -> None:
        logger.info("Starting to run...")
        logger.info("Connecting to User Data Stream...")
        fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        for s in AppConfig.SYMBOLS.value:
            self._set_leverage(symbol=s)
            self._subscribe_to_stream(symbol=s)

        asyncio.run(self._keepalive_stream())

    def _set_leverage(self, symbol: str):
        logger.info(f"Setting leverage to {AppConfig.LEVERAGE.value} for {symbol}...")
        fetch(
            self.client.change_leverage,
            symbol=symbol,
            leverage=AppConfig.LEVERAGE.value,
        )

    def _subscribe_to_stream(self, symbol: str):
        logger.info(f"Subscribing to {symbol} klines by {AppConfig.INTERVAL.value}...")
        fetch(self.client_ws.kline, symbol=symbol, interval=AppConfig.INTERVAL.value)

    async def _keepalive_stream(self):
        while True:
            await asyncio.sleep(self._KEEPALIVE_INTERVAL)
            logger.info("Reconnecting to the stream to maintain the connection...")

            fetch(self.client_ws.stop)
            fetch(self.client_ws_user.stop)

            self.client_ws = UMFuturesWebsocketClient(
                stream_url=self.stream_url, on_message=self._market_stream_handler
            )
            self.client_ws_user = UMFuturesWebsocketClient(
                stream_url=self.stream_url, on_message=self._user_stream_handler
            )

            for s in AppConfig.SYMBOLS.value:
                self._subscribe_to_stream(symbol=s)

            fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

    def _market_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                if data["e"] == EventType.KLINE.value:
                    self._handle_kline(data["k"])
        except Exception as e:
            logger.error(f"An error occurred in the market stream handler: {e}")

    def _handle_kline(self, data: dict) -> None:
        if not data["x"]:
            return

        open_time = (
            pd.to_datetime(data["t"], unit="ms")
            .tz_localize("UTC")
            .tz_convert("Asia/Seoul")
            .strftime("%Y-%m-%d %H:%M")
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
        self.indicators[symbol] = self.strategy.load(
            pd.concat(
                [self.indicators[symbol], pd.DataFrame([new_data])], ignore_index=True
            )
        )
        logger.info(
            f"Updated indicators for {symbol}:\n{self.indicators[symbol].tail(Indicator.RSI_RECENT_WINDOW.value).to_string(index=False)}"
        )
        self.strategy.run(
            indicators=self.indicators[symbol],
            positions=self.positions[symbol],
            orders=self.orders[symbol],
            exchange_info=self.exchange_info[symbol],
            balance=self.balance,
            client=self.client,
        )

    def _user_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                logger.debug(
                    f"Received {data['e']} event:\n{json.dumps(data, indent=2)}"
                )

                if data["e"] == EventType.ORDER_TRADE_UPDATE.value:
                    self._handle_order_trade_update(data["o"])

                if data["e"] == EventType.ACCOUNT_UPDATE.value:
                    self._handle_account_update(data["a"])

                if data["e"] == EventType.LISTEN_KEY_EXPIRED.value:
                    logger.info("Listen key expired. Recreating listen key...")
                    self.listen_key = fetch(self.client.new_listen_key).get(
                        self._LISTEN_KEY
                    )
                    fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        except Exception as e:
            logger.error(f"An error occurred in the user data handler: {e}")

    def _handle_order_trade_update(self, data: dict):
        order_id = data["i"]
        symbol = data["s"]
        og_order_type = OrderType(data["ot"])
        curr_order_type = OrderType(data["o"])
        status = OrderStatus(data["X"])
        side = PositionSide(data["S"])
        price = float(data["p"])
        quantity = float(data["q"])
        realized_profit = float(data["rp"])
        filled = float(data["z"])

        if symbol in AppConfig.SYMBOLS.value:
            if status == OrderStatus.NEW and og_order_type == curr_order_type:
                self.orders[symbol].add(
                    Order(
                        symbol=symbol,
                        id=order_id,
                        type=og_order_type,
                        side=side,
                        price=price,
                        quantity=quantity,
                    )
                )

                if og_order_type == OrderType.LIMIT:
                    stop_side = (
                        PositionSide.SELL
                        if side == PositionSide.BUY
                        else PositionSide.BUY
                    )
                    self.strategy.set_tpsl(
                        symbol=symbol,
                        position_side=stop_side,
                        price=price,
                        client=self.client,
                    )

            elif status in [
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
            ]:
                filled_percentage = (filled / quantity) * 100
                logger.info(
                    f"Order for {symbol} filled: Type={og_order_type}, Side={side}, Price={price}, Quantity={filled}/{quantity} ({filled_percentage:.2f}%)"
                )
                logger.info(f"Realized profit/loss for {symbol}: {realized_profit}")

                if status == OrderStatus.FILLED:
                    self.orders[symbol].remove_by_id(order_id)

            elif status == OrderStatus.CANCELED:
                self.orders[symbol].remove_by_id(order_id)

                if (
                    og_order_type == OrderType.LIMIT
                    and self.positions[symbol].is_empty()
                ):
                    logger.info("Cancelling all open orders...")
                    fetch(self.client.cancel_open_orders, symbol=symbol)

            elif status == OrderStatus.EXPIRED:
                self.orders[symbol].remove_by_id(order_id)

    def _handle_account_update(self, data: dict):
        if "P" in data and data["P"]:
            for position in data["P"]:
                symbol = position["s"]
                position_amount = float(position["pa"])
                entry_price = float(position["ep"])

                self.positions[symbol] = (
                    Positions(
                        [
                            Position(
                                symbol=symbol,
                                entry_price=entry_price,
                                amount=position_amount,
                            )
                        ]
                    )
                    if position_amount
                    else Positions()
                )

        if "B" in data and data["B"]:
            if usdt_balance := list(filter(lambda b: b["a"] == self._USDT, data["B"])):
                self.balance = Balance(float(usdt_balance[0]["cw"]))
                logger.info(f"Updated available USDT: {self.balance}")

    def close(self) -> None:
        logger.info("Initiating shutdown process...")
        fetch(self.client.close_listen_key, listenKey=self.listen_key)
        fetch(self.client_ws_user.stop)
        fetch(self.client_ws.stop)
        logger.info("Shutdown process completed successfully.")
