"""Readable close-up examples, receipts, and report from the completed frozen run."""
import gzip,hashlib,json,platform,subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from .engine import prepare
from .render import render
from .run import clean,dump
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'docs/research/kline_pattern_library_20260928'
def main():
 patterns=json.loads((OUT/'patterns.json').read_text());results=json.loads((OUT/'results.json').read_text());manifest=json.loads((OUT/'run_manifest.json').read_text());cases=list(map(json.loads,(OUT/'cases.jsonl').read_text().splitlines()))
 raw=pd.DataFrame([json.loads(s) for s in gzip.open(OUT/'daily_inputs.jsonl.gz','rt')]);raw.trade_date=pd.to_datetime(raw.trade_date)
 pool={};mapping={p['id']:p for p in patterns};first={}
 for c in cases:
  if c['matched'] and c['pattern_id'] not in first:first[c['pattern_id']]=c
 for pid,c in first.items():
  p=mapping[pid]
  if p['family'] not in ('蜡烛组合','单根影线'):p['example_image']=c['image'];continue
  code=c['stock_code']
  if code not in pool:
   b=raw[raw.stk_code==code].set_index('trade_date').drop(columns=['open','high','low','close']).rename(columns={'adjopen':'open','adjhigh':'high','adjlow':'low','adjclose':'close'});pool[code]=prepare(b)
  f=pool[code];t=f.index.get_loc(c['signal_date']);rel=f'images/{pid}_example.png'
  render(p,f,t,code,OUT/rel,display_bars=28);p['example_image']=rel
 dump(OUT/'patterns.json',patterns)
 pos=[c for c in cases if c['matched']]
 means={k:float(np.mean([c[k] for c in pos])) for k in ['cached_match_ms','prepare_and_match_ms','render_ms']}
 pct={k:{'p50':float(np.percentile([c[k] for c in pos],50)),'p95':float(np.percentile([c[k] for c in pos],95))} for k in means}
 dump(OUT/'timing_summary.json',{'positive_sample_count':len(pos),'means_ms':means,'percentiles_ms':pct,'aggregation':'each selected positive sample equally weighted; 25 warm repeats averaged within each cached decision; excluded cases not timed','note':'prepare+match includes full shared stack from fixed anchor, including unrelated features. Does not represent an optimized per-pattern cold path. Charts use completed bars only.'})
 paths=list((ROOT/'src/experiments/kline_patterns').glob('*.py'))+[ROOT/'tests/test_kline_pattern_lab.py',ROOT/'src/services/technical_indicator_calculator.py']
 manifest['file_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
 manifest['model_calls']=1+int((OUT/'ds_flash_text_audit.json').exists())
 manifest['visual_verification']='DS Flash accepted an image-containing request but responded that no image was available; visual capability unverified. See ds_flash_probe.json.'
 manifest['display_note']='Primary candle examples additionally rendered with 28-bar window for readability; hit timing refers to the 80-bar full-context chart, close-up creation is not included.'
 dates=set(raw.trade_date);manifest['missing_within_union_calendar']={code:len({d for d in dates if b.trade_date.min()<=d<=b.trade_date.max()}-set(b.trade_date)) for code,b in raw.groupby('stk_code')}
 dump(OUT/'run_manifest.json',manifest)
 rows=['| 形态 | 回扫命中 | 抽验组数 | 缓存判断均时 ms | 特征重算+判断均时 ms | 绘图均时 ms |','|---|---:|---:|---:|---:|---:|']
 for r in results:rows.append(f'| {r["name"]} | {r["total_matches"]} | {r["tested_hits"]} | {r["mean_cached_match_ms"]:.4f} | {r["mean_prepare_and_match_ms"]:.2f} | {r["mean_render_ms"]:.2f} |')
 (OUT/'timings.md').write_text('# 命中样本耗时\n\n只计成功命中的选定样本。每条规则通常3组；下降三法2组。未记录排除样本耗时。缓存判断不含特征准备；重算从同一历史起点开始，包含全套共享特征；绘图单列。无DB、网络、模型费用或耗时。不是完整生产端到端SLA。\n\n'+'\n'.join(rows))
 readme=f'''# 日K与指标形态库：标准化研究与真实行情回扫

2026-09-28。已完成 **62 条可执行定义（原资料归并37，新增25）**，逐条配含义/注意事项、同源可执行公式、统一图表，以及本次真实行情表的命中证据。主入口：[可搜索图册](index.html)；文本：[完整目录](catalog.md)。

## 本轮结果

- 固定24只A股，2019-01-01起，来源最新为2026-09-24，共 **44,676 根来源日记录**。不是全市场、不是随机代表性样本，选股名单先于形态扫描固定。
- 62条均有命中；**185组命中抽验 + 62组近似反例**。严格版下降三法只有2组，其余各3组。近似反例是恰好一项条件不满足的真实窗口，未计耗时。
- 命中均时：已有共享特征时 **{means['cached_match_ms']:.4f} ms/例**；从历史起点准备全套特征再判断 **{means['prepare_and_match_ms']:.2f} ms/例**；80根上下文绘图 **{means['render_ms']:.2f} ms/例**。这三个数字不得相互替代或冒充包含模型的整体识别时间。逐形态见 [耗时表](timings.md)，分位数见 [计时汇总](timing_summary.json)。
- 所有正例均截断到信号日重新计算确认；相同定义下的向量/单点一致、价格缩放不变、缺数、未来偏移拒绝、峰谷确认延迟已做本地测试。已有指标及新实验测试共53项通过，见[test_receipt.json](test_receipt.json)。
- DS Flash 图片请求接口未报错，但模型回复未收到图；本次**视觉能力未验证**，无有效CV判定耗时或CV准确率，见[探测回执](ds_flash_probe.json)。未用其答复改写数学判断。
- DS Flash另对6种形态的24组正反例作盲序文字审算，23组与代码一致；1组把`114.86 < 113.61`错判为真。见[文字复核原始回执](ds_flash_text_audit.json)。这是算术一致性审阅，不是24组专家真值准确率；该误判按明确数值纠正，确定性公式结果保持不变。

## 怎样复用

1. 在 `src/experiments/kline_patterns/catalog.py` 增加自然语言解释及明确条件；表达式只接受命名序列、非正时间偏移、比较、算术、&/|、abs/min/max，不执行任意Python。机器格式在 [patterns.json](patterns.json)。
2. 一只股票只准备一次同口径特征，多个形态复用。`detect(pattern, features)` 批量回扫，`detect(pattern, features, at=t)` 复核单点，`render(...)` 统一画图。两种判断共用相同表达式树，避免文档公式与代码各写一套漂移。
3. 本次为离线业务研究能力，未登记生产Tool、未修改数据库和核心HARD协议。后续接入可沿既有日线工具和Renderer承接。

```sh
.venv/bin/python -m src.experiments.kline_patterns.fetch
PYTHONPATH=/tmp/fin-agent-kline-plot-deps:$PWD .venv/bin/python -m src.experiments.kline_patterns.run
PYTHONPATH=/tmp/fin-agent-kline-plot-deps:$PWD .venv/bin/python -m src.experiments.kline_patterns.finish
.venv/bin/python -m src.experiments.kline_patterns.coverage
.venv/bin/python -m src.experiments.kline_patterns.report
.venv/bin/python -m pytest tests/test_kline_pattern_lab.py tests/test_standard_technical_indicators.py tests/test_technical_indicator_calculator.py -q
```

`fetch` 会用现有配置只读获取公开行情并覆盖本目录快照；精确重放本次结果时应跳过fetch，直接使用已保存快照。绘图用matplotlib 3.10.8，安装在任务临时目录，不改应用依赖；换机器可按[plot_requirements.txt](plot_requirements.txt)装入隔离环境。中文字体当前为macOS Arial Unicode，其他机器须提供相应字体。定义/输入/代码/环境绑定见 [run_manifest.json](run_manifest.json)、[input_manifest.json](input_manifest.json)。

## 关键口径与边界

- 每根K线方向使用 `c-o`，涨跌幅方向使用 `c-c[-1]`。`&`为逻辑并且，不能将用户示例中的`*`含糊地当成并且。完整符号、指标公式、预热与峰谷取点见图册说明。
- 输入按现有服务口径取adjopen/adjhigh/adjlow/adjclose（hfq），成交量保留原始股数。检测、均线、图同口径；股本变化可能影响长期量能可比性。当前源快照不具备历史当时修订版本，不能宣称PIT回测。
- 无效OHLC、缺数、零成交记录保持空值并重启相关递推预热。没有虚构停牌bar，也不把未知当0；24只股票各自首末日之间相对样本交易日期并集没有缺行，这不是独立交易所日历审计。
- 峰谷必须等右侧两根完成，背离/双顶底的信号日期是确认或突破日，原极值日另作图中定位，防止未来数据倒灌。
- 3组正例由公式命中后抽取，重放通过是**实现与公式一致性证据**，不能计算独立识别准确率。近似反例也由相同条件构造筛选，未形成专家盲标真值。当前没有收益、成本或胜率回测。
- 文字中的“较长、相近、低位”已落实为可见的v1数值约定，但这些阈值不是经过优化验证的最优值。同名教材和软件可能不同；十字版本、上下影家族、母型之间存在包含或重叠，不能当独立证据相加。
- [原资料30条逐条映射](original_coverage.md)保留了未能唯一解释的揉搓、暗度陈仓、画线取点等内容，以及无法仅用日线测试的分时、竞价和资金意图。不为这些内容制造确定公式。

## 文件入口

| 内容 | 文件 |
|---|---|
| 图文与公式 | [index.html](index.html)、[catalog.md](catalog.md)、[patterns.json](patterns.json) |
| 185正例及62近似反例的逐条件值 | [cases.jsonl](cases.jsonl) |
| 每条形态命中数及均时 | [results.json](results.json)、[timings.md](timings.md) |
| 可重放的公开行情快照 | [daily_inputs.jsonl.gz](daily_inputs.jsonl.gz) |
| 原资料去重与未决事项 | [original_coverage.md](original_coverage.md) |
| 外部定义参考 | [sources.json](sources.json) |

资料核对来自 [StockCharts蜡烛字典](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-pattern-dictionary)、Fidelity的 [MACD](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd)、[RSI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI)、[BOLL](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands)。图均按本次行情数据自行重绘，不复制第三方图表。
'''
 (OUT/'README.md').write_text(readme)
 print(json.dumps({'means_ms':means,'cases':len(cases)},ensure_ascii=False))
if __name__=='__main__':main()
