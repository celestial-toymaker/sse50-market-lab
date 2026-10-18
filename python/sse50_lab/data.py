"""Verified course data, causal event ordering and bounded replay checkpoints."""
from __future__ import annotations
import gzip
import hashlib
import json
import math
import time
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import requests

URL = 'https://github.com/aslan9/pku_quantllm/releases/download/data-sse50-20260923/sse50_20260923.zip'
SHA256 = '18c0e124a9c68eb2f77b965ca1f29c93bb30339d7db45245e1af3c0420c6c947'
DATE = '2026-09-23'
EVENT_COLUMNS = ['logical_ms','biz_index','channel','kind','side','price_cents','quantity','order_id','bid_id','ask_id','phase','raw_time']
SOURCE_NAMES = ['ord_20260923.parquet','exe_20260923.parquet','snp_20260923.parquet']

def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+'.part')
    def clean(v):
        if isinstance(v,np.generic):return clean(v.item())
        if isinstance(v,float) and not math.isfinite(v):return None
        if isinstance(v,dict):return {str(k):clean(x) for k,x in v.items()}
        if isinstance(v,(list,tuple)):return [clean(x) for x in v]
        if isinstance(v,np.ndarray):return clean(v.tolist())
        if isinstance(v,Path):return str(v)
        return v
    temp.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)

def _json(v):
    if isinstance(v,np.generic): return v.item()
    if isinstance(v,Path): return str(v)
    raise TypeError(type(v).__name__)

def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(4*1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def raw_ms(t):
    a=np.asarray(t,dtype=np.int64)
    return (a//10000000*3600+(a//100000)%100*60+(a//1000)%100)*1000+a%1000

def clock_ms(text: str | int) -> int:
    if isinstance(text,int): return text
    parts=text.split(':')
    if len(parts)!=3: raise ValueError('时间格式为 HH:MM:SS 或 HH:MM:SS.sss')
    h,m=int(parts[0]),int(parts[1]); sec=float(parts[2])
    if not(0<=h<24 and 0<=m<60 and 0<=sec<60): raise ValueError('时间须位于一天之内')
    return int(round((h*3600+m*60+sec)*1000))

def clock_text(t: int) -> str:
    return f'{t//3600000:02d}:{t//60000%60:02d}:{t//1000%60:02d}.{t%1000:03d}'

def continuous(t):
    a=np.asarray(t)
    return ((a>=clock_ms('09:30:00'))&(a<clock_ms('11:30:00')))|((a>=clock_ms('13:00:00'))&(a<clock_ms('14:57:00')))

def fetch(data_dir: Path) -> dict:
    data_dir=Path(data_dir); data_dir.mkdir(parents=True,exist_ok=True)
    archive=data_dir/'sse50_20260923.zip'
    if not archive.exists() or digest(archive)!=SHA256:
        part=archive.with_suffix('.zip.part'); h=hashlib.sha256()
        with requests.get(URL,stream=True,timeout=(20,120)) as response:
            response.raise_for_status()
            with part.open('wb') as f:
                for chunk in response.iter_content(4*1024*1024):
                    if chunk: f.write(chunk); h.update(chunk)
        if h.hexdigest()!=SHA256: raise ValueError('数据包校验值须与课程发布值一致')
        part.replace(archive)
    source=data_dir/'source'; source.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for name in SOURCE_NAMES:
            member=f'sse50_20260923/{name}'
            target=source/name
            if not target.exists() or target.stat().st_size!=z.getinfo(member).file_size:
                target.write_bytes(z.read(member))
    manifest={'date':DATE,'url':URL,'archive_sha256':digest(archive),'archive_bytes':archive.stat().st_size,
              'files':{n:{'sha256':digest(source/n),'rows':pq.ParquetFile(source/n).metadata.num_rows,
                          'fields':{f.name:str(f.type) for f in pq.ParquetFile(source/n).schema_arrow}} for n in SOURCE_NAMES}}
    dump(data_dir/'source_manifest.json',manifest)
    return manifest

def normalize_orders(o: pd.DataFrame) -> np.ndarray:
    n=len(o); a=np.zeros((n,12),dtype=np.int64)
    a[:,0]=raw_ms(o.Time); a[:,1]=o.BizIndex; a[:,2]=o.Channel
    a[:,3]=np.where(o.OrderKind==65,0,np.where(o.OrderKind==68,1,3))
    a[:,4]=np.where(o.FunctionCode==66,1,np.where(o.FunctionCode==83,-1,0))
    a[:,5]=np.rint(o.Price.to_numpy(dtype=np.float64)*100).astype(np.int64)
    a[:,6]=o.Volume; a[:,7]=o.OrderOriNo; a[:,10]=np.where(a[:,3]==3,o.FunctionCode,0); a[:,11]=o.Time
    return a

def normalize_trades(e: pd.DataFrame) -> np.ndarray:
    a=np.zeros((len(e),12),dtype=np.int64)
    a[:,0]=raw_ms(e.Time); a[:,1]=e.BizIndex; a[:,2]=e.Channel; a[:,3]=2
    a[:,4]=np.where(e.BSFlag==66,1,np.where(e.BSFlag==83,-1,0))
    a[:,5]=np.rint(e.Price.to_numpy(dtype=np.float64)*100).astype(np.int64)
    a[:,6]=e.Volume; a[:,8]=e.BidOrder; a[:,9]=e.AskOrder; a[:,11]=e.Time
    return a

def sort_events(a: np.ndarray) -> tuple[np.ndarray,int]:
    a=a[np.lexsort((a[:,3],a[:,1],a[:,2]))].copy()
    retreats=0
    for channel in np.unique(a[:,2]):
        mask=a[:,2]==channel; t=a[mask,0]; retreats+=int(np.count_nonzero(np.diff(t)<0))
        a[mask,0]=np.maximum.accumulate(t)
    return np.ascontiguousarray(a[np.lexsort((a[:,3],a[:,1],a[:,2],a[:,0]))]),retreats

class DataStore:
    def __init__(self,data_dir: Path):
        self.root=Path(data_dir).resolve(); self.cache=self.root/'cache'
        self.manifest=json.loads((self.cache/'manifest.json').read_text()) if (self.cache/'manifest.json').exists() else {}
        self._events={}
    @property
    def symbols(self): return self.manifest.get('symbols',[])
    def events(self,symbol: int):
        symbol=int(symbol)
        if symbol not in self.symbols:raise ValueError('请选择数据集中的股票代码')
        if symbol not in self._events:
            if len(self._events)>=4: self._events.pop(next(iter(self._events)))
            self._events[symbol]=np.load(self.cache/f'{symbol}/events.npy',mmap_mode='r')
        return self._events[symbol]
    def lifecycle(self,symbol:int,channel:int,oid:int,to:int):
        a=self.events(symbol);visible=a[(a[:,0]<=to)&(a[:,2]==channel)]
        rows=visible[((visible[:,3]<2)&(visible[:,7]==oid))|((visible[:,3]==2)&((visible[:,8]==oid)|(visible[:,9]==oid)))]
        remaining=0;traded=0;canceled=0;added=0;limit_price=None
        records=[]
        for row in rows:
            kind=int(row[3]);quantity=int(row[6]);role=''
            if kind==0:remaining+=quantity;added+=quantity;limit_price=int(row[5])
            elif kind==1:remaining-=quantity;canceled+=quantity
            else:
                traded+=quantity
                passive=(row[4]==1 and row[9]==oid) or (row[4]==-1 and row[8]==oid) or row[4]==0
                role='PASSIVE' if passive else 'AGGRESSIVE'
                if passive:remaining-=quantity
            records.append({'time_ms':int(row[0]),'biz_index':int(row[1]),'kind':['ADD','CANCEL','TRADE','PHASE'][kind],'side':int(row[4]),'price_cents':int(row[5]),'quantity':quantity,'role':role,'visible_remaining':max(0,remaining)})
        return {'symbol':symbol,'channel':channel,'order_id':oid,'asof_ms':to,'row_count':len(records),'records':records[-1000:],'observed_traded':traded,'observed_canceled':canceled,'observed_added_residual':added,'visible_remaining':max(0,remaining),'observed_limit_price':limit_price,'scope':'截至查询时刻可见的订单、成交与撤单；成交后剩余挂单另由ADD发布。'}
    def snapshots(self,symbol: int): return pq.read_table(self.cache/f'{int(symbol)}/snapshots.parquet').to_pandas()
    def metadata(self,symbol: int): return self.manifest['instruments'][str(symbol)]
    def replay(self,symbol: int,to: int | None=None):
        from ._core import Replay
        r=Replay(self.events(symbol))
        if to is not None:
            directory=self.cache/f'{symbol}/checkpoints'
            choices=sorted(int(p.stem.split('.')[0]) for p in directory.glob('*.json.gz') if int(p.stem.split('.')[0])<=to)
            if choices:
                with gzip.open(directory/f'{choices[-1]}.json.gz','rt') as f: r.restore(f.read())
            r.advance_to(to)
        return r

def prepare(data_dir: Path,force: bool=False) -> dict:
    from ._core import Replay
    data_dir=Path(data_dir); cache=data_dir/'cache'; cache.mkdir(parents=True,exist_ok=True)
    if (cache/'manifest.json').exists() and not force:
        previous=json.loads((cache/'manifest.json').read_text())
        if previous.get('version')!=2:raise ValueError('缓存版本已更新，请运行 sse50 prepare --force')
        for name,info in previous['source']['files'].items():
            if digest(data_dir/'source'/name)!=info['sha256']:raise ValueError('原始文件校验失败，请重新获取课程数据')
        for symbol in previous['symbols']:
            info=previous['instruments'][str(symbol)]
            if digest(cache/f'{symbol}/events.npy')!=info['events_sha256']:raise ValueError('事件缓存校验失败，请重新准备数据')
        return previous
    manifest=fetch(data_dir); start=time.perf_counter()
    orders=pq.read_table(data_dir/'source'/SOURCE_NAMES[0]).to_pandas()
    trades=pq.read_table(data_dir/'source'/SOURCE_NAMES[1]).to_pandas()
    snaps=pq.read_table(data_dir/'source'/SOURCE_NAMES[2]).to_pandas()
    symbols=sorted(int(s) for s in orders.Symbol.unique()); instruments={}; total=0
    for symbol in symbols:
        started=time.perf_counter(); directory=cache/str(symbol); directory.mkdir(exist_ok=True)
        o=orders[orders.Symbol==symbol]; e=trades[trades.Symbol==symbol]; s=snaps[snaps.Symbol==symbol].sort_values('Time')
        a,retreats=sort_events(np.concatenate([normalize_orders(o),normalize_trades(e)]))
        np.save(directory/'events.npy',a); s.to_parquet(directory/'snapshots.parquet',index=False)
        preclose=int(round(float(s.PreClose.iloc[0])*100)); total+=len(a)
        instr={'symbol':symbol,'board':'STAR' if symbol>=688000 else 'MAIN','preclose':preclose,'rows':len(a),'orders':len(o),'trades':len(e),'snapshots':len(s),'timestamp_retreats':retreats,'start_ms':int(a[0,0]),'end_ms':int(a[-1,0]),'events_sha256':digest(directory/'events.npy')}
        r=Replay(a); cp=directory/'checkpoints'; cp.mkdir(exist_ok=True)
        for t in range(clock_ms('09:15:00'),clock_ms('15:30:00')+1,300000):
            r.advance_to(t)
            with gzip.open(cp/f'{t}.json.gz','wt',compresslevel=3) as f: f.write(r.checkpoint())
        instr['final']=json.loads(r.snapshot()); instr['prepare_seconds']=time.perf_counter()-started
        instruments[str(symbol)]=instr
        print(f'prepared {symbol}: {len(a):,} events, {instr["prepare_seconds"]:.2f}s',flush=True)
    result={'version':2,'ordering_scope':'security + channel BizIndex; deterministic cross-security clock; original timestamps retained','date':DATE,'symbols':symbols,'instruments':instruments,'event_columns':EVENT_COLUMNS,'source':manifest,'total_events':total,'prepare_seconds':time.perf_counter()-start}
    dump(cache/'manifest.json',result);return result
