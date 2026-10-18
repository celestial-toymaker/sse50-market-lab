import json
import numpy as np
import pytest
from sse50_lab.data import clock_ms,clock_text,raw_ms,sort_events
from sse50_lab.settings import load_config,fee_schedule

def test_clock_units_and_ordering(event_factory):
    assert int(raw_ms(93000120))==clock_ms('09:30:00.120')
    assert clock_text(clock_ms('13:42:08.123'))=='13:42:08.123'
    a=np.array([event_factory(20,0,biz=1),event_factory(10,0,biz=2)],dtype=np.int64)
    ordered,retreats=sort_events(a)
    assert retreats==1 and ordered[0,0]==ordered[1,0] and ordered[0,1]<ordered[1,1]

def test_configuration_costs_and_freeze(tmp_path):
    p=tmp_path/'config.yaml';p.write_text("device: cpu\nfees:\n  commission_rate: '0.0001'\nresearch:\n  parent_yuan: 30000\n")
    cfg=load_config(p);assert cfg['device']=='cpu' and cfg['research']['parent_yuan']==30000
    assert fee_schedule(cfg).total(10000000,1)==1100
    p.write_text('research:\n  deadline_s: 30\n')
    with pytest.raises(ValueError):load_config(p)

def test_lifecycle_uses_visible_messages(tiny_store):
    from conftest import BASE
    early=tiny_store.lifecycle(600036,1001,21,BASE+100)
    assert early['observed_added_residual']==300 and early['observed_traded']==100 and early['visible_remaining']==200
    final=tiny_store.lifecycle(600036,1001,21,BASE+500)
    assert final['observed_traded']==200 and final['observed_canceled']==100 and final['visible_remaining']==0
    active=tiny_store.lifecycle(600036,1001,30,BASE+100)
    assert active['observed_limit_price'] is None and active['records'][0]['role']=='AGGRESSIVE'
