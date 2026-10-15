"""R18 fixed comparisons and frozen monthly choices, with concise review tables."""
from __future__ import annotations
import json
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score
from scripts.experiment_limit_rebound_segments_r18 import OUT,FEATURES,NAMES,ALIGNED,EXTRA,scores
from scripts.experiment_limit_rebound_two_stage_r15 import TEST_MONTHS,classify
from scripts.experiment_limit_rebound_short_mixed_r12 import dump
from scripts.experiment_limit_rebound_monthly_r14 import block

PARTS={'train':'训练拟合内','oof':'训练折外','valid':'验证','test':'测试'}
DEFINITIONS={
 'normal_days5':('板前天数','本轮首板之前连续非封板区间，最近最多5天；不足不补齐'),
 'normal_return5':('板前涨幅','板前区间最近最多5天的复合涨幅'),
 'normal_turnover5':('板前换手','板前区间最近最多5天平均换手，原值单位为百分数'),
 'normal_volatility5':('板前波动','同一板前区间日涨幅样本标准差；不足2天缺失'),
 'd0_normal_volume_ratio':('D0量比','D0成交量/板前最近最多5天均量'),
 'd0_normal_amount_ratio':('D0额比','D0成交额/板前最近最多5天均额'),
 'd0_normal_turnover_ratio':('D0换手比','D0换手/板前最近最多5天平均换手'),
 'normal_days10':('板前长天数','本轮首板前连续非封板区间，最近最多10天'),
 'normal_return10':('板前长涨幅','板前最近最多10天复合涨幅'),
 'normal_volatility10':('板前长波动','板前最近最多10天日涨幅样本标准差'),
 'normal_range5':('板前振幅','板前最近最多5天平均(最高-最低)/昨收'),
 'normal_drawdown5':('板前回撤','同一板前区间收盘序列相对此前峰值的最大跌幅，含区间起始昨收'),
 'board_normal_volume_ratio':('板段量比','本轮首板至D0均量/板前均量'),
 'board_normal_amount_ratio':('板段额比','本轮首板至D0均额/板前均额'),
 'board_normal_turnover_ratio':('板段换手比','本轮首板至D0平均换手/板前平均换手'),
 'board_turnover_sum':('板段总换手','本轮首板至D0换手率相加；原值单位为百分数'),
 'board_one_price_fraction':('板段一字比','本轮首板至D0一字且有成交量的天数/连板天数'),
 'board_range_mean':('板段振幅','本轮首板至D0平均(最高-最低)/昨收'),
 'board_volume_cv':('板段量波动','板段成交量总体标准差/板段均量'),
 'board_volume_slope':('板段量趋势','对板段日序号回归成交量的斜率/板段均量；首板为0'),
 'd0_board_volume_ratio':('D0板段量比','D0成交量/本轮首板至D0均量'),
 'normal_volume_ratio5_10':('板前短长量比','板前最近最多5天均量/最近最多10天均量'),
 'board_price_ok':('板段连续','板段OHLCV合法且日间昨收与上日收盘差不超过0.011元'),
}


def flatten(m,variant,part,month=None):
    a=m['open'];b=m['all']
    return {'预测月':month,'方案':NAMES[variant],'集合':PARTS[part],'可标注数':a['known'],
      '开盘样本再板率':a['rate'],'开盘AP':a['ap'],
      'Top1数':a['top1']['n'],'Top1中':a['top1']['hits'],'Top1命中率':a['top1']['rate'],
      'Top3数':a['top3']['n'],'Top3中':a['top3']['hits'],'Top3命中率':a['top3']['rate'],
      'Top5数':a['top5']['n'],'Top5中':a['top5']['hits'],'Top5命中率':a['top5']['rate'],
      '全池Top1':b['top1']['rate'],'全池Top3':b['top3']['rate'],'全池Top5':b['top5']['rate']}


def paired_interval(a,b,k):
    """Month-cluster bootstrap, exploratory uncertainty, never a selection input."""
    stats=[]
    for month in TEST_MONTHS:
        row=[]
        for f in (a,b):
            q=f[f.d0_date.str.startswith(month)&f.activated&f.observed_3&f.d0_rank.le(k)]
            row.extend([int(q.label_3.sum()),len(q)])
        stats.append(row)
    x=np.array(stats);rng=np.random.default_rng(20261015)
    draw=x[rng.integers(0,len(x),(4000,len(x)))].sum(axis=1)
    delta=draw[:,0]/draw[:,1]-draw[:,2]/draw[:,3]
    return [float(v) for v in np.quantile(delta,[.025,.975])]


def run():
    p=pd.read_pickle(OUT/'panel.pkl');sel=json.loads((OUT/'all_selections_frozen.json').read_text())
    monthly=[];summary=[];conditions=[];testframes={};selected=[];metrics={}
    calendar=json.loads((OUT/'calendar.json').read_text())
    for s in sel:
        month=s['month'];variant=s['chosen'];w=s['window']
        conditions.append({'预测月':month,'训练起':w['train'][0],'训练止':w['train'][1],
          '验证起':w['valid'][0],'验证止':w['valid'][1],'方案':NAMES[variant],
          '变量数':len(FEATURES[variant]),'算法':'随机森林','树数':240,'树深':7,'最小叶样本':50})
        for r in s['candidates']:
            for part in ['train','oof','valid']:
                monthly.append(flatten(r[part],r['variant'],part,month))
            if r['variant']==variant:
                for part in ['train','oof','valid']:
                    monthly.append(flatten(r[part],'selected',part,month))
    for variant in FEATURES:
        alltest=[]
        for s in sel:
            month=s['month'];f=scores(p,month,variant,'forecast');f['variant']=variant
            va=block(p,calendar,*s['window']['valid'],purge_events=False)
            previous_events=pd.MultiIndex.from_frame(va[['symbol','first_limit_date']])
            f['prior_validation_event']=pd.MultiIndex.from_frame(f[['symbol','first_limit_date']]).isin(previous_events)
            f['evaluation_month']=month;alltest.append(f)
            m=classify(f);monthly.append(flatten(m,variant,'test',month))
            if s['chosen']==variant:
                selected.append(f);monthly.append(flatten(m,'selected','test',month))
        testframes[variant]=pd.concat(alltest,ignore_index=True)
    testframes['selected']=pd.concat(selected,ignore_index=True).sort_values(['d0_date','d0_rank'])
    for variant,f in testframes.items():
        assert not f.duplicated(['symbol','d0_date']).any()
        f.to_pickle(OUT/f'{variant}_test_scored.pkl')
        metrics[variant]=classify(f)
        summary.append(flatten(metrics[variant],variant,'test'))
    mf=pd.DataFrame(monthly)
    for variant in testframes:
        for part in ['train','oof','valid']:
            g=mf[mf['方案'].eq(NAMES[variant])&mf['集合'].eq(PARTS[part])]
            row={'预测月':None,'方案':NAMES[variant],'集合':PARTS[part]}
            row.update({c:float(g[c].median()) for c in g if c not in row})
            summary.append(row)
    sf=pd.DataFrame(summary).drop(columns='预测月')
    mf.to_csv(OUT/'monthly_metrics.csv',index=False);sf.to_csv(OUT/'summary.csv',index=False)
    pd.DataFrame(conditions).to_csv(OUT/'conditions.csv',index=False)
    comparison=[]
    base=mf[mf['方案'].eq(NAMES['old'])&mf['集合'].eq('测试')].set_index('预测月')
    for variant in ['aligned','segments','selected']:
        own=mf[mf['方案'].eq(NAMES[variant])&mf['集合'].eq('测试')].set_index('预测月').reindex(base.index)
        for k in (1,3,5):
            delta=metrics[variant]['open'][f'top{k}']['rate']-metrics['old']['open'][f'top{k}']['rate']
            comparison.append({'variant':variant,'k':k,'pooled_rate_difference':delta,
              'positive_months':int((own[f'Top{k}命中率']>base[f'Top{k}命中率']).sum()),
              'tied_months':int((own[f'Top{k}命中率']==base[f'Top{k}命中率']).sum()),
              'month_cluster_95pct':paired_interval(testframes[variant],testframes['old'],k)})
    dump(OUT/'test_comparisons.json',comparison)
    audit=pd.read_pickle(OUT/'segment_audit.pkl')
    diagnostics={'rows':len(p),'multi_board_rows':int(p.board_streak_days.gt(1).sum()),
      'old_pre5_contains_any_board':int(audit.history_pre5_board_count.gt(0).sum()),
      'normal_zero_days':int(p.normal_days5.eq(0).sum()),'normal_less_than5':int(p.normal_days5.lt(5).sum()),
      'board_price_discontinuity':int(p.board_price_ok.eq(0).sum()),
      'd0_turnover_quantiles':{str(q):float(p.d0_turnover_pct.quantile(q)) for q in [.1,.5,.9]},
      'd0_volume_below_normal_fraction':float(p.loc[p.d0_normal_volume_ratio.notna(),'d0_normal_volume_ratio'].lt(1).mean()),
      'choices':pd.Series([s['chosen'] for s in sel]).value_counts().to_dict()}
    joined=audit.merge(p[['symbol','d0_date','board_streak_days']],on=['symbol','d0_date'],validate='one_to_one')
    diagnostics['multi_board_old_pre5_contamination']=float(joined.loc[joined.board_streak_days.gt(1),'history_pre5_board_count'].gt(0).mean())
    dump(OUT/'feature_diagnostics.json',diagnostics)
    # One compact detail sheet for the feature hypothesis being investigated:
    # the predeclared full segmented RF, retrained monthly. Keep the comparison
    # selector transparent in its own small record, without duplicate details.
    detail=[]
    chosen=testframes['selected'];chosen[chosen.d0_rank.le(5)].to_pickle(OUT/'selected_test_top5.pkl')
    f=testframes['segments'];top=f[f.d0_rank.le(5)].copy();top.to_pickle(OUT/'segments_test_top5.pkl')
    for r in top.itertuples():
        detail.append({'日期':r.d0_date,'方案':NAMES[r.variant],'D0排名':int(r.d0_rank),
          '评分':r.score,'开盘激活':int(r.activated),'三日再板':int(r.label_3) if r.observed_3 else None,
          'D1封板':int(r.d1_up) if pd.notna(r.d1_up) else None,
          'D2封板':int(r.d2_up) if pd.notna(r.d2_up) else None,
          'D3封板':int(r.d3_up) if pd.notna(r.d3_up) else None,
          '原连板':int(r.board_streak_days),'板前天数':int(r.normal_days5),'板前涨幅':r.normal_return5,
          '板前换手':r.normal_turnover5/100,'D0换手':r.d0_turnover_pct/100,
          'D0量比':r.d0_normal_volume_ratio,'板段量比':r.board_normal_volume_ratio,
          '板段总换手':r.board_turnover_sum/100,'板段一字比':r.board_one_price_fraction,
          '成交额亿':r.d0_amount_100m,'流值估算亿':np.exp(r.log_float_mv_proxy)/1e8,
          '是否ST':int(r.d0_st)})
    defs=[{'类别':'重新对齐' if c in ALIGNED else '新增', '字段':DEFINITIONS[c][0], '含义':DEFINITIONS[c][1]} for c in ALIGNED+EXTRA]
    data={'效果对照':sf.to_dict('records'),'逐月效果':monthly,'选模记录':conditions,'分段模型前五':detail,'特征定义':defs}
    # Pandas serializes unknown values as JSON null, preserving them as blanks.
    clean={k:json.loads(pd.DataFrame(v).to_json(orient='records',force_ascii=False,double_precision=15)) for k,v in data.items()}
    dump(OUT/'workbook_data.json',clean)
    sensitivity={variant:{'excluded_stock_days':int(f.prior_validation_event.sum()),
       'metrics':classify(f[~f.prior_validation_event])} for variant,f in testframes.items()}
    dump(OUT/'cross_event_sensitivity.json',sensitivity)
    dump(OUT/'result.json',{'test_metrics':metrics,'diagnostics':diagnostics,'comparisons':comparison,
      'cross_event_sensitivity':sensitivity})
    print(sf[['方案','集合','Top1命中率','Top3命中率','Top5命中率']].to_string(index=False))
    print(json.dumps(diagnostics,ensure_ascii=False))


if __name__=='__main__':run()
