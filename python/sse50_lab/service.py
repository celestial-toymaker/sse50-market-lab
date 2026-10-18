"""Persistent command sessions share the same replay, account and simulation logic."""
from __future__ import annotations
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from ._core import Exchange,PaperEngine
from .account import Account,validate_order,validate_cage
from .data import DataStore,clock_ms,clock_text,continuous
from .models import OrderRequest,SessionRequest
from .settings import load_config,fee_schedule

TERMINAL={'FILLED','CANCELED','REJECTED','EXPIRED'}
class Session:
    def __init__(self,store:DataStore,config:SessionRequest,sid:str,settings=None):
        self.store=store;self.config=config;self.initial_config=config.model_copy(deep=True);self.id=sid;self.now=config.time_ms;self.symbol=config.symbol;self.meta=store.metadata(self.symbol);self.settings=settings or load_config();self.account=Account(config.cash_yuan*100,fee_schedule(self.settings))
        self.account.seed(self.symbol,config.overnight,self.meta['preclose']);self.orders={};self.reports=[];self.revision=0;self.commands=[];self.sandbox_ids={};self.last_price=self.meta['preclose']
        if config.mode=='paper':
            self.engine=PaperEngine(store.events(self.symbol),self.symbol,self.meta['preclose'],config.report_ms,config.conservative)
            self.engine.advance_to(self.now)
            self.visible=store.replay(self.symbol,max(0,self.now-config.market_ms))
        elif config.mode=='history':self.engine=store.replay(self.symbol,self.now)
        else:
            self.engine=Exchange();self.engine.set_phase(config.phase,self.meta['preclose'])
            if config.seed_history:self._seed_history()
    def _seed_history(self):
        r=self.store.replay(self.symbol,self.now); state=json.loads(r.checkpoint());self.last_price=state['stats']['last_price'] or self.meta['preclose']
        for o in sorted(state['orders'],key=lambda x:x['seq']):
            self.engine.seed(o['key'][1],o['side'],o['price'],o['qty'])
    def _consume(self,reports):
        for report in reports:
            self.account.apply(report);self.reports.append(report)
    def advance(self,to:int):
        if not(0<=to<=clock_ms('15:30:10')):raise ValueError('回放时间须位于当日15:30:10以前')
        if to<self.now:
            if self.config.mode=='history':self.engine=self.store.replay(self.symbol,to)
            else:raise ValueError('模拟回看通过分支接口恢复对应时点')
        elif self.config.mode=='history':self.engine.advance_to(to)
        elif self.config.mode=='paper':self._consume(json.loads(self.engine.advance_to(to)))
        if self.config.mode=='sandbox':
            expirations=[]
            day_end=clock_ms('15:30:00') if self.config.phase=='AFTER_HOURS' else clock_ms('15:00:00')
            for oid,o in self.orders.items():
                expiry=min(o['submitted_ms']+o['ttl_ms'],day_end)
                if expiry<=to and self.account.orders[oid]['status'] not in TERMINAL:expirations.append((expiry,oid))
            for expiry,oid in sorted(expirations):
                self.now=expiry
                native=next((k for k,v in self.sandbox_ids.items() if v==oid),None)
                if native is not None:self.engine.cancel(native,self.account.orders[oid]['quantity']-self.account.orders[oid]['filled'])
                self._sandbox_end(oid,'EXPIRED')
        self.now=to;self.revision+=1;return self.snapshot()
    def submit(self,request:OrderRequest,oid:str|None=None):
        if self.config.mode=='history':raise ValueError('请进入历史模拟或独立仿真后提交委托')
        side=1 if request.side=='BUY' else -1;pos=self.account.positions[self.symbol]
        phase=self.config.phase if self.config.mode=='sandbox' else 'CONTINUOUS'
        validate_order(self.symbol,request,self.meta['preclose'],pos.sellable-pos.frozen,phase)
        if self.config.mode=='paper' and not bool(continuous(self.now)):raise ValueError('历史模拟委托在连续竞价时段提交')
        if self.config.mode=='sandbox' and phase=='CONTINUOUS' and request.kind=='LIMIT':
            validate_cage(self.symbol,side,request.price_cents,json.loads(self.engine.snapshot())['book'],self.last_price)
        if self.config.mode=='sandbox' and phase=='AFTER_HOURS':
            close=json.loads(self.engine.snapshot())['close_price']
            if (side==1 and request.price_cents<close) or (side==-1 and request.price_cents>close):raise ValueError('收盘定价保护价须覆盖固定收盘价')
        oid=oid or uuid.uuid4().hex[:12]
        self.account.reserve(oid,self.symbol,side,request.price_cents,request.quantity)
        self.orders[oid]=request.model_dump()|{'id':oid,'submitted_ms':self.now,'status':'PENDING','filled':0}
        if self.config.mode=='paper':
            arrival=self.now+self.config.submit_ms;expiry=min(self.now+request.ttl_ms,clock_ms('14:57:00'))
            if expiry<arrival:
                self.account.apply({'order_id':oid,'status':'REJECTED','qty':0,'price':0,'execution_time':self.now,'delivery_time':self.now,'seq':len(self.reports)+1,'reason':'有效期须覆盖报单到达时间'})
            else:
                self.engine.submit(oid,side,request.price_cents,request.quantity,arrival,expiry,request.kind)
                self._consume(json.loads(self.engine.advance_to(self.now)))
        else:
            try:result=json.loads(self.engine.submit(side,request.price_cents,request.quantity,request.kind))
            except Exception:
                self.account.apply({'order_id':oid,'status':'REJECTED','qty':0,'price':0,'execution_time':self.now,'delivery_time':self.now,'seq':len(self.reports)+1,'reason':'申报参数待调整'});raise
            self.sandbox_ids[result['id']]=oid
            for fill in result['fills']:
                self._sandbox_fill(oid,fill['qty'],fill['price'])
                passive=self.sandbox_ids.get(fill['passive_id'])
                if passive:self._sandbox_fill(passive,fill['qty'],fill['price'])
            current=self.account.orders[oid]
            if result['status']=='CANCELED':self._sandbox_end(oid,'CANCELED')
            elif result['status'] not in TERMINAL:self._sandbox_end(oid,result['status'],result.get('price',request.price_cents))
        self.revision+=1;return self.snapshot()
    def _sandbox_fill(self,oid,q,p):
        self.last_price=p
        o=self.account.orders[oid];status='FILLED' if o['filled']+q==o['quantity'] else 'PARTIAL'
        self._consume([{'seq':len(self.reports)+1,'order_id':oid,'status':status,'qty':q,'price':p,'execution_time':self.now,'delivery_time':self.now,'reason':''}])
    def _sandbox_end(self,oid,status,price=0):
        self._consume([{'seq':len(self.reports)+1,'order_id':oid,'status':status,'qty':0,'price':price,'execution_time':self.now,'delivery_time':self.now,'reason':''}])
    def cancel(self,oid):
        if oid not in self.orders:raise ValueError('请选择本会话中的委托')
        o=self.account.orders[oid]
        if o['status'] in TERMINAL:return self.snapshot()
        if self.orders[oid].get('cancel_pending'):return self.snapshot()
        if self.config.mode=='paper':
            self.engine.cancel(oid,self.now+self.config.cancel_ms);self.orders[oid]['cancel_pending']=True
        else:
            if self.config.phase=='CLOSE_AUCTION' or (self.config.phase=='OPEN_AUCTION' and clock_ms('09:20:00')<=self.now<clock_ms('09:25:00')):raise ValueError('当前集合竞价窗口保留已接受的委托')
            native=next((key for key,value in self.sandbox_ids.items() if value==oid),None)
            if native is not None:self.engine.cancel(native,o['quantity']-o['filled'])
            self._sandbox_end(oid,'CANCELED')
        self.revision+=1;return self.snapshot()
    def phase(self,phase,close_price):
        if self.config.mode!='sandbox':raise ValueError('交易阶段切换用于独立仿真')
        if phase!=self.config.phase and (phase=='AFTER_HOURS' or self.config.phase=='AFTER_HOURS'):
            for oid in self.orders:
                if self.account.orders[oid]['status'] not in TERMINAL:self._sandbox_end(oid,'EXPIRED')
        self.engine.set_phase(phase,close_price or self.meta['preclose']);self.config.phase=phase;
        if phase=='AFTER_HOURS':self.last_price=close_price or self.meta['preclose']
        self.revision+=1;return self.snapshot()
    def seed(self,id,side,price,qty):
        if self.config.mode!='sandbox':raise ValueError('新增外部对手委托用于独立仿真')
        if self.config.phase in {'OPEN_AUCTION','CLOSE_AUCTION'}:self.engine.seed(id,1 if side=='BUY' else -1,price,qty)
        else:
            result=json.loads(self.engine.submit(1 if side=='BUY' else -1,price,qty,'CLOSE_PRICE' if self.config.phase=='AFTER_HOURS' else 'LIMIT',id))
            for fill in result['fills']:
                self.last_price=fill['price'];passive=self.sandbox_ids.get(fill['passive_id'])
                if passive:self._sandbox_fill(passive,fill['qty'],fill['price'])
        self.revision+=1;return self.snapshot()
    def auction(self):
        if self.config.mode!='sandbox':raise ValueError('集合撮合用于独立仿真')
        result=json.loads(self.engine.auction())
        if result['volume']:self.last_price=result['price']
        for fill in result['fills']:
            for key in ['bid','ask']:
                oid=self.sandbox_ids.get(fill[key])
                if oid:self._sandbox_fill(oid,fill['qty'],fill['price'])
        self.revision+=1;return self.snapshot()|{'auction_result':result}
    def snapshot(self):
        if self.config.mode=='paper':
            native=json.loads(self.engine.snapshot()); self.visible.advance_to(max(0,self.now-self.config.market_ms));market=json.loads(self.visible.snapshot());native_orders={o['id']:o for o in native['orders']}
        elif self.config.mode=='history':market=json.loads(self.engine.snapshot());native_orders={}
        else:market={'book':json.loads(self.engine.snapshot())['book'],'time':self.now,'phase':self.config.phase};native_orders={}
        book=market['book'];bids=book['bids'];asks=book['asks'];mid=(bids[0][0]+asks[0][0])//2 if bids and asks else (self.last_price if self.config.mode=='sandbox' else market.get('stats',{}).get('last_price') or self.meta['preclose'])
        market['mark_price_cents']=mid
        orders=[]
        for oid,request in self.orders.items():
            o=self.account.orders[oid];native=native_orders.get(oid,{})
            if self.config.mode=='sandbox' and o['status'] not in TERMINAL:
                native_id=next((k for k,v in self.sandbox_ids.items() if v==oid),None);ahead=0
                for resting in json.loads(self.engine.queue(o['side'],o['price'],100000)):
                    if resting['key'][1]==native_id:break
                    ahead+=resting['qty']
                native={'ahead':ahead}
            orders.append(request|{'status':o['status'],'filled':o['filled'],'remaining':o['quantity']-o['filled'],'turnover_cents':o['turnover'],'fees_cents':o['fees'],'queue_ahead':native.get('ahead',0),'effective_price_cents':o['price'],'cancel_pending':request.get('cancel_pending',False) and o['status'] not in TERMINAL})
        return {'id':self.id,'symbol':self.symbol,'mode':self.config.mode,'time_ms':self.now,'time':clock_text(self.now),'revision':self.revision,'market':market,'orders':orders,'reports':self.reports[-200:],'account':self.account.snapshot({self.symbol:mid}),'config':self.config.model_dump(),'model':{'path':'固定历史路径与共享虚拟容量' if self.config.mode=='paper' else '历史数据' if self.config.mode=='history' else '独立交易所仿真','queue':'仅实际成交流推进' if self.config.conservative else 'FIFO及队前撤单','latency':'历史模拟采用行情、报撤单和回报延迟；独立仿真即时处理'}}

class SessionStore:
    def __init__(self,data:DataStore,path:Path,config=None):
        self.config=config or load_config();path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path,check_same_thread=False);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, config TEXT)');self.db.execute('CREATE TABLE IF NOT EXISTS commands (seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, time_ms INTEGER, kind TEXT, payload TEXT)');
        if 'settings' not in [r[1] for r in self.db.execute('PRAGMA table_info(sessions)')]:self.db.execute('ALTER TABLE sessions ADD COLUMN settings TEXT')
        self.db.commit();self.data=data;self.sessions={};self.lock=threading.RLock()
    def create(self,config:SessionRequest):
        with self.lock:
            if config.symbol not in self.data.symbols:raise ValueError('请选择数据集中的股票代码')
            sid=uuid.uuid4().hex[:12];s=Session(self.data,config,sid,self.config);self.sessions[sid]=s;self.db.execute('INSERT INTO sessions(id,config,settings) VALUES (?,?,?)',(sid,config.model_dump_json(),json.dumps(self.config)));self.db.commit();return s
    def get(self,sid):
        with self.lock:
            if sid in self.sessions:return self.sessions[sid]
            row=self.db.execute('SELECT config,settings FROM sessions WHERE id=?',(sid,)).fetchone()
            if row is None:raise KeyError(sid)
            s=Session(self.data,SessionRequest.model_validate_json(row[0]),sid,json.loads(row[1]) if row[1] else self.config)
            for t,kind,payload in self.db.execute('SELECT time_ms,kind,payload FROM commands WHERE session_id=? ORDER BY seq',(sid,)):
                if t>s.now:s.advance(t)
                self._apply(s,kind,json.loads(payload))
            self.sessions[sid]=s;return s
    @staticmethod
    def _apply(s,kind,payload):
        if kind=='advance':return s.advance(payload['to_ms'])
        if kind=='submit':return s.submit(OrderRequest(**payload['request']),payload['oid'])
        if kind=='cancel':return s.cancel(payload['oid'])
        if kind=='phase':return s.phase(payload['phase'],payload['close_price'])
        if kind=='seed':return s.seed(payload['id'],payload['side'],payload['price_cents'],payload['quantity'])
        if kind=='auction':return s.auction()
        raise ValueError('请选择受支持的会话操作')
    def command(self,sid,kind,payload):
        with self.lock:
            s=self.get(sid);now=s.now
            if kind=='submit':payload=payload|{'oid':uuid.uuid4().hex[:12]}
            result=self._apply(s,kind,payload)
            self.db.execute('INSERT INTO commands(session_id,time_ms,kind,payload) VALUES (?,?,?,?)',(sid,now,kind,json.dumps(payload)));self.db.commit();return result
    def branch(self,sid,to):
        with self.lock:
            old=self.get(sid);config=old.initial_config.model_copy(deep=True);config.time_ms=min(config.time_ms,to);s=self.create(config)
            rows=self.db.execute('SELECT time_ms,kind,payload FROM commands WHERE session_id=? ORDER BY seq',(sid,)).fetchall()
            for t,kind,payload in rows:
                if t>to:break
                p=json.loads(payload)
                if kind=='advance' and p['to_ms']>to:break
                if t>s.now:self.command(s.id,'advance',{'to_ms':t})
                self._apply(s,kind,p)
                self.db.execute('INSERT INTO commands(session_id,time_ms,kind,payload) VALUES (?,?,?,?)',(s.id,t,kind,payload))
            self.db.commit();self.command(s.id,'advance',{'to_ms':to});return s.snapshot()|{'parent_session':sid}
