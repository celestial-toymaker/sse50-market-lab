"""Causal virtual probes and parent-order execution with a shared account audit."""
from __future__ import annotations
import json
import math
import numpy as np
import pandas as pd
from ._core import PaperEngine
from .account import Account
from .data import DataStore,clock_ms,continuous
from .features import split
from .settings import load_config,fee_schedule

NAMES={'immediate':'立即主动','twap':'固定 TWAP','passive':'被动等待','adaptive':'信号驱动'}

def at(frame,t):
    i=int(np.searchsorted(frame.t.to_numpy(),t,side='right'))-1
    return frame.iloc[max(0,i)]

def probes(data:DataStore,symbol:int,f:pd.DataFrame,scale=1,conservative=False,config=None):
    latency=(config or load_config())['latency']
    engine=PaperEngine(data.events(symbol),symbol,data.metadata(symbol)['preclose'],latency['report_ms']*scale,conservative)
    rows=[];meta={};qty=200 if symbol>=688000 else 100
    for r in f[(f.t%60000==0)&(f.split!='context')].itertuples():
        limit=clock_ms('11:30:00') if r.t<clock_ms('13:00:00') else clock_ms('14:57:00')
        if r.t+90000>=limit:continue
        visible=at(f,r.t-latency['market_ms']*scale)
        for side in [1,-1]:
            oid=f'{symbol}-{int(r.t)}-{side}';px=int(visible.bid if side==1 else visible.ask)
            engine.submit(oid,side,px,qty,int(r.t+latency['submit_ms']*scale),int(r.t+30000),'LIMIT')
            meta[oid]={'symbol':symbol,'t':int(r.t),'side':side,'quantity':qty,'price':px,'signal':float(visible.imbalance*side),'spread_tick':int(visible.spread_tick),'depth':float(visible.depth),'volatility':float(visible.volatility_30),'mid':float(visible.mid),'split':r.split,'block':int(r.block),'scale':scale,'conservative':conservative,'filled':0,'first_fill':None,'turnover':0}
    reports=json.loads(engine.advance_to(clock_ms('15:30:10')))
    fills={}
    for report in reports:
        if report['qty']:
            o=meta[report['order_id']];o['filled']+=report['qty'];o['turnover']+=report['qty']*report['price'];o['first_fill']=min(o['first_fill'] or report['execution_time'],report['execution_time']);fills.setdefault(report['order_id'],[]).append(report)
    for oid,o in meta.items():
        o['fill_rate']=o['filled']/qty;o['completed']=float(o['filled']==qty)
        o['strong']=o['signal']>=0.6;o['weak']=o['signal']<=-0.6
        for h in [1,5,30,60]:
            future=at(f,o['t']+h*1000);o[f'ret_{h}']=o['side']*(future.mid-o['mid'])/o['mid']*10000
            values=[]
            for fill in fills.get(oid,[]):
                future=at(f,fill['execution_time']+h*1000)
                values.append(fill['qty']*o['side']*(future.mid-fill['price'])/fill['price']*10000)
            o[f'markout_{h}']=sum(values)/o['filled'] if o['filled'] else np.nan
        rows.append(o)
    return pd.DataFrame(rows)

def parent_quantity(symbol,preclose,target_yuan=50000):
    raw=math.ceil(target_yuan*100/preclose)
    return max(200,raw) if symbol>=688000 else max(100,math.ceil(raw/100)*100)

def execution_symbol(data,symbol,f,strategy,scale,conservative,threshold,config=None):
    config=config or load_config();latency=config['latency'];fees=fee_schedule(config)
    preclose=data.metadata(symbol)['preclose'];parent_q=parent_quantity(symbol,preclose,config['research']['parent_yuan']);unit=1 if symbol>=688000 else 100;minimum=200 if symbol>=688000 else 100
    engine=PaperEngine(data.events(symbol),symbol,preclose,latency['report_ms']*scale,conservative)
    log=[];parents=[];orders={};order_counter=0;engine_clock=0
    def receive(t):
        nonlocal engine_clock
        if t<engine_clock:return
        engine_clock=t
        for report in json.loads(engine.advance_to(int(t))):
            if report['order_id'] in orders:
                o=orders[report['order_id']];o['status']=report['status'];o['filled']+=report['qty']
                if report['qty']:o['fills'].append(report)
                log.append({'time':int(report['delivery_time']),'symbol':symbol,'event':'report','payload':report})
    starts=['09:35:00','10:05:00','10:35:00','11:05:00','13:05:00','13:35:00','14:05:00','14:35:00']
    for k,text in enumerate(starts):
        start=clock_ms(text);end=start+60000;side=1 if k%2==0 else -1;receive(start);initial=at(f,start-latency['market_ms']*scale);benchmark=float(at(f,start).mid)
        protection=int(initial.ask+2 if side==1 else initial.bid-2);prefix=f'{symbol}:{strategy}:{scale}:{int(conservative)}:{k}';ids=[];switched=False;pending_cancel={};last_action=start-10000
        def totals():return sum(orders[i]['filled'] for i in ids)
        def open_ids():return [i for i in ids if orders[i]['status'] not in {'FILLED','CANCELED','REJECTED','EXPIRED'}]
        def submit(now,passive,target):
            nonlocal order_counter,last_action
            visible=at(f,now-latency['market_ms']*scale)
            cap=min(float(visible.adepth if side==1 else visible.bdepth)*0.10,float(visible.trade_volume_60)*0.01,target)
            q=int(cap//unit)*unit
            if q<minimum:return False
            px=int(visible.bid if side==1 else visible.ask) if passive else protection
            if (side==1 and px>protection) or (side==-1 and px<protection):return False
            oid=f'{prefix}:{order_counter}';order_counter+=1
            kind='LIMIT' if passive else 'BEST5_CANCEL';expiry=end if passive else min(end,now+1000+latency['submit_ms']*scale)
            arrival=now+latency['submit_ms']*scale
            if arrival>=expiry:return False
            engine.submit(oid,side,px,q,int(arrival),int(expiry),kind)
            orders[oid]={'id':oid,'symbol':symbol,'side':side,'price':px,'quantity':q,'filled':0,'status':'PENDING','fills':[],'parent':prefix};ids.append(oid)
            log.append({'time':int(now),'symbol':symbol,'event':'submit','payload':{'id':oid,'symbol':symbol,'side':side,'price':px,'quantity':q}});last_action=now;return True
        for now in range(start,end,1000):
            if now<engine_clock:continue
            receive(now)
            if totals()>=parent_q:continue
            active=open_ids();remaining=parent_q-totals();visible=at(f,now-latency['market_ms']*scale)
            if strategy=='adaptive' and not switched and float(visible.imbalance)*side>=threshold:
                switched=True
                for oid in active:
                    engine.cancel(oid,now+latency['cancel_ms']*scale);pending_cancel[oid]=now+(latency['cancel_ms']+latency['report_ms'])*scale
            if strategy=='immediate':
                if not active and (now==start or now>=end-2000):submit(now,False,remaining)
            elif strategy=='twap':
                due=min(parent_q,math.floor(parent_q*(now-start+10000)/60000/unit)*unit)
                if not active and now-last_action>=10000:submit(now,False,min(remaining,max(0,due-totals())))
            elif strategy=='passive':
                if not active:submit(now,True,remaining)
            else:
                if not active:submit(now,not switched,remaining)
            if now>=end-2000 and strategy in {'passive','adaptive'}:
                for oid in open_ids():
                    if oid not in pending_cancel:engine.cancel(oid,now+latency['cancel_ms']*scale);pending_cancel[oid]=now+(latency['cancel_ms']+latency['report_ms'])*scale
                receive(now+max(1,(latency['cancel_ms']+latency['report_ms'])*scale))
                if not open_ids():submit(now+max(1,(latency['cancel_ms']+latency['report_ms'])*scale),False,parent_q-totals())
        receive(end+max(1000,(latency['cancel_ms']+latency['report_ms']+latency['submit_ms'])*scale));fills=[fill for oid in ids for fill in orders[oid]['fills']];filled=sum(x['qty'] for x in fills)
        if filled>parent_q:raise AssertionError('母单成交须位于目标数量以内')
        turnover=sum(x['qty']*x['price'] for x in fills);terminal=float(at(f,end).mid);remaining=parent_q-filled
        total_fees=sum(fees.total(sum(x['qty']*x['price'] for x in orders[oid]['fills']),side) for oid in ids)
        cost=side*(turnover+remaining*terminal-parent_q*benchmark)+total_fees
        parents.append({'parent':prefix,'symbol':symbol,'t':start,'side':side,'strategy':strategy,'scale':scale,'conservative':conservative,'quantity':parent_q,'filled':filled,'remaining':remaining,'turnover_cents':turnover,'fees_cents':total_fees,'benchmark':benchmark,'terminal':terminal,'cost_cents':cost,'cost_bp':cost/(parent_q*benchmark)*10000,'complete':filled==parent_q,'wait_s':(max(x['delivery_time'] for x in fills)-start)/1000 if filled==parent_q else np.nan,'split':split(start)})
    return parents,log,parent_q

def shared_account(logs,quantities,data,config=None):
    config=config or load_config();account=Account(config['research']['cash_yuan']*100,fee_schedule(config))
    for symbol,q in quantities.items():account.seed(symbol,q*config['research']['inventory_parents'],data.metadata(symbol)['preclose'])
    for i,item in enumerate(sorted(logs,key=lambda x:(x['time'],x['symbol'],x['ordinal']))):
        p=item['payload']
        if item['event']=='submit':account.reserve(p['id'],p['symbol'],p['side'],p['price'],p['quantity'])
        else:account.apply(p)
    prices={s:int(round(float(at(pd.read_parquet(data.cache/f'{s}/features.parquet',columns=['t','mid']),clock_ms('14:57:00')-1000).mid))) for s in quantities}
    return account.snapshot(prices)
