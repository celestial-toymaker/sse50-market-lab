"""Integer-cent account, cumulative per-order fees and T+1 inventory."""
from __future__ import annotations
from dataclasses import dataclass,field
from decimal import Decimal,ROUND_HALF_UP

@dataclass
class FeeSchedule:
    commission_rate:Decimal=Decimal('0.00025')
    commission_min_cents:int=500
    transfer_rate:Decimal=Decimal('0.00001')
    sell_stamp_rate:Decimal=Decimal('0.0005')
    @staticmethod
    def round(value): return int(Decimal(value).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    def total(self,turnover_cents:int,side:int)->int:
        if turnover_cents==0:return 0
        commission=max(self.commission_min_cents,self.round(Decimal(turnover_cents)*self.commission_rate))
        transfer=self.round(Decimal(turnover_cents)*self.transfer_rate)
        stamp=self.round(Decimal(turnover_cents)*self.sell_stamp_rate) if side==-1 else 0
        return commission+transfer+stamp

@dataclass
class Position:
    total:int=0
    sellable:int=0
    frozen:int=0
    initial:int=0
    initial_price:int=0

class Account:
    def __init__(self,cash_cents:int,fees:FeeSchedule|None=None):
        self.cash=cash_cents;self.initial_cash=cash_cents;self.fees=fees or FeeSchedule();self.positions={};self.orders={};self.fees_paid=0;self.frozen_cash=0;self.fills=[]
    def seed(self,symbol:int,qty:int,price:int):
        if qty<0 or price<=0:raise ValueError('初始持仓数量须非负且估值价格为正')
        self.positions[symbol]=Position(qty,qty,0,qty,price)
    def reserve(self,oid:str,symbol:int,side:int,price:int,qty:int):
        if oid in self.orders:raise ValueError('委托标识须唯一')
        pos=self.positions.setdefault(symbol,Position())
        amount=price*qty+self.fees.total(price*qty,side) if side==1 else max(0,self.fees.total(price,side)-price)
        if amount>self.cash-self.frozen_cash:raise ValueError('可用资金须覆盖委托金额与费用')
        if side==-1 and qty>pos.sellable-pos.frozen:raise ValueError('可卖隔夜持仓须覆盖卖出数量；当日买入按T+1处理')
        self.frozen_cash+=amount
        if side==-1:pos.frozen+=qty
        self.orders[oid]={'id':oid,'symbol':symbol,'side':side,'price':price,'quantity':qty,'filled':0,'turnover':0,'fees':0,'reserve':amount,'status':'PENDING','reports':[]}
    def apply(self,report:dict):
        oid=report['order_id'];o=self.orders[oid];o['reports'].append(report);o['status']=report['status'];pos=self.positions[o['symbol']]
        q=int(report['qty']);p=int(report['price'])
        if q==0 and p>0 and report['status'] in {'OPEN','PARTIAL'}:
            o['price']=p
            if o['side']==1:
                remaining=o['quantity']-o['filled'];reserve=p*remaining+max(0,self.fees.total(o['turnover']+p*remaining,1)-o['fees'])
                self.frozen_cash+=reserve-o['reserve'];o['reserve']=reserve
        if q:
            if q>o['quantity']-o['filled']:raise ValueError('成交数量须位于剩余数量内')
            previous_reserve=o['reserve'];o['filled']+=q;o['turnover']+=p*q
            fee_total=self.fees.total(o['turnover'],o['side']);fee=fee_total-o['fees'];o['fees']=fee_total;self.fees_paid+=fee
            if o['side']==1:
                self.cash-=p*q+fee;pos.total+=q
                remaining=o['quantity']-o['filled']
                reserve=o['price']*remaining+max(0,self.fees.total(o['turnover']+o['price']*remaining,1)-fee_total)
                o['reserve']=reserve;self.frozen_cash+=reserve-previous_reserve
            else:
                self.cash+=p*q-fee;pos.total-=q;pos.sellable-=q;pos.frozen-=q
                self.frozen_cash-=o['reserve'];o['reserve']=0
            self.fills.append(report|{'symbol':o['symbol'],'side':o['side'],'fee_cents':fee})
        if report['status'] in {'CANCELED','REJECTED','EXPIRED','FILLED'}:
            self.frozen_cash-=o['reserve'];o['reserve']=0
            if o['side']==-1 and report['status']!='FILLED':
                pos.frozen-=o['quantity']-o['filled']
        self.assert_invariants()
    def assert_invariants(self):
        assert self.cash>=0 and self.frozen_cash>=0 and self.cash>=self.frozen_cash, '资金守恒检查'
        for p in self.positions.values():assert 0<=p.frozen<=p.sellable<=p.total,'持仓守恒检查'
    def snapshot(self,prices:dict[int,int]):
        holdings=sum(p.total*prices.get(s,p.initial_price) for s,p in self.positions.items())
        baseline=self.initial_cash+sum(p.initial*prices.get(s,p.initial_price) for s,p in self.positions.items())
        return {'cash_cents':self.cash,'frozen_cash_cents':self.frozen_cash,'available_cash_cents':self.cash-self.frozen_cash,'fees_cents':self.fees_paid,'equity_cents':self.cash+holdings,'incremental_pnl_cents':self.cash+holdings-baseline,'positions':{str(s):{'total':p.total,'sellable':p.sellable,'frozen':p.frozen,'available':p.sellable-p.frozen,'initial':p.initial} for s,p in self.positions.items()}}

def validate_order(symbol:int,request,preclose:int,sellable:int,phase:str='CONTINUOUS'):
    star=symbol>=688000;q=request.quantity;side=request.side
    if side=='BUY':
        if star and q<200:raise ValueError('科创板买入至少200股')
        if not star and q%100:raise ValueError('主板买入数量须为100股的整数倍')
    else:
        unit=200 if star else 100
        if star and q<unit and q!=sellable:raise ValueError('科创板不足200股的余额须一次性卖出')
        if not star and q%100 and q%100!=sellable%100:raise ValueError('主板零股部分须一次性申报卖出')
    maximum=1000000
    if star and request.kind!='CLOSE_PRICE':maximum=100000 if request.kind=='LIMIT' else 50000
    if q>maximum:raise ValueError(f'此类申报单笔数量上限为{maximum}股')
    ratio=Decimal('0.2') if star else Decimal('0.1')
    low=int((Decimal(preclose)*(1-ratio)).quantize(Decimal('1'),rounding=ROUND_HALF_UP));high=int((Decimal(preclose)*(1+ratio)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    if not low<=request.price_cents<=high:raise ValueError(f'申报价格须位于{low/100:.2f}至{high/100:.2f}元')
    if phase in {'OPEN_AUCTION','CLOSE_AUCTION'} and request.kind!='LIMIT':raise ValueError('集合竞价使用限价申报')
    if phase=='AFTER_HOURS' and request.kind!='CLOSE_PRICE':raise ValueError('盘后使用收盘定价申报')
    if phase!='AFTER_HOURS' and request.kind=='CLOSE_PRICE':raise ValueError('收盘定价委托用于盘后仿真阶段')


def validate_cage(symbol,side,price,book,preclose,last_price=0):
    opposite=book['asks'] if side==1 else book['bids']
    own=book['bids'] if side==1 else book['asks']
    base=opposite[0][0] if opposite else own[0][0] if own else last_price or preclose
    star=symbol>=688000
    upper=(base*102+50)//100
    lower=(base*98+50)//100
    if not star:upper=max(upper,base+10);lower=min(lower,base-10)
    if (side==1 and price>upper) or (side==-1 and price<lower):
        raise ValueError('连续竞价限价申报须位于当前有效价格范围内')
