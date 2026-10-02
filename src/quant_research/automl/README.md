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
- 不导入 `src.services`、Web/API、Agent路由、Skill运行时或系统数据库。现有应用也不导入本模块。
- ML依赖放在独立的 `requirements-automl.txt`，不增加到主服务依赖。
- 数据库和LLM仅在显式调用时连接；可直接传入DataFrame运行研究，`--demo` 不读取数据库、不调用LLM（除非显式加 `--llm`）。
- `.env` 只由CLI加载。作为库调用时，由调用者提供数据及运行配置；不会在import时加载凭据或启动任务。
- 实验文件默认保存在被Git忽略的 `outputs/stock_automl/`。提交结果仅选取汇总指标、配置、版本证据及说明，行情、训练矩阵、逐行预测和模型留在本地。

这是仓库内独立研究模块，尚未包装成独立分发的Python项目，也未注册生产API、聊天工具、调度或交易功能。
