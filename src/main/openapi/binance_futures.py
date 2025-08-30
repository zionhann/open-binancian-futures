import logging
from typing import Final

import pandas as pd
from binance.um_futures import UMFutures

from model.balance import Balance
from model.constant import AppConfig, BaseURL, OrderType, PositionSide, Required
from model.exchange_info import ExchangeInfo
from model.indicator import Indicator
from model.order import Order, OrderBook, OrderList
from model.position import Position, PositionBook, PositionList
from utils import fetch

LOGGER = logging.getLogger(__name__)

KLINES_COLUMNS = [
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

if AppConfig.IS_TESTNET.value:
    _base_url, stream_url = BaseURL.TESTNET_REST.value, BaseURL.TESTNET_WS.value
    _api_key, _api_secret = (
        Required.API_KEY_TEST.value,
        Required.API_SECRET_TEST.value,
    )
else:
    _base_url, stream_url = BaseURL.MAINNET_REST.value, BaseURL.MAINNET_WS.value
    _api_key, _api_secret = (
        Required.API_KEY.value,
        Required.API_SECRET.value,
    )

client: Final = UMFutures(
    key=_api_key,
    secret=_api_secret,
    base_url=_base_url,
)


def init_exchange_info() -> ExchangeInfo:
    payload = fetch(client.exchange_info)
    target_symbols = filter(
        lambda item: item["symbol"] in AppConfig.SYMBOLS.value, payload["symbols"]
    )
    return ExchangeInfo(target_symbols)


def init_balance() -> Balance:
    LOGGER.info("Fetching available USDT balance...")
    data = fetch(client.balance)["data"]
    df = pd.DataFrame(data)
    usdt_balance = df[df["asset"] == "USDT"]

    available = float(usdt_balance["availableBalance"].iloc[0])
    LOGGER.info(f'Available USDT balance: "{available:.2f}"')

    return Balance(available)


def init_orders() -> OrderBook:
    orders = {
        symbol: OrderList(
            [
                Order(
                    symbol=symbol,
                    id=item["orderId"],
                    type=OrderType(item["origType"]),
                    side=PositionSide(item["side"]),
                    price=float(item["price"]) or float(item["stopPrice"]),
                    quantity=float(item["origQty"]),
                    gtd=item["goodTillDate"],
                )
                for item in fetch(client.get_orders, symbol=symbol)["data"]
            ]
        )
        for symbol in AppConfig.SYMBOLS.value
    }
    LOGGER.info(f"Loaded open orders: {orders}")
    return OrderBook(orders)


def init_positions() -> PositionBook:
    positions = {
        symbol: PositionList(
            [
                Position(
                    symbol=symbol,
                    price=price,
                    amount=amount,
                    side=(PositionSide.BUY if amount > 0 else PositionSide.SELL),
                    leverage=AppConfig.LEVERAGE.value,
                    bep=bep
                )
                for p in fetch(client.get_position_risk, symbol=symbol)["data"]
                for price, amount, bep in
                [(float(p["entryPrice"]), float(p["positionAmt"]), float(p["breakEvenPrice"]))]
                if not (price == 0.0 or amount == 0.0)
            ]
        )
        for symbol in AppConfig.SYMBOLS.value
    }
    LOGGER.info(f"Loaded positions: {positions}")
    return PositionBook(positions)


def init_indicators(limit: int | None = None) -> Indicator:
    indicators = {}

    for symbol in AppConfig.SYMBOLS.value:
        LOGGER.info(f"Fetching {symbol} klines by {AppConfig.INTERVAL.value}...")

        klines_data = fetch(
            client.klines,
            symbol=symbol,
            interval=AppConfig.INTERVAL.value,
            limit=limit,
        )["data"][:-1]

        df = pd.DataFrame(data=klines_data, columns=KLINES_COLUMNS)
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

        indicators[symbol] = df

    return Indicator(indicators)
