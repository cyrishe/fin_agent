# CC 固定对照基准：9月2日评测重建版

用途：今后用固定的旧 CC 金融问答流程对比 DSH。本目录封存后不优化、不回填主线修改；当前项目继续独立迭代 DSH。

## 固定的是什么

| 内容 | 固定版本 |
|---|---|
| 重建源码 | `2242990c9a092c00a4cd1bb8a0bbb42d9fbc7b7f`，9月4日首次集成提交 |
| 原始对比 | 9月2日随机20题的完整问题、结果、工具、答案、token、计时与 trace |
| 数据目录 | SHA256 `aa5713c6946a5aa1a802b0be62c6d77dec0f94bd6c33cf0b7b5b3006a39335a3`，与9月2日实际记录一致 |
| CC 默认配置 | DeepSeek 官网、`deepseek-v4-flash`、low、12轮、auto、完整取数及回答 |
| 已测试运行依赖 | Python 3.12.14、claude-agent-sdk 0.2.123、内置 Claude Code 2.1.215；64个包精确锁版 |
| 独立 Git 标签 | `cc-reference-20260902-rebuilt-v1` |

**这是经用户确认的重建版，不是9月2日全部源码的精确快照。** 当天没有保存完整源码/依赖清单；9月4日提交可能包含其后改动。目录逐字节相同不能证明整个运行时逐字节相同。历史20题正常结束，也不代表20题业务上全部正确。

## 文件

- `source.tar.gz`、`manifest.json`：旧 `src/config/frontend`、根提示词、股票/指数静态目录等714个文件；全部来自固定 Git 对象，没有当前工作区覆盖。
- `history/`：20题完整历史证据及其来源、哈希、证据局限。
- `tools/run_cc_reference.py`：固定运行入口；自动验证、解包并加载旧代码。
- `requirements.lock.txt`、`environment.json`：重建验证所用依赖，不冒称找回了9月2日完整依赖。
- `requirements.macos-x86_64.hashed.txt`、`wheels.json`：本机安装包的精确哈希。
- `verification.md`：本次独立环境验证结果及边界。
- `SHA256SUMS`：本目录封存文件的完整性清单。

封存源保留完整金融 CC 场景、旧协议/转换/工具/Skill/答案生成及旧应用角色配置。运行器直接进入旧金融问答服务，目录全开放，**不预选某个 subject/dataview，不走当前 DSH，也不替模型生成 API**。旧源码中保留了同期 DSH 文件，但不激活它。

## 使用

以下在仓库根目录执行。环境已在本机安装为 `.venv-cc-reference-20260902/`；不要复用会随主线升级的 `.venv/`。

```sh
# 新机器安装（Python 3.12；Linux尚未做运行验证）
python3.12 -m venv .venv-cc-reference-20260902
.venv-cc-reference-20260902/bin/python -m pip install -r baselines/cc_20260902_rebuilt/requirements.lock.txt

# 检查封存内容；从目录内部运行sha校验
cd baselines/cc_20260902_rebuilt
shasum -a 256 -c SHA256SUMS
cd ../..

# 无网络、无数据库、无模型调用的离线检查；输出目录必须是新的
.venv-cc-reference-20260902/bin/python baselines/cc_20260902_rebuilt/tools/run_cc_reference.py --check-only --output-dir outputs/cc_reference_check_new

# 单题完整问答；凭据文件在封存目录之外
.venv-cc-reference-20260902/bin/python baselines/cc_20260902_rebuilt/tools/run_cc_reference.py --env-file /absolute/private/cc-reference.env --query '机构对阳光电源2026年至2028年的营收增速预测分别是多少？' --output-dir outputs/cc_reference_query_new

# 跑固定20题（顺序执行；不是历史并发3的性能复现）
.venv-cc-reference-20260902/bin/python baselines/cc_20260902_rebuilt/tools/run_cc_reference.py --env-file /absolute/private/cc-reference.env --cases baselines/cc_20260902_rebuilt/history/cases.json --output-dir outputs/cc_reference_20_new
```

抽样可加 `--case-ids RTE055,RTE003,RTE073,RTE093`。显式切换到本机可用的百炼验证路线，可加 `--provider dashscope --model deepseek-v4-flash-0731`；结果将记录与历史配置的差异，不标为历史效果复现。

`.env.example` 只含空 Key 和业务数据库配置。默认 DeepSeek 路线需要 `DEEPSEEK_API_KEY`；百炼路线需要 `DASHSCOPE_API_KEY`。可以读取当前项目 `.env`，运行器只提取需要的凭据并固定模型配置；平台库 URL 仅允许被拆出金融库所需连接凭据，不传递系统库连接。所有 key 都通过进程环境注入，封存包不含个人登录、系统库数据或历史会话状态。

输出目录包括 `metadata.json`、`results.jsonl`、逐题事件和独立 `workspace/`。后者保留本次旧 CC 会话、工具数据和日志；未来结果不写进本封存目录。每次新建运行目录，从封存源码重新解包。

## 对比边界

1. 源码、目录、提示词、固定入口和依赖与当前 DSH 隔离；外部数据库、数据日期和上游模型服务没有被冻结，未来答案不保证逐字相同。
2. 历史评测是 Chat/SSE 入口；本运行器验证金融 CC 的完整子链路，不声称复现登录、顶层路由、UI或HTTP并发性能。
3. 本次百炼烟测只能证明旧 CC 流程可运行；要做 CC/DSH 效果或耗时公平对比，应显式使用相同 provider/model、题目、数据时点和运行条件，分别保留历史与新测结果。
4. 这是研究对照，不是生产发布版本，也不是生成代码的安全沙箱。不要将旧服务直接对外开放；不为修正旧答案修改封存源码。

## Git 恢复

基准有独立分支 `codex/cc-baseline-20260902-rebuilt` 和标签。当前开发分支不切换，现有未提交改动不进入这个基准。

```sh
git worktree add --detach /absolute/new/cc-reference cc-reference-20260902-rebuilt-v1
```

该 worktree 是旧提交加封存目录，而不是当前 DSH 工作区。运行时使用其中 `baselines/cc_20260902_rebuilt/tools/` 的固定入口。标签保留在本地；远端同步须另行执行，本文不代表已推送或已部署。
