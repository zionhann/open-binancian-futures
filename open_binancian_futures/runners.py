import asyncio
import json
import logging
import textwrap
from abc import ABC, abstractmethod
from typing import Optional, Self, override

from pandas import Timestamp

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
    AlgoUpdate,
    AlgoUpdateO,
)
from .models import Balance
from .constants import settings
from .types import (
    AlgoStatus,
    EventType,
    OrderStatus,
    OrderType,
    PositionSide,
)
from .models import Order, OrderBook, OrderEvent, OrderList
from .models import Position, PositionBook
from . import exchange as futures
from .client import client
from .strategy import Strategy, StrategyContext
from .utils import fetch, get_or_raise
from .webhook import Webhook

LOGGER = logging.getLogger(__name__)
MESSAGE = "message"
KEEPALIVE_USER_STREAM_INTERVAL = 60 * 50


class Runner(ABC):
    """
    Abstract base class for trading runners with context manager support.
    """

    @abstractmethod
    def run(self): ...

    @abstractmethod
    def close(self): ...

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class LiveTrading(Runner):
    def __init__(self) -> None:
        self.client = client()
        self.webhook = Webhook.of(settings.webhook_url)
        context = StrategyContext(client=self.client, webhook=self.webhook)
        self.strategy = Strategy.of(name=settings.strategy, context=context)

    def _market_stream_handler(self, stream: KlineCandlestickStreamsResponse) -> None:
        try:
            if stream.e == EventType.KLINE.value:
                data = get_or_raise(stream.k)
                asyncio.create_task(self.strategy.on_new_candlestick(data))
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
                elif event == EventType.ALGO_UPDATE.value:
                    algo_update = get_or_raise(AlgoUpdate.from_dict(stream))
                    self._handle_algo_update(get_or_raise(algo_update.o))
                elif event == EventType.ACCOUNT_UPDATE.value:
                    account_update = get_or_raise(AccountUpdate.from_dict(stream))
                    self._handle_account_update(get_or_raise(account_update.a))
                elif event == EventType.LISTEN_KEY_EXPIRED.value:
                    LOGGER.info("Listen key has expired. Opening a new one...")
                    expired_key = get_or_raise(
                        Listenkeyexpired.from_dict(stream)
                    ).listen_key
                    asyncio.create_task(
                        self._subscribe_to_user_stream(expired_listen_key=expired_key)
                    )
        except Exception as e:
            LOGGER.error(f"An error occurred in the user data handler: {e}")
            self.webhook.send_message(
                f"[ALERT] An error occurred in the user data handler: {e}"
            )

    def _handle_order_trade_update(self, data: OrderTradeUpdateO):
        event = OrderEvent.from_order_trade_update(data)
        curr_order_type = OrderType(get_or_raise(data.o))
        if event.symbol in settings.symbols_list:
            if event.status == OrderStatus.NEW and event.order_type == curr_order_type:
                self.strategy.on_new_order(event)
            elif event.status in [OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED]:
                self.strategy.accumulate_realized_profit(
                    event.symbol, event.realized_profit or 0.0
                )
                if event.status == OrderStatus.FILLED:
                    self.strategy.on_filled_order(event)
            elif event.status == OrderStatus.CANCELED:
                self.strategy.on_cancelled_order(event)
            elif event.status == OrderStatus.EXPIRED:
                self.strategy.on_expired_order(event)

    def _handle_algo_update(self, data: AlgoUpdateO):
        event = OrderEvent.from_algo_update(data)
        if event.symbol not in settings.symbols_list:
            return
        if event.status == AlgoStatus.NEW:
            self.strategy.on_new_order(event)
        if event.status == AlgoStatus.TRIGGERED:
            self.strategy.on_triggered_algo(event)
        elif event.status == AlgoStatus.CANCELED:
            self.strategy.on_cancelled_order(event)
        elif event.status == AlgoStatus.EXPIRED:
            self.strategy.on_expired_order(event)

    def _handle_account_update(self, data: AccountUpdateA):
        if data.P:
            for position_data in data.P:
                self.strategy.on_position_update(position_data)
        if data.B:
            for balance_data in data.B:
                self.strategy.on_balance_update(balance_data)

    @override
    def run(self) -> None:
        LOGGER.info("Starting to run...")
        for s in settings.symbols_list:
            self._set_leverage(symbol=s)
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        self.strategy.lock = asyncio.Lock()
        await self.client.websocket_streams.create_connection()
        await asyncio.gather(
            *[self._subscribe_to_kline_stream(symbol=s) for s in settings.symbols_list],
            self._subscribe_to_user_stream(),
        )
        asyncio.create_task(self._keepalive_user_stream())
        await asyncio.Event().wait()

    def _set_leverage(self, symbol: str):
        fetch(
            self.client.rest_api.change_initial_leverage,
            symbol=symbol,
            leverage=settings.leverage,
        )
        LOGGER.info(f"Set leverage for {symbol} to {settings.leverage}.")

    async def _subscribe_to_kline_stream(self, symbol: str):
        stream = await self.client.websocket_streams.kline_candlestick_streams(
            symbol=symbol, interval=settings.interval
        )
        stream.on(event=MESSAGE, callback=self._market_stream_handler)
        LOGGER.info(f"Subscribed to {symbol} klines by {settings.interval}...")

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

    @override
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


class BacktestingResult:
    """Stores backtesting metrics per symbol using dicts for cleaner aggregation."""

    def __init__(self, symbol: str):
        self._symbol = symbol
        self._hit_count: dict[PositionSide, int] = {
            PositionSide.BUY: 0,
            PositionSide.SELL: 0,
        }
        self._win_count: dict[PositionSide, int] = {
            PositionSide.BUY: 0,
            PositionSide.SELL: 0,
        }
        self._trade_count: dict[PositionSide, int] = {
            PositionSide.BUY: 0,
            PositionSide.SELL: 0,
        }
        self._profit = 0.0
        self._loss = 0.0

    @property
    def trade_count(self) -> int:
        return sum(self._trade_count.values())

    @property
    def hit_count(self) -> int:
        return sum(self._hit_count.values())

    @property
    def win_count(self) -> int:
        return sum(self._win_count.values())

    @property
    def loss_count(self) -> int:
        return self.trade_count - self.win_count

    @property
    def average_win(self) -> float:
        return self._profit / self.win_count if self.win_count else 0.0

    @property
    def average_loss(self) -> float:
        return self._loss / self.loss_count if self.loss_count else 0.0

    def increment_hit_count(self, side: PositionSide) -> None:
        self._hit_count[side] += 1

    def increment_trade_count(self, side: PositionSide, is_win: bool) -> None:
        self._trade_count[side] += 1
        if is_win:
            self._win_count[side] += 1

    def increase_pnl(self, pnl: float) -> None:
        if pnl > 0:
            self._profit += pnl
        else:
            self._loss += pnl

    def print(self):
        hit_rate = round(self.hit_count / settings.sample_size, 2)
        win_rate = round(
            self.win_count / self.trade_count if self.trade_count else 0, 2
        )

        buy_hits = self._hit_count[PositionSide.BUY]
        sell_hits = self._hit_count[PositionSide.SELL]
        buy_rate = round(buy_hits / self.hit_count if self.hit_count else 0, 2)
        sell_rate = round(sell_hits / self.hit_count if self.hit_count else 0, 2)

        buy_trades = self._trade_count[PositionSide.BUY]
        sell_trades = self._trade_count[PositionSide.SELL]
        buy_wins = self._win_count[PositionSide.BUY]
        sell_wins = self._win_count[PositionSide.SELL]
        buy_win_rate = round(buy_wins / buy_trades if buy_trades else 0, 2)
        sell_win_rate = round(sell_wins / sell_trades if sell_trades else 0, 2)

        expectancy = (win_rate * self.average_win) - (
            (1 - win_rate) * self.average_loss
        )
        rr = self.average_win / abs(self.average_loss) if self.average_loss else 0.0
        pf = self._profit / abs(self._loss) if self._loss else 0.0

        LOGGER.info(
            f"==={self._symbol} Backtesting Results===\n"
            f"Hit Rate: {hit_rate*100:.2f}% ({self.hit_count}/{settings.sample_size})\n"
            f"- BUY Hit Rate: {buy_rate*100:.2f}% ({buy_hits}/{self.hit_count})\n"
            f"- SELL Hit Rate: {sell_rate*100:.2f}% ({sell_hits}/{self.hit_count})\n\n"
            f"Win Rate: {win_rate*100:.2f}% ({self.win_count}/{self.trade_count})\n"
            f"- BUY Win Rate: {buy_win_rate*100:.2f}% ({buy_wins}/{buy_trades})\n"
            f"- SELL Win Rate: {sell_win_rate*100:.2f}% ({sell_wins}/{sell_trades})\n\n"
            f"Cumulative PNL: {self._profit+self._loss:.2f} USDT\n"
            f"Expectancy: {expectancy:.2f} USDT\n"
            f"Risk-Reward Ratio: {rr:.2f}\n"
            f"Profit Factor: {pf:.2f}"
        )


class BacktestingSummary:
    def __init__(self, results: list[BacktestingResult]) -> None:
        self._results = results
        self._total_trade_count = 0
        self._total_hit_count = 0
        self._total_win_count = 0
        self._total_profit = 0.0
        self._total_loss = 0.0

    def print(self):
        self._print_details()
        self._print_summary()

    def _print_details(self):
        for result in self._results:
            self._total_trade_count += result.trade_count
            self._total_hit_count += result.hit_count
            self._total_win_count += result.win_count
            self._total_profit += result._profit
            self._total_loss += result._loss
            result.print()

    def _print_summary(self):
        total_hit_rate = (
            self._total_hit_count / settings.sample_size
            if settings.sample_size > 0
            else 0
        )
        total_win_rate = (
            self._total_win_count / self._total_trade_count
            if self._total_trade_count > 0
            else 0
        )
        cumulative_pnl = self._total_profit + self._total_loss
        roi = cumulative_pnl / settings.balance * 100
        total_average_win = (
            self._total_profit / self._total_win_count
            if self._total_win_count > 0
            else 0.0
        )
        total_average_loss = (
            self._total_loss / (self._total_trade_count - self._total_win_count)
            if self._total_trade_count > 0
            else 0.0
        )
        expectancy = (total_win_rate * total_average_win) - (
            (1 - total_win_rate) * total_average_loss
        )
        rr = total_average_win / abs(total_average_loss) if total_average_loss else 0.0
        pf = self._total_profit / abs(self._total_loss) if self._total_loss else 0.0
        LOGGER.info(
            textwrap.dedent(
                f"""
                ===Overall Results===
                Hit Rate: {total_hit_rate*100:.2f}%
                Win Rate: {total_win_rate*100:.2f}%
                Cumulative PNL: {cumulative_pnl:.2f} USDT ({roi:.2f}%)
                
                Expectancy: {expectancy:.2f} USDT
                Risk-Reward Ratio: {rr:.2f}
                Profit Factor: {pf:.2f}
                """
            )
        )


class Backtesting(Runner):
    def __init__(self) -> None:
        self.client = client()
        self.balance = Balance(settings.balance)
        self.orders = OrderBook()
        self.positions = PositionBook()
        self.indicators = futures.init_indicators(limit=settings.klines_limit)
        context = StrategyContext(
            client=self.client,
            balance=self.balance,
            orders=self.orders,
            positions=self.positions,
            indicators=self.indicators,
        )
        self.strategy = Strategy.of(name=settings.strategy, context=context)
        self.test_results = {s: BacktestingResult(s) for s in settings.symbols_list}

    @override
    def run(self) -> None:
        LOGGER.info("Starting backtesting...")
        asyncio.run(self._run_backtest_loop())

    async def _run_backtest_loop(self) -> None:
        # Use actual data length instead of klines_limit to avoid IndexError
        # (init_indicators removes last incomplete candle with [:-1])
        actual_length = min(len(self.indicators[s]) for s in settings.symbols_list)
        for i in range(settings.indicator_init_size, actual_length):
            for symbol in settings.symbols_list:
                klines = self.indicators[symbol][: i + 1]
                current = klines.iloc[-1]
                time, high, low = current["Open_time"], current["High"], current["Low"]
                await self.strategy.run_backtest(symbol, i)
                self._expire_orders(symbol, time)
                self._eval_orders(symbol, high, low, time)
        LOGGER.info("Backtesting finished. Closing remaining positions...")
        for symbol in settings.symbols_list:
            self._flush_position(symbol)
        BacktestingSummary(list(self.test_results.values())).print()

    def _expire_orders(self, symbol: str, time: Timestamp) -> None:
        orders = self.orders[symbol]
        if (
            order := orders.find_by_type(OrderType.LIMIT)
        ) is not None and order.is_expired(time):
            LOGGER.debug(
                textwrap.dedent(
                    f"""
                        {order.type.value} ORDER EXPIRED @ {time}
                        ID: {order.order_id}
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
            if not self.positions[symbol] and order.is_type(OrderType.LIMIT):
                self._open_position(order, time)
                test_result.increment_hit_count(order.side)
            if (position := self.positions[symbol].find_first()) is not None and (
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
            [order for order in self.orders[symbol] if order.is_filled(high, low)]
        )

    def _open_position(self, order: Order, time: Timestamp) -> None:
        position = Position(
            symbol=order.symbol,
            amount=order.quantity,
            price=order.price,
            side=order.side,
            leverage=settings.leverage,
        )
        self.orders[order.symbol].remove_by_id(order.order_id)
        self.positions[order.symbol].update_positions([position])
        self.balance.add_pnl(-position.initial_margin())
        LOGGER.info(
            textwrap.dedent(
                f"""
                Date: {time.strftime("%Y-%m-%d %H:%M:%S")}
                {order.side.value} {position.symbol} @ {position.price}
                ID: {order.order_id}
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
                ID: {order.order_id}
                Type: {order.type.value}
                Realized PNL: {pnl:.2f} USDT ({pnl / margin * 100:.2f}%)
                Balance: {self.balance}
                """
            )
        )
        self.positions[order.symbol].clear()
        self.orders[order.symbol].clear()
        return pnl

    def _flush_position(self, symbol: str) -> None:
        for position in self.positions[symbol]:
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
        self.positions[symbol].clear()
        self.orders[symbol].clear()

    @override
    def close(self) -> None:
        return
