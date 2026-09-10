# Fin Agent 配套 DSH 补丁

当前配套提交：`0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4`；上游基线：`cd5ef8148158c3a752a658978873241fdf8e2bbc`。

`0433ae6-request-history.patch` 保存本项目请求历史选择优化，供部署复现和代码审查。DSH 的 origin 是 DeepSeek 上游，因此没有向上游直接推送本项目提交。生产使用包含该提交的 Git bundle 快进到相同 commit；从基线重建时也可使用 `git am` 应用本补丁。

服务器与本机另有此前已有的 DeepSeek 流式 tool call 兼容修改；本次未覆盖。两处 `translate.ts`、`types.ts` 的 Git diff SHA-256 均为 `5139613e00c2f207056bac6ec746f5f7e4553232dd3383735c5c8a31405015ca`。此补丁只代表新增提交，不代表包含这些已有修改的完整运行树。

新日志包含 `request/history`。回退 Fin 策略时保留此 DSH 版本；不要直接用旧版本读取新日志。验证与边界见 `docs/development_tasks/dsh_harness_efficiency_implementation_20260910.md`。
