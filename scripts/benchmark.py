"""Measure checkpoint seeks and compare restored states with sequential replay."""
from pathlib import Path
import json,time,resource,platform
import numpy as np
from sse50_lab.data import DataStore,clock_ms,dump
from sse50_lab._core import Replay
root=Path(__file__).resolve().parents[1]
data=DataStore(root/'data');times=[clock_ms(t) for t in ['09:45:15','10:17:37','11:26:11','13:27:43','14:55:19']]
latencies=[];consistent=[];began=time.perf_counter()
for symbol in data.symbols:
    data.replay(symbol,times[0])
    seq=Replay(data.events(symbol))
    for t in times:
        s=time.perf_counter();restored=data.replay(symbol,t);latencies.append((time.perf_counter()-s)*1000)
        expected=json.loads(seq.advance_to(t));actual=json.loads(restored.snapshot())
        consistent.append(actual==expected)
result={'machine':platform.platform(),'architecture':platform.machine(),'samples':len(latencies),'stocks':len(data.symbols),'warm_seek_ms':{'p50':float(np.percentile(latencies,50)),'p95':float(np.percentile(latencies,95)),'max':max(latencies)},'checkpoint_state_equal':sum(consistent),'checkpoint_state_total':len(consistent),'seconds':time.perf_counter()-began,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'scope':'5 intraday times per stock; 5-minute compressed checkpoints; warm filesystem cache; exact decoded state equality'}
dump(root/'evidence/benchmark.json',result);print(json.dumps(result,indent=2))
