from decimal import Decimal
import pytest
from sse50_lab.account import Account,FeeSchedule,validate_order
from sse50_lab.models import OrderRequest

def report(oid,q,p,status='PARTIAL'):return {'order_id':oid,'qty':q,'price':p,'status':status,'execution_time':1,'delivery_time':51,'seq':1}

def test_hand_calculated_partial_fill_cancel():
    a=Account(1000000);a.seed(600036,0,1000);a.reserve('a',600036,1,1000,500)
    assert a.frozen_cash==500505
    a.apply(report('a',300,1000));a.apply(report('a',100,1000));a.apply(report('a',0,0,'CANCELED'))
    assert a.cash==599496 and a.frozen_cash==0 and a.fees_paid==504
    assert a.positions[600036].total==400 and a.positions[600036].sellable==0

def test_sell_freezing_and_fee_accounting():
    a=Account(1000000);a.seed(600036,500,1000);a.reserve('s',600036,-1,1000,500)
    with pytest.raises(ValueError):a.reserve('s2',600036,-1,1000,100)
    a.apply(report('s',200,1000));a.apply(report('s',0,0,'CANCELED'))
    assert a.positions[600036].frozen==0 and a.positions[600036].sellable==300
    assert a.fees_paid==602 and a.cash==1199398

def test_tplus1_and_fund_limits():
    a=Account(100000);a.seed(600036,0,1000)
    with pytest.raises(ValueError):a.reserve('a',600036,1,1000,100)
    a=Account(200000);a.seed(600036,0,1000);a.reserve('a',600036,1,1000,100);a.apply(report('a',100,1000,'FILLED'))
    with pytest.raises(ValueError):a.reserve('s',600036,-1,1000,100)

def test_per_order_minimum_and_cumulative_rounding():
    a=Account(10000000);a.reserve('a',600036,1,1000,1000)
    for i in range(10):a.apply(report('a',100,1000,'FILLED' if i==9 else 'PARTIAL'))
    assert a.fees_paid==510

def test_inventory_benchmark_separates_market_move():
    a=Account(10000);a.seed(600036,1000,1000)
    assert a.snapshot({600036:900})['incremental_pnl_cents']==0

def test_main_star_units_and_price_limits():
    with pytest.raises(ValueError):validate_order(600036,OrderRequest(side='BUY',price_cents=1000,quantity=101),1000,0)
    validate_order(688981,OrderRequest(side='BUY',price_cents=1000,quantity=201),1000,0)
    with pytest.raises(ValueError):validate_order(688981,OrderRequest(side='BUY',price_cents=1000,quantity=199),1000,0)
    with pytest.raises(ValueError):validate_order(600036,OrderRequest(side='BUY',price_cents=1200,quantity=100),1000,0)

def test_price_cage_fallback_and_odd_lot():
    from sse50_lab.account import validate_cage
    book={'asks':[[1000,100,1]],'bids':[[999,100,1]]}
    validate_cage(600036,1,1020,book,1000)
    with pytest.raises(ValueError):validate_cage(600036,1,1021,book,1000)
    validate_order(600036,OrderRequest(side='SELL',price_cents=1000,quantity=150),1000,250)
    with pytest.raises(ValueError):validate_order(600036,OrderRequest(side='SELL',price_cents=1000,quantity=125),1000,250)

def test_effective_resting_price_releases_excess_reservation():
    a=Account(1000000);a.reserve('a',600036,1,1002,500)
    original=a.frozen_cash;a.apply(report('a',0,1000,'OPEN'))
    assert a.orders['a']['price']==1000 and a.frozen_cash<original

def test_sell_fee_shortfall_is_reserved():
    a=Account(100);a.seed(600036,1,315)
    with pytest.raises(ValueError):a.reserve('s',600036,-1,315,1)
    a=Account(1000);a.seed(600036,1,315);a.reserve('s',600036,-1,315,1)
    assert a.frozen_cash==185
    a.apply(report('s',1,315,'FILLED'));assert a.cash==815 and a.frozen_cash==0
