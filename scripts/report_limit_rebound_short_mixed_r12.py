"""Write the auditable R12 comparisons without choosing again on test results."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from scripts.experiment_limit_rebound_short_mixed_r12 import OUT, dump, sha

DOC=Path('docs/stock_automl_runs/20261014_limit_rebound_short_mixed_r12')
PARTCN={'train':'训练（拟合内）','valid':'验证','test':'测试'}
CN={'pre5_up_count':'前五日涨停次数','board_streak_days':'当前连板天数','log_price':'股价对数',
    'pre5_mean_turnover_pct':'前五日平均换手率','streak_return':'本轮连板累计涨幅',
    'd0_return':'D0涨幅','gap_to_limit':'开盘涨幅/涨停幅度','d0_lower_shadow':'D0下影线',
    'market_median_return':'市场涨幅中位数','pre5_return':'前五日累计涨幅',
    'market_up_fraction':'市场上涨比例','d0_amount_ratio_pre5':'成交额/前五日均额',
    'd0_turnover_pct':'D0换手率','d0_volume_ratio_pre5':'成交量/前五日均量',
    'first_turnover_pct':'本轮首板换手率','streak_return':'本轮连板累计涨幅',
    'd0_amount_100m':'D0成交额','turnover_board_rank':'涨停池换手率分位',
    'amount_board_rank':'涨停池成交额分位','log_float_mv_proxy':'流通市值近似值对数',
    'market_reseal_fraction':'市场连板延续比例','turnover_ratio_pre5':'换手率/前五日均值',
    'pre5_volatility':'前五日涨幅波动','limit_rate':'涨停幅度','d0_one_price':'D0一字板',
    'range_to_limit':'振幅/涨停幅度'}


def pct(x):
    return '空缺' if x is None else f'{x*100:.2f}%'


def label(candidate):
    family={'rf':'随机森林','extra':'极端随机树','hgb':'浅层提升树','logistic':'逻辑回归'}[candidate['family']]
    return family+('＋扩展因子' if candidate['features']=='expanded' else '原23因子')


def run():
    spec=json.loads((OUT/'specification.json').read_text())
    coverage=json.loads((OUT/'detail_coverage.json').read_text())
    lines=['# R12：连续短窗口与跨年随机月份实验','',
        '2026-10-14。共完成 18 个预先定义的模型配置；训练只使用 D0 及以前的日线量价、换手率和市场汇总特征，历史 ST 表用于候选资格。分钟 K 只补充事后开盘与封板时点，不进入特征。全部模型已保存并重新加载核验；没有在验证后把验证集并回训练集。三份表使用同一个已保存模型。',
        '', '## 集合划分与选模型的方法','',
        'A 组：分别使用 2025 年 5—10 月、3—10 月、1—10 月的 6/8/10 个月训练，统一在 2025 年 11—12 月验证，2026 年全部可观察日期测试。先比较窗口与因子，再比较模型。',
        '',
        'B 组：种子 20261014，抽签一次，不按结果重新抽月份。2024 年选 6 个月训练、2 个月验证，另 4 个月不用；2025、2026 年剩余月份全部分配到测试。2026 年 10 月为不完整月份，预先全部放入测试。',
        '', '| 年份 | 训练月份 | 验证月份 | 测试月份 | 不用 |','|---|---|---|---|---|']
    for year,parts in spec['mixed']['months'].items():
        fmt=lambda part:'、'.join(str(int(m[-2:])) for m in parts[part]) or '—'
        lines.append(f'| {year} | {fmt("train")} | {fmt("valid")} | {fmt("test")} | {fmt("unused")} |')
    lines+=['',
        '严格分开样本：从“D0 前五日或本轮首板（较早者）”到 D4 的整个信息区间，必须落在同一个连续集合区间；边界附近的样本剔除。同一连板事件不能跨集合，原始价格观察窗口也不跨集合。随机月份虽然样本隔离，但训练包含部分晚于测试的月份，因此 B 组是跨月份泛化实验，不能解释为按时间向未来的回测；A 组才是连续时间留出。',
        '',
        '冻结的选择规则：优先看验证集 D0 前五内 D1 激活 Top1 的命中数/可评估日数（轮空记零）；相同再比较验证集平均精确率 AP。收益用于效果检查，不在看到测试结果后更换模型。测试同时保留原八个月随机森林，以及存在时同窗口、同算法的原因子对照。所有测试日期此前可能被其它研究查看，不属于从未见过的新盲测。',
        '',
        '初始训练和模型选择的数据快照截至 10 月 13 日。完成选择后，补入新到的 10 月 14 日日线，只延长测试结果观察；断言确认所有特征、训练/验证数据不变，模型没有重训。完整 D4 的 D0 最晚为 10 月 8 日。更晚 D0 的 D4 尚未发生，不混入完整收益统计。',
        '', '## 选定模型的三集合效果','',
        '命中标签为 D1～D3 任意一天收盘封板；其中包含 D1 当天再板。收益假设 D1 开盘价买入，在 D2～D4 第一次收盘封板时按收盘价卖出，否则 D4 收盘卖出。以下都是每笔独立价格毛收益的平均值，不是账户收益，未计成交限制与费用。训练行为拟合内，不能当成样本外效果。','',
        '| 组别与模型 | 集合 | 分类样本数 | Top1 三日再板 | Top1 命中率 | 平均退出涨幅 | 收益样本/异常剔除 |',
        '|---|---|---:|---:|---:|---:|---:|']
    bundle={};comparison=[]
    for group,title in [('chronological','A 连续短窗'),('mixed','B 跨年随机月')]:
        root=OUT/group;selection=json.loads((root/'selection_frozen.json').read_text())
        selected=selection['selected'];cid=selected['id']
        chosen=next(r for r in selection['candidates'] if r['candidate']['id']==cid)
        tested=json.loads((root/'test_results.json').read_text())
        metrics={**chosen['parts'],'test':tested['results'][cid]}
        bundle[group]={'selected':selected,'parts':metrics,'test_controls':tested['results'],
                       'split':{},'coverage':{}}
        for part in ('train','valid','test'):
            f=pd.read_pickle(root/f'selected_{part}_all.pkl')
            split={'dates':[f.d0_date.min(),f.d0_date.max()], 'days':int(f.d0_date.nunique()),
                   'rows':len(f),'classification_rows':int((f.activated&f.observed_3).sum()),
                   'snapshot_sha256':sha(root/f'selected_{part}_all.pkl')}
            bundle[group]['split'][part]=split
            bundle[group]['coverage'][part]=coverage['parts'][group+'_'+part]
            m=metrics[part];t=m['top']['1'];partcn={'train':'训练（拟合内）','valid':'验证','test':'测试'}[part]
            lines.append(f'| {title}：{label(selected)} | {partcn} | {m["rows"]:,} | {t["hits"]}/{t["observed_labels"]} | {pct(t["hit_rate"])} | {pct(t["exit_mean"])} | {t["clean_returns"]}/{t["excluded_returns"]} |')
        for r in selection['candidates']:
            c=r['candidate'];comparison.append({'组别':title,'模型':label(c),'训练月数':c['window'],
                '模型ID':c['id'],'选定':int(c['id']==cid),
                '训练Top1命中':r['parts']['train']['top']['1']['hit_rate'],
                '验证Top1命中':r['parts']['valid']['top']['1']['hit_rate'],
                '验证Top1收益':r['parts']['valid']['top']['1']['exit_mean'],
                '验证AP':r['parts']['valid']['ap']})
    lines+=['','## 整体分类指标','',
            '分类指标仅在 D1 激活、未来三日标签完整的候选池计算。精确率、召回率、F1、准确率采用固定分数阈值 0.5；每日 Top1 则按排名选择，两种口径不同。类别均衡使模型分数不等同于未经校准的真实发生概率。',
            '', '| 组别 | 集合 | 正例基率 | AP | AUC | 准确率 | 精确率 | 召回率 | F1 |',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for group,b in bundle.items():
        for part,m in b['parts'].items():
            lines.append(f'| {"A" if group=="chronological" else "B"} | {PARTCN[part]} | {pct(m["base_rate"])} | {m["ap"]:.4f} | {m["auc"]:.4f} | {pct(m["accuracy"])} | {pct(m["precision"])} | {pct(m["recall"])} | {m["f1"]:.4f} |')
    lines+=['','## 每日 Top1/2/3/5','',
            '先固定 D0 前五，再只在 D1 激活者中取前 K；不会用 D0 第六名替补。某日激活不足 K 只时，保留实际数量。','',
            '| 组别 | 集合 | K | 命中/完整标签 | 命中率 | 每笔平均退出涨幅 |','|---|---|---:|---:|---:|---:|']
    for group,b in bundle.items():
        for part,m in b['parts'].items():
            for k,t in m['top'].items():
                lines.append(f'| {"A" if group=="chronological" else "B"} | {PARTCN[part]} | {k} | {t["hits"]}/{t["observed_labels"]} | {pct(t["hit_rate"])} | {pct(t["exit_mean"])} |')
    lines+=['','## 18 个配置的验证比较','',
        '| 组别 | 模型 | 训练月数 | 训练 Top1 命中率 | 验证 Top1 命中率 | 验证平均退出涨幅 | 验证 AP | 选定 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for r in comparison:
        lines.append(f'| {r["组别"]} | {r["模型"]} | {r["训练月数"]} | {pct(r["训练Top1命中"])} | {pct(r["验证Top1命中"])} | {pct(r["验证Top1收益"])} | {r["验证AP"]:.4f} | {r["选定"]} |')
    lines+=['','## 因子检查','',
        '扩展方案在原 23 项之上增加 12 项：涨停幅度、当日一字板、振幅/涨停幅度、开盘涨幅/涨停幅度、换手率/前五日均值、前五日波动、前五日涨停次数、市场连板延续比例、涨停池成交额/换手率分位、流通市值近似值对数、股价对数。全部可在 D0 收盘得到。流通市值近似值为成交额/(换手率/100)，与表里展示的总市值不同。',
        '', '只在验证集做打乱因子的检查：打乱后 AP 下降越多，说明当前模型越依赖该项；不代表因果关系或稳定的未来增益。','',
        '| 组别 | 因子 | 打乱后 AP 平均下降 |','|---|---|---:|']
    for group in bundle:
        factors=pd.read_csv(OUT/group/'validation_factor_importance.csv').head(8)
        for r in factors.itertuples():
            lines.append(f'| {"A" if group=="chronological" else "B"} | {CN.get(r.feature,r.feature)} | {r.ap_loss:.4f} |')
    lines+=['','同窗口的因子对照与测试：','',
            '| 组别 | 模型 | 测试 Top1 命中 | 测试平均退出涨幅 | 测试 AP |','|---|---|---:|---:|---:|']
    for group,b in bundle.items():
        for cid,m in b['test_controls'].items():
            t=m['top']['1']
            lines.append(f'| {"A" if group=="chronological" else "B"} | {cid} | {t["hits"]}/{t["observed_labels"]} | {pct(t["exit_mean"])} | {m["ap"]:.4f} |')
    lines+=['',
        '连续组新增因子在验证集改善了 Top1，但测试命中并未全面超过同窗口原因子；收益接近零的结果不足以确认形成可执行优势。跨年组的验证和测试退出收益仍为负。因此保留全部证据，后续选择与卖出策略应另行定义，再用未参与定规则的数据验证，不能据此次测试结果回头换模型。',
        '', '## 明细与数据核验','',
        '两份 Excel 各三张表：训练集（拟合内）、验证集、测试集。每天仅 D0 前五，无日期、代码和名称；日序仅用于分组。`D1入选=1` 是当天五只中的激活第一名。`分钟齐全=1` 才有经过完整 240 根、开盘价、收盘价、成交量校验的分钟事实；为 0 时首封/终封时间留空。2024 年没有分钟档案，训练/验证仍能按日线计算分类标签，但不能验证开盘成交窗口。',
        '',
        '首封时是第一根收盘达到涨停价的一分钟 K 的结束时间，终封时是最后一次开板后恢复封板的分钟时点，不能精确到逐笔成交。未激活或未入选行的涨幅仅用于价格路径比较；策略统计须筛选 `D1入选=1`。D2/D3/D4 收涨与策略退出涨均相对 D1 开盘假设买价；冷静天数以 D1 开始计算，D1 当天收盘再封板则为 0。',
        '',
        '`异常=1` 包括 D2～D4 昨收与前日收盘跳变超过 2%、D1 全日零成交/无有效开盘价、或 D4 个股收盘缺失。异常只在选股后从收益统计剔除，不更改名次、不替补；收益字段留空。涨停标签缺失时不当成 0。总市值使用 D0 当日历史值，缺失时以前期已知股本×D0 收盘价估算并标记，仍无依据则空缺。',
        '', '| 组别 | 集合 | Top5 行数 | 入选笔数 | 完整分钟（全部/入选） | 市值有值/估算 |','|---|---|---:|---:|---:|---:|']
    for group,b in bundle.items():
        for part,c in b['coverage'].items():
            lines.append(f'| {"A" if group=="chronological" else "B"} | {PARTCN[part]} | {c["top5_rows"]} | {c["selected"]} | {c["minute_complete_top5"]}/{c["minute_complete_selected"]} | {c["market_cap_present"]}/{c["market_cap_estimated"]} |')
    lines+=['',
        '四项针对划分/激活的测试通过；一分钟 CSV 快速抽取与完整解析的 480 行逐值一致。训练候选模型全部保存后重载，训练与验证评分逐值相等。`specification.json` 固定月份、候选和选择规则；`selection_frozen.json` 先于测试评估生成；`test_endpoint_extension.json` 记录追加观察日且未重训；模型文件、选定模型三集合全体快照、原始价格来源及 SHA-256 均保留。',
        '',
        f'主证据目录：`{OUT}`。脚本：`scripts/experiment_limit_rebound_short_mixed_r12.py`、`scripts/export_limit_rebound_short_mixed_r12.py`。本轮模型仅作为研究候选保存，没有替换此前基准策略。']
    DOC.mkdir(parents=True,exist_ok=True)
    (DOC/'README.md').write_text('\n'.join(lines)+'\n')
    pd.DataFrame(comparison).to_csv(OUT/'model_comparison_zh.csv',index=False)
    dump(OUT/'summary.json',bundle)
    manifests=[]
    for path in sorted(OUT.glob('*/*.joblib')):
        manifests.append({'file':str(path),'sha256':sha(path),'bytes':path.stat().st_size})
    dump(OUT/'models_manifest.json',{'models':manifests,'count':len(manifests),
                                    'source_manifest_sha256':sha(OUT/'source_manifest.json'),
                                    'specification_sha256':sha(OUT/'specification.json')})
    print('report',DOC/'README.md','models',len(manifests))


if __name__=='__main__': run()
