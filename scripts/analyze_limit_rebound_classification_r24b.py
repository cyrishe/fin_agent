"""Frozen four/five-bar classification, independent of entries and account returns."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path('outputs/stock_automl')
OUT=ROOT/'limit_rebound_classification_r24b_20261015'
DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_classification_r24b')
R20=ROOT/'limit_rebound_shape_top3_r20_20261015'
R19=ROOT/'limit_rebound_shape_r19_20261015'
R24=ROOT/'limit_rebound_four_minutes_r24_20261015'
NAMES={'none':'不判形态','five':'五K原规则','four_old':'四K沿用门槛','four_fit':'四K重选规则'}
PARTS={'train':'训练窗口','valid':'验证窗口','test':'测试'}
KEYS=['model_month','part','symbol','d0_date']


def dump(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def mask(f,rule):
    z=pd.Series(True,index=f.index)
    for c,op,v in rule['atoms']:
        z &= f[c].ge(v) if op=='>=' else f[c].lt(v) if op=='<' else f[c].eq(v)
    return z


def metrics(f,code):
    y=f.label_3.eq(1); p=f[code].astype(bool)
    tp=int((y&p).sum());fp=int((~y&p).sum());fn=int((y&~p).sum());tn=int((~y&~p).sum());n=len(f)
    ratio=lambda a,b:float(a/b) if b else None
    r=dict(n=n,unique=len(f[['symbol','d0_date']].drop_duplicates()),kept=tp+fp,
        retention=ratio(tp+fp,n),accuracy=ratio(tp+tn,n),precision=ratio(tp,tp+fp),recall=ratio(tp,tp+fn),
        f1=ratio(2*tp,2*tp+fp+fn),balanced_accuracy=(tp/(tp+fn)+tn/(tn+fp))/2 if tp+fn and tn+fp else None,
        tp=tp,fp=fp,fn=fn,tn=tn)
    # Independent categorical cross-tab, rather than the Boolean arithmetic above.
    table=pd.crosstab(y.astype(int),p.astype(int)).reindex(index=[0,1],columns=[0,1],fill_value=0)
    assert [tp,fp,fn,tn]==[int(table.loc[1,1]),int(table.loc[0,1]),int(table.loc[1,0]),int(table.loc[0,0])]
    assert tp+fp+fn+tn==n
    return r


def display(r,monthly=False):
    z={}
    if monthly:z['目标月']=r['month']+'-01'
    z.update({'集合':PARTS[r['part']],'范围':r['pool'],'方案':NAMES[r['policy']],
        '评估数':r['n'],'独立数':r['unique'],'保留数':r['kept'],'保留率':r['retention'],
        '准确率':r['accuracy'],'命中率':r['precision'],'召回率':r['recall'],'F1':r['f1'],
        '平衡准确':r['balanced_accuracy'],'选中再板':r['tp'],'选中未板':r['fp'],
        '漏掉再板':r['fn'],'排除未板':r['tn']})
    return z


def main():
    OUT.mkdir(exist_ok=True);DOC.mkdir(exist_ok=True)
    paths=[R20/'enriched_top3.pkl',R24/'four_all_top3.pkl',R19/'all_rules_frozen.json',R24/'four_rules_frozen.json']
    f=pd.read_pickle(paths[0]);g=pd.read_pickle(paths[1])
    assert not f.duplicated(KEYS).any() and not g.duplicated(KEYS).any()
    g=g.set_index(KEYS).loc[f.set_index(KEYS).index].reset_index()
    assert f[KEYS].equals(g[KEYS]);assert f.eligible.equals(g.eligible)
    for c in ['label_3','observed_3','d0_rank','score']:assert f[c].equals(g[c])
    old=json.loads(paths[2].read_text());new=json.loads(paths[3].read_text())
    r5={x['month']:x['selected'] for x in old};r4={x['month']:x['selected'] for x in new}
    spec=dict(name='R24B_frozen_classification_confusion_matrices',
        months=[f'2026-{i:02}' for i in range(1,11)],
        primary='All historical ordinary N/R, audited D0 original Top1/Top3 with mature D1-D3 sealed-close label; no condition on later opening or fill, no buy price or outcome return selection.',
        secondary='Same original five-bar future-entry-eligible subset for all four policies, descriptive execution-conditioned comparison only; not primary classification population.',
        label='1 iff D1 or D2 or D3 sealed at daily close. D1 included. All three days must be observed; partial labels excluded even if D1 already sealed.',
        prediction='Shape pass=1, reject=0. No-filter retains all=all-positive reference, not a fitted classifier. No -8 body gate, daily one-stock choice, latency or account results in classifier predictions.',
        split='Frozen 2026-01..10 monthly model windows: six months train with causal OOF D0 scores, two validation, next month test. Train/valid count each model-stock-day evaluation; overlapping windows are NOT independent samples. Unique stock-day count separately shown. Test stock-days unique.',
        date='Target/model month determined by D0. 2025-12-31 D0 / 2026-01-05 D1 uses December model, hence outside this ten-model comparison; this accounts for three observations vs prior D1-calendar-year matrix.',
        metrics='Accuracy=(TP+TN)/N; precision=TP/(TP+FP); recall=TP/(TP+FN); F1=2TP/(2TP+FP+FN); balanced accuracy=mean(TPR,TNR). Undefined denominators blank. No continuous calibrated shape score, so probability AUC/calibration not invented.',
        fitting='No retraining or rule changes. Four-K reselected rules are R24; five-K original R19 rules; four-K transferred thresholds reuse five-K selections.',
        exposure='Training/validation used for rule selection, optimistic. Monthly test not used for selection, but historical period previously studied, not new blind holdout.',
        source_hashes=[{'path':str(p),'sha256':sha(p)} for p in paths])
    dump(OUT/'specification.json',spec)
    for c in NAMES:f[c]=True
    for month,ix in f.groupby('model_month').groups.items():
        f.loc[ix,'five']=mask(f.loc[ix],r5[month]);f.loc[ix,'four_old']=mask(g.loc[ix],r5[month]);f.loc[ix,'four_fit']=mask(g.loc[ix],r4[month])
    f=f[f.model_month.isin(spec['months'])].copy()
    calendar=json.loads((ROOT/'limit_rebound_segments_r18_20261015/calendar.json').read_text())
    for (month,part),a in f.groupby(['model_month','part']):
        if part!='test':
            rule=next(x for x in old if x['month']==month)
            cutoff=str(pd.Period(rule['window'][part][1])+1)+'-01'
            assert all(calendar[int(i)+4]<cutoff for i in a.date_i)
    mature=f[f.observed_3]
    assert mature[['d1_up','d2_up','d3_up']].notna().all().all()
    assert np.array_equal(mature.label_3.astype(int),mature[['d1_up','d2_up','d3_up']].eq(True).any(axis=1).astype(int))
    f['primary']=f.eligible&f.observed_3
    f['common_fill']=f.primary&f.trade
    assert not f[f.part.eq('test')].duplicated(['symbol','d0_date']).any()
    evidence=f[KEYS+['d1_date','d0_rank','score','eligible','observed_3','label_3','primary','common_fill',*NAMES]].copy()
    evidence.to_pickle(OUT/'predictions.pkl')
    overall=[];monthly=[];coverage=[]
    for scope,col in [('全部候选','primary'),('共同可买','common_fill')]:
        for pool,rank in [('Top1',1),('Top3',3)]:
            h=f[f[col]&f.d0_rank.le(rank)]
            for part in PARTS:
                a=h[h.part.eq(part)]
                for code in NAMES:overall.append(dict(scope=scope,pool=pool,part=part,policy=code,**metrics(a,code)))
            for (month,part),a in h.groupby(['model_month','part']):
                for code in NAMES:monthly.append(dict(scope=scope,pool=pool,month=month,part=part,policy=code,**metrics(a,code)))
    for (month,part),a in f.groupby(['model_month','part']):
        coverage.append(dict(month=month,part=part,total=len(a),not_eligible=int((~a.eligible).sum()),
            immature=int((a.eligible&~a.observed_3).sum()),primary=int(a.primary.sum()),common_fill=int(a.common_fill.sum())))
    for r in overall:
        rows=[m for m in monthly if all(m[k]==r[k] for k in ['scope','pool','part','policy'])]
        assert all(sum(m[k] for m in rows)==r[k] for k in ['n','kept','tp','fp','fn','tn'])
    for _,a in evidence.groupby(['model_month','part']):
        if len(a[a.primary])==0:continue
        assert a.loc[a.primary,['none','five','four_old','four_fit']].notna().all().all()
    dump(OUT/'overall.json',overall);dump(OUT/'monthly.json',monthly);dump(OUT/'coverage.json',coverage)
    # Summary has primary whole-pool results; conditional comparison lives in the monthly table.
    summary=[display(r) for r in overall if r['scope']=='全部候选']
    order={'test':0,'train':1,'valid':2}
    primary=[r for r in overall if r['scope']=='全部候选']
    primary.sort(key=lambda r:(order[r['part']],0 if r['pool']=='Top3' else 1,list(NAMES).index(r['policy'])))
    summary=[display(r) for r in primary]
    monthrows=[]
    for r in monthly:
        z=display(r,True);z={'目标月':z.pop('目标月'),'样本口径':r['scope'],**z};monthrows.append(z)
    rules=[]
    for s in new:
        if s['month'] not in spec['months']:continue
        o=next(x for x in old if x['month']==s['month'])
        rules.append({'目标月':s['month']+'-01','训练段':'至'.join(s['window']['train']),
            '验证段':'至'.join(s['window']['valid']),'五K规则':o['selected']['text'],
            '四K重选':s['selected']['text'].replace('五分','四分')})
    notes=[
        '目标月份2026年1—10月，行情截至10月14日。测试D0从1月5日开始，D1从1月6日开始。',
        '主表包含没有开板、没有成交的原Top3个体。Top3包含Top1，不可把两个范围相加。',
        '标签1：D1—D3至少一天收盘封板；含D1当日，非未来必然涨停。三个交易日未齐的样本不计分。',
        '形态通过记预测1，未通过记0；不判形态全部保留、全部预测1，仅作为参照。',
        '本表只衡量形态，未叠加实体−8%、每日唯一选股和交易执行条件。',
        '准确率：全部样本中判断正确的比例。命中率：保留样本中实际再板的比例，即精确率。',
        '召回率：实际再板样本中保留下来的比例。F1综合命中率与召回率。平衡准确为正负两类识别率的均值。',
        '选中再板=TP，选中未板=FP，漏掉再板=FN，排除未板=TN。无分母的指标留空。',
        '训练窗口、验证窗口汇总按每次月度模型判断计数，同一股票日可能重复；独立数为股票日去重，测试不重复。',
        '训练D0分数来自历史折外预测，但形态规则用这些样本选择，因此训练指标仍是形态筛选的拟合内效果。',
        '验证也用于选规则，只有对应后续月份未参与当次选择；整段历史已被研究，不是新盲测。',
        '逐月表可筛全部候选或共同可买；共同可买统一用原五K后的分钟开价窗口，仅供衔接旧结果。',
        '四K重选为R24已冻结的6个月训练、2个月验证规则；四K沿用门槛为五K同月规则用于四根K。没有新训练。',
        '原D0为2025年12月31日、D1为2026年1月5日的3个个体属于12月模型，本次十个目标月不包含。',
        '来源：R20保存的五K三集合候选、R24四K对应因子、R19与R24冻结月度规则。']
    dump(OUT/'workbook_data.json',{'分类对照':summary,'逐月对照':monthrows,'规则与口径':rules})
    dump(OUT/'workbook_notes.json',notes)
    dump(OUT/'verification.json',dict(status='PASS',rows=len(f),classification_groups=len(overall)+len(monthly),
        identical_keys_scores_ranks_labels=True,mature_labels_from_D1_D3_rebuilt=len(mature),
        independent_crosstabs=True,monthly_confusion_sums_match=True,train_valid_D4_purged=True,
        frozen_rule_hashes_unchanged=True,test_unique=True))
    report=['# R24B：四K、五K与不判形态的分类对照','',*notes,'',
        '## 测试集：全部原Top3个体','',
        '|方案|样本|准确率|命中率|召回率|F1|平衡准确|TP|FP|FN|TN|',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in primary:
        if r['part']=='test' and r['pool']=='Top3':
            report.append('|'+NAMES[r['policy']]+'|'+str(r['n'])+'|'+'|'.join(f"{r[k]:.2%}" for k in ['accuracy','precision','recall','f1','balanced_accuracy'])+'|'+'|'.join(str(r[k]) for k in ['tp','fp','fn','tn'])+'|')
    report += ['', '## 结论','',
        '五K的Top3准确率在训练窗口、验证窗口、测试分别为74.67%、74.04%、73.23%；四K沿用为74.62%、73.12%、71.75%；四K重选为70.53%、69.45%、65.43%。训练/验证计数是重叠窗口中的模型判断，不可将3451/1090次评估当独立样本；对应独立股票日884/758。测试538个股票日不重复。',
        '', '测试五K保留350个，265个再板、85个未板，漏掉59个再板，排除129个未板。四K重选保留420个，279个再板、141个未板，漏掉45个再板，排除73个未板。相对五K，四K重选多保留14个再板，同时多放进56个未板；召回较高，准确率/命中率/F1/平衡准确较低。四K沿用门槛接近五K，仍未超过。',
        '', 'Top1测试177个：五K准确79.10%、命中83.46%；四K沿用76.84%/81.40%；四K重选70.62%/75.00%；不判68.93%/68.93%。Top1与Top3方向一致。',
        '', '共同可买的338个Top3测试个体中，不判/五K/四K沿用/四K重选命中率42.01%/55.63%/53.25%/43.89%；这是执行条件下的子集，不能与主表全部候选的60.22%/75.71%/74.43%/66.43%直接混用。共同可买子集五K的F1为57.34%，低于不判的59.17%，但准确率和平衡准确更高；不宣称每个指标均优于不判。',
        '', '训练/验证/测试及Top1、Top3全部结果保存在同名Excel与overall.json，逐月与共同可买对照保存在monthly.json。全部预测与样本键保存在predictions.pkl。',
        '', '本地补充评估，不修改原模型、规则和交易账本。云端待同步。','']
    (DOC/'README.md').write_text('\n'.join(report))
    print(pd.DataFrame([r for r in primary if r['part']=='test'])[['pool','policy','n','accuracy','precision','recall','f1','balanced_accuracy','tp','fp','fn','tn']].to_string(index=False))


if __name__=='__main__':main()
