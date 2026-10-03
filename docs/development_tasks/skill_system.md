# Skill Runtime Agent 开发任务包

负责人建议：Skill Runtime / Security Agent。本文记录未实施工作，不把结构测试等同于业务可用。

## 当前结构

当前存在两套 Skill：

1. 11 个 Finance CC-native business methods：由 Finance CC 自动选择，仍由 CC 调数据和回答。
2. 2 个 legacy compiled Skills：由 SkillStudio、`$Skill`、AsyncTask/ScheduledTask 和 SkillRunner 执行。

两套体系的发现、权限、版本、执行和评测不同。短期不强行合成一个执行器，但必须统一目录语义和安全边界。

## 2026-08-03 主线纵切面进展

本轮按“统一发现、执行隔离、运行时不可变”的顺序落地了 Phase 1 的确定性部分：

- `FinanceBusinessSkillCatalog` 在启动或显式 `reload()` 时编译 catalog、`SKILL.md`、frontmatter、reference 与 allowlisted companion 内容及 hash；请求热路径只读内存 snapshot，任意 `scripts/` 不进入本阶段的金融 Skill 包。
- 每个 revision 物化到内容寻址的 CC 插件目录，带可复算的文件 manifest，物化校验通过后封成只读；Finance CC 不再把可变仓库目录直接作为运行载体。
- Finance CC 的 client fingerprint、插件路径、可见 Skill 子集和工具授权来自同一个 turn snapshot。非法/漂移的 pinned binding 直接失败，不再回退成“新 Skill 正文 + 旧权限”；显式 reload 后旧 client 会断开，以同一 CC session 在新 revision 上恢复。
- 新增只读 `/api/skill-hub`，当前公开展示 11 个 `business_method` 与 1 个 public `legacy_compiled`；另 1 个 internal legacy bundle 只保留在兼容 Studio API，不进入普通用户 Hub。聊天 `/skills`、语义 catalog browse 和 `/skills` 页面已改读该 Hub。
- 旧 `/api/skills/catalog`、Skill Studio、`$` picker、ScheduledTask、Job API 和 SkillRunner 保持 legacy-only，避免 business method 被误送进旧执行器。
- 新 Hub 对 `business_method` 明确返回 `invocation_enabled=false`；legacy 行复用实际 AssetInvocation 的 active/lifecycle/visibility/auth 判定。Hub 不返回 Legacy 正文、schema 或 embedding，也不暴露写入口。

验证：相关目录、snapshot、session 和 Flask 接口回归为 `67 passed`；AssetInvocation、ScheduledTask、SkillRunner 和顶层路由边界回归为 `91 passed`；前端 Hub 资源标识测试 `25 passed` 且 TypeScript typecheck 通过，Hub 内联脚本通过 JavaScript 语法检查。真实 Flask smoke 为公开 Hub 12 项（11 + 1 public legacy）、Legacy API 2 项，internal legacy 不进入 Hub，Hub 不泄露旧正文/schema，`/skills` 页面使用 Hub。

本轮没有宣称完成：DB active revision 权威、candidate/publish、跨实例发布事件、受控 reference 读取工具、`$business_method` 路由、自然语言 authoring 和 `/create_skill`。这些必须沿下面任务继续，不能用旧 Blueprint/SkillRunner 代替。

## SK-P0-01 SkillStudio 与 Job API 授权

根因：Skill 修改/可用性/job/task result 接口缺用户或管理员授权；全局 Skill 可以被 guest 修改或运行。

验收：

- SkillStudio 写接口默认关闭，只有明确管理员能力才能开启。
- Skill name 经过规范化并限定为允许字符；最终路径必须 containment 于 Skill root。
- 使用 staging → validate → atomic publish，校验失败不得留下半份文件。
- availability、tools、auth 和发布操作写审计事件。
- job 创建保存 owner；job/task/result/status 全部校验 owner 或管理员。
- mixed tenant、路径穿越、无权扩 tools、读取他人 task result 都有拒绝测试。

主要涉及：`skill_studio_service.py`、Skill/Task Web routes、`task_service.py`。

## SK-P0-02 Legacy Skill 有效权限交集

有效工具必须满足：

```text
Agent tools ∩ Skill allowed-tools ∩ Runtime available tools ∩ owner/policy
```

本轮已先修复一个确定性漏洞：`allowed_tools=[]` 现在稳定表示 deny-all，只有 `None` 才委托 selector。完整的跨 Async/Scheduled/owner permission resolver 仍未实现，因此本任务仍保持 P0。

验收：

- 显式空 `allowed_tools=[]` 表示 deny-all，不能回退自动选择。
- Async、Scheduled、显式 `$Skill` 使用同一 permission resolver。
- prompt/context 里的 agent profile 不能代替系统执行门。
- 运行结果记录 effective grant 和拒绝原因，但不污染业务回答。
- 写工具、副作用工具和 Action Tool 默认不在 legacy Skill grant 中。

## SK-P1-03 不可变发布与内存 Snapshot

目标：把系统级 Skill catalog、body、frontmatter、references 索引在启动/发布时编译成不可变 snapshot，请求热路径不读配置文件。

验收：

- snapshot 包含 revision/hash、目录摘要、body hash、reference index 和 allowed-tools。
- Finance CC session fingerprint 包含 snapshot revision。
- publish 成功后原子切换；旧 session 继续使用其固定 revision 或明确重建。
- 多实例通过持久化 revision/事件同步，不依赖各自文件修改时间。
- 请求路径的应用层 Skill/config 文件读取次数为 0。
- 提供显式 reload/publish 操作；不做隐式每轮热加载。

当前状态：仓库 system seed 的内存 snapshot、内容寻址插件物化、manifest 校验、只读封装、revision-consistent 子集绑定、显式 reload 和 CC client 重建已完成；完整 hash 只在启动/物化/reload 校验，请求热路径只校验已验证的内存 binding。DB active revision、原子 publish、跨实例事件和用户级授权 snapshot 未完成，所以本项仍未关闭。

## SK-P1-04 受控 Progressive Reference Loader

当前 Skill body 可按需加载，但 `references/method.md` 没有受控读取入口。

验收：

- 只读取当前已成功加载 Skill 的 allowlisted reference。
- reference path containment 于固定 Skill revision。
- 返回标题、摘要、引用段和 source ref，不把所有 references 塞入上下文。
- 不开放通用 Read/Glob/Grep/Bash。
- reference 读取记录到 evidence；未读取不假装使用。

## SK-P1-05 统一发现目录与显式调用语义

目录至少区分：

- `business_method`：Finance CC-native 方法。
- `legacy_compiled`：迁移期仍可提交独立运行的历史 Skill。
- `system_skill`：平台治理能力，不进入普通用户业务 picker。

验收：

- `/api/skills/catalog` 与 `$` 补全能展示两类用户可见 Skill 的名称、作用、类型和调用语义。
- `$business_method` 进入 `investment_analyst + normal_qa`，并把该方法作为本轮显式偏好；最终仍由 Finance CC 控制。
- `$legacy_compiled` 才进入 SkillRunner。
- 不仅凭相似名称选择近似 Skill；歧义时返回候选而不是静默执行。

当前状态：只读 `/api/skill-hub` 和三处浏览入口已统一，且类型/调用语义已显式区分；旧 `/api/skills/catalog` 暂作为 Studio 兼容 API。`$` 补全和显式执行分流尚未完成，因此 `business_method` 仍不进入 invocable picker。

## 待确认的产品决策（不阻断当前纵切面）

1. **发布对已有长会话的影响**：建议默认固定已有会话的 snapshot，新会话使用新 active revision；只有用户明确选择“在本会话应用新版本”时才重建。当前 system seed 的显式 `reload()` 会重建 CC client 并保留会话文本，用户 Skill publish 尚未接入。
2. **个人 Skill 的稳定标识**：现有 `aiia_runtime_artifact` 唯一键不含 owner。建议第一纵切面使用系统生成、全局唯一的内部 `skill_id`，用户可重复的名称只作为 `display_name`；不要把 owner 拼进用户可见名称，也不要为此先改大表。
3. **公共 Catalog API 迁移**：建议当前保留 `/api/skills/catalog` 给 Legacy Studio，使用 `/api/skill-hub` 承载统一只读目录；等 Studio 和旧 workbench 改到显式 legacy endpoint 后，再决定是否把公共 `/api/skills/catalog` 指向 Hub。

## SK-P1-06 退役重复 Legacy Skill

`stock_deep_dive` 与新 `stock-research` 重叠，且旧 strict path 强制多工具，容易产生慢和重复查询。

验收：

- 先统计真实调用与兼容依赖。
- 新请求重定向到 Finance CC business method。
- 旧运行记录、固定任务和已发布资产仍可追溯。
- 在明确迁移窗口后将 legacy entry 设为 retired/direct-only，而不是直接删除。

## SK-P1-07 Eval Gate

- 入口评测明确命名为 `entry_only`，只证明 Agent/Skill entrance。
- full execution 遇到空回答、provider/tool error、权限绕过或无 evidence 必须失败。
- 增加 Skill 选择质量、answer correctness、reference usage、权限交集、并发发布和 revision consistency 测试。
- 分开记录路由准确率、执行成功率、回答正确性、工具有效轮次和耗时。

## SK-P2-08 架构与迁移文档

更新 `skill-arch.md`：补充 legacy 退役图、显式调用语义、revision 发布、tenant 权限、可调度边界、评测门，以及 Skill 与 Strategy/Backtest 的 point-in-time 依赖。

## SK-P1-09 Skill 组合与结果依赖语义

当前 Finance CC 可以在同一轮加载零个、一个或少量并列 `business_method`，并在同一个会话、working set 和证据空间中综合使用；“Sibling Skill”只是方法之间的自然语言关系，不是运行时层级。Skill 本身不执行另一个 Skill，也没有可被其他 Skill 消费的独立结构化返回值。

预研与验收：

- 区分“方法组合”和“结果依赖”：前者由 CC 动态加载多个 Skill；后者若需要固定前置结果、严格顺序和机器消费，应进入 Workflow / Tool Plan，而不是增加通用 `depends_on_skill`。
- authored Skill 只引用稳定业务能力或相关方法语义，不要求用户维护内部 Skill ID；Registry / Control Compiler 负责解析当前可用方法。
- 若未来引入独立研究 worker，其输出必须作为带 revision、as-of、evidence refs 的 Artifact 交给总控；明确这是 Agent/Workflow 编排，不伪装成 Skill-to-Skill 调用。
- 增加多个 Skill 同轮加载、权限并集、重复方法去重、冲突指令、证据共享和无硬依赖时可跳过的评测。

## SK-P1-10 自然语言生成 Progressive References

目标：用户通过 Chat 或 Skill Studio 描述专业经验时，Creator 能按复杂度生成一个精简 `SKILL.md` 和零到少量按需 reference，而不是要求用户自行设计目录或把全部说明塞进主文件。

预研与验收：

- 简单 Skill 默认只生成 `SKILL.md`；只有存在行业变体、长方法说明、模板、证据口径或反复复用资料时才拆 reference。
- Creator 先形成资源计划，再生成 candidate revision；reference 必须由 `SKILL.md` 直接链接并说明读取条件，禁止深层引用链和孤儿文件。
- 系统校验路径 containment、文件数量和大小预算、链接完整性、重复内容、脚本禁用、来源与权限；质量问题以 warning / eval 呈现，不用字段化协议阻断自然表达。
- 测试覆盖简单无需 reference、中等 1–2 份、复杂 3–5 份、自然语言增删/合并 reference、旧 reference 保持、candidate vs active 和实际按需加载率。
- 用 with/without reference 的真实任务对比结论质量、token、延迟和无效读取；只有证明专业增益且没有明显上下文膨胀才发布。

## SK-P2-11 2C 多租户 Skill / Reference Artifact Store

结论方向：Agent Skills 的目录结构保留为可移植、可导入导出的包格式和必要的 CC Runtime 适配层；生产权威不应是共享工作目录。用户 Skill、reference、API catalog/reference 和其他运行时方法资产应由租户化持久层管理，并在发布时编译成不可变 snapshot。

预研与验收：

- DB 保存 skill identity、owner、visibility、revision、active pointer、manifest、hash、权限与审计；较大正文和附件评估使用对象存储，DB 保存引用与完整性 hash。
- 多实例通过 revision/event 原子切换进程内 Registry Snapshot；请求热路径不扫描文件、不按用户目录 grep、不依赖本机 mtime。
- 模型通过受控的 `skill_id / revision / resource_id` Loader 读取内存或对象内容，不接触任意文件路径；每个 session 固定 snapshot revision。
- 若 CC/SDK 仍要求文件目录，只在发布/会话启动时物化内容寻址、只读、租户隔离的临时 bundle；它是缓存和兼容层，不是事实来源。
- API reference、Tool catalog 和系统参考同样采用“权威 revision → 编译索引/目录切片 → 内存 snapshot → 受控读取”，避免每轮系统 IO；系统 seed 可以随发布包提供，但运行时仍绑定明确 hash。
- 验证跨租户隔离、并发发布、回滚、多实例一致性、缓存失效、进程重启恢复、孤立 blob 回收、对象存储失败降级及热路径零文件扫描。

## 顺序

```text
SK-P0-01 API/路径授权
  → SK-P0-02 有效权限交集
  → SK-P1-03 发布与 Snapshot
  → SK-P1-04 Reference Loader
  → SK-P1-05 统一发现
  → SK-P1-09 组合与依赖语义
  → SK-P1-10 自然语言 References
  → SK-P1-06 迁移重复 Skill
  → SK-P1-07 Eval Gate
  → SK-P2-11 多租户 Artifact Store
```
