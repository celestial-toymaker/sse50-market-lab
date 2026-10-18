"""Preserve the distinction between per-security order and cross-security labels."""
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
from sse50_lab.data import dump,raw_ms
root=Path(__file__).resolve().parents[1]
o=pq.read_table(root/'data/source/ord_20260923.parquet',columns=['Channel','BizIndex','Time','OrderKind','Symbol']).to_pandas()
e=pq.read_table(root/'data/source/exe_20260923.parquet',columns=['Channel','BizIndex','Time','Symbol']).to_pandas()
rows=[]
for channel in sorted(set(o.Channel)|set(e.Channel)):
    a=o[(o.Channel==channel)&(o.OrderKind!=83)];b=e[e.Channel==channel]
    biz=np.r_[a.BizIndex,b.BizIndex];raw=np.r_[a.Time,b.Time];symbol=np.r_[a.Symbol,b.Symbol];idx=np.argsort(biz,kind='stable')
    t=raw_ms(raw[idx]);sy=symbol[idx];bi=biz[idx];rr=raw[idx]
    reg=np.flatnonzero(np.diff(t)<0)
    cont=(t>=34200000)&(t<53820000);ci=np.flatnonzero(cont);ct=t[ci];r2=np.flatnonzero(np.diff(ct)<0)
    rows.append({'channel':int(channel),'cross_security_regressions':len(reg),'continuous_regressions':len(r2),'examples':[{'before':{'symbol':int(sy[i]),'biz':int(bi[i]),'time':int(rr[i])},'after':{'symbol':int(sy[i+1]),'biz':int(bi[i+1]),'time':int(rr[i+1])},'difference_ms':int(t[i+1]-t[i])} for i in reg[:3]]})
dump(root/'evidence/channel_clock.json',{'ordering':'Per-security channel business order; raw clock labels retained. Cross-security scheduling uses timestamp, symbol and local event order.','rows':rows})
print(rows)
