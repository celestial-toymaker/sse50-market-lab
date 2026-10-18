"""Four declared studies, causal execution comparisons and auditable output."""
from __future__ import annotations
import html
import json
import math
import platform
import resource
import time
from pathlib import Path
import numpy as np
import pandas as pd
from .compute import ComputeBackend
from .data import DataStore,clock_ms,clock_text,digest,dump
from .execution import NAMES,at,execution_symbol,probes,shared_account
from .features import HORIZONS,build_features
from .settings import load_config

PROTOCOL={
 'version':1,'date':'2026-09-23','external_prior':'Read the complete course issue #6 discussion before implementation. Report independent replication and extensions explicitly.',
 'calibration':['09:35:00','11:28:00'],'validation':['13:05:00','13:58:00'],'holdout':['14:02:00','14:57:00'],
 'embargo_seconds':120,'horizons_seconds':HORIZONS,'features_clock':'Latest one-second state at or before decision minus market latency',
 'primary_tests':['strong versus weak imbalance: passive fill fraction','fresh replenished versus mature depth: future five-second bid retreat','high versus normal cancellations: five-second depth ratio','small aggressor packets: gap >=2 versus gap=1, immediate >=2-tick midpoint impact'],
 'matching':['symbol','half_hour','spread_band','depth_band','side when applicable'],'window_thinning_seconds':60,
 'uncertainty':'Fixed matching weights; synchronized five-minute block bootstrap; single-day conditional uncertainty',
 'multiple_tests':'Benjamini-Hochberg across the four primary normal-approximation block tests; all horizon checks retained',
 'execution':{'parent_yuan':50000,'deadline_seconds':60,'protection_ticks':2,'max_displayed_depth_fraction':0.10,'max_trailing_minute_volume_fraction':0.01,'commission':'2.5bp inclusive of exchange/regulatory fees, 5 yuan minimum per order; transfer 0.1bp both sides; stamp 5bp sells'},
 'cuda_validation':'未测试',
}

def thin(frame:pd.DataFrame,time_col='t',spacing=60000):
    rows=[]
    for _,g in frame.sort_values(['symbol',time_col]).groupby('symbol',sort=True):
        next_t=-1
        for i,t in zip(g.index,g[time_col]):
            if t>=next_t:rows.append(i);next_t=t+spacing
    return frame.loc[rows].copy()

def compare(frame,outcome,backend,multiplier=1,extra_controls=()):
    frame=frame[frame.group.isin([0,1])&np.isfinite(frame[outcome])].copy()
    if frame.empty:return {'n':0,'stocks':0,'matched_n':0,'matched_stocks':0,'effect':None,'ci':[None,None],'p':None,'raw_a':None,'raw_b':None}
    frame['half_hour']=frame.t//1800000
    frame['spread_band']=np.minimum(frame.spread_tick,3).astype(int)
    strata=['symbol','half_hour','spread_band','depth_band']+(['side'] if 'side' in frame else [])+list(extra_controls)
    by=frame.groupby(strata+['group'])[outcome].agg(['mean','size']).unstack('group')
    raw=frame.groupby(['symbol','group'])[outcome].mean().unstack('group')
    result={'n':len(frame),'stocks':int(frame.symbol.nunique()),'raw_a':float(raw[1].mean()*multiplier) if 1 in raw else None,'raw_b':float(raw[0].mean()*multiplier) if 0 in raw else None,'matched_n':0,'matched_stocks':0,'effect':None,'ci':[None,None],'p':None}
    if 0 not in by['size'] or 1 not in by['size']:return result
    ok=by[(by['size'][0]>=1)&(by['size'][1]>=1)].copy()
    if len(ok)==0:return result
    cells=ok.index.to_frame(index=False);cells['cell_id']=np.arange(len(cells))
    m=frame.merge(cells,on=strata);counts=m.groupby(['cell_id','group'])[outcome].transform('size')
    cell_stocks=cells.groupby('symbol').size();nstock=len(cell_stocks)
    weight=m.symbol.map(lambda s:1/(nstock*cell_stocks.loc[s]))
    m['contribution']=np.where(m.group==1,1,-1)*m[outcome]*multiplier/counts*weight
    block=m.groupby('block').contribution.sum();values=block.to_numpy()*len(block)
    effect=float(m.contribution.sum());ci=backend.bootstrap(values)
    se=float(np.std(values,ddof=1)/np.sqrt(len(values))) if len(values)>1 else float('nan')
    p=math.erfc(abs(effect/se)/math.sqrt(2)) if math.isfinite(se) and se>0 else None
    per_stock=ok['mean'].groupby(level='symbol').mean();effects=(per_stock[1]-per_stock[0])*multiplier
    result['per_stock_effects']={str(k):float(v) for k,v in effects.items()}
    result['leave_one_stock_out_range']=[float((effects.sum()-effects).min()/(len(effects)-1)),float((effects.sum()-effects).max()/(len(effects)-1))] if len(effects)>1 else None
    means=per_stock.mean()
    result.update({'matched_n':len(m),'matched_stocks':int(m.symbol.nunique()),'matched_cells':len(cells),'blocks':len(block),'effect':effect,'ci':ci,'p':p,'matched_a':float(means[1]*multiplier),'matched_b':float(means[0]*multiplier)})
    return result

def qvalues(values):
    valid=[(i,p) for i,p in enumerate(values) if p is not None];valid.sort(key=lambda x:x[1]);out=[None]*len(values);v=1
    for rank in range(len(valid),0,-1):
        i,p=valid[rank-1];v=min(v,p*len(valid)/rank);out[i]=float(v)
    return out

def add_bands(frame,thresholds):
    frame=frame.copy()
    frame['depth_band']=[int(np.searchsorted(thresholds[int(s)]['depth'],math.log1p(float(d)))) for s,d in zip(frame.symbol,frame.depth)]
    return frame

def case_of(frame):
    a=frame[(frame.split=='holdout')&(frame.group==1)].sort_values(['t','symbol'])
    if a.empty:return None
    row=a.iloc[0];return {'symbol':int(row.symbol),'time_ms':int(row.t),'note':'按时间和证券代码选取的首个合格案例'}

def predict_models(all_features,backend,out):
    frame=all_features[(all_features.t%5000==0)&all_features.split.isin(['calibration','validation','holdout'])].copy()
    columns=['imbalance','spread_bp','momentum_5','volatility_30','mature','gap']
    x0=frame[columns].to_numpy(dtype=np.float32);x0=np.column_stack([x0,np.log1p(frame.depth),np.log1p(frame.trade_volume_60)])
    train=frame.split.eq('calibration').to_numpy();mean=x0[train].mean(axis=0);std=x0[train].std(axis=0).clip(min=1e-6)
    x=np.column_stack([np.ones(len(frame)),(x0-mean)/std]).astype(np.float32);records=[];weights={}
    for h in HORIZONS:
        y=frame[f'ret_{h}'].to_numpy(dtype=np.float32);valid=np.isfinite(y);fit=train&valid;val=frame.split.eq('validation').to_numpy()&valid;test=frame.split.eq('holdout').to_numpy()&valid
        choices=[]
        for alpha in [0.1,1.,10.]:
            w=backend.ridge(x[fit],y[fit],alpha);pred=backend.predict(x[val],w);choices.append((float(np.mean((pred-y[val])**2)),alpha,w))
        _,alpha,w=min(choices,key=lambda c:(c[0],c[1]));pred=backend.predict(x[test],w);actual=y[test]
        records.append({'horizon_s':h,'alpha':alpha,'train_n':int(fit.sum()),'validation_n':int(val.sum()),'holdout_n':int(test.sum()),'holdout_mse':float(np.mean((pred-actual)**2)),'holdout_correlation':float(np.corrcoef(pred,actual)[0,1]),'mean_prediction_bp':float(pred.mean())})
        weights[str(h)]=w.tolist()
    dump(out/'models.json',{'columns':['intercept']+columns+['log_depth','log_trailing_volume'],'mean':mean.tolist(),'std':std.tolist(),'weights':weights,'metrics':records,'backend':backend.describe()});return records

def make_studies(features,packets,probe_frame,backend,out):
    thresholds={}
    for symbol,g in features[features.split=='calibration'].groupby('symbol'):
        p=packets[(packets.symbol==symbol)&(packets.split=='calibration')]
        thresholds[int(symbol)]={'depth':np.quantile(np.log1p(g.depth),[.25,.5,.75]).tolist(),'high_depth':float(g.depth.quantile(.75)),'cancel_high':float(g.cancel_volume_5.quantile(.9)),'cancel_low':float(g.cancel_volume_5.quantile(.25)),'cancel_mid':float(g.cancel_volume_5.quantile(.5)),'small_quantity':float(p.quantity.quantile(.5)) if len(p) else 0}
    dump(out/'frozen_thresholds.json',thresholds)
    studies=[];details={}
    p=add_bands(probe_frame,thresholds);p['group']=np.where(p.strong,1,np.where(p.weak,0,-1))
    groups={'calibration':p[p.split=='calibration'],'validation':p[p.split=='validation'],'holdout':p[p.split=='holdout']}
    metrics={name:compare(g,'fill_rate',backend,100) for name,g in groups.items()}
    eligible=p[(p.split=='holdout')&(p.group==1)]
    selected=float(eligible.markout_30.mean()) if eligible.filled.sum() else None
    overall=float(eligible.ret_30.mean()) if len(eligible) else None
    conditional=float(eligible.loc[eligible.filled>0,'ret_30'].mean()) if (eligible.filled>0).any() else None
    unfilled=float(eligible.loc[eligible.filled==0,'ret_30'].mean()) if (eligible.filled==0).any() else None
    details['selection']={'comparisons':metrics,'horizons':{str(h):compare(groups['holdout'],f'markout_{h}',backend) for h in HORIZONS},'strong_all_ret30':overall,'strong_filled_same_origin_ret30':conditional,'strong_unfilled_same_origin_ret30':unfilled,'strong_filled_markout30':selected,'strong_filled_count':int((eligible.filled>0).sum()),'strong_count':len(eligible)}
    m=metrics['holdout'];studies.append({'id':'selection','title':'看涨时，买一真的更容易成交吗？','finding':f'强信号全部起点未来30秒的方向变化为 {overall:.3f} bp；实际成交起点按相同起点口径计算为 {conditional:.3f} bp。成交后另计30秒价格表现为 {selected:.3f} bp。' if overall is not None and selected is not None else '成交机会与成交后的价格变化分别保留；当前合格样本覆盖见完整报告。','headline':format_effect(m),'unit':'成交率差 / 个百分点','method':'最小合法数量、排队30秒；匹配股票、方向、时段、价差和深度，保留未成交时点。','case':case_of(p),'primary':m})
    f=add_bands(features,thresholds)
    f['high_depth']=[d>=thresholds[int(s)]['high_depth'] for s,d in zip(f.symbol,f.depth)]
    f['group']=np.where(f.high_depth&f.replenishing&(f.mature<.5),1,np.where(f.high_depth&(f.mature>=.8),0,-1))
    young=thin(f[(f.group>=0)&(f.split!='context')&f.bid_down_5.notna()])
    metrics={name:compare(young[young.split==name],'bid_down_5',backend,100) for name in groups}
    details['replenishment']={'comparisons':metrics,'horizons':{str(h):compare(young[young.split=='holdout'],f'bid_down_{h}',backend,100) for h in HORIZONS},'definition':'新增成交后同价深度维持、成熟量占比低于50%，对照高深度且成熟量占比至少80%；成熟指已存续一秒，午休从订单年龄扣除。'}
    studies.append({'id':'replenishment','title':'盘口补得快，也可能更脆弱。','finding':evidence_sentence(metrics['holdout'],'买一在未来5秒后退的概率差'),'headline':format_effect(metrics['holdout']),'unit':'概率差 / 个百分点','method':'比较厚盘口中的新生补量与成熟挂量，60秒去重；统计相关性与补量机制分别解释。','case':case_of(young),'primary':metrics['holdout']})
    f['group']=[1 if c>=thresholds[int(s)]['cancel_high'] and c>0 else 0 if thresholds[int(s)]['cancel_low']<=c<=thresholds[int(s)]['cancel_mid'] else -1 for s,c in zip(f.symbol,f.cancel_volume_5)]
    cancellation=thin(f[(f.group>=0)&(f.split!='context')&f.depth_ratio_5.notna()])
    cancellation['depth_change_pct']=(cancellation.depth_ratio_5-1)*100
    metrics={name:compare(cancellation[cancellation.split==name],'depth_change_pct',backend) for name in groups}
    placebo=cancellation.copy();lookup=features.set_index(['symbol','t'])['depth_ratio_5']
    placebo['placebo']=pd.Series([lookup.get((int(s),int(t)+60000),np.nan) for s,t in zip(placebo.symbol,placebo.t)],index=placebo.index).sub(1).mul(100)
    details['cancellation']={'comparisons':metrics,'placebo_60s':compare(placebo[placebo.split=='holdout'],'placebo',backend),'horizons':{str(h):compare(cancellation.assign(outcome=(cancellation[f'depth_ratio_{h}']-1)*100).query('split=="holdout"'),'outcome',backend) for h in HORIZONS},'definition':'撤单为过去5秒全簿撤单股数；近价容量指随后观察到的一档双边深度，含报价变化。匿名订单的数量相似只作为描述。'}
    studies.append({'id':'cancellation','title':'撤单很多，近价容量会怎样变化？','finding':evidence_sentence(metrics['holdout'],'未来5秒一档容量变化差'),'headline':format_effect(metrics['holdout']),'unit':'容量变化差 / 百分点','method':'上午固定撤单量分位数，比较高撤单与正常撤单；加入60秒错时对照。','case':case_of(cancellation),'primary':metrics['holdout']})
    pp=packets.copy();pp['t']=pp.observed_ms;pp['spread_tick']=pp.spread;pp['depth']=pp.pre_depth;pp=add_bands(pp,thresholds)
    pp['small']=[q<=thresholds[int(s)]['small_quantity'] for s,q in zip(pp.symbol,pp.quantity)]
    pp['group']=np.where(pp.small&(pp.relative_size<=1)&(pp.gap>=2),1,np.where(pp.small&(pp.relative_size<=1)&(pp.gap==1),0,-1))
    gap=thin(pp[(pp.group>=0)&(pp.split!='context')])
    metrics={name:compare(gap[gap.split==name],'jump_2tick',backend,100) for name in groups}
    for h in HORIZONS:
        values=[]
        for symbol,g in gap.groupby('symbol'):
            source=features[features.symbol==symbol];t=source.t.to_numpy();target=g.t.to_numpy()+h*1000;idx=np.searchsorted(t,target,side='right')-1;valid=(idx>=0)&(np.abs(t[np.maximum(idx,0)]-target)<1000)&(((g.t.to_numpy()<41400000)&(target<41400000))|((g.t.to_numpy()>=46800000)&(target<53820000)))
            val=np.where(valid,g.side.to_numpy()*(source.mid.to_numpy()[np.maximum(idx,0)]-g.post_mid.to_numpy())/g.post_mid.to_numpy()*10000,np.nan)
            values.append(pd.Series(val,index=g.index))
        gap[f'drift_{h}']=pd.concat(values).reindex(gap.index) if values else np.nan
    details['gap']={'comparisons':metrics,'future_drift':{str(h):compare(gap[gap.split=='holdout'],f'drift_{h}',backend) for h in HORIZONS},'mechanical_component':'小量限制在原最优档数量之内，单侧吃光后的中间价跳动与下一档距离存在规则决定的机械关系；主结果解释即时结构，后续漂移另行检验。','definition':'按同频道连续同主动订单号聚合成交包；直到下一消息才确认包边界。小量阈值来自上午，结果描述即时价格变化。','cases':gap[gap.split=='holdout'].sort_values('impact_ticks',ascending=False).head(10).to_dict('records')}
    studies.append({'id':'gap','title':'小成交，也能触发明显跳价。','finding':evidence_sentence(metrics['holdout'],'即时两分以上中间价变化的概率差'),'headline':format_effect(metrics['holdout']),'unit':'概率差 / 个百分点','method':'固定小成交门槛，比较相邻档距一分与至少两分；分别核对成交规模及相对深度。','case':case_of(gap),'primary':metrics['holdout']})
    q=qvalues([s['primary']['p'] for s in studies])
    for s,value in zip(studies,q):s['primary']['bh_q']=value
    for name,frame in [('selection',p),('replenishment',young),('cancellation',cancellation),('gap',gap)]:
        frame.to_parquet(out/f'study_{name}.parquet',index=False)
    # Additional controls use morning cutoffs and information preceding each observation.
    controls=['volatility_band','intensity_band','gap_band','queue_band']
    cutoffs={int(symbol):{'volatility':float(g.volatility_30.median()),'intensity':float(g.trade_volume_60.median()),'gap':float(g.gap.median()),'queue':float(g.depth.median()/2)} for symbol,g in features[features.split=='calibration'].groupby('symbol')}
    for name,source,outcome,multiplier in [('selection',p,'fill_rate',100),('replenishment',young,'bid_down_5',100),('cancellation',cancellation,'depth_change_pct',1),('gap',gap,'jump_2tick',100)]:
        controlled=[]
        for symbol,g in source[source.split=='holdout'].groupby('symbol'):
            g=g.copy();history=features[features.symbol==symbol];target=g.t.to_numpy()-(1000 if name=='gap' else 100);idx=np.maximum(0,np.searchsorted(history.t.to_numpy(),target,side='right')-1);visible=history.iloc[idx];cut=cutoffs[int(symbol)]
            g['volatility_band']=(visible.volatility_30.to_numpy()>cut['volatility']).astype(int)
            g['intensity_band']=(visible.trade_volume_60.to_numpy()>cut['intensity']).astype(int)
            g['gap_band']=(visible.gap.to_numpy()>cut['gap']).astype(int)
            side=g.side.to_numpy() if 'side' in g else np.ones(len(g));queue=np.where(side==1,visible.bq.to_numpy(),visible.aq.to_numpy())
            g['queue_band']=(queue>cut['queue']).astype(int);controlled.append(g)
        extended=pd.concat(controlled) if controlled else source.iloc[:0]
        details[name]['extended_controls']=compare(extended,outcome,backend,multiplier,controls)
        details[name]['extended_controls_note']='Additional morning-median bands: preceding volatility, trailing minute traded volume, adjacent gap and visible same-side queue. Sparse overlap is reported explicitly; weights and matched sample differ from the primary contrast.'
    dump(out/'study_details.json',details);return studies,details

def format_effect(m):return '覆盖不足' if m['effect'] is None else f'{m["effect"]:+.2f}'
def evidence_sentence(m,label):
    if m['effect'] is None:return f'{m["n"]:,} 个合格观察，匹配覆盖不足；保留原始比较，机制判断等待更多证据。'
    lo,hi=m['ci'];certainty='区间包含零，方向证据有限' if lo<=0<=hi else '区间在零的一侧，仍需多日验证'
    return f'日内留出匹配后，{label}为 {m["effect"]:+.2f}，区间 [{lo:+.2f}, {hi:+.2f}]；{certainty}。'

def execution_runs(data,features_by_symbol,backend,out,config):
    all_parents=[];accounts=[]
    train=pd.concat([f[f.split=='calibration'] for f in features_by_symbol.values()])
    threshold=float(np.clip(train.imbalance.abs().quantile(.8),.4,.9))
    dump(out/'execution_policy.json',PROTOCOL['execution']|{'imbalance_threshold':threshold,'parent_schedule':['09:35','10:05','10:35','11:05','13:05','13:35','14:05','14:35'],'directions':'alternate BUY, SELL','adaptive':'cancel passive remainder, await cancel acknowledgement, then use protected taker; deadline remainder retained'})
    for scale in [0,1,5]:
        for conservative in [False,True]:
            for strategy in NAMES:
                log=[];quantities={};parents=[]
                for symbol in data.symbols:
                    p,l,q=execution_symbol(data,symbol,features_by_symbol[symbol],strategy,scale,conservative,threshold,config)
                    for ordinal,item in enumerate(l):item['ordinal']=ordinal
                    parents.extend(p);log.extend(l);quantities[symbol]=q
                account=shared_account(log,quantities,data,config);account.update({'strategy':strategy,'scale':scale,'conservative':conservative})
                accounts.append(account);all_parents.extend(parents)
                pd.DataFrame([{'time':x['time'],'symbol':x['symbol'],'event':x['event'],'ordinal':x['ordinal'],'payload':json.dumps(x['payload'],ensure_ascii=False)} for x in log]).to_parquet(out/f'ledger_{strategy}_{scale}_{int(conservative)}.parquet',index=False)
                print(f'execution {strategy}, latency={scale}, conservative={conservative}: {len(parents)} parents, fees={account["fees_cents"]/100:.2f}',flush=True)
    frame=pd.DataFrame(all_parents);frame.to_parquet(out/'execution_tasks.parquet',index=False);dump(out/'account_scenarios.json',accounts)
    results=[]
    for strategy,name in NAMES.items():
        p=frame[(frame.strategy==strategy)&(frame.scale==1)&~frame.conservative]
        a=next(a for a in accounts if a['strategy']==strategy and a['scale']==1 and not a['conservative'])
        results.append({'strategy':strategy,'name':name,'cost_bp':float(p.groupby('symbol').cost_bp.mean().mean()),'completion_rate':float(p.complete.mean()),'volume_completion':float(p.filled.sum()/p.quantity.sum()),'median_wait_s':float(p.wait_s.median()) if p.wait_s.notna().any() else None,'fees_cents':int(p.fees_cents.sum()),'remaining':int(p.remaining.sum()),'incremental_pnl_cents':a['incremental_pnl_cents'],'tasks':len(p)})
    comparisons=[];baseline=frame[frame.strategy=='immediate'].set_index(['symbol','t','scale','conservative']).cost_bp
    for (strategy,scale,conservative),p in frame.groupby(['strategy','scale','conservative']):
        p=p.copy();p['saving']=baseline.reindex(pd.MultiIndex.from_frame(p[['symbol','t','scale','conservative']])).to_numpy()-p.cost_bp.to_numpy();block=p.groupby(p.t//300000).saving.mean()
        comparisons.append({'strategy':strategy,'scale':int(scale),'conservative':bool(conservative),'saving_bp':float(p.groupby('symbol').saving.mean().mean()),'ci':backend.bootstrap(block.to_numpy()),'completion_rate':float(p.complete.mean())})
    dump(out/'execution_comparisons.json',comparisons);return results,frame,comparisons

def figures(studies,execution,frame,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'#f5f8fa','axes.facecolor':'#f5f8fa','axes.labelcolor':'#34465a','text.color':'#263a50','axes.edgecolor':'#b7c6d3','grid.color':'#dce4eb'})
    fig,axes=plt.subplots(2,2,figsize=(12,8),constrained_layout=True)
    labels=['Passive fill selection','Fresh vs mature replenishment','Cancellations & depth recovery','Small trades & price gaps']
    for ax,study,label in zip(axes.flat,studies,labels):
        p=study['primary'];a=p.get('matched_a',p.get('raw_a'));b=p.get('matched_b',p.get('raw_b'))
        if a is not None and b is not None:ax.bar(['Reference','Condition'],[b,a],color=['#6689aa','#30aa92'],width=.5)
        ax.set_title(label,loc='left',fontweight='bold',pad=15);ax.grid(axis='y',alpha=.5);ax.set_axisbelow(True)
        ax.text(.03,.97,f"n={p['n']:,}; matched n={p['matched_n']:,}; stocks={p['matched_stocks']}\nEffect={study['headline']}; CI=[{p['ci'][0]:+.2f}, {p['ci'][1]:+.2f}]",transform=ax.transAxes,va='top',fontsize=8,bbox={'facecolor':'white','alpha':.8,'edgecolor':'none'})
        ax.set_ylabel(['Fill fraction (%)','Bid retreat probability (%)','Depth change (%)','Immediate jump probability (%)'][labels.index(label)])
    fig.suptitle('SSE 50 | Four hypotheses, within-day holdout',fontsize=16,fontweight='bold');fig.savefig(out/'four_studies.png',dpi=170);fig.savefig(out/'four_studies.svg');plt.close(fig)
    fig,axes=plt.subplots(1,3,figsize=(14,4),constrained_layout=True);names=['Immediate','TWAP','Passive','Adaptive'];colors=['#6d88a4','#adc3d4','#62bda4','#197c78']
    axes[0].bar(names,[r['cost_bp'] for r in execution],color=colors);axes[0].set_ylabel('All-task cost (bp)')
    axes[1].bar(names,[r['completion_rate']*100 for r in execution],color=colors);axes[1].set_ylabel('Fully completed tasks (%)');axes[1].set_ylim(0,105)
    for strategy,name in zip(NAMES,names):
        p=frame[(frame.strategy==strategy)&~frame.conservative].groupby('scale').cost_bp.mean();axes[2].plot(p.index,p.values,'o-',label=name)
    axes[2].set_xlabel('Latency multiplier');axes[2].set_ylabel('All-task cost (bp)');axes[2].legend(fontsize=8)
    for ax in axes:ax.grid(axis='y',alpha=.5);ax.set_axisbelow(True);ax.tick_params(axis='x',labelrotation=15)
    fig.suptitle('Same parent tasks | fees, capacity, deadlines and shared-account audit',fontsize=13);fig.savefig(out/'execution.png',dpi=170);fig.savefig(out/'execution.svg');plt.close(fig)
    return [{'path':'four_studies.png','title':'四项研究：匹配后的条件比较与覆盖范围'},{'path':'execution.png','title':'执行对照：成本、完成率与延迟敏感性'}]

def report(result,details,out):
    cfg=result['run']['configuration'];fee=cfg['fees'];rcfg=cfg['research']
    rows=''.join(f'<tr><td>{html.escape(r["name"])}</td><td>{r["cost_bp"]:.3f}</td><td>{r["completion_rate"]:.1%}</td><td>{r["fees_cents"]/100:,.2f}</td><td>{r["incremental_pnl_cents"]/100:,.2f}</td><td>{r["remaining"]:,}</td></tr>' for r in result['execution'])
    sections=''
    for study in result['studies']:
        p=study['primary'];sections+=f'<section><h2>{html.escape(study["title"])}</h2><p>{html.escape(study["finding"])}</p><p>{html.escape(study["method"])}</p><p>合格观察 {p["n"]:,}，匹配观察 {p["matched_n"]:,}，匹配股票 {p["matched_stocks"]}；BH 校正 q={p.get("bh_q")}。</p><details><summary>查看完整数值与覆盖</summary><pre>{html.escape(json.dumps(details[study["id"]],ensure_ascii=False,indent=2,default=str))}</pre></details></section>'
    text=f'''<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SSE50 微观结构研究报告</title><style>body{{max-width:1000px;margin:50px auto;padding:0 25px;color:#25364a;font:16px/1.85 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}h1{{font-size:34px;line-height:1.4}}h2{{margin-top:45px}}small{{color:#60778b}}img{{max-width:100%;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;border-bottom:1px solid #dce3eb;text-align:left}}pre{{white-space:pre-wrap;font-size:12px;background:#f0f5f8;padding:16px}}.lead{{padding:22px;background:#eaf5f1;border-left:4px solid #329b81}}a{{color:#157e76}}</style><h1>把盘口直觉放进订单队列，再算一次成本。</h1><small>2026-09-23 · 上证50课堂数据 · 单日研究 · 后端 {result['backend']['effective_device']}</small><p class="lead">本报告把信号出现时的价格变化、实际成交机会、成交后的价格变化和账户增量分别计算。所有结果来自随附脚本的实际运行。CUDA 验收状态：未测试。</p><h2>数据与方法</h2><p>逐笔委托 6,276,360 条、逐笔成交 3,792,934 条、快照 252,631 条。使用频道内业务顺序及原始订单号重建盘口；新增消息按剩余挂单解释。快照用于独立核验。课程讨论已作为外部先验登记，本项目的日内留出属于冻结参数后的同日验证。</p><p>上午校准，13:05—13:58 验证选择，14:02—14:57 留出；标签保持在连续竞价时段内。四项主检验使用股票、方向、半小时时段、价差及深度分层匹配。主检验的p值采用时间块贡献的近似正态检验，再作BH校正。置信区间采用固定匹配权重下的同步5分钟时间块重采样。匹配覆盖、样本量、四个期限及错时对照完整保留。另以上午中位数固定近期波动、成交强度、相邻档距和可见队前量分档，保存扩展控制、逐股效果及逐股剔除范围。更细分层的重叠覆盖与主检验分别报告。</p>{sections}<img src="four_studies.png" alt="四项研究"><h2>执行实验</h2><p>每股八个固定时点、买卖交替，母单目标{rcfg['parent_yuan']/10000:g}万元，60秒截止。保护价为起点对手价外2分钱；子单上限取可见十档对手量10%、过去一分钟成交量1%及剩余母单数量的最小值。四方案共享任务与预算设定。本轮佣金为万分之{float(fee['commission_rate'])*10000:g}、每订单最低{float(fee['commission_min_yuan']):g}元，含经手及监管项目；另计双边万分之{float(fee['transfer_rate'])*10000:g}过户费及卖方万分之{float(fee['sell_stamp_rate'])*10000:g}印花税，部分成交按累计订单金额计费。</p><table><thead><tr><th>方案</th><th>全任务成本/bp</th><th>全部完成率</th><th>费用/元</th><th>账户交易增量/元</th><th>剩余股数</th></tr></thead><tbody>{rows}</tbody></table><p>全任务成本相对任务开始中间价计算，计入成交费用及剩余数量的截止时点估值。完成率和等待时间共同呈现。另保存每笔实际成交金额额外增加1bp成本的压力覆盖，成交路径与完成率沿用基准。账户另用{rcfg['cash_yuan']/10000:g}万元现金与每股{rcfg['inventory_parents']}笔母单的隔夜股数逐事件核验冻结、资金、可卖持仓及累计费用；账户增量相对持有初始底仓计算，估值时点为14:56:59。成本先按股票等权统计，再对股票平均；账户金额按实际合法股数计算。</p><img src="execution.png" alt="执行方案比较"><h2>解释边界与竞争解释</h2><p>信号与成交条件共同筛选实际样本，价格变动还受到价差、订单年龄、档距及日内共同波动影响。快速补量可以反映持续供应或频繁报价更新；撤单后深度变化也包含价格档位迁移。匿名订单支持观察行为模式，账户身份及交易动机需要额外证据。档间断层研究主要解释即时冲击，其执行价值由独立的因果回放对照检验。</p><p>历史行情路径固定，虚拟成交共享容量；大额交易对后续真实报价的反馈需要交互市场模型。行情及动作延迟为情景假设，标注时间的精度限制更短期限的解释。单日重采样区间描述当天的条件不确定性，多交易日检验承担泛化判断。</p><h2>复现与文件</h2><p>运行 <code>uv sync --frozen</code>，然后 <code>uv run sse50 reproduce --device auto</code>。协议、阈值、每个订单的回报、母单剩余量、共享账户、模型及输入校验值均有对应文件。参考 <a href="../docs/method.md">方法文档</a> 与 <a href="../docs/references.md">原始来源</a>。</p><details><summary>运行清单</summary><pre>{html.escape(json.dumps(result['run'],ensure_ascii=False,indent=2))}</pre></details></html>'''
    (out/'report.html').write_text(text,encoding='utf-8')

def run_research(data_dir:Path,out:Path,device='auto',seed=20260923,refresh=False,config=None):
    from copy import deepcopy
    config=deepcopy(config or load_config());config['device']=device;config['seed']=seed
    out=Path(out);out.mkdir(parents=True,exist_ok=True);start=time.perf_counter();backend=ComputeBackend(device,seed);data=DataStore(data_dir)
    protocol=PROTOCOL|{'seed':seed,'backend_request':device,'effective_configuration':config};dump(out/'protocol.json',protocol)
    previous=out/'probe_configuration.json'
    probe_configuration={'latency':config['latency'],'source':data.manifest['source']['archive_sha256'],'core_sha256':digest(Path(__file__).resolve().parents[2]/'crates/core/src/lib.rs')}
    probe_refresh=refresh or not previous.exists() or json.loads(previous.read_text())!=probe_configuration
    dump(previous,probe_configuration)
    features_by_symbol={};packets=[];probe_frames=[]
    for symbol in data.symbols:
        f,p=build_features(data,symbol,refresh);features_by_symbol[symbol]=f.drop(columns=['bids','asks'],errors='ignore');packets.append(p)
        cache=out/f'probes_{symbol}.parquet'
        if cache.exists() and not probe_refresh:probe=pd.read_parquet(cache)
        else:
            probe=probes(data,symbol,f,config=config);probe.to_parquet(cache,index=False)
        probe_frames.append(probe)
    features=pd.concat(features_by_symbol.values(),ignore_index=True);packet_frame=pd.concat(packets,ignore_index=True);probe_frame=pd.concat(probe_frames,ignore_index=True)
    metrics=predict_models(features,backend,out);studies,details=make_studies(features,packet_frame,probe_frame,backend,out)
    execution,execution_frame,comparisons=execution_runs(data,features_by_symbol,backend,out,config)
    stress=[]
    for item in execution:
        tasks=execution_frame[(execution_frame.strategy==item['strategy'])&(execution_frame.scale==1)&~execution_frame.conservative].copy()
        tasks['extra_bp']=tasks.turnover_cents/(tasks.quantity*tasks.benchmark)
        extra=float(tasks.groupby('symbol').extra_bp.mean().mean())
        stress.append({'strategy':item['strategy'],'extra_cost_bp_per_traded_notional':1.0,'all_task_cost_bp':item['cost_bp']+extra,'incremental_pnl_cents':item['incremental_pnl_cents']-int(round(tasks.turnover_cents.sum()/10000)),'scope':'Additional one-bp execution-cost overlay; fills and completion remain the baseline path.'})
    dump(out/'cost_stress.json',stress)
    fig=figures(studies,execution,execution_frame,out)
    run={'seconds':time.perf_counter()-start,'peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if platform.system()=='Darwin' else 1024),'date':'2026-09-23','seed':seed,'source_sha256':data.manifest['source']['archive_sha256'],'python':platform.python_version(),'platform':platform.platform(),'source_files':{str(p.relative_to(Path(__file__).resolve().parents[2])):digest(p) for p in Path(__file__).parent.glob('*.py')},'feature_rows':len(features),'packet_rows':len(packet_frame),'probe_rows':len(probe_frame),'parent_tasks':len(execution_frame),'configuration':config}
    result={'status':'complete','studies':studies,'execution':execution,'execution_sensitivity':comparisons,'cost_stress':stress,'models':metrics,'figures':fig,'backend':backend.describe(),'scope':'全部50股，单日时间留出；固定历史路径、排队、共享容量、含费执行及独立共享账户核验。','run':run}
    dump(out/'results.json',result);report(result,details,out);return result
