import asyncio
import json
import logging
from typing import Optional

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    StartUserDataStreamResponse,
)
from binance_sdk_derivatives_trading_usds_futures.websocket_streams.models import (
    KlineCandlestickStreamsResponse,
    OrderTradeUpdate,
    OrderTradeUpdateO,
    AccountUpdate,
    AccountUpdateA,
    Listenkeyexpired,
)

from model.constant import *
from openapi import binance_futures as futures
from runner import Runner
from strategy import Strategy
from utils import fetch, get_or_raise
from webhook import Webhook

LOGGER = logging.getLogger(__name__)
MESSAGE = "message"
KEEPALIVE_USER_STREAM_INTERVAL = 60 * 50


class LiveTrading(Runner):
    def __init__(self) -> None:
        self.client = futures.client
        self.webhook = Webhook.of(AppConfig.WEBHOOK_URL)
        self.strategy = Strategy.of(
            name=Required.STRATEGY, client=self.client, webhook=self.webhook
        )
        self.realized_profit = {s: 0.0 for s in AppConfig.SYMBOLS}

    def _market_stream_handler(self, stream: KlineCandlestickStreamsResponse) -> None:
        try:
            if stream.e == EventType.KLINE.value:
                data = get_or_raise(stream.k)
                self.strategy.on_new_candlestick(data)
        except Exception as e:
            LOGGER.error(f"An error occurred in the market stream handler: {e}")
            self.webhook.send_message(
                f"[ALERT] An error occurred in the market stream handler:\n{e}"
            )

    def _user_stream_handler(self, stream: dict) -> None:
        try:

            if event := stream.get("e"):
                LOGGER.debug(f"Received {event} event:\n{json.dumps(stream, indent=2)}")

                if event == EventType.ORDER_TRADE_UPDATE.value:
                    order_trade_update = get_or_raise(
                        OrderTradeUpdate.from_dict(stream)
                    )
                    self._handle_order_trade_update(get_or_raise(order_trade_update.o))

                elif event == EventType.ACCOUNT_UPDATE.value:
                    account_update = get_or_raise(AccountUpdate.from_dict(stream))
                    self._handle_account_update(get_or_raise(account_update.a))

                elif event == EventType.LISTEN_KEY_EXPIRED.value:
                    LOGGER.info("Listen key has expired. Opening a new one...")
                    expired_key = get_or_raise(
                        Listenkeyexpired.from_dict(stream)
                    ).listenKey
                    asyncio.create_task(
                        self._subscribe_to_user_stream(expired_listen_key=expired_key)
                    )

        except Exception as e:
            LOGGER.error(f"An error occurred in the user data handler: {e}")
            self.webhook.send_message(
                f"[ALERT] An error occurred in the user data handler: {e}"
            )

    def _handle_order_trade_update(self, data: OrderTradeUpdateO):
        symbol = get_or_raise(data.s)
        og_order_type = OrderType(get_or_raise(data.ot))
        curr_order_type = OrderType(get_or_raise(data.o))
        status = OrderStatus(get_or_raise(data.X))
        realized_profit = float(get_or_raise(data.rp))

        if symbol in AppConfig.SYMBOLS:
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

    def _handle_account_update(self, data: AccountUpdateA):
        if data.P:
            for position_data in data.P:
                self.strategy.on_position_update(position_data)

        if data.B:
            for balance_data in data.B:
                self.strategy.on_balance_update(balance_data)

    def run(self) -> None:
        LOGGER.info("Starting to run...")
        LOGGER.info("Connecting to User Data Stream...")

        for s in AppConfig.SYMBOLS:
            self._set_leverage(symbol=s)

        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        await self.client.websocket_streams.create_connection()
        await asyncio.gather(
            *[self._subscribe_to_kline_stream(symbol=s) for s in AppConfig.SYMBOLS],
            self._subscribe_to_user_stream(),
        )
        asyncio.create_task(self._keepalive_user_stream())
        await asyncio.Event().wait()

    def _set_leverage(self, symbol: str):
        fetch(
            self.client.rest_api.change_initial_leverage,
            symbol=symbol,
            leverage=AppConfig.LEVERAGE,
        )
        LOGGER.info(f"Set leverage for {symbol} to {AppConfig.LEVERAGE}.")

    async def _subscribe_to_kline_stream(self, symbol: str):
        stream = await self.client.websocket_streams.kline_candlestick_streams(
            symbol=symbol, interval=AppConfig.INTERVAL
        )
        stream.on(event=MESSAGE, callback=self._market_stream_handler)
        LOGGER.info(f"Subscribed to {symbol} klines by {AppConfig.INTERVAL}...")

    async def _subscribe_to_user_stream(self, expired_listen_key: Optional[str] = None):
        if expired_listen_key:
            await self.client.websocket_streams.unsubscribe([expired_listen_key])
            await asyncio.sleep(1)

        data: StartUserDataStreamResponse = fetch(
            self.client.rest_api.start_user_data_stream
        )
        listen_key = get_or_raise(data.listen_key)

        stream = await self.client.websocket_streams.user_data(listen_key)
        stream.on(event=MESSAGE, callback=self._user_stream_handler)
        LOGGER.info("Subscribed to user data stream...")

    async def _keepalive_user_stream(self):
        while True:
            await asyncio.sleep(KEEPALIVE_USER_STREAM_INTERVAL)
            fetch(self.client.rest_api.keepalive_user_data_stream)
            LOGGER.info("Successfully sent keepalive for user data stream.")

    def close(self) -> None:
        LOGGER.info("Initiating shutdown process...")
        asyncio.run(self._close_async())
        self.client.rest_api.close_user_data_stream()
        LOGGER.info("Shutdown process completed successfully.")

    async def _close_async(self) -> None:
        await asyncio.gather(
            self.client.websocket_streams.close_connection(),
            self.client.websocket_api.close_connection(),
        )
