"""Export concise frozen-rule transfer results, no strategy retuning."""
import json
from pathlib import Path
import numpy as np,pandas as pd
from scripts.experiment_limit_rebound_shape_top3_r20 import OUT,POLICIES,dump

DOC=Path('docs/stock_automl_runs/20261015_limit_rebound_shape_top3_r20')
SET_NAMES={'train':'训练·窗中位','valid':'验证·窗中位','test':'测试·去重日期'}


def med(rows,key,sub=None):
    values=[r[key][sub] if sub else r[key] for r in rows]
    return float(pd.Series(values).median()) if any(v is not None for v in values) else None


def effect(name,policy,rows=None,value=None):
    get=lambda k,s=None:(value[k][s] if s else value[k]) if value is not None else med(rows,k,s)
    return {'集合':name,'方案':policy,'成交笔数':get('n'),'再板有效笔':get('known'),
      '三日再板率':get('hit'),'D2均值':get('ret2','mean'),'D3均值':get('ret3','mean'),
      'D4均值':get('ret4','mean'),'策略均值':get('strategy','mean'),
      '策略有效笔':get('strategy','n'),'D2大涨率':get('ret2','up'),'D2大跌率':get('ret2','down')}


def main():
    result=json.loads((OUT/'result.json').read_text());md=json.loads((OUT/'monthly_discrimination.json').read_text())
    mp=json.loads((OUT/'monthly_policy_metrics.json').read_text());f=pd.read_pickle(OUT/'test_top3.pkl')
    split=[];effects=[]
    for part in ['train','valid','test']:
        for rank in [1,2,3]:
            for group,label in [('all','全部'),('pass','通过'),('reject','未过')]:
                rows=[r for r in md if r['part']==part and r['rank']==rank and r['group']==group]
                for horizon,col in [('D2','ret2'),('D3','ret3'),('D4','ret4'),('策略','strategy')]:
                    a=result['ranks'][str(rank)][group][col] if part=='test' else None
                    get=lambda key:a[key] if a is not None else med(rows,col,key)
                    split.append({'集合':SET_NAMES[part],'D0排名':rank,'形态':label,'观察':horizon,
                      '有效笔':get('n'),'大涨率':get('up'),'大跌率':get('down'),'平均收益':get('mean')})
        for policy,name in POLICIES.items():
            rows=[r for r in mp if r['part']==part and r['policy']==policy]
            effects.append(effect(SET_NAMES[part],name,rows,result['policies'][policy] if part=='test' else None))
    for key,label in [('fallback','顺延第2/3名'),('fallback_new_days','顺延新增日期'),('fallback_displaced_later_top1','先买顺延·Top1后开')]:
        effects.append(effect('测试·顺延子集',label,value=result[key]))
    details=[]
    for r in f.sort_values(['d0_date','d0_rank']).itertuples():
        status='普通'
        if not r.ordinary:status='历史特殊·排除'
        elif r.audit_ok!=True:status='无成交·分钟缺失' if r.d1_volume==0 else '数据未齐'
        elif r.trade and pd.isna(r.ret4):status='后续收益未齐/不连续'
        details.append({'日期':r.d0_date,'D1日期':r.d1_date,'D0排名':r.d0_rank,'评分':r.score,
          '形态':r.shape,'五分最高':r.high5,'五分最低':r.low5,'五分实体':r.body5,
          '高点回落':r.pullback5,'收价位置':r.position5,'触板分钟':r.touch5,'整分板下':r.whole5,
          '形态通过':int(r.shape_pass),'可买代理':int(r.trade),'Top1入选':int(r.top1_shape),
          '前三入选':int(r.top3_shape),'买点分钟':r.entry_time if r.trade else None,'买入价':r.entry if r.trade else None,
          '买分全板下':int(r.entry_whole_below) if r.trade else None,
          '三日再板':int(r.label_3) if r.observed_3 else None,
          'D2收益':r.ret2,'D3收益':r.ret3,'D4收益':r.ret4,'策略收益':r.strategy,'核对':status})
    def clean(rows):return json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False,double_precision=15))
    data={k:clean(v) for k,v in [('涨跌区分',split),('顺延效果',effects),('测试前三',details)]};dump(OUT/'workbook_data.json',data)
    DOC.mkdir(exist_ok=True)
    def pct(v):return '—' if v is None else f'{v:.2%}'
    def signed(v):return '—' if v is None else f'{v:+.2%}'
    test_effect='\n'.join(f"|{POLICIES[k]}|{v['n']}|{pct(v['hit'])}|{signed(v['ret2']['mean'])}|{signed(v['ret3']['mean'])}|{signed(v['ret4']['mean'])}|{signed(v['strategy']['mean'])}|" for k,v in result['policies'].items())
    split_effect='\n'.join(f"|Top{rank}|{'通过' if group=='pass' else '未过'}|{result['ranks'][str(rank)][group]['ret2']['n']}|{pct(result['ranks'][str(rank)][group]['ret2']['up'])}|{pct(result['ranks'][str(rank)][group]['ret2']['down'])}|" for rank in [1,2,3] for group in ['pass','reject'])
    all_effect='\n'.join(f"|{r['集合']}|{r['方案']}|{r['成交笔数']:g}|{pct(r['三日再板率'])}|{signed(r['D2均值'])}|{signed(r['D3均值'])}|{signed(r['策略均值'])}|" for r in effects[:8])
    text=f'''# R20：前五分钟形态的大涨/大跌区分与Top3顺延

## 结论

形态对大涨有一定区分度，迁移到第2、第3名后D2大涨率仍提高；大跌过滤随排名和持有期变化，不能当作统一避险开关。

前三顺延加形态相对前三顺延不筛形态，测试D2每笔毛收益从+0.17%提高到+1.51%，D3从−0.87%提高到+1.32%，原策略从−2.03%提高到−0.07%。相对原Top1加形态，交易67→156笔，D2略高，D3和策略收益反而更低。扩大覆盖成立，整体收益优势尚未成立，不升级为交易模型。

## 冻结口径

- 第一阶段仍是R18保存的完整分段随机森林分数，按D0原排名取前三；第二阶段只迁移R19已选定的逐月规则，未对第2/第3名重新训练、调参或看测试选阈值。
- “光看形态”在这里指检验D0模型候选池中的形态筛选层；并非完全脱离D0模型的全市场形态扫描。
- 大涨：对应退出日收盘价/模拟买价−1 **≥7%**；大跌：**<−5%**；中间样本仍计在分母。分别看D2、D3、D4及原策略卖出，均为累计持有收益，不是单日涨幅或盘中最大涨幅。
- D1观察09:30—09:35，不交易；09:35后至09:40逐分钟检查。当前分钟有成交且开价低于涨停价、形态通过的候选中，取D0排名最前的一只；每天最多一笔。买价为该分钟开价代理。09:36为09:35—09:36这一分钟的结束标签。
- 允许当时不可买的Top1顺延到Top2/3。如果买入后Top1才开板，不改写为Top1。不看完整五分钟后回头按排名挑选。
- 6月训练/2月验证/下月测试沿用R19；训练第一阶段分数为历史折外。原形态规则曾使用Top1训练与验证，本轮不重新选规则。训练/验证统计为14个重叠窗口各自指标中位数，不能相加作独立样本。
- 本轮各收益期限独立保留所有已知且价格连续的结果；R19选阈值时要求D4完整。因此部分训练/验证描述性分母与旧R19“用于选阈值的完整样本”不同；测试原Top1分数、规则、买价及收益均未改。

## 大涨/大跌区分：测试D2

下表为各排名独立可买样本，不施加每日只能买一只的竞争限制，用来单独检查形态分层。

|原排名|形态|有效笔|大涨≥7%|大跌>5%|
|---|---|---:|---:|---:|
{split_effect}

Top2与Top3虽然大涨比例提高，D2大跌比例也分别由27.59%/35.51%变为33.33%/37.31%，不能称这一期限也有效避跌。D3时，Top2大跌44.19%→42.50%改善有限，Top3为47.17%→32.81%；完整三集合、四种退出期限在工作簿“涨跌区分”。这些是比例差异，未声称已具有稳定因果或统计显著性。

## 每日最多一笔的测试效果

|方案|模拟笔数|D1—D3再板率|D2均值|D3均值|D4均值|策略均值|
|---|---:|---:|---:|---:|---:|---:|
{test_effect}

- 时间范围：D0 2025-09-01至2026-10-13，267个测试日期、801条前三候选。历史区间此前已研究，非新外部盲测；10月不完整。
- 前三加形态156笔中，Top1 64笔、Top2 59笔、Top3 33笔。92笔为顺延，其中89笔增加了原Top1方案未交易的日期，3笔在后来开板的Top1之前买入了低排名股票。
- 这92笔顺延，D2/D3/D4/策略均值为+1.56%/+1.00%/+0.37%/−0.45%；只看新增89个日期，对应+1.66%/+1.11%/+0.65%/−0.17%。因此顺延本身没有证明原策略卖出能稳定正收益。
- 前三加形态的156笔中，再板标签有效154笔，100次命中；D2/D3/D4收益有效155/154/152笔，策略152笔。末尾数据不足或价格连续性异常保持空白，不记为0。
- 原策略卖出=D2封板收盘卖，否则D3封板收盘卖，否则D4收盘卖；包含未再板者。全部毛收益，未计费、滑点、真实委托成交、资金占用和账户回撤。

## 训练与验证的窗口中位数

|集合|方案|成交笔数|再板率|D2均值|D3均值|策略均值|
|---|---|---:|---:|---:|---:|---:|
{all_effect}

同一窗口内顺延规则始终固定。Top3加形态相对不加形态的改善并不只出现在测试集，但训练/验证每笔利润也偏薄，不能据此忽略成本。

## 数据与验证

- 7234条重叠窗口样本，1618个唯一股票日。1609个有完整分钟数据并通过R19同等量价审计；8个日线无成交且缺分钟，1个历史L状态股票没有D1行情，均排除。
- 测试801条中7条历史特殊状态（含1条无D1）、6条普通股无成交缺分钟；788条可审计普通候选。按D0/D1当日历史状态区间判断，旧第一阶段训练池本轮没有重训修复。
- 原R19的2410条Top1记录复算一致；1609个原始分钟日最高/最低因子复算；9656个“月份×集合×日期×策略”按原始分钟逐时序选股核对；21702项收益独立复算通过。4项定向测试通过。
- 固定规则SHA256：`{result['frozen_rules_sha256']}`。分钟代理可买不等于真实委托一定成交，整分板下仅提供较充分的价格窗口证据。

## 文件

输出目录：`{OUT}`。

- `前五分形态_涨跌区分与前三顺延_R20_20261015.xlsx`：涨跌区分、顺延效果、测试前三三页；日期在最前，无代码名称。筛“前三入选=1”得到156条每日交易。
- `rules_frozen.json`与`specification.json`：不可事后改写的规则及本轮设计。
- `all_top3.pkl`、`enriched_top3.pkl`、`daily_policy_picks.pkl`、`test_top3.pkl`：全部三集合样本、各方案逐日选择和预测。
- `minute_days/`、历史状态和日线子集：原始数据证据；`verification.json`保存独立复核及工作簿回读结果。
- `monthly_policy_metrics.json`、`monthly_discrimination.json`、`result.json`：逐月、逐排名和整体统计。

脚本：`experiment_limit_rebound_shape_top3_r20.py`依次执行prepare、minutes、report；`verify_limit_rebound_shape_top3_r20.py`独立核对；`report_limit_rebound_shape_top3_r20.py`生成报告输入，`build_limit_rebound_shape_top3_r20.mjs`生成工作簿。运行环境沿用`.venv-automl`与`/tmp/fin_agent_sklearn171`，D0模型未重新训练。材料本地保存，未声称阿里云盘同步。
'''
    (DOC/'README.md').write_text(text)
    print(json.dumps({'sheets':{k:len(v) for k,v in data.items()}},ensure_ascii=False))


if __name__=='__main__':main()
