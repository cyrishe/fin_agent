# Fin Harness 仓库建立记录（2026-09-26）

## 范围与版本

本次把正在使用的 DSH 源码及本地修改纳入独立仓库，建立官方跟踪线、维护线、不可变发布标签和 Fin Agent 配套版本记录。没有改变运行时业务行为，没有迁移金融 MCP 或 Skill，没有切换到新版官方 DSH，也没有连接、重启或部署生产环境。

源码仓库：[fin_harness](https://codeup.aliyun.com/684beabd28a6beb51d765af2/fin_harness)。配套版本以 [锁定文件](../../deploy/dsh/fin_harness.lock.json) 为准，首个标签为 `fin-v0.1.0`。

维护分支、官方跟踪分支及三个标签已通过普通 Git 原子推送发布。远端默认分支 `master` 与 `fin-v0.1.0` 均核对为 `38ea60b22f86425fc8a52fdc88de7550a971035b`。从 Codeup 按该标签重新克隆后，commit 和完整 Git tree 与本机维护版本一致，两个工作树均干净。该重新克隆验证检查源码完整性，没有在克隆副本重新安装依赖或重复构建。

| 内容 | commit |
| --- | --- |
| 官方运行时基线 `dsh-v0.1.2-alpha.1` | `cd5ef8148158c3a752a658978873241fdf8e2bbc` |
| 已有可回放历史选择 | `0433ae61319e1cdbe8adc8f1ca7192306bd2a6e4` |
| 已有网关工具身份兼容修复归档 | `8bcea71528bdd50e4e802d787a3b75c73daafa75` |
| Fin 维护指南及默认入口 | `75a70d980c5c20287446ca091c3df11d57e616de` |
| 保留 Codeup 初始化历史的合并 | `d2bb146e82f666cfdd087090080a8a73c434a52a` |
| 维护说明按上游字数限制精简，首个 Fin 版本 | `38ea60b22f86425fc8a52fdc88de7550a971035b` |
| 官方候选 `dsh-v0.1.7-rc.2` | `477b4f420553e8a52c2fbccc464d7561b239c443` |

网关修复的六个已跟踪文件，提交后的 diff 与操作前备份完全相同，SHA-256 均为 `1d76424f0cb352ef14e47da554de3ca960998d78ae60d2feb2d6e4cd15c78ab5`。同时归档其原有双语 Agent Note。合并提交保留 Codeup 原有两个初始化提交的祖先关系，并使用 DSH 的 README 和 `.gitignore`，合并树与前一维护提交完全相同；没有强推或重写官方历史。

## 分支及维护约定

- `origin` 指向 Codeup，默认推送目标为 `origin`；官方 remote 改名 `upstream`，本机 `pushurl=DISABLED` 防止误推。
- Codeup `master` 是经过验证的 Fin 维护版本；`upstream/master` 保存未修改的官方候选，仅用于跟踪和比较。
- 新改动使用 `codex/*` 分支。运行时升级使用隔离 checkout 和独立 `dsh_home`，完成适配、双仓回归后再合入、打新标签、更新锁定文件。
- 不使用 Codeup 的强制覆盖仓库同步，不移动已发布 Fin 标签。Git 配置只作用于当前本机，新的 clone 需配置自己的 remote。
- 本次浏览器访问停在阿里云登录页，SSH Git 权限可用；服务端保护分支设置和 Codeup CI 尚未核实或配置，不应把本地 hook 当成服务端强制保护。

## 当前验证

原生回归通过 5 文件 / 127 测试，覆盖 session 历史选择、agent loop 请求构造、token 投影及流式工具调用。Fin 两条策略测试通过 74 项，源码启动器和两条业务接入测试通过 66 项。SDK/MCP 多轮回放通过：3 轮、6 次本地模型夹具请求，独立问题隐藏旧请求历史，后续追问恢复历史，原始 session 事件与 `request/history` 选择均保留；付费模型调用为 0。

上述 Fin 接入测试使用本机已有工作树，其中有本次任务之外的未提交业务修改，未将它们混入提交。测试基准 HEAD、源码差异摘要、环境、命令和结果见 [机器可读证据](evidence/fin_harness_repository_20260926.json)。因此这不是对干净 Fin HEAD 的完整发布认证，也不是生产效果、压测或长稳结论。DSH 运行时代码归档前后相同；维护文档和初始化历史合并不修改其行为。

上游文档检查覆盖 32 个检查项：`doc-sync` 首跑为 31 通过、1 失败，失败原因是本机工具环境没有 npm；在临时目录提供 npm 10.9.3 后，`doc-typecheck` 单独重跑通过，编译 78 个文档代码块。`test:docs` 首跑发现新增根 AGENTS 文字超预算，已精简并通过最终的预算检查。完整 `pnpm run lint` 通过，正常推送 hook 的 `pnpm run typecheck` 通过（122.66 秒）。没有修改上游检查配置或绕过 hook。

## 上游兼容与后续方向

本次获取的最新官方 `master` 为 `477b4f4`，并重新核对了 `0.1.7-rc.2` 源码：DeepSeek adapter 仍调用 Messages API；Session writer 为 v4；核心仍无本地 `request/history` / `requestHistory`；v0 迁移清单仍没有该事件。新版新增工具定义历史投影，也不等于已有的对话历史选择协议。详见 [9 月 24 日完整对照](dsh_upstream_compatibility_review_20260924.md)。官方跟踪线已经具备更新入口，维护运行时仍需适配验证后才能升级。

Fin Harness 的积累方向是可复用的金融 Agent 运行能力及验证资产：上下文和数据引用、工具执行与恢复、provider 兼容、可回放观测、统一效果与成本回归。优先通过 DSH 原生插件、profile 和事件实现；只有无法表达关键持久事实时才维护小型核心补丁。业务公式、金融口径、权限资产、Skill 和展示留在 Fin Agent。当前只完成运行时仓库化，没有宣称已建成金融插件产品；后续以第二个真实消费者及可复现的效果和消耗证据检验复用价值。
