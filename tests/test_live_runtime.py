import asyncio
import copy
from types import SimpleNamespace

import pandas as pd
import pytest

from open_binancian_futures.exchange_adapter import ExchangeSnapshot, OrderOutcomeUnknown, OrderReceipt, OrderRejected
from open_binancian_futures.execution import ExecutionConfig
from open_binancian_futures.live import LiveTrading
from open_binancian_futures.live_journal import OrderJournal
from open_binancian_futures.managed_orders import ManagedOrderGateway
from open_binancian_futures.models import Balance, ExchangeInfo, Filter, Indicator, Order, OrderBook, OrderIntent, Position, PositionBook
from open_binancian_futures.types import OrderType, PositionSide

SYMBOL = 'BTCUSDT'
INTENT = OrderIntent(SYMBOL, PositionSide.BUY, OrderType.LIMIT, 100, 1)


class Adapter:
    def __init__(self):
        info=ExchangeInfo([])
        info.filters[SYMBOL]=Filter(tick_size=0.1,step_size=0.001,min_notional=5)
        self.state=ExchangeSnapshot(info,Balance(100),OrderBook(symbols=[SYMBOL]),PositionBook(symbols=[SYMBOL]),{SYMBOL:10})
        self.mutations=[]; self.calls=[]; self.receipts={}; self.submit_error=None; self.hedge=False; self.cutoff=60000
        self.snapshot_hook=None; self.query_unknown=False; self.mode='oneway'
    def identity(self): return 'test-identity'
    def account_mode(self): self.calls.append('mode'); return self.hedge
    def snapshot(self,*args):
        self.calls.append('snapshot')
        if self.snapshot_hook: self.snapshot_hook()
        return copy.deepcopy(self.state)
    def initial_indicators(self,*args):
        frame=pd.DataFrame({'Open_time':[pd.Timestamp(0,unit='ms',tz='UTC')],'Symbol':[SYMBOL],'Open':[100.],'High':[101.],'Low':[99.],'Close':[100.],'Volume':[1.]},index=pd.DatetimeIndex([pd.Timestamp(0,unit='ms',tz='UTC')]))
        return Indicator({SYMBOL:{'1m':frame}})
    def history(self,*args,**kwargs): return []
    def server_time(self): return self.cutoff
    def start_listen_key(self): self.mutations.append('listen-start'); return 'test-key'
    def keepalive_listen_key(self,key): self.mutations.append('keepalive')
    def close_listen_key(self,key): self.mutations.append('listen-close')
    def leverage(self,symbol): return self.state.leverage[symbol]
    def set_leverage(self,symbol,value): self.mutations.append(('leverage',value)); self.state.leverage[symbol]=value
    def submit(self,intent,identifier):
        self.mutations.append(('submit',intent,identifier))
        self.receipts[identifier]=OrderReceipt(1,identifier,intent.symbol,'NEW',0,None,{})
        self.state.orders[SYMBOL].add(Order(SYMBOL,1,intent.order_type,intent.side,intent.price,intent.quantity or 0,reduce_only=intent.reduce_only))
        if self.submit_error: raise self.submit_error
        return self.receipts[identifier]
    def query(self,symbol,identifier,**kwargs):
        self.calls.append('query')
        if self.query_unknown or identifier not in self.receipts: raise OrderOutcomeUnknown('not found')
        return self.receipts[identifier]
    def cancel(self,*args,**kwargs): self.mutations.append('cancel'); raise OrderOutcomeUnknown('timeout')


class Streams:
    def __init__(self): self.recovery=asyncio.Event(); self.callbacks=[]; self.closed=False
    async def connect(self): pass
    async def subscribe_user(self,key,callback): self.callbacks.append(callback)
    async def subscribe_klines(self,symbol,interval,callback): self.callbacks.append(callback)
    async def close(self): self.closed=True
    def emit(self,data): self.callbacks[-1](data)


class TestStrategy:
    __test__=False
    def __init__(self): self.calls=[]; self.pnl=0; self.fail=False; self.trade=False
    async def run(self,symbol,interval):
        self.calls.append((symbol,interval))
        if self.fail: raise ValueError('broken strategy')
        if self.trade: await self.order_gateway.submit_order(INTENT)
    def accumulate_realized_profit(self,symbol,value): self.pnl+=value


def candle(t=60000):
    return {'e':'kline','s':SYMBOL,'k':{'s':SYMBOL,'i':'1m','t':t,'T':t+59999,'x':True,'o':'100','h':'101','l':'99','c':'100','v':'1'}}


async def eventually(predicate):
    for _ in range(500):
        if predicate(): return
        await asyncio.sleep(0.001)
    raise AssertionError('condition never reached')


def runtime(tmp_path,adapter=None,strategy=None,**kwargs):
    generations=[]
    def factory():
        stream=Streams(); generations.append(stream); return stream
    runner=LiveTrading(adapter or Adapter(),streams_factory=factory,strategy=strategy or TestStrategy(),symbols=[SYMBOL],intervals=['1m'],journal_path=tmp_path/'orders.sqlite3',webhook=SimpleNamespace(send_message=lambda message:None),**kwargs)
    return runner,generations


def test_constructor_no_remote_calls(tmp_path):
    adapter=Adapter(); runner,_=runtime(tmp_path,adapter)
    assert not adapter.calls and not adapter.mutations and not runner.journal_path.exists()
    runner.close()


@pytest.mark.asyncio
async def test_injected_fullrun_default_gateway_stop_preserves_orders(tmp_path):
    adapter=Adapter(); strategy=TestStrategy(); strategy.trade=True
    runner,generations=runtime(tmp_path,adapter,strategy)
    task=asyncio.create_task(runner.run_async())
    await eventually(lambda:runner.active)
    generations[-1].emit(candle())
    await eventually(lambda:any(isinstance(m,tuple) and m[0]=='submit' for m in adapter.mutations))
    runner.close(); await task; await runner.aclose()
    assert strategy.calls==[(SYMBOL,'1m')]
    assert 'cancel' not in adapter.mutations and len(adapter.state.orders[SYMBOL])==1
    assert generations[0].closed and not runner.tasks


@pytest.mark.asyncio
async def test_hedge_and_second_instance_fail_before_mutations(tmp_path):
    adapter=Adapter(); adapter.hedge=True
    runner,_=runtime(tmp_path,adapter)
    with pytest.raises(ValueError,match='Hedge'): await runner.run_async()
    assert adapter.mutations==[]
    first,_=runtime(tmp_path); task=asyncio.create_task(first.run_async())
    await eventually(lambda:first.active)
    second_adapter=Adapter(); second,_=runtime(tmp_path,second_adapter)
    with pytest.raises(RuntimeError,match='already running'): await second.run_async()
    assert second_adapter.mutations==[]
    first.close(); await task


@pytest.mark.asyncio
async def test_strategy_failure_latched_across_two_recoveries(tmp_path):
    strategy=TestStrategy(); strategy.fail=True
    runner,streams=runtime(tmp_path,strategy=strategy)
    task=asyncio.create_task(runner.run_async()); await eventually(lambda:runner.active)
    streams[-1].emit(candle()); await eventually(lambda:runner.failed)
    for count in (2,3):
        runner.recovery.set()
        await eventually(lambda:len(streams)==count)
        await eventually(lambda:not runner.recovery.is_set())
        assert not runner.active and runner.gateway.failed
    streams[-1].emit(candle(120000)); await asyncio.sleep(.01)
    assert len(strategy.calls)==1
    runner.close(); await task
    assert all(stream.closed for stream in streams)


@pytest.mark.asyncio
async def test_snapshot_authority_duplicate_partial_fills_and_stale_new(tmp_path):
    adapter=Adapter(); strategy=TestStrategy(); runner,streams=runtime(tmp_path,adapter,strategy)
    task=asyncio.create_task(runner.run_async()); await eventually(lambda:runner.active)
    event={'e':'ORDER_TRADE_UPDATE','T':10,'o':{'s':SYMBOL,'i':7,'t':11,'z':'1','rp':'2','X':'PARTIALLY_FILLED'}}
    streams[-1].emit(event); streams[-1].emit(event)
    streams[-1].emit({'e':'ACCOUNT_UPDATE','a':{'B':[{'a':'USDT','cw':'99999'}]}})
    filled={'e':'ORDER_TRADE_UPDATE','T':20,'o':{'s':SYMBOL,'i':7,'t':12,'z':'2','rp':'3','X':'FILLED'}}
    streams[-1].emit(filled)
    streams[-1].emit({'e':'ORDER_TRADE_UPDATE','T':5,'o':{'s':SYMBOL,'i':7,'t':-1,'z':'0','rp':'0','X':'NEW'}})
    await eventually(lambda:strategy.pnl==5)
    assert runner.balance.available==100 and not list(runner.orders[SYMBOL])
    calls=len(adapter.calls)
    streams[-1].emit({'e':'ORDER_TRADE_UPDATE','o':{'s':'ETHUSDT','i':1}})
    await asyncio.sleep(.01); assert len(adapter.calls)==calls
    runner.close(); await task


def gateway(tmp_path,adapter,config=ExecutionConfig(leverage=10)):
    journal=OrderJournal(tmp_path/'gateway.sqlite3','test-identity');journal.open()
    gateway=ManagedOrderGateway(adapter,journal,[SYMBOL],config,lambda message:None)
    gateway.reconcile();gateway.active=True
    return gateway,journal


@pytest.mark.asyncio
async def test_accepted_timeout_restart_query_no_resend(tmp_path):
    adapter=Adapter(); adapter.submit_error=TimeoutError('accepted but response lost')
    first,journal=gateway(tmp_path,adapter)
    with pytest.raises(OrderOutcomeUnknown): await first.submit_order(INTENT)
    identifier=journal.pending()[0].client_order_id
    assert first.snapshot().balance.reserved_margin==10 and SYMBOL in first.blocked
    with pytest.raises(OrderOutcomeUnknown): await first.submit_order(INTENT)
    journal.close()
    adapter.query_unknown=True
    restored,journal=gateway(tmp_path,adapter)
    assert SYMBOL in restored.blocked and restored.snapshot().balance.reserved_margin==10
    adapter.query_unknown=False;restored.reconcile()
    assert SYMBOL not in restored.blocked and restored.snapshot().balance.reserved_margin==0
    assert len([m for m in adapter.mutations if isinstance(m,tuple) and m[0]=='submit'])==1
    assert journal.pending()[0].client_order_id==identifier
    journal.close()


@pytest.mark.asyncio
async def test_rejection_rollback_once_and_storage_failure_no_send(tmp_path):
    adapter=Adapter(); managed,journal=gateway(tmp_path,adapter)
    adapter.submit_error=OrderRejected('margin')
    assert not await managed.submit_order(INTENT)
    assert managed.snapshot().balance.available==100 and managed.snapshot().balance.reserved_margin==0
    sent=len(adapter.mutations)
    journal.connection.execute('PRAGMA query_only=ON')
    with pytest.raises(Exception): await managed.submit_order(INTENT)
    assert len(adapter.mutations)==sent
    journal.close()


@pytest.mark.asyncio
async def test_actual_leverage_additions_then_flat_transition_after_old_orders(tmp_path):
    adapter=Adapter();adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL,1,100,PositionSide.BUY,leverage=10)])
    managed,journal=gateway(tmp_path,adapter,ExecutionConfig(leverage=20))
    await managed.submit_order(INTENT)
    assert ('leverage',20) not in adapter.mutations
    adapter.state.positions[SYMBOL].clear(); managed.reconcile()
    with pytest.raises(OrderOutcomeUnknown,match='entry orders'): await managed.submit_order(INTENT)
    adapter.state.orders[SYMBOL].clear(); managed.reconcile()
    await managed.submit_order(INTENT)
    assert ('leverage',20) in adapter.mutations and managed.effective_leverage(SYMBOL)==20
    journal.close()

@pytest.mark.asyncio
async def test_snapshot_buffers_old_events_without_overwriting_new_state(tmp_path):
    adapter=Adapter(); runner,streams=runtime(tmp_path,adapter)
    injected=False
    def during_snapshot():
        nonlocal injected
        if not injected:
            injected=True
            streams[-1].emit({'e':'ACCOUNT_UPDATE','a':{'B':[{'a':'USDT','cw':'999999'}],'P':[{'s':SYMBOL,'pa':'99','ep':'1'}]}})
    adapter.snapshot_hook=during_snapshot
    adapter.state.balance=Balance(42)
    task=asyncio.create_task(runner.run_async()); await eventually(lambda:runner.active)
    await eventually(lambda:adapter.calls.count('snapshot')>=2)
    assert runner.balance.available==42 and runner.positions[SYMBOL].find_first() is None
    runner.close(); await task


@pytest.mark.asyncio
async def test_response_rejected_rolls_back_once(tmp_path):
    adapter=Adapter(); managed,journal=gateway(tmp_path,adapter)
    adapter.submit=lambda intent,identifier:OrderReceipt(3,identifier,SYMBOL,'REJECTED',0,None,{})
    assert not await managed.submit_order(INTENT)
    assert managed.snapshot().balance.available==100
    managed.reconcile()
    assert managed.snapshot().balance.available==100 and not journal.pending()
    journal.close()


@pytest.mark.asyncio
async def test_leverage_confirmation_failure_never_sends(tmp_path):
    adapter=Adapter(); managed,journal=gateway(tmp_path,adapter,ExecutionConfig(leverage=20))
    adapter.set_leverage=lambda symbol,value:adapter.mutations.append(('leverage-attempt',value))
    with pytest.raises(OrderOutcomeUnknown,match='not confirmed'): await managed.submit_order(INTENT)
    assert not journal.pending() and adapter.mutations==[('leverage-attempt',20)]
    journal.close()


@pytest.mark.asyncio
async def test_cancel_timeout_is_not_repeated_after_requery(tmp_path):
    adapter=Adapter();managed,journal=gateway(tmp_path,adapter)
    await managed.submit_order(INTENT); identifier=journal.pending()[0].client_order_id
    with pytest.raises(OrderOutcomeUnknown): await managed.cancel_order(identifier)
    managed.reconcile()
    assert SYMBOL in managed.blocked
    with pytest.raises(OrderOutcomeUnknown): await managed.cancel_order(identifier)
    assert adapter.mutations.count('cancel')==1
    journal.close()


@pytest.mark.asyncio
async def test_initial_failure_retry_and_shutdown_interrupts_delay(tmp_path):
    adapter=Adapter();runner,streams=runtime(tmp_path,adapter)
    sleeping=asyncio.Event();delays=[]
    async def sleep(delay): delays.append(delay);sleeping.set();await asyncio.Event().wait()
    runner.sleep=sleep
    adapter.start_listen_key=lambda: (_ for _ in ()).throw(ConnectionError('key unavailable'))
    task=asyncio.create_task(runner.run_async());await sleeping.wait()
    runner.close();await asyncio.wait_for(task,1)
    assert delays and 0.8<=delays[0]<=1 and streams[0].closed and runner.closed


@pytest.mark.asyncio
async def test_recovery_backfills_indicators_and_only_calls_next_new_bar(tmp_path):
    adapter=Adapter(); strategy=TestStrategy(); runner,streams=runtime(tmp_path,adapter,strategy)
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    adapter.cutoff=180000
    adapter.history=lambda symbol,interval,start,end,limit=1000:[[t,'100','101','99','100','1',t+59999,0,0,0,0,0] for t in (60000,120000) if t>=start]
    runner.recovery.set();await eventually(lambda:len(streams)==2 and runner.active)
    assert len(runner.indicators[SYMBOL]['1m'])==3 and strategy.calls==[]
    streams[-1].emit(candle(120000));streams[-1].emit(candle(180000));streams[-1].emit(candle(180000))
    await eventually(lambda:len(strategy.calls)==1)
    assert len(runner.indicators[SYMBOL]['1m'])==4
    runner.close();await task


@pytest.mark.asyncio
async def test_recovery_load_exception_latches_strategy_failure(tmp_path):
    adapter=Adapter();strategy=TestStrategy();runner,streams=runtime(tmp_path,adapter,strategy)
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    strategy.add_indicators=lambda frames:(_ for _ in ()).throw(ValueError('bad indicator'))
    runner.recovery.set();await eventually(lambda:runner.failed)
    assert not runner.active and runner.gateway.failed
    runner.close();await task


@pytest.mark.asyncio
async def test_malformed_market_does_not_kill_event_worker(tmp_path):
    runner,streams=runtime(tmp_path)
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    streams[-1].emit({'e':'kline','k':{'s':SYMBOL,'i':'1m','x':True}})
    await eventually(lambda:len(streams)==2)
    assert all(not owned.done() for owned in runner.tasks)
    runner.close();await task


@pytest.mark.asyncio
async def test_real_strategy_factory_receives_protected_complete_context(tmp_path,monkeypatch):
    from open_binancian_futures.strategy import Strategy
    from open_binancian_futures.constants import settings
    seen=[]
    class ActualStrategy(Strategy):
        def __init__(self,**kwargs):
            seen.append((kwargs['client'],kwargs['order_gateway'].active,kwargs['positions'],kwargs['balance']))
            super().__init__(**kwargs)
        def load(self,frame): return frame
        async def run(self,symbol,interval): await self.submit_order(INTENT)
        async def run_backtest(self,*args): pass
    monkeypatch.setattr(Strategy,'_import_strategy',staticmethod(lambda name:ActualStrategy))
    monkeypatch.setattr(settings,'strategy','test')
    runner,streams=runtime(tmp_path);runner.strategy=None
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    assert seen[0][0] is None and seen[0][1] is False and seen[0][2] is not None and seen[0][3] is not None
    streams[-1].emit(candle());await eventually(lambda:bool(runner.adapter.receipts))
    runner.close();await task


@pytest.mark.asyncio
async def test_unknown_order_is_not_strategy_failure(tmp_path):
    adapter=Adapter();adapter.submit_error=TimeoutError();strategy=TestStrategy();strategy.trade=True
    runner,streams=runtime(tmp_path,adapter,strategy)
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    streams[-1].emit(candle());await eventually(lambda:SYMBOL in runner.gateway.blocked)
    assert not runner.failed and runner.active
    runner.close();await task


@pytest.mark.asyncio
async def test_cancellation_of_run_releases_all_owned_tasks_and_lock(tmp_path):
    runner,streams=runtime(tmp_path);task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert runner.closed and not runner.tasks and streams[-1].closed
    journal=OrderJournal(runner.journal_path,'test-identity');journal.open();journal.close()

@pytest.mark.asyncio
async def test_fractional_filter_rounding_matches_exact_transport_step(tmp_path):
    adapter=Adapter();adapter.state.exchange_info.filters[SYMBOL]=Filter(tick_size=.05,step_size=.1,min_notional=5)
    managed,journal=gateway(tmp_path,adapter)
    await managed.submit_order(OrderIntent(SYMBOL,PositionSide.BUY,OrderType.LIMIT,100.027,.3))
    sent=[m[1] for m in adapter.mutations if isinstance(m,tuple) and m[0]=='submit'][0]
    assert sent.quantity==.3 and str(sent.quantity)=='0.3' and sent.price==100.05
    journal.close()


@pytest.mark.asyncio
async def test_order_hooks_exactly_once_after_snapshot_and_cannot_reopen(tmp_path):
    strategy=TestStrategy();hooks=[]
    def filled(event): hooks.append(('filled',len(list(strategy.orders[SYMBOL]))))
    strategy.on_filled_order=filled
    strategy.on_new_order=lambda event:hooks.append(('new',event.order_id))
    runner,streams=runtime(tmp_path,strategy=strategy);task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    event={'e':'ORDER_TRADE_UPDATE','T':20,'o':{'s':SYMBOL,'i':7,'t':12,'z':'2','rp':'3','X':'FILLED','o':'LIMIT','S':'BUY','q':'2','p':'100'}}
    streams[-1].emit(event);streams[-1].emit(event)
    streams[-1].emit({'e':'ORDER_TRADE_UPDATE','T':5,'o':{'s':SYMBOL,'i':7,'t':11,'z':'1','rp':'2','X':'PARTIALLY_FILLED'}})
    streams[-1].emit({'e':'ORDER_TRADE_UPDATE','T':1,'o':{'s':SYMBOL,'i':7,'t':-1,'z':'0','X':'NEW'}})
    await eventually(lambda:strategy.pnl==5)
    assert hooks==[('filled',0)] and not list(runner.orders[SYMBOL])
    runner.close();await task

@pytest.mark.asyncio
async def test_async_fill_hook_can_submit_managed_protection(tmp_path):
    strategy=TestStrategy();runner,streams=runtime(tmp_path,strategy=strategy)
    async def on_filled(event):
        await strategy.order_gateway.submit_order(OrderIntent(SYMBOL,PositionSide.SELL,OrderType.STOP_MARKET,90,close_position=True))
    strategy.on_filled_order=on_filled
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    streams[-1].emit({'e':'ORDER_TRADE_UPDATE','T':20,'o':{'s':SYMBOL,'i':7,'t':12,'z':'1','rp':'0','X':'FILLED','o':'LIMIT','S':'BUY','q':'1','p':'100'}})
    await eventually(lambda:bool(runner.adapter.receipts))
    sent=[m[1] for m in runner.adapter.mutations if isinstance(m,tuple) and m[0]=='submit'][0]
    assert sent.close_position and sent.order_type==OrderType.STOP_MARKET and not runner.failed
    runner.close();await task


@pytest.mark.asyncio
async def test_default_constructor_uses_same_supervised_run(tmp_path,monkeypatch):
    from open_binancian_futures import live
    adapter=Adapter();stream=Streams();strategy=TestStrategy()
    sdk=SimpleNamespace(websocket_streams=SimpleNamespace(configuration='config'))
    monkeypatch.setattr(live,'client',lambda:sdk)
    monkeypatch.setattr(live,'BinanceExchangeAdapter',lambda supplied:adapter)
    monkeypatch.setattr(live,'BinanceStreams',lambda config:stream)
    runner=live.LiveTrading(strategy=strategy,symbols=[SYMBOL],intervals=['1m'],journal_path=tmp_path/'default.sqlite3')
    assert adapter.calls==[] and adapter.mutations==[]
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    stream.emit(candle());await eventually(lambda:len(strategy.calls)==1)
    assert isinstance(runner.gateway,ManagedOrderGateway) and strategy.client is sdk
    runner.close();await task


@pytest.mark.asyncio
async def test_listen_expiry_reconnects_and_stale_callbacks_ignored(tmp_path):
    runner,streams=runtime(tmp_path);task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    streams[0].emit({'e':'listenKeyExpired'})
    assert not runner.active
    await eventually(lambda:len(streams)==2 and runner.active)
    streams[0].emit(candle());await asyncio.sleep(.01)
    assert runner.strategy.calls==[]
    streams[1].emit(candle());await eventually(lambda:len(runner.strategy.calls)==1)
    runner.close();await task


@pytest.mark.asyncio
async def test_keepalive_failure_and_receiver_signal_trigger_recovery(tmp_path):
    now=[0.0]
    runner,streams=runtime(tmp_path,clock=lambda:now[0])
    async def tick(delay): await asyncio.sleep(.005)
    runner.sleep=tick
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    await asyncio.sleep(.01)
    now[0]=3001;runner._last_market=3001
    runner.adapter.keepalive_listen_key=lambda key:(_ for _ in ()).throw(ConnectionError('key lost'))
    await eventually(lambda:len(streams)>=2)
    runner.adapter.keepalive_listen_key=lambda key:None
    count=len(streams);streams[-1].recovery.set()
    await eventually(lambda:len(streams)>count)
    runner.close();await task
    assert all(stream.closed for stream in streams)


def test_notifications_deduplicate_repeated_failures(tmp_path):
    messages=[];runner,_=runtime(tmp_path)
    runner.webhook=SimpleNamespace(send_message=messages.append)
    for _ in range(10): runner.report('Recovery pending; retrying')
    runner.report('Connection recovered');runner.report('Strategy failed')
    assert messages==['Recovery pending; retrying','Connection recovered','Strategy failed']

@pytest.mark.asyncio
async def test_disconnect_blocks_gateway_before_watchdog_tick(tmp_path):
    runner,streams=runtime(tmp_path);task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    streams[-1].recovery.set()
    with pytest.raises(OrderOutcomeUnknown,match='paused'): await runner.gateway.submit_order(INTENT)
    assert not runner.adapter.receipts
    runner.close();await task


@pytest.mark.asyncio
async def test_constructor_exception_not_retried_on_network_recovery(tmp_path,monkeypatch):
    from open_binancian_futures.strategy import Strategy
    attempts=[]
    def broken(name,context): attempts.append(1);raise ValueError('constructor failure')
    monkeypatch.setattr(Strategy,'of',staticmethod(broken))
    runner,streams=runtime(tmp_path);runner.strategy=None
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.failed)
    runner.recovery.set();await eventually(lambda:len(streams)==2)
    assert len(attempts)==1 and not runner.active
    runner.close();await task

@pytest.mark.asyncio
async def test_notifications_repeat_for_distinct_recovery_incidents(tmp_path):
    messages=[];runner,streams=runtime(tmp_path)
    runner.webhook=SimpleNamespace(send_message=messages.append)
    task=asyncio.create_task(runner.run_async());await eventually(lambda:runner.active)
    for count in (2,3):
        runner.recovery.set()
        await eventually(lambda:len(streams)==count and runner.active)
    assert messages.count('Connection interrupted; strategy paused')==2
    assert messages.count('Connection recovered; state synchronized')==2
    runner.close();await task

@pytest.mark.asyncio
async def test_actual_leverage_drives_auto_size_and_stop_distance(tmp_path):
    from open_binancian_futures.strategy import Strategy
    adapter=Adapter();adapter.state.positions[SYMBOL].update_positions([Position(SYMBOL,1,100,PositionSide.BUY,leverage=10)])
    managed,journal=gateway(tmp_path,adapter,ExecutionConfig(leverage=20,position_size=.1))
    await managed.submit_order(OrderIntent(SYMBOL,PositionSide.BUY,OrderType.LIMIT,100))
    sent=[m[1] for m in adapter.mutations if isinstance(m,tuple) and m[0]=='submit'][0]
    assert sent.quantity==1  # Actual 10x; requested 20x would produce 2.
    subject=SimpleNamespace(effective_leverage=managed.effective_leverage,_require_exchange_info=lambda:adapter.state.exchange_info)
    assert Strategy.calculate_stop_price(subject,SYMBOL,100,OrderType.STOP_MARKET,PositionSide.SELL,.1)==99
    journal.close()
