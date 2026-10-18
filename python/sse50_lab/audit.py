"""Independent price/volume/auction checks and snapshot clock diagnostics."""
from __future__ import annotations
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
from ._core import Exchange,Replay
from .data import DataStore,clock_ms,continuous,dump,raw_ms

def snapshot_matrix(s):
    a=np.zeros((len(s),42),dtype=np.int64);a[:,0]=raw_ms(s.Time);a[:,1]=s.AccVolume
    j=2
    for side in ['Bid','Ask']:
        for level in range(1,11):
            a[:,j]=np.rint(s[f'{side}Price{level}'].to_numpy(dtype=np.float64)*100).astype(np.int64)
            a[:,j+1]=np.rint(s[f'{side}Volume{level}']).astype(np.int64);j+=2
    return np.ascontiguousarray(a)

def auction_check(events,time_ms):
    r=Replay(events);r.advance_to(time_ms-1);book=json.loads(r.checkpoint())['orders'];ex=Exchange();ex.set_phase('OPEN_AUCTION',0)
    for o in sorted(book,key=lambda x:x['seq']):ex.seed(o['key'][1],o['side'],o['price'],o['qty'])
    got=json.loads(ex.auction());actual=events[(events[:,3]==2)&(events[:,4]==0)&(events[:,0]==time_ms)]
    expected=int(actual[:,6].sum()) if len(actual) else 0
    pairs=sorted((int(row[8]),int(row[9]),int(row[6]),int(row[5])) for row in actual)
    actual_pairs=sorted((f['bid'],f['ask'],f['qty'],f['price']) for f in got['fills'])
    return {'price':got['price'],'volume':got['volume'],'expected_volume':expected,'price_volume_equal':got['volume']==expected and (expected==0 or got['price']==int(actual[0,5])),'pairs_equal':pairs==actual_pairs}

def run_audit(data_dir:Path,out:Path):
    data=DataStore(data_dir);out=Path(out);out.mkdir(parents=True,exist_ok=True);start=time.perf_counter();results=[];diagnostics=[]
    for symbol in data.symbols:
        began=time.perf_counter();events=data.events(symbol);s=data.snapshots(symbol)
        r=Replay(events);final=json.loads(r.advance_to(clock_ms('15:30:10')));stats=final['stats']
        ordinary=s[(s.Time%1000==0)&((s.AskPrice2>0)|(s.BidPrice2>0))].iloc[-1]
        end_equal=True;diff=[]
        for side in ['Bid','Ask']:
            depth=final['book']['bids' if side=='Bid' else 'asks']
            for level in range(1,11):
                expected=(int(round(float(ordinary[f'{side}Price{level}'])*100)),int(ordinary[f'{side}Volume{level}']))
                got=tuple(depth[level-1][:2]) if len(depth)>=level else (0,0)
                if got!=expected:end_equal=False;diff.append({'side':side,'level':level,'expected':expected,'reconstructed':got})
        comp=s[continuous(raw_ms(s.Time))& (s.BidPrice1>0)&(s.AskPrice1>0)].sort_values('Time')
        diag=pd.DataFrame(json.loads(Replay(events).reconcile(snapshot_matrix(comp))))
        diag['symbol']=symbol;diagnostics.append(diag)
        row={'symbol':symbol,'events':len(events),'missing_refs':stats['missing_refs'],'overdraws':stats['overdraws'],'duplicate_adds':stats['duplicate_adds'],
             'volume_equal':stats['volume']==int(ordinary.AccVolume),'after_volume_equal':stats['after_volume']==int(s.AfterVolume.max()),
             'turnover_error_yuan':abs(stats['turnover_cents']/100-float(ordinary.AccTurnover)),
             'after_turnover_error_yuan':abs(stats['after_turnover_cents']/100-float(s.AfterTurnover.max())),
             'closing_depth_equal':end_equal,'closing_differences':diff,'snapshot_count':len(diag),
             'direct_rate':float(diag.direct.mean()),'best_rate':float(diag.best.mean()),'prices_rate':float(diag.prices.mean()),
             'same_second_rate':float(diag.same_second.mean()),'expanded_rate':float(diag.expanded.mean()),
             'opening':auction_check(events,clock_ms('09:25:00')),'closing':auction_check(events,clock_ms('15:00:00')),
             'anomalies':stats['anomalies'],'seconds':time.perf_counter()-began}
        results.append(row);print(f'audit {symbol}: refs={row["missing_refs"]}, end={end_equal}, same-second={row["same_second_rate"]:.3%}',flush=True)
    diag=pd.concat(diagnostics,ignore_index=True);diag.to_parquet(out/'snapshot_checks.parquet',index=False)
    diag[~diag.same_second].head(500).to_csv(out/'snapshot_exceptions.csv',index=False)
    structural=all(r['missing_refs']==r['overdraws']==r['duplicate_adds']==0 for r in results)
    summary={'status':'complete','symbols':len(results),'total_events':sum(r['events'] for r in results),'structural_pass':structural,
             'closing_depth_pass':sum(r['closing_depth_equal'] for r in results),'volume_pass':sum(r['volume_equal'] for r in results),'after_volume_pass':sum(r['after_volume_equal'] for r in results),
             'opening_pass':sum(r['opening']['price_volume_equal'] and r['opening']['pairs_equal'] for r in results),
             'closing_pass':sum(r['closing']['price_volume_equal'] and r['closing']['pairs_equal'] for r in results),
             'snapshot_count':len(diag),'direct_rate':float(diag.direct.mean()),'same_second_rate':float(diag.same_second.mean()),'expanded_rate':float(diag.expanded.mean()),
             'seconds':time.perf_counter()-start,'rows':results,
             'alignment_note':'快照缺少频道业务水位。同秒及扩窗匹配属于离线时间映射诊断；保留直接匹配原始结果，策略采用逐笔事件的因果顺序。'}
    dump(out/'audit.json',summary);return summary
