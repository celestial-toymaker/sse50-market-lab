import json
import numpy as np
import pytest
from hypothesis import given,strategies as st
from sse50_lab._core import Exchange,PaperEngine,Replay
from conftest import BASE,event

def array(rows):return np.asarray(rows,dtype=np.int64).reshape(-1,12)
def parse(s):return json.loads(s)

def test_fifo_partial_cancel():
    x=Exchange();x.seed(1,-1,1000,300);x.seed(2,-1,1000,400)
    a=parse(x.submit(1,1000,500));assert [(f['passive_id'],f['qty']) for f in a['fills']]==[(1,300),(2,200)]
    assert parse(x.snapshot())['book']['asks']==[[1000,200,1]]
    b=parse(x.submit(1,999,400));assert b['status']=='OPEN'
    assert parse(x.cancel(b['id'],100))['canceled']==100
    assert parse(x.snapshot())['book']['bids'][0][1]==300

def test_five_levels_protection_and_conversion():
    x=Exchange()
    for i in range(7):x.seed(i+1,-1,1000+i,100)
    a=parse(x.submit(1,1010,700,'BEST5_CANCEL'));assert sum(f['qty'] for f in a['fills'])==500;assert a['status']=='CANCELED'
    b=parse(x.submit(1,1005,300,'BEST5_LIMIT'));assert sum(f['qty'] for f in b['fills'])==100;assert b['price']==1005
    assert parse(x.snapshot())['book']['bids'][0][:2]==[1005,200]

def test_market_best_and_empty_side():
    x=Exchange();assert parse(x.submit(1,1000,100,'OWN_BEST'))['status']=='CANCELED'
    x.seed(1,1,998,100);a=parse(x.submit(1,1000,100,'OWN_BEST'));assert a['price']==998
    x.seed(2,-1,1000,100);a=parse(x.submit(1,1000,100,'OPPOSITE_BEST'));assert a['status']=='FILLED'

def test_auction_uniform_price_and_fifo():
    x=Exchange();x.set_phase('OPEN_AUCTION',0);x.seed(1,1,1002,500);x.seed(2,1,1002,100);x.seed(3,-1,1000,300)
    a=parse(x.auction());assert a['price']==1002 and a['volume']==300 and a['fills'][0]['bid']==1

def test_after_hours_price():
    x=Exchange();x.set_phase('AFTER_HOURS',1000);x.seed(1,-1,1000,300)
    a=parse(x.submit(1,1005,200,'CLOSE_PRICE'));assert a['fills'][0]['price']==1000
    with pytest.raises(ValueError):x.submit(1,999,200,'CLOSE_PRICE')

@given(st.lists(st.integers(min_value=1,max_value=10000),min_size=1,max_size=25),st.integers(min_value=1,max_value=100000))
def test_volume_conservation(quantities,take):
    x=Exchange()
    for i,q in enumerate(quantities):x.seed(i+1,-1,1000,q)
    r=parse(x.submit(1,1000,take,'BEST5_CANCEL'));remaining=sum(level[1] for level in parse(x.snapshot())['book']['asks'])
    assert sum(f['qty'] for f in r['fills'])+remaining==sum(quantities)

def test_trade_then_residual_add():
    a=array([event(0,0,-1,1000,300,1,biz=1),event(10,2,1,1000,200,0,bid=2,ask=1,biz=2),event(10,0,1,1000,100,2,biz=3)])
    s=parse(Replay(a).advance_to(BASE+20));assert s['stats']['missing_refs']==0;assert s['book']['bids'][0][1]==100;assert s['book']['asks'][0][1]==100

def test_checkpoint_roundtrip_and_future_independence():
    a=array([event(0,0,1,999,500,1,biz=1),event(100,0,-1,1000,200,2,biz=2),event(200,1,1,999,100,1,biz=3)])
    r=Replay(a);r.advance_to(BASE+100);cp=r.checkpoint();r.advance_to(BASE+300);s=r.snapshot()
    v=Replay(a);v.restore(cp);assert v.advance_to(BASE+300)==s
    b=a.copy();b[-1,6]=400;assert Replay(a).advance_to(BASE+100)==Replay(b).advance_to(BASE+100)

def test_cancel_ahead_and_partial_passive():
    a=array([event(0,0,1,1000,300,1,biz=1),event(10,0,1,1000,200,2,biz=2),event(30,1,1,1000,300,1,biz=3),event(40,2,-1,1000,200,0,bid=2,ask=9,biz=4)])
    p=PaperEngine(a,600036,1000,0,False);p.submit('v',1,1000,100,BASE+5,BASE+1000)
    reports=parse(p.advance_to(BASE+50));assert sum(r['qty'] for r in reports)==100
    conservative=PaperEngine(a,600036,1000,0,True);conservative.submit('v',1,1000,100,BASE+5,BASE+1000)
    assert sum(r['qty'] for r in parse(conservative.advance_to(BASE+50)))==0

def test_shared_passive_capacity_and_ahead_advancement():
    a=array([event(0,0,1,1000,100,1,biz=1),event(10,0,1,1000,100,2,biz=2),event(20,2,-1,1000,100,0,bid=1,ask=9,biz=3),event(30,2,-1,1000,100,0,bid=2,ask=10,biz=4),event(40,0,1,1000,100,3,biz=5),event(50,2,-1,1000,100,0,bid=3,ask=11,biz=6)])
    p=PaperEngine(a,600036,1000,0,False);p.submit('first',1,1000,100,BASE+5,BASE+1000);p.submit('second',1,1000,100,BASE+15,BASE+1000)
    r=parse(p.advance_to(BASE+60));assert sum(x['qty'] for x in r if x['order_id']=='first')==100;assert sum(x['qty'] for x in r if x['order_id']=='second')==100

def test_taker_capacity_persists_after_cancel():
    a=array([event(0,0,-1,1000,300,1,biz=1),event(100,2,1,1000,200,0,bid=4,ask=1,biz=2)])
    p=PaperEngine(a,600036,1000,0,False)
    p.submit('a',1,1000,200,BASE+10,BASE+1000,'BEST5_CANCEL');p.submit('b',1,1000,200,BASE+20,BASE+1000,'BEST5_CANCEL')
    r=parse(p.advance_to(BASE+50));assert sum(x['qty'] for x in r)==300
    p.cancel('a',BASE+60);p.submit('c',1,1000,100,BASE+70,BASE+1000,'BEST5_CANCEL')
    assert sum(x['qty'] for x in parse(p.advance_to(BASE+80)))==0

def test_cancel_latency_and_delivery():
    a=array([event(0,0,1,1000,100,1,biz=1),event(10,0,1,1000,100,2,biz=2),event(20,2,-1,1000,100,0,bid=1,ask=9,biz=3),event(30,2,-1,1000,100,0,bid=2,ask=10,biz=4)])
    p=PaperEngine(a,600036,1000,50,False);p.submit('v',1,1000,100,BASE+5,BASE+1000);p.cancel('v',BASE+40)
    assert parse(p.advance_to(BASE+35))==[]
    r=parse(p.advance_to(BASE+100));assert sum(x['qty'] for x in r)==100;assert all(x['delivery_time']>=x['execution_time']+50 for x in r)

def test_expiry_without_later_history():
    p=PaperEngine(array([event(0,0,1,999,100,1)]),600036,1000,0,False);p.submit('v',1,998,100,BASE+5,BASE+20)
    assert any(r['status']=='EXPIRED' for r in parse(p.advance_to(BASE+100)))

def test_same_second_diagnostic_retains_direct_failure():
    a=array([event(500,0,1,999,100,1,biz=1),event(600,0,-1,1000,100,2,biz=2)])
    snp=np.zeros((1,42),dtype=np.int64);snp[0,0]=BASE;snp[0,2:4]=[999,100];snp[0,22:24]=[1000,100]
    v=parse(Replay(a).reconcile(snp))[0];assert not v['direct'];assert v['same_second']
