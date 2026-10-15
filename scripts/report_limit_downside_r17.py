"""Summaries and a frozen overlay on prior strategies; never selects R17 models."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd,joblib
from scripts.experiment_limit_downside_r17 import OUT,MONTHS,metrics,predict,X
from scripts.experiment_limit_rebound_short_mixed_r12 import dump,sha

PARTS={'train':'训练拟合内','cv':'训练交叉验证','valid':'验证','test':'测试'}
FEATURE_NAMES={
 'board_streak_days':'原连板','streak_return':'连板累计涨幅','d0_return':'D0涨幅','d0_open_gap':'D0开涨',
 'd0_range':'D0振幅','d0_lower_shadow':'D0下影','d0_close_position':'D0收盘位置','first_open_gap':'首板开涨',
 'first_range':'首板振幅','first_one_price':'首板一字','d0_volume_ratio_pre5':'五日量比','d0_amount_ratio_pre5':'五日额比',
 'd0_turnover_pct':'换手率','d0_amount_100m':'成交额亿','d0_volume_ratio_first':'首板量比','d0_turnover_ratio_first':'首板换手比',
 'first_turnover_pct':'首板换手率','pre5_return':'前五日涨幅','pre5_mean_turnover_pct':'前五日换手',
 'market_limit_count':'市场涨停数','market_prev_limit_count':'昨日涨停数','market_up_fraction':'市场上涨占比',
 'market_median_return':'市场涨幅中位','limit_rate':'涨停幅度','d0_one_price':'D0一字','range_to_limit':'振幅相对板幅',
 'gap_to_limit':'开涨相对板幅','turnover_ratio_pre5':'换手放大倍数','pre5_volatility':'前五日波动',
 'pre5_up_count':'前五日涨停数','market_reseal_fraction':'市场连板比例','amount_board_rank':'成交额分位',
 'turnover_board_rank':'换手率分位','log_float_mv_proxy':'流值估算对数','log_price':'价格对数'}


def screening(f,flag='flag20'):
    g=f[f.risk_known].copy();r=g[g[flag]];k=g[~g[flag]]
    bad=g.risk_y.eq(1);safe=g.risk_y.eq(0);profit=g.risk_return.gt(0)
    div=lambda a,b:float(a/b) if b else None
    return {'known':len(g),'flagged':len(r),'kept':len(k),'bad':int(bad.sum()),
      'caught_bad':int(r.risk_y.eq(1).sum()),'killed_safe':int(r.risk_y.eq(0).sum()),'killed_up':int(r.risk_return.gt(0).sum()),
      'flag_rate':div(len(r),len(g)),'base_bad_rate':float(bad.mean()),
      'flag_bad_precision':float(r.risk_y.eq(1).mean()) if len(r) else None,
      'bad_recall':div(r.risk_y.eq(1).sum(),bad.sum()),'safe_kill_rate':div(r.risk_y.eq(0).sum(),safe.sum()),
      'up_kill_rate':div(r.risk_return.gt(0).sum(),profit.sum()),
      'kept_bad_rate':float(k.risk_y.eq(1).mean()) if len(k) else None,
      'mean_return':float(g.risk_return.mean()),'kept_mean_return':float(k.risk_return.mean()) if len(k) else None,
      'flagged_mean_return':float(r.risk_return.mean()) if len(r) else None}


def flag_frame(f,s):
    for q,v in s['thresholds'].items():f['flag'+str(round((1-float(q))*100))]=f.risk_score.ge(v)
    return f


def summary_row(part,scope,f):
    a=metrics(f);b=screening(f)
    return {'集合':PARTS[part],'范围':scope,'可标注数':b['known'],'两极数':a['poles'],
      '两极AUC':a['auc'],'两极AP':a['ap'],'平衡准确率':a['balanced_accuracy_at_half'],
      '原大跌率':b['base_bad_rate'],'标记比例':b['flag_rate'],'标记大跌率':b['flag_bad_precision'],
      '大跌检出率':b['bad_recall'],'非大跌误杀率':b['safe_kill_rate'],'上涨误杀率':b['up_kill_rate'],
      '保留大跌率':b['kept_bad_rate'],'原三日均涨':b['mean_return'],'保留三日均涨':b['kept_mean_return']}


def augment_old(old,selected,p):
    old=old.copy();old['old_risk_score']=old.risk_score
    # Existing risk_score means the old D1 filter. Keep it separate from new independent D0 score.
    old=old.drop(columns=['risk_score'])
    results=[]
    for month,g in old.groupby('evaluation_month'):
        s=selected[month];b=joblib.load(OUT/month/s['chosen']/'full.joblib')
        g=g.copy();g['risk_score']=predict(b,g);g=flag_frame(g,s)
        # D0 universe eligibility is known at decision time. Future label quality never controls a veto.
        g['r17_covered']=g.close.sub(g.d0_cap).abs().le(.005)&g.d0_st.eq(0)&g.up.eq(True)
        g.loc[~g.r17_covered,'risk_score']=np.nan
        for flag in ('flag10','flag20','flag30'):g.loc[~g.r17_covered,flag]=False
        details=p[['symbol','d0_date','risk_known','risk_y','risk_return','risk_group','worst_close_return']]
        g=g.merge(details,on=['symbol','d0_date'],how='left',validate='one_to_one')
        g['r17_quality_ok']=g.risk_known.eq(True)
        # Outside-domain D0 rows have no new signal and leave the original decision unchanged.
        results.append(g)
    return pd.concat(results,ignore_index=True)


def overlay(p,selected):
    comparisons=[];monthly=[];detail=[];evidence={}
    for name,folder in [('R15','limit_rebound_two_stage_r15_20261015'),('R16','limit_rebound_search_r16_20261015')]:
        root=Path('outputs/stock_automl')/folder
        top=augment_old(pd.read_pickle(root/'test_top5_scored.pkl'),selected,p)
        top['experiment']=name;detail.append(top)
        t=pd.read_pickle(root/'selected_trades.pkl');t=t[t.part.eq('forecast')&t.policy.eq('过滤后')]
        tr=augment_old(t,selected,p)
        tr.to_pickle(OUT/f'{name.lower()}_trade_overlay.pkl');top.to_pickle(OUT/f'{name.lower()}_top5_overlay.pkl')
        evidence[name]={'selected_trade_source_sha256':sha(root/'selected_trades.pkl'),'top5_source_sha256':sha(root/'test_top5_scored.pkl')}
        for scope,base in [('D0前五',top),('原实际交易',tr)]:
            for policy,g in [('原名单',base),('保留',base[~base.flag20]),('标记',base[base.flag20])]:
                known=g[g.r17_quality_ok];tradable=g[g.clean_return.eq(True)&g.fill_proxy.eq(True)&g.eligible.eq(True)]
                if scope=='原实际交易':tradable=g
                comparisons.append({'原模型':name,'范围':scope,'处理':policy,'数量':len(g),'模型覆盖数':int(g.r17_covered.sum()),
                   '可标注数':len(known),'三日大跌率':float(known.risk_y.eq(1).mean()) if len(known) else None,
                   'D1-3再板率':float(g.loc[g.observed_3,'label_3'].mean()),
                   'D0至D3均涨':float(known.risk_return.mean()) if len(known) else None,
                   '交易样本数':len(tradable),'D2均涨':float(tradable.ret2.mean()) if len(tradable) else None,
                   'D3均涨':float(tradable.ret3.mean()) if len(tradable) else None,
                   'D4均涨':float(tradable.ret4.mean()) if len(tradable) else None,
                   '策略均涨':float(tradable.strategy_return.mean()) if len(tradable) else None})
        for month,g in tr.groupby('evaluation_month'):
            for policy,h in [('原名单',g),('保留',g[~g.flag20])]:
                monthly.append({'月份':month,'原模型':name,'处理':policy,'笔数':len(h),
                  'D2均涨':float(h.ret2.mean()) if len(h) else None,
                  'D3均涨':float(h.ret3.mean()) if len(h) else None,
                  'D4均涨':float(h.ret4.mean()) if len(h) else None,
                  '策略均涨':float(h.strategy_return.mean()) if len(h) else None})
    dump(OUT/'overlay_source_manifest.json',evidence)
    allrows=pd.concat(detail,ignore_index=True);rows=[]
    for r in allrows.itertuples():
        rows.append({'日期':r.d0_date,'原模型':r.experiment,'原排名':int(r.d0_rank),'原入选':int(r.selected_after),
          '风险评分':float(r.risk_score),'风险标记':int(r.flag20),'标签':r.risk_group if isinstance(r.risk_group,str) else '待核验',
          '三日涨幅':r.risk_return,'三日最低收涨':r.worst_close_return,
          'D1-3再板':int(r.label_3) if r.observed_3 else None,
          'D2收益':r.ret2 if r.selected_after else None,'D3收益':r.ret3 if r.selected_after else None,
          'D4收益':r.ret4 if r.selected_after else None,'策略收益':r.strategy_return if r.selected_after else None,
          '原连板':int(r.board_streak_days),'换手率':r.d0_turnover_pct/100,
          '成交额亿':r.d0_amount_100m,'流值估算亿':float(np.exp(r.log_float_mv_proxy)/1e8) if np.isfinite(r.log_float_mv_proxy) else None,
          '是否ST':int(r.d0_st)})
    return comparisons,monthly,rows


def report():
    p=pd.read_pickle(OUT/'panel.pkl');selections=json.loads((OUT/'all_selections_frozen.json').read_text());selected={s['month']:s for s in selections}
    alltest=pd.read_pickle(OUT/'test_scored.pkl');monthly=[];conditions=[];candidates=[];saved=[];summaries=[];metrics_by={}
    for s in selections:
        month=s['month'];c=next(r for r in s['candidates'] if r['config']['id']==s['chosen']);config=c['config'];pr=config['parameters']
        conditions.append({'预测月':month,'训练起':s['window']['train'][0],'训练止':s['window']['train'][1],
         '验证起':s['window']['valid'][0],'验证止':s['window']['valid'][1],'组合':s['chosen'],'算法':config['algorithm'],
         '采样':config['scheme'],'样本比例':.8,'种子':'171+191','树数':pr.get('n_estimators'),'树深':pr.get('max_depth'),
         '最小叶样本':pr.get('min_samples_leaf',pr.get('min_child_samples')),'叶数':pr.get('num_leaves'),
         '学习率':pr.get('learning_rate'),'正则L2':pr.get('reg_lambda'),'逻辑C':pr.get('C'),
         '训练CVAUC':c['cv']['auc'],'验证AUC':c['valid']['auc'],'风险门槛':s['thresholds']['0.8']})
        for item in s['candidates']:
            candidates.append({'预测月':month,'组合':item['config']['id'],'选中':int(item['config']['id']==s['chosen']),
             '训练AUC':item['train']['auc'],'训练CVAUC':item['cv']['auc'],'验证AUC':item['valid']['auc'],
             '月度波动':item['month_auc_std'],'选模分':item['score']})
        for part in ('train','cv','valid','test'):
            if part=='test':f=alltest[alltest.evaluation_month.eq(month)].copy()
            else:
                scores=pd.read_pickle(OUT/month/s['chosen']/f'{part}_scores.pkl');f=p.loc[scores.index].copy();f['risk_score']=scores.risk_score
                f=flag_frame(f,s)
            f['part']=part;f['evaluation_month']=month;saved.append(f)
            monthly.append(summary_row(part,month,f))
            metrics_by.setdefault(part,[]).append({'month':month,'classification':metrics(f),'screening':screening(f)})
    full=pd.concat(saved,ignore_index=True);full.to_pickle(OUT/'chosen_all_parts_scored.pkl')
    for part in ('train','cv','valid'):
        rows=[r for r in monthly if r['集合']==PARTS[part]]
        med={'集合':PARTS[part],'范围':'14窗口中位'}
        for key in rows[0]:
            if key not in med:med[key]=float(np.median([r[key] for r in rows]))
        summaries.append(med)
    summaries.append(summary_row('test','去重全期',alltest))
    sensitivity=[]
    for flag in ('flag10','flag20','flag30'):
        for scope,g in [('全部',alltest),('首板',alltest[alltest.board_streak_days.eq(1)]),('连板',alltest[alltest.board_streak_days.ge(2)])]:
            b=screening(g,flag)
            sensitivity.append({'名义标记比例':int(flag[4:])/100,'范围':scope,'样本数':b['known'],'原大跌数':b['bad'],
              '标记数':b['flagged'],'截获大跌':b['caught_bad'],'误杀非大跌':b['killed_safe'],'误杀上涨':b['killed_up'],
              '实际标记比例':b['flag_rate'],'标记大跌率':b['flag_bad_precision'],'大跌检出率':b['bad_recall'],
              '非大跌误杀率':b['safe_kill_rate'],'保留大跌率':b['kept_bad_rate'],'原三日均涨':b['mean_return'],
              '保留三日均涨':b['kept_mean_return']})
    comp,om,detail=overlay(p,selected)
    def clean(o):
        if isinstance(o,dict):return {k:clean(v) for k,v in o.items()}
        if isinstance(o,list):return [clean(v) for v in o]
        if isinstance(o,(float,np.floating)) and not np.isfinite(o):return None
        return o
    data={'总体效果':summaries,'逐月效果':monthly,'过滤强度':sensitivity,'条件组合':conditions,'原模型核验':comp,
          '原交易逐月':om,'前五核验':detail}
    dump(OUT/'workbook_data.json',clean(data));dump(OUT/'candidate_comparison.json',candidates)
    dump(OUT/'report_metrics.json',clean({'summary':summaries,'test_sensitivity':sensitivity,'overlay':comp,'monthly':metrics_by}))
    doc=Path('docs/stock_automl_runs/20261015_limit_downside_r17');doc.mkdir(parents=True,exist_ok=True)
    dump(doc/'result_snapshot.json',clean({'summary':summaries,'test_sensitivity':sensitivity,'overlay':comp,'conditions':conditions}))
    good=alltest[alltest.risk_known];z=screening(alltest)
    compact=pd.DataFrame(summaries)[['集合','范围','两极AUC','两极AP','原大跌率','标记大跌率','大跌检出率','非大跌误杀率','保留大跌率']]
    for c in ['原大跌率','标记大跌率','大跌检出率','非大跌误杀率','保留大跌率']:compact[c]=compact[c].map(lambda x:f'{x:.2%}')
    for c in ['两极AUC','两极AP']:compact[c]=compact[c].map(lambda x:f'{x:.4f}')
    trades=pd.DataFrame(comp);trades=trades[trades['范围'].eq('原实际交易')][['原模型','处理','数量','三日大跌率','D2均涨','D3均涨','D4均涨','策略均涨']]
    for c in ['三日大跌率','D2均涨','D3均涨','D4均涨','策略均涨']:trades[c]=trades[c].map(lambda x:f'{x:.2%}')
    lower=sum(r['screening']['kept_bad_rate']<r['screening']['base_bad_rate'] for r in metrics_by['test'])
    text='\n\n## 结果\n\n'
    text+=f'测试可标注 D0：{good.d0_date.min()} 至 {good.d0_date.max()}，{len(good):,} 条；模型评分还覆盖期末未完整观察的记录，但未将其作为负例。14 个滚动窗口的 AUC、AP 均只计算两极样本。AUC 的 0.5 约相当于无区分，越接近 1 越好；AP 需结合大跌类占比看。\n\n'
    text+=compact.to_markdown(index=False)+'\n\n'
    text+=f'主门槛在测试中标记 {z["flagged"]:,}/{z["known"]:,} 条（{z["flag_rate"]:.2%}），截获 {z["caught_bad"]:,}/{z["bad"]:,} 个大跌；误杀非大跌 {z["killed_safe"]:,} 条，其中上涨 {z["killed_up"]:,} 条。保留池大跌比例改善 {lower}/14 个月。D0 至 D3 平均价格变化 {z["mean_return"]:.2%}→{z["kept_mean_return"]:.2%}，不能把它解释为可执行账户收益。\n\n'
    text+='### 原实际交易名单的冻结核验\n\n'+trades.to_markdown(index=False)+'\n\n'
    text+='上表“保留”是原交易经过新模型标记后剩下的交易；“标记”是新模型建议放弃的原交易，不是新选股名单。具体逐月结果、全部过滤强度、每月参数及前五明细见 Excel。\n'
    path=doc/'README.md';old=path.read_text();path.write_text(old.split('\n\n## 结果\n')[0]+text)
    print(json.dumps({'summary':summaries,'overlay':comp},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':report()
