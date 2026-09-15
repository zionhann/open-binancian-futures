import pytest
from open_binancian_futures.live_history import recover_history


def bar(t):
    return [t, '1','2','0.5','1','3',t+59999,0,0,0,0,0]


class History:
    def __init__(self, rows): self.rows=rows; self.calls=[]
    def history(self, symbol, interval, start_time, end_time, limit=1000):
        self.calls.append((start_time,end_time))
        return [r for r in self.rows if start_time <= r[0] <= end_time][:limit]


def test_paginate_fixed_cutoff_and_forming_candle():
    adapter=History([bar(i*60000) for i in range(1502)])
    rows=recover_history(adapter,'BTCUSDT','1m',0,1501*60000)
    assert len(rows)==1500 and rows[-1][0]==1500*60000
    assert len(adapter.calls)==2 and len({c[1] for c in adapter.calls})==1


@pytest.mark.parametrize('rows', [[bar(0)], [bar(60000),bar(180000)], []])
def test_nonprogress_or_gap_blocks_resume(rows):
    adapter=History(rows)
    if rows==[bar(0)]: adapter.history=lambda *a,**kw: rows
    with pytest.raises(ValueError,match='gap|advance'):
        recover_history(adapter,'BTCUSDT','1m',0,240000)
