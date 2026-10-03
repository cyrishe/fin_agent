# 独立股票 AutoML 模块

从仓库根目录执行：

```bash
python -m pip install -r requirements-automl.txt
python -m src.quant_research.automl --demo --max-trials 6
python -m src.quant_research.automl --config docs/examples/stock_automl.json --llm
python -m pytest -q tests/automl tests/test_backtest_core.py
```

`scripts/run_stock_automl.py` 仅兼容转发到本模块 CLI。完整用法见 [使用文档](../../../docs/stock_automl.md)。

## 依赖边界

- 模块内完成配置、数据读取、特征/标签、训练、评估、研究编排、LLM规划评审、推理与CLI。
- 唯一仓库内运行依赖为 `src.backtest` 的纯计算回测引擎，单向复用现金账本、执行成本和净值计算；不复制另一套引擎。`runner` 还读取该引擎的源码以保存可追踪快照。
- 不导入 `src.services`、Web/API、Agent路由、Skill运行时或系统数据库。后台工具适配层延迟导入本模块，Web 服务无需安装 ML 依赖。
- ML依赖放在独立的 `requirements-automl.txt`，不增加到主服务依赖。
- 数据库和LLM仅在显式调用时连接；可直接传入DataFrame运行研究，`--demo` 不读取数据库、不调用LLM（除非显式加 `--llm`）。
- `.env` 只由CLI加载。作为库调用时，由调用者提供数据及运行配置；不会在import时加载凭据或启动任务。
- 实验文件默认保存在被Git忽略的 `outputs/stock_automl/`。提交结果仅选取汇总指标、配置、版本证据及说明，行情、训练矩阵、逐行预测和模型留在本地。

这是仓库内独立研究模块，后台任务通过 `src/tools/stock_automl_research_tool.py` 接入。用户自然语言由领域 `planning.py` 编译为 `ResearchSpec`，训练中按实验保存检查点；通用运行器持有归属、租约、取消和运行预算。没有自动交易能力。

## 后台任务与恢复

`stock_automl_research` 只能由授权后台任务执行，输入完整 `requirement_brief`，可选 `source=demo`（仅合成数据验证）及 `llm_review`。只有用户明确提供完整结构化配置时才传 `spec`；此时字段是执行约束，说明文字不再隐式改写。自然语言要求不支持时，在训练前明确指出。

核心 `run_research` 的 `progress`、`checkpoint` 和 `check_cancel` 回调不依赖任务系统。进程监督负责取消正在阻塞的拟合；业务回调负责实验边界。恢复读取同一数据、实现、候选和划分；已预留但中断的实验计入预算，并记录原因，不无限重跑。最终模型选择在留出集揭盲前固定。恢复拒绝变更配置或数据指纹。

```bash
python -m src.quant_research.automl --requirement '研究近三年股票持有3/7交易日收益，最多12个实验' --llm
python -m src.quant_research.automl --resume outputs/stock_automl/<run_id>
python -m src.quant_research.automl --review-only outputs/stock_automl/<run_id>
```

`--review-only` 只读取汇总报告并重试 LLM 评审，不重新训练。后台默认发布设计、汇总指标、开发实验、样本计数和报告，原始行情、逐行预测和模型文件保留在受任务归属保护的本地执行目录。配置数据库仍为只读访问，任务产物不自动写入 Git。
