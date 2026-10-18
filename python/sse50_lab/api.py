from __future__ import annotations
import asyncio
import csv
import io
import json
from pathlib import Path
from fastapi import BackgroundTasks,FastAPI,HTTPException,WebSocket,WebSocketDisconnect
from fastapi.responses import FileResponse,Response
from fastapi.staticfiles import StaticFiles
from .data import DataStore,raw_ms
from .models import AccountState,AdvanceRequest,ExecutionReport,ExperimentConfig,MarketEvent,OrderRequest,PhaseRequest,SeedRequest,SessionRequest
from .service import SessionStore
from .settings import load_config

ROOT=Path(__file__).resolve().parents[2]
def create_app(data_dir:Path|None=None,db_path:Path|None=None,config_path:Path|None=None):
    settings=load_config(config_path)
    data=DataStore(data_dir or ROOT/'data');sessions=SessionStore(data,db_path or ROOT/'work/sessions.sqlite',settings)
    sessions.db.execute('CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, status TEXT, config TEXT, error TEXT)')
    sessions.db.execute("UPDATE experiments SET status='interrupted',error='服务重启后可重新运行' WHERE status='running'");sessions.db.commit()
    app=FastAPI(title='SSE 50 Market Lab',version='0.1.0',description='本地历史回放、模拟交易及微观结构实验')
    app.state.sessions=sessions;app.state.data=data
    @app.exception_handler(ValueError)
    async def value_error(request,exc):return Response(json.dumps({'detail':str(exc)},ensure_ascii=False),status_code=400,media_type='application/json')
    @app.exception_handler(KeyError)
    async def key_error(request,exc):return Response(json.dumps({'detail':'请选择已有会话或数据项'},ensure_ascii=False),status_code=404,media_type='application/json')
    @app.get('/api/health')
    def health():return {'status':'ready' if data.symbols else 'prepare_data','date':'2026-09-23','symbols':len(data.symbols),'version':'0.1.0'}
    @app.get('/api/config')
    def configuration():return settings
    @app.get('/api/instruments')
    def instruments():return [{k:v for k,v in data.metadata(s).items() if k not in ['final']} for s in data.symbols]
    @app.post('/api/sessions')
    def create(config:SessionRequest):
        defaults={'cash_yuan':settings['cash_yuan'],'overnight':settings.get('overnight',{}).get(str(config.symbol),0)}|settings['latency']
        for key,value in defaults.items():
            if key not in config.model_fields_set:setattr(config,key,value)
        return sessions.create(config).snapshot()
    @app.get('/api/sessions')
    def listing():return [{'id':r[0],'config':json.loads(r[1])} for r in sessions.db.execute('SELECT id,config FROM sessions ORDER BY rowid DESC LIMIT 30')]
    @app.get('/api/sessions/{sid}')
    def state(sid:str):
        with sessions.lock:return sessions.get(sid).snapshot()
    @app.post('/api/sessions/{sid}/advance')
    def advance(sid:str,request:AdvanceRequest):return sessions.command(sid,'advance',request.model_dump())
    @app.post('/api/sessions/{sid}/step')
    def step(sid:str):
        import numpy as np
        current=sessions.get(sid);a=data.events(current.symbol);i=int(np.searchsorted(a[:,0],current.now,side='right'))
        return sessions.command(sid,'advance',{'to_ms':int(a[i,0]) if i<len(a) else current.now+10})
    @app.post('/api/sessions/{sid}/branch')
    def branch(sid:str,request:AdvanceRequest):return sessions.branch(sid,request.to_ms)
    @app.post('/api/sessions/{sid}/orders')
    def submit(sid:str,request:OrderRequest):return sessions.command(sid,'submit',{'request':request.model_dump()})
    @app.post('/api/sessions/{sid}/orders/{oid}/cancel')
    def cancel(sid:str,oid:str):return sessions.command(sid,'cancel',{'oid':oid})
    @app.post('/api/sessions/{sid}/phase')
    def phase(sid:str,request:PhaseRequest):return sessions.command(sid,'phase',request.model_dump())
    @app.post('/api/sessions/{sid}/seed')
    def seed(sid:str,request:SeedRequest):return sessions.command(sid,'seed',request.model_dump())
    @app.post('/api/sessions/{sid}/auction')
    def auction(sid:str):return sessions.command(sid,'auction',{})
    @app.get('/api/sessions/{sid}/account',response_model=AccountState)
    def account(sid:str):return sessions.get(sid).snapshot()['account']
    @app.get('/api/sessions/{sid}/reports',response_model=list[ExecutionReport])
    def reports(sid:str):return sessions.get(sid).reports
    @app.get('/api/sessions/{sid}/export')
    def export(sid:str):
        s=sessions.get(sid);buf=io.StringIO();rows=s.account.fills
        writer=csv.DictWriter(buf,fieldnames=['order_id','symbol','side','qty','price','fee_cents','execution_time','delivery_time','seq','status','reason'],extrasaction='ignore');writer.writeheader();writer.writerows(rows)
        return Response('\ufeff'+buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="fills-{sid}.csv"'})
    @app.get('/api/market/{symbol}/series')
    def series(symbol:int):
        path=data.cache/f'{symbol}/features.parquet'
        if path.exists():
            import pandas as pd
            frame=pd.read_parquet(path);cols=['t','bid','ask','bq','aq','volume','spread_bp','imbalance','bids','asks'];frame=frame[[c for c in cols if c in frame.columns]].iloc[::5]
            return json.loads(frame.to_json(orient='records'))
        s=data.snapshots(symbol).iloc[::10].copy();s['t']=raw_ms(s.Time)
        return [{'t':int(r.t),'bid':round(float(r.BidPrice1)*100),'ask':round(float(r.AskPrice1)*100),'bq':int(r.BidVolume1),'aq':int(r.AskVolume1),'volume':int(r.AccVolume),'bids':[[round(float(getattr(r,f'BidPrice{i}'))*100),int(getattr(r,f'BidVolume{i}')),0] for i in range(1,11)],'asks':[[round(float(getattr(r,f'AskPrice{i}'))*100),int(getattr(r,f'AskVolume{i}')),0] for i in range(1,11)]} for r in s.itertuples()]
    @app.get('/api/market/{symbol}/events')
    def events(symbol:int,time_ms:int,limit:int=30):
        import numpy as np
        a=data.events(symbol);end=int(np.searchsorted(a[:,0],time_ms,side='right'));start=max(0,end-min(max(limit,1),100))
        return a[start:end].tolist()
    @app.get('/api/market/{symbol}/orders/{channel}/{oid}')
    def lifecycle(symbol:int,channel:int,oid:int,time_ms:int):return data.lifecycle(symbol,channel,oid,time_ms)
    @app.get('/api/sessions/{sid}/queue')
    def queue(sid:str,side:int=1,price:int=0):
        s=sessions.get(sid)
        if s.config.mode=='sandbox':return json.loads(s.engine.queue(side,price,100))
        r=data.replay(s.symbol,max(0,s.now-s.config.market_ms));return json.loads(r.queue(side,price,100))
    @app.get('/api/market/{symbol}/event_records',response_model=list[MarketEvent])
    def event_records(symbol:int,time_ms:int,limit:int=30):
        return [MarketEvent(logical_ms=r[0],biz_index=r[1],channel=r[2],kind=['ADD','CANCEL','TRADE','PHASE'][r[3]],side=r[4],price_cents=r[5],quantity=r[6],order_id=r[7]) for r in events(symbol,time_ms,limit)]
    @app.post('/api/experiments')
    def experiment(config:ExperimentConfig,background:BackgroundTasks):
        import copy,uuid
        if config.horizons_s!=[1,5,30,60] or config.deadline_s!=60:raise ValueError('实验期限须与冻结协议一致')
        with sessions.lock:
            if sessions.db.execute("SELECT 1 FROM experiments WHERE status='running'").fetchone():raise HTTPException(409,'当前研究完成后可开始下一轮')
            job=uuid.uuid4().hex[:12];configuration=copy.deepcopy(settings)
            if 'device' not in config.model_fields_set:config.device=settings['device']
            if 'seed' not in config.model_fields_set:config.seed=settings['seed']
            if 'parent_yuan' not in config.model_fields_set:config.parent_yuan=settings['research']['parent_yuan']
            configuration['research']['parent_yuan']=config.parent_yuan;configuration['seed']=config.seed;configuration['device']=config.device
            sessions.db.execute('INSERT INTO experiments VALUES (?,?,?,?)',(job,'running',json.dumps(configuration),None));sessions.db.commit()
        def work():
            try:
                from .research import run_research
                run_research(data.root,ROOT/'reports/runs'/job,config.device,config.seed,config=configuration)
                status,error='complete',None
            except Exception as exc:status,error='failed',str(exc)
            with sessions.lock:sessions.db.execute('UPDATE experiments SET status=?,error=? WHERE id=?',(status,error,job));sessions.db.commit()
        background.add_task(work);return {'job_id':job,'status':'running'}
    @app.get('/api/experiments')
    def experiments():
        with sessions.lock:return [{'job_id':r[0],'status':r[1],'config':json.loads(r[2]),'error':r[3]} for r in sessions.db.execute('SELECT id,status,config,error FROM experiments ORDER BY rowid DESC')]
    @app.get('/api/research')
    def research(run_id:str|None=None):
        if run_id is not None:
            import re
            if re.fullmatch(r'[0-9a-f]{12}',run_id) is None:raise ValueError('请选择有效实验编号')
        prefix=f'runs/{run_id}/' if run_id else ''
        p=ROOT/'reports'/prefix/'results.json'
        if not p.exists():return {'status':'pending','message':'执行 sse50 research 生成实验结果'}
        result=json.loads(p.read_text());result['report_url']='/reports/'+prefix+'report.html'
        for item in result.get('figures',[]):item['path']=prefix+item['path']
        return result
    @app.get('/api/audit')
    def audit():
        p=ROOT/'evidence/audit.json';return json.loads(p.read_text()) if p.exists() else {'status':'pending'}
    @app.websocket('/ws/{sid}')
    async def websocket(ws:WebSocket,sid:str):
        await ws.accept();revision=-1
        try:
            while True:
                with sessions.lock:s=sessions.get(sid);state=s.snapshot() if s.revision!=revision else None
                if state is not None:await ws.send_json(state);revision=state['revision']
                await asyncio.sleep(0.25)
        except WebSocketDisconnect:pass
        except Exception:await ws.close(code=1011)
    if (ROOT/'docs').exists():app.mount('/docs',StaticFiles(directory=ROOT/'docs'),name='guides')
    if (ROOT/'reports').exists():app.mount('/reports',StaticFiles(directory=ROOT/'reports'),name='reports')
    frontend=ROOT/'frontend/dist'
    if frontend.exists():app.mount('/',StaticFiles(directory=frontend,html=True),name='workbench')
    return app
