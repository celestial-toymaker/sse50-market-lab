use numpy::PyReadonlyArray2;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};

type Key = (i64, i64);
const CONT_START: i64 = 34_200_000;
const MORNING_END: i64 = 41_400_000;
const AFTERNOON_START: i64 = 46_800_000;
const AUCTION_START: i64 = 53_820_000;
fn continuous(t: i64) -> bool {
    (CONT_START..MORNING_END).contains(&t) || (AFTERNOON_START..AUCTION_START).contains(&t)
}
fn json<T: Serialize>(x: &T) -> String { serde_json::to_string(x).unwrap() }

#[derive(Clone, Serialize, Deserialize, Debug)]
struct Event {
    t: i64, biz: i64, channel: i64, kind: i64, side: i64,
    price: i64, qty: i64, oid: i64, bid: i64, ask: i64, status: i64, raw_t: i64,
}
fn events_from(rows: PyReadonlyArray2<'_, i64>) -> PyResult<Vec<Event>> {
    let a = rows.as_array();
    if a.ncols() != 12 { return Err(PyValueError::new_err("事件矩阵须有12列")); }
    let mut previous = i64::MIN;
    let mut events = Vec::with_capacity(a.nrows());
    for r in a.rows() {
        if r[0] < previous { return Err(PyValueError::new_err("事件逻辑时间须递增")); }
        previous = r[0];
        events.push(Event {t:r[0],biz:r[1],channel:r[2],kind:r[3],side:r[4],price:r[5],qty:r[6],oid:r[7],bid:r[8],ask:r[9],status:r[10],raw_t:r[11]});
    }
    Ok(events)
}
#[derive(Clone, Serialize, Deserialize, Debug)]
struct Resting { key: Key, side: i64, price: i64, qty: i64, seq: u64, born: i64 }
#[derive(Clone, Serialize, Deserialize, Default, Debug)]
struct Level { qty: i64, ids: BTreeMap<u64, Key> }
#[derive(Clone, Serialize, Deserialize, Default, Debug)]
struct Book {
    orders: HashMap<Key, Resting>,
    bids: BTreeMap<i64, Level>, asks: BTreeMap<i64, Level>, seq: u64,
}
impl Book {
    fn levels(&self, side: i64) -> &BTreeMap<i64, Level> { if side == 1 { &self.bids } else { &self.asks } }
    fn add(&mut self, key: Key, side: i64, price: i64, qty: i64, born: i64) -> bool {
        if self.orders.contains_key(&key) || qty <= 0 || price <= 0 { return false; }
        self.seq += 1;
        let o = Resting {key, side, price, qty, seq:self.seq, born};
        let levels = if side == 1 { &mut self.bids } else { &mut self.asks };
        let level = levels.entry(price).or_default();
        level.qty += qty; level.ids.insert(o.seq, key);
        self.orders.insert(key, o); true
    }
    fn reduce(&mut self, key: Key, qty: i64) -> (i64, bool) {
        let Some(o) = self.orders.get_mut(&key) else { return (0, false) };
        let taken = qty.min(o.qty).max(0); o.qty -= taken;
        let (side,price,seq,done) = (o.side,o.price,o.seq,o.qty == 0);
        let levels = if side == 1 { &mut self.bids } else { &mut self.asks };
        let level = levels.get_mut(&price).unwrap(); level.qty -= taken;
        if done { level.ids.remove(&seq); self.orders.remove(&key); }
        if level.qty == 0 { levels.remove(&price); }
        (taken, true)
    }
    fn best(&self, side: i64) -> Option<(i64,i64)> {
        if side == 1 { self.bids.last_key_value().map(|(p,l)|(*p,l.qty)) }
        else { self.asks.first_key_value().map(|(p,l)|(*p,l.qty)) }
    }
    fn depth(&self, side: i64, n: usize) -> Vec<(i64,i64,usize)> {
        if side == 1 { self.bids.iter().rev().take(n).map(|(p,l)|(*p,l.qty,l.ids.len())).collect() }
        else { self.asks.iter().take(n).map(|(p,l)|(*p,l.qty,l.ids.len())).collect() }
    }
    fn priority(&self, side: i64) -> Vec<Key> {
        let levels = self.levels(side);
        if side == 1 { levels.iter().rev().flat_map(|(_,l)|l.ids.values().copied()).collect() }
        else { levels.values().flat_map(|l|l.ids.values().copied()).collect() }
    }
    fn state(&self, n: usize) -> serde_json::Value {
        serde_json::json!({"bids":self.depth(1,n),"asks":self.depth(-1,n),"order_count":self.orders.len()})
    }
    fn best_age(&self, side:i64, t:i64) -> (f64,f64) {
        let Some((p,total))=self.best(side) else { return (0.0,0.0) };
        let l=&self.levels(side)[&p]; let mut age=0.0; let mut mature=0;
        for key in l.ids.values() { let o=&self.orders[key]; let a=((t-o.born).max(0)-(t.min(AFTERNOON_START)-o.born.max(MORNING_END)).max(0)).max(0); age+=a as f64*o.qty as f64; if a>=1000 { mature+=o.qty; } }
        (age/(total.max(1) as f64),mature as f64/total.max(1) as f64)
    }
}
#[derive(Clone, Serialize, Deserialize, Default)]
struct Stats {
    adds:u64, cancels:u64, trades:u64, volume:i64, turnover_cents:i64,
    after_trades:u64, after_volume:i64, after_turnover_cents:i64,
    missing_refs:u64, overdraws:u64, duplicate_adds:u64, anomalies:Vec<serde_json::Value>, last_price:i64,
}
#[derive(Clone, Serialize, Deserialize)]
struct ReplayState { book:Book, stats:Stats, pos:usize, time:i64, phase:i64 }
#[pyclass]
struct Replay { events:Vec<Event>, state:ReplayState }
impl Replay {
    fn fresh(events:Vec<Event>)->Self { Self {events,state:ReplayState{book:Book::default(),stats:Stats::default(),pos:0,time:0,phase:0}} }
    fn anomaly(&mut self,e:&Event,key:Key,reason:&str) {
        if self.state.stats.anomalies.len()<100 { self.state.stats.anomalies.push(serde_json::json!({"biz":e.biz,"channel":e.channel,"time":e.raw_t,"order":key.1,"reason":reason})); }
    }
    fn consume(&mut self,e:&Event,key:Key) {
        let (q,found)=self.state.book.reduce(key,e.qty);
        if !found { self.state.stats.missing_refs+=1; self.anomaly(e,key,"关联订单待核验"); }
        else if q<e.qty { self.state.stats.overdraws+=1; self.anomaly(e,key,"成交或撤单数量超过剩余数量"); }
    }
    fn apply(&mut self,e:&Event) {
        self.state.time=e.t;
        match e.kind {
            0 => { self.state.stats.adds+=1; if !self.state.book.add((e.channel,e.oid),e.side,e.price,e.qty,e.t) { self.state.stats.duplicate_adds+=1; self.anomaly(e,(e.channel,e.oid),"新增订单标识或数量待核验"); } },
            1 => { self.state.stats.cancels+=1; self.consume(e,(e.channel,e.oid)); },
            2 => {
                if e.channel==2301 { self.state.stats.after_trades+=1; self.state.stats.after_volume+=e.qty; self.state.stats.after_turnover_cents+=e.price*e.qty; }
                else {
                    self.state.stats.trades+=1; self.state.stats.volume+=e.qty; self.state.stats.turnover_cents+=e.price*e.qty; self.state.stats.last_price=e.price;
                    if e.side==1 { self.consume(e,(e.channel,e.ask)); }
                    else if e.side == -1 { self.consume(e,(e.channel,e.bid)); }
                    else { self.consume(e,(e.channel,e.bid)); self.consume(e,(e.channel,e.ask)); }
                }
            },
            3 => self.state.phase=e.status,
            _=>(),
        }
    }
    fn next(&mut self)->Option<Event> {
        if self.state.pos>=self.events.len() {return None;}
        let e=self.events[self.state.pos].clone(); self.state.pos+=1; self.apply(&e); Some(e)
    }
    fn advance(&mut self,t:i64) {
        while self.state.pos<self.events.len() && self.events[self.state.pos].t<=t {self.next();}
        self.state.time=t;
    }
}
#[pymethods]
impl Replay {
    #[new]
    fn new(rows:PyReadonlyArray2<'_,i64>)->PyResult<Self> { Ok(Self::fresh(events_from(rows)?)) }
    fn advance_to(&mut self,t:i64)->PyResult<String> {
        if t<self.state.time {return Err(PyValueError::new_err("向前推进须采用当前或后续时间；回看可恢复检查点"));}
        self.advance(t); Ok(self.snapshot())
    }
    fn step(&mut self,count:usize)->String { for _ in 0..count { if self.next().is_none() { break; } } self.snapshot() }
    fn snapshot(&self)->String { json(&serde_json::json!({"time":self.state.time,"position":self.state.pos,"total":self.events.len(),"phase":self.state.phase,"book":self.state.book.state(10),"stats":self.state.stats})) }
    fn checkpoint(&self)->String {
        let mut orders:Vec<_>=self.state.book.orders.values().cloned().collect(); orders.sort_by_key(|o|o.seq);
        json(&serde_json::json!({"orders":orders,"stats":self.state.stats,"pos":self.state.pos,"time":self.state.time,"phase":self.state.phase,"seq":self.state.book.seq}))
    }
    fn restore(&mut self,data:&str)->PyResult<()> {
        let v:serde_json::Value=serde_json::from_str(data).map_err(|e|PyValueError::new_err(e.to_string()))?;
        let orders:Vec<Resting>=serde_json::from_value(v["orders"].clone()).map_err(|e|PyValueError::new_err(e.to_string()))?;
        let pos=v["pos"].as_u64().ok_or_else(||PyValueError::new_err("检查点需要事件位置"))? as usize;
        if pos>self.events.len() {return Err(PyValueError::new_err("检查点位置须位于事件范围内"));}
        let mut book=Book::default(); let mut sorted=orders; sorted.sort_by_key(|o|o.seq);
        for o in sorted {let levels=if o.side==1 {&mut book.bids}else{&mut book.asks};let level=levels.entry(o.price).or_default();level.qty+=o.qty;level.ids.insert(o.seq,o.key);book.orders.insert(o.key,o);}
        book.seq=v["seq"].as_u64().unwrap_or(0);
        self.state=ReplayState{book,stats:serde_json::from_value(v["stats"].clone()).map_err(|e|PyValueError::new_err(e.to_string()))?,pos,time:v["time"].as_i64().unwrap(),phase:v["phase"].as_i64().unwrap()}; Ok(())
    }
    fn queue(&self,side:i64,price:i64,limit:usize)->String {
        let rows:Vec<_>=self.state.book.priority(side).into_iter().filter_map(|k| {let o=&self.state.book.orders[&k]; if o.price==price {Some(o)} else {None}}).take(limit).collect(); json(&rows)
    }
    fn reconcile(&mut self, snapshots:PyReadonlyArray2<'_,i64>)->PyResult<String> {
        if self.state.pos>0 {return Err(PyValueError::new_err("快照诊断从完整事件流起点开始"));}
        let a=snapshots.as_array();
        if a.ncols()!=42 {return Err(PyValueError::new_err("核验矩阵须有42列"));}
        let signature=|r:&Replay| -> Vec<i64> {
            let mut key=vec![r.state.stats.volume];
            for side in [1,-1] {let depth=r.state.book.depth(side,10);for i in 0..10 {let item=depth.get(i).copied().unwrap_or((0,0,0));key.push(item.0);key.push(item.1);}}
            key
        };
        let mut indexed:HashMap<Vec<i64>,Vec<(i64,i64)>>=HashMap::new();
        let mut last=signature(self);let mut begin=0;let mut direct=Vec::new();let mut query=0;
        while self.state.pos<self.events.len() {
            let e=self.events[self.state.pos].clone();
            while query<a.nrows() && a[[query,0]]<e.t {direct.push(signature(self));query+=1;}
            self.next();let key=signature(self);
            if key!=last {indexed.entry(last).or_default().push((begin,e.t));last=key;begin=e.t;}
        }
        while query<a.nrows() {direct.push(signature(self));query+=1;}
        indexed.entry(last).or_default().push((begin,86_400_000));
        let mut rows=Vec::new();
        for (i,row) in a.rows().into_iter().enumerate() {
            let t=row[0];let expected=row.iter().skip(1).copied().collect::<Vec<_>>();
            let intervals=indexed.get(&expected);let mut same=false;let mut extended=false;let mut nearest:Option<i64>=None;
            if let Some(intervals)=intervals {for &(start,end) in intervals {
                if start<=t+999 && end>=t {same=true;}
                if start<=t+999 && end>=t-3000 {extended=true;}
                let lag=if t<start {start-t}else if t>end {end-t}else{0};
                if nearest.map(|x|lag.abs()<x.abs()).unwrap_or(true) {nearest=Some(lag);}
            }}
            let got=&direct[i];let best_equal=got[1]==expected[1] && got[21]==expected[21];
            let price_equal=(1..41).step_by(2).all(|j|got[j]==expected[j]);
            rows.push(serde_json::json!({"time":t,"direct":*got==expected,"best":best_equal,"prices":price_equal,"same_second":same,"expanded":extended,"nearest_lag_ms":nearest}));
        }
        Ok(json(&rows))
    }
    fn trade_features(&mut self)->String {
        let mut rows=Vec::new();let mut active:Option<(i64,i64,i64)>=None;let mut packet:Option<serde_json::Value>=None;
        while self.state.pos<self.events.len() {
            let e=self.events[self.state.pos].clone();
            let tag=(e.channel,if e.side==1 {e.bid}else{e.ask},e.side);
            let eligible=e.kind==2&&e.side!=0&&e.channel!=2301&&continuous(e.t);
            if active.is_some()&&(!eligible||Some(tag)!=active) {if let Some(mut p)=packet.take(){p["observed_ms"]=e.t.into();rows.push(p);}active=None;}
            if eligible&&active.is_none() {
                let b=self.state.book.depth(1,2);let a=self.state.book.depth(-1,2);
                if !b.is_empty()&&!a.is_empty() {
                    let contra=if e.side==1 {&a}else{&b};let gap=contra.get(1).map(|x|(x.0-contra[0].0).abs()).unwrap_or(0);
                    packet=Some(serde_json::json!({"t":e.t,"side":e.side,"quantity":0,"active_id":tag.1,"pre_mid":(b[0].0+a[0].0) as f64/2.0,"pre_depth":contra[0].1,"gap":gap,"spread":a[0].0-b[0].0,"biz":e.biz}));active=Some(tag);
                }
            }
            self.next();
            if eligible {if let Some(ref mut p)=packet {
                p["quantity"]=(p["quantity"].as_i64().unwrap()+e.qty).into();
                let b=self.state.book.best(1).unwrap_or((0,0));let a=self.state.book.best(-1).unwrap_or((0,0));
                p["post_mid"]=((b.0+a.0) as f64/2.0).into();p["last_ms"]=e.t.into();
            }}
        }
        if let Some(mut p)=packet {p["observed_ms"]=self.state.time.into();rows.push(p);}
        json(&rows)
    }
    fn samples(&mut self,start:i64,end:i64,interval:i64)->PyResult<String> {
        if start<self.state.time {return Err(PyValueError::new_err("采样从当前或后续时间开始"));}
        if interval<=0 {return Err(PyValueError::new_err("采样间隔须为正数"));}
        let mut out=Vec::new(); let mut t=start;
        while t<=end {
            self.advance(t);
            let b=self.state.book.best(1).unwrap_or((0,0)); let a=self.state.book.best(-1).unwrap_or((0,0));
            let ba=self.state.book.best_age(1,t); let aa=self.state.book.best_age(-1,t);
            let bd=self.state.book.depth(1,10); let ad=self.state.book.depth(-1,10);
            let b2=bd.get(1).map(|x|x.0).unwrap_or(b.0); let a2=ad.get(1).map(|x|x.0).unwrap_or(a.0);
            out.push(serde_json::json!({"t":t,"bid":b.0,"ask":a.0,"bq":b.1,"aq":a.1,"bid2":b2,"ask2":a2,"bdepth":bd.iter().map(|x|x.1).sum::<i64>(),"adepth":ad.iter().map(|x|x.1).sum::<i64>(),"bage":ba.0,"aage":aa.0,"bmature":ba.1,"amature":aa.1,"volume":self.state.stats.volume,"turnover":self.state.stats.turnover_cents,"adds":self.state.stats.adds,"cancels":self.state.stats.cancels,"bids":bd,"asks":ad})); t+=interval;
        }
        Ok(json(&out))
    }
}

#[derive(Clone, Serialize, Deserialize, Debug)]
struct Virtual {
    id:String,side:i64,price:i64,qty:i64,filled:i64,status:String,kind:String,
    arrive:i64,expires:i64,ahead:i64,ahead_keys:HashSet<Key>,seq:u64,
}
#[derive(Clone)]
enum Command { New(Virtual), Cancel(String), Expire(String) }
#[pyclass]
struct PaperEngine {
    replay:Replay, orders:HashMap<String,Virtual>, active:HashSet<String>, commands:BTreeMap<i64,Vec<Command>>,
    used:HashMap<Key,i64>, messages:BTreeMap<i64,Vec<serde_json::Value>>, delivered:Vec<serde_json::Value>,
    now:i64,report_ms:i64,seq:u64,conservative:bool,symbol:i64,preclose:i64,
}
impl PaperEngine {
    fn emit(&mut self,t:i64,id:&str,status:&str,qty:i64,price:i64,reason:&str) {
        self.seq+=1;
        let msg=serde_json::json!({"seq":self.seq,"order_id":id,"status":status,"qty":qty,"price":price,"execution_time":t,"delivery_time":t+self.report_ms,"reason":reason});
        self.messages.entry(t+self.report_ms).or_default().push(msg);
    }
    fn place(&mut self,mut o:Virtual,t:i64) {
        if !continuous(t) { o.status="REJECTED".into(); self.emit(t,&o.id,"REJECTED",0,0,"历史模拟交易采用连续竞价时段"); self.orders.insert(o.id.clone(),o); return; }
        let protect=o.price;
        if o.kind=="OWN_BEST" || o.kind=="OPPOSITE_BEST" {
            let s=if o.kind=="OWN_BEST" {o.side} else {-o.side};
            let Some((p,_))=self.replay.state.book.best(s) else {o.status="CANCELED".into();self.emit(t,&o.id,"CANCELED",0,0,"对应最优报价待形成");self.orders.insert(o.id.clone(),o);return;};
            if (o.side==1 && p>protect)||(o.side==-1 && p<protect) {o.status="CANCELED".into();self.emit(t,&o.id,"CANCELED",0,0,"保护价约束");self.orders.insert(o.id.clone(),o);return;}
            o.price=p;
        }
        if o.kind=="LIMIT" {
            let base=self.replay.state.book.best(-o.side).or_else(||self.replay.state.book.best(o.side)).map(|x|x.0).unwrap_or(if self.replay.state.stats.last_price>0 {self.replay.state.stats.last_price}else{self.preclose});
            let star=self.symbol>=688000;
            let cage=if o.side==1 {((base*102+50)/100).max(if star {0}else{base+10})} else {((base*98+50)/100).min(if star {i64::MAX}else{base-10})};
            if (o.side==1&&o.price>cage)||(o.side==-1&&o.price<cage) {o.status="REJECTED".into();self.emit(t,&o.id,"REJECTED",0,0,"到达时申报价格超出有效范围");self.orders.insert(o.id.clone(),o);return;}
        }
        o.status="OPEN".into(); self.emit(t,&o.id,"OPEN",0,o.price,"");
        let keys=self.replay.state.book.priority(-o.side); let mut visited=Vec::new(); let mut last=0;
        for key in keys {
            let r=&self.replay.state.book.orders[&key];
            if (o.side==1&&r.price>o.price)||(o.side==-1&&r.price<o.price) {break;}
            if !visited.contains(&r.price) {visited.push(r.price);}
            if (o.kind=="BEST5_CANCEL"||o.kind=="BEST5_LIMIT")&&visited.len()>5 {break;}
            let cap=(r.qty-self.used.get(&key).copied().unwrap_or(0)).max(0);
            let q=cap.min(o.qty-o.filled); if q<=0 {continue;}
            let px=r.price; *self.used.entry(key).or_default()+=q; o.filled+=q;last=px;
            self.emit(t,&o.id,if o.filled==o.qty {"FILLED"}else{"PARTIAL"},q,px,"");
            if o.filled==o.qty {break;}
        }
        if o.filled==o.qty {o.status="FILLED".into();}
        else if o.kind=="BEST5_CANCEL" {o.status="CANCELED".into();self.emit(t,&o.id,"CANCELED",0,0,"五档成交后的剩余数量撤销");}
        else {
            if o.kind=="BEST5_LIMIT" {
                if last>0 {o.price=last;} else if let Some((p,_))=self.replay.state.book.best(o.side) {o.price=p;} else {o.status="CANCELED".into();self.emit(t,&o.id,"CANCELED",0,0,"本方报价待形成");self.orders.insert(o.id.clone(),o);return;}
                if (o.side==1&&o.price>protect)||(o.side==-1&&o.price<protect) {o.status="CANCELED".into();self.emit(t,&o.id,"CANCELED",0,0,"剩余转限价受保护价约束");self.orders.insert(o.id.clone(),o);return;}
            }
            o.status=if o.filled>0 {"PARTIAL".into()} else {"OPEN".into()};
            self.emit(t,&o.id,&o.status,0,o.price,"剩余委托有效价格");
            if let Some(l)=self.replay.state.book.levels(o.side).get(&o.price) {
                o.ahead_keys=l.ids.values().copied().collect();
                o.ahead=l.ids.values().map(|k|(self.replay.state.book.orders[k].qty-self.used.get(k).copied().unwrap_or(0)).max(0)).sum();
            }
        }
        if o.status=="OPEN"||o.status=="PARTIAL" {self.active.insert(o.id.clone());}
        self.orders.insert(o.id.clone(),o);
    }
    fn history(&mut self,e:Event) {
        let key=if e.kind==1 {(e.channel,e.oid)} else if e.side==1 {(e.channel,e.ask)} else {(e.channel,e.bid)};
        let prior=self.replay.state.book.orders.get(&key).cloned();
        let used=self.used.get(&key).copied().unwrap_or(0); let absorbed=used.min(e.qty).max(0);
        if (e.kind==1||e.kind==2)&&e.channel!=2301 {if used>0 { self.used.insert(key,used-absorbed); }}
        let mut cap=(e.qty-absorbed).max(0); let original_cap=cap;
        if e.kind==1|| (e.kind==2&&e.side!=0&&e.channel!=2301) {
            let side=if e.kind==1 {prior.as_ref().map(|x|x.side).unwrap_or(0)} else {-e.side};
            let px=if e.kind==1 {prior.as_ref().map(|x|x.price).unwrap_or(0)}else{e.price};
            let mut ids:Vec<String>=self.active.iter().filter(|id|self.orders[*id].side==side).cloned().collect();
            ids.sort_by_key(|id|{let o=&self.orders[id];(-o.side*o.price,o.arrive,o.seq)});
            for id in ids {
                let o=self.orders.get_mut(&id).unwrap(); let same=o.price==px;
                if e.kind==1 {if same&&o.ahead_keys.contains(&key)&&!self.conservative {o.ahead=(o.ahead-cap).max(0);}continue;}
                let better=(o.side==1&&o.price>px)||(o.side==-1&&o.price<px);
                if !same&&!better {continue;}
                let mut available=cap;
                if same {
                    if self.conservative {let ahead=o.ahead.min(available);o.ahead-=ahead;available-=ahead;}
                    else if o.ahead_keys.contains(&key) {o.ahead=(o.ahead-original_cap).max(0);continue;}
                    else if o.ahead>0 {continue;}
                }
                let q=available.min(o.qty-o.filled);if q<=0 {continue;}
                o.filled+=q;cap-=q; let status=if o.filled==o.qty {"FILLED"}else{"PARTIAL"};o.status=status.into();let price=o.price;
                if status=="FILLED" {self.active.remove(&id);}
                self.emit(e.t,&id,status,q,price,"");
            }
        }
        self.replay.state.pos+=1; self.replay.apply(&e);
    }
    fn run(&mut self,to:i64) {
        loop {
            let ht=self.replay.events.get(self.replay.state.pos).map(|e|e.t).unwrap_or(i64::MAX);
            let ct=self.commands.first_key_value().map(|(t,_)|*t).unwrap_or(i64::MAX);
            let t=ht.min(ct);if t==i64::MAX||t>to {break;}
            if ht<=ct {let e=self.replay.events[self.replay.state.pos].clone();self.history(e);}
            else {
                for cmd in self.commands.remove(&ct).unwrap() {
                    match cmd {
                        Command::New(o)=>self.place(o,ct),
                        Command::Cancel(id)=>{
                            if let Some(o)=self.orders.get_mut(&id) {if o.status=="OPEN"||o.status=="PARTIAL" {o.status="CANCELED".into();self.active.remove(&id);self.emit(ct,&id,"CANCELED",0,0,"撤单到达并生效");}}
                        }
                        Command::Expire(id)=>{
                            if let Some(o)=self.orders.get_mut(&id) {if o.status=="OPEN"||o.status=="PARTIAL" {o.status="EXPIRED".into();self.active.remove(&id);self.emit(ct,&id,"EXPIRED",0,0,"有效期或连续竞价阶段结束");}}
                        }
                    }
                }
            }
        }
        self.now=to; self.replay.state.time=to;
        while let Some((&t,_))=self.messages.first_key_value() {if t>to {break;}self.delivered.extend(self.messages.remove(&t).unwrap());}
    }
}
#[pymethods]
impl PaperEngine {
    #[new]
    #[pyo3(signature=(rows,symbol,preclose,report_ms=50,conservative=false))]
    fn new(rows:PyReadonlyArray2<'_,i64>,symbol:i64,preclose:i64,report_ms:i64,conservative:bool)->PyResult<Self> {
        Ok(Self{replay:Replay::fresh(events_from(rows)?),orders:HashMap::new(),active:HashSet::new(),commands:BTreeMap::new(),used:HashMap::new(),messages:BTreeMap::new(),delivered:Vec::new(),now:0,report_ms,seq:0,conservative,symbol,preclose})
    }
    #[pyo3(signature=(id,side,price,qty,arrive,expires,kind="LIMIT"))]
    fn submit(&mut self,id:String,side:i64,price:i64,qty:i64,arrive:i64,expires:i64,kind:&str)->PyResult<()> {
        if !["LIMIT","BEST5_CANCEL","BEST5_LIMIT","OWN_BEST","OPPOSITE_BEST"].contains(&kind) {return Err(PyValueError::new_err("历史模拟采用连续竞价申报类型"));}
        if arrive<self.now||qty<=0||price<=0||(side!=1&&side != -1)||expires<arrive {return Err(PyValueError::new_err("委托参数须满足方向、价格、数量和时间约束"));}
        if self.orders.contains_key(&id)||self.commands.values().flatten().any(|c|matches!(c,Command::New(o) if o.id==id)) {return Err(PyValueError::new_err("委托标识须唯一"));}
        self.seq+=1;let o=Virtual{id:id.clone(),side,price,qty,filled:0,status:"PENDING".into(),kind:kind.into(),arrive,expires,ahead:0,ahead_keys:HashSet::new(),seq:self.seq};
        self.commands.entry(arrive).or_default().push(Command::New(o));self.commands.entry(expires.min(AUCTION_START)).or_default().push(Command::Expire(id));Ok(())
    }
    fn cancel(&mut self,id:String,arrive:i64)->PyResult<()> {if arrive<self.now{return Err(PyValueError::new_err("撤单到达时间须位于当前或后续时间"));}self.commands.entry(arrive).or_default().push(Command::Cancel(id));Ok(())}
    fn advance_to(&mut self,to:i64)->PyResult<String> {if to<self.now{return Err(PyValueError::new_err("回看模拟会话须创建分支"));}self.run(to);Ok(json(&std::mem::take(&mut self.delivered)))}
    fn snapshot(&self)->String {
        let mut orders:Vec<_>=self.orders.values().collect();orders.sort_by_key(|o|o.seq);
        json(&serde_json::json!({"time":self.now,"orders":orders,"market":serde_json::from_str::<serde_json::Value>(&self.replay.snapshot()).unwrap()}))
    }
}

#[pyclass]
struct Exchange {book:Book,next_id:i64,phase:String,close_price:i64}
#[pymethods]
impl Exchange {
    #[new]
    fn new()->Self {Self{book:Book::default(),next_id:1_000_000_000,phase:"CONTINUOUS".into(),close_price:0}}
    fn set_phase(&mut self,phase:&str,close_price:i64)->PyResult<()> {
        if !["CONTINUOUS","OPEN_AUCTION","CLOSE_AUCTION","AFTER_HOURS"].contains(&phase) {return Err(PyValueError::new_err("请选择连续、开盘集合、收盘集合或盘后阶段"));}
        if phase=="AFTER_HOURS" && close_price<=0 {return Err(PyValueError::new_err("盘后撮合需要收盘价"));}
        if phase=="AFTER_HOURS"&&self.phase=="AFTER_HOURS"&&close_price!=self.close_price {return Err(PyValueError::new_err("同一盘后阶段采用固定收盘价"));}
        if phase!=self.phase&&(phase=="AFTER_HOURS"||self.phase=="AFTER_HOURS") {self.book=Book::default();}
        self.phase=phase.into();self.close_price=close_price;Ok(())
    }
    fn seed(&mut self,id:i64,side:i64,price:i64,qty:i64)->PyResult<()> {
        let mut price=price;
        if self.phase=="AFTER_HOURS" {if (side==1&&price<self.close_price)||(side==-1&&price>self.close_price) {return Err(PyValueError::new_err("盘后对手保护价须覆盖收盘价"));}price=self.close_price;}
        if !self.book.add((0,id),side,price,qty,0) {return Err(PyValueError::new_err("初始委托需要唯一标识及正价格数量"));}Ok(())}
    #[pyo3(signature=(side,price,qty,kind="LIMIT",external_id=None))]
    fn submit(&mut self,side:i64,price:i64,qty:i64,kind:&str,external_id:Option<i64>)->PyResult<String> {
        if qty<=0||price<=0||(side!=1&&side != -1) {return Err(PyValueError::new_err("方向、价格和数量须有效"));}
        if !["LIMIT","BEST5_CANCEL","BEST5_LIMIT","OWN_BEST","OPPOSITE_BEST","CLOSE_PRICE"].contains(&kind) {return Err(PyValueError::new_err("请选择受支持的申报类型"));}
        if let Some(id)=external_id {if id<=0||self.book.orders.contains_key(&(0,id)) {return Err(PyValueError::new_err("对手委托标识须为独立正整数"));}}
        self.next_id+=1;let id=external_id.unwrap_or(self.next_id);let mut px=price;let mut remaining=qty;let mut fills=Vec::new();let mut status="OPEN";
        if self.phase=="OPEN_AUCTION"||self.phase=="CLOSE_AUCTION" {
            if kind!="LIMIT" {return Err(PyValueError::new_err("集合竞价使用限价申报"));}
            self.book.add((0,id),side,px,qty,0);return Ok(json(&serde_json::json!({"id":id,"status":status,"remaining":qty,"fills":fills})));
        }
        if self.phase=="AFTER_HOURS" {
            if kind!="CLOSE_PRICE" {return Err(PyValueError::new_err("盘后阶段使用收盘定价申报"));}
            if (side==1&&price<self.close_price)||(side==-1&&price>self.close_price) {return Err(PyValueError::new_err("收盘价须满足保护限价"));}px=self.close_price;
        }
        if kind=="OWN_BEST"||kind=="OPPOSITE_BEST" {
            let s=if kind=="OWN_BEST"{side}else{-side};if let Some((p,_))=self.book.best(s) {px=p;}else{return Ok(json(&serde_json::json!({"id":id,"status":"CANCELED","remaining":qty,"fills":fills})));}
            if (side==1&&px>price)||(side==-1&&px<price) {return Ok(json(&serde_json::json!({"id":id,"status":"CANCELED","remaining":qty,"fills":fills})));}
        }
        let mut prices=Vec::new();let mut last=0;
        for key in self.book.priority(-side) {
            let o=&self.book.orders[&key];if (side==1&&o.price>px)||(side==-1&&o.price<px){break;}
            if !prices.contains(&o.price){prices.push(o.price);}if (kind=="BEST5_CANCEL"||kind=="BEST5_LIMIT")&&prices.len()>5{break;}
            let q=remaining.min(o.qty);let p=o.price;let passive=key.1;
            self.book.reduce(key,q);remaining-=q;last=p;fills.push(serde_json::json!({"order_id":id,"passive_id":passive,"price":p,"qty":q}));if remaining==0{break;}
        }
        if remaining==0{status="FILLED";}else if kind=="BEST5_CANCEL"{status="CANCELED";}else{
            if kind=="BEST5_LIMIT"{if last>0{px=last;}else if let Some((p,_))=self.book.best(side){px=p;}else{status="CANCELED";}}
            if (side==1&&px>price)||(side==-1&&px<price){status="CANCELED";}
            if status!="CANCELED"{self.book.add((0,id),side,px,remaining,0);if remaining<qty{status="PARTIAL";}}
        }
        Ok(json(&serde_json::json!({"id":id,"status":status,"price":px,"remaining":remaining,"fills":fills})))
    }
    fn cancel(&mut self,id:i64,qty:i64)->String {let(q,found)=self.book.reduce((0,id),qty);json(&serde_json::json!({"id":id,"canceled":q,"found":found}))}
    fn auction(&mut self)->PyResult<String> {
        if self.phase!="OPEN_AUCTION"&&self.phase!="CLOSE_AUCTION" {return Err(PyValueError::new_err("集合定价适用于集合竞价阶段"));}
        let prices:Vec<i64>=self.book.bids.keys().chain(self.book.asks.keys()).copied().collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        let mut best=0;let mut imbalance=i64::MAX;let mut candidates=Vec::new();
        for p in prices {
            let b:i64=self.book.bids.range(p..).map(|(_,l)|l.qty).sum();let a:i64=self.book.asks.range(..=p).map(|(_,l)|l.qty).sum();let v=b.min(a);let im=(b-a).abs();
            let better_b:i64=self.book.bids.range((std::ops::Bound::Excluded(p),std::ops::Bound::Unbounded)).map(|(_,l)|l.qty).sum();
            let better_a:i64=self.book.asks.range(..p).map(|(_,l)|l.qty).sum();
            if better_b>v||better_a>v {continue;}
            if v>best||(v==best&&im<imbalance){best=v;imbalance=im;candidates.clear();candidates.push(p);}else if v==best&&im==imbalance{candidates.push(p);}
        }
        if best==0||candidates.is_empty(){return Ok(json(&serde_json::json!({"price":0,"volume":0,"fills":[]})));}
        let p=(candidates[0]+candidates[candidates.len()-1]+1)/2;let mut left=best;let mut fills=Vec::new();
        while left>0 {
            let bk=self.book.priority(1)[0];let ak=self.book.priority(-1)[0];let q=left.min(self.book.orders[&bk].qty).min(self.book.orders[&ak].qty);
            self.book.reduce(bk,q);self.book.reduce(ak,q);left-=q;fills.push(serde_json::json!({"bid":bk.1,"ask":ak.1,"qty":q,"price":p}));
        }
        self.close_price=p;Ok(json(&serde_json::json!({"price":p,"volume":best,"fills":fills})))
    }
    fn queue(&self,side:i64,price:i64,limit:usize)->String {
        let rows:Vec<_>=self.book.priority(side).into_iter().filter_map(|k| {let o=&self.book.orders[&k];if o.price==price {Some(o)}else{None}}).take(limit).collect();json(&rows)
    }
    fn snapshot(&self)->String {json(&serde_json::json!({"phase":self.phase,"close_price":self.close_price,"book":self.book.state(10)}))}
}

#[pymodule]
fn _core(m:&Bound<'_,PyModule>)->PyResult<()> {m.add_class::<Replay>()?;m.add_class::<PaperEngine>()?;m.add_class::<Exchange>()?;Ok(())}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn cancellation_conserves_book() {let mut b=Book::default();assert!(b.add((1,1),1,1000,500,0));assert_eq!(b.reduce((1,1),200),(200,true));assert_eq!(b.best(1),Some((1000,300)));assert_eq!(b.reduce((1,1),500),(300,true));assert!(b.orders.is_empty());assert!(b.bids.is_empty());}
    #[test] fn fifo_and_sweep() {let mut e=Exchange::new();e.seed(1,-1,1000,300).unwrap();e.seed(2,-1,1000,400).unwrap();let v:serde_json::Value=serde_json::from_str(&e.submit(1,1000,500,"LIMIT",None).unwrap()).unwrap();assert_eq!(v["fills"][0]["passive_id"],1);assert_eq!(v["fills"][1]["qty"],200);assert_eq!(e.book.best(-1),Some((1000,200)));}
    #[test] fn auction_price_and_quantity() {let mut e=Exchange::new();e.set_phase("OPEN_AUCTION",0).unwrap();e.seed(1,1,1002,500).unwrap();e.seed(2,-1,1000,300).unwrap();let v:serde_json::Value=serde_json::from_str(&e.auction().unwrap()).unwrap();assert_eq!(v["volume"],300);assert_eq!(v["price"],1002);}
    #[test] fn deterministic_resume() {let es=vec![Event{t:1,biz:1,channel:1,kind:0,side:1,price:1000,qty:300,oid:4,bid:0,ask:0,status:0,raw_t:1}];let mut r=Replay::fresh(es);r.advance(1);let cp=r.checkpoint();r.restore(&cp).unwrap();assert_eq!(r.state.book.best(1),Some((1000,300)));}
}
