# 2026-10-15 收盘—开盘 1.7.1 服务器部署

## 运行位置与口径

- 主机：`che@39.106.248.18`；目录：`/home/che/cyris/fin_agent_deploy/automl1440-171-20261015`。独立于 stock_agent 服务及其 Python 环境，无 sudo。
- 代码来源：`deploy/stock-automl-1440/` 构建的 17 个脚本模块、SQL、入口及精确依赖清单。运行入口 `automl1440.sh`；服务器现有 `/home/che/cyris/fin_agent/.env` 仅由入口读取，未打入包。
- 运行时：Python 3.12.12、scikit-learn 1.7.1、NumPy 2.5.2、SciPy 1.18.1、pandas 2.3.3；另有 14 个固定版本依赖，包括显式锁定的 `cloudpickle 3.1.2`。Linux x86_64 轮子已放在 `wheelhouse/`，`install_runtime.sh` 可离线安装依赖。
- `che` 用户 crontab 于服务器本地时间 13:50 运行 `prepare`，14:40 运行 `run --write-db`，`AUTOML1440_USER_ID` 默认为 `imac`。`prepare` 先核查及补全上一交易日分钟 K。现有 crontab 备份为 `crontab.before-20261015.txt`；本机同时间两个 LaunchAgent 已停用但 plist 保留。

## 包与验证

| 资产 | SHA-256 | 位置 |
|---|---|---|
| 源码包，22 个清单文件 | `642e743345ec95cd0f2d836339390b582b9d16c0b90d3ae9fad930a627529a8d` | 本机 `outputs/stock_automl/deployment/automl1440-171-source-20261015.tar.gz`；服务器同名文件 |
| Linux 轮子包 | `9c9ac7b81ba233bfc5ee3ef5f4c7714208ace0d6512c943759d3a120394fb3fe` | 本机 `outputs/stock_automl/deployment/automl1440-171-linux-wheels.tar`；服务器同名文件 |
| 10/15 模型与 20 日成交额缓存 | `0378cee4f63956f7b395dfcd12a2424d5a86b805431dd17b11d8c1071f61e6a8` | 本机 `outputs/stock_automl/deployment/automl1440-171-cache-20261015.tar.gz`；服务器同名文件 |
| 完整 Linux 包（源码、轮子、Python、虚拟环境、缓存；256 MB） | `1130e5afc5f8616fd36e4406ebd81214c6493ef4dffe0eca4ebe404a6f00994f` | 服务器 `automl1440-171-linux-x86_64-py312-complete.tar.gz` |

源码包回读 22/22 文件哈希通过；完整包 13,648 个归档条目，没有 `.env`。完整包的虚拟环境记录了部署路径，恢复时应解压到上述同一路径。

服务器从上传缓存加载 10/15 模型：6,042 行、sklearn 1.7.1、模型 SHA `01c1d52a305f478cd37c1b7dbc3f5870356157b78eda0a6bcd8325fd72209079`，加载与校验耗时 0.91 秒。服务器独立重训并回放 10/14：训练 200.71 秒，训练文件 SHA 与本机 X8 相同，6,370 行；历史推理 17.98 秒，106 候选，Top1 `002812`、Top2 `603216`、规则通过。106 只候选的四类概率与本机最大绝对差约 `1.1e-16`。joblib 模型字节哈希跨平台不同，不影响此次名单和概率复现；未重复写入 10/14 数据库。

全市场行情接口实测 5,574/5,574 只，约 2 秒；10/14 分钟 K 审核为 5,214/5,214 只完整，耗时 11.61 秒。服务器准备、回放日志分别为 `logs/replay_20261014_prepare.log` 和 `logs/replay_20261014_run.log`。这些是历史回放证据，尚未验证 10/15 14:40 定时触发及写库回读。

## 日常检查与恢复

每天先看服务器 `logs/prepare.log`、`logs/run.log`，再核对 `outputs/stock_automl/live_1440/cache/YYYY-MM-DD_model.json`、`outputs/stock_automl/live_1440/YYYY-MM-DD_decision.json` 和 `kingdomai.automl_strategy_selection`。准备模型每日仍重新读取 20 个训练日，10/14 服务器实测约 3 分 21 秒；14:40 推理使用准备好的缓存。

完整包在原路径恢复后，运行 `./install_runtime.sh` 可核对环境，再运行 `./automl1440.sh run --help` 核对入口。服务器 crontab 使用 `# BEGIN fin-agent-automl1440-171` 至 `# END fin-agent-automl1440-171` 两行任务；本机恢复原调度需重新启用 `com.finagent.automl1440.prepare` 和 `.run`。历史 B1 仅在 2–7 月确认逐日复现，当前服务器自动任务仍需继续核对 8–10 月输入链与逐日名单，不能仅凭 1.7.1 版本称作 B1 延续。
