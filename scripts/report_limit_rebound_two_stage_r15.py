"""Fit small R15 risk filters on causal stage-one selections, freeze, then evaluate."""
from __future__ import annotations
import json
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier,export_text
from scripts.experiment_limit_rebound_two_stage_r15 import OUT,SOURCE,TEST_MONTHS,X2,window,classify,score_frame,rank_quality
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha,xmatrix
from scripts.audit_limit_rebound_account_drawdown_r13 import replay

DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_two_stage_r15')
PARTS={'train_oof':'训练','valid':'验证','forecast':'测试'}
RISK_NAMES={'none':'不过滤','logistic':'逻辑回归','tree':'两层树'}
FIRST_NAMES={'rf':'原随机森林','rf_shallow':'浅随机森林','logistic':'逻辑回归'}

def clean(f):return f[f.eligible & f.fill_proxy.eq(True) & f.clean_return].copy()


def risk_model(name):
 last=(LogisticRegression(C=.1,max_iter=1000,class_weight='balanced',random_state=20261015)
  if name=='logistic' else DecisionTreeClassifier(max_depth=2,min_samples_leaf=40,class_weight='balanced',random_state=20261015))
 steps=[SimpleImputer(strategy='median',keep_empty_features=True)]
 if name=='logistic':steps.append(StandardScaler())
 return make_pipeline(*steps,last)


def risk_stats(f,p,threshold):
 rejected=np.asarray(p)>=threshold;kept=~rejected
 bad=f.bad.to_numpy(bool);good=f.strategy_return.gt(0).to_numpy()
 return {'n':len(f),'bad':int(bad.sum()),'removed':int(rejected.sum()),'bad_removed':int((bad&rejected).sum()),
  'good_removed':int((good&rejected).sum()),'good':int(good.sum()),
  'retention':float(kept.mean()) if len(f) else 1.,
  'precision':float(bad[rejected].mean()) if rejected.any() else 0.,
  'recall':float(rejected[bad].mean()) if bad.any() else 0.,
  'base_bad_rate':float(bad.mean()) if len(f) else 0.,
  'kept_bad_rate':float(bad[kept].mean()) if kept.any() else None,
  'base_return':float(f.strategy_return.mean()) if len(f) else None,
  'kept_return':float(f.loc[kept,'strategy_return'].mean()) if kept.any() else None}


def passes(s,min_removed):
 return bool(s['removed']>=min_removed and s['retention']>=.65 and s['precision']>s['base_bad_rate']
  and s['kept_return'] is not None and s['kept_return']>=s['base_return'])


def freeze():
 allf=pd.read_pickle(OUT/'enriched_top5.pkl');summaries=[]
 panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
 for month in TEST_MONTHS:
  root=OUT/month
  if (root/'risk_frozen.json').exists():continue
  frames={p:allf[allf.evaluation_month.eq(month)&allf.part.eq(p)].copy() for p in PARTS}
  tr=clean(frames['train_oof']);va=clean(frames['valid']);months=sorted(tr.d0_date.str[:7].unique())
  candidates=[]
  for name in ('logistic','tree'):
   oofrows=[];oofp=[]
   for index in range(3,len(months)):
    cutoff=months[index]+'-01'
    cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
    end_i=np.searchsorted(cal,cutoff)-1
    past=tr[tr.d0_date.lt(cutoff)&tr.date_i.add(4).le(end_i)]
    later=tr[tr.d0_date.str.startswith(months[index])]
    running=panel[panel.date_i.eq(end_i)]
    keys=set(zip(running.symbol,running.first_limit_date))
    # Purge events known to be ongoing at the training cutoff.
    past=past[[key not in keys for key in zip(past.symbol,past.first_limit_date)]]
    if len(past)<60 or past.bad.nunique()<2 or later.empty:continue
    model=risk_model(name);model.fit(xmatrix(past,X2),past.bad.astype(int))
    joblib.dump({'model':model,'features':X2,'training_keys':past[['symbol','d0_date']].to_dict('records')},root/f'risk_{name}_cv{index}.joblib',compress=3)
    oofrows.append(later);oofp.extend(model.predict_proba(xmatrix(later,X2))[:,1])
   if len(tr)<100 or tr.bad.nunique()<2 or not oofrows:continue
   oof=pd.concat(oofrows,ignore_index=True);pred=np.array(oofp)
   model=risk_model(name);model.fit(xmatrix(tr,X2),tr.bad.astype(int))
   path=root/f'risk_{name}.joblib'
   joblib.dump({'model':model,'features':X2,'training_keys':tr[['symbol','d0_date']].to_dict('records'),
     'stage1_selection_sha256':sha(root/'stage1_frozen.json'),'spec_sha256':sha(OUT/'specification.json')},path,compress=3)
   loaded=joblib.load(path)['model'];assert np.array_equal(model.predict_proba(xmatrix(tr,X2)),loaded.predict_proba(xmatrix(tr,X2)))
   validp=model.predict_proba(xmatrix(va,X2))[:,1] if len(va) else np.array([])
   oof.assign(risk_score=pred).to_pickle(root/f'risk_{name}_oof.pkl')
   for tail in (.1,.2,.3):
    threshold=float(np.quantile(pred,1-tail))
    a=risk_stats(oof,pred,threshold);b=risk_stats(va,validp,threshold)
    accepted=passes(a,5) and passes(b,3)
    gain=(a['kept_return']-a['base_return'] if a['kept_return'] is not None else -1)+(b['kept_return']-b['base_return'] if b['kept_return'] is not None else -1)
    candidates.append({'name':name,'tail':tail,'threshold':threshold,'train_oof':a,'valid':b,'passes':accepted,'selection_gain':gain})
  accepted=[r for r in candidates if r['passes']]
  chosen=max(accepted,key=lambda r:(r['selection_gain'],-r['tail'])) if accepted else {'name':'none','threshold':None,'tail':0}
  if chosen['name']!='none':
   model=joblib.load(root/f'risk_{chosen["name"]}.joblib')['model']
   if chosen['name']=='tree':description=export_text(model.steps[-1][1],feature_names=X2)
   else:description=json.dumps(dict(zip(X2,model.steps[-1][1].coef_[0].tolist())),ensure_ascii=False)
   (root/'risk_factors.txt').write_text(description+'\n')
  dump(root/'risk_frozen.json',{'month':month,'chosen':chosen,'candidates':candidates,
   'training_rows':len(tr),'validation_rows':len(va),'risk_features':X2,'test_results_seen':False})
  print('risk frozen',month,chosen['name'],chosen.get('tail'),flush=True)


def pick(f,filtered=False):
 eligible=f[f.eligible & (~f.reject if filtered else True)].sort_values(['d0_date','d0_rank','symbol'])
 picked=eligible.groupby('d0_date',sort=False).head(1)
 # Execution and audit failures after selection do not promote the next stock.
 return picked[picked.fill_proxy.eq(True)&picked.clean_return].copy()


def account(g,exit_key,calendar,months):
 f=g.copy();f['d1_open']=f.buy_price
 for k in (2,3,4):f[f'd{k}_gross']=f[f'ret{k}']
 f['exit_gross']=f.strategy_return
 return replay(f,exit_key,calendar,months)


def summary_stats(f):
 return {'候选数':len(f),'坏样本':int(f.bad.sum()),'坏样本率':float(f.bad.mean()) if len(f) else None,
 'D2均涨':float(f.ret2.mean()) if len(f) else None,'D3均涨':float(f.ret3.mean()) if len(f) else None,
 'D4均涨':float(f.ret4.mean()) if len(f) else None,'策略均涨':float(f.strategy_return.mean()) if len(f) else None}


def report():
 assert all((OUT/m/'risk_frozen.json').exists() for m in TEST_MONTHS)
 allf=pd.read_pickle(OUT/'enriched_top5.pkl');panel=pd.read_pickle(SOURCE/'panel_replay_through_20261014.pkl')
 cal=json.loads((SOURCE/'calendar_through_20261014.json').read_text())
 first=[];risk=[];config=[];details=[];allpicked=[];alltest=[]
 for month in TEST_MONTHS:
  root=OUT/month;stage1=json.loads((root/'stage1_frozen.json').read_text());fr=json.loads((root/'risk_frozen.json').read_text());chosen=fr['chosen'];w=window(month)
  config.append({'预测月':month,'训练起':w['train'][0],'训练止':w['train'][1],'验证起':w['valid'][0],'验证止':w['valid'][1],
    '初选模型':FIRST_NAMES[stage1['family']],'过滤模型':RISK_NAMES[chosen['name']],'过滤门槛':chosen['threshold'],
    '风险训练数':fr['training_rows'],'风险验证数':fr['validation_rows']})
  for cand in stage1['candidates']:
   evals={p:cand[p] for p in ('train_infit','train_oof','valid')}
   if cand['family'] in [stage1['family'],'rf']:
    evals['forecast']=classify(score_frame(panel,month,cand['family'],'forecast'))
   for p,m in evals.items():
    for scope in ('all','open'):
     for k in (3,5):
      s=m[scope][f'top{k}'];first.append({'预测月':month,'集合':{'train_infit':'训练拟合内',**PARTS,'train_oof':'训练折外'}[p],
       '模型':FIRST_NAMES[cand['family']],'选定':int(cand['family']==stage1['family']),'口径':'D0全部' if scope=='all' else '开盘激活',
       'Top':k,'样本数':s['n'],'命中数':s['hits'],'命中率':s['rate'],'同日基率':s['same_day_base'],
       '提升':s['rate']-s['same_day_base'] if s['rate'] is not None and s['same_day_base'] is not None else None})
  model=joblib.load(root/f'risk_{chosen["name"]}.joblib')['model'] if chosen['name']!='none' else None
  for part in PARTS:
   f=allf[allf.evaluation_month.eq(month)&allf.part.eq(part)].copy()
   f['risk_score']=model.predict_proba(xmatrix(f,X2))[:,1] if model else np.nan
   f['reject']=f.risk_score.ge(chosen['threshold']) if model else False
   f['selected_before']=False;f['selected_after']=False
   for filtered in (False,True):
    picks=pick(f,filtered)
    f.loc[picks.index,'selected_after' if filtered else 'selected_before']=True
    picks['policy']='过滤后' if filtered else '过滤前';picks['part']=part;picks['evaluation_month']=month
    allpicked.append(picks)
   c=clean(f);removed=c[c.reject];retained=c[~c.reject]
   risk.append({'预测月':month,'集合':PARTS[part],'过滤模型':RISK_NAMES[chosen['name']],**summary_stats(c),
    '拦截数':len(removed),'拦坏数':int(removed.bad.sum()),'拦盈利数':int(removed.strategy_return.gt(0).sum()),
    '拦再板数':int(removed.future_seal.sum()),'保留数':len(retained),
    '保留坏例率':float(retained.bad.mean()) if len(retained) else None,
    '保留策略均涨':float(retained.strategy_return.mean()) if len(retained) else None})
   if part=='forecast':alltest.append(f)
   for r in f.itertuples():
    known=bool(r.clean_return and r.fill_proxy==True)
    details.append({'日期':r.d0_date,'预测月':month,'集合':PARTS[part],'D0名次':int(r.d0_rank),'D0评分':r.score,
     '940候选':int(r.eligible),'拦截':int(r.reject) if r.eligible else None,
     '过滤前入选':int(r.selected_before),'过滤后入选':int(r.selected_after),'风险评分':r.risk_score,
     '坏样本':int(r.bad) if known else None,'三日再板':int(r.future_seal) if known else None,
     'D2涨幅':r.ret2 if known else None,'D3涨幅':r.ret3 if known else None,'D4涨幅':r.ret4 if known else None,
     '策略涨幅':r.strategy_return if known else None,'原连板':int(r.board_streak_days),'换手率':r.d0_turnover_pct/100,
     '流值估算亿':np.exp(r.log_float_mv_proxy)/1e8,'D1开涨':r.open_gap,'早盘涨幅':r.early_return,
     '早盘回吐':r.early_pullback,'早盘量比':r.early_volume_ratio,'当时ST':int(r.d1_st) if pd.notna(r.d1_st) else None,
     '分钟齐全':int(r.early_valid==True),'收益可用':int(known)})
 pd.DataFrame(details).to_pickle(OUT/'detail_export.pkl')
 pd.concat(alltest,ignore_index=True).to_pickle(OUT/'test_top5_scored.pkl')
 picks=pd.concat(allpicked,ignore_index=True);picks.to_pickle(OUT/'selected_trades.pkl')
 # Each monthly train/validation run stands alone; overlapping rows are not pooled into a pseudo-independent sample.
 performance=[];monthly=[];nav=[]
 exits={'d2_gross':'D2收盘','d3_gross':'D3收盘','d4_gross':'D4收盘','exit_gross':'策略卖出'}
 for part in PARTS:
  scopes=([('测试全期',picks[picks.part.eq(part)])]
    +[(year,picks[picks.part.eq(part)&picks.d0_date.str.startswith(year)]) for year in ('2025','2026')]
    if part=='forecast' else [(m,picks[picks.part.eq(part)&picks.evaluation_month.eq(m)]) for m in TEST_MONTHS])
  for scope,g in scopes:
   for policy in ('过滤前','过滤后'):
    q=g[g.policy.eq(policy)]
    start=((TEST_MONTHS[0] if scope in ('测试全期','2025') else '2026-01') if part=='forecast' else window(scope)['train' if part=='train_oof' else 'valid'][0])
    end=((cal[-1][:7] if scope in ('测试全期','2026') else '2026-01') if part=='forecast' else window(scope)['train' if part=='train_oof' else 'valid'][1])
    months=[str(m) for m in pd.period_range(start,end,freq='M')]
    for key,name in exits.items():
     s,mm,dd,_=account(q,key,cal,months)
     ctx={'范围':scope,'集合':PARTS[part],'方案':policy,'卖出':name}
     performance.append({**ctx,**s,'坏样本率':float(q.bad.mean()) if len(q) else None})
     if part=='forecast' and scope=='测试全期':
      monthly += [{**ctx,**v} for v in mm];nav += [{**ctx,**v} for v in dd]
 # Actual per-month selections with outcome-safe labels; net mean is a sensitivity, not reconstructed account NAV.
 for r in performance:r['扣20基点均涨']=r['每笔均涨']-.002 if r['每笔均涨'] is not None else None
 tables={'第一阶段':first,'风险过滤':risk,'交易效果':performance,'逐月收益':monthly,'月度模型':config,'前五明细':details}
 def safe(v):
  if isinstance(v,dict):return {k:safe(x) for k,x in v.items()}
  if isinstance(v,list):return [safe(x) for x in v]
  if isinstance(v,(float,np.floating)) and not np.isfinite(v):return None
  return v
 dump(OUT/'workbook_data.json',safe(tables))
 for name,rows in [('stage1_results',first),('risk_results',risk),('account_results',performance),('account_monthly',monthly),('daily_nav',nav)]:
  pd.DataFrame(rows).to_csv(OUT/f'{name}.csv',index=False)
 models=[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(OUT.glob('*/*.joblib'))]
 dump(OUT/'model_manifest.json',{'models':models,'spec_sha256':sha(OUT/'specification.json')})
 DOC.mkdir(parents=True,exist_ok=True)
 stats=pd.DataFrame(performance).query('集合 == "测试"')
 print(stats.to_string(index=False),flush=True)
 print('filters',pd.DataFrame(config)['过滤模型'].value_counts().to_dict(),flush=True)
 write_report(tables,models)


def write_report(tables,models):
 config=tables['月度模型'];test=[x for x in tables['交易效果'] if x['集合']=='测试' and x['范围']=='测试全期']
 lines=['# R15：六个月训练、两个月验证，D0 初选与 D1 09:40 风险过滤','',
 '每月冻结一次模型。第一阶段沿用 R14 的非 ST 日线候选及 D1—D3 再封板标签，仅把训练窗口改为六个月；历史 D1 开盘低于涨停价的样本参与第一阶段拟合。比较原 35 因子随机森林、更浅随机森林、35 因子逻辑回归。D0 先对完整标准封板池排序，Top3/Top5 固定后才统计开盘激活子集，避免把未打开的续板直接当成可交易命中。',
 '', '## 数据与切分','',
 '- 日线采用冻结快照：D0 至 2026-10-13，后续报价至 10-14；10 月不完整。2024 仅作第一阶段历史训练。完整两阶段测试为 2025-09 至 2026-10；例如 2025-09：2025-01—06 风险训练，07—08 验证，09 测试。',
 '- 第二阶段的六个月训练候选来自第一阶段各历史月的时间外预测。每个固定算法在历史月同样只用此前六个月、隔开两个月的日线训练；不使用最终模型在训练集上的拟合内 Top5。历史预热模型允许使用更早的数据，风险模型本身只有当轮六个月的样本。',
 '- 第一阶段按六个月因果折外 Top3/Top5 与两个月验证表现共同选择，惩罚两者落差；挑战者须两段均不低于原森林。训练拟合内仅诊断。两月验证用于选模与二段门槛，不能视为独立证明；下一月才是留出评估。所有月门槛先冻结再汇总测试。历史此前研究过，不称新盲测。',
 '- 所有训练／验证样本 D4 必须早于下一阶段起点；训练额外隔离仍在持续的连板事件。跨月滚动可在更晚月份使用当时已经知道结果的早期测试月。各轮训练及验证重复，不将这些重复行合成独立总体成绩。',
 '', '## 第二阶段','',
 '- D0 Top5 中，D1 09:40 收盘价低于当日涨停价、当时非 ST 且首十分钟齐全者为候选；开盘封板但 09:40 已打开者也可进入。本轮不叠加 D0 晚封板条件。',
 '- 八因子：原连板、D0 换手、流通市值估算对数、D0 全市场涨幅中位数、D1 开盘涨幅、前十分钟涨幅、前十分钟自高点回吐、前十分钟量／D0 全日量。均在 09:40 或更早可见；流值估算不能标成总市值。',
 '- 买入代理为下一分钟 09:41 开盘且低于涨停价、有成交量。选股先完成，再检查成交代理；失败不事后递补。分钟 K 仅支持价格代理，无法证明实际委托成交。分钟全日价量匹配仅用于事后质量核验，不作为决策因子。',
 '- 坏样本为 D2—D4 均未收盘封板、且 D4 收盘较 09:41 买入价亏损严格超过 5%。不把曾经盘中跌超 5% 自动当成坏样本。收益窗口不完整保持空白。',
 '- 二段仅比较强正则逻辑回归与最多两层、叶子至少 40 行的树；在风险训练段做前三月起步的前推评分，按其 10%／20%／30% 尾部分位冻结风险门槛。训练折外和验证都须提升坏样本拦截精度与保留样本收益，保留至少 65%；不满足则不过滤。二段同样不把验证并回训练。',
 '- 训练表中的二段最终评分属于二段拟合内诊断，选门槛另用风险模型的前推折外结果，保存在 risk_frozen.json；第一阶段训练候选始终是折外的。',
 '', '## 留出测试账户对照','',
 '|方案|退出|笔数|每笔毛均涨|账户毛收益|最大回撤|','|---|---|---:|---:|---:|---:|']
 for r in test:
  lines.append(f'|{r["方案"]}|{r["卖出"]}|{r["笔数"]}|{r["每笔均涨"]:.2%}|{r["账户总收益"]:.2%}|{r["账户最大回撤"]:.2%}|')
 lines+=['','账户使用与 R14 相同的现金约束：每日最多一笔，D0 名次最高的可买候选；过滤后在原 Top5 中递补。每笔最多前收盘净值 25%，不借款，按收盘价估值。D2/D3/D4 固定收盘退出，策略为 D2—D4 首次收盘封板退出、否则 D4 收盘退出。账户为毛收益；表另列每笔扣 20 基点的敏感性，未将其冒称净账户回放。异常收益在选股后剔除且不递补。',
  '',f'保存模型共 {len(models)} 个，均带依赖版本、训练键及哈希；二段模型重载概率完全一致。逐月选择与所有候选阈值在本机 `{OUT}`。',
  '', '云盘归档按 `涨停冷静期/D0判断-D1开盘伺机买入/20261015` 组织；本轮先本地保存，未声称上传或入库。']
 (DOC/'README.md').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','report']);a=p.parse_args();globals()[a.stage]()
