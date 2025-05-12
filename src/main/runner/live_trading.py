import json
import logging
import time
from typing import Any, Final

from binance.websocket.um_futures.websocket_client import UMFuturesWebsocketClient

from model.constant import *
from openapi import binance_futures as futures
from runner import Runner
from strategy import Strategy
from utils import fetch

LOGGER = logging.getLogger(__name__)
LISTEN_KEY = "listenKey"
KEEPALIVE_INTERVAL = 3600 * 23 + 60 * 55


class LiveTrading(Runner):
    def __init__(self) -> None:
        self.client, self.stream_url = (futures.client, futures.stream_url)
        self.listen_key = fetch(self.client.new_listen_key).get(LISTEN_KEY)
        self.client_ws = UMFuturesWebsocketClient(
            stream_url=self.stream_url, on_message=self._market_stream_handler
        )
        self.client_ws_user = UMFuturesWebsocketClient(
            stream_url=self.stream_url,
            on_message=self._user_stream_handler,
        )

        self.strategy: Final = Strategy.of(
            name=Required.STRATEGY.value,
            client=self.client,
        )
        self.realized_profit = {s: 0.0 for s in AppConfig.SYMBOLS.value}

    def _market_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                if data["e"] == EventType.KLINE.value:
                    self.strategy.on_new_candlestick(data=data["k"])
        except Exception as e:
            LOGGER.error(f"An error occurred in the market stream handler: {e}")

    def _user_stream_handler(self, _, stream) -> None:
        try:
            data = json.loads(stream)

            if "e" in data:
                LOGGER.debug(
                    f"Received {data['e']} event:\n{json.dumps(data, indent=2)}"
                )

                if data["e"] == EventType.ORDER_TRADE_UPDATE.value:
                    self._handle_order_trade_update(data["o"])

                elif data["e"] == EventType.ACCOUNT_UPDATE.value:
                    self._handle_account_update(data["a"])

                elif data["e"] == EventType.LISTEN_KEY_EXPIRED.value:
                    LOGGER.info("Listen key has expired. Recreating listen key...")
                    self.listen_key = fetch(self.client.new_listen_key).get(LISTEN_KEY)
                    fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        except Exception as e:
            LOGGER.error(f"An error occurred in the user data handler: {e}")

    def _handle_order_trade_update(self, data: dict[str, Any]):
        symbol = data["s"]
        og_order_type = OrderType(data["ot"])
        curr_order_type = OrderType(data["o"])
        status = OrderStatus(data["X"])
        realized_profit = float(data["rp"])

        if symbol in AppConfig.SYMBOLS.value:
            if status == OrderStatus.NEW and og_order_type == curr_order_type:
                self.strategy.on_new_order(data)

            elif status in [
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.FILLED,
            ]:
                self.realized_profit[symbol] += realized_profit

                if status == OrderStatus.FILLED:
                    self.strategy.on_filled_order(
                        data=data,
                        realized_profit=self.realized_profit[symbol],
                    )
                    self.realized_profit[symbol] = 0.0

            elif status == OrderStatus.CANCELED:
                self.strategy.on_cancelled_order(data)

            elif status == OrderStatus.EXPIRED:
                self.strategy.on_expired_order(data)

    def _handle_account_update(self, data: dict[str, Any]):
        if "P" in data and data["P"]:
            for position_data in data["P"]:
                self.strategy.on_position_update(position_data)

        if "B" in data and data["B"]:
            for balance_data in data["B"]:
                self.strategy.on_balance_update(balance_data)

    def run(self) -> None:
        LOGGER.info("Starting to run...")
        LOGGER.info("Connecting to User Data Stream...")
        fetch(self.client_ws_user.user_data, listen_key=self.listen_key)

        for s in AppConfig.SYMBOLS.value:
            self._set_leverage(symbol=s)
            self._subscribe_to_stream(symbol=s)

        self._keepalive_stream()

    def _set_leverage(self, symbol: str):
        fetch(
            self.client.change_leverage,
            symbol=symbol,
            leverage=AppConfig.LEVERAGE.value,
        )
        LOGGER.info(f"Set leverage for {symbol} to {AppConfig.LEVERAGE.value}.")

    def _subscribe_to_stream(self, symbol: str):
        fetch(self.client_ws.kline, symbol=symbol, interval=AppConfig.INTERVAL.value)
        LOGGER.info(f"Subscribed to {symbol} klines by {AppConfig.INTERVAL.value}...")

    def _keepalive_stream(self):
        while True:
            time.sleep(KEEPALIVE_INTERVAL)
            LOGGER.info("Reconnecting to the stream to maintain the connection...")

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

    def close(self) -> None:
        LOGGER.info("Initiating shutdown process...")
        fetch(self.client.close_listen_key, listenKey=self.listen_key)
        fetch(self.client_ws_user.stop)
        fetch(self.client_ws.stop)
        LOGGER.info("Shutdown process completed successfully.")
