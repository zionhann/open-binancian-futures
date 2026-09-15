from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from binance_common.errors import BadRequestError
from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
    NewOrderResponse,
)

from open_binancian_futures.exchange_adapter import (
    BinanceExchangeAdapter,
    OrderOutcomeUnknown,
    OrderRejected,
    normalize_receipt,
)
from open_binancian_futures.models import OrderIntent
from open_binancian_futures.types import OrderType, PositionSide


def test_receipt_real_model_flat_and_envelope():
    raw = dict(orderId=123, clientOrderId="mine", symbol="BTCUSDT", status="NEW")
    for value in [raw, NewOrderResponse.from_dict(raw), {"result": raw}]:
        assert normalize_receipt(value).order_id == 123
    for raw in [{}, {"orderId": 0}, {"orderId": 1, "status": "invented"}]:
        with pytest.raises(OrderOutcomeUnknown):
            normalize_receipt(raw)


def test_submit_once_unknown_and_explicit_rejection():
    rest = Mock()
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    intent = OrderIntent("BTCUSDT", PositionSide.BUY, OrderType.LIMIT, 10, 2)
    for error, expected in [
        (TimeoutError(), OrderOutcomeUnknown),
        (BadRequestError("uncertain", -1007), OrderOutcomeUnknown),
        (BadRequestError("margin", -2019), OrderRejected),
    ]:
        rest.new_order.reset_mock()
        rest.new_order.side_effect = error
        with pytest.raises(expected):
            adapter.submit(intent, "mine")
        assert rest.new_order.call_count == 1
        assert rest.new_order.call_args.kwargs["new_client_order_id"] == "mine"
        assert rest.new_order.call_args.kwargs["reduce_only"] == "false"


def test_query_uses_client_identifier_and_flat_dict():
    rest = Mock()
    rest.query_order.return_value.data.return_value = dict(
        orderId=123, clientOrderId="mine", symbol="BTCUSDT", status="FILLED"
    )
    assert (
        BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
        .query("BTCUSDT", "mine")
        .status
        == "FILLED"
    )
    rest.query_order.assert_called_once_with(
        symbol="BTCUSDT", orig_client_order_id="mine"
    )


def test_algo_close_and_trailing_semantics():
    rest = Mock()
    rest.new_algo_order.return_value.data.return_value = dict(
        algoId=42, algoStatus="NEW"
    )
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    adapter.submit(
        OrderIntent(
            "BTCUSDT",
            PositionSide.SELL,
            OrderType.STOP_MARKET,
            10,
            close_position=True,
            time_in_force="GTE_GTC",
        ),
        "close",
    )
    args = rest.new_algo_order.call_args.kwargs
    assert args["client_algo_id"] == "close"
    assert args["close_position"] == "true"
    assert args["trigger_price"] == 10
    assert args["time_in_force"] == "GTE_GTC"
    assert "quantity" not in args and "reduce_only" not in args
    adapter.submit(
        OrderIntent(
            "BTCUSDT",
            PositionSide.SELL,
            OrderType.TRAILING_STOP_MARKET,
            quantity=2,
            reduce_only=True,
            activation_price=11,
            callback_rate=0.5,
            time_in_force="GTE_GTC",
        ),
        "trail",
    )
    args = rest.new_algo_order.call_args.kwargs
    assert args["activate_price"] == 11 and args["callback_rate"] == 0.5
    assert args["reduce_only"] == "true"


def test_actual_leverage_list_and_missing_symbol():
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        SymbolConfigurationResponse,
    )

    rest = Mock()
    rest.symbol_configuration.return_value.data.return_value = [
        SymbolConfigurationResponse(symbol="BTCUSDT", leverage=7)
    ]
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    assert adapter.leverage("BTCUSDT") == 7
    with pytest.raises(ValueError):
        adapter.leverage("ETHUSDT")


@pytest.mark.asyncio
async def test_strategy_gateway_preserves_time_and_unknown():
    from unittest.mock import AsyncMock
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        NewOrderSideEnum,
        NewOrderTimeInForceEnum,
    )
    from open_binancian_futures.strategy import Strategy

    class Concrete(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol, interval):
            pass

        def run_backtest(self, *args):
            pass

    strategy = Concrete(None, None, None, None, None, None, None)
    strategy.order_gateway = SimpleNamespace(
        submit_order=AsyncMock(side_effect=OrderOutcomeUnknown("timeout"))
    )
    with pytest.raises(OrderOutcomeUnknown):
        await strategy.open_order(
            "BTCUSDT",
            NewOrderSideEnum.BUY,
            OrderType.LIMIT,
            10,
            NewOrderTimeInForceEnum.GTD,
            123456,
        )
    intent = strategy.order_gateway.submit_order.call_args.args[0]
    assert intent.time_in_force == "GTD" and intent.gtd == 123456
    with pytest.raises(RuntimeError, match="submit_order"):
        strategy.set_trailing_stop("BTCUSDT", PositionSide.SELL, 2, 0.1)


def test_snapshot_uses_only_injected_client_and_preserves_remaining_order(monkeypatch):
    from open_binancian_futures import exchange
    from open_binancian_futures.execution import ExecutionConfig
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        AllOrdersResponse,
        PositionInformationV3Response,
        SymbolConfigurationResponse,
    )

    monkeypatch.setattr(
        exchange, "client", Mock(side_effect=AssertionError("global client used"))
    )
    rest = Mock()
    rest.symbol_configuration.return_value.data.return_value = [
        SymbolConfigurationResponse(symbol="BTCUSDT", leverage=7)
    ]
    rest.exchange_information.return_value.data.return_value = SimpleNamespace(
        symbols=[]
    )
    rest.futures_account_balance_v3.return_value.data.return_value = []
    rest.position_information_v3.return_value.data.return_value = [
        PositionInformationV3Response(
            symbol="BTCUSDT", entryPrice="10", positionAmt="2", breakEvenPrice="10"
        )
    ]
    rest.current_all_open_orders.return_value.data.return_value = [
        AllOrdersResponse(
            orderId=1,
            origType="LIMIT",
            side="SELL",
            price="12",
            origQty="3",
            executedQty="1",
            reduceOnly=True,
        )
    ]
    rest.current_all_algo_open_orders.return_value.data.return_value = []
    snapshot = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest)).snapshot(
        ["BTCUSDT"], ExecutionConfig(leverage=1)
    )
    assert snapshot.positions["BTCUSDT"].find_first().leverage == 7
    order = next(iter(snapshot.orders["BTCUSDT"]))
    assert order.quantity == 2 and order.reduce_only


def test_query_missing_status_and_not_found_remain_unknown():
    rest = Mock()
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    rest.query_order.return_value.data.return_value = {"orderId": 1}
    with pytest.raises(OrderOutcomeUnknown):
        adapter.query("BTCUSDT", "mine")
    rest.query_order.side_effect = BadRequestError("not found", -2013)
    with pytest.raises(OrderOutcomeUnknown):
        adapter.query("BTCUSDT", "mine")


@pytest.mark.parametrize(
    "field,value", [("symbol", "ETHUSDT"), ("clientOrderId", "other")]
)
def test_query_rejects_identity_mismatch(field, value):
    rest = Mock()
    rest.query_order.return_value.data.return_value = {
        "orderId": 1,
        "status": "NEW",
        field: value,
    }
    with pytest.raises(OrderOutcomeUnknown):
        BinanceExchangeAdapter(SimpleNamespace(rest_api=rest)).query("BTCUSDT", "mine")


@pytest.mark.parametrize("identifier", [True, 0, -1, 1.5, "1.5"])
def test_malformed_identifier_is_unknown(identifier):
    with pytest.raises(OrderOutcomeUnknown):
        normalize_receipt({"orderId": identifier})


def test_runner_accepts_adapter_without_global_client(monkeypatch):
    from open_binancian_futures import live
    monkeypatch.setattr(live, "client", Mock(side_effect=AssertionError("global client used")))
    adapter = SimpleNamespace(snapshot=Mock(), initial_indicators=Mock())
    runner = live.LiveTrading(adapter=adapter)
    assert runner.adapter is adapter and runner.client is None
    adapter.snapshot.assert_not_called()
    adapter.initial_indicators.assert_not_called()
    runner.close()


def test_algo_close_position_string_zero_uses_trigger_and_is_protective():
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import CurrentAllAlgoOpenOrdersResponse
    from open_binancian_futures.exchange import _create_order_from_algo
    model=CurrentAllAlgoOpenOrdersResponse.model_validate({'algoId':8,'symbol':'BTCUSDT','orderType':'STOP_MARKET','side':'SELL','price':'0','triggerPrice':'90','quantity':'0','closePosition':True,'reduceOnly':False})
    order=_create_order_from_algo(model,'BTCUSDT')
    assert order.price==90 and order.reduce_only and order.quantity==0


def test_real_sdk_identity_fingerprint_is_account_and_endpoint_bound():
    from binance_sdk_derivatives_trading_usds_futures import DerivativesTradingUsdsFutures
    from binance_common.configuration import ConfigurationRestAPI
    def fingerprint(key,url):
        client=DerivativesTradingUsdsFutures(config_rest_api=ConfigurationRestAPI(api_key=key,api_secret='secret-never-stored',base_path=url))
        return BinanceExchangeAdapter(client).identity()
    first=fingerprint('key-a','https://fapi.binance.com')
    assert len(first)==64 and 'key-a' not in first and 'secret' not in first
    assert first!=fingerprint('key-b','https://fapi.binance.com')
    assert first!=fingerprint('key-a','https://testnet.binancefuture.com')


@pytest.mark.parametrize("reduce_only", [False, True])
def test_market_omits_time_in_force_and_expiry(reduce_only):
    rest = Mock()
    rest.new_order.return_value = NewOrderResponse(orderId=1)
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    adapter.submit(
        OrderIntent(
            "BTCUSDT",
            PositionSide.SELL,
            OrderType.MARKET,
            quantity=2,
            reduce_only=reduce_only,
        ),
        "mine",
    )
    kwargs = rest.new_order.call_args.kwargs
    assert "time_in_force" not in kwargs
    assert "good_till_date" not in kwargs
    assert kwargs["reduce_only"] == str(reduce_only).lower()


@pytest.mark.parametrize(
    "options",
    [
        {"time_in_force": "GTC"},
        {"gtd": 123456},
        {"time_in_force": "GTD", "gtd": 123456},
    ],
)
def test_market_rejects_explicit_time_constraints_before_rest(options):
    rest = Mock()
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    with pytest.raises(ValueError, match="MARKET"):
        adapter.submit(
            OrderIntent(
                "BTCUSDT", PositionSide.BUY, OrderType.MARKET, quantity=2, **options
            ),
            "mine",
        )
    assert not rest.mock_calls


@pytest.mark.parametrize(
    "order_type,options",
    [
        (OrderType.MARKET, {}),
        (OrderType.LIMIT, {}),
        (OrderType.STOP_LIMIT, {}),
        (OrderType.TAKE_PROFIT_LIMIT, {}),
        (OrderType.TRAILING_STOP_MARKET, {}),
        (OrderType.STOP_MARKET, {"quantity": 2}),
        (OrderType.TAKE_PROFIT_MARKET, {"reduce_only": True}),
    ],
)
def test_close_position_rejects_incompatible_semantics_before_rest(order_type, options):
    rest = Mock()
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))
    with pytest.raises(ValueError, match="close_position"):
        adapter.submit(
            OrderIntent(
                "BTCUSDT",
                PositionSide.SELL,
                order_type,
                price=10,
                close_position=True,
                **options,
            ),
            "mine",
        )
    assert not rest.mock_calls


@pytest.mark.parametrize("algo", [False, True])
@pytest.mark.parametrize("field,value", [("symbol", "ETHUSDT"), ("client_id", "other")])
def test_cancel_rejects_present_identity_mismatch(algo, field, value):
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        CancelAlgoOrderResponse,
        CancelOrderResponse,
    )

    payload = {"algoId" if algo else "orderId": 1}
    payload[
        ("clientAlgoId" if algo else "clientOrderId") if field == "client_id" else field
    ] = value
    model = CancelAlgoOrderResponse if algo else CancelOrderResponse
    rest = Mock()
    getattr(rest, "cancel_algo_order" if algo else "cancel_order").return_value = (
        model.from_dict(payload)
    )
    with pytest.raises(OrderOutcomeUnknown):
        BinanceExchangeAdapter(SimpleNamespace(rest_api=rest)).cancel(
            "BTCUSDT", "mine", algo=algo
        )


def test_cancel_algo_accepts_real_sdk_response_without_symbol():
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        CancelAlgoOrderResponse,
    )

    rest = Mock()
    rest.cancel_algo_order.return_value = CancelAlgoOrderResponse(
        algoId=1, clientAlgoId="mine"
    )
    receipt = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest)).cancel(
        "BTCUSDT", "mine", algo=True
    )
    assert receipt.client_order_id == "mine" and receipt.symbol is None


@pytest.mark.parametrize(
    "order_type", [OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET]
)
def test_close_position_preserves_supported_types(order_type):
    rest = Mock()
    rest.new_algo_order.return_value = {"algoId": 1}
    BinanceExchangeAdapter(SimpleNamespace(rest_api=rest)).submit(
        OrderIntent(
            "BTCUSDT", PositionSide.SELL, order_type, price=10, close_position=True
        ),
        "mine",
    )
    kwargs = rest.new_algo_order.call_args.kwargs
    assert kwargs["close_position"] == "true"
    assert "quantity" not in kwargs and "reduce_only" not in kwargs


@pytest.mark.parametrize(
    "options,expected_tif", [({}, "GTC"), ({"gtd": 123456}, "GTD")]
)
def test_limit_retains_time_in_force_and_expiry(options, expected_tif):
    rest = Mock()
    rest.new_order.return_value = NewOrderResponse(orderId=1)
    BinanceExchangeAdapter(SimpleNamespace(rest_api=rest)).submit(
        OrderIntent(
            "BTCUSDT",
            PositionSide.BUY,
            OrderType.LIMIT,
            price=10,
            quantity=2,
            **options,
        ),
        "mine",
    )
    kwargs = rest.new_order.call_args.kwargs
    assert kwargs["time_in_force"] == expected_tif
    assert kwargs.get("good_till_date") == options.get("gtd")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order_type,explicit_tif",
    [
        (OrderType.MARKET, None),
        (OrderType.LIMIT, None),
        (OrderType.MARKET, "GTC"),
    ],
)
async def test_strategy_open_order_defaults_reach_real_adapter(
    order_type, explicit_tif
):
    from dataclasses import replace
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        NewOrderSideEnum,
        NewOrderTimeInForceEnum,
    )
    from open_binancian_futures.strategy import Strategy

    class Concrete(Strategy):
        def load(self, df):
            return df

        async def run(self, symbol, interval):
            pass

        def run_backtest(self, *args):
            pass

    rest = Mock()
    rest.new_order.return_value = NewOrderResponse(orderId=1)
    adapter = BinanceExchangeAdapter(SimpleNamespace(rest_api=rest))

    class Gateway:
        async def submit_order(self, intent):
            adapter.submit(replace(intent, quantity=2), "mine")
            return True

    strategy = Concrete(None, None, None, None, None, None, None)
    strategy.order_gateway = Gateway()
    args = ["BTCUSDT", NewOrderSideEnum.BUY, order_type, 10]
    if explicit_tif is not None:
        args.append(NewOrderTimeInForceEnum(explicit_tif))
    if order_type == OrderType.MARKET and explicit_tif is not None:
        with pytest.raises(ValueError, match="MARKET"):
            await strategy.open_order(*args)
        assert not rest.mock_calls
    else:
        assert await strategy.open_order(*args)
        kwargs = rest.new_order.call_args.kwargs
        if order_type == OrderType.MARKET:
            assert "time_in_force" not in kwargs
        else:
            assert kwargs["time_in_force"] == "GTC"


@pytest.mark.asyncio
@pytest.mark.parametrize("order_type", [OrderType.MARKET, OrderType.LIMIT])
async def test_unmanaged_open_order_keeps_order_specific_default(order_type):
    from binance_sdk_derivatives_trading_usds_futures.rest_api.models import (
        NewOrderSideEnum,
        NewOrderTimeInForceEnum,
    )
    from open_binancian_futures.execution import ExecutionConfig
    from open_binancian_futures.models import Balance
    from open_binancian_futures.strategy import Strategy

    rest = Mock()
    subject = SimpleNamespace(
        balance=Balance(100),
        execution_config=ExecutionConfig(),
        LOGGER=Mock(),
        _require_exchange_info=lambda: SimpleNamespace(
            to_entry_quantity=lambda **kw: 2
        ),
        _require_client=lambda: SimpleNamespace(rest_api=rest),
    )
    assert await Strategy.open_order(
        subject, "BTCUSDT", NewOrderSideEnum.BUY, order_type, 10
    )
    assert rest.new_order.call_args.kwargs["time_in_force"] == (
        None if order_type == OrderType.MARKET else NewOrderTimeInForceEnum.GTC
    )
