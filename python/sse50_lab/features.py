"""One-second causal book features with explicit session-bounded labels."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from ._core import Replay
from .data import DataStore,clock_ms,continuous

HORIZONS=[1,5,30,60]
def split(t):
    if clock_ms('09:35:00')<=t<clock_ms('11:28:00'):return 'calibration'
    if clock_ms('13:05:00')<=t<clock_ms('13:58:00'):return 'validation'
    if clock_ms('14:02:00')<=t<clock_ms('14:57:00'):return 'holdout'
    return 'context'

def build_features(data:DataStore,symbol:int,refresh=False):
    path=data.cache/f'{symbol}/features.parquet';packet_path=path.with_name('packets.parquet')
    if path.exists() and packet_path.exists() and not refresh:return pd.read_parquet(path),pd.read_parquet(packet_path)
    events=data.events(symbol)
    f=pd.DataFrame(json.loads(Replay(events).samples(clock_ms('09:30:00'),clock_ms('14:57:00')-1000,1000)))
    f=f[continuous(f.t)&(f.bid>0)&(f.ask>f.bid)].reset_index(drop=True)
    f['symbol']=symbol;f['mid']=(f.bid+f.ask)/2;f['spread_tick']=f.ask-f.bid;f['spread_bp']=f.spread_tick/f.mid*10000
    f['imbalance']=(f.bq-f.aq)/(f.bq+f.aq).clip(lower=1);f['depth']=f.bq+f.aq
    f['gap']=(f.bid-f.bid2).clip(lower=0)+(f.ask2-f.ask).clip(lower=0)
    f['mature']=(f.bmature*f.bq+f.amature*f.aq)/f.depth.clip(lower=1)
    f['age_ms']=(f.bage*f.bq+f.aage*f.aq)/f.depth.clip(lower=1)
    f['split']=[split(int(t)) for t in f.t];f['block']=f.t//300000;f['session']=(f.t>=clock_ms('13:00:00')).astype(int)
    t=f.t.to_numpy();m=f.mid.to_numpy();b=f.bid.to_numpy();q=f.depth.to_numpy()
    for h in HORIZONS:
        i=np.searchsorted(t,t+h*1000);safe=np.minimum(i,len(t)-1);valid=(i<len(t))&(t[safe]==t+h*1000)&continuous(t+h*1000)
        f[f'mid_{h}']=np.where(valid,m[safe],np.nan);f[f'ret_{h}']=np.where(valid,(m[safe]-m)/m*10000,np.nan)
        f[f'depth_ratio_{h}']=np.where(valid,q[safe]/q.clip(min=1),np.nan)
        f[f'bid_down_{h}']=np.where(valid,b[safe]<b,np.nan)
    for name,mask in [('buy_volume',(events[:,3]==2)&(events[:,4]==1)&(events[:,2]!=2301)),('sell_volume',(events[:,3]==2)&(events[:,4]==-1)&(events[:,2]!=2301)),('cancel_volume',events[:,3]==1),('add_volume',events[:,3]==0)]:
        e=events[mask];bins=((e[:,0]+999)//1000)*1000;i=np.searchsorted(t,bins);v=np.minimum(i,len(t)-1);valid=(i<len(t))&(t[v]==bins)
        values=np.zeros(len(t));np.add.at(values,i[valid],e[valid,6]);f[name]=values
    for col in ['buy_volume','sell_volume','cancel_volume','add_volume']:
        for h in [5,60]:f[f'{col}_{h}']=f.groupby('session')[col].transform(lambda x:x.rolling(h,min_periods=1).sum())
    f['trade_volume_60']=f.buy_volume_60+f.sell_volume_60
    f['momentum_5']=f.groupby('session').mid.pct_change(5).fillna(0)*10000
    f['volatility_30']=f.groupby('session').mid.transform(lambda x:x.pct_change().rolling(30,min_periods=5).std()).fillna(0)*10000
    f['cancel_pressure']=f.cancel_volume_5/(f.add_volume_5+f.cancel_volume_5).clip(lower=1)
    previous=f.groupby('session').depth.shift(1)
    f['replenishing']=((f.buy_volume+f.sell_volume)>0)&(f.depth>=previous)&(f.groupby('session').bid.diff()==0)&(f.groupby('session').ask.diff()==0)
    f.to_parquet(path,index=False)
    packets=pd.DataFrame(json.loads(Replay(events).trade_features()));packets['symbol']=symbol
    if len(packets):
        packets['split']=[split(int(t)) for t in packets.observed_ms];packets['impact_ticks']=(packets.post_mid-packets.pre_mid)*packets.side
        packets['jump_2tick']=(packets.impact_ticks>=2).astype(float);packets['relative_size']=packets.quantity/packets.pre_depth.clip(lower=1)
        packets['block']=packets.observed_ms//300000
    packets.to_parquet(packet_path,index=False)
    print(f'features {symbol}: {len(f):,} one-second books, {len(packets):,} aggressor packets',flush=True)
    return f,packets
