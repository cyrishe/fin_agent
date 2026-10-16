# 多资产 Skill 合并评审

日期：2026-09-09。范围：审阅另一任务的业务方法、目录注册及评测资产，并按用户授权提交和同步服务器代码。

## 结论

可以合并为当前开发版本。没有发现新增权限、强制选Skill、循环调度或破坏现有调用协议的设计问题。此次改动主要属于业务SOFT层，不重构Agent主循环。

| 范围 | 处理 |
| --- | --- |
| 基金、债券、资金分析 | 新增3个系统业务Skill |
| 板块主题、股票比较 | 增强现有入口，未另建同义Skill |
| 个股、研报分析 | 按未解决问题组合其他方法，共享证据 |
| ETF、可转债、热点演变等 | 保留为总Skill下的按需参考，不注册成独立Agent |

共15个唯一系统业务入口。新增方法不额外授予工具，显式选择、自动发现、父方法先加载及版本快照沿用现有框架。

## 本次修正

基金基础信息的旧简介声称提供类型、管理人等，实际公开字段仅代码、简称、全称；债券旧简介声称有类型和期限，实际仅代码、简称、发行主体。另一任务在Skill中加入了绕开这些简介的提醒。

本次直接修正权威数据目录的两条简介，同时将Skill中的提醒改为正向的数据来源说明。没有新增字段、业务支持矩阵或校验器，也未改查询实现。基金净值差额等本框架特有口径继续保留在按需参考中，实际调用以执行包为准。

Skill编写规范用于检查用途描述、渐进引用和元信息一致性。新增3项及增强2项均通过通用格式检查；研报也通过。个股方法已有本项目扩展`execution-budget`，通用Codex校验器不认识，但项目加载器与专项测试支持，保留而不删除。

## 验证口径

合并前专项检查：Python 287项通过、DSH Node 49项通过。覆盖15个注册入口、元信息一致性、授权、显式上下文、参考资源、版本、执行包门槛、跨运行时披露及目录契约；不是全仓默认质量门。

另一任务的12题初选、3题复选、6题合成方法测试是历史证据，详见[原任务报告](finance_multi_asset_skills_20260908.md)。其中债券数值计算、资金解释仍有失败；不删除失败、不修改gold，也不把历史命中率外推成本次合并后的整体准确率。本次不复制生产数据、不执行真实金融查询。

### 固定ref复核与同步回执

被测代码commit：`35e0bc566f54743478945871fd88a5c04574da53`。后续仅追加本回执文档，业务源码不变。

- 本地：macOS，仓库`.venv`，Python 3.12.14；下列12文件专项合并执行，**287 passed**，1条既有multipart弃用提示。Node流程测试`tests/dsh_finance_loop_policy.test.mjs` **49 passed**。
- 服务器：既有隔离checkout `/home/che/cyris/fin_agent_deploy/skill-outer-review-sD4EuV/src`，同commit，复用服务器Python 3.10虚拟环境；执行下列服务器5文件，**143 passed**，同类弃用提示。无模型调用、无金融数据查询；未运行全仓或爬虫依赖测试。
- GitHub、Codeup均已推送；服务器主目录`/home/che/cyris/fin_agent`已快进同步同一代码。只读状态检查显示两个生产service均为active；本任务未重启它们，active不证明已加载新代码。
- 本轮Skill内容revision：`9d9553e3f0c1b43fd2a4fe9d19ef6287c332fbc43133538996e9b9ece8eb6443`；数据目录revision：`3569d9f987b331788bc6d9e9d16bc886be9b27c5156ba8880d8a2f135fee4563`。

本地workload（在仓库根目录）：

```bash
.venv/bin/python -m pytest -q \
  tests/test_finance_multi_asset_skills.py tests/test_finance_business_skill_catalog.py \
  tests/test_finance_business_skills.py tests/test_finance_business_default_skills_v2.py \
  tests/test_stock_research_composition.py tests/test_finance_cross_runtime_disclosure.py \
  tests/test_agent_skill_registry_policy.py tests/test_skill_registry_visibility.py \
  tests/test_skill_selection_only_eval.py tests/test_finance_catalog_content_contract.py \
  tests/test_finance_data_tool_catalog_snapshot.py tests/test_finance_explicit_query.py
node --test tests/dsh_finance_loop_policy.test.mjs
```

服务器workload：

```bash
.venv/bin/python -m pytest -q \
  tests/test_finance_multi_asset_skills.py tests/test_finance_business_skill_catalog.py \
  tests/test_finance_cross_runtime_disclosure.py tests/test_finance_business_default_skills_v2.py \
  tests/test_stock_research_composition.py
```

## 发布边界

静态评审未发现阻止合并的问题；专项功能测试通过。当前ref的完整效果、压力／长稳、恢复演练未完成，运行维保持`NOT_READY`，不做七维综合评分或生产放行。

用户本轮授权范围是Git提交推送和服务器代码同步。生产重启、数据库、凭据、回滚及nginx不在本轮操作内；生产进程继续由人工Gate控制。
