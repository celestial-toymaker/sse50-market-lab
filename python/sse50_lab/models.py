from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

OrderKind=Literal['LIMIT','BEST5_CANCEL','BEST5_LIMIT','OWN_BEST','OPPOSITE_BEST','CLOSE_PRICE']
class MarketEvent(BaseModel):
    logical_ms:int
    biz_index:int
    channel:int
    kind:Literal['ADD','CANCEL','TRADE','PHASE']
    side:int
    price_cents:int
    quantity:int
    order_id:int=0

class OrderRequest(BaseModel):
    side:Literal['BUY','SELL']
    price_cents:int=Field(gt=0)
    quantity:int=Field(gt=0)
    kind:OrderKind='LIMIT'
    ttl_ms:int=Field(default=60000,ge=1,le=21600000)

class ExecutionReport(BaseModel):
    seq:int
    order_id:str
    status:str
    qty:int
    price:int
    execution_time:int
    delivery_time:int
    reason:str=''

class AccountState(BaseModel):
    cash_cents:int
    frozen_cash_cents:int
    available_cash_cents:int
    positions:dict
    fees_cents:int
    equity_cents:int
    incremental_pnl_cents:int

class ExperimentConfig(BaseModel):
    device:Literal['auto','mps','cuda','cpu']='auto'
    seed:int=Field(default=20260923,ge=0)
    parent_yuan:int=Field(default=50000,gt=0)
    horizons_s:list[int]=[1,5,30,60]
    deadline_s:int=60

class SessionRequest(BaseModel):
    symbol:int=600036
    mode:Literal['history','paper','sandbox']='history'
    time_ms:int=34200000
    cash_yuan:int=Field(default=1000000,gt=0)
    overnight:int=Field(default=0,ge=0)
    market_ms:int=Field(default=100,ge=0,le=5000)
    submit_ms:int=Field(default=50,ge=0,le=5000)
    cancel_ms:int=Field(default=50,ge=0,le=5000)
    report_ms:int=Field(default=50,ge=0,le=5000)
    conservative:bool=False
    seed_history:bool=True
    phase:Literal['CONTINUOUS','OPEN_AUCTION','CLOSE_AUCTION','AFTER_HOURS']='CONTINUOUS'

class AdvanceRequest(BaseModel):
    to_ms:int

class PhaseRequest(BaseModel):
    phase:Literal['CONTINUOUS','OPEN_AUCTION','CLOSE_AUCTION','AFTER_HOURS']
    close_price:int=0

class SeedRequest(BaseModel):
    id:int=Field(gt=0,lt=1000000000)
    side:Literal['BUY','SELL']
    price_cents:int=Field(gt=0)
    quantity:int=Field(gt=0)
