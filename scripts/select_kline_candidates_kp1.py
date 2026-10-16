"""Morphology-level frequency and same-date market excess, from saved matches."""
import numpy as np
import pandas as pd

from scripts.research_kline_forward_kp1 import OUT, REPORT, CATALOG, BULL, CONTEXT, dump


def main():
    b=pd.concat([pd.read_pickle(p) for p in sorted((OUT/'parts').glob('*.pkl.gz'))],ignore_index=True)
    calendar=pd.DatetimeIndex(sorted(b.date.unique()))
    universe=b.groupby('date').size().reindex(calendar)
    for h in (1,2,3):
        b[f'market{h}']=b.groupby('date')[f'r{h}'].transform('mean')
        b[f'mx{h}']=b[f'r{h}']-b[f'market{h}']
        b[f'bx{h}']=b[f'r{h}']-b.groupby(['date','board'])[f'r{h}'].transform('mean')
    rows=[];monthly=[];frequencies=[]
    matches=OUT/'pattern_matches'
    matches.mkdir(exist_ok=True)
    for p in CATALOG:
        e=b[b['hit_'+p['id']]]
        e[['code','date','board','r1','r2','r3','mfe3','mae3','limit_up','one_price','start_'+p['id']]].to_csv(
            matches/(p['id']+'.csv.gz'),index=False)
        n=e.groupby('date').size().reindex(calendar,fill_value=0)
        share=100*n/universe
        row={'id':p['id'],'name':p['name'],'role':'bull' if p['id'] in BULL else 'context' if p['id'] in CONTEXT else 'bear_control',
             'count_6m':len(e),'avg_per_market_day':len(e)/len(calendar),'signal_days':int(n.gt(0).sum()),
             'avg_daily_market_share_pct':share.mean(),'p90_daily_market_share_pct':share.quantile(.9),
             'max_daily_market_share_pct':share.max(),'distinct_stocks':e.code.nunique(),
             'episode_starts':int(e['start_'+p['id']].sum())}
        for h in (1,2,3):
            v=e[e[f'r{h}'].notna()]
            row.update({f'n{h}':len(v),f'mean{h}_pct':v[f'r{h}'].mean(),f'win{h}_pct':100*v[f'r{h}'].gt(1e-9).mean(),
                        f'median{h}_pct':v[f'r{h}'].median(),f'market_excess{h}_pp':v[f'mx{h}'].mean(),
                        f'board_excess{h}_pp':v[f'bx{h}'].mean(),
                        f'recent_market_excess{h}_pp':v.loc[v.date.ge('2026-07-01'),f'mx{h}'].mean(),
                        f'nonlimit_market_excess{h}_pp':v.loc[~v.limit_up & ~v.one_price,f'mx{h}'].mean(),
                        f'episode_market_excess{h}_pp':v.loc[v['start_'+p['id']],f'mx{h}'].mean()})
        row['loss3_pct']=100*e.loc[e.r3.notna(),'r3'].le(-3).mean()
        row['mfe3_touch3_pct']=100*e.loc[e.r3.notna(),'mfe3'].ge(3).mean()
        row['mae3_touchminus3_pct']=100*e.loc[e.r3.notna(),'mae3'].le(-3).mean()
        bymonth=e.assign(month=e.date.dt.strftime('%Y-%m')).groupby('month')
        for month,g in bymonth:
            monthly.append({'id':p['id'],'name':p['name'],'month':month,'count':len(g),
                            **{f'excess{h}_pp':g[f'mx{h}'].mean() for h in (1,2,3)}})
        row['months_positive_all3']=sum(all(g[f'mx{h}'].mean()>0 for h in (1,2,3)) for _,g in bymonth)
        row['months_observed']=e.date.dt.to_period('M').nunique()
        # Fixed before viewing full-market pattern outcomes: about 4–200/day,
        # present on >= half of sessions, without broad daily crowding.
        row['moderate_frequency']=500<=len(e)<=25000 and row['signal_days']>=60 and row['p90_daily_market_share_pct']<=10
        row['all3_market_positive']=all(row[f'market_excess{h}_pp']>0 for h in (1,2,3))
        row['recent_all3_positive']=all(row[f'recent_market_excess{h}_pp']>0 for h in (1,2,3))
        row['nonlimit_all3_positive']=all(row[f'nonlimit_market_excess{h}_pp']>0 for h in (1,2,3))
        row['episode_all3_positive']=all(row[f'episode_market_excess{h}_pp']>0 for h in (1,2,3))
        rows.append(row)
        frequencies.extend({'id':p['id'],'date':str(d.date()),'count':int(n[d]),'market_stock_count':int(universe[d]),'share_pct':share[d]} for d in calendar)
    r=pd.DataFrame(rows).sort_values('count_6m')
    r.to_csv(REPORT/'morphology_table.csv',index=False)
    pd.DataFrame(monthly).to_csv(REPORT/'morphology_monthly.csv',index=False)
    pd.DataFrame(frequencies).to_csv(REPORT/'morphology_daily_frequency.csv',index=False)
    shortlist=r[r.role.eq('bull') & r.moderate_frequency & r.all3_market_positive].sort_values('market_excess3_pp',ascending=False)
    shortlist.to_csv(REPORT/'research_candidates.csv',index=False)
    lines=['# 按形态统计六个月频次与后三日超额涨跌','',
           f'2026年4–9月，共{len(calendar)}个交易日。总次数为股票日；日均=总次数÷全部交易日（包含零命中日）。',
           '超额涨跌=个股后续涨跌−同信号日合格沪深股票等权平均涨跌，再对该形态所有样本取均值；单位为百分点。不是与整个半年一个固定收益比较。',
           '', '| 形态 | 六个月次数 | 日均次数 | 日均市场占比 | 后1天超额 | 后2天超额 | 后3天超额 | 频次适中 | 三期限均优于市场 |',
           '|---|---:|---:|---:|---:|---:|---:|---|---|']
    for v in r[r.role.ne('bear_control')].itertuples():
        lines.append(f'| {v.name} | {v.count_6m:,} | {v.avg_per_market_day:.1f} | {v.avg_daily_market_share_pct:.2f}% | {v.market_excess1_pp:+.3f} | {v.market_excess2_pp:+.3f} | {v.market_excess3_pp:+.3f} | {"是" if v.moderate_frequency else "否"} | {"是" if v.all3_market_positive else "否"} |')
    lines.extend(['','频次初筛：六个月500–25,000次（约4–200次/日）、至少60个交易日出现、每日市场占比的90分位不超过10%。这是描述性筛选标准，不是训练得到的最优阈值。',
                  '前31种为偏多候选，另外4种为背景观察；全部64条（含偏空对照）见 morphology_table.csv。',
                  '完整公式及名称边界见 [FORMULA_AUDIT.md](FORMULA_AUDIT.md)。命中明细按形态保存在 outputs/stock_automl/kline_survey_kp1_20261016/pattern_matches/，后续更换筛选口径直接读取，不重新识别形态。'])
    (REPORT/'MORPHOLOGY.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    dump(REPORT/'screening_rules.json',{'frequency':'500–25000 occurrences in six months; >=60 signal days; P90 daily market share <=10%',
         'return':'same-date equal-weight market excess >0 at each of t+1,t+2,t+3; no requirement every stock or every day wins',
         'robustness':'recent three months, exclude signal close limit/one-price, episode starts; disclose failures',
         'model_training':False,'market_days':len(calendar),'selected_count':len(shortlist)})
    cols=['name','count_6m','avg_per_market_day','market_excess1_pp','market_excess2_pp','market_excess3_pp','recent_all3_positive','nonlimit_all3_positive','episode_all3_positive','months_positive_all3']
    print(shortlist[cols].round(4).to_string(index=False))


if __name__=='__main__':main()
