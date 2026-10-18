"""Validated configuration shared by command line, API and research."""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import yaml
from .account import FeeSchedule

ROOT=Path(__file__).resolve().parents[2]

def load_config(path:Path|None=None):
    default=ROOT/'configs/default.yaml'
    config=yaml.safe_load(default.read_text())
    if path is not None:
        incoming=yaml.safe_load(Path(path).read_text()) or {}
        def merge(a,b):
            for key,value in b.items():
                if isinstance(value,dict) and isinstance(a.get(key),dict):merge(a[key],value)
                else:a[key]=value
        merge(config,incoming)
    if str(config['date'])!='2026-09-23':raise ValueError('课程数据日期为2026-09-23')
    if config['device'] not in ['auto','mps','cuda','cpu']:raise ValueError('计算设备须为auto、mps、cuda或cpu')
    if any(not 0<=int(v)<=5000 for v in config['latency'].values()):raise ValueError('延迟参数范围为0至5000毫秒')
    if config['research']['horizons_s']!=[1,5,30,60] or config['research']['deadline_s']!=60:raise ValueError('本版冻结研究协议采用1/5/30/60秒期限及60秒截止')
    for key,expected in [('calibration',['09:35:00','11:28:00']),('validation',['13:05:00','13:58:00']),('holdout',['14:02:00','14:57:00'])]:
        if config['research'][key]!=expected:raise ValueError('研究时段须与冻结协议一致')
    if config['research']['parent_yuan']<=0 or config['research']['cash_yuan']<=0 or config['research']['inventory_parents']<1:raise ValueError('研究账户及母单预算须为正')
    if config['research']['bootstrap_blocks']!=1000:raise ValueError('本版冻结研究协议采用1000次重采样')
    fee_schedule(config)
    return config

def fee_schedule(config):
    f=config['fees']
    if not f['commission_includes_exchange_and_regulatory']:raise ValueError('佣金配置采用包含交易规费的口径')
    values=[Decimal(str(f[k])) for k in ['commission_rate','commission_min_yuan','transfer_rate','sell_stamp_rate']]
    if any(v<0 for v in values):raise ValueError('费率及最低佣金须非负')
    return FeeSchedule(commission_rate=values[0],commission_min_cents=int(values[1]*100),transfer_rate=values[2],sell_stamp_rate=values[3])
