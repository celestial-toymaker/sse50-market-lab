import json
from fastapi.testclient import TestClient
from sse50_lab.api import create_app
from sse50_lab.models import SessionRequest,OrderRequest
from sse50_lab.service import SessionStore
from conftest import BASE

def test_api_paper_fill_cancel_and_restore(tiny_store,tmp_path):
    app=create_app(tiny_store.root,tmp_path/'sessions.sqlite')
    with TestClient(app) as c:
        r=c.post('/api/sessions',json={'symbol':600036,'mode':'paper','time_ms':BASE,'cash_yuan':1000000,'market_ms':0,'submit_ms':50,'report_ms':50}).json();sid=r['id']
        r=c.post(f'/api/sessions/{sid}/orders',json={'side':'BUY','price_cents':1000,'quantity':200,'kind':'LIMIT'});assert r.status_code==200
        result=c.post(f'/api/sessions/{sid}/advance',json={'to_ms':BASE+200}).json();assert result['orders'][0]['filled']==200
        before=result['account'];app.state.sessions.sessions.clear();restored=c.get(f'/api/sessions/{sid}').json();assert restored['account']==before
        branch=c.post(f'/api/sessions/{sid}/branch',json={'to_ms':BASE+50}).json();assert branch['id']!=sid and branch['account']['positions']['600036']['total']==0
        assert c.get(f'/api/sessions/{sid}/export').status_code==200
        assert c.get('/openapi.json').status_code==200

def test_sandbox_and_account_self_orders(tiny_store,tmp_path):
    store=SessionStore(tiny_store,tmp_path/'s.sqlite');s=store.create(SessionRequest(symbol=600036,mode='sandbox',time_ms=BASE,seed_history=False,overnight=500))
    store.command(s.id,'seed',{'id':1,'side':'SELL','price_cents':1000,'quantity':100})
    r=store.command(s.id,'submit',{'request':{'side':'BUY','price_cents':1000,'quantity':200,'kind':'LIMIT'}})
    assert r['orders'][0]['filled']==100
    oid=r['orders'][0]['id'];r=store.command(s.id,'cancel',{'oid':oid});assert r['account']['frozen_cash_cents']==0

def test_auction_cancel_window(tiny_store,tmp_path):
    store=SessionStore(tiny_store,tmp_path/'s.sqlite');s=store.create(SessionRequest(symbol=600036,mode='sandbox',time_ms=BASE,seed_history=False,phase='CLOSE_AUCTION'))
    r=store.command(s.id,'submit',{'request':{'side':'BUY','price_cents':1000,'quantity':100,'kind':'LIMIT'}})
    import pytest
    with pytest.raises(ValueError):store.command(s.id,'cancel',{'oid':r['orders'][0]['id']})

def test_actual_step_and_validation(tiny_store,tmp_path):
    with TestClient(create_app(tiny_store.root,tmp_path/'s.sqlite')) as c:
        s=c.post('/api/sessions',json={'symbol':600036,'time_ms':BASE}).json();sid=s['id']
        assert c.post(f'/api/sessions/{sid}/step',json={}).json()['time_ms']==BASE+100
        assert c.post('/api/sessions',json={'symbol':12345}).status_code==400

def test_day_expiry_and_after_hours_resources(tiny_store,tmp_path):
    store=SessionStore(tiny_store,tmp_path/'s.sqlite');s=store.create(SessionRequest(symbol=600036,mode='sandbox',time_ms=BASE,seed_history=False))
    r=store.command(s.id,'submit',{'request':{'side':'BUY','price_cents':1000,'quantity':100,'kind':'LIMIT','ttl_ms':100}})
    result=store.command(s.id,'advance',{'to_ms':BASE+101});assert result['orders'][0]['status']=='EXPIRED' and result['account']['frozen_cash_cents']==0
    store.command(s.id,'submit',{'request':{'side':'BUY','price_cents':1000,'quantity':100,'kind':'LIMIT'}})
    r=store.command(s.id,'phase',{'phase':'AFTER_HOURS','close_price':1000});assert r['account']['frozen_cash_cents']==0 and r['market']['book']['bids']==[]
    store.command(s.id,'seed',{'id':123,'side':'SELL','price_cents':1000,'quantity':100})
    r=store.command(s.id,'submit',{'request':{'side':'BUY','price_cents':1001,'quantity':100,'kind':'CLOSE_PRICE'}})
    assert r['orders'][-1]['filled']==100 and r['account']['positions']['600036']['total']==100

def test_public_schemas_and_experiment_validation(tiny_store,tmp_path):
    with TestClient(create_app(tiny_store.root,tmp_path/'s.sqlite')) as c:
        schemas=c.get('/openapi.json').json()['components']['schemas']
        assert {'MarketEvent','OrderRequest','ExecutionReport','AccountState','ExperimentConfig'}<=schemas.keys()
        assert c.post('/api/experiments',json={'deadline_s':10}).status_code==400

def test_sandbox_uses_own_queue_and_last_trade_valuation(tiny_store,tmp_path):
    with TestClient(create_app(tiny_store.root,tmp_path/'s.sqlite')) as c:
        s=c.post('/api/sessions',json={'symbol':600036,'mode':'sandbox','time_ms':BASE,'seed_history':False,'phase':'OPEN_AUCTION'}).json();sid=s['id']
        c.post(f'/api/sessions/{sid}/seed',json={'id':123,'side':'SELL','price_cents':1000,'quantity':300})
        c.post(f'/api/sessions/{sid}/orders',json={'side':'BUY','kind':'LIMIT','price_cents':1002,'quantity':500})
        r=c.post(f'/api/sessions/{sid}/auction',json={}).json()
        assert r['market']['mark_price_cents']==1002
        assert r['account']['incremental_pnl_cents']==-r['account']['fees_cents']
        queue=c.get(f'/api/sessions/{sid}/queue?side=1&price=1002').json()
        assert len(queue)==1 and queue[0]['qty']==200 and queue[0]['key'][0]==0
        c.post(f'/api/sessions/{sid}/seed',json={'id':456,'side':'BUY','price_cents':1002,'quantity':100})
        r=c.post(f'/api/sessions/{sid}/orders',json={'side':'BUY','kind':'LIMIT','price_cents':1002,'quantity':100}).json()
        assert r['orders'][-1]['queue_ahead']==300


def test_external_arrival_reproduces_the_worked_partial_fill_case(tiny_store,tmp_path):
    store=SessionStore(tiny_store,tmp_path/'s.sqlite');s=store.create(SessionRequest(symbol=600036,mode='sandbox',time_ms=BASE,cash_yuan=10000,seed_history=False))
    store.command(s.id,'seed',{'id':1,'side':'SELL','price_cents':1000,'quantity':300})
    r=store.command(s.id,'submit',{'request':{'side':'BUY','price_cents':1000,'quantity':500,'kind':'LIMIT'}});oid=r['orders'][0]['id']
    assert r['orders'][0]['filled']==300
    r=store.command(s.id,'seed',{'id':2,'side':'SELL','price_cents':1000,'quantity':100})
    assert r['orders'][0]['filled']==400 and r['market']['book']['asks']==[]
    r=store.command(s.id,'cancel',{'oid':oid})
    assert r['account']['cash_cents']==599496 and r['account']['frozen_cash_cents']==0
    assert r['account']['positions']['600036']['total']==400
