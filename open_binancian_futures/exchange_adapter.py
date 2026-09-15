"""Injected, synchronous REST boundary. Placement is never automatically retried."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewAlgoOrderSideEnum,
    NewOrderSideEnum,
)

from . import exchange
from .execution import ExecutionConfig
from .models import (
    Balance,
    ExchangeInfo,
    Indicator,
    OrderBook,
    OrderIntent,
    PositionBook,
)
from .types import OrderType


class OrderOutcomeUnknown(RuntimeError):
    """Exchange execution is uncertain; retain reservation and reconcile the ID."""


class OrderRejected(RuntimeError):
    """Exchange explicitly rejected the request without accepting an order."""


@dataclass(frozen=True)
class OrderReceipt:
    order_id: int
    client_order_id: str | None
    symbol: str | None
    status: str | None
    executed_quantity: float | None
    average_price: float | None
    raw: dict[str, Any]


def response_data(value: Any) -> Any:
    if callable(getattr(value, "data", None)):
        value = value.data()
    if callable(getattr(value, "to_dict", None)):
        value = value.to_dict()
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    return value


def normalize_receipt(value: Any) -> OrderReceipt:
    try:
        data = response_data(value)
        identifier = data.get("orderId", data.get("algoId"))
        if (
            isinstance(identifier, bool)
            or not isinstance(identifier, (int, str))
            or not str(identifier).isdigit()
            or int(identifier) <= 0
        ):
            raise ValueError("missing positive order ID")
        status = data.get("status", data.get("algoStatus"))
        if status is not None and status not in {
            "NEW",
            "PARTIALLY_FILLED",
            "FILLED",
            "CANCELED",
            "EXPIRED",
            "EXPIRED_IN_MATCH",
            "REJECTED",
            "TRIGGERING",
            "TRIGGERED",
            "FINISHED",
        }:
            raise ValueError("invalid order status")
        return OrderReceipt(
            int(identifier),
            data.get("clientOrderId", data.get("clientAlgoId")),
            data.get("symbol"),
            status,
            float(data["executedQty"]) if data.get("executedQty") is not None else None,
            float(data["avgPrice"]) if data.get("avgPrice") is not None else None,
            data,
        )
    except (TypeError, ValueError, AttributeError) as error:
        raise OrderOutcomeUnknown("Malformed exchange receipt") from error


@dataclass
class ExchangeSnapshot:
    exchange_info: ExchangeInfo
    balance: Balance
    orders: OrderBook
    positions: PositionBook
    leverage: dict[str, int]


class OrderGateway(Protocol):
    async def submit_order(self, intent: OrderIntent) -> bool: ...


class ExchangeStreams(Protocol):
    """Task 5 supervisor-owned streaming surface; callbacks are synchronous.

    Implementations must own receiver/timer tasks and report recovery signals.
    The REST adapter deliberately does not expose unowned SDK streams.
    """

    async def connect(self) -> None: ...
    async def subscribe_klines(
        self, symbol: str, interval: str, callback: Callable[[Any], None]
    ) -> None: ...
    async def subscribe_user(
        self, listen_key: str, callback: Callable[[Any], None]
    ) -> None: ...
    async def close(self) -> None: ...


class ExchangeAdapter(Protocol):
    def snapshot(
        self, symbols: Sequence[str], execution_config: ExecutionConfig
    ) -> ExchangeSnapshot: ...
    def initial_indicators(
        self, symbols: Sequence[str], intervals: Sequence[str], timezone: str
    ) -> Indicator: ...
    def identity(self) -> str: ...
    def server_time(self) -> int: ...
    def start_listen_key(self) -> str: ...
    def keepalive_listen_key(self, listen_key: str) -> None: ...
    def close_listen_key(self, listen_key: str) -> None: ...
    def account_mode(self) -> bool: ...
    def leverage(self, symbol: str) -> int: ...
    def set_leverage(self, symbol: str, leverage: int) -> None: ...
    def history(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
        limit: int = 1000,
    ) -> list[list[Any]]: ...
    def submit(self, intent: OrderIntent, client_order_id: str) -> OrderReceipt: ...
    def query(
        self, symbol: str, client_order_id: str, *, algo: bool = False
    ) -> OrderReceipt: ...
    def cancel(
        self, symbol: str, client_order_id: str, *, algo: bool = False
    ) -> OrderReceipt: ...


class BinanceExchangeAdapter:
    # Only explicit parameter/margin/filter rejections. Unknown is the default.
    REJECTION_CODES = frozenset(
        {
            -1100,
            -1101,
            -1102,
            -1103,
            -1104,
            -1105,
            -1106,
            -1111,
            -1116,
            -1117,
            -1118,
            -1119,
            -1121,
            -1130,
            -2019,
            -2022,
            -4003,
            -4004,
            -4005,
            -4013,
            -4014,
            -4023,
            -4164,
        }
    )

    def __init__(self, client: Any) -> None:
        self.client = client
        self.rest = client.rest_api

    def initial_indicators(
        self, symbols: Sequence[str], intervals: Sequence[str], timezone: str
    ) -> Indicator:
        return exchange.init_indicators(
            symbols=symbols,
            intervals=intervals,
            timezone=timezone,
            sdk_client=self.client,
        )

    def identity(self) -> str:
        configuration = self.rest.configuration
        endpoint = str(configuration.base_path).rstrip('/')
        key = configuration.api_key
        if not key:
            raise ValueError('API key is required for account identity')
        return hashlib.sha256((endpoint + '\0' + str(key)).encode()).hexdigest()

    def server_time(self) -> int:
        return int(response_data(self.rest.check_server_time())['serverTime'])

    def start_listen_key(self) -> str:
        key = response_data(self.rest.start_user_data_stream()).get('listenKey')
        if not isinstance(key, str) or not key:
            raise ValueError('Missing listen key')
        return key

    def keepalive_listen_key(self, listen_key: str) -> None:
        self.rest.keepalive_user_data_stream().data()

    def close_listen_key(self, listen_key: str) -> None:
        self.rest.close_user_data_stream().data()

    def account_mode(self) -> bool:
        data = response_data(self.rest.get_current_position_mode())
        mode = data.get("dualSidePosition")
        if not isinstance(mode, bool):
            raise ValueError("Invalid account position mode")
        return mode

    def leverage(self, symbol: str) -> int:
        values = self.rest.symbol_configuration(symbol=symbol).data()
        for value in values:
            data = response_data(value)
            if data.get("symbol") == symbol:
                leverage = data.get("leverage")
                if (
                    isinstance(leverage, int)
                    and not isinstance(leverage, bool)
                    and leverage > 0
                ):
                    return leverage
        raise ValueError(f"Missing actual leverage for {symbol}")

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self.rest.change_initial_leverage(symbol=symbol, leverage=leverage).data()

    def snapshot(
        self, symbols: Sequence[str], execution_config: ExecutionConfig
    ) -> ExchangeSnapshot:
        leverages = {symbol: self.leverage(symbol) for symbol in symbols}
        positions = PositionBook(symbols=symbols)
        for symbol in symbols:
            positions[symbol] = exchange.init_positions(
                leverages[symbol], symbols=[symbol], sdk_client=self.client
            )[symbol]
        return ExchangeSnapshot(
            exchange.init_exchange_info(symbols, sdk_client=self.client),
            exchange.init_balance(execution_config, sdk_client=self.client),
            exchange.init_orders(symbols, sdk_client=self.client),
            positions,
            leverages,
        )

    def history(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
        limit: int = 1000,
    ) -> list[list[Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("history limit must be within 1..1000")
        return self.rest.kline_candlestick_data(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        ).data()

    @staticmethod
    def is_algo(intent: OrderIntent) -> bool:
        return intent.order_type not in {OrderType.LIMIT, OrderType.MARKET}

    def submit(self, intent: OrderIntent, client_order_id: str) -> OrderReceipt:
        if not client_order_id:
            raise ValueError("client_order_id is required")
        if intent.close_position and (
            intent.order_type not in {OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET}
            or intent.quantity is not None
            or intent.reduce_only
        ):
            raise ValueError(
                "close_position requires stop/take-profit market without quantity or reduce_only"
            )
        if intent.order_type == OrderType.MARKET and (
            intent.time_in_force is not None or intent.gtd is not None
        ):
            raise ValueError("MARKET orders do not support time_in_force or good till date")
        algo = self.is_algo(intent)
        kwargs: dict[str, Any] = dict(
            symbol=intent.symbol,
            side=(
                NewAlgoOrderSideEnum(intent.side.value)
                if algo
                else NewOrderSideEnum(intent.side.value)
            ),
            type=intent.order_type.value,
        )
        if intent.quantity is not None:
            kwargs["quantity"] = intent.quantity
        kwargs["reduce_only"] = "true" if intent.reduce_only else "false"
        kwargs["client_algo_id" if algo else "new_client_order_id"] = client_order_id
        tif = intent.time_in_force or ("GTD" if intent.gtd is not None else "GTC")
        if intent.gtd is not None and tif != "GTD":
            raise ValueError("good till date requires GTD time in force")
        if intent.order_type != OrderType.MARKET:
            kwargs["time_in_force"] = tif
        if intent.gtd is not None:
            kwargs["good_till_date"] = intent.gtd
        if algo:
            kwargs["algo_type"] = "CONDITIONAL"
            if intent.close_position:
                kwargs["close_position"] = "true"
                kwargs.pop("reduce_only")
                kwargs.pop("quantity", None)
            if intent.order_type == OrderType.TRAILING_STOP_MARKET:
                kwargs["activate_price"] = intent.activation_price
                kwargs["callback_rate"] = intent.callback_rate
            else:
                kwargs["trigger_price"] = intent.price
                if intent.order_type in {
                    OrderType.STOP_LIMIT,
                    OrderType.TAKE_PROFIT_LIMIT,
                }:
                    kwargs["price"] = intent.price
        elif intent.order_type == OrderType.LIMIT:
            kwargs["price"] = intent.price
        try:
            return normalize_receipt(
                (self.rest.new_algo_order if algo else self.rest.new_order)(**kwargs)
            )
        except OrderOutcomeUnknown:
            raise
        except Exception as error:
            if getattr(error, "status_code", None) in self.REJECTION_CODES:
                raise OrderRejected(str(error)) from error
            raise OrderOutcomeUnknown(
                "Placement outcome requires reconciliation"
            ) from error

    @staticmethod
    def _validate_identity(
        receipt: OrderReceipt, symbol: str, client_order_id: str
    ) -> None:
        if receipt.symbol is not None and receipt.symbol != symbol:
            raise OrderOutcomeUnknown("Exchange returned another symbol")
        if (
            receipt.client_order_id is not None
            and receipt.client_order_id != client_order_id
        ):
            raise OrderOutcomeUnknown("Exchange returned another client order ID")

    def query(
        self, symbol: str, client_order_id: str, *, algo: bool = False
    ) -> OrderReceipt:
        try:
            result = (
                self.rest.query_algo_order(client_algo_id=client_order_id)
                if algo
                else self.rest.query_order(
                    symbol=symbol, orig_client_order_id=client_order_id
                )
            )
            receipt = normalize_receipt(result)
            self._validate_identity(receipt, symbol, client_order_id)
            if receipt.status is None:
                raise OrderOutcomeUnknown("Lookup has no order status")
            return receipt
        except Exception as error:
            raise OrderOutcomeUnknown(
                "Lookup did not establish order outcome"
            ) from error

    def cancel(
        self, symbol: str, client_order_id: str, *, algo: bool = False
    ) -> OrderReceipt:
        try:
            result = (
                self.rest.cancel_algo_order(client_algo_id=client_order_id)
                if algo
                else self.rest.cancel_order(
                    symbol=symbol, orig_client_order_id=client_order_id
                )
            )
            receipt = normalize_receipt(result)
            self._validate_identity(receipt, symbol, client_order_id)
            return receipt
        except Exception as error:
            raise OrderOutcomeUnknown(
                "Cancellation outcome requires reconciliation"
            ) from error
