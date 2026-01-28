import logging
import os
from typing import Any, cast, Iterable, Optional

import pandas as pd
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, Message
from openai import AsyncOpenAI

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

from .models import Balance
from .constants import settings
from .types import OrderType, PositionSide
from .models import ExchangeInfo
from .models import Indicator
from .models import Order, OrderBook, OrderList
from .models import Position, PositionBook, PositionList
from .utils import fetch, get_or_raise

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


class BinanceClientManager:
    def __init__(self):
        self._client: DerivativesTradingUsdsFutures | None = None

    def _get_config(self) -> tuple[str, str, str, str, str]:
        if settings.is_testnet:
            if settings.api_key_test is None or settings.api_secret_test is None:
                raise ValueError("Testnet API credentials not configured")
            return (
                TESTNET_REST,
                TESTNET_WS_STREAMS,
                TESTNET_WS_API,
                settings.api_key_test,
                settings.api_secret_test,
            )
        else:
            if settings.api_key is None or settings.api_secret is None:
                raise ValueError("Mainnet API credentials not configured")
            return (
                MAINNET_REST,
                MAINNET_WS_STREAMS,
                MAINNET_WS_API,
                settings.api_key,
                settings.api_secret,
            )

    @property
    def client(self) -> DerivativesTradingUsdsFutures:
        if self._client is None:
            rest_url, ws_stream_url, ws_api_url, api_key, api_secret = (
                self._get_config()
            )
            config_rest = ConfigurationRestAPI(
                api_key=api_key, api_secret=api_secret, base_path=rest_url, timeout=2000
            )
            config_ws_api = ConfigurationWebSocketAPI(
                api_key=api_key, api_secret=api_secret, stream_url=ws_api_url
            )
            config_ws = ConfigurationWebSocketStreams(stream_url=ws_stream_url)
            self._client = DerivativesTradingUsdsFutures(
                config_rest_api=config_rest,
                config_ws_api=config_ws_api,
                config_ws_streams=config_ws,
            )
            network = "Testnet" if settings.is_testnet else "Mainnet"
            LOGGER.info(f"Binance Futures client initialized ({network})")
        return self._client

    def cleanup(self) -> None:
        if self._client is not None:
            LOGGER.info("Binance client cleanup completed")


_client_manager = BinanceClientManager()


class _ClientProxy:
    """Minimal proxy for lazy client initialization."""

    def __getattr__(self, name: str) -> Any:
        return getattr(_client_manager.client, name)


# Proxy allows imports without requiring API keys
client: DerivativesTradingUsdsFutures = _ClientProxy()  # type: ignore


def init_exchange_info() -> ExchangeInfo:
    data: ExchangeInformationResponse = fetch(client.rest_api.exchange_information)
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
        client.rest_api.futures_account_balance_v3
    )
    usdt_balance = next((item for item in data if item.asset == "USDT"), None)
    if usdt_balance:
        available = float(get_or_raise(usdt_balance.available_balance))
        LOGGER.info(f'Available USDT balance: "{available:.2f}"')
        return Balance(available)
    return Balance(0.0)


def _create_order(item: Any, symbol: str) -> Order:
    """Create Order from either regular or algo response."""
    # Handle both response types with flexible attribute access
    order_id = getattr(item, "order_id", None) or getattr(item, "algo_id", None)
    order_type = getattr(item, "orig_type", None) or getattr(item, "order_type", None)
    side = getattr(item, "side", None)

    # Price can be in different fields
    price = (
        getattr(item, "price", None)
        or getattr(item, "stop_price", None)
        or getattr(item, "trigger_price", None)
    )

    # Quantity fields vary
    quantity = getattr(item, "orig_qty", None) or getattr(item, "quantity", None)

    gtd = getattr(item, "good_till_date", None)

    return Order(
        symbol=symbol,
        id=get_or_raise(order_id),
        type=OrderType(get_or_raise(order_type)),
        side=PositionSide(get_or_raise(side)),
        price=float(get_or_raise(price)),
        quantity=float(get_or_raise(quantity)),
        gtd=get_or_raise(gtd),
    )


def _fetch_regular_orders(symbol: str) -> list[Order]:
    items = cast(
        list[AllOrdersResponse],
        fetch(client.rest_api.current_all_open_orders, symbol=symbol),
    )
    return [_create_order(item, symbol) for item in items]


def _fetch_algo_orders(symbol: str) -> list[Order]:
    items = cast(
        list[CurrentAllAlgoOpenOrdersResponse],
        fetch(client.rest_api.current_all_algo_open_orders, symbol=symbol),
    )
    return [_create_order(item, symbol) for item in items]


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
            fetch(client.rest_api.position_information_v3, symbol=symbol),
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
            client.rest_api.kline_candlestick_data,
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


# --- AI Clients ---


async def ask_anthropic(
    messages: Iterable[MessageParam], model: str, max_tokens: int, **kwargs
) -> Optional[Message]:
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            LOGGER.error("ANTHROPIC_API_KEY not configured")
            return None
        client = AsyncAnthropic(api_key=api_key)
        return await client.messages.create(
            max_tokens=max_tokens, model=model, messages=messages, **kwargs
        )
    except Exception as e:
        LOGGER.error(f"Error in Anthropic API: {e}")
        return None


async def ask_openai(input: str, model: str, **kwargs) -> Optional[str]:
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            LOGGER.error("OPENAI_API_KEY not configured")
            return None
        client = AsyncOpenAI(api_key=api_key)
        response = await client.responses.create(model=model, input=input, **kwargs)
        return response.output_text
    except Exception as e:
        LOGGER.error(f"Error in OpenAI API: {e}")
        return None
