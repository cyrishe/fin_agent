# Backtest Agent 开发任务包

负责人建议：Backtest / Quant Runtime Agent。本文同时记录已验证基线和剩余 backlog；每项必须按“已实现 / fixture 验证 / 未接产品主线”区分，不能用协议或测试桩冒充在线能力。

## 当前边界

- `src/backtest` 是可验证的日频 long-only 组合账本核心。
- Buy & Hold 是真实数据纵切面。
- Custom Tool strategy backtest 已有固定 targets 的内部历史 Host 纵切面与 fixture，尚无用户级产品运行链。
- `/backtests` 当前是静态 Event Study DEMO，不是账本结果页。
- 普通自然语言主线是 `investment_analyst + normal_qa → FinancialQaCcService`；不新增顶层 `backtest` turn mode。
- 显式资产主线仍会在 ToolPlan 执行时丢失固定 revision，现有 Custom Tool Runtime 只运行 active revision，不能直接复用为历史主机。
- 现有 AsyncTaskService 与任务查询 API 没有完整的用户 ownership 边界，只能复用事件和步骤思想，不能原样当作用户回测运行主机。
- 同步 Buy & Hold API 尚无身份、限流和持久运行归属，只是受限技术纵切面。
- 暂不实现下单、实盘交易或 Action Tool。

## BT-P0-01 历史资产执行主机

目标：实现 server-principal-scoped、revision-pinned、point-in-time 的 Custom Tool 历史执行主机；第一期只接 Custom Tool，不同时扩展 Skill。

根因：现有 Runtime 只运行 active revision，且金融查询没有系统强制的历史截止。

验收：

- requester/run owner 必须来自服务端认证会话，不能由客户端或模型自报；资产 owner、visibility、稳定 asset ID 和 revision 由授权 Host 解析。资产 owner 与运行 owner 是不同事实。
- 内部运行引用必须固定明确 revision；active revision 改变不能静默影响已创建运行。
- hidden runtime scope 按 API Catalog 声明的真实可用时间语义强制 cutoff。行情可按交易日，财报应按公告/可用时间；不能把全部 API 一律写成 `trade_date`。
- 没有可靠 point-in-time 能力的 API 在 provider 执行前拒绝，不能由业务 Tool 自觉遵守。
- Action family 始终禁止执行。
- asset fingerprint 包含代码、Schema、strategy companion contract 和 revision。
- owner 不匹配、revision 冲突、未来数据读取分别有协议测试。
- 至少一个真实 Custom Tool 在受控历史日期完成集成回放。

当前进展：

- 已实现 owner 可见性校验后的精确 revision 加载、冻结 bundle、策略伴随契约复核和 asset fingerprint；预检后不再读取 active pointer。
- 已实现隐藏的 `effective_as_of + explicit symbols` 下沉；第一期只允许 `stock.quote.dynamic_cal` 的原始、交易日绑定行情列，强制历史模式并二次拒绝未来日期结果。当前证券名称、ST/退市标签、分钟列和后复权列会在 provider 前拒绝。
- 历史 Host 不沿用资产的开发态 `local_dev`，而由服务端强制选择可用的 formal sandbox；服务器没有安全后端时预检失败，避免代码绕过 Host 直接读取网络或工作区。
- 已用真实 Custom Tool Runtime 执行协议、finance bridge、Strategy Wrapper 和组合账本完成受控 fixture 纵切面；公开参数中没有新增 `as_of`、owner 或 revision。
- 当前 point-in-time 承诺只覆盖 Host 代理的金融数据可见性；策略代码若自行读取系统时钟或随机数，尚不属于确定性复现保证，结果指纹只能发现差异而不能冻结这些输入。
- 真实 `dynamic_cal` 仍会在 provider 调用时生成计算代码；当前纵切面使用受控 provider。正式回放前必须冻结并指纹化生成逻辑，或改用确定性原始行情 API，不能只凭相同自然语言 task 宣称策略可复现。
- 尚未接产品路由，也尚未把当前 `ct_bottom_five_red_scan` 视为可回测资产：其 active revision 没有策略伴随契约，且它依赖的 ST/退市名称没有历史快照，现有 Host 会明确拒绝该列而不是读取当前名称。

主要涉及：`custom_tool_service.py`、`database_custom_tool_store_service.py`、`finance_data_tool_runtime_service.py`，以及新的历史资产 host。

## BT-P0-02 历史 Universe Timeline

目标：固定 targets 保持静态；`universe_ref` 在每个调仓日解析当时成员，并在运行开始时冻结 timeline。它只阻塞动态市场范围，不阻塞受控的固定 targets 回放。

根因：当前 market scope 只按最终 `effective_as_of` 解析一次 universe，随后所有历史日复用最终成员，会产生新股前视和退市幸存者偏差。

验收：

- `scope.targets` 行为保持不变。
- 全 A、指数、行业/板块范围按每个 decision date 使用当时成员。
- timeline 有稳定 fingerprint。
- MarketData 加载成员并集，但每日策略只能看到当天成员。
- 用中途上市和中途退市 fixture 证明早期决策看不到未来成员。
- 不把 timeline 逻辑塞入用户策略核心代码。

当前进展：组合策略桥已对非 `explicit_targets` 历史回放 fail-closed；Timeline 本身尚未实现。

## BT-P0-03 真实 MarketData 与数据 Snapshot Host

目标：为策略回测提供 market-scale、版本化、可寻址的数据输入，并保持账本与数据库解耦。

验收：

- 输入覆盖 universe timeline 所需成员和决策/成交日期。
- 明确 raw、adjusted 和 corporate-action 口径；不能用复权 OHLC 直接替换成交价格。
- 重复 symbol/date 行 fail-fast，而不是静默覆盖。
- snapshot/version 与 checksum 可以重新读取原运行数据。
- 历史证券状态、缺失行情和不可成交事实进入 evidence。

当前进展：Buy & Hold 真实日线加载器已补重复 symbol/date 拒绝；可寻址的不可变 market-scale snapshot、公司行动和历史交易状态仍未完成。

## BT-P0-04 用户级异步回测运行主机

目标：复用现有事件、步骤和 Surface 展示思想，提供持久化、可查询、可取消的用户运行；现有 AsyncTaskService 在补齐 owner 数据访问边界前不能直接复用，也不得另造两套并行生命周期。

验收：

- 固化 owner、request、asset revision、plan/data/asset fingerprint。
- idempotency key 防止重复提交。
- 全局并发、单用户额度、universe/调用总量上限和背压。
- 单决策、单资产和整次运行 timeout。
- 支持取消、失败证据、进度事件、重启恢复与结果 ownership 校验。
- Buy & Hold 入口进入同一治理，或至少先具备身份、限流和资源上限。

## BT-P1-05 区分 Event Study 与 Portfolio Simulation

产品规则由用户目标和可执行输出共同决定，不能只按 Tool family 自动推断：

```text
只想观察信号出现后的表现                     -> event_study
有完整目标持仓、调仓频率和退出/清仓语义       -> portfolio_simulation
只有买入候选但没有退出或持仓规则               -> 不伪造成交，先做 event_study 或补充语义
```

验收：

- API 与结果显式说明 evaluation mode。
- 2C 界面只表达“看信号表现”和“模拟持有”，不向普通用户暴露内部分类或 `TargetPortfolio`。
- Event Study 不伪造订单、仓位和交易成本。
- Portfolio Simulation 必须使用统一账本。
- 静态页面在真实接口接通前保留明显 DEMO 标识。
- 真实账本 Renderer 展示净值、收益/回撤/成本、关键调仓、未成交和数据边界。

## BT-P1-06 输出新鲜度与 Adapter 扩展

- ranked selection 默认要求 `output_date == decision_date`。
- 允许滞后时使用显式、可版本化政策，并展示证据。
- 当前只支持 ranked list → Top-N → equal weight。
- `portfolio_target` 只有在出现真实需求时单独增加小型 Adapter。
- 单股 signal 不得按调用顺序自动解释为排名。
- 不设计覆盖所有未来策略的巨大 Schema。

## BT-P1-07 数据可信度、基准与归因

- 公司行动、退市结算、历史交易限制与费用口径进入模型或明确风险边界。
- 固定股票组合入口已增加真实同区间指数基准：默认沪深300，允许用户指定主要指数，并允许 CC 在执行前补充最多两个有持仓依据的相关指数；已输出归一化曲线、超额收益、跟踪误差和信息比率。任意策略运行的统一基准仍需随用户运行主机推进。
- 增加组合/个股贡献；行业归因放在真实需要后实现。
- 数据 snapshot 与每日策略输出都可寻址，做到可恢复而不仅是能检测 checksum 变化。

## 依赖顺序：按能力门控，而非全量串行

```text
固定少量 targets：
BT-P0-01 历史资产主机
  → P0-03 的最小数据正确性（重复行、口径、缺失证据）
  → BT-P0-04 用户运行主机
  → 受限 portfolio simulation 入口

动态全 A / 指数 / 行业范围：
BT-P0-01 历史资产主机
  → BT-P0-02 Universe Timeline
  → P0-03 的 market-scale snapshot
  → BT-P0-04 用户运行主机
  → 产品入口

信号观察：
先完成 BT-P1-05 的语义与结果协议；它不进入交易账本，也不能借此绕过数据 cutoff
```

在 server-resolved owner、固定 revision、数据 cutoff 和用户运行 ownership 完成前，不开放任意 Custom Tool/Skill 的产品级历史回测路由。固定 targets 的内部受控验证可以先行；动态范围必须等待 Universe Timeline。当前不增加更多策略类型。
