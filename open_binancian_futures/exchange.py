import logging
from typing import cast

import pandas as pd
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    ExchangeInformationResponse,
    FuturesAccountBalanceV3Response,
    AllOrdersResponse,
    PositionInformationV3Response,
    KlineCandlestickDataResponse,
    CurrentAllAlgoOpenOrdersResponse,
)

from .models import Balance
from .constants import settings
from .types import OrderType, PositionSide
from .models import ExchangeInfo
from .models import Indicator
from .models import Order, OrderBook, OrderList
from .models import Position, PositionBook, PositionList
from .utils import fetch, get_or_raise
from .client import client

LOGGER = logging.getLogger(__name__)

# --- Binance Config & Client ---

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


def init_exchange_info() -> ExchangeInfo:
    data: ExchangeInformationResponse = fetch(client().rest_api.exchange_information)
    symbols = get_or_raise(data.symbols)
    target_symbols = [
        item
        for item in symbols
        if item.symbol is not None and item.symbol in settings.symbols_list
    ]
    return ExchangeInfo(target_symbols)


def init_balance() -> Balance:
    LOGGER.info("Fetching available balance...")
    data: list[FuturesAccountBalanceV3Response] = fetch(
        client().rest_api.futures_account_balance_v3
    )
    usdt_balance = next((item for item in data if item.asset == "USDT"), None)
    if usdt_balance:
        available = float(get_or_raise(usdt_balance.available_balance))
        LOGGER.info(f'Available USDT balance: "{available:.2f}"')
        return Balance(available)
    return Balance(0.0)


def _create_order_from_regular(item: AllOrdersResponse, symbol: str) -> Order:
    """Create Order from regular order response."""
    price = item.price or item.stop_price
    return Order(
        symbol=symbol,
        order_id=get_or_raise(item.order_id),
        type=OrderType(get_or_raise(item.orig_type)),
        side=PositionSide(get_or_raise(item.side)),
        price=float(get_or_raise(price)),
        quantity=float(get_or_raise(item.orig_qty)),
        gtd=item.good_till_date,
    )


def _create_order_from_algo(
    item: CurrentAllAlgoOpenOrdersResponse, symbol: str
) -> Order:
    """Create Order from algo order response."""
    price = item.price or item.trigger_price
    return Order(
        symbol=symbol,
        order_id=get_or_raise(item.algo_id),
        type=OrderType(get_or_raise(item.order_type)),
        side=PositionSide(get_or_raise(item.side)),
        price=float(get_or_raise(price)),
        quantity=float(get_or_raise(item.quantity)),
        gtd=item.good_till_date,
    )


def _fetch_regular_orders(symbol: str) -> list[Order]:
    items = cast(
        list[AllOrdersResponse],
        fetch(client().rest_api.current_all_open_orders, symbol=symbol),
    )
    return [_create_order_from_regular(item, symbol) for item in items]


def _fetch_algo_orders(symbol: str) -> list[Order]:
    items = cast(
        list[CurrentAllAlgoOpenOrdersResponse],
        fetch(client().rest_api.current_all_algo_open_orders, symbol=symbol),
    )
    return [_create_order_from_algo(item, symbol) for item in items]


def init_orders() -> OrderBook:
    orders = {
        symbol: OrderList(_fetch_regular_orders(symbol) + _fetch_algo_orders(symbol))
        for symbol in settings.symbols_list
    }
    LOGGER.info(f"Loaded open orders: {orders}")
    return OrderBook(orders)


def _create_position_from_response(
    item: PositionInformationV3Response, symbol: str
) -> Position | None:
    price = float(get_or_raise(item.entry_price))
    amount = float(get_or_raise(item.position_amt))
    bep = float(get_or_raise(item.break_even_price))

    if price == 0.0 or amount == 0.0:
        return None

    return Position(
        symbol=symbol,
        price=price,
        amount=amount,
        side=(PositionSide.BUY if amount > 0 else PositionSide.SELL),
        leverage=settings.leverage,
        break_even_price=bep,
    )


def init_positions() -> PositionBook:
    positions = {}
    for symbol in settings.symbols_list:
        items = cast(
            list[PositionInformationV3Response],
            fetch(client().rest_api.position_information_v3, symbol=symbol),
        )
        position_list = [
            pos
            for item in items
            if (pos := _create_position_from_response(item, symbol)) is not None
        ]
        positions[symbol] = PositionList(position_list)
    LOGGER.info(f"Loaded positions: {positions}")
    return PositionBook(positions)


def init_indicators(limit: int | None = None) -> Indicator:
    indicators = {}
    for symbol in settings.symbols_list:
        LOGGER.info(f"Fetching {symbol} klines by {settings.interval}...")
        klines_data: KlineCandlestickDataResponse = fetch(
            client().rest_api.kline_candlestick_data,
            symbol=symbol,
            interval=settings.interval,
            limit=limit,
        )[:-1]
        df = pd.DataFrame(data=klines_data, columns=KLINES_COLUMNS)
        df["Open_time"] = (
            pd.to_datetime(df["Open_time"], unit="ms")
            .dt.tz_localize("UTC")
            .dt.tz_convert(settings.timezone)
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
