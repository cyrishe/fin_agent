# 独立股票 AutoML 模块

用户用自然语言提出研究想法，系统在共同约束内规划多个方向，各自训练、解释、验证并交付策略资产。目标是支持用户开展不同研究，不是全任务只找一个最优策略。完整数据、标签、precision 与回测口径见[使用文档](../../../docs/stock_automl.md)。

```bash
python -m pip install -r requirements-automl.txt
python -m src.quant_research.automl --demo --max-trials 4
python -m src.quant_research.automl --config docs/examples/stock_automl.json --llm
python -m src.quant_research.automl --requirement '从低波动量能和行业估值两个方向研究3/7日持有机会，宁可漏选，总共6次实验' --llm
python -m pytest -q tests/automl tests/test_backtest_core.py
```

`--config` 是显式单方向配置；`--requirement` 需要 LLM 编译，可以产生多个方向。`--llm` 还开启开发候选规划和报告评审。`scripts/run_stock_automl.py` 仅兼容转发 CLI。

## 模块职责与依赖

- `planning.py` 保留用户原文，解释研究假设，将现有能力编译为共同 `ResearchSpec` 和方向配置；不执行任意生成代码或 SQL。
- `study.py` 分配一个共享候选预算，运行各方向，保存集合与失败证据；不根据最终测试挑全局赢家。
- `data.py` 使用指定 KingdomAI 只读连接。默认覆盖研究区间历史有效股票池，期间 IPO/退市纳入；整数 `max_symbols` 才做公司试跑抽样。分批读取数据，并提供独立于选定公司报价的同源全市场日历。
- `features.py` 构造当时可得输入与固定开盘持有收益标签；`learning.py` 负责拟合、时间校准与训练行预算。
- `decision.py` 在开发证据中选择出手阈值；`runner.py` 完成单方向候选比较、最终拟合/校准、独立门槛段和留出评估。
- `assets.py` 从真实模型导出策略及结构解释；`inference.py` 复用预处理、校准模型和冻结规则；`advisor.py` 提供独立语言规划与解读。
- 唯一仓库内运行依赖是 `src.backtest` 的纯计算回测引擎；`runner` 同时保存其源码快照。模块不导入 `src.services`、Web/API、路由、Skill 运行时或系统数据库。
- ML 依赖单列在 `requirements-automl.txt`。后台适配层延迟导入，使通用任务/Web 进程无需预装 ML 栈。数据库和 LLM 仅在明确执行时连接；只有 CLI 加载 `.env`，库 import 没有任务或凭据副作用。

## 用户任务接入与自主边界

`src/tools/stock_automl_research_tool.py` 已接入现有后台任务体系，只在授权的任务归属与产物目录中执行。输入完整 `requirement_brief`，可选 `source=demo` 和 `llm_review`；用户明确给出结构化配置时使用 `spec`，其字段是执行依据。通用系统负责一次性/定时调度、进度、取消和恢复，本模块不增加平行状态机。

用户决定方向、目标与限制；LLM 可组合已有特征子集、样本机制、模型及持有期，并据开发期结果规划下一轮候选。各方向共享预算，不能自行扩大共同范围或改写测试/成本约定。当前仍只支持 `open[t+h+1]/open[t+1]-1` 持有收益；尾盘指定窗口预测次日高开、任意新特征公式、触价和自动交易都未实现，不能近似替代用户要求。

自然语言默认使用 precision 模式：少出手可以接受，比较目标精确率、误选下跌、出手日期和月度表现；Top K 是上限。显式旧配置 `min_precision=None, optimize_threshold=False` 仍按预测损失与固定阈值运行。precision 模式最终模型/校准器冻结后，在未参与其拟合的开发末段选择门槛，最后才评估留出。每个方向只在开发期选模型，未达标候选也保留原因。

## 产物、恢复与复用

CLI 新输出为 `outputs/stock_automl/study_<id>/`，含 `research_design.json`、`study_plan.json`、检查点和 `study.json/.md`；各方向位于 `direction_<n>/<run_id>/`。子目录保存独立模型、样本计数、开发结果、冻结选择、回测、`strategy.json` 和 `explanation.json/.md`。解释来自模型系数或树路径等真实结构，当前未自动新增事后剪枝。

```bash
python -m src.quant_research.automl --resume outputs/stock_automl/study_<id>
python -m src.quant_research.automl --review-only outputs/stock_automl/study_<id>/direction_1/<run_id>
```

`--review-only` 使用单方向目录，只重试 LLM 评审。兼容旧版 `<run_id>` 单方向目录格式；恢复仍需匹配冻结配置及实现指纹，不表示可跨代码版本续训。`run_study` 冻结完整计划和来源，`run_research` 冻结配置、代码、样本、候选、切分和选择；已完成方向不重训，中断但已预留的候选继续占用预算。业务 `progress/checkpoint/check_cancel` 回调不依赖任务系统，阻塞拟合由外层进程监督取消。

```python
from src.quant_research.automl.inference import predict_strategy

scored = predict_strategy(study_directory, daily_frame, events, strategy_id="direction_1")
picks = scored[scored.selected]
```

研究集合必须明确策略标识；兼容单方向路径及 `predict_latest`。输入需足够滚动历史，并建议提供 `daily_frame.attrs['market_calendar']`；输出 `selected` 使用保存的阈值和每日 Top K，可以全为空选。只加载可信、自己生成的本地模型。

输出目录受 Git 忽略；行情、训练矩阵、逐行预测和模型保留本地，提交仅选取获准的汇总、配置和版本证据。合成测试验证程序，不证明策略收益；分批查询不代表全市场训练性能已验收。当前能力与限制、历史审计及运行证据链接均在[使用文档](../../../docs/stock_automl.md)。

换环境的依赖、启动、运行资产边界和后续定时推理接入入口见[开发交接](../../../docs/development_tasks/automl_handoff_20261003.md)。
