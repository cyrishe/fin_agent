# DSH 上游升级兼容性复核（2026-09-24）

本文保留 9 月 24 日的检查快照。9 月 26 日的自有远端、完整版本锁定及最新候选状态见 [Fin Harness 仓库建立记录](fin_harness_repository_setup_20260926.md)；本文关于“尚未推送”“未提交修改”的描述属于当时状态。

## 结论

Fin Agent 当前本机走 DSH 官方源码 checkout，而不是已安装的 DSH SDK/runtime wheel；但它**仍通过官方 Python SDK API 调用 DSH**，并没有把 DSH 核心复制进 Fin 仓库。Fin 自己维护 `sdk-minimal` 的 profile patch、MCP server 和 Agent policy 插件。当前 DSH checkout 为 `0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4`（`dsh-v0.1.2-alpha.1` 后一个本地提交），另有 6 个未提交的 DeepSeek translator 相关文件修改。Fin checkout 为 `95449b3a2304c42b57839780b22dea436c30aa3a`，工作树有其他未提交改动。本次没有修改、合并或部署这些代码。

截至本次远端 `refs/heads/master` 和 tags 检查，官方最新是 `dsh-v0.1.7-rc.1`、`46a7f68b0922371ce7144b668b90e377d8e799f4`（2026-09-23）。**不能直接把当前源码目录快进到最新版并期待 Fin 无损运行。** Python SDK 的构造参数基本未变，MCP stdio 配置和多数 Agent hooks 仍在；主要阻碍是模型接口协议、Fin 依赖的历史选择扩展、既有会话日志格式，以及上游组件/事件变化。

## 当前装载路径

1. 本机 `.env` 配置 `FINANCE_DSH_SOURCE_ROOT=/Volumes/ext/deepseek-harness`、`FINANCE_DSH_SDK_SOURCE=/Volumes/ext/deepseek-harness/python/sdk/src` 和专用 Node 路径。当前 `.venv` 没有安装 `deepseek-harness-sdk`，PATH 里没有 `dsh`；把源码 SDK 加入 `PYTHONPATH` 后导入位置是相邻 DSH checkout。源码启动脚本的 `--version` 输出 `0.1.2-alpha.1`。
2. Fin 的 `_load_sdk_class()` 先尝试 `import deepseek_harness`，仅在导入失败时使用 `FINANCE_DSH_SDK_SOURCE`。因此若其他环境装了 wheel，会优先加载 wheel；`FINANCE_DSH_SDK_SOURCE` 本身不是强制固定版本的开关。
3. Fin 的 `_dsh_bin()` 优先 `FINANCE_DSH_BIN`，再找 PATH 的 `dsh`，最后使用 `scripts/dsh_source_runtime.sh`。该脚本调用 `apps/cli/src/bin.ts` 的 Node/tsx 源码入口。Fin 通过 SDK 的 `dsh_bin`、`profile="sdk-minimal"`、`patches`、`dsh_home` 来启动和隔离 worker。
4. 2026-09-10 的发布记录说服务器 DSH 以 Git bundle 更新到 `0433ae6`、仍保留 translator 未提交 diff。那是当时的发布证据，不代表 2026-09-24 的实际线上进程；本次未连接生产主机核实。

## 静态契约对照

| 接点 | 当前 Fin 依赖 | 官方 `0.1.7-rc.1` | 判断 |
|---|---|---|---|
| Python SDK | `DeepSeekHarness` 构造参数、`run`/notifications | `python/sdk/src/deepseek_harness/api.py` 参数保持一致；该源码目录与当前 DSH 之间无文件改动 | 基本兼容，但不代表 runtime 兼容 |
| `sdk-minimal` | 禁用 shell/editor，添加 Fin MCP 和 policy | `persistent-bash/pwsh` 仍在；`str-replace-editor` 行已移除；核心服务改成显式组件树 | editor 的旧禁用补丁仅产生“找不到行”的跳过告警；不是致命失败，应清理 |
| MCP | `@deepseek-ai/dsh-mcp-client` stdio、超时、失败策略和 reconnect | 配置字段及 `mcp__finance__*` 命名仍保留 | 结构可复用，但需启动和工具发现实测 |
| Agent 插件 | `agent/pre-step`、`agent/request`、`agent/turn-stopping`、`tools/execute`、`tools/post-execute`、`tools.restrict/guard` | 这些扩展点仍在 | 需对新 Agent 行为与事件内容回归 |
| 独立问题上下文 | 本地 DSH `request/history` 事件和 `session.requestHistory()`；Fin policy 语义决定 `fromTurn` | 官方各已抽样 release tag 和最新源码均没有该 API；Fin 的 feature check 会跳过此逻辑 | 不会自动崩溃，却会悄悄恢复“全历史送模型”，影响 token、速度和回答边界 |
| 会话恢复 | 本地 v0 日志可能含 `request/history` | 最新 writer 为 v4；官方 v0 历史事件清单没有 `request/history`，未知、未标记为 ignorable 的历史事件会被迁移校验拒绝 | 含该事件的旧 home 不能假定可直接由新 runtime 恢复；需兼容迁移或旧 reader 留存 |
| 模型地址 | Fin 使用统一 `LLM_BASE_URL`，本机是 `/compatible-mode/v1` 的 OpenAI 兼容入口 | 新 `dsh-llm-deepseek` 仅支持 Messages，发往 `{baseURL}/v1/messages`（或已以 `/v1` 结尾的 `{baseURL}/messages`） | 当前地址直接沿用会打到错误接口；必须重配 adapter 或 endpoint |
| 本地 translator | 未提交补丁处理 OpenAI 兼容网关的空 tool-call id/name 分片 | 新 `dsh-llm-deepseek` 已是 Messages 流的另一套 translator | 补丁不能原样移植；如继续用 OpenAI 兼容网关，应在对应 adapter 做等价回归 |

中间版本抽样：`dsh-v0.1.2-rc.1` 已经没有本地 `request/history`；`0.1.3-alpha.2` 的 Session writer 已是 v2；`0.1.5-rc.3` 已移除 minimal editor 行；`0.1.7-alpha.2` 已采用 Messages translator。故不存在一个“直接切官方标签而保留本地历史选择协议”的停靠点。这里只抽样了这些 tag，不宣称每个中间提交均已测试。

## 可行升级路径

1. **先固定和保存现状。** 在独立分支或补丁文件中保存 DSH `0433ae6` 后的本地 `request/history` 提交与 translator diff，记下 Fin、DSH 双 commit 和线上进程对应关系。升级实验另用隔离 checkout/独立 `dsh_home`，不能对现有脏工作树直接 `git pull`。
2. **先解决模型协议。** 若保持当前 OpenAI 兼容网关，可利用新版官方 `dsh-llm-pi-ai` 的 hand-declared `openai-completions` provider，在 profile patch 层替换 `llm-deepseek` 及仅供 DeepSeek Messages 的扩展行，并保持模型、凭据和上层 Fin 统一入口一致。另一条路径是换用已经验证支持 Messages 的 endpoint，但那会改变模型提供路径，必须单独比对工具调用、thinking、缓存与费用；不能只改 URL。
3. **恢复历史选择能力。** 保留 Fin 的语义判断，重新在 DSH Session/Agent 原生扩展边界实现“本轮选择何段历史进入请求”的可回放机制；不能从 Fin 侧随意截取临时消息数组，否则日志、工具配对和续聊可能不一致。若把能力上游化，应是通用协议，不写金融关键词规则。
4. **处理旧日志。** 针对含 `request/history` 的 v0 日志设计显式迁移/兼容读取，并在副本上验证恢复；否则继续让旧 runtime 读旧 home，新 runtime 使用新 home，避免静默丢失上下文。现有 worker home 按进程隔离，也需核实线上真正会重用哪些日志。
5. **完成上线门槛。** 无费用本地 SDK+MCP 多轮回放（独立问题、追问、切题回返、错误重试）、两条 Fin 业务入口的工具和输出回归、同一网关的真实模型固定样本 A/B、并发及重启恢复，再小流量灰度。效果和 token/时延以相同 workload 判断。未完成这些前，保持当前线上版本。

`dsh-llm-pi-ai` 只是源码证实的可行适配面，不是已经验证通过的迁移方案。本次完成代码与配置静态审查、当前本机 SDK/CLI 装载验证、远端 tag/HEAD 核对；没有安装新版依赖、执行新版端到端测试、调用付费模型或读取生产状态。

## 自有 DSH 仓库建议（2026-09-24 补充）

建议建立 DSH 的**自有远端**，作为我们维护的发行来源；可以是与 Fin Agent 同账号或组织下的 GitHub fork，也可以是独立的 Codeup 代码库。官方 `deepseek-ai/deepseek-harness` 单独配置为 `upstream`。Codeup 官方支持从 GitHub 导入 Git 数据，但这种跨平台导入没有 GitHub fork 关系；对我们已有本地提交和未提交修复的情况，创建空 Codeup 库、整理本地提交后显式推送维护分支更直接。不要启用 Codeup 的“仓库同步”来追官方进度：官方文档说明该功能会强制覆盖当前库的不同 Git 改动，不适合两端同时开发。上游更新应在隔离分支用普通 Git fetch、审查、合并和测试。这不是把 DSH 源码复制进 Fin Agent。当前本机 DSH 的 `master` 仍以官方仓库为 `origin`，相对其跟踪分支是 ahead 1、behind 2151；直接在这个工作树做同步容易混淆上游变更和我们自己的版本。

第一次整理应从已验证的 `0433ae6` 创建稳定分支，保留其上游基线 `cd5ef81`。本地尚未提交的 translator 修复（6 个跟踪文件，加 3 份说明）经测试和内容审查后单独提交，不与 `request/history` 混为一个提交。Fin 的 `deploy/dsh/0433ae6-request-history.patch` 可继续保留为审查及灾备资产，但发布应引用自有远端上的**确定 commit/tag**，不再依靠线上目录的未提交 diff。业务 MCP、Skill、policy 和凭据仍由 Fin Agent 仓库维护。

升级到 `0.1.7-rc.1` 应另开升级分支，在隔离 DSH home 中完成模型协议、历史选择和日志迁移适配及双仓回归；验收后才更新稳定分支与 Fin 的固定 DSH ref。每次部署记录 Fin commit、DSH commit、配置与验证结果；回滚按同一对版本进行。自有远端建立和推送尚未执行，本段是仓库管理方案。

## 权威来源

- [Codeup 从 GitHub 导入 Git 数据](https://help.aliyun.com/zh/yunxiao/user-guide/import-the-three-party-code-base-through-the-web-page)
- [Codeup 仓库同步的覆盖语义](https://help.aliyun.com/zh/yunxiao/user-guide/warehouse-synchronization)
- [DSH 最新 sdk-minimal 组成](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/bundle/sdk-minimal/cordis.patch.yml)
- [DSH 最新 Python SDK API](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/python/sdk/src/deepseek_harness/api.py)
- [DSH 最新 Session 事件协议](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/core/session/src/types.ts)
- [DSH 历史 v0 事件清单与校验](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/session/session-format-v0-to-v1/src/dispositions.ts)
- [DSH 最新 DeepSeek 适配器](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/llm/llm-deepseek/README.md)
- [DSH 最新 pi-ai 适配器](https://github.com/deepseek-ai/deepseek-harness/blob/46a7f68b0922371ce7144b668b90e377d8e799f4/packages/llm/llm-pi-ai/README.md)
