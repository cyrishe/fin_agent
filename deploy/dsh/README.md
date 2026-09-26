# Fin Agent 配套 Fin Harness

运行时源码由独立的 [Fin Harness 仓库](https://codeup.aliyun.com/684beabd28a6beb51d765af2/fin_harness) 维护。完整版本固定在 [fin_harness.lock.json](fin_harness.lock.json)，包含上游基线、请求历史选择及网关工具调用兼容修复，不再以“commit 加未提交 diff”描述配套版本。

该 JSON 是源码获取和部署核对记录，不是运行时自动安装器。获取源码时必须 checkout 其中的精确 `commit`，核对 `git rev-parse HEAD` 和干净工作树；不要部署浮动的 `master` 或官方跟踪分支。依赖按该 checkout 的 `pnpm-lock.yaml` 安装并完成所需构建。

Fin 仍使用官方 Python SDK API，通过 `sdk-minimal`、profile patch、MCP 和 policy 插件接入。`FINANCE_DSH_SOURCE_ROOT` 与 `FINANCE_DSH_SDK_SOURCE` 必须指向同一固定 checkout；本机路径继续为 `/Volumes/ext/deepseek-harness` 和其 `python/sdk/src`。`_load_sdk_class()` 优先已安装的 SDK，`_dsh_bin()` 优先显式配置及 PATH 上的 `dsh`，所以部署还应核对实际导入位置和启动命令，不能仅核对环境变量。

Fin Harness 的 `master` 保存验证后的维护版本，`upstream/master` 跟踪未修改的官方代码。升级流程和职责见该仓库的 `docs/fin-harness.zh.md`。每次发布记录 Fin Agent commit、Fin Harness commit、配置及 workload；仓库更新不等于生产部署。

`0433ae6-request-history.patch` 保留为历史审查和灾备资产，已包含在锁定版本中，不要再次应用。会话日志包含 `request/history`，回退 Fin 策略时仍需保留能读取该事件的 DSH；更换日志格式前先在副本上验证恢复。最初实现证据见 [效率优化记录](../../docs/development_tasks/dsh_harness_efficiency_implementation_20260910.md)，本次归档和验证见 [仓库建立记录](../../docs/development_tasks/fin_harness_repository_setup_20260926.md)。
