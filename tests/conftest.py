import json
from pathlib import Path
import numpy as np
import pytest

BASE=34200000

def event(t,kind,side=1,price=1000,qty=100,oid=1,bid=0,ask=0,channel=1001,biz=1):
    return [BASE+t,biz,channel,kind,side,price,qty,oid,bid,ask,0,93000000+t]

@pytest.fixture
def event_factory():return event

@pytest.fixture
def tiny_store(tmp_path):
    from sse50_lab.data import DataStore
    root=tmp_path/'data';directory=root/'cache/600036';directory.mkdir(parents=True)
    rows=[event(0,0,1,999,500,11,biz=1),event(0,0,-1,1000,300,21,biz=2),event(100,2,1,1000,100,0,bid=30,ask=21,biz=3),event(200,1,-1,1000,100,21,biz=4),event(300,2,1,1000,100,0,bid=31,ask=21,biz=5),event(400,0,-1,1000,300,22,biz=6)]
    np.save(directory/'events.npy',np.asarray(rows,dtype=np.int64))
    (root/'cache/manifest.json').write_text(json.dumps({'symbols':[600036],'instruments':{'600036':{'preclose':1000,'board':'MAIN'}}}))
    return DataStore(root)
