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
    from open_binancian_futures import runners
    from open_binancian_futures.models import (
        Balance,
        ExchangeInfo,
        OrderBook,
        PositionBook,
        Indicator,
    )
    from open_binancian_futures.exchange_adapter import ExchangeSnapshot

    monkeypatch.setattr(
        runners, "client", Mock(side_effect=AssertionError("global client used"))
    )
    adapter = SimpleNamespace(
        snapshot=Mock(
            return_value=ExchangeSnapshot(
                ExchangeInfo([]), Balance(100), OrderBook(), PositionBook(), {}
            )
        ),
        initial_indicators=Mock(return_value=Indicator()),
    )
    build = Mock(return_value=object())
    monkeypatch.setattr(runners.Strategy, "of", build)
    gateway = object()
    runner = runners.LiveTrading(adapter=adapter, order_gateway=gateway)
    context = build.call_args.kwargs["context"]
    assert context.client is None and context.order_gateway is gateway
    assert context.preserve_position_leverage
    assert runner.orders is adapter.snapshot.return_value.orders
    adapter.snapshot.assert_called_once_with(runner.symbols, runner.execution_config)
    adapter.initial_indicators.assert_called_once_with(
        runner.symbols, runner.intervals, runner.execution_config.timezone
    )
    with pytest.raises(RuntimeError, match="supervisor"):
        runner.run()
    runner.close()
