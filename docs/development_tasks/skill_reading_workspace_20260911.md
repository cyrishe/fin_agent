# Skill 阅读空间：设计与实现

## 本轮目标

先让用户找到、读懂、使用当前已发布 Skill，再迭代个人编辑与优化。改动属于展示 SOFT 框架与只读路由接入，没有修改业务 Skill 内容、运行协议、权限算法或数据库结构。

之前的基础见 `docs/skill_system_v2_architecture.md` 第 7 节，以及 `src/web/templates/skill_studio_v2.html`。原型已具备真实 Skill Hub、候选创建/修订/启用能力，但创建表单与系统方法阅读混排，Markdown 阅读器不支持完整表格、链接和主子方法导航。

## 交互设计

1. **对话内方法库**：侧栏打开原生 modal dialog；保留输入、附件与当前会话；Escape 关闭并恢复焦点。点击“用于对话”只增加已有的 Skill 选择，不发送问题。
2. **独立 Studio**：`/skills/studio` 显示当前账户已授权的方法卡片，支持关键词、system/public/private 与研究方向筛选。`/skills/studio/<id>` 支持直接进入、刷新及浏览器前后退。
3. **概览**：展示真实描述、只读归属、从 SKILL.md 抽取的章节，以及主方法到参考的结构。结构明确不是实时执行状态，不捏造固定执行顺序。
4. **专业方法**：主方法与按需参考共用阅读器；支持 Markdown 标题、列表、表格、代码和安全链接。相对参考链接只打开当前快照允许的文件；原文可切换查看，不能编辑。
5. **来源与版本**：展示方法内容哈希、目录 revision、参考数量和实际附加工具申请。不为缺少版本绑定证据的 Skill 展示评分或“评测已通过”。
6. **对话中已用方法**：已加载方法名称可在新标签页打开当前方法；明确是当前内容，不冒充历史运行版本。

视觉上使用低对比暖白背景、绿色强调、轻量目录和独立文档阅读区；桌面保留固定导航，移动端详情隐藏无作用的侧栏筛选，通过“全部方法”返回目录。没有为展示添加新的业务 schema。

系统 Skill 在阅读空间不可修改。个人公开/私有方法沿用服务器授权目录，不在前端自行扩权。已有候选构建工作区保留在 `?mode=authoring`，明确另开，不把系统方法送入候选写入流程。本轮没有实现“复制为自己的 Skill”、段落编辑或版本对比的虚假按钮。

## 数据与实现

- `frontend/src/SkillStudio.tsx`：共用目录、详情阅读器、对话弹层与独立页面，按需加载。
- `frontend/src/skillLibrary.ts`：现有只读 API 适配、身份分组、章节与参考寻址。
- `frontend/src/skill-studio.css`：隔离样式与响应式布局。
- `App.tsx` / `Sidebar.tsx` / `SkillActivity.tsx` / `main.tsx`：对话入口、使用动作、详情链接与页面分流。
- `src/web/flask_app.py`：有前端构建时默认交给 React 阅读页，显式 authoring 和未构建场景保留原模板。

仍然读取既有 `/api/skill-hub`、`/api/skill-hub/<id>`、`/api/skill-hub/<id>/references/<path>?revision=...`。无模型调用，无新写入接口。身份通过现有会话携带，参考读取与详情 revision 绑定；取消/切换时中断旧请求，迟到返回不得覆盖新选择。

应用 React 最佳实践的按需模块加载、派生导航、取消异步请求和可访问交互；浏览器验证覆盖 UI → 既有 API → 真实本地 Skill 快照 → 阅读显示。

## 验证记录

基线 commit：`511041efd0ac0b08a1e3f647eae73742d8f7ce7d` 加本轮未提交工作区。共享工作区中的工具开发、BlockRenderer 等其他修改保留不动。当前读取目录 revision：`9d9553e3f0c1b43fd2a4fe9d19ef6287c332fbc43133538996e9b9ece8eb6443`，15 个系统 Skill。

- TypeScript `tsc -b --pretty false` 通过。
- 前端定向测试 **18 passed**：`skillLibrary.test.tsx`、`Composer.test.tsx`、`apiInvocationAssets.test.ts`。
- 后端定向测试 **20 passed**：`test_skill_reading_workspace.py`、`test_skill_hub_catalog_service.py`、`test_skill_registry_visibility.py`。涵盖当前系统方法可读、普通 member 写入/删除无对应路由、未知资产不替换、版本错配拒绝、已有可见性隔离、React/authoring 路由兼容。
- Vite production build 通过。存在项目既有 Mermaid 等大 chunk 告警；未宣称全仓质量门通过。
- Agent-browser 实测：15 项列表、基金搜索缩到 1 项、基金与研报详情、按需参考与原文切换、对话弹层带入选择和 Escape 恢复焦点。
- 请求记录：进入基金详情仅请求目录与主方法；点击“表现与交易”后首次请求该参考，携带同一 revision。
- 对话操作后仍保留“比较两只基金的差异（浏览测试，暂不发送）”，出现基金调用标签，`/api/chat/` 请求数为 0。
- 1440px 桌面与 390px 移动端观察；移动端 `documentWidth === innerWidth`，未见横向溢出；浏览器 errors 为空。

本地原有 22053 进程仍加载旧 11 项目录。没有重启它或改写其构建目录，另外启动 22056 Flask 进程加载当前 15 项及隔离构建：`outputs/skill_studio_preview_20260911/`。这是本地预览，不是服务器部署。

截图保留在 `outputs/skill_studio_browser_20260911/`。本轮没有执行真实金融分析、个人候选创建/发布、直接数据库运维或服务器同步；浏览过程仍沿用应用已有的会话机制。

## 下一轮建议

沿同一阅读对象做“复制为我的私有 Skill → 选择段落描述修改意图 → 查看局部 diff → 用固定问题对比候选与启用版”。修改始终进入个人候选，不触碰系统源；先做小范围可理解的改动与证据反馈，再逐步接入发布和分享。先不扩张核心协议或把自然语言方法改成固定表单。
