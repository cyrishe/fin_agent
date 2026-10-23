from pathlib import Path
import hashlib
import json
import pandas as pd

R=Path('/data/cyris/work')
O=R/'b2_prebuy_volume_warning_r83_20261022'
d=pd.read_csv(O/'306笔D0_D1买前指标与结果.csv',dtype={'股票':str})
acc=pd.read_csv(O/'固定预警下_年度与连续账户价格假设.csv')
same='D1首分钟量除板前20同刻最大量'
cut_same=float(d.loc[d.split.eq('train'),same].quantile(.1))
cut_acc=float(d.loc[d.split.eq('train'),'early_volume_accel'].quantile(.2))
cut_d0=float(d.loc[d.split.eq('train'),'D0量除首板前20日均量'].quantile(.2))
cut_pre=float(d.loc[d.split.eq('train'),'pre20_price_return'].quantile(.8))
s=pd.read_pickle(R/'b2_news_flow_years_r76_20261022'/'joined_306.pkl')
d=d.merge(s[['id','seven_unverified']],left_on=d.股票+'|'+d.D0选中日,right_on='id',validate='one_to_one')
d['同刻低10预警']=d[same].le(cut_same).astype(int)
d['四K下限一字预警']=d['D1前四K下限一字']
d['同刻低10_旧模型未报']=(d['同刻低10预警'].eq(1)&d.fit_sens_D1_q75.eq(0)).astype(int)
d[d['同刻低10预警'].eq(1)|d['四K下限一字预警'].eq(1)][[
 '股票','D0选中日','D1','D2','split',same,'early_volume_accel','high5',
 '同刻低10预警','四K下限一字预警','fit_sens_D1_q75',
 'D2全天一价封死','D2D3连续收盘跌停','原卖法费后收益_pct','seven_unverified',
 '若禁买则少赚_1万元','若禁买则避亏_1万元'
]].sort_values(['D0选中日','股票']).to_csv(O/'两类D1买前预警_逐笔.csv',index=False,encoding='utf-8-sig')

rows=[]
for feature in [same,'early_volume_accel','D0量除首板前20日均量']:
 train=d.loc[d.split.eq('train'),feature].dropna()
 for quantile in [.1,.2,.3]:
  cut=float(train.quantile(quantile))
  for split in ['train','valid','test']:
   g=d[d.split.eq(split)&d[feature].le(cut)]
   base=d[d.split.eq(split)&d[feature].notna()]
   rows.append({'指标':feature,'训练分位':quantile,'训练阈值':cut,'阶段':split,
                '可比笔数':len(base),'预警笔数':len(g),
                '预警全天一价封死':int(g.D2全天一价封死.sum()),
                '可比全天一价封死':int(base.D2全天一价封死.sum()),
                '预警D2D3连续收盘跌停':int(g.D2D3连续收盘跌停.sum()),
                '盈利误拦笔数':int((g.原卖法费后收益_pct>0).sum()),
                '大于10%盈利误拦笔数':int((g.原卖法费后收益_pct>10).sum()),
                '少赚_每笔1万元':round(g['若禁买则少赚_1万元'].sum(),2),
                '避亏_每笔1万元':round(g['若禁买则避亏_1万元'].sum(),2),
                '净贡献_每笔1万元':round(g['若禁买则避亏_1万元'].sum()-g['若禁买则少赚_1万元'].sum(),2)})
pd.DataFrame(rows).to_csv(O/'训练低10_20_30分位敏感性.csv',index=False,encoding='utf-8-sig')

overlap=[]
for split in ['train','valid','test']:
 g=d[d.split.eq(split)]
 for name,flag in [('同刻低10全部',g['同刻低10预警'].eq(1)),
                   ('同刻低10且旧模型未提示',g['同刻低10_旧模型未报'].eq(1)),
                   ('四K下限一字',g['四K下限一字预警'].eq(1))]:
  z=g[flag]
  overlap.append({'阶段':split,'分组':name,'预警笔数':len(z),
                  '一价封死':int(z.D2全天一价封死.sum()),
                  '连续收盘跌停':int(z.D2D3连续收盘跌停.sum()),
                  '盈利误拦':int((z.原卖法费后收益_pct>0).sum()),
                  '少赚_每笔1万元':round(z['若禁买则少赚_1万元'].sum(),2),
                  '避亏_每笔1万元':round(z['若禁买则避亏_1万元'].sum(),2),
                  '净贡献_每笔1万元':round(z['若禁买则避亏_1万元'].sum()-z['若禁买则少赚_1万元'].sum(),2)})
overlap=pd.DataFrame(overlap)
overlap.to_csv(O/'同刻预警与既有D1模型重叠.csv',index=False,encoding='utf-8-sig')

test=d[d.split.eq('test')]
assert len(test)==98 and test.D2全天一价封死.sum()==4
assert int(test.loc[test['同刻低10预警'].eq(1),'D2全天一价封死'].sum())==2
assert set(test.loc[test['同刻低10预警'].eq(1)&test.D2全天一价封死.eq(1),'股票'])=={'603137.SH'}
assert int(test.loc[test['同刻低10_旧模型未报'].eq(1),'D2全天一价封死'].sum())==0
assert d['四K下限一字预警'].sum()==5
assert d.loc[d['四K下限一字预警'].eq(1),'D2全天一价封死'].sum()==4
assert d.loc[d['四K下限一字预警'].eq(1),'fit_sens_D1_q75'].sum()==5
assert d['板前20同刻完整'].sum()==219

def account(name,period):
 return acc[(acc.预警==name)&(acc.期间==period)].iloc[0]
a0=account('既有D1_Q75','2022-2026连续')
a1=account('既有D1_Q75加同刻低10%','2022-2026连续')
a25=account('既有D1_Q75','2025')
a26=account('既有D1_Q75','2026')
b25=account('既有D1_Q75加同刻低10%','2025')
b26=account('既有D1_Q75加同刻低10%','2026')
def part(name,split):
 x=overlap[(overlap.分组==name)&(overlap.阶段==split)].iloc[0]
 return f"{int(x.预警笔数)}／{int(x.一价封死)}／{int(x.盈利误拦)}／{x['净贡献_每笔1万元']:+,.0f}"

report=f"""# R83：D0 与 D1 买前量价预警复核

## 结论

D1 出现一条清楚的价格警报和一条值得跟踪的同刻量线索；新量比尚不足以成为正式禁买条件。

1. D1 09:34 前四根分钟 K 全是一价，价格较 D0 前收约跌 10%：306 笔中 5 笔，之后 D2 全天一价封死 4 笔；旧上午 7% 否则收盘的价格账本有 1 笔反弹盈利 +1.09%。2026 两笔均 D2 封死。5 笔全部已被既有 D1 买前 Q75 风险模型提示，所以没有新增严重案例。冻结 B2 回放确实曾在 D1 09:36 买入一价跌停的股票。
2. D1 09:31 首分钟量除首板前 20 个实际交易日同刻最大量：训练期低 10% 切点为 {cut_same:.4f} 倍。2026 可比 56 笔，低端 9 笔中 D2 全天封死 2 笔，均为 603137.SH 连续两次信号；旧账误拦 3 笔盈利、每笔一万元避亏 4,920 元、少赚 824 元。2025 低端 3 笔、封死 0、误拦 1 笔收益超过 10% 的交易。两笔封死均已被旧 D1 Q75 捕获，因此没有证实新增尾部保护。
3. D0 日量除板前 20 日均量低 20%：2026 提示 17 笔，0 笔 D2 全天封死，错过 7 笔盈利、其中 2 笔超过 10%；每笔一万元净 −203 元。D0 单独缩量不能当本次禁买规则。另看 D0 首板前 20 日涨幅高 20%：2026 提示 21 笔，虽然包含 3 笔全天封死，却误拦 12 笔盈利，每笔一万元净 −3,834 元；2025 提示 6 笔而未命中封死，净 −2,990 元。它只能作背景风险。D1 后两 K 量除前两 K 量低 20% 命中的 2026 两笔封死正是上述一价跌停，剔除它们后不再捕获全天封死。

## 时点和数据

冻结 V2 放行 306 笔；2022—24 训练 152、2025 验证 54、2026 已观察测试 98、跨年边界剔除 2。收益沿用 R76 费后上午超过 7% 否则盘末的同一价格账本；跌停全额成交尚未证实。D1 09:31 第一根分钟 K 含集合竞价与首个连续分钟，D1 09:34 四根完整 K 均早于原 09:35 以后买入。没有用 D1 全天或 D2 结果生成买前指标。

同刻量以该股首板前实际交易日为基线。R80 已核的股票日复用，其余从服务器原始压缩档逐日提取。合计 5,367 个不同板前股票日，有效 4,774、原压缩档缺失 593。306 个 D1 整日分钟源重新读取，四 K 量比、加速度和开盘价与既有特征逐笔核对。20 日同刻完整 219/306：训练 115/152、验证 46/54、测试 56/98；缺失保留空值。

## 分期经济得失

“提示／封死／误拦盈利／净得”依次为笔数、笔数、笔数、每笔固定一万元下避亏减去少赚的元数。它不是账户收益。分位切点只从训练期有数据的样本计算。

| 买前指标 | 训练 2022—24 | 验证 2025 | 已观察 2026 |
| --- | ---: | ---: | ---: |
| D0 日量／板前20日均量低20%，≤{cut_d0:.3f} | 29／3／12／+6,677 | 6／0／3／+1,694 | 17／0／7／−203 |
| D0 板前20日涨幅高20%，≥{cut_pre:.1%} | 29／3／12／+3,523 | 6／0／4／−2,990 | 21／3／12／−3,834 |
| D1 同刻最大量比低10%，≤{cut_same:.3f} | {part('同刻低10全部','train')} | {part('同刻低10全部','valid')} | {part('同刻低10全部','test')} |
| D1 后两 K／前两 K 量低20%，≤{cut_acc:.3f} | 31／4／14／+744 | 8／0／3／+2,397 | 12／2／5／+813 |
| D1 前四 K 一价下限，涨幅≤−9.5% | {part('四K下限一字','train')} | {part('四K下限一字','valid')} | {part('四K下限一字','test')} |

同刻量比改用训练低 20% 门槛，2025／2026 分别误拦 3／12 笔盈利，净得 −1,048／−1,424 元。固定 0.6 倍与 {cut_same:.3f} 倍只差少许，训练期多碰到一笔较大盈利，就使净得从 +744 变为 −416 元。低 10% 的结果对阈值敏感，而且本轮比较过多个变量和门槛，不能当新的盲测发现。

## 与旧 D1 风险模型的关系

既有 D1 Q75 是 R75 冻结的买前研究候选，尚未生产上线。它在 2025 的 1 笔、2026 的 4 笔 D2 全天一价封死上都已提示。同刻低 10% 在 2026 命中的两笔封死同属 603137.SH；它未增加被提示的严重股票。从旧 Q75 未提示的交易里，新量比另提示训练 8、2025 年 1、2026 年 5 笔，额外封死均为 0；每笔一万元净得分别 −2,584、+927、+1,177 元。

按 R76 原现金投入、同日买卖和未核实卖价的价格假设回放：旧 D1 Q75 在 2025／2026 独立账户为 +{a25.账户收益率*100:.2f}%／+{a26.账户收益率*100:.2f}%，再加同刻低 10% 为 +{b25.账户收益率*100:.2f}%／+{b26.账户收益率*100:.2f}%。2022—26 单一连续账户由 +{a0.账户收益率*100:.2f}% 降至 +{a1.账户收益率*100:.2f}%。这些回放仍把部分跌停收盘记作可卖，不能视为实盘收益。2022—24 为模型开发期，2026 历史已被多轮研究观察。新量比保留为提示研究，不修改正式 V2 或现有卖单规则。

## 资产

逐笔数据、训练低10/20/30分位敏感性、与旧模型重叠、年度及连续账户均在本目录。服务器主目录为 /data/cyris/work/b2_prebuy_volume_warning_r83_20261022；本地只同步衍生研究档。关联 KingdomAI 不可变策略 imac/B2_V2，定义 SHA-256 54ebe0150dfefa6ce9837d403fd83b331f2720ad835271525e3106aae7529f00。本轮没有新训练模型、生产库写入、定时任务或云端归档回读。
"""
(O/'README.md').write_text(report,encoding='utf-8')
names=['README.md','306笔D0_D1买前指标与结果.csv','训练分位固定后_单指标分期误伤与风险.csv',
       '训练低10_20_30分位敏感性.csv','固定预警下_年度与连续账户价格假设.csv',
       '同刻预警与既有D1模型重叠.csv','两类D1买前预警_逐笔.csv',
       '首板前同刻分钟_逐股日.csv.gz','D1前四分钟原档复核_306笔.csv',
       'extract_preboard.py','audit_d1_minutes.py','analyze.py','replay_alerts.py','summarize.py',
       '输入文件SHA256.json','新读取压缩源SHA.json','D1分钟源SHA.json']
sha={name:hashlib.sha256((O/name).read_bytes()).hexdigest() for name in names}
(O/'manifest.json').write_text(json.dumps(sha,ensure_ascii=False,indent=2))
print('report',O/'README.md','low10',cut_same,'files',len(names))
