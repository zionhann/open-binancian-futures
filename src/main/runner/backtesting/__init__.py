import asyncio
import logging
import textwrap
from typing import override

from pandas import Timestamp

from model.balance import Balance
from model.constant import AppConfig, BacktestConfig, Required, OrderType
from model.order import OrderBook, OrderList, Order
from model.position import PositionBook, Position
from openapi import binance_futures as futures
from runner import Runner
from strategy import Strategy
from utils import get_or_raise
from .backtesting_result import BacktestingResult
from .backtesting_summary import BacktestingSummary

LOGGER = logging.getLogger(__name__)


class Backtesting(Runner):
    def __init__(self) -> None:
        self.client = futures.client
        self.balance = Balance(BacktestConfig.BALANCE)
        self.orders = OrderBook()
        self.positions = PositionBook()
        self.indicators = futures.init_indicators(limit=BacktestConfig.KLINES_LIMIT)
        self.strategy = Strategy.of(
            name=Required.STRATEGY,
            client=self.client,
            balance=self.balance,
            orders=self.orders,
            positions=self.positions,
            indicators=self.indicators,
        )
        self.test_results = {s: BacktestingResult(s) for s in AppConfig.SYMBOLS}

    @override
    def run(self) -> None:
        LOGGER.info("Starting backtesting...")
        sample_size = BacktestConfig.KLINES_LIMIT - BacktestConfig.INDICATOR_INIT_SIZE

        for i in range(BacktestConfig.INDICATOR_INIT_SIZE, sample_size):
            for symbol in AppConfig.SYMBOLS:
                klines = self.indicators.get(symbol)[: i + 1]
                current = klines.iloc[-1]
                time, high, low = current["Open_time"], current["High"], current["Low"]

                asyncio.run(self.strategy.run_backtest(symbol, i))
                self._expire_orders(symbol, time)
                self._eval_orders(symbol, high, low, time)

        LOGGER.info("Backtesting finished. Closing remaining positions...")

        for symbol in AppConfig.SYMBOLS:
            self._flush_position(symbol)

        BacktestingSummary(list(self.test_results.values())).print()

    def _expire_orders(self, symbol: str, time: Timestamp) -> None:
        orders = self.orders.get(symbol)

        if (
            order := orders.find_by_type(OrderType.LIMIT)
        ) is not None and order.is_expired(time):
            LOGGER.debug(
                textwrap.dedent(
                    f"""
                        {order.type.value} ORDER EXPIRED @ {time}
                        ID: {order.id}
                        Symbol: {order.symbol}
                        Side: {order.side.value}
                        Price: {order.price}
                        Quantity: {order.quantity}
                        """
                )
            )
            orders.clear()

    def _eval_orders(
        self, symbol: str, high: float, low: float, time: Timestamp
    ) -> None:
        for order in self._filled_orders(symbol, high, low):
            test_result = get_or_raise(
                self.test_results.get(symbol),
                lambda: KeyError(f"Test result not found for symbol: {symbol}"),
            )

            if self.positions.get(symbol).is_empty() and order.is_type(OrderType.LIMIT):
                self._open_position(order, time)
                test_result.increment_hit_count(order.side)

            if (position := self.positions.get(symbol).find_first()) is not None and (
                order.is_type(
                    OrderType.TAKE_PROFIT_MARKET,
                    OrderType.STOP_MARKET,
                    OrderType.MARKET,
                )
            ):
                pnl = self._close_position(position, order, time)
                test_result.increase_pnl(pnl)
                test_result.increment_trade_count(position.side, pnl > 0)

                return

    def _filled_orders(self, symbol: str, high: float, low: float) -> OrderList:
        return OrderList(
            [order for order in self.orders.get(symbol) if order.is_filled(high, low)]
        )

    def _open_position(self, order: Order, time: Timestamp) -> None:
        position = Position(
            symbol=order.symbol,
            amount=order.quantity,
            price=order.price,
            side=order.side,
            leverage=AppConfig.LEVERAGE,
        )
        self.orders.get(order.symbol).remove_by_id(order.id)
        self.positions.get(order.symbol).update_positions([position])
        self.balance.add_pnl(-position.initial_margin())

        LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {position.symbol} @ {position.price}
                ID: {order.id}
                Type: {order.type.value}
                Size: {order.quantity} {position.symbol[:-4]}
                """
            )
        )

    def _close_position(
        self, position: Position, order: Order, time: Timestamp
    ) -> float:
        pnl, margin = position.simple_pnl(order.price), position.initial_margin()
        self.balance.add_pnl(pnl + margin)

        LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {order.symbol} @ {order.price}
                ID: {order.id}
                Type: {order.type.value}
                Realized PNL: {pnl:.2f} USDT ({pnl / margin * 100:.2f}%)
                Balance: {self.balance}
                """
            )
        )

        self.positions.get(order.symbol).clear()
        self.orders.get(order.symbol).clear()

        return pnl

    def _flush_position(self, symbol: str) -> None:
        for position in self.positions.get(symbol):
            margin = position.initial_margin()
            self.balance.add_pnl(margin)

            LOGGER.info(
                textwrap.dedent(
                    f"""
                    CLOSED {position.symbol}
                    Balance: {self.balance}
                    """
                )
            )

        self.positions.get(symbol).clear()
        self.orders.get(symbol).clear()

    @override
    def close(self) -> None:
        return
