"""Render CO25 annual and monthly evidence without selecting a winning variant."""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from scripts.research_low_base_co25 import OUT,REPORT,FEATURES

NAMES={'eligible':'同日基础池','low':'低位L','stable':'低位止跌S','compact':'低位收敛C',
       'first_compact':'首次进入C','ridge_top3':'S池线性预测Top3','narrow_top3':'S池窄幅Top3'}


def md(d,index=False,digits=3):
    if index:d=d.reset_index()
    rows=['|'+'|'.join(map(str,d.columns))+'|','|'+'|'.join(['---']*len(d.columns))+'|']
    for row in d.itertuples(index=False,name=None):
        values=[]
        for x in row:
            if pd.isna(x):values.append('—')
            elif isinstance(x,(float,np.floating)):values.append(f'{x:.{digits}f}')
            else:values.append(str(x))
        rows.append('|'+'|'.join(values)+'|')
    return '\n'.join(rows)


def table(d,cols,labels):
    rows=['|'+'|'.join(labels)+'|','|'+'|'.join(['---']*len(cols))+'|']
    for _,r in d.iterrows():
        values=[]
        for c in cols:
            x=r[c]
            if c=='method':x=NAMES.get(x,x)
            elif c in ['mean','daily_mean','excess_stable','excess_eligible','up','strong','loss2','median','tail5']:
                x='—' if pd.isna(x) else f'{x*100:+.3f}' if c not in ['up','strong','loss2'] else f'{x*100:.2f}'
            elif c in ['rows','known','unknown','signal_days','horizon']:x=str(int(x))
            values.append(str(x))
        rows.append('|'+'|'.join(values)+'|')
    return '\n'.join(rows)


def main():
    s=pd.read_csv(REPORT/'summary.csv');counts=pd.read_csv(REPORT/'daily_counts.csv')
    quality=pd.read_csv(REPORT/'model_quality.csv')
    net=s[s.target.eq('net')]
    chunks=['# CO25：低位蓄势规则与2/3日预测\n\n2026-10-27。用户要求换一个方向：先框定低位蓄势股票，再观察2/3日涨幅。本轮为独立日线研究；以下结果均为事件/单笔参考收益，不是B1升级或账户回测。',
        '**结论：这组规则尚未形成稳定上涨优势，简单线性预测也没有改善。** 严格C池D+2单笔净均值2024/2025/2026分别−0.719%/+0.147%/−0.100%，D+3分别−0.997%/+0.376%/−0.088%；2026仅3/9和4/9个月净均值为正。2026相比S池，C的D+2大亏比例约25.64%降至21.00%，D+3约30.06%降至27.03%，但均值没有稳定改善。同日配对超额的描述性区间跨零，首次进入条件也未确认正收益。\n\n模型Top3在2026年D+2/D+3平均净收益−0.611%/−1.063%，日内排序相关为负，误差不优于训练常数；不能把强势命中有所上升当可靠排序。“收敛”可以表示波动降低，但本轮未识别随后向上的方向。“缺少启动确认”仅为下一步待检验解释，不在已看结果后继续改规则找优胜者。',
        '## 规则与时点\n\n见[结果前固定方案](DESIGN.md)。L：近60日价格位置≤35%、20日涨幅≤8%；S在L上要求5日跌幅不超过2%、MA5近3日下降不超过1%、近3日低点不明显跌破此前10日低点；C在S上要求5日高低幅度≤8%、真实振幅缩至此前20日的85%以内、均量缩至90%以内。此前20日不含最近5日。',
        '基础池为历史沪深股票，连续60日有效行情，过去20日均成交额≥2000万元、当天≥500万元。没有沿用B1当日涨3%—6%候选。完整收盘后筛选，信号D；观察D+2/D+3收盘涨幅。交易参考采用D+1开盘买、D+2或D+3收盘卖，买卖各0.1%假设费用，无额外滑点。',
        '2024为结构发现段，2025验证，2026年前9个月固定规则检验；不依据2026结果改阈值。所有历史曾用于其他研究，不称全新盲测。模型从2025-01起，每月用此前6个月的S池、已成熟标签训练Ridge(alpha=100)，训练日期等权、缩尾和标准化只拟合训练集；分别预测D+2/D+3净收益，42个模型，不调参、不设预测为正才交易的后验门槛。']
    countrows=counts.groupby(counts.date.str[:4])[['eligible','low','stable','compact']].mean().round(1)
    chunks.append('## 平均每日候选\n\n'+md(countrows,index=True,digits=1)+'\n\n完整逐日数量见[daily_counts.csv](daily_counts.csv)。零候选日期保留；没有用未来停牌或涨跌标签删掉当日候选。')
    event=s[s.target.eq('event')&s.period.eq('2026')&s.method.isin(['low','stable','compact','first_compact'])]
    chunks.append('## 2026：信号收盘到第2/3日收盘涨幅\n\n'+table(event,
        ['horizon','method','rows','unknown','mean','median','up','strong','loss2'],
        ['D+观察','方案','信号数','未知标签','平均涨幅%','中位涨幅%','上涨比例%','涨≥3%比例','跌>2%比例'])+
        '\n\n这张表从D收盘计涨幅，没有扣费；它描述形态后的价格变化。下表才是D+1开盘进入后的净收益参考。')
    for year in ['2024','2025','2026']:
        z=net[net.period.eq(year)&net.method.isin(NAMES)].copy()
        z=z[(z.rows>0)].sort_values(['horizon','method'])
        chunks.append(f'## {year}：次日开盘进入后的单笔净收益\n\n'+table(z,
            ['horizon','method','rows','unknown','mean','up','strong','loss2','excess_stable'],
            ['D+退出','方案','信号数','未知执行','净均值%','净盈利比例%','净≥3%比例','净亏>2%比例','日期等权超S百分点']))
    chunks.append('表中“净均值”按已知股票×信号日等权；“超S”先在同日比较，再按日期等权，因此两者不可直接相减。涨跌分类先将收益四舍五入至小数点后10位，避免价格链浮点尾差把平盘计成上涨。全部已知/未知标签、日期等权均值、中位数和最差5%见[summary.csv](summary.csv)。同一股票连续入选会重复计数，“首次进入C”另作检验，不能把几十万条相关样本当几十万份独立证据。')
    months=sorted(counts.date.str[:7].unique());rows=[]
    for month in months:
        row={'月份':month}
        for h in [2,3]:
            for method,label in [('stable','S'),('compact','C'),('ridge_top3','模型Top3')]:
                r=net[net.period.eq(month)&net.method.eq(method)&net.horizon.eq(h)].iloc[0]
                row[f'D{h}_{label}']=r['mean']*100
        rows.append(row)
    monthly=pd.DataFrame(rows);monthly.to_csv(REPORT/'monthly_compact_view.csv',index=False)
    chunks.append('## 逐月平均单笔净收益（%，不是月度账户收益）\n\n'+md(monthly)+'\n\n33个月完整表见[monthly_compact_view.csv](monthly_compact_view.csv)，包含2024无模型的空列。因为2/3日持仓相互重叠，本轮没有把它连乘成总收益、没有报告虚构的账户最大回撤。')
    chunks.append('## 预测是否有区分力\n\n'+md(quality.assign(year=quality.month.str[:4]).groupby(['year','horizon'])[['mae','constant_mae','mse','constant_mse','daily_rank_ic']].mean(),index=True,digits=6)+
        '\n\n误差与同一训练段的日期等权常数预测比较；日内秩相关按日期平均。完整逐月见[model_quality.csv](model_quality.csv)。不能仅因Top3涨幅较高就认为点预测准确。固定“更窄幅Top3”是无需训练的排序对照，所有方案均报告。')
    ci=pd.read_csv(REPORT/'paired_block_intervals.csv')
    z=ci[ci.year.eq(2026)&ci.reference.eq('stable')].copy()
    for c in ['daily_excess','ci_low','ci_high']:z[c]*=100
    chunks.append('## 日期配对的不确定性\n\n2026相对同日S池的日均净收益差，5市场日移动块重采样2000次，百分点：\n\n'+md(z[['method','horizon','daily_excess','ci_low','ci_high']])+
        '\n\n区间是描述性的，没有修正多组比较；只分析均值，不等于尾部风险或账户回撤已受控。')
    chunks.append('## 价格、可交易性与归档限制\n\n价格位置和未来收益使用close/preclose因果链，避免未来复权因子进入特征。主标签相当于参考调整收益，原价敏感性见[adjustment_sensitivity.csv](adjustment_sensitivity.csv)。停牌/无效路径导致标签未知；次日一字行情、退出日疑似一字跌停的执行收益未知，不填零。不具有完整历史ST状态、真实撮合、持仓资金限制及延迟退出账本，因此不作可执行账户承诺。\n\n七项定向测试、120条独立标量特征/标签复算、42模型回读和标签成熟/成员核对、84组Top3逐月独立重算，结果见[verification.json](verification.json)。初次测试发现测试样本OHLC数组共享内存及手算量比窗口误差，修正测试样本后通过；未因此修改规则阈值。未运行无关应用全仓库测试。\n\n全部历史候选/特征/标签在本地完整panel.parquet中；严格C历史名单另存输出目录compact_candidates.parquet，报告附[最后研究日名单](last_research_day_candidates.csv)，其日期为2026-09-30，**不是2026-10-27实时推荐**。代码与本地完整归档回执见本目录；云盘按用户要求待同步。')
    p=pd.read_parquet(OUT/'panel.parquet')
    p[p.compact].to_parquet(OUT/'compact_candidates.parquet',index=False)
    pr=pd.read_parquet(OUT/'predictions.parquet')
    latest=p[p.compact & p.date.eq(p.date.max())][['date','stk_code',*FEATURES,'event2','event3','net2','net3']].copy()
    for h in [2,3]:
        q=pr[pr.horizon.eq(h)&pr.date.eq(p.date.max())][['stk_code','predicted_net']]
        latest=latest.merge(q.rename(columns={'predicted_net':f'predicted_net{h}'}),on='stk_code',how='left',validate='one_to_one')
    latest.sort_values('range5').to_csv(REPORT/'last_research_day_candidates.csv',index=False)
    font=FontProperties(fname='/System/Library/Fonts/STHeiti Light.ttc')
    fig,axes=plt.subplots(2,1,figsize=(13,7),sharex=True)
    m=monthly[monthly['月份'].str.startswith('2026')]
    for ax,h in zip(axes,[2,3]):
        for name in ['S','C','模型Top3']:
            ax.plot(m['月份'],m[f'D{h}_{name}'],marker='o',label=name)
        ax.axhline(0,color='gray',lw=.7);ax.set_title(f'D+{h}退出：各月平均单笔净收益（%）',fontproperties=font)
        ax.legend(prop=font);ax.grid(alpha=.2)
    fig.suptitle('CO25 低位蓄势：2026年1—9月；非账户收益',fontproperties=font,fontsize=16)
    fig.tight_layout();fig.savefig(REPORT/'monthly_comparison.png',dpi=150);plt.close(fig)
    chunks.insert(5,'![2026逐月单笔净均值](monthly_comparison.png)')
    (REPORT/'README.md').write_text('\n\n'.join(chunks)+'\n')
    print('REPORT COMPLETE',flush=True)


if __name__=='__main__':main()
