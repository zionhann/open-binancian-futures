import logging
from typing import cast

import pandas as pd
from binance_common.configuration import (
    ConfigurationWebSocketStreams,
    ConfigurationWebSocketAPI,
)
from binance_common.constants import (
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL as TESTNET_REST,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL as TESTNET_WS_STREAMS,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_TESTNET_URL as TESTNET_WS_API,
)
from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    ConfigurationRestAPI,
    DERIVATIVES_TRADING_USDS_FUTURES_REST_API_PROD_URL as MAINNET_REST,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_PROD_URL as MAINNET_WS_STREAMS,
    DERIVATIVES_TRADING_USDS_FUTURES_WS_API_PROD_URL as MAINNET_WS_API,
)
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponse,
    FuturesAccountBalanceV3Response,
    AllOrdersResponse,
    PositionInformationV3Response,
    KlineCandlestickDataResponse,
    CurrentAllAlgoOpenOrdersResponse,
)

from model.balance import Balance
from model.constant import AppConfig, OrderType, PositionSide, Required
from model.exchange_info import ExchangeInfo
from model.indicator import Indicator
from model.order import Order, OrderBook, OrderList
from model.position import Position, PositionBook, PositionList
from utils import fetch, get_or_raise

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

if AppConfig.IS_TESTNET:
    _rest_url, ws_stream_url, ws_api_url = (
        TESTNET_REST,
        TESTNET_WS_STREAMS,
        TESTNET_WS_API,
    )

    if Required.API_KEY_TEST is None or Required.API_SECRET_TEST is None:
        raise ValueError()

    _api_key, _api_secret = (
        Required.API_KEY_TEST,
        Required.API_SECRET_TEST,
    )

else:
    _rest_url, ws_stream_url, ws_api_url = (
        MAINNET_REST,
        MAINNET_WS_STREAMS,
        MAINNET_WS_API,
    )

    if Required.API_KEY is None or Required.API_SECRET is None:
        raise ValueError()

    _api_key, _api_secret = (
        Required.API_KEY,
        Required.API_SECRET,
    )

config_rest = ConfigurationRestAPI(
    api_key=_api_key, api_secret=_api_secret, base_path=_rest_url, timeout=2000
)
config_ws_api = ConfigurationWebSocketAPI(
    api_key=_api_key, api_secret=_api_secret, stream_url=ws_api_url
)
config_ws = ConfigurationWebSocketStreams(stream_url=ws_stream_url)

client = DerivativesTradingUsdsFutures(
    config_rest_api=config_rest,
    config_ws_api=config_ws_api,
    config_ws_streams=config_ws,
)


def init_exchange_info() -> ExchangeInfo:
    data: ExchangeInformationResponse = fetch(client.rest_api.exchange_information)
    symbols = get_or_raise(data.symbols)
    target_symbols = [
        item
        for item in symbols
        if item.symbol is not None and item.symbol in AppConfig.SYMBOLS
    ]
    return ExchangeInfo(target_symbols)


def init_balance() -> Balance:
    LOGGER.info("Fetching available balance...")
    data: list[FuturesAccountBalanceV3Response] = fetch(
        client.rest_api.futures_account_balance_v3
    )

    if usdt_balance := list(filter(lambda item: item.asset == "USDT", data)):
        available = get_or_raise(usdt_balance[0].available_balance)
        initial_balance = float(available)
        LOGGER.info(f'Available USDT balance: "{initial_balance:.2f}"')

        return Balance(initial_balance)

    return Balance(0.0)


def _fetch_regular_orders(symbol: str) -> list[Order]:
    """Fetch regular open orders for a symbol."""
    return [
        Order(
            symbol=symbol,
            id=get_or_raise(item.order_id),
            type=OrderType(get_or_raise(item.orig_type)),
            side=PositionSide(get_or_raise(item.side)),
            price=float(get_or_raise(item.price))
            or float(get_or_raise(item.stop_price)),
            quantity=float(get_or_raise(item.orig_qty)),
            gtd=get_or_raise(item.good_till_date),
        )
        for item in cast(
            list[AllOrdersResponse],
            fetch(client.rest_api.current_all_open_orders, symbol=symbol),
        )
    ]


def _fetch_algo_orders(symbol: str) -> list[Order]:
    """Fetch algo open orders for a symbol."""
    return [
        Order(
            symbol=symbol,
            id=get_or_raise(item.algo_id),
            type=OrderType(get_or_raise(item.order_type)),
            side=PositionSide(get_or_raise(item.side)),
            price=float(get_or_raise(item.price))
            or float(get_or_raise(item.trigger_price)),
            quantity=float(get_or_raise(item.quantity)),
            gtd=get_or_raise(item.good_till_date),
        )
        for item in cast(
            list[CurrentAllAlgoOpenOrdersResponse],
            fetch(client.rest_api.current_all_algo_open_orders, symbol=symbol),
        )
    ]


def init_orders() -> OrderBook:
    """Initialize OrderBook with both regular and algo orders."""
    orders = {
        symbol: OrderList(_fetch_regular_orders(symbol) + _fetch_algo_orders(symbol))
        for symbol in AppConfig.SYMBOLS
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
                    leverage=AppConfig.LEVERAGE,
                    bep=bep,
                )
                for p in cast(
                    list[PositionInformationV3Response],
                    fetch(client.rest_api.position_information_v3, symbol=symbol),
                )
                for price, amount, bep in [
                    (
                        float(get_or_raise(p.entry_price)),
                        float(get_or_raise(p.position_amt)),
                        float(get_or_raise(p.break_even_price)),
                    )
                ]
                if not (price == 0.0 or amount == 0.0)
            ]
        )
        for symbol in AppConfig.SYMBOLS
    }
    LOGGER.info(f"Loaded positions: {positions}")
    return PositionBook(positions)


def init_indicators(limit: int | None = None) -> Indicator:
    indicators = {}

    for symbol in AppConfig.SYMBOLS:
        LOGGER.info(f"Fetching {symbol} klines by {AppConfig.INTERVAL}...")

        klines_data: KlineCandlestickDataResponse = fetch(
            client.rest_api.kline_candlestick_data,
            symbol=symbol,
            interval=AppConfig.INTERVAL,
            limit=limit,
        )[:-1]

        df = pd.DataFrame(data=klines_data, columns=KLINES_COLUMNS)
        df["Open_time"] = (
            pd.to_datetime(df["Open_time"], unit="ms")
            .dt.tz_localize("UTC")
            .dt.tz_convert(AppConfig.TIMEZONE)
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
